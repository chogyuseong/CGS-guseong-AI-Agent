"""2단계 보강(1): 실행 계획(action_plans)·operation 저장과 결정에 따른 상태 (BUILD_SPEC 3.2, 3.4, 4.2, 9.1).

- EXTERNAL 호출이 승인 대기가 되면 서버가 인자를 검증·정규화해 계획 하나와 operation 하나를 만든다.
- 해시는 도구 이름·버전·정규화한 인자로 만든다. 키 순서가 달라도 같고, 값이 바뀌면 다르다.
- 승인·거절·만료·취소가 operation 상태에 반영된다. 승인하면 실행 유효 시간(5분)이 정해진다.
- 등록되지 않은 EXTERNAL 도구는 계획을 만들지 않고 실패로 끝낸다(서버 정책 검사).
- 계획·operation의 user_id는 상위 task·계획의 소유자와 다를 수 없다(DB 제약).
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import plans
from app.agent.agent import MyAgent
from app.agent.toolsets import EXTERNAL_TOOL_SPECS, external_tools
from app.models import ActionPlan, AgentRun, Approval, Connection, Operation, Step, Task, User
from app.models.enums import OperationStatus
from tests.fake_tools import FakeExternalCounter
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send
from tests.test_approvals import approve, external_then_answer, make_chat_task, reject, task_detail


async def plan_of(session: AsyncSession, task_id: int) -> tuple[ActionPlan, Operation, Approval]:
    session.expire_all()
    row = (
        await session.execute(
            select(ActionPlan, Operation, Approval)
            .join(Operation, Operation.plan_id == ActionPlan.id)
            .join(Approval, Approval.plan_id == ActionPlan.id)
            .where(ActionPlan.task_id == task_id)
        )
    ).one()
    return row[0], row[1], row[2]


# ---- 계획 저장 ----


async def test_waiting_external_call_saves_plan_and_operation(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    logged_in_user: User,
    fake_external_counter: FakeExternalCounter,
) -> None:
    user_id = logged_in_user.id
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")

    plan, operation, approval = await plan_of(db_session, task["id"])
    step = await db_session.get_one(Step, approval.step_id)
    assert fake_external_counter.count == 0
    assert isinstance(plan.operation_id, uuid.UUID)
    assert plan.user_id == user_id
    assert plan.step_id == step.id
    assert plan.tool_call_id == step.tool_call_id
    assert plan.tool_name == "fake_external"
    assert plan.tool_version == "1"
    assert plan.canonical_args == {"value": 7}
    assert plan.args_hash == plans.args_hash("fake_external", "1", {"value": 7})
    assert plan.approval_round == 1
    assert plan.connection_id is None
    assert plan.target_resource_id is None
    assert plan.preview == approval.summary == "가짜 외부 도구를 값 7로 실행합니다."
    assert plan.decision_expires_at == approval.expires_at
    assert plan.policy_version == plans.POLICY_VERSION
    assert operation.status == OperationStatus.AWAITING_APPROVAL
    assert operation.user_id == user_id
    assert approval.approval_round == 1
    assert approval.execute_before is None


async def test_plan_round_follows_agent_run_round(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    """재개한 run(2회차)이 다시 EXTERNAL을 부르면 그 계획은 승인 회차 2다."""
    model = scripted(call("fake_external", value=1), call("fake_external", value=2), text("끝"))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "두 번 실행해줘")
        await approve(client, task["approvals"][0]["id"])

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(ActionPlan.approval_round, ActionPlan.canonical_args)
            .where(ActionPlan.task_id == task["id"])
            .order_by(ActionPlan.approval_round)
        )
    ).all()
    rounds = list(await db_session.scalars(select(AgentRun.round).where(AgentRun.task_id == task["id"])))
    assert [(r, a) for r, a in rows] == [(1, {"value": 1}), (2, {"value": 2})]
    assert sorted(rounds) == [1, 2]


def test_canonical_args_fill_defaults_and_hash_is_order_free() -> None:
    toolsets = [external_tools]
    first = plans.canonical_args(toolsets, "update_event", {"title": "회의", "event_id": "abc"})
    second = plans.canonical_args(toolsets, "update_event", {"event_id": "abc", "title": "회의"})
    changed = plans.canonical_args(toolsets, "update_event", {"event_id": "abc", "title": "회식"})

    assert first == {
        "event_id": "abc",
        "title": "회의",
        "start": None,
        "end": None,
        "location": None,
        "description": None,
    }
    assert plans.args_hash("update_event", "1", first) == plans.args_hash("update_event", "1", second)
    assert plans.args_hash("update_event", "1", first) != plans.args_hash("update_event", "1", changed)
    assert plans.args_hash("update_event", "1", first) != plans.args_hash("update_event", "2", first)


def test_canonical_args_reject_invalid_or_unknown_tool() -> None:
    with pytest.raises(plans.PlanRejectedError):
        plans.canonical_args([external_tools], "update_event", {"event_id": "bad id!"})
    with pytest.raises(plans.PlanRejectedError):
        plans.canonical_args([external_tools], "no_such_tool", {})


async def test_calendar_plan_records_connection_and_target(db_session: AsyncSession, logged_in_user: User) -> None:
    connection = Connection(user_id=logged_in_user.id, provider="google", encrypted_token="x", scopes=[])
    db_session.add(connection)
    await db_session.commit()

    draft = await plans.draft_plan(
        db_session,
        user_id=logged_in_user.id,
        toolsets=[external_tools],
        tool_name="delete_event",
        args={"event_id": "e1"},
    )

    assert draft.tool_version == EXTERNAL_TOOL_SPECS["delete_event"].version
    assert draft.connection_id == connection.id
    assert draft.target_resource_id == "e1"
    assert draft.canonical_args == {"event_id": "e1"}


async def test_unregistered_external_tool_fails_without_plan(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(EXTERNAL_TOOL_SPECS, "fake_external")
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")

    assert task["status"] == "failed"
    assert task["approvals"] == []
    assert fake_external_counter.count == 0
    plans_count = (await db_session.scalars(select(ActionPlan.id).where(ActionPlan.task_id == task["id"]))).all()
    assert plans_count == []


# ---- 결정이 operation 상태로 ----


async def test_approve_sets_execute_window_and_operation_approved(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    model = scripted(call("fake_external", value=7), text("처리했습니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await approve(client, task["approvals"][0]["id"])

    _, operation, approval = await plan_of(db_session, task["id"])
    assert approval.decided_at is not None and approval.execute_before is not None
    assert approval.execute_before - approval.decided_at == plans.EXECUTE_WINDOW
    # 실행·영수증은 2단계 보강(2)에서 operation에 반영한다. 지금은 승인됐다는 것까지.
    assert operation.status in (OperationStatus.APPROVED, OperationStatus.SUCCEEDED)


async def test_reject_marks_operation_rejected(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    with agent.override(model=scripted(call("fake_external", value=7), text("안 했습니다.")).model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await reject(client, task["approvals"][0]["id"])

    _, operation, approval = await plan_of(db_session, task["id"])
    assert operation.status == OperationStatus.REJECTED
    assert approval.execute_before is None


async def test_expired_decision_marks_operation_expired(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        approval_id = task["approvals"][0]["id"]
        past = datetime.now(UTC) - timedelta(minutes=1)
        await db_session.execute(update(Approval).where(Approval.id == approval_id).values(expires_at=past))
        await db_session.commit()
        assert (await approve(client, approval_id)).status_code == 410

    _, operation, _ = await plan_of(db_session, task["id"])
    assert operation.status == OperationStatus.EXPIRED


async def test_cancel_marks_pending_operation_cancelled(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        assert (await client.post(f"/api/tasks/{task['id']}/cancel")).status_code == 200

    _, operation, _ = await plan_of(db_session, task["id"])
    assert operation.status == OperationStatus.CANCELLED
    assert (await task_detail(client, task["id"]))["status"] == "cancelled"


# ---- 소유자 일치 제약 (BUILD_SPEC 9.1) ----


def plan_row(task: Task, step: Step, user_id: int) -> ActionPlan:
    now = datetime.now(UTC)
    return ActionPlan(
        operation_id=uuid.uuid4(),
        user_id=user_id,
        task_id=task.id,
        step_id=step.id,
        approval_round=1,
        tool_call_id="c-1",
        tool_name="fake_external",
        tool_version="1",
        canonical_args={},
        args_hash="0" * 64,
        preview="",
        decision_expires_at=now,
        policy_version=plans.POLICY_VERSION,
    )


async def test_plan_user_must_match_task_owner(db_session: AsyncSession, logged_in_user: User, make_user) -> None:
    other = await make_user("owner-check@example.com", "password-other")
    task = await make_chat_task(db_session, logged_in_user)
    step = Step(
        task_id=task.id,
        order=1,
        tool_name="fake_external",
        tool_call_id="c-1",
        risk_level="EXTERNAL",
        input={},
        status="waiting_approval",
        label="x",
    )
    db_session.add(step)
    await db_session.flush()
    db_session.add(plan_row(task, step, other.id))

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_operation_user_must_match_plan_owner(db_session: AsyncSession, logged_in_user: User, make_user) -> None:
    other = await make_user("owner-check2@example.com", "password-other")
    task = await make_chat_task(db_session, logged_in_user)
    step = Step(
        task_id=task.id,
        order=1,
        tool_name="fake_external",
        tool_call_id="c-1",
        risk_level="EXTERNAL",
        input={},
        status="waiting_approval",
        label="x",
    )
    db_session.add(step)
    await db_session.flush()
    plan = plan_row(task, step, logged_in_user.id)
    db_session.add(plan)
    await db_session.flush()
    db_session.add(Operation(plan_id=plan.id, user_id=other.id, status=OperationStatus.AWAITING_APPROVAL))

    with pytest.raises(IntegrityError):
        await db_session.flush()
