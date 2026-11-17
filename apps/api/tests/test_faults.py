"""3단계 보강(1): 장애 주입과 결과 재조회 (BUILD_SPEC 13절 3단계, 4.1~4.3, 15절 12~16).

서버가 외부 실행 도중 죽으면 워크플로는 복구 때 다시 돌고, 도구 실행 기록(step)이 running으로 남아 있다.
그때 같은 작업을 다시 보내지 않고 외부 서비스에 operation_id로 결과를 물어 확정한다(reconcile).

각 테스트는 "그 지점에서 프로세스가 죽었을 때 남는 DB·외부 상태"를 만든 뒤 재개 워크플로를 실제로 돌린다.

    지점                          남는 상태                              복구 결과
    1 외부 요청 전                 executing, 외부 기록 없음              재조회 404 → failed(not_executed), 효과 0
    2 외부 처리 후 응답 전          executing, 외부 기록 있음              재조회 → succeeded + 영수증, 효과 1
    3 성공 응답 후 영수증 저장 전   (2와 같음)                             (2와 같음)
    4 영수증 저장 후 워크플로 완료 전 succeeded + 영수증, step running       외부 호출 없이 영수증 재사용, 효과 1
    5 승인 커밋 후 재개 등록 전     tests/test_fake_service.py             서버 시작 때 재개, 효과 1
    재조회도 실패                  executing, 조회 오류                   unknown 유지, task needs_review

장애 결과 표는 docs/FAULTS.md에도 둔다.
"""

from datetime import UTC, datetime

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals
from app.agent.agent import MyAgent
from app.agent.workflows import start_resume_if_waiting
from app.models import ActionPlan, ExecutionReceipt, Operation, Step, Task, User
from app.models.enums import OperationStatus, StepStatus, TaskStatus
from tests.conftest import WaitingLauncher
from tests.fake_service import FakeExternalService
from tests.fake_tools import FakeExternalCounter
from tests.test_agent_run import send
from tests.test_approvals import external_then_answer, task_detail
from tests.test_fake_service import post_action


async def approved_plan(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession, user_id: int
) -> tuple[dict, ActionPlan]:
    """fake_external(value=7) 승인까지 커밋한다(재개는 아직)."""
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
    await approvals.decide(db_session, task["approvals"][0]["id"], user_id, approve=True)
    plan = await db_session.scalar(select(ActionPlan).where(ActionPlan.task_id == task["id"]))
    assert plan is not None
    return task, plan


async def crash_during_execution(db_session: AsyncSession, plan: ActionPlan) -> None:
    """begin_execution을 통과한 뒤(operation executing, step running) 프로세스가 죽은 상태."""
    now = datetime.now(UTC)
    await db_session.execute(
        update(Operation)
        .where(Operation.plan_id == plan.id)
        .values(status=OperationStatus.EXECUTING, attempt_count=1, started_at=now)
    )
    await db_session.execute(update(Step).where(Step.id == plan.step_id).values(status=StepStatus.RUNNING))
    await db_session.commit()


async def recover(agent: MyAgent, factory: async_sessionmaker[AsyncSession], task_id: int) -> None:
    """서버가 다시 켜져 재개 워크플로가 도는 것과 같다(모델 응답은 대본)."""
    with agent.override(model=external_then_answer().model()):
        await start_resume_if_waiting(WaitingLauncher(), factory, task_id)


async def state(db_session: AsyncSession, task_id: int) -> tuple[Task, Operation, Step, ExecutionReceipt | None]:
    db_session.expire_all()
    task = await db_session.get_one(Task, task_id)
    plan = await db_session.scalar(select(ActionPlan).where(ActionPlan.task_id == task_id))
    assert plan is not None
    operation = await db_session.scalar(select(Operation).where(Operation.plan_id == plan.id))
    assert operation is not None
    step = await db_session.get_one(Step, plan.step_id)
    receipt = await db_session.scalar(select(ExecutionReceipt).where(ExecutionReceipt.operation_id == operation.id))
    return task, operation, step, receipt


# ---- 지점 1: 외부 요청 전 ----


