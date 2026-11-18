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

진입점(누가 부르는지):
- main.create_app: app.state.launcher = WorkflowLauncher(), bind_app_state(app.state).
- main.py lifespan: launch_dbos → restart_orphaned_tasks, 끝날 때 shutdown_dbos.
- 워크플로 시작: outbox.dispatch(메시지 API·승인 API·서버 시작)와 routers/approvals.py의 start_resume_if_waiting,
  1분 검사(automation/check.run_check)의 start_schedule·start_watch. 1분 검사 자체는 DBOS 예약이 매분 시작한다.
- 테스트: tests/conftest.py가 WorkflowLauncher를 감싼 WaitingLauncher로 끝날 때까지 기다린다.

근거: 명세 [내구성 실행: DBOS], BUILD_SPEC 4.4, docs/DECISIONS.md 2026-11-17(application_version 고정,
워크플로 ID 형식, DBOS는 lifespan 안에서 launch, dbos 스키마, apply_schedules·automatic_backfill, 직렬화기 암호화).

고칠 때 주의:
- 워크플로 이름(@DBOS.workflow name="run_task" 등)과 단계 이름·순서(runner.py의 claim_task·prepare_run·finish_task·
  notify, claim_resume·prepare_resume, 여기의 check_clock, check.py의 단계들)를 바꾸면 WORKFLOW_VERSION을 올린다.
  올리기 전의 끝나지 않은 워크플로는 복구되지 않으므로 running task가 없을 때만 배포한다(docs/OPERATIONS.md 3절).
  올리면 DECISIONS에 한 줄 남긴다.
- 워크플로 인자(task_id, prompt 등)는 DB에 저장되어 복구 때 그대로 다시 넘어온다. DECISIONS 2026-11-17은
  "워크플로 인자는 그대로"인 것을 버전을 유지한 근거로 들었다. 인자를 바꾸면 버전 규칙을 함께 검토한다.
- 워크플로 ID 형식: run·재개는 outbox.run_workflow_id·resume_workflow_id, 예약·감시는 schedule_workflow_id·
  watch_workflow_id(moment_key). 같은 요청이 같은 ID가 되는 것이 중복 실행 방지의 핵심이다.
- 앱 테이블을 초기화하면 dbos 스키마도 지운다(옛 "task-1"과 겹치면 새 task가 실행되지 않는다, docs/DURABILITY-CHECK.md).
- API 서버는 하나만 띄운다(executor "local"끼리 서로의 워크플로를 복구한다).
- 관련 테스트: tests/test_workflows.py, tests/test_outbox.py, tests/test_faults.py, tests/test_fake_service.py,
  tests/test_cancel_race.py, tests/test_automation_runs.py, tests/test_payload_crypto.py.
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
from app.payload_crypto import payload_serializer
from app.push import PushNotifier

logger = logging.getLogger(__name__)

# DBOS 애플리케이션 버전. 복구는 같은 버전의 워크플로만 한다. 단계 구성을 바꾸면 올린다.
# 기본값(코드 해시)이면 코드를 고친 뒤 재시작할 때 끝나지 않은 워크플로가 복구되지 않아 직접 고정했다
# (DECISIONS 2026-11-17). 이력: myagent-3(3단계) → 4(1분 검사에 find_outbox) → 5(아래).
# 올리는 배포 절차는 docs/OPERATIONS.md 3절(running task가 없을 때만).
WORKFLOW_VERSION = "myagent-5"  # 6단계 보강: 1분 검사에서 seed_briefings 제거(5단계 보강의 prune_records 포함)
# DBOS 설정의 name. 바꿀 때 기존 dbos 기록·복구에 미치는 영향은 아직 확인하지 않았다(바꾸기 전에 확인할 것).
DBOS_APP_NAME = "myagent"
# DBOS 시스템 테이블은 앱과 같은 DB의 별도 스키마에 둔다(앱 테이블, Alembic과 섞이지 않는다).
DBOS_SYSTEM_SCHEMA = "dbos"
# 6단계: 1분 검사 DBOS 예약의 이름. DBOS가 실행마다 워크플로 ID를 "sched-{이름}-{예정 시각}"으로 정한다.
AUTOMATION_SCHEDULE_NAME = "automation-check"

# create_app이 bind_app_state로 넣는 app.state. 워크플로는 인자로 실행 도구를 받을 수 없어 여기서 꺼낸다.
_app_state: Any = None


