"""작업 취소와 실행 회차 기록(1단계 보강, BUILD_SPEC 4.6, 9.2).

- queued·승인 대기 task는 바로 cancelled. 아직 결정되지 않은 승인은 cancelled, 실행하지 않은 단계는 실패로 닫는다.
- 실행 중인 task는 cancel_requested. 실행 쪽은 다음 모델 호출·새 도구 실행 직전에 확인하고 멈춘다(cancelled).
- 취소는 이미 끝난 행동을 되돌리지 않는다. 취소 요청 전에 끝까지 끝난 작업은 done으로 남는다.
- 실행(run)과 재개(resume)는 agent_runs에 회차별로 남는다.
"""

import httpx
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.agent import MyAgent
from app.agent.guard import GuardAgent
from app.agent.runner import RunServices, run_task
from app.config import Settings
from app.models import AgentRun, Approval, Conversation, Message, Step, Task, Todo, User
from app.models.enums import ApprovalStatus, MessageRole, StepStatus, TaskOrigin, TaskStatus
from app.push import PushNotifier
from tests.conftest import UserFactory, login
from tests.fake_push import FakePushSender
from tests.fake_tools import FakeExternalCounter
from tests.model_scripts import GuardScript, call, registered, scripted, text
from tests.test_agent_run import send


async def cancel(client: httpx.AsyncClient, task_id: int) -> httpx.Response:
    return await client.post(f"/api/tasks/{task_id}/cancel")


async def queued_task(session: AsyncSession, user: User) -> Task:
    conversation = Conversation(user_id=user.id, title="대화")
    session.add(conversation)
    await session.flush()
    message = Message(conversation_id=conversation.id, role=MessageRole.USER, content="우유 사기 추가해줘")
    session.add(message)
    await session.flush()
    task = Task(conversation_id=conversation.id, user_id=user.id, trigger_message_id=message.id, origin=TaskOrigin.CHAT)
    session.add(task)
    await session.commit()
    return task


@pytest.fixture
def services(
    agent: MyAgent, guard: GuardAgent, session_factory: async_sessionmaker[AsyncSession], test_settings: Settings
) -> RunServices:
    notifier = PushNotifier(session_factory, test_settings, FakePushSender())
    return RunServices(
        agent=agent,
        guard=guard,
        session_factory=session_factory,
        settings=test_settings,
        notifier=notifier,
        http=httpx.AsyncClient(),
    )


async def test_cancel_queued_task_never_runs(
    client: httpx.AsyncClient,
    agent: MyAgent,
    services: RunServices,
    db_session: AsyncSession,
    logged_in_user: User,
) -> None:
    task = await queued_task(db_session, logged_in_user)

    response = await cancel(client, task.id)
    model = scripted(call("add_todo", title="우유 사기"), text("추가했습니다."))
    with agent.override(model=model.model()):
        await run_task(services, task.id, prompt="우유 사기 추가해줘")  # 워크플로가 늦게 시작된 경우

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert model.calls == 0
    await db_session.refresh(task)
    assert task.status == TaskStatus.CANCELLED
    reply = await db_session.scalar(select(Message).where(Message.task_id == task.id, Message.role == "assistant"))
    assert reply is not None and "취소" in reply.content


