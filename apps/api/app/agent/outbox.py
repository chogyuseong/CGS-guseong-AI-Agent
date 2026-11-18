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

진입점(누가 부르는지):
- 요청 남기기: routers/conversations.py(메시지 저장 트랜잭션에서 add_run_event),
  agent/approvals.py decide(마지막 결정 트랜잭션에서 add_resume_event). 커밋은 두 곳이 한다.
- 처리: routers/conversations.py·routers/approvals.py가 커밋 직후 dispatch(task_id=...),
  workflows.restart_orphaned_tasks(서버 시작 lifespan)가 dispatch(include_not_due=True),
  automation/check.run_check(1분 검사)가 due_events를 "find_outbox" 단계로 기록한 뒤 dispatch_events.
- 실제 시작은 OutboxLauncher를 구현한 workflows.WorkflowLauncher(테스트는 tests/conftest.py의 WaitingLauncher).

근거: BUILD_SPEC 4.4(DB 변경과 워크플로 시작), docs/DECISIONS.md 2026-11-17 "DBOS 트랜잭션 연계 대신 outbox",
"outbox 처리"(재시도 간격·10번·오류 종류만), "WorkflowLauncher.start_resume은 재개 워크플로 ID를 받는다".

고칠 때 주의:
- 워크플로 ID 형식(run_workflow_id, resume_workflow_id)은 다른 곳이 같은 형식을 가정한다. 바꾸면
  routers/conversations.py·automation/retention.py(지울 DBOS 기록 찾기), runner.notification_key(워크플로 밖 기본값),
  이미 저장된 tasks.workflow_id·outbox_events.workflow_id·DBOS 기록과의 호환을 함께 본다.
- dedupe_key 형식("run:{task}", "resume:{task}:{워크플로 ID}")은 outbox_events.dedupe_key UNIQUE
  (models/execution.py)와 짝이다.
- 상태 전이(pending → dispatched / pending / failed)는 docs/STATE_MACHINES.md 4절 표와 같아야 한다.
  함수 이름(add_run_event, add_resume_event, add_once, record, dispatch_events)을 바꾸면 그 표도 고친다
  (tests/test_state_machines.py가 이름을 확인한다).
- 관련 테스트: tests/test_outbox.py, tests/test_workflows.py, tests/test_faults.py(장애 결과 표 docs/FAULTS.md).
- last_error에는 오류 종류 이름만 넣는다(메시지에 개인 정보가 섞일 수 있어서, DECISIONS 2026-11-17).
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
# 실패 뒤 간격은 30초부터 두 배씩(retry_delay). 값은 DECISIONS 2026-11-17 "outbox 처리"에서 정했다.
FIRST_RETRY_DELAY = timedelta(seconds=30)
# 간격 상한(같은 결정). 오래 실패해도 다음 시도가 10분보다 늦어지지 않는다.
MAX_RETRY_DELAY = timedelta(minutes=10)
# 이만큼 실패하면 failed로 두고 더 시도하지 않는다(test_outbox.py가 이 값으로 반복한다).
MAX_ATTEMPTS = 10
# 한 번 처리에서 꺼내는 최대 요청 수. 남은 것은 다음 처리(1분 검사)가 가져간다.
BATCH_SIZE = 50
# resume_workflow_id가 지금 워크플로 ID에서 재개 번호를 읽는 패턴. ID 형식과 같이 바꾼다.
RESUME_SUFFIX = re.compile(r"-resume-(\d+)$")


class OutboxLauncher(Protocol):
    """dispatch가 워크플로를 시작할 때 쓰는 모양. 구현은 workflows.WorkflowLauncher(순환 import를 피해 Protocol로 둠).

    두 메서드는 고정된 워크플로 ID로 시작하므로 같은 요청을 여러 번 불러도 DBOS가 한 번만 실행한다.
    예외를 던지면 dispatch_events가 실패로 기록하고 나중에 다시 시도한다.
    """

    async def start_run(self, task_id: int, prompt: str) -> Any: ...

    async def start_resume(self, task_id: int, workflow_id: str) -> Any: ...


