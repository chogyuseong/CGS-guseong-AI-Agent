"""3단계 보강(3): outbox — DB 변경과 워크플로 시작 사이의 중단 (BUILD_SPEC 4.4, 15절 12·17).

task 저장과 실행 시작, 마지막 승인 결정과 재개 시작은 서로 다른 시스템(앱 DB, DBOS)이라 한 트랜잭션이 아니다.
그래서 같은 트랜잭션에 outbox 요청을 남기고, 시작은 나중에 몇 번이든 다시 할 수 있게 한다.
- 키(dedupe_key)와 워크플로 ID가 고정이라 여러 번 처리해도 한 번만 실행된다.
- 시작에 실패하면 pending으로 남기고 시도 횟수·다음 시도 시각·오류 종류를 기록한다. 10번 실패하면 failed.
- 처리하는 곳: API 커밋 직후, 서버 시작 때, 1분 검사 때.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import outbox
from app.agent.agent import MyAgent
from app.automation.check import AutomationServices, run_check
from app.models import OutboxEvent, Task, Todo
from app.models.enums import OutboxStatus, TaskStatus
from tests.conftest import WaitingLauncher, login, make_client
from tests.fake_service import FakeExternalService
from tests.fake_tools import FakeExternalCounter
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import message_body
from tests.test_approvals import approve, external_then_answer, task_detail


class BrokenLauncher(WaitingLauncher):
    """워크플로 시작이 실패하는 시작기(DBOS 연결 끊김 등)."""

    async def start_run(self, task_id: int, prompt: str) -> Any:
        raise ConnectionError("dbos down")

    async def start_resume(self, task_id: int, workflow_id: str) -> Any:
        raise ConnectionError("dbos down")


async def post_message(client: httpx.AsyncClient, conversation: int, content: str) -> int:
    response = await client.post(f"/api/conversations/{conversation}/messages", json=message_body(content))
    assert response.status_code == 202, response.text
    return response.json()["task_id"]


async def events_of(db_session: AsyncSession, task_id: int) -> list[OutboxEvent]:
    db_session.expire_all()
    rows = await db_session.scalars(select(OutboxEvent).where(OutboxEvent.task_id == task_id).order_by(OutboxEvent.id))
    return list(rows)


async def pending_ref(db_session: AsyncSession, task_id: int) -> outbox.EventRef:
    """처리 표시와 관계없이 그 task의 run 요청을 다시 시작할 때 쓰는 값."""
    [event] = await events_of(db_session, task_id)
    return outbox.EventRef(event.id, event.kind, event.task_id, event.workflow_id, event.attempts)


def todo_script() -> Any:
    return scripted(call("add_todo", title="우유 사기"), text("추가했습니다."))


async def test_message_creates_run_event_and_dispatches_it(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession
) -> None:
    with agent.override(model=todo_script().model()):
        task_id = await post_message(client, conversation, "우유 사기 추가해줘")

    [event] = await events_of(db_session, task_id)
    assert event.kind == outbox.OutboxKind.RUN
    assert event.dedupe_key == f"run:{task_id}"
    assert event.workflow_id == f"task-{task_id}"
    assert event.status == OutboxStatus.DISPATCHED
    assert (await task_detail(client, task_id))["status"] == "done"


async def test_failed_start_is_kept_and_retried_once(
    app: FastAPI,
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    working = app.state.launcher
    app.state.launcher = BrokenLauncher()
    with agent.override(model=todo_script().model()):
        task_id = await post_message(client, conversation, "우유 사기 추가해줘")  # 202는 그대로
        app.state.launcher = working
        [pending] = await events_of(db_session, task_id)
        assert pending.status == OutboxStatus.PENDING
        assert pending.attempts == 1 and pending.last_error == "ConnectionError"
        assert pending.next_attempt_at > datetime.now(UTC)
        assert (await db_session.get_one(Task, task_id)).status == TaskStatus.QUEUED

        later = pending.next_attempt_at + timedelta(seconds=1)
        # dispatcher가 두 번 돌아도(검사 겹침, 서버 재시작) 한 번만 실행된다. 같은 요청을 두 번 시작하는 경우는
        # 처리 표시 전에 죽은 경우와 같다. 진짜 동시 시작은 test_double_dispatch_of_same_event_runs_once.
        await outbox.dispatch(WaitingLauncher(), session_factory, later)
        await outbox.start_event(WaitingLauncher(), session_factory, (await pending_ref(db_session, task_id)))

    [event] = await events_of(db_session, task_id)
    todos = (await db_session.scalars(select(Todo).where(Todo.title == "우유 사기"))).all()
    assert event.status == OutboxStatus.DISPATCHED
    assert len(todos) == 1
    assert (await task_detail(client, task_id))["status"] == "done"


async def test_not_due_event_waits_for_retry_time(
    app: FastAPI,
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    working = app.state.launcher
    app.state.launcher = BrokenLauncher()
    task_id = await post_message(client, conversation, "우유 사기 추가해줘")
    app.state.launcher = working

    started = await outbox.dispatch(WaitingLauncher(), session_factory, datetime.now(UTC))

    assert started == 0
    assert (await events_of(db_session, task_id))[0].status == OutboxStatus.PENDING


async def test_gives_up_after_max_attempts(
    app: FastAPI,
    client: httpx.AsyncClient,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app.state.launcher = BrokenLauncher()
    task_id = await post_message(client, conversation, "우유 사기 추가해줘")
    for day in range(1, outbox.MAX_ATTEMPTS):  # 매번 다음 시도 시각이 지난 뒤에 다시 처리한다
        await outbox.dispatch(BrokenLauncher(), session_factory, datetime.now(UTC) + timedelta(days=day))

    [event] = await events_of(db_session, task_id)
    assert event.status == OutboxStatus.FAILED
    assert event.attempts == outbox.MAX_ATTEMPTS


async def test_resume_event_is_retried_and_runs_once(
    app: FastAPI,
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    fake_service: FakeExternalService,
    fake_external_counter: FakeExternalCounter,
) -> None:
    """마지막 승인 결정과 재개 요청이 한 트랜잭션에 남는다. 재개 시작이 실패해도 나중에 한 번만 재개된다."""
    with agent.override(model=external_then_answer().model()):
        task_id = await post_message(client, conversation, "외부 도구 실행해줘")
        approval_id = (await task_detail(client, task_id))["approvals"][0]["id"]
        working = app.state.launcher
        app.state.launcher = BrokenLauncher()
        assert (await approve(client, approval_id)).status_code == 200
        app.state.launcher = working
        events = await events_of(db_session, task_id)
        assert [e.kind for e in events] == [outbox.OutboxKind.RUN, outbox.OutboxKind.RESUME]
        assert events[1].status == OutboxStatus.PENDING
        assert events[1].workflow_id == f"task-{task_id}-resume-1"

        later = events[1].next_attempt_at + timedelta(seconds=1)
        await outbox.dispatch(WaitingLauncher(), session_factory, later)
        await outbox.dispatch(WaitingLauncher(), session_factory, later + timedelta(minutes=5))

    assert len(fake_service.effects()) == 1
    assert (await events_of(db_session, task_id))[1].status == OutboxStatus.DISPATCHED
    assert (await task_detail(client, task_id))["status"] == "done"


async def test_minute_check_dispatches_due_events(
    app: FastAPI,
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    automation_services: AutomationServices,
) -> None:
    app.state.launcher = BrokenLauncher()
    with agent.override(model=todo_script().model()):
        task_id = await post_message(client, conversation, "우유 사기 추가해줘")
        [pending] = await events_of(db_session, task_id)
        await run_check(automation_services, pending.next_attempt_at + timedelta(seconds=1))

    assert (await events_of(db_session, task_id))[0].status == OutboxStatus.DISPATCHED
    assert (await db_session.get_one(Task, task_id)).status == TaskStatus.DONE


async def test_double_dispatch_of_same_event_runs_once(committed_app: FastAPI, make_committed_user) -> None:
    """실제 DB 연결 두 개로 같은 요청을 동시에 시작해도 워크플로는 한 번만 돈다(고정된 워크플로 ID)."""
    agent: MyAgent = committed_app.state.agent
    factory = committed_app.state.session_factory
    user = await make_committed_user("outbox-race@example.com", "password-race")
    committed_app.state.launcher = BrokenLauncher()
    async with make_client(committed_app) as client:
        assert (await login(client, "outbox-race@example.com", "password-race")).status_code == 200
        conversation = (await client.post("/api/conversations", json={})).json()["id"]
        task_id = await post_message(client, conversation, "우유 사기 추가해줘")
        later = datetime.now(UTC) + timedelta(minutes=5)
        with agent.override(model=todo_script().model()):
            await asyncio.gather(
                outbox.dispatch(WaitingLauncher(), factory, later),
                outbox.dispatch(WaitingLauncher(), factory, later),
            )
        detail = await task_detail(client, task_id)
    async with factory() as session:
        todos = (await session.scalars(select(Todo).where(Todo.user_id == user.id))).all()
    assert len(todos) == 1
    assert detail["status"] == "done"