def bind_app_state(state: Any) -> None:
    """워크플로가 쓸 실행 도구(에이전트, 세션 공장, 설정, 푸시)를 가진 app.state를 알려 준다(create_app이 부른다).

    값이 아니라 state 객체를 잡아 두므로, 테스트가 나중에 app.state의 세션 공장 등을 바꿔도 워크플로가 따라간다
    (DECISIONS 2026-11-17). 앱을 여러 번 만들면 마지막 것이 쓰인다.
    """
    global _app_state
    _app_state = state


def services_from_state(state: Any) -> RunServices:
    """app.state에서 runner가 쓸 RunServices를 그때그때 만든다(PushNotifier도 매번 새로, 상태 없음)."""
    return RunServices(
        agent=state.agent,
        guard=state.guard_agent,
        session_factory=state.session_factory,
        settings=state.settings,
        notifier=PushNotifier(state.session_factory, state.settings, state.push_sender),
        http=state.http_client,
    )


def current_services() -> RunServices:
    """워크플로 안에서 부른다. 앱보다 먼저 워크플로가 시작되면(설정 실수) RuntimeError로 바로 드러나게 한다."""
    if _app_state is None:
        raise RuntimeError("bind_app_state가 불리지 않았다(create_app 전에 워크플로가 시작됨)")
    return services_from_state(_app_state)


# ---- 워크플로 ----


@DBOS.workflow(name="run_task")
async def run_task_workflow(task_id: int, prompt: str) -> None:
    """채팅 task 실행. ID는 "task-{id}"(WorkflowLauncher.start_run). 단계 구성은 runner.run_task.

    서버가 죽으면 launch 때 처음부터 다시 돌고, 기록된 단계는 결과만 재생된다. prompt는 DBOS 입력으로
    저장된다(키가 있으면 암호화, dbos_config).
    """
    await runner.run_task(current_services(), task_id, prompt=prompt)


@DBOS.workflow(name="resume_task")
async def resume_task_workflow(task_id: int) -> None:
    """승인 뒤 재개. ID는 "task-{id}-resume-{n}"(outbox.resume_workflow_id). 단계 구성은 runner.resume_task."""
    await runner.resume_task(current_services(), task_id)


# ---- 6단계: 자동화 워크플로 (본문은 app/automation/check.py) ----


async def utc_now() -> datetime:
    """check_clock 단계로 감싸 쓰는 지금 시각(단계 결과로 기록되어야 하므로 async 함수로 둠)."""
    return datetime.now(UTC)


@DBOS.workflow(name="automation_check")
async def automation_check_workflow(scheduled_at: datetime, context: Any) -> None:
    """DBOS 예약 실행이 매분 부른다. 지금 시각은 단계로 기록해 복구 때도 같은 시각으로 다시 계산한다."""
    now = await durable_step("check_clock", utc_now)
    await check.run_check(current_automation_services(), now)


@DBOS.workflow(name="run_schedule")
async def run_schedule_workflow(job_id: int, run_at: str, checked_at: str) -> None:
    """예약 한 건의 한 번 실행. ID는 schedule_workflow_id(job_id, run_at)라 같은 예정 시각은 한 번만 돈다.

    시각은 ISO 문자열로 받는다(DBOS 입력으로 저장). task 생성·실행 본문은 check.run_scheduled_job이고
    그 안에서 runner.run_task를 부르므로 outbox 없이 같은 워크플로에서 실행된다.
    """
    services = current_services()
    await check.run_scheduled_job(services, job_id, datetime.fromisoformat(run_at), datetime.fromisoformat(checked_at))


@DBOS.workflow(name="run_watch")
async def run_watch_workflow(watch_id: int, run_at: str, checked_at: str) -> None:
    """감시 한 건의 한 번 확인. ID는 watch_workflow_id(watch_id, run_at). 본문은 check.run_watch(READ 도구만)."""
    services = current_automation_services()
    await check.run_watch(services, watch_id, datetime.fromisoformat(run_at), datetime.fromisoformat(checked_at))


def automation_services_from_state(state: Any) -> check.AutomationServices:
    """자동화 워크플로용 묶음: 실행 도구 + 감시 판정 에이전트 + 시작기(tests/conftest.py도 직접 쓴다)."""
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
    """예약 실행 ID. run_at은 DB의 next_run_at 그대로라 1분 검사가 몇 번 돌아도 같은 ID다(중복 실행 방지 첫 겹).

    두 번째 겹은 check.claim_schedule_run의 행 잠금 검사다(DECISIONS 2026-11-17).
    """
    return f"schedule-{job_id}-{moment_key(run_at)}"


def watch_workflow_id(watch_id: int, run_at: datetime) -> str:
    """감시 확인 ID. 예약과 같은 원리(같은 감시·같은 예정 시각 = 같은 ID)."""
    return f"watch-{watch_id}-{moment_key(run_at)}"


