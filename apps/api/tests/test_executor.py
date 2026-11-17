"""2단계 보강(2): EXTERNAL 실행기의 DB 검사(BUILD_SPEC 3.3)와 영수증(4.2, 절대 원칙 10).

호출 ID와 approved 여부만 보지 않는다. 실행 직전에 다음을 모두 확인한다.
- 소유권(사용자·task·계획), 도구 이름, 인자 해시 → 어긋나면 PermissionError(권한 위반은 일반 실패로 삼키지 않는다)
- 도구 버전, 연결, 실행 유효 시간, task 취소, 이미 실행됨, 다른 시도 진행 중 → 실행하지 않고 그 이유의 결과
- 재개할 때 도구는 모델이 낸 인자가 아니라 계획에 저장된 인자로 실행된다(ToolApproved.override_args).
- 성공은 영수증이 있어야 한다. 성공한 operation을 다시 부르면 외부를 다시 부르지 않고 영수증을 돌려준다.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals
from app.agent.agent import MyAgent
from app.agent.history import dump_messages, load_messages
from app.agent.plans import ExternalToolSpec
from app.agent.results import ToolResult, ToolStatus
from app.agent.toolsets import EXTERNAL_TOOL_SPECS
from app.agent.tracking import receipt_checked
from app.models import ActionPlan, Approval, Connection, ExecutionReceipt, Operation, Step, Task, User
from app.models.enums import OperationStatus, RiskLevel, StepStatus, TaskStatus
from tests.fake_tools import FakeExternalCounter, fake_external
from tests.model_scripts import registered, text
from tests.test_agent_run import send
from tests.test_approvals import approve, direct_context, external_then_answer, task_detail


async def waiting_task(client: httpx.AsyncClient, agent: MyAgent, conversation: int) -> dict:
    """fake_external(value=7) 승인 대기 task를 만든다(재개는 하지 않는다)."""
    with agent.override(model=external_then_answer().model()):
        return await send(client, conversation, "외부 도구 실행해줘")


async def decide_only(db_session: AsyncSession, approval_id: int, user_id: int) -> None:
    """재개 워크플로를 시작하지 않고 승인만 기록한다(실행기를 직접 부르는 테스트용)."""
    await approvals.decide(db_session, approval_id, user_id, approve=True)


async def loaded(db_session: AsyncSession, task_id: int) -> tuple[Task, ActionPlan, Operation]:
    db_session.expire_all()
    task = await db_session.get_one(Task, task_id)
    plan = await db_session.scalar(select(ActionPlan).where(ActionPlan.task_id == task_id))
    assert plan is not None
    operation = await db_session.scalar(select(Operation).where(Operation.plan_id == plan.id))
    assert operation is not None
    return task, plan, operation


async def call_directly(
    task: Task, factory: async_sessionmaker[AsyncSession], tool_call_id: str, value: int = 7
) -> ToolResult:
    return await fake_external(direct_context(task, factory, tool_call_id), value=value)


# ---- 정상 경로: 저장된 인자로 실행하고 영수증을 남긴다 ----


async def test_approved_run_records_receipt_and_succeeds(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await approve(client, task["approvals"][0]["id"])

    _, plan, operation = await loaded(db_session, task["id"])
    receipt = await db_session.scalar(select(ExecutionReceipt).where(ExecutionReceipt.operation_id == operation.id))
    detail = await task_detail(client, task["id"])
    output = detail["steps"][0]["output"]
    assert fake_external_counter.count == 1
    assert operation.status == OperationStatus.SUCCEEDED
    assert operation.attempt_count == 1
    assert operation.started_at is not None and operation.finished_at is not None
    assert receipt is not None and receipt.user_id == plan.user_id
    assert output["operation_id"] == str(plan.operation_id)
    assert output["receipt_id"] == str(receipt.id)
    assert fake_external_counter.calls[0]["idempotency_key"] == str(plan.operation_id)
    assert detail["status"] == "done"


async def test_resume_uses_stored_args_even_if_history_was_altered(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    """승인 대기 중 기록 속 도구 인자가 바뀌어도(변조) 실행은 승인된 계획의 인자(7)로 한다."""
    task = await waiting_task(client, agent, conversation)
    stored = await db_session.get_one(Task, task["id"])
    messages = load_messages(stored.message_history or [])
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart) and part.tool_name == "fake_external":
                part.args = {"value": 99}
    await db_session.execute(update(Task).where(Task.id == task["id"]).values(message_history=dump_messages(messages)))
    await db_session.commit()

    with agent.override(model=external_then_answer().model()):
        await approve(client, task["approvals"][0]["id"])

    assert [c["value"] for c in fake_external_counter.calls] == [7]
    assert (await task_detail(client, task["id"]))["status"] == "done"


# ---- 권한 위반: PermissionError ----


async def test_direct_call_with_changed_args_is_refused(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_external_counter: FakeExternalCounter,
) -> None:
    task = await waiting_task(client, agent, conversation)
    await decide_only(db_session, task["approvals"][0]["id"], logged_in_user.id)
    row, plan, operation = await loaded(db_session, task["id"])

    with pytest.raises(PermissionError):
        await call_directly(row, session_factory, plan.tool_call_id, value=8)

    assert fake_external_counter.count == 0
    assert (await loaded(db_session, task["id"]))[2].status == OperationStatus.APPROVED


async def test_other_users_context_cannot_use_plan(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    make_user,
    fake_external_counter: FakeExternalCounter,
) -> None:
    """다른 사용자가 같은 task id·호출 id를 대도 그 계획을 실행할 수 없다."""
    task = await waiting_task(client, agent, conversation)
    await decide_only(db_session, task["approvals"][0]["id"], logged_in_user.id)
    other = await make_user("executor-other@example.com", "password-other")
    other_id = other.id
    await db_session.commit()
    row, plan, _ = await loaded(db_session, task["id"])
    forged = Task(id=row.id, user_id=other_id, conversation_id=row.conversation_id, origin=row.origin)

    with pytest.raises(PermissionError):
        await call_directly(forged, session_factory, plan.tool_call_id)

    assert fake_external_counter.count == 0


async def test_pending_plan_cannot_run(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    fake_external_counter: FakeExternalCounter,
) -> None:
    task = await waiting_task(client, agent, conversation)
    row, plan, _ = await loaded(db_session, task["id"])

    with pytest.raises(PermissionError):
        await call_directly(row, session_factory, plan.tool_call_id)

    assert fake_external_counter.count == 0


# ---- 실행하지 않고 이유를 돌려주는 경우 ----


async def prepared(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession, user_id: int
) -> tuple[Task, ActionPlan]:
    task = await waiting_task(client, agent, conversation)
    await decide_only(db_session, task["approvals"][0]["id"], user_id)
    row, plan, _ = await loaded(db_session, task["id"])
    row.status = TaskStatus.RUNNING  # 재개 워크플로가 잡은 상태를 흉내 낸다
    await db_session.commit()
    return row, plan


async def test_execute_window_expired_does_not_run(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_external_counter: FakeExternalCounter,
) -> None:
    row, plan = await prepared(client, agent, conversation, db_session, logged_in_user.id)
    past = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.execute(update(Approval).where(Approval.plan_id == plan.id).values(execute_before=past))
    await db_session.commit()

    result = await call_directly(row, session_factory, plan.tool_call_id)

    assert fake_external_counter.count == 0
    assert result.status == ToolStatus.FAILED
    assert result.error_code == "approval_window_expired"
    assert (await loaded(db_session, row.id))[2].status == OperationStatus.EXPIRED


async def test_cancel_requested_task_does_not_run(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_external_counter: FakeExternalCounter,
) -> None:
    row, plan = await prepared(client, agent, conversation, db_session, logged_in_user.id)
    await db_session.execute(update(Task).where(Task.id == row.id).values(status=TaskStatus.CANCEL_REQUESTED))
    await db_session.commit()

    result = await call_directly(row, session_factory, plan.tool_call_id)

    assert fake_external_counter.count == 0
    assert result.error_code == "task_cancelled"
    assert (await loaded(db_session, row.id))[2].status == OperationStatus.CANCELLED


async def test_tool_version_change_makes_plan_stale(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_external_counter: FakeExternalCounter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, plan = await prepared(client, agent, conversation, db_session, logged_in_user.id)
    monkeypatch.setitem(EXTERNAL_TOOL_SPECS, "fake_external", ExternalToolSpec(version="2"))

    result = await call_directly(row, session_factory, plan.tool_call_id)

    assert fake_external_counter.count == 0
    assert result.error_code == "plan_outdated"
    assert (await loaded(db_session, row.id))[2].status == OperationStatus.STALE


async def test_connection_removed_after_plan_does_not_run(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_external_counter: FakeExternalCounter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """계획 때 연결돼 있던 계정이 해제(또는 다른 계정으로 다시 연결)되면 실행하지 않는다."""
    monkeypatch.setitem(EXTERNAL_TOOL_SPECS, "fake_external", ExternalToolSpec(version="1", provider="google"))
    user_id = logged_in_user.id
    connection = Connection(user_id=user_id, provider="google", encrypted_token="x", scopes=[])
    db_session.add(connection)
    await db_session.flush()
    connection_id = connection.id
    await db_session.commit()
    row, plan = await prepared(client, agent, conversation, db_session, user_id)
    assert plan.connection_id == connection_id
    await db_session.delete(connection)
    await db_session.commit()  # 해제한 뒤
    db_session.add(Connection(user_id=user_id, provider="google", encrypted_token="y", scopes=[]))  # 다시 연결
    await db_session.commit()

    result = await call_directly(row, session_factory, plan.tool_call_id)

    assert fake_external_counter.count == 0
    assert result.error_code == "connection_changed"
    assert (await loaded(db_session, row.id))[2].status == OperationStatus.FAILED


async def test_executing_operation_is_not_started_again(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_external_counter: FakeExternalCounter,
) -> None:
    """다른 시도가 실행 중(executing)이면 결과를 모르므로 시작하지 않고 unknown을 돌려준다."""
    row, plan = await prepared(client, agent, conversation, db_session, logged_in_user.id)
    await db_session.execute(
        update(Operation).where(Operation.plan_id == plan.id).values(status=OperationStatus.EXECUTING)
    )
    await db_session.commit()

    result = await call_directly(row, session_factory, plan.tool_call_id)

    assert fake_external_counter.count == 0
    assert result.status == ToolStatus.UNKNOWN
    assert result.error_code == "already_executing"


async def test_succeeded_operation_returns_saved_receipt(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    fake_external_counter: FakeExternalCounter,
) -> None:
    """이미 성공한 operation을 다시 부르면 외부를 다시 부르지 않고 저장된 영수증을 돌려준다(BUILD_SPEC 4.3 끝)."""
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await approve(client, task["approvals"][0]["id"])
    row, plan, operation = await loaded(db_session, task["id"])

    result = await call_directly(row, session_factory, plan.tool_call_id)

    receipt = await db_session.scalar(select(ExecutionReceipt).where(ExecutionReceipt.operation_id == operation.id))
    assert receipt is not None
    assert fake_external_counter.count == 1
    assert result.status == ToolStatus.SUCCEEDED
    assert result.receipt_id == str(receipt.id)
    assert result.data == {"value": 7}


# ---- 완료 표시는 영수증 기준 ----


def test_external_success_without_receipt_is_unknown() -> None:
    no_receipt = ToolResult.success({"x": 1})
    with_receipt = ToolResult(status=ToolStatus.SUCCEEDED, data={"x": 1}, receipt_id="3")

    assert receipt_checked(no_receipt, RiskLevel.EXTERNAL).status == ToolStatus.UNKNOWN
    assert receipt_checked(no_receipt, RiskLevel.EXTERNAL).error_code == "no_receipt"
    assert receipt_checked(with_receipt, RiskLevel.EXTERNAL) is with_receipt
    assert receipt_checked(no_receipt, RiskLevel.INTERNAL) is no_receipt


async def test_two_approvals_in_one_round_each_run_once(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    def two_calls(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
            return ModelResponse(parts=[text("둘 다 처리했습니다.")])
        calls = [ToolCallPart("fake_external", {"value": 1}, "c-a"), ToolCallPart("fake_external", {"value": 2}, "c-b")]
        return ModelResponse(parts=calls)

    with agent.override(model=registered(two_calls)):
        task = await send(client, conversation, "두 번 실행해줘")
        for item in task["approvals"]:
            await approve(client, item["id"])

    db_session.expire_all()
    statuses = list(
        await db_session.scalars(
            select(Operation.status)
            .join(ActionPlan, ActionPlan.id == Operation.plan_id)
            .where(ActionPlan.task_id == task["id"])
        )
    )
    assert sorted(c["value"] for c in fake_external_counter.calls) == [1, 2]
    assert statuses == [OperationStatus.SUCCEEDED, OperationStatus.SUCCEEDED]
    steps = list(await db_session.scalars(select(Step.status).where(Step.task_id == task["id"])))
    assert steps == [StepStatus.DONE, StepStatus.DONE]
