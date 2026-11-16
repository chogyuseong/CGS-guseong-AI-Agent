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
import re
from typing import Any

from dbos import DBOS, DBOSConfig, SetWorkflowID, WorkflowHandleAsync
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals, runner
from app.agent.runner import RunServices
from app.config import Settings
from app.models import Message, Task
from app.models.enums import TaskOrigin, TaskStatus
from app.push import PushNotifier

logger = logging.getLogger(__name__)

# DBOS 애플리케이션 버전. 복구는 같은 버전의 워크플로만 한다. 단계 구성을 바꾸면 올린다.
WORKFLOW_VERSION = "myagent-3"
DBOS_APP_NAME = "myagent"
# DBOS 시스템 테이블은 앱과 같은 DB의 별도 스키마에 둔다(앱 테이블, Alembic과 섞이지 않는다).
DBOS_SYSTEM_SCHEMA = "dbos"
RESUME_SUFFIX = re.compile(r"-resume-(\d+)$")

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


# ---- 워크플로 ID ----


def run_workflow_id(task_id: int) -> str:
    return f"task-{task_id}"


def resume_workflow_id(task_id: int, current_workflow_id: str | None) -> str:
    """지금 task.workflow_id의 재개 번호 + 1로 다음 재개 워크플로 ID를 만든다."""
    match = RESUME_SUFFIX.search(current_workflow_id or "")
    number = int(match.group(1)) + 1 if match else 1
    return f"task-{task_id}-resume-{number}"


# ---- 시작 ----


class WorkflowLauncher:
    """API가 워크플로를 시작할 때 쓰는 객체(app.state.launcher). 시작만 하고 끝나길 기다리지 않는다.

    테스트는 이 객체를 감싸 끝날 때까지 기다리게 바꿔 끼운다(tests/conftest.py).
    """

    async def start_run(self, task_id: int, prompt: str) -> WorkflowHandleAsync[None]:
        with SetWorkflowID(run_workflow_id(task_id)):
            return await DBOS.start_workflow_async(run_task_workflow, task_id, prompt)

    async def start_resume(self, task_id: int, current_workflow_id: str | None) -> WorkflowHandleAsync[None]:
        with SetWorkflowID(resume_workflow_id(task_id, current_workflow_id)):
            return await DBOS.start_workflow_async(resume_task_workflow, task_id)


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
    await launcher.start_resume(task_id, current)


async def restart_orphaned_tasks(launcher: WorkflowLauncher, session_factory: async_sessionmaker[AsyncSession]) -> int:
    """서버 시작 때: task는 커밋됐는데 워크플로를 시작하기 전에 서버가 죽은 경우를 잇는다.

    - queued인 chat task → run 워크플로 시작(같은 ID가 이미 있으면 DBOS가 다시 실행하지 않는다)
    - waiting_approval인데 pending 승인이 없는 task(마지막 결정 직후 죽음) → 재개 워크플로 시작
    """
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


def shutdown_dbos() -> None:
    DBOS.destroy()