# ---- 시작 ----


class WorkflowLauncher:
    """API가 워크플로를 시작할 때 쓰는 객체(app.state.launcher). 시작만 하고 끝나길 기다리지 않는다.

    테스트는 이 객체를 감싸 끝날 때까지 기다리게 바꿔 끼운다(tests/conftest.py).
    outbox.OutboxLauncher와 check.AutomationLauncher(Protocol)를 함께 만족한다. 메서드 이름·인자를 바꾸면 두 곳과
    WaitingLauncher를 같이 고친다. 모든 시작은 SetWorkflowID로 고정 ID를 주므로 여러 번 불러도 한 번만 실행된다.
    """

    async def start_run(self, task_id: int, prompt: str) -> WorkflowHandleAsync[None]:
        """새 task 실행을 "task-{id}"로 시작한다. 이미 그 ID가 있으면 DBOS가 기존 핸들을 돌려준다."""
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
        """감시 한 건의 이 예정 시각 확인. 같은 (감시 id, 예정 시각)은 한 번만 실행된다."""
        with SetWorkflowID(watch_workflow_id(watch_id, run_at)):
            return await DBOS.start_workflow_async(
                run_watch_workflow, watch_id, run_at.isoformat(), checked_at.isoformat()
            )


async def start_resume_if_waiting(
    launcher: WorkflowLauncher, session_factory: async_sessionmaker[AsyncSession], task_id: int
) -> None:
    """승인이 모두 결정된 task의 재개 워크플로를 시작한다. 이미 재개가 시작됐으면(running 등) 아무것도 하지 않는다.

    outbox 요청이 없던 옛 기록을 잇는 보조 경로다(routers/approvals.py가 outbox.dispatch 뒤에, 서버 시작 때
    restart_orphaned_tasks가 부른다). 읽기만 하고 잠그지 않는다. 겹쳐 불려도 재개 번호가 같아 한 번만 돌고,
    runner.claim_resume의 행 잠금이 한 번 더 막는다. 시작 실패 예외는 호출한 쪽으로 나간다.
    """
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

    main.py lifespan이 launch_dbos 직후 한 번 부른다. 돌려주는 값은 queued에서 시작한 task 수(로그·테스트용).
    예약·감시 task(origin != chat)는 queued 검사에서 뺀다(그 워크플로가 task를 만들고 직접 실행한다).
    관련 테스트: test_workflows.py, test_fake_service.py(두 번 불러도 효과 1건), test_cancel_race.py.
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
    """7단계 보강: PAYLOAD_ENCRYPTION_KEY가 있으면 워크플로 입력·단계 결과를 암호화해 저장한다(app/payload_crypto.py).

    키가 없으면 DBOS 기본 직렬화기(pickle, 평문과 같음)이고 메일 기능도 꺼진다. 단계 구성은 바뀌지 않아
    WORKFLOW_VERSION은 그대로다(키를 넣기 전의 끝나지 않은 워크플로는 serialization 칸이 py_pickle이라 그대로 읽힌다).

    키를 바꾸면 옛 키로 암호화한 끝나지 않은 워크플로를 읽지 못하므로 교체 절차는 docs/OPERATIONS.md 4절을 따른다.
    시스템 DB는 앱과 같은 DATABASE_URL의 dbos 스키마다(DECISIONS 2026-11-17).
    """
    config: DBOSConfig = {
        "name": DBOS_APP_NAME,
        "system_database_url": settings.database_url,
        "dbos_system_schema": DBOS_SYSTEM_SCHEMA,
        "application_version": WORKFLOW_VERSION,
    }
    serializer = payload_serializer(settings)
    if serializer is not None:
        config["serializer"] = serializer
    return config


def launch_dbos(settings: Settings) -> None:
    """DBOS를 만들고 시작한다. 실행 중인 이벤트 루프 안에서 불러야 복구된 워크플로가 이 루프에서 돈다.

    (DBOS는 루프 밖에서 시작하면 자기 루프를 새로 만드는데, Windows에서는 그 루프가 psycopg 비동기를 못 쓴다.)
    launch가 끝나지 않은 워크플로(같은 WORKFLOW_VERSION, executor "local")를 복구 실행하기 시작한다.
    그 뒤 1분 검사 예약을 등록한다. 테스트는 예약을 등록하지 않는다(테스트 DB에서 매분 검사가 돌게 되므로).
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
    """lifespan 종료 때. 끝나지 않은 워크플로는 기록에 남아 다음 launch가 이어서 실행한다."""
    DBOS.destroy()
