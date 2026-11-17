"""DBOS 워크플로: run_task(새 요청 실행)와 resume_task(승인 뒤 재개), 그리고 시작·복구 도우미.

왜 워크플로인가: 메시지 전송 API가 실행을 기다리지 않고 바로 202를 돌려주고, 서버가 작업 도중 죽었다 다시 켜져도
DBOS가 끝나지 않은 워크플로를 찾아 이어서 실행하게 하려는 것이다(명세 [내구성 실행: DBOS]).

    POST /api/conversations/7/messages
      → task(queued) 커밋 → start_run: SetWorkflowID("task-42")로 run_task_workflow 시작 → 202 {task_id}
    run_task_workflow("task-42")  ── runner.run_task (단계 구성은 runner.py)
    마지막 승인 결정
      → start_resume: SetWorkflowID("task-42-resume-1")로 resume_task_workflow 시작 → 200 바로 응답

워크플로 ID (같은 task가 두 번 실행되지 않게, 테스트 9):
- run: "task-{task_id}". 같은 ID로 두 번 시작하면 DBOS는 이미 있는 워크플로의 핸들을 돌려주고 다시 실행하지 않는다.
- 재개: "task-{task_id}-resume-{n}". n은 tasks.workflow_id에 적힌 지금 워크플로의 재개 번호 + 1이다.
  run 워크플로가 끝나 승인 대기가 되면 workflow_id는 "task-42"이므로 첫 재개는 1, 재개 run이 또 승인 대기가 되면
  workflow_id가 "task-42-resume-1"이므로 다음은 2다. 승인 버튼을 동시에 눌러도 같은 번호가 나와 한 번만 실행된다.

서버 재시작: DBOS.launch()가 이 프로세스(executor "local")의 끝나지 않은 워크플로를 찾아 다시 실행한다.
DBOS는 워크플로 코드가 바뀌면 옛 워크플로를 복구하지 않으므로(코드 해시가 버전) 버전을 WORKFLOW_VERSION으로 고정한다.
워크플로·단계 순서를 바꾸면 이 값을 올리고, 그 전에 끝나지 않은 작업이 없게 한다(docs/DURABILITY-CHECK.md).

워크플로 함수는 DBOS 규칙상 모듈에 한 번만 등록되고 인자는 DB에 저장되므로, 에이전트·DB 세션 공장 같은 실행 도구는
인자로 넘기지 않고 앱이 만들 때 bind_app_state로 알려 준 app.state에서 꺼낸다.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from dbos import DBOS, DBOSConfig, ScheduleInput, SetWorkflowID, WorkflowHandleAsync
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals, outbox, runner
from app.agent.durable import durable_step
from app.agent.outbox import resume_workflow_id, run_workflow_id
from app.agent.runner import RunServices
from app.automation import check
from app.config import Settings
from app.models import Message, Task
from app.models.enums import TaskOrigin, TaskStatus
from app.push import PushNotifier

logger = logging.getLogger(__name__)

# DBOS 애플리케이션 버전. 복구는 같은 버전의 워크플로만 한다. 단계 구성을 바꾸면 올린다.
WORKFLOW_VERSION = "myagent-4"  # 3단계 보강: 1분 검사에 find_outbox 단계 추가
DBOS_APP_NAME = "myagent"
# DBOS 시스템 테이블은 앱과 같은 DB의 별도 스키마에 둔다(앱 테이블, Alembic과 섞이지 않는다).
DBOS_SYSTEM_SCHEMA = "dbos"
# 6단계: 1분 검사 DBOS 예약의 이름. DBOS가 실행마다 워크플로 ID를 "sched-{이름}-{예정 시각}"으로 정한다.
AUTOMATION_SCHEDULE_NAME = "automation-check"

_app_state: Any = None


def bind_app_state(state: Any) -> None:
    """워크플로가 쓸 실행 도구(에이전트, 세션 공장, 설정, 푸시)를 가진 app.state를 알려 준다(create_app이 부른다)."""
    global _app_state
    _app_state = state


def services_from_state(state: Any) -> RunServices:
    return RunServices(
        agent=state.agent,
        guard=state.guard_agent,
        session_factory=state.session_factory,
        settings=state.settings,
        notifier=PushNotifier(state.session_factory, state.settings, state.push_sender),
        http=state.http_client,
    )


def current_services() -> RunServices:
    if _app_state is None:
        raise RuntimeError("bind_app_state가 불리지 않았다(create_app 전에 워크플로가 시작됨)")
    return services_from_state(_app_state)


# ---- 워크플로 ----


@DBOS.workflow(name="run_task")
async def run_task_workflow(task_id: int, prompt: str) -> None:
    await runner.run_task(current_services(), task_id, prompt=prompt)


@DBOS.workflow(name="resume_task")
async def resume_task_workflow(task_id: int) -> None:
    await runner.resume_task(current_services(), task_id)


# ---- 6단계: 자동화 워크플로 (본문은 app/automation/check.py) ----


async def utc_now() -> datetime:
    return datetime.now(UTC)


@DBOS.workflow(name="automation_check")
async def automation_check_workflow(scheduled_at: datetime, context: Any) -> None:
    """DBOS 예약 실행이 매분 부른다. 지금 시각은 단계로 기록해 복구 때도 같은 시각으로 다시 계산한다."""
    now = await durable_step("check_clock", utc_now)
    await check.run_check(current_automation_services(), now)


@DBOS.workflow(name="run_schedule")
async def run_schedule_workflow(job_id: int, run_at: str, checked_at: str) -> None:
    services = current_services()
    await check.run_scheduled_job(services, job_id, datetime.fromisoformat(run_at), datetime.fromisoformat(checked_at))


@DBOS.workflow(name="run_watch")
async def run_watch_workflow(watch_id: int, run_at: str, checked_at: str) -> None:
    services = current_automation_services()
    await check.run_watch(services, watch_id, datetime.fromisoformat(run_at), datetime.fromisoformat(checked_at))


def automation_services_from_state(state: Any) -> check.AutomationServices:
    return check.AutomationServices(
        run=services_from_state(state), watch_agent=state.watch_agent, launcher=state.launcher
    )


def current_automation_services() -> check.AutomationServices:
    if _app_state is None:
        raise RuntimeError("bind_app_state가 불리지 않았다(create_app 전에 워크플로가 시작됨)")
    return automation_services_from_state(_app_state)


# ---- 워크플로 ID ----


def moment_key(moment: datetime) -> str:
    """예정 시각을 워크플로 ID에 넣을 글자로(UTC, 마이크로초까지). 같은 예정 시각이면 항상 같은 글자다."""
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def schedule_workflow_id(job_id: int, run_at: datetime) -> str:
    return f"schedule-{job_id}-{moment_key(run_at)}"


def watch_workflow_id(watch_id: int, run_at: datetime) -> str:
    return f"watch-{watch_id}-{moment_key(run_at)}"


# ---- 시작 ----


class WorkflowLauncher:
    """API가 워크플로를 시작할 때 쓰는 객체(app.state.launcher). 시작만 하고 끝나길 기다리지 않는다.

    테스트는 이 객체를 감싸 끝날 때까지 기다리게 바꿔 끼운다(tests/conftest.py).
    """

    async def start_run(self, task_id: int, prompt: str) -> WorkflowHandleAsync[None]:
        with SetWorkflowID(run_workflow_id(task_id)):
            return await DBOS.start_workflow_async(run_task_workflow, task_id, prompt)

    async def start_resume(self, task_id: int, workflow_id: str) -> WorkflowHandleAsync[None]:
        """재개 워크플로를 정해진 ID(outbox.resume_workflow_id)로 시작한다."""
        with SetWorkflowID(workflow_id):
            return await DBOS.start_workflow_async(resume_task_workflow, task_id)

    async def start_schedule(self, job_id: int, run_at: datetime, checked_at: datetime) -> WorkflowHandleAsync[None]:
        """예약 한 건의 이 예정 시각 실행. 같은 (예약 id, 예정 시각)은 같은 ID라 한 번만 실행된다."""
        with SetWorkflowID(schedule_workflow_id(job_id, run_at)):
            return await DBOS.start_workflow_async(
                run_schedule_workflow, job_id, run_at.isoformat(), checked_at.isoformat()
            )

    async def start_watch(self, watch_id: int, run_at: datetime, checked_at: datetime) -> WorkflowHandleAsync[None]:
        with SetWorkflowID(watch_workflow_id(watch_id, run_at)):
            return await DBOS.start_workflow_async(
                run_watch_workflow, watch_id, run_at.isoformat(), checked_at.isoformat()
            )


async def start_resume_if_waiting(
    launcher: WorkflowLauncher, session_factory: async_sessionmaker[AsyncSession], task_id: int
) -> None:
    """승인이 모두 결정된 task의 재개 워크플로를 시작한다. 이미 재개가 시작됐으면(running 등) 아무것도 하지 않는다."""
    async with session_factory() as session:
        task = await session.scalar(select(Task).where(Task.id == task_id).execution_options(populate_existing=True))
        if task is None or task.status != TaskStatus.WAITING_APPROVAL:
            return
        if await approvals.pending_count(session, task_id) > 0:
            return
        current = task.workflow_id
    await launcher.start_resume(task_id, resume_workflow_id(task_id, current))


async def restart_orphaned_tasks(launcher: WorkflowLauncher, session_factory: async_sessionmaker[AsyncSession]) -> int:
    """서버 시작 때: task는 커밋됐는데 워크플로를 시작하기 전에 서버가 죽은 경우를 잇는다.

    3단계 보강: 먼저 outbox의 pending 요청을 모두 처리한다. 아래 검사는 outbox가 생기기 전 기록을 위해 남겨 둔다
    (같은 워크플로 ID라 겹쳐도 한 번만 실행된다).

    - queued인 chat task → run 워크플로 시작(같은 ID가 이미 있으면 DBOS가 다시 실행하지 않는다)
    - waiting_approval인데 pending 승인이 없는 task(마지막 결정 직후 죽음) → 재개 워크플로 시작
    """
    await outbox.dispatch(launcher, session_factory, include_not_due=True)
    async with session_factory() as session:
        queued = (
            await session.execute(
                select(Task.id, Message.content)
                .join(Message, Message.id == Task.trigger_message_id)
                .where(Task.status == TaskStatus.QUEUED, Task.origin == TaskOrigin.CHAT)
            )
        ).all()
        waiting = list(await session.scalars(select(Task.id).where(Task.status == TaskStatus.WAITING_APPROVAL)))
    for task_id, prompt in queued:
        await launcher.start_run(task_id, prompt)
    for task_id in waiting:
        await start_resume_if_waiting(launcher, session_factory, task_id)
    if queued:
        logger.info("시작하지 못했던 task %d개의 워크플로를 시작했습니다.", len(queued))
    return len(queued)


# ---- DBOS 시작과 종료 (main.py의 lifespan이 부른다) ----


def dbos_config(settings: Settings) -> DBOSConfig:
    return {
        "name": DBOS_APP_NAME,
        "system_database_url": settings.database_url,
        "dbos_system_schema": DBOS_SYSTEM_SCHEMA,
        "application_version": WORKFLOW_VERSION,
    }


def launch_dbos(settings: Settings) -> None:
    """DBOS를 만들고 시작한다. 실행 중인 이벤트 루프 안에서 불러야 복구된 워크플로가 이 루프에서 돈다.

    (DBOS는 루프 밖에서 시작하면 자기 루프를 새로 만드는데, Windows에서는 그 루프가 psycopg 비동기를 못 쓴다.)
    """
    DBOS(config=dbos_config(settings))
    DBOS.launch()
    register_automation_schedule()


def automation_schedule() -> ScheduleInput:
    """1분 검사의 DBOS 예약 설정. 놓친 분을 몰아 실행하지 않는다(automatic_backfill=False):
    밀린 예약·감시는 DB의 next_run_at으로 다음 검사가 찾고, 따라잡기 규칙은 check.claim_schedule_run이 정한다."""
    return {
        "schedule_name": AUTOMATION_SCHEDULE_NAME,
        "workflow_fn": automation_check_workflow,
        "schedule": "* * * * *",
        "cron_timezone": "Asia/Seoul",
        "automatic_backfill": False,
    }


def register_automation_schedule() -> None:
    """DBOS.launch() 뒤에 부른다. 같은 이름이면 덮어쓰므로(upsert) 서버를 다시 켤 때마다 불러도 하나다."""
    DBOS.apply_schedules([automation_schedule()])


def shutdown_dbos() -> None:
    DBOS.destroy()