# ---- 워크플로 ID (같은 요청이면 항상 같은 ID) ----


def run_workflow_id(task_id: int) -> str:
    """task의 첫 실행 워크플로 ID. task 하나에 run은 한 번뿐이라 task id만으로 고정한다.

    DBOS는 같은 ID로 두 번 시작하면 기존 워크플로를 돌려주고 다시 실행하지 않는다(task 이중 실행 방지).
    "task-" 접두사는 예약 실행 ID("sched-…", "schedule-…", "watch-…")와 구별하려는 것이다(DECISIONS 2026-11-17).
    """
    return f"task-{task_id}"


def resume_workflow_id(task_id: int, current_workflow_id: str | None) -> str:
    """지금 task.workflow_id의 재개 번호 + 1. 첫 run(task-42) 뒤면 resume-1, resume-1 뒤면 resume-2.

    번호를 DB의 지금 워크플로 ID에서 계산하므로 승인 버튼을 동시에 눌러도 같은 ID가 나와 재개가 한 번만 돈다.
    current_workflow_id가 None(워크플로 밖에서 실행된 task)이면 1이다.
    """
    match = RESUME_SUFFIX.search(current_workflow_id or "")
    number = int(match.group(1)) + 1 if match else 1
    return f"task-{task_id}-resume-{number}"


# ---- 요청 남기기 (호출한 쪽의 트랜잭션 안에서, 커밋은 호출한 쪽) ----


def new_event(task: Task, kind: OutboxKind, key: str, workflow_id: str, now: datetime) -> OutboxEvent:
    """pending 요청 행을 만든다(세션에 넣지 않음).

    시작 시각을 now+30초로 두어 API의 즉시 처리와 1분 검사가 겹치지 않게 한다.

    사용자 메시지는 복사하지 않는다. run 요청은 처리할 때 task의 trigger 메시지에서 읽는다(start_event).
    """
    return OutboxEvent(
        user_id=task.user_id,
        task_id=task.id,
        kind=kind,
        dedupe_key=key,
        workflow_id=workflow_id,
        next_attempt_at=now + FIRST_RETRY_DELAY,
    )


async def add_once(session: AsyncSession, event: OutboxEvent) -> None:
    """같은 키의 요청이 이미 있으면 새로 만들지 않는다.

    호출한 쪽의 트랜잭션 안에서 session.add만 한다(flush·커밋 없음). 동시에 같은 키가 들어와 이 검사를 둘 다
    통과하면 dedupe_key UNIQUE 제약이 커밋 때 막는다(그 예외는 호출한 쪽 트랜잭션의 실패가 된다).
    """
    exists = await session.scalar(select(OutboxEvent.id).where(OutboxEvent.dedupe_key == event.dedupe_key))
    if exists is None:
        session.add(event)


async def add_run_event(session: AsyncSession, task: Task, now: datetime | None = None) -> None:
    """새 task의 실행 요청. task가 flush되어 id가 있어야 한다. 커밋은 호출한 쪽(메시지 저장 API)이 한다."""
    now = now or datetime.now(UTC)
    workflow_id = run_workflow_id(task.id)
    await add_once(session, new_event(task, OutboxKind.RUN, f"run:{task.id}", workflow_id, now))


async def add_resume_event(session: AsyncSession, task: Task, now: datetime | None = None) -> None:
    """마지막 승인 결정 뒤의 재개 요청. 커밋은 호출한 쪽(approvals.decide)이 결정과 함께 한다.

    키에 워크플로 ID가 들어가므로 같은 task가 재개 뒤 다시 승인 대기가 되면 다음 번호로 새 요청이 생긴다.
    """
    now = now or datetime.now(UTC)
    workflow_id = resume_workflow_id(task.id, task.workflow_id)
    key = f"resume:{task.id}:{workflow_id}"
    await add_once(session, new_event(task, OutboxKind.RESUME, key, workflow_id, now))


