"""3단계 보강(2): 취소와 외부 실행 경합 (BUILD_SPEC 4.6, 15절 18).

취소는 이미 끝난 외부 행동을 되돌리지 않는다. 대신 숨기지도 않는다.
- 외부 요청이 나간 뒤 취소가 오면: 결과를 끝까지 기록(영수증)하고, task는 cancelled, 카드와 답변이 완료된 효과를 알린다.
- 승인했지만 아직 실행하지 않았을 때 취소하면: operation도 cancelled, 서버를 다시 켜도 실행하지 않는다.
"""

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals
from app.agent.agent import MyAgent
from app.agent.executor import Execution
from app.agent.workflows import restart_orphaned_tasks
from app.models import ActionPlan, Message, Operation, User
from app.models.enums import MessageRole, OperationStatus
from tests import fake_tools
from tests.conftest import WaitingLauncher
from tests.fake_service import FakeExternalService
from tests.fake_tools import FakeExternalCounter
from tests.test_agent_run import send
from tests.test_approvals import approve, external_then_answer, task_detail


async def operation_of(db_session: AsyncSession, task_id: int) -> Operation:
    db_session.expire_all()
    operation = await db_session.scalar(
        select(Operation).join(ActionPlan, ActionPlan.id == Operation.plan_id).where(ActionPlan.task_id == task_id)
    )
    assert operation is not None
    return operation


async def test_cancel_during_external_call_keeps_completed_effect_visible(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
    monkeypatch,
) -> None:
    original = fake_tools.send_action
    cancel_status: list[int] = []

    async def cancel_then_send(started: Execution, value: int):
        # 외부 요청을 보내는 바로 그때 사용자가 취소를 누른다.
        response = await client.post(f"/api/tasks/{task_id[0]}/cancel")
        cancel_status.append(response.status_code)
        return await original(started, value)

    monkeypatch.setattr(fake_tools, "send_action", cancel_then_send)
    task_id: list[int] = []
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        task_id.append(task["id"])
        await approve(client, task["approvals"][0]["id"])

    detail = await task_detail(client, task["id"])
    operation = await operation_of(db_session, task["id"])
    replies = list(
        await db_session.scalars(
            select(Message.content).where(Message.task_id == task["id"], Message.role == MessageRole.ASSISTANT)
        )
    )
    assert cancel_status == [200]
    assert len(fake_service.effects()) == 1
    assert operation.status == OperationStatus.SUCCEEDED
    assert detail["status"] == "cancelled"
    assert detail["approvals"][0]["receipt_confirmed"] is True
    assert detail["steps"][0]["status"] == "done"
    assert len(replies) == 1 and "취소 전에 끝난 외부 작업 1건" in replies[0]


async def test_cancel_after_approval_before_execution_never_runs(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
) -> None:
    user_id = logged_in_user.id
    with agent.override(model=external_then_answer().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await approvals.decide(db_session, task["approvals"][0]["id"], user_id, approve=True)
        cancelled = await client.post(f"/api/tasks/{task['id']}/cancel")
        await restart_orphaned_tasks(WaitingLauncher(), session_factory)

    operation = await operation_of(db_session, task["id"])
    assert cancelled.json()["status"] == "cancelled"
    assert operation.status == OperationStatus.CANCELLED
    assert fake_service.effects() == []
    assert (await task_detail(client, task["id"]))["status"] == "cancelled"