async def test_crash_before_request_is_confirmed_not_executed(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
) -> None:
    task, plan = await approved_plan(client, agent, conversation, db_session, logged_in_user.id)
    await crash_during_execution(db_session, plan)

    await recover(agent, session_factory, task["id"])

    stored, operation, step, receipt = await state(db_session, task["id"])
    assert fake_service.effects() == []
    assert fake_external_counter.count == 0  # 다시 보내지 않았다
    assert operation.status == OperationStatus.FAILED
    assert operation.error_code == "not_executed"
    assert receipt is None
    assert step.status == StepStatus.FAILED
    assert stored.status == TaskStatus.DONE


# ---- 지점 2·3: 외부 처리 후 응답 전 / 성공 응답 후 영수증 저장 전 ----


async def test_crash_after_external_success_reconciles_to_receipt(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
) -> None:
    task, plan = await approved_plan(client, agent, conversation, db_session, logged_in_user.id)
    await crash_during_execution(db_session, plan)
    await post_action(fake_service, str(plan.operation_id), 7)  # 외부는 이미 처리했다

    await recover(agent, session_factory, task["id"])

    stored, operation, step, receipt = await state(db_session, task["id"])
    effects = fake_service.effects()
    assert len(effects) == 1
    assert fake_external_counter.count == 0
    assert operation.status == OperationStatus.SUCCEEDED
    assert receipt is not None and receipt.external_resource_id == str(effects[0]["id"])
    assert step.status == StepStatus.DONE
    assert (step.output or {})["receipt_id"] == str(receipt.id)
    assert stored.status == TaskStatus.DONE


async def test_lost_response_without_crash_is_reconciled_at_once(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
    monkeypatch,
) -> None:
    """외부가 처리했는데 응답이 끊겼다(시간 초과). 실패로 단정하지 않고 바로 재조회해 성공을 확인한다."""
    original = fake_service.handle

    def lose_post_response(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if request.method == "POST":
            raise httpx.ReadTimeout("응답 유실", request=request)
        return response

    monkeypatch.setattr(fake_service, "handle", lose_post_response)
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await client.post(f"/api/approvals/{task['approvals'][0]['id']}/approve")

    stored, operation, step, receipt = await state(db_session, task["id"])
    assert len(fake_service.effects()) == 1
    assert operation.status == OperationStatus.SUCCEEDED
    assert receipt is not None
    assert step.status == StepStatus.DONE
    assert stored.status == TaskStatus.DONE


# ---- 지점 4: 영수증 저장 후 워크플로 완료 전 ----


async def test_crash_after_receipt_reuses_receipt(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
) -> None:
    task, plan = await approved_plan(client, agent, conversation, db_session, logged_in_user.id)
    await crash_during_execution(db_session, plan)
    created = (await post_action(fake_service, str(plan.operation_id), 7)).json()
    operation = await db_session.scalar(select(Operation).where(Operation.plan_id == plan.id))
    assert operation is not None
    db_session.add(
        ExecutionReceipt(
            operation_id=operation.id,
            user_id=operation.user_id,
            provider="fake",
            external_resource_id=str(created["id"]),
            summary={"data": {"value": 7}, "sources": []},
            confirmed_at=datetime.now(UTC),
        )
    )
    operation.status = OperationStatus.SUCCEEDED
    await db_session.commit()
    lookups_before = len(fake_service.effects())

    await recover(agent, session_factory, task["id"])

    stored, operation, step, receipt = await state(db_session, task["id"])
    assert len(fake_service.effects()) == lookups_before == 1
    assert fake_external_counter.count == 0
    assert operation.status == OperationStatus.SUCCEEDED
    assert receipt is not None
    assert step.status == StepStatus.DONE
    assert (step.output or {})["receipt_id"] == str(receipt.id)
    assert stored.status == TaskStatus.DONE


# ---- 재조회도 실패: 단정하지 않는다 ----


async def test_failed_lookup_keeps_unknown_and_needs_review(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
    monkeypatch,
) -> None:
    task, plan = await approved_plan(client, agent, conversation, db_session, logged_in_user.id)
    await crash_during_execution(db_session, plan)

    def lookup_down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("조회 불가", request=request)

    monkeypatch.setattr(fake_service, "handle", lookup_down)
    await recover(agent, session_factory, task["id"])

    stored, operation, step, receipt = await state(db_session, task["id"])
    assert operation.status == OperationStatus.UNKNOWN
    assert receipt is None
    assert step.status == StepStatus.UNKNOWN
    assert stored.status == TaskStatus.NEEDS_REVIEW
    assert (await task_detail(client, task["id"]))["approvals"][0]["receipt_confirmed"] is False