# ---- 처리 ----


@dataclass(frozen=True)
class EventRef:
    """처리할 요청의 값 복사본. ORM 객체를 세션 밖으로 들고 나가지 않으려고, 그리고 1분 검사에서는
    DBOS 단계 결과(find_outbox)로 직렬화되어 저장되므로 단순한 값만 둔다."""

    id: int
    kind: OutboxKind
    task_id: int
    workflow_id: str
    attempts: int


async def pending_events(
    factory: async_sessionmaker[AsyncSession], now: datetime, task_id: int | None, include_not_due: bool
) -> list[EventRef]:
    """pending 요청을 id 순으로 BATCH_SIZE개까지 읽는다(읽기만, 잠그지 않음).

    잠그지 않아 여러 dispatcher가 같은 요청을 집을 수 있지만, 고정 워크플로 ID라 실행은 한 번이고
    record가 pending일 때만 바꿔 결과도 한 번만 남는다(test_outbox.py의 동시 처리 테스트).
    """
    query = select(OutboxEvent).where(OutboxEvent.status == OutboxStatus.PENDING)
    if task_id is not None:
        query = query.where(OutboxEvent.task_id == task_id)
    if not include_not_due:
        query = query.where(OutboxEvent.next_attempt_at <= now)
    async with factory() as session:
        rows = await session.scalars(query.order_by(OutboxEvent.id).limit(BATCH_SIZE))
        return [EventRef(e.id, e.kind, e.task_id, e.workflow_id, e.attempts) for e in rows]


async def start_event(launcher: OutboxLauncher, factory: async_sessionmaker[AsyncSession], event: EventRef) -> None:
    """요청 하나의 워크플로를 시작한다(끝나길 기다리지 않음). 실패는 예외로 알린다(dispatch_events가 기록).

    재개는 요청에 저장된 워크플로 ID를 그대로 쓴다(결정 때 정한 번호, 지금 task.workflow_id로 다시 계산하지 않음).
    run은 task의 trigger 메시지를 프롬프트로 읽는다. 메시지가 없으면 빈 문자열로 시작한다.
    """
    if event.kind == OutboxKind.RESUME:
        await launcher.start_resume(event.task_id, event.workflow_id)
        return
    async with factory() as session:
        prompt = await session.scalar(
            select(Message.content).join(Task, Task.trigger_message_id == Message.id).where(Task.id == event.task_id)
        )
    await launcher.start_run(event.task_id, prompt or "")


def retry_delay(attempts: int) -> timedelta:
    """attempts번 실패한 뒤 기다릴 시간: 30초, 1분, 2분 … 최대 10분."""
    return min(FIRST_RETRY_DELAY * (2 ** (attempts - 1)), MAX_RETRY_DELAY)


async def record(
    factory: async_sessionmaker[AsyncSession], event: EventRef, now: datetime, error: Exception | None
) -> None:
    """결과를 남긴다. 이미 dispatched인 요청(다른 dispatcher가 먼저 처리)은 실패로 되돌리지 않는다.

    자기 세션을 열어 커밋한다. attempts는 읽을 때의 값 + 1이라 동시 실패가 겹치면 한 번으로 셀 수 있다
    (그래도 상한에 다다르면 failed가 된다). last_error는 오류 종류 이름만 200자까지(개인 정보가 섞이지 않게).
    """
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
    include_not_due=True는 서버 시작 때(restart_orphaned_tasks) 시도 시각과 무관하게 모두 처리하는 모양이다.
    워크플로 안(1분 검사)에서는 이 함수 대신 due_events를 단계로 기록하고 dispatch_events를 부른다.
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
    """요청을 하나씩 시작하고 결과를 기록한다. 시작한 수를 돌려준다.

    한 요청의 실패가 다른 요청을 막지 않게 모든 예외를 잡는다(로그에는 오류 종류만). record의 DB 오류는
    잡지 않으므로 그때는 예외가 호출한 쪽으로 나간다.
    """
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