async def test_cancel_waiting_approval_closes_pending_approvals(
    client: httpx.AsyncClient,
    agent: MyAgent,
    guard: GuardAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    with (
        agent.override(model=scripted(call("fake_external", value=1)).model()),
        guard.override(model=GuardScript().model()),
    ):
        task = await send(client, conversation, "외부 도구 실행해줘")
    assert task["status"] == "waiting_approval"
    approval_id = task["approvals"][0]["id"]

    response = await cancel(client, task["id"])
    decided = await client.post(f"/api/approvals/{approval_id}/approve")

    assert response.json()["status"] == "cancelled"
    # 이미 결정된(여기서는 취소된) 승인은 지금 상태를 돌려줄 뿐 실행하지 않는다(같은 결정 재요청 규칙).
    assert decided.status_code == 200
    assert decided.json()["approval"]["status"] == "cancelled"
    assert fake_external_counter.calls == []
    approval = await db_session.get_one(Approval, approval_id)
    await db_session.refresh(approval)
    assert approval.status == ApprovalStatus.CANCELLED
    step = await db_session.scalar(select(Step).where(Step.task_id == task["id"]))
    assert step is not None and step.status == StepStatus.FAILED


async def test_cancel_running_task_stops_before_next_tool(
    client: httpx.AsyncClient,
    agent: MyAgent,
    services: RunServices,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
    logged_in_user: User,
) -> None:
    """실행 중 취소 요청 → 모델이 부른 다음 도구를 실행하지 않고 cancelled로 끝난다."""
    task = await queued_task(db_session, logged_in_user)
    calls = 0

    async def cancel_then_call_tool(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        # 사용자가 실행 도중 취소를 누른 상황(API가 하는 일과 같다: running → cancel_requested)
        async with session_factory() as session:
            await session.execute(update(Task).where(Task.id == task.id).values(status=TaskStatus.CANCEL_REQUESTED))
            await session.commit()
        return ModelResponse(parts=[call("add_todo", title="우유 사기")])

    with agent.override(model=registered(cancel_then_call_tool)):
        await run_task(services, task.id, prompt="우유 사기 추가해줘")

    await db_session.refresh(task)
    assert task.status == TaskStatus.CANCELLED
    assert calls == 1
    assert await db_session.scalar(select(func.count()).select_from(Todo)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Step).where(Step.task_id == task.id)) == 0


async def test_finished_work_is_not_hidden_by_late_cancel(
    agent: MyAgent,
    services: RunServices,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
    logged_in_user: User,
) -> None:
    """마지막 답을 받은 뒤에 온 취소 요청은 결과를 바꾸지 않는다(이미 한 일을 숨기지 않는다)."""
    task = await queued_task(db_session, logged_in_user)

    async def answer_then_cancel(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        async with session_factory() as session:
            await session.execute(update(Task).where(Task.id == task.id).values(status=TaskStatus.CANCEL_REQUESTED))
            await session.commit()
        return ModelResponse(parts=[TextPart("다 했습니다.")])

    with agent.override(model=registered(answer_then_cancel)):
        await run_task(services, task.id, prompt="안녕")

    await db_session.refresh(task)
    assert task.status == TaskStatus.DONE
    assert task.final_answer == "다 했습니다."


async def test_cancel_running_task_marks_cancel_requested(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User
) -> None:
    task = await queued_task(db_session, logged_in_user)
    task.status = TaskStatus.RUNNING
    await db_session.commit()

    first = await cancel(client, task.id)
    again = await cancel(client, task.id)

    assert first.status_code == again.status_code == 200
    assert first.json()["status"] == again.json()["status"] == "cancel_requested"


async def test_cannot_cancel_finished_or_other_users_task(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User, make_user: UserFactory
) -> None:
    mine = await queued_task(db_session, logged_in_user)
    mine.status = TaskStatus.DONE
    other = await make_user("other@example.com", "password-other")
    theirs = await queued_task(db_session, other)
    await db_session.commit()

    assert (await cancel(client, mine.id)).status_code == 409
    assert (await cancel(client, theirs.id)).status_code == 404
    await db_session.refresh(theirs)
    assert theirs.status == TaskStatus.QUEUED


async def test_runs_are_recorded_per_round(
    client: httpx.AsyncClient,
    agent: MyAgent,
    guard: GuardAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    model = scripted(call("fake_external", value=1), text("끝냈습니다."))
    with agent.override(model=model.model()), guard.override(model=GuardScript().model()):
        task = await send(client, conversation, "외부 도구 실행해줘")
        await client.post(f"/api/approvals/{task['approvals'][0]['id']}/approve")

    runs = (
        await db_session.scalars(select(AgentRun).where(AgentRun.task_id == task["id"]).order_by(AgentRun.round))
    ).all()
    assert [(r.round, r.kind, r.status) for r in runs] == [
        (1, "run", TaskStatus.WAITING_APPROVAL),
        (2, "resume", TaskStatus.DONE),
    ]
    assert all(r.finished_at is not None for r in runs)
    assert sum(r.request_count for r in runs) == model.calls


async def test_login_still_needed_for_cancel(
    client: httpx.AsyncClient, db_session: AsyncSession, make_user: UserFactory
) -> None:
    user = await make_user("me@example.com", "password-me")
    task = await queued_task(db_session, user)

    assert (await cancel(client, task.id)).status_code == 401
    await login(client, "me@example.com", "password-me")
    assert (await cancel(client, task.id)).status_code == 200
