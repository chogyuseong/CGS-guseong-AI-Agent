"""outbox: 워크플로 시작 요청을 DB 변경과 같은 트랜잭션에 남기고, 나중에 몇 번이든 다시 처리한다(3단계 보강).

BUILD_SPEC 4.4: task 저장과 실행 워크플로 등록, 승인 결정과 재개 워크플로 등록 사이에 서버가 죽을 수 있다.
앱 DB(SQLAlchemy)와 DBOS 시스템 테이블은 같은 DB여도 한 트랜잭션이 아니므로 이렇게 한다.

    메시지 전송  ─ 한 트랜잭션 ─ messages + tasks(queued) + outbox_events(run:{task}, pending)
    마지막 결정  ─ 한 트랜잭션 ─ approvals(approved/rejected) + outbox_events(resume:{task}:{워크플로 ID}, pending)
                   ↓ 커밋 뒤
    dispatch: 워크플로를 고정된 ID로 시작 → dispatched
              시작이 실패하면 pending 그대로, attempts +1, next_attempt_at(뒤로 미룸), last_error(오류 종류만)
              MAX_ATTEMPTS번 실패하면 failed

다시 처리하는 곳: API 커밋 직후(그 task만), 서버 시작 때(모두), 1분 검사 때(시도 시각이 된 것).
워크플로 ID가 고정이라(task-{id}, task-{id}-resume-{n}) 같은 요청을 여러 번, 동시에 처리해도 DBOS가 한 번만 돌린다.
예약 실행은 워크플로 안에서 task를 만들고 바로 실행하므로(automation/check.py) outbox가 필요 없다.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Message, OutboxEvent, Task
from app.models.enums import OutboxKind, OutboxStatus

logger = logging.getLogger(__name__)

__all__ = ["OutboxKind", "OutboxStatus", "dispatch"]

# 처음 요청은 API가 커밋 직후 바로 시작한다. 주기 검사는 이 시간이 지난 것만 본다(같은 요청을 겹쳐 보내지 않게).
FIRST_RETRY_DELAY = timedelta(seconds=30)
MAX_RETRY_DELAY = timedelta(minutes=10)
MAX_ATTEMPTS = 10
BATCH_SIZE = 50
RESUME_SUFFIX = re.compile(r"-resume-(\d+)$")


class OutboxLauncher(Protocol):
    async def start_run(self, task_id: int, prompt: str) -> Any: ...

    async def start_resume(self, task_id: int, workflow_id: str) -> Any: ...


# ---- 워크플로 ID (같은 요청이면 항상 같은 ID) ----


def run_workflow_id(task_id: int) -> str:
    return f"task-{task_id}"


def resume_workflow_id(task_id: int, current_workflow_id: str | None) -> str:
    """지금 task.workflow_id의 재개 번호 + 1. 첫 run(task-42) 뒤면 resume-1, resume-1 뒤면 resume-2."""
    match = RESUME_SUFFIX.search(current_workflow_id or "")
    number = int(match.group(1)) + 1 if match else 1
    return f"task-{task_id}-resume-{number}"


# ---- 요청 남기기 (호출한 쪽의 트랜잭션 안에서, 커밋은 호출한 쪽) ----


def new_event(task: Task, kind: OutboxKind, key: str, workflow_id: str, now: datetime) -> OutboxEvent:
    return OutboxEvent(
        user_id=task.user_id,
        task_id=task.id,
        kind=kind,
        dedupe_key=key,
        workflow_id=workflow_id,
        next_attempt_at=now + FIRST_RETRY_DELAY,
    )


async def add_once(session: AsyncSession, event: OutboxEvent) -> None:
    """같은 키의 요청이 이미 있으면 새로 만들지 않는다."""
    exists = await session.scalar(select(OutboxEvent.id).where(OutboxEvent.dedupe_key == event.dedupe_key))
    if exists is None:
        session.add(event)


async def add_run_event(session: AsyncSession, task: Task, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    workflow_id = run_workflow_id(task.id)
    await add_once(session, new_event(task, OutboxKind.RUN, f"run:{task.id}", workflow_id, now))


async def add_resume_event(session: AsyncSession, task: Task, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    workflow_id = resume_workflow_id(task.id, task.workflow_id)
    key = f"resume:{task.id}:{workflow_id}"
    await add_once(session, new_event(task, OutboxKind.RESUME, key, workflow_id, now))


# ---- 처리 ----


@dataclass(frozen=True)
class EventRef:
    id: int
    kind: OutboxKind
    task_id: int
    workflow_id: str
    attempts: int


async def pending_events(
    factory: async_sessionmaker[AsyncSession], now: datetime, task_id: int | None, include_not_due: bool
) -> list[EventRef]:
    query = select(OutboxEvent).where(OutboxEvent.status == OutboxStatus.PENDING)
    if task_id is not None:
        query = query.where(OutboxEvent.task_id == task_id)
    if not include_not_due:
        query = query.where(OutboxEvent.next_attempt_at <= now)
    async with factory() as session:
        rows = await session.scalars(query.order_by(OutboxEvent.id).limit(BATCH_SIZE))
        return [EventRef(e.id, e.kind, e.task_id, e.workflow_id, e.attempts) for e in rows]


async def start_event(launcher: OutboxLauncher, factory: async_sessionmaker[AsyncSession], event: EventRef) -> None:
    if event.kind == OutboxKind.RESUME:
        await launcher.start_resume(event.task_id, event.workflow_id)
        return
    async with factory() as session:
        prompt = await session.scalar(
            select(Message.content).join(Task, Task.trigger_message_id == Message.id).where(Task.id == event.task_id)
        )
    await launcher.start_run(event.task_id, prompt or "")


def retry_delay(attempts: int) -> timedelta:
    return min(FIRST_RETRY_DELAY * (2 ** (attempts - 1)), MAX_RETRY_DELAY)


async def record(
    factory: async_sessionmaker[AsyncSession], event: EventRef, now: datetime, error: Exception | None
) -> None:
    """결과를 남긴다. 이미 dispatched인 요청(다른 dispatcher가 먼저 처리)은 실패로 되돌리지 않는다."""
    if error is None:
        values: dict[str, Any] = {"status": OutboxStatus.DISPATCHED, "dispatched_at": now, "last_error": None}
    else:
        attempts = event.attempts + 1
        values = {
            "attempts": attempts,
            "next_attempt_at": now + retry_delay(attempts),
            "last_error": type(error).__name__[:200],
            "status": OutboxStatus.FAILED if attempts >= MAX_ATTEMPTS else OutboxStatus.PENDING,
        }
    async with factory() as session:
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event.id, OutboxEvent.status == OutboxStatus.PENDING)
            .values(**values)
        )
        await session.commit()


async def dispatch(
    launcher: OutboxLauncher,
    factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
    *,
    task_id: int | None = None,
    include_not_due: bool = False,
) -> int:
    """pending 요청을 시작한다. 시작한 수를 돌려준다. 예외를 밖으로 내지 않는다(API 응답·검사를 막지 않게).

    task_id를 주면 그 task의 요청만(시도 시각과 무관하게) 처리한다. API가 커밋 직후 부르는 모양이다.
    """
    now = now or datetime.now(UTC)
    events = await pending_events(factory, now, task_id, include_not_due or task_id is not None)
    return await dispatch_events(launcher, factory, events, now)


async def due_events(factory: async_sessionmaker[AsyncSession], now: datetime) -> list[EventRef]:
    """1분 검사용: 시도 시각이 된 요청 목록. 워크플로 안에서는 이 목록을 단계로 기록한다(복구 때 같은 순서)."""
    return await pending_events(factory, now, None, include_not_due=False)


async def dispatch_events(
    launcher: OutboxLauncher, factory: async_sessionmaker[AsyncSession], events: list[EventRef], now: datetime
) -> int:
    started = 0
    for event in events:
        try:
            await start_event(launcher, factory, event)
        except Exception as error:
            logger.warning("outbox 시작 실패: event=%s kind=%s 오류=%s", event.id, event.kind, type(error).__name__)
            await record(factory, event, now, error)
            continue
        await record(factory, event, now, None)
        started += 1
    return started
