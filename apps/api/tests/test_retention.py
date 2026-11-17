"""5단계 보강(3): 실행 기록 보관 기간(BUILD_SPEC 5.2). EXECUTION_RETENTION_DAYS(7) · RECEIPT_RETENTION_DAYS(90).

끝난 작업이 보관 기간을 넘으면 모델 기록(tasks.message_history), 단계 입력·출력, 실행 계획 인자,
처리한 outbox 요청, DBOS 워크플로 기록을 지운다. 대화 메시지(사용자에게 보인 것)와 작업·단계의 상태·문구는 남는다.
끝나지 않은 작업은 건드리지 않는다(실행 중인 워크플로 기록을 지워 복구를 망가뜨리지 않는다).
영수증은 90일 뒤 지운다. 시각은 now로 넣는다.
"""

from datetime import UTC, datetime, timedelta

import httpx
from dbos import DBOS
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.agent import MyAgent
from app.automation.check import AutomationServices, run_check
from app.automation.retention import RETENTION_MINUTE, prune_expired_records
from app.config import Settings
from app.models import ActionPlan, AgentRun, ExecutionReceipt, Message, OutboxEvent, Step, Task
from tests.fake_tools import FakeExternalCounter
from tests.test_agent_run import send
from tests.test_approvals import approve, external_then_answer


async def finished_external_task(client: httpx.AsyncClient, agent: MyAgent, conversation: int) -> int:
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        assert (await approve(client, task["approvals"][0]["id"])).status_code == 200
    return task["id"]


async def workflow_ids(db_session: AsyncSession, task_id: int) -> list[str]:
    runs = await db_session.scalars(select(AgentRun.workflow_id).where(AgentRun.task_id == task_id))
    return [workflow_id for workflow_id in runs if workflow_id]


async def test_finished_task_payloads_are_pruned_after_execution_retention(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    fake_external_counter: FakeExternalCounter,
) -> None:
    task_id = await finished_external_task(client, agent, conversation)
    ids = await workflow_ids(db_session, task_id)
    assert len(ids) == 2  # 첫 실행 + 승인 뒤 재개

    report = await prune_expired_records(session_factory, test_settings, datetime.now(UTC) + timedelta(days=8))

    db_session.expire_all()
    assert report.tasks_pruned >= 1
    task = await db_session.get(Task, task_id)
    assert task is not None and task.status == "done" and task.message_history is None
    steps = (await db_session.scalars(select(Step).where(Step.task_id == task_id))).all()
    assert steps and all(step.input == {} and step.output is None for step in steps)
    assert all(step.label for step in steps)  # 타임라인 문구·상태는 남는다
    [plan] = (await db_session.scalars(select(ActionPlan).where(ActionPlan.task_id == task_id))).all()
    assert plan.canonical_args == {}
    outbox = await db_session.scalar(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.task_id == task_id)
    )
    assert outbox == 0
    for workflow_id in ids:
        assert await DBOS.get_workflow_status_async(workflow_id) is None
    assert await db_session.scalar(select(func.count()).select_from(ExecutionReceipt)) == 1  # 90일 전에는 남는다
    roles = await db_session.scalars(select(Message.role).where(Message.conversation_id == conversation))
    assert list(roles) == ["user", "assistant"]


async def test_records_inside_retention_are_kept(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    fake_external_counter: FakeExternalCounter,
) -> None:
    task_id = await finished_external_task(client, agent, conversation)

    await prune_expired_records(session_factory, test_settings, datetime.now(UTC) + timedelta(days=6))

    db_session.expire_all()
    task = await db_session.get(Task, task_id)
    assert task is not None and task.message_history
    for workflow_id in await workflow_ids(db_session, task_id):
        assert await DBOS.get_workflow_status_async(workflow_id) is not None


async def test_unfinished_task_is_not_touched(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    fake_external_counter: FakeExternalCounter,
) -> None:
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
    assert task["status"] == "waiting_approval"

    await prune_expired_records(session_factory, test_settings, datetime.now(UTC) + timedelta(days=30))

    db_session.expire_all()
    row = await db_session.get(Task, task["id"])
    assert row is not None and row.message_history
    [step] = (await db_session.scalars(select(Step).where(Step.task_id == task["id"]))).all()
    assert step.input == {"value": 7}
    [plan] = (await db_session.scalars(select(ActionPlan).where(ActionPlan.task_id == task["id"]))).all()
    assert plan.canonical_args
    for workflow_id in await workflow_ids(db_session, task["id"]):
        assert await DBOS.get_workflow_status_async(workflow_id) is not None


async def test_receipts_are_deleted_after_receipt_retention(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    fake_external_counter: FakeExternalCounter,
) -> None:
    await finished_external_task(client, agent, conversation)

    report = await prune_expired_records(session_factory, test_settings, datetime.now(UTC) + timedelta(days=91))

    db_session.expire_all()
    assert report.receipts_deleted == 1
    assert await db_session.scalar(select(func.count()).select_from(ExecutionReceipt)) == 0


async def test_hourly_check_runs_retention(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    automation_services: AutomationServices,
    fake_external_counter: FakeExternalCounter,
) -> None:
    task_id = await finished_external_task(client, agent, conversation)
    later = (datetime.now(UTC) + timedelta(days=8)).replace(minute=RETENTION_MINUTE)

    await run_check(automation_services, later.replace(minute=RETENTION_MINUTE + 1))
    db_session.expire_all()
    task = await db_session.get(Task, task_id)
    assert task is not None and task.message_history  # 17분이 아니면 정리하지 않는다

    await run_check(automation_services, later)
    db_session.expire_all()
    task = await db_session.get(Task, task_id)
    assert task is not None and task.message_history is None
