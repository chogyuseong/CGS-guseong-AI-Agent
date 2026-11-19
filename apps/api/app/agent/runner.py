"""task 하나를 실행하고(run_task), 승인이 모두 결정되면 이어서 실행한다(resume_task).

3단계부터 이 두 함수는 DBOS 워크플로(workflows.py) 안에서 돈다. 서버가 죽었다 살아나면 워크플로가 처음부터
다시 실행되므로, 다시 하면 안 되는 일은 DBOS 단계(durable_step)로 감쌌다. 단계는 처음 결과를 기록해 두고
복구 때 그 결과를 그대로 돌려준다.

run_task 흐름 (POST /api/conversations/{id}/messages → run_task 워크플로):

    1. [단계 claim_task]     queued → running, tasks.workflow_id 기록. 시작 시각도 함께 기록(푸시 20초 규칙).
    2. [단계 prepare_run]    하루 비용 검사(넘으면 budget_exceeded로 끝낼 결과), 같은 대화의 이전 기록 불러오기.
    3. execute_agent         agent.run(...). 모델 요청은 DBOSDurability가 단계로 만든다(복구 때 다시 부르지 않는다).
                             도구 실행은 단계가 아니다. 대신 StepTracking이 tool_call_id로 steps 행을 찾아
                             이미 끝난 호출은 기록된 결과를 돌려주고, 도중에 끊긴 호출은 다시 실행하지 않는다
                             (tracking.py).
    4. [단계 finish_task]    승인 대기면 preview·감시 에이전트·승인 요청 저장. task 상태·기록·usage_logs·답변 저장.
                             task가 이미 running이 아니면 아무것도 하지 않는다(같은 결과를 두 번 저장하지 않게).
    5. [단계 notify]         승인 필요·실패·(오래 걸린) 완료를 푸시로 알린다. 본문은 고정 문구(app/push.py).

resume_task 흐름 (마지막 승인 결정 → resume_task 워크플로):

    1. [단계 claim_resume]   waiting_approval이고 pending 승인이 없는 task만 running으로 바꾼다(행 잠금).
    2. [단계 prepare_resume] 하루 비용 검사, 이전 대화 기록 + 이 task 기록, 승인=ToolApproved / 거절=ToolDenied,
                             이 task가 이미 쓴 사용량(usage_logs 합계).
    3~5. run_task와 같다(재개한 run이 또 EXTERNAL을 부르면 다시 승인 대기가 된다).

어떤 예외도 잡아 failed/budget_exceeded로 끝낸다. HTTP 요청 객체를 모른다(RunServices만 받는다).

진입점(누가 부르는지):
- workflows.run_task_workflow·resume_task_workflow(채팅 task, ID "task-{id}", "task-{id}-resume-{n}").
- automation/check.run_scheduled_job(예약 task, done_kind로 브리핑 푸시 종류를 바꿈). check.py는 lock_task도 쓴다.
- evals/harness.py(워크플로 밖에서 run_task를 직접 부름. 그때 durable_step은 그냥 함수 실행이다).
- 테스트도 run_task·resume_task를 직접 부른다(durable.py 설명 참고).

근거: BUILD_SPEC 4.4·9.2(task 상태, 결과 불명이면 done 아님), 명세 푸시 규칙(task_done은 20초 넘게 걸렸거나 자동 작업),
docs/DECISIONS.md 2026-11-16(FOR UPDATE claim), 2026-11-17(단계 구성 claim→prepare→agent.run→finish→notify,
도구 실행은 단계로 감싸지 않음, 메일 결과 봉하기·푸는 곳은 execute_agent뿐).

고칠 때 주의:
- durable_step 이름("claim_task", "prepare_run", "claim_resume", "prepare_resume", "finish_task", "notify")과 순서,
  단계 안팎의 경계를 바꾸면 workflows.WORKFLOW_VERSION을 올린다(docs/OPERATIONS.md 3절, DECISIONS에 한 줄).
  단계 함수의 결과(TaskInfo, Prepared, PushKind)는 DBOS에 직렬화되어 저장되고 복구 때 그대로 돌아오므로
  필드를 바꾸면 옛 기록과의 호환도 본다.
- 단계 함수는 "커밋 뒤, 결과 기록 전에 죽으면 다시 실행"(at-least-once)이다. 그래서 claim은 reclaimed로,
  finish는 IN_PROGRESS 검사로, notify는 notification_key로 두 번째 실행을 무해하게 만든다. 새 단계도 같게 만든다.
- task 상태 전이는 docs/STATE_MACHINES.md 1절 표와 같아야 한다. 전이 함수 이름(claim_run, mark_claimed, reclaimed,
  claim_resume, finish, save_outcome, has_unresolved_operation, execute_agent, prepare_run, prepare_resume,
  close_unexecuted_steps)을 바꾸면 표도 고친다(tests/test_state_machines.py가 이름을 확인한다).
- 사용자에게 보이는 실패 문구(아래 *_MESSAGE)는 tests/test_outage.py·test_workflows.py가 상수로 가져다 쓴다.
  문장을 바꾸면 docs/OPERATIONS.md 5절(장애 때 화면)과 맞는지 확인한다.
- 관련 테스트(runner를 직접 쓰는 것): tests/test_agent_run.py, test_workflows.py, test_cancel.py,
  test_automation_runs.py, test_outage.py, test_push.py, test_state_machines.py.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from cryptography.fernet import Fernet
from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import RunUsage
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals, plans
from app.agent.agent import MyAgent
from app.agent.budget import (
    UNPRICED_MESSAGE,
    DailyBudgetExceededError,
    UnpricedModelError,
    build_usage_limits,
    calculate_cost,
    daily_cost_exceeded,
)
from app.agent.cancel import NOT_STARTED, RUN_CANCELLED_MESSAGE, TaskCancelledError, completed_external_note
from app.agent.deps import AgentDeps
from app.agent.durable import current_workflow_id, durable_step
from app.agent.guard import GuardAgent, GuardReview, check_action
from app.agent.history import (
    dump_messages,
    history_for_storage,
    load_conversation_history,
    load_messages,
    open_mail_returns,
    pending_tool_call_ids,
)
from app.agent.plans import PlanDraft
from app.agent.replies import AutomationReply
from app.agent.results import not_executed_output
from app.agent.toolsets import label_for, preview_for, run_toolsets, toolsets_for
from app.config import Settings
from app.models import ActionPlan, AgentRun, Approval, Conversation, Message, Operation, Step, Task, UsageLog
from app.models.enums import (
    MessageRole,
    OperationStatus,
    RunKind,
    StepStatus,
    TaskOrigin,
    TaskStatus,
    UsagePurpose,
)
from app.push import PushKind, PushNotifier
from app.security import payload_cipher

logger = logging.getLogger(__name__)

# 아래 *_MESSAGE는 task.error와 assistant 메시지로 사용자에게 그대로 보인다. 내부 오류 내용은 넣지 않는다
# (예외 메시지에 개인 정보·제공자 응답이 섞일 수 있어서). 일부는 docs/OPERATIONS.md 5절 표에 그대로 적혀 있다.
DAILY_BUDGET_MESSAGE = "오늘 쓸 수 있는 비용 한도를 다 써서 작업을 시작하지 않았습니다. 내일 다시 시도해 주세요."
TASK_BUDGET_MESSAGE = "이 작업에 정해 둔 요청 수나 토큰 한도를 넘어 중간에 멈췄습니다. 위 단계까지만 처리되었습니다."
MODEL_BEHAVIOR_MESSAGE = "모델이 도구를 올바르게 부르지 못해 작업을 끝내지 못했습니다."
UNEXPECTED_MESSAGE = "작업 중 오류가 발생해 끝내지 못했습니다."
PLAN_REJECTED_MESSAGE = "이 외부 작업은 서버 정책상 실행 계획을 만들 수 없어 진행하지 않았습니다."
# 2026-11-19 한 번의 승인 요청에서 일정 삭제는 1건까지(사용자 결정: 추천 제안). 모델이 "전부 지워"에 카드를 여러 장
# 띄우지 못하게 서버가 막는다(eval 08번, docs/EVALS.md). 바꾸면 tests/test_bulk_delete_limit.py를 같이 고친다.
MAX_DELETES_PER_ROUND = 1
BULK_DELETE_MESSAGE = "일정은 한 번에 한 건씩만 지울 수 있어 진행하지 않았습니다. 지울 일정을 하나씩 말씀해 주세요."
# 승인됐지만 작업이 그 전에 끝나(하루 비용 상한, 오류) 실행하지 않은 EXTERNAL 호출의 step 결과.
NOT_EXECUTED_MESSAGE = "작업이 이 단계 전에 멈춰 실행하지 않았습니다."
# 명세: task_done 푸시는 20초 넘게 걸렸거나 origin이 schedule·watch일 때만.
TASK_DONE_NOTIFY_AFTER_SECONDS = 20.0
# task_failed 푸시를 보내는 상태. cancelled는 여기에 없어 푸시하지 않는다(push_kind_for).
FAILED_STATUSES = (TaskStatus.FAILED, TaskStatus.BUDGET_EXCEEDED)
# finish가 결과를 저장할 수 있는 상태. 실행 중 취소 요청이 와도 끝난 결과는 저장한다.
IN_PROGRESS = (TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED)
# 모델의 마지막 답을 그대로 답변으로 남기는 상태. needs_review도 답은 있다(확인 필요는 카드·푸시로 알린다).
ANSWERED_STATUSES = (TaskStatus.DONE, TaskStatus.NEEDS_REVIEW)
# 실행했는지 아직 모르는 외부 작업 상태. 하나라도 남으면 done 대신 needs_review(BUILD_SPEC 9.2, 성공은 영수증 근거).
# OperationStatus에 "결과 불명" 상태를 더하면 여기에도 넣는다(docs/STATE_MACHINES.md 2절).
UNRESOLVED_OPERATIONS = (OperationStatus.UNKNOWN, OperationStatus.RECONCILING, OperationStatus.EXECUTING)


@dataclass(frozen=True)
class RunServices:
    """run_task·resume_task에 필요한 것들. 앱 시작 때 만든 것을 넘긴다."""

    agent: MyAgent
    guard: GuardAgent
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    notifier: PushNotifier
    http: httpx.AsyncClient  # 외부 API 도구가 쓰는 클라이언트(4단계, app/tools/http.py)


@dataclass(frozen=True)
class TaskInfo:
    """claim 단계의 결과. DBOS에 저장되어 복구 때 같은 값으로 돌아오므로 이후 단계는 task 행 대신 이것을 쓴다.

    필드를 바꾸면 끝나지 않은 옛 워크플로의 기록을 읽을 수 있는지 확인한다(WORKFLOW_VERSION 규칙).
    """

    id: int
    user_id: int
    conversation_id: int
    origin: TaskOrigin
    # 사용자의 원래 요청 문장. 감시 에이전트의 입력이 된다. 메시지 없이 시작한 task는 None.
    request_text: str | None = None
    # 이번 실행(run 또는 재개)을 시작한 시각(time.time). claim 단계에 기록되어 복구 뒤에도 같다.
    started_at: float = 0.0


@dataclass
class RunInput:
    """agent.run 한 번에 넘길 것."""

    prompt: str | None
    history: list[ModelMessage]  # 모델에게 보일 전체 기록(이전 대화 + 이 task가 이미 저장한 기록)
    task_messages: list[ModelMessage] = field(default_factory=list)  # 이 task가 이미 저장한 기록(재개일 때)
    deferred_results: DeferredToolResults | None = None
    usage: RunUsage | None = None  # 이 task가 앞선 run에서 이미 쓴 사용량(재개일 때)


@dataclass
class Outcome:
    """run 한 번의 결과(finish가 저장한다). error는 사용자에게 보이는 문구, deferred는 승인 대기 호출들."""

    status: TaskStatus
    final_answer: str | None = None
    error: str | None = None
    new_messages: list[ModelMessage] = field(default_factory=list)
    deferred: DeferredToolRequests | None = None
    suggestions: list[dict[str, str]] | None = None  # 6단계 보강: 예약 작업 답의 제안(replies.AutomationReply)


@dataclass
class Prepared:
    """prepare 단계의 결과. run_input이 있으면 agent.run을, 없으면 outcome으로 바로 끝낸다.

    DBOS 단계 결과로 저장된다(키가 있으면 암호화). 이전 기록 속 메일 결과는 봉한 채 들어 있다(execute_agent가 푼다).
    """

    run_input: RunInput | None = None
    outcome: Outcome | None = None
    previous: list[ModelMessage] = field(default_factory=list)  # 이 task가 이미 저장한 기록(재개일 때)


# ---- 시작과 재개: task 행 잠그기 ----


async def task_info(session: AsyncSession, task: Task) -> TaskInfo:
    """잠근 task에서 TaskInfo를 만든다. started_at은 지금 시각이고 claim 단계 결과로 고정된다(푸시 20초 규칙)."""
    request_text = None
    if task.trigger_message_id is not None:
        request_text = await session.scalar(select(Message.content).where(Message.id == task.trigger_message_id))
        request_text = await with_revision_notes(session, task.id, request_text)
    return TaskInfo(
        task.id, task.user_id, task.conversation_id, task.origin, request_text=request_text, started_at=time.time()
    )


# 감시 에이전트의 "사용자 요청"에 이 task에서 승인 카드 '고쳐서 다시'로 적은 내용을 순서대로 붙인다
# (요구사항 밖, 2026-11-18). 고친 카드가 원래 요청과 달라 보여 mismatch로 잘못 걸리지 않게 하려는 것이다.
# 붙이는 것은 사용자 본인이 쓴 글뿐이다(도구 결과·대화 기록은 넣지 않는다: guard.py 입력 네 가지 원칙).
# 고칠 때 주의: 문구 "승인 카드에서 고쳐 달라고 함"은 tests/test_approval_revise.py가 찾는다. 읽기만 한다.
async def with_revision_notes(session: AsyncSession, task_id: int, request_text: str | None) -> str | None:
    """원래 요청 뒤에 고칠 내용을 한 줄씩 붙인 문장. 고칠 내용이 없으면 request_text 그대로."""
    notes = await session.scalars(
        select(Approval.revision_note)
        .join(Step, Step.id == Approval.step_id)
        .where(Step.task_id == task_id, Approval.revision_note.is_not(None))
        .order_by(Approval.id)
    )
    lines = [f"(승인 카드에서 고쳐 달라고 함: {note})" for note in notes]
    if not lines:
        return request_text
    return "\n".join([request_text or "", *lines])


async def lock_task(session: AsyncSession, task_id: int) -> Task | None:
    """task 행을 SELECT … FOR UPDATE로 잠그고 최신 값으로 읽는다(세션 캐시를 덮어씀). 잠금은 커밋까지 유지된다.

    claim·finish와 1분 검사의 승인 만료(check.expire_overdue_approvals)가 쓴다. 승인 결정(approvals)·취소(cancel)는
    같은 task 행을 각자 FOR UPDATE로 잠가 상태 전이가 겹치지 않는다(DECISIONS 2026-11-16).
    user_id 조건이 없다: 서버 내부에서 task id로만 부르는 함수다. API에서 쓰려면 소유자 검사를 따로 한다.
    """
    return await session.scalar(
        select(Task).where(Task.id == task_id).with_for_update().execution_options(populate_existing=True)
    )


def reclaimed(task: Task, workflow_id: str | None) -> bool:
    """복구된 워크플로가 자기가 이미 running으로 바꾼 task를 다시 잡는 경우.

    claim 단계가 커밋한 뒤 결과를 기록하기 전에 서버가 죽으면 단계가 다시 실행된다. 그때 task는 이미 running이고
    workflow_id가 지금 워크플로와 같다. 다른 워크플로가 잡은 task는 다시 잡지 않는다.
    """
    return task.status == TaskStatus.RUNNING and workflow_id is not None and task.workflow_id == workflow_id


async def claim_run(services: RunServices, task_id: int, workflow_id: str | None) -> TaskInfo | None:
    """queued인 task만 running으로 바꾼다. 행을 잠가 같은 task가 두 번 시작되지 않게 한다.

    None이면 실행하지 않는다(이미 다른 워크플로가 잡았거나, 취소됐거나, 없는 task). 성공하면 커밋까지 한다.
    "claim_task" 단계로 실행되어 복구 때는 기록된 결과를 재생한다.
    """
    async with services.session_factory() as session:
        task = await lock_task(session, task_id)
        if task is None or not (task.status == TaskStatus.QUEUED or reclaimed(task, workflow_id)):
            return None
        return await mark_claimed(session, task, workflow_id, RunKind.RUN)


async def claim_resume(services: RunServices, task_id: int, workflow_id: str | None) -> TaskInfo | None:
    """승인 대기이고 pending 승인이 없는 task만 running으로 바꾼다. 동시에 두 번 불려도 한 번만 통과한다.

    다른 ID의 재개 워크플로가 겹쳐도 행 잠금과 상태 검사가 두 번째를 None으로 막는다(이미 running이므로).
    None이면 재개하지 않는다. 성공하면 커밋까지 한다. "claim_resume" 단계로 실행된다.
    """
    async with services.session_factory() as session:
        task = await lock_task(session, task_id)
        if task is None:
            return None
        if reclaimed(task, workflow_id):
            return await mark_claimed(session, task, workflow_id, RunKind.RESUME)
        if task.status != TaskStatus.WAITING_APPROVAL or await approvals.pending_count(session, task_id) > 0:
            return None
        return await mark_claimed(session, task, workflow_id, RunKind.RESUME)


async def mark_claimed(session: AsyncSession, task: Task, workflow_id: str | None, kind: RunKind) -> TaskInfo:
    """잠근 task를 running으로 바꾸고 agent_runs 회차를 남긴 뒤 커밋한다(잠금 해제).

    tasks.workflow_id는 resume_workflow_id가 다음 재개 번호를 계산하는 근거다. 워크플로 밖(테스트·eval)이면
    workflow_id가 None이라 바꾸지 않는다.
    """
    task.status = TaskStatus.RUNNING
    if workflow_id is not None:
        task.workflow_id = workflow_id
    await record_run_start(session, task.id, workflow_id, kind)
    info = await task_info(session, task)
    await session.commit()
    return info


async def record_run_start(session: AsyncSession, task_id: int, workflow_id: str | None, kind: RunKind) -> None:
    """이번 회차의 agent_runs 행을 만든다. 같은 워크플로가 복구로 다시 claim하면 새로 만들지 않는다.

    task 행을 잠근 채 부르므로 같은 task의 회차 번호가 겹치지 않는다.
    """
    if workflow_id is not None:
        existing = await session.scalar(
            select(AgentRun.id).where(AgentRun.task_id == task_id, AgentRun.workflow_id == workflow_id)
        )
        if existing is not None:
            return
    last_round = await session.scalar(select(func.max(AgentRun.round)).where(AgentRun.task_id == task_id))
    session.add(AgentRun(task_id=task_id, round=(last_round or 0) + 1, kind=kind, workflow_id=workflow_id))


async def record_run_finish(
    session: AsyncSession, task_id: int, status: TaskStatus, responses: list[ModelResponse]
) -> None:
    """아직 끝나지 않은 마지막 회차에 결과 상태·요청 수·토큰을 채운다. 커밋은 save_outcome이 한다.

    열린 회차가 없으면 아무것도 하지 않는다. 요청 수·토큰은 이번 회차의 새 응답만 센다.
    """
    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.task_id == task_id, AgentRun.finished_at.is_(None))
        .order_by(AgentRun.round.desc())
        .limit(1)
    )
    if run is None:
        return
    run.status = status
    run.finished_at = func.now()
    run.request_count = len(responses)
    run.total_tokens = sum(r.usage.total_tokens for r in responses)


# ---- agent.run ----


def output_type_for(origin: TaskOrigin) -> Any:
    """6단계 보강: 예약 작업은 구조화된 답(AutomationReply)도 받는다. 채팅은 그대로 글(str)."""
    if origin == TaskOrigin.SCHEDULE:
        return [str, AutomationReply, DeferredToolRequests]
    return [str, DeferredToolRequests]


def outcome_of(output: object, new_messages: list[ModelMessage]) -> Outcome:
    """agent.run의 출력을 task 상태로 바꾼다. 승인 대기(approvals만 있는 DeferredToolRequests)면 waiting_approval."""
    if isinstance(output, AutomationReply):
        return Outcome(
            TaskStatus.DONE, final_answer=output.text, new_messages=new_messages, suggestions=output.suggestion_rows()
        )
    if not isinstance(output, DeferredToolRequests):
        return Outcome(TaskStatus.DONE, final_answer=str(output), new_messages=new_messages)
    if output.calls:
        # 승인이 아닌 "외부 실행 대기"(CallDeferred)는 이 앱에 없다. 생기면 실패로 끝낸다.
        logger.error("처리할 수 없는 지연 호출: %s", [c.tool_name for c in output.calls])
        return Outcome(TaskStatus.FAILED, error=UNEXPECTED_MESSAGE, new_messages=new_messages)
    return Outcome(TaskStatus.WAITING_APPROVAL, new_messages=new_messages, deferred=output)


def deps_for(services: RunServices, info: TaskInfo) -> AgentDeps:
    """도구가 받는 의존성. user_id·task_id·origin은 claim 때 읽은 task 값이라 도구의 모든 조회가 이 사용자로 묶인다."""
    return AgentDeps(
        user_id=info.user_id,
        task_id=info.id,
        origin=info.origin,
        db=services.session_factory,
        settings=services.settings,
        http=services.http,
    )


# execute_agent 보충(40줄 제한으로 함수 위에 둔다):
# - 결과: 항상 Outcome. 예외를 밖으로 내지 않는다. 멈춘 경우에도 그때까지의 새 메시지(captured)를 돌려줘
#   finish가 기록·사용량(usage_logs)을 남긴다(이미 쓴 토큰을 빠뜨리지 않게).
# - 부수 효과: 모델 호출(비용 예약·정산은 budget의 capability가 호출마다 한다), 도구 실행(DB 쓰기·외부 API·steps 기록).
# - 복구: 이 함수는 단계 밖이라 다시 실행되지만, 모델 응답은 DBOSDurability 단계로 재생되고 끝난 도구 호출은
#   StepTracking이 재생한다. 그래서 같은 EXTERNAL 호출을 두 번 실행하지 않는다.
# - 메일 결과를 푸는 곳은 여기뿐이다. 푼 history를 반환값·단계 결과에 넣지 않는다(DBOS·DB에 평문이 남지 않게).
async def execute_agent(services: RunServices, info: TaskInfo, run_input: RunInput) -> Outcome:
    """agent.run 한 번. DBOS 단계가 아니라 입력·중간 결과가 DBOS에 저장되지 않는다(모델 응답만 단계 결과로 남는다).

    7단계 보강: 이전 기록의 메일 결과는 여기서만 풀어 모델에게 준다(prepare 단계 결과에는 암호문만 남는다).
    도구 묶음은 run_toolsets(메일 기능 플래그, 메일 작업의 공개 검색 제외)다.
    """
    deps = deps_for(services, info)
    history = open_mail_returns(run_input.history, payload_cipher(services.settings))
    with capture_run_messages() as captured:
        try:
            result = await services.agent.run(
                run_input.prompt,
                deps=deps,
                message_history=history,
                deferred_tool_results=run_input.deferred_results,
                toolsets=run_toolsets(info.origin),
                output_type=output_type_for(info.origin),
                usage_limits=build_usage_limits(services.settings),
                usage=run_input.usage,
            )
            return outcome_of(result.output, captured[len(history) :])
        except TaskCancelledError:
            # 사용자가 실행 중 취소했다. 다음 모델 호출·도구 실행을 시작하지 않고 멈췄다.
            return Outcome(TaskStatus.CANCELLED, error=RUN_CANCELLED_MESSAGE, new_messages=captured[len(history) :])
        except UsageLimitExceeded:
            return Outcome(TaskStatus.BUDGET_EXCEEDED, error=TASK_BUDGET_MESSAGE, new_messages=captured[len(history) :])
        except DailyBudgetExceededError:
            # 다음 모델 호출의 예상 비용을 잡으면 오늘 한도를 넘는다. 그 호출은 보내지 않았다.
            new = captured[len(history) :]
            return Outcome(TaskStatus.BUDGET_EXCEEDED, error=DAILY_BUDGET_MESSAGE, new_messages=new)
        except UnpricedModelError:
            logger.error("task %s: 가격표에 없는 모델이라 부르지 않음", info.id)
            return Outcome(TaskStatus.FAILED, error=UNPRICED_MESSAGE, new_messages=captured[len(history) :])
        except UnexpectedModelBehavior:
            logger.warning("task %s: 모델 응답 이상으로 실패", info.id, exc_info=True)
            return Outcome(TaskStatus.FAILED, error=MODEL_BEHAVIOR_MESSAGE, new_messages=captured[len(history) :])
        except Exception:
            logger.exception("task %s: 예상하지 못한 오류로 실패", info.id)
            return Outcome(TaskStatus.FAILED, error=UNEXPECTED_MESSAGE, new_messages=captured[len(history) :])


# ---- 승인 요청 준비 (preview + 감시 에이전트) ----


async def draft_for(services: RunServices, info: TaskInfo, tool_name: str, args: dict[str, Any]) -> PlanDraft:
    """서버가 인자를 다시 검증·정규화하고 실행 계획 값을 확정한다(2단계 보강). 실패하면 PlanRejectedError.

    모델이 낸 인자를 그대로 승인 카드에 쓰면 검증 안 된 값이 고정 인자가 되므로 여기서 한 번 더 거른다(절대 원칙 3·4).
    계획 초안은 정의 그대로의 toolsets_for를 쓴다(run_toolsets의 메일 필터는 모델 표시용, DECISIONS 2026-11-17).
    DB 세션은 초안을 만든 뒤 닫고, 대상 버전 읽기(외부 조회)는 세션 밖에서 한다. 저장은 finish가 한다.
    """
    async with services.session_factory() as session:
        draft = await plans.draft_plan(
            session, user_id=info.user_id, toolsets=toolsets_for(info.origin), tool_name=tool_name, args=args
        )
    return await plans.with_target_version(deps_for(services, info), draft)


# prepare_requests 보충: finish_task 단계 안에서 돈다. 감시 에이전트 호출은 모델 비용이 들고, 예산 예약 키를
# "guard:{task}:{tool_call_id}"로 고정해 단계가 다시 실행돼도 같은 호출은 다시 예약하지 않는다(DECISIONS 2026-11-17).
# 일정 수정·삭제의 preview와 draft_for의 대상 버전 읽기는 외부 조회(Google 일정 GET)를 한다. 승인 요청 저장은
# 하지 않는다(finish가 같은 트랜잭션에서 한다). PlanRejectedError는 밖으로 나가고 finish가 잡는다.
async def prepare_requests(
    services: RunServices, info: TaskInfo, deferred: DeferredToolRequests
) -> tuple[list[approvals.ApprovalRequest], list[GuardReview]]:
    """승인 대상 호출마다 실행 계획 초안을 만들고, 그 정규화 인자로 preview와 감시 검사를 한다.

    감시 에이전트에는 원래 요청·도구 이름·정규화 인자·preview만 넘긴다. 도구 결과와 메시지 기록은 넘기지 않는다.
    """
    deps = deps_for(services, info)
    requests, reviews = [], []
    for call in deferred.approvals:
        draft = await draft_for(services, info, call.tool_name, call.args_as_dict())
        summary = await preview_for(deps, call.tool_name, draft.canonical_args)
        scope = replace(
            deps.budget_scope, key_prefix=f"guard:{info.id}:{call.tool_call_id}", purpose=UsagePurpose.GUARD
        )
        review = await check_action(
            services.guard,
            scope,
            request_text=info.request_text or "",
            tool_name=call.tool_name,
            tool_input=draft.canonical_args,
            preview=summary,
        )
        reviews.append(review)
        requests.append(
            approvals.ApprovalRequest(
                tool_call_id=call.tool_call_id,
                plan=draft,
                label=label_for(call.tool_name, draft.canonical_args),
                summary=summary,
                verdict=review.verdict,
                reason=review.reason,
                guard_model=review.model,
            )
        )
    return requests, reviews


# ---- 저장 ----


def usage_log(info: TaskInfo, purpose: UsagePurpose, model: str, input_tokens: int, output_tokens: int) -> UsageLog:
    """usage_logs 한 행(세션에 넣지 않음). 비용 화면·하루 한도 합계의 근거다. 모델 이름은 컬럼 길이 100자로 자른다."""
    return UsageLog(
        user_id=info.user_id,
        task_id=info.id,
        purpose=purpose,
        model=model[:100],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=calculate_cost(model, input_tokens, output_tokens),
    )


def usage_rows(info: TaskInfo, messages: Sequence[ModelMessage]) -> list[UsageLog]:
    """모델 응답 하나마다 usage_logs 한 행. 중단된 응답도 토큰은 이미 썼으므로 기록한다."""
    return [
        usage_log(info, UsagePurpose.AGENT, r.model_name or "unknown", r.usage.input_tokens, r.usage.output_tokens)
        for r in messages
        if isinstance(r, ModelResponse)
    ]


def guard_usage_rows(info: TaskInfo, reviews: Sequence[GuardReview]) -> list[UsageLog]:
    """감시 에이전트 호출 한 번마다 purpose=guard 한 행. 호출이 실패해 사용량을 모르면 남기지 않는다."""
    return [
        usage_log(info, UsagePurpose.GUARD, r.model, r.usage.input_tokens, r.usage.output_tokens)
        for r in reviews
        if r.usage is not None
    ]


async def close_unexecuted_steps(session: AsyncSession, task_id: int) -> None:
    """task가 끝났는데 아직 waiting_approval인 step(승인됐지만 실행 전에 작업이 멈춘 호출)을 failed로 닫는다.

    예: 재개 직전 하루 비용 상한에 걸려 budget_exceeded로 끝나면 승인된 호출은 실행되지 않는다.
    실행하지 않았다는 사실이 확실하므로 output.error에 그렇게 적는다(실행 여부를 모르는 경우와 구별).
    9단계 보강: 그 호출의 operation(awaiting_approval·approved)도 cancelled로 닫는다. 실행기는 task가 running일 때만
    실행하므로 끝난 task의 승인은 다시 쓰일 수 없다(전이 표: docs/STATE_MACHINES.md).
    """
    output = not_executed_output(NOT_EXECUTED_MESSAGE)
    await session.execute(
        update(Step)
        .where(Step.task_id == task_id, Step.status == StepStatus.WAITING_APPROVAL)
        .values(status=StepStatus.FAILED, ok=False, output=output)
    )
    task_plans = select(ActionPlan.id).where(ActionPlan.task_id == task_id)
    await approvals.set_operation_status(session, task_plans, OperationStatus.CANCELLED, NOT_STARTED)


# save_outcome 보충(40줄 제한으로 함수 위에 둔다):
# - finish가 task 행을 잠근 세션으로 부른다. 승인 요청·감시 사용량과 이 함수의 쓰기가 한 트랜잭션이고 마지막에 커밋한다.
# - needs_review 승격: 결과를 모르는 외부 작업이 남았는데 done이라 하면 "성공은 영수증에 근거"(절대 원칙 10)를 어긴다.
# - 승인 대기가 아니면 close_unexecuted_steps로 남은 승인 step·operation을 닫는다
#   (끝난 task의 승인이 나중에 실행되지 않게).
# - 기록(message_history)은 history_for_storage가 상태에 맞게 다듬고 메일 결과를 봉한다(cipher가 없으면 내용을 비움).
async def save_outcome(
    session: AsyncSession,
    info: TaskInfo,
    outcome: Outcome,
    previous: Sequence[ModelMessage] = (),
    cipher: Fernet | None = None,
) -> None:
    """task 상태·기록·사용량을 저장하고 커밋한다. 재개한 run이면 요청 수·토큰·기록을 앞선 run에 더한다.

    cipher(PAYLOAD_ENCRYPTION_KEY): 기록 속 메일 결과를 암호화한다(7단계 보강, history.seal_mail_returns).
    """
    task = await session.get_one(Task, info.id)
    responses = [m for m in outcome.new_messages if isinstance(m, ModelResponse)]
    if outcome.status == TaskStatus.DONE and await has_unresolved_operation(session, info.id):
        # 3단계 보강: 결과를 확인하지 못한 외부 작업이 있으면 done이라 하지 않는다(BUILD_SPEC 9.2).
        outcome = replace(outcome, status=TaskStatus.NEEDS_REVIEW)
    task.status = outcome.status
    await record_run_finish(session, info.id, outcome.status, responses)
    task.final_answer = outcome.final_answer
    task.error = outcome.error
    task.request_count = (task.request_count or 0) + len(responses)
    task.total_tokens = (task.total_tokens or 0) + sum(r.usage.total_tokens for r in responses)
    stored = history_for_storage(previous, outcome.new_messages, status=outcome.status, cipher=cipher)
    task.message_history = dump_messages(stored) if stored else None
    session.add_all(usage_rows(info, outcome.new_messages))
    if outcome.status != TaskStatus.WAITING_APPROVAL:
        # 승인 대기 중에는 답변을 남기지 않는다. 화면은 승인 카드를 보여 준다.
        await close_unexecuted_steps(session, info.id)
        reply = outcome.final_answer if outcome.status in ANSWERED_STATUSES else outcome.error
        if outcome.status == TaskStatus.CANCELLED:
            reply = (reply or "") + await completed_external_note(session, info.id)
        session.add(
            Message(
                conversation_id=info.conversation_id,
                role=MessageRole.ASSISTANT,
                content=reply or "",
                task_id=info.id,
                suggestions=outcome.suggestions if outcome.status in ANSWERED_STATUSES else None,
            )
        )
    conversation = await session.get_one(Conversation, info.conversation_id)
    conversation.updated_at = func.now()
    await session.commit()


async def has_unresolved_operation(session: AsyncSession, task_id: int) -> bool:
    """이 task에 결과를 확인하지 못한 외부 작업(unknown·reconciling·executing)이 있는가.

    task id로만 찾는다(호출한 쪽이 이미 그 task를 잠근 내부 함수). True면 save_outcome이 done을 needs_review로 바꾼다.
    """
    found = await session.scalar(
        select(Operation.id)
        .join(ActionPlan, ActionPlan.id == Operation.plan_id)
        .where(ActionPlan.task_id == task_id, Operation.status.in_(UNRESOLVED_OPERATIONS))
        .limit(1)
    )
    return found is not None


def push_kind_for(info: TaskInfo, status: TaskStatus, *, finished_at: float) -> PushKind | None:
    """명세의 푸시 규칙. 승인 필요·실패는 항상, 완료는 20초 넘게 걸렸거나 자동 작업일 때만.

    None이면 푸시하지 않는다(빠른 채팅 완료, cancelled). started_at이 claim 단계 값이라 복구 뒤 계산해도
    처음 시작 시각 기준이다. finished_at은 finish 단계 안의 시각이고 그 결과(PushKind)가 단계로 기록된다.
    """
    if status == TaskStatus.WAITING_APPROVAL:
        return PushKind.APPROVAL_NEEDED
    if status in FAILED_STATUSES:
        return PushKind.TASK_FAILED
    if status == TaskStatus.NEEDS_REVIEW:
        return PushKind.TASK_NEEDS_REVIEW
    if status == TaskStatus.DONE:
        slow = finished_at - info.started_at > TASK_DONE_NOTIFY_AFTER_SECONDS
        return PushKind.TASK_DONE if slow or info.origin != TaskOrigin.CHAT else None
    return None


# finish 보충(40줄 제한으로 함수 위에 둔다): "finish_task" 단계로 실행된다.
# - 순서: 승인 요청 준비(외부 조회·감시 모델 호출, task 잠금 전) → task 잠금 → 상태 검사 → 저장·커밋.
#   준비가 끝난 뒤 상태를 다시 보므로 그 사이 들어온 취소 요청도 반영된다.
# - 안전 분기: 자동 작업의 승인 요청은 실패로(절대 원칙 7, 자동 작업은 READ만), 계획을 못 만드는 호출은 실패로,
#   취소 요청 뒤의 승인 대기는 cancelled로 바꿔 승인 카드를 만들지 않는다.
# - 결과 None: 푸시 없음(이미 저장됨, 또는 푸시 규칙상 없음).
def too_many_deletes(deferred: DeferredToolRequests) -> bool:
    """이번 승인 요청에 일정 삭제가 MAX_DELETES_PER_ROUND건보다 많은가."""
    return sum(call.tool_name == "delete_event" for call in deferred.approvals) > MAX_DELETES_PER_ROUND


async def finish(
    services: RunServices, info: TaskInfo, outcome: Outcome, previous: Sequence[ModelMessage]
) -> PushKind | None:
    """결과를 저장하고 보낼 푸시 종류를 돌려준다. 승인 대기면 승인 요청·감시 결과·감시 사용량도 같은 트랜잭션에 넣는다.

    task가 이미 running이 아니면(같은 결과를 이미 저장했으면) 아무것도 하지 않는다. 복구 때 이 단계가 다시 실행돼도
    승인 요청·답변 메시지·usage_logs가 두 번 생기지 않는다.
    """
    requests, reviews = [], []
    if outcome.deferred is not None and info.origin != TaskOrigin.CHAT:
        # 자동 작업(예약·감시)은 READ 도구만 받으므로 여기에 올 수 없다. 와도 승인을 만들지 않고 실패로 끝낸다.
        outcome = Outcome(TaskStatus.FAILED, error=UNEXPECTED_MESSAGE, new_messages=outcome.new_messages)
    if outcome.deferred is not None and too_many_deletes(outcome.deferred):
        # 승인 카드를 하나도 만들지 않는다(대량 삭제를 사람이 카드 여러 장으로 한꺼번에 승인하는 사고를 막는다).
        outcome = Outcome(TaskStatus.FAILED, error=BULK_DELETE_MESSAGE, new_messages=outcome.new_messages)
    if outcome.deferred is not None:
        try:
            requests, reviews = await prepare_requests(services, info, outcome.deferred)
        except plans.PlanRejectedError:
            # 서버 정책상 실행 계획을 만들 수 없는 호출(규격 없는 도구 등). 승인을 만들지 않고 실패로 끝낸다.
            logger.warning("task %s: 실행 계획을 만들 수 없어 실패로 끝냄", info.id, exc_info=True)
            outcome = Outcome(TaskStatus.FAILED, error=PLAN_REJECTED_MESSAGE, new_messages=outcome.new_messages)
    async with services.session_factory() as session:
        task = await lock_task(session, info.id)
        if task is None or task.status not in IN_PROGRESS:
            return None
        if task.status == TaskStatus.CANCEL_REQUESTED and outcome.status == TaskStatus.WAITING_APPROVAL:
            # 취소 요청 뒤에는 승인 요청을 만들지 않는다.
            requests = []
            outcome = Outcome(TaskStatus.CANCELLED, error=RUN_CANCELLED_MESSAGE, new_messages=outcome.new_messages)
        if requests:
            await approvals.save_requests(
                session, task_id=info.id, user_id=info.user_id, origin=info.origin, requests=requests
            )
        session.add_all(guard_usage_rows(info, reviews))
        await save_outcome(session, info, outcome, previous, payload_cipher(services.settings))
    return push_kind_for(info, outcome.status, finished_at=time.time())


def notification_key(info: TaskInfo, kind: PushKind) -> str:
    """같은 task·같은 실행 회차(워크플로)·같은 종류의 알림은 하나다. 복구로 notify 단계가 다시 돌아도 한 번만 보낸다.

    키는 PushNotifier가 notifications에서 (user, key)로 중복을 막는 데 쓴다. 워크플로 밖이면 run 워크플로 ID 형식
    (outbox.run_workflow_id와 같은 "task-{id}")을 쓴다. 예약 실행은 schedule-… 워크플로 ID가 들어간다.
    """
    workflow = current_workflow_id() or f"task-{info.id}"
    return f"{kind.value}:task-{info.id}:{workflow}"


async def send_push(services: RunServices, info: TaskInfo, kind: PushKind, key: str) -> int:
    """notify 단계 본문. 본문은 종류별 고정 문구라 task 내용이 푸시 서버로 나가지 않는다. 보낸 기기 수를 돌려준다."""
    return await services.notifier.notify(info.user_id, kind, f"/chat/{info.conversation_id}", key)


async def complete(
    services: RunServices, info: TaskInfo, prepared: Prepared, done_kind: PushKind | None = None
) -> None:
    """prepare 결과로 run을 실행(또는 바로 끝내기)하고, 결과를 저장한 뒤 푸시를 보낸다.

    done_kind: 완료(task_done) 푸시 대신 보낼 고정 문구 종류(6단계: 아침 브리핑은 briefing).
    run_task·resume_task가 함께 쓰는 뒷부분이다. execute_agent는 단계가 아니고 finish_task·notify는 단계다.
    """
    outcome = prepared.outcome
    if prepared.run_input is not None:
        outcome = await execute_agent(services, info, prepared.run_input)
    assert outcome is not None
    kind = await durable_step("finish_task", finish, services, info, outcome, prepared.previous)
    if kind == PushKind.TASK_DONE and done_kind is not None:
        kind = done_kind
    if kind is not None:
        await durable_step("notify", send_push, services, info, kind, notification_key(info, kind))


# ---- 공개 함수 ----


async def prepare_run(services: RunServices, info: TaskInfo, prompt: str) -> Prepared:
    """하루 비용 검사와 이전 기록. 6단계: 예약·감시 task는 이전 기록을 이어받지 않는다(실행마다 독립).

    '예약 작업' 대화방에는 지난 실행들의 검색 결과·일정 제목 같은 외부 글이 쌓이므로, 넘기면 주입 문장이
    다음 자동 실행으로 계속 옮겨 가고 토큰도 늘어난다.
    "prepare_run" 단계로 실행된다. 하루 한도를 넘었으면 모델을 부르지 않고 budget_exceeded로 끝낼 결과를 돌려준다.
    읽기만 한다.
    """
    async with services.session_factory() as session:
        if await daily_cost_exceeded(session, info.user_id, services.settings):
            return Prepared(outcome=Outcome(TaskStatus.BUDGET_EXCEEDED, error=DAILY_BUDGET_MESSAGE))
        history: list[ModelMessage] = []
        if info.origin == TaskOrigin.CHAT:
            history = await load_conversation_history(
                session, conversation_id=info.conversation_id, before_task_id=info.id
            )
    return Prepared(run_input=RunInput(prompt=prompt, history=history))


async def run_task(services: RunServices, task_id: int, *, prompt: str, done_kind: PushKind | None = None) -> None:
    """task 하나를 실행하고 결과를 저장한다. 예외를 밖으로 내지 않는다.

    단계: claim_task → prepare_run → (agent.run) → finish_task → notify. 이 이름·순서는 WORKFLOW_VERSION과 묶여 있다.
    claim이 None이면(이미 잡힘·취소) 아무것도 하지 않는다. done_kind는 예약 실행(check.run_scheduled_job)만 넘긴다.
    """
    info = await durable_step("claim_task", claim_run, services, task_id, current_workflow_id())
    if info is None:
        return
    prepared = await durable_step("prepare_run", prepare_run, services, info, prompt)
    await complete(services, info, prepared, done_kind)


async def previous_usage(session: AsyncSession, task_id: int) -> RunUsage:
    """이 task가 앞선 run에서 쓴 요청 수와 토큰(usage_logs의 agent 행 합계).

    재개한 run이 작업당 요청 수·토큰 한도(build_usage_limits)를 처음부터 다시 세지 않게 이어받는다.
    감시(guard) 행은 빼고 센다.
    """
    row = (
        await session.execute(
            select(
                func.count(UsageLog.id),
                func.coalesce(func.sum(UsageLog.input_tokens), 0),
                func.coalesce(func.sum(UsageLog.output_tokens), 0),
            ).where(UsageLog.task_id == task_id, UsageLog.purpose == UsagePurpose.AGENT)
        )
    ).one()
    return RunUsage(requests=int(row[0]), input_tokens=int(row[1]), output_tokens=int(row[2]))


async def load_resume_input(session: AsyncSession, info: TaskInfo, task_messages: list[ModelMessage]) -> RunInput:
    """재개 입력: 이전 대화 + 이 task 기록, 승인·거절 결과(DeferredToolResults), 이어받을 사용량.

    승인된 호출은 계획에 저장된 정규화 인자(override_args)로 실행된다(approvals.build_results, 절대 원칙 4).
    prompt는 None이다(새 사용자 메시지 없이 기록 끝의 승인 대기 호출부터 잇는다).
    """
    earlier = await load_conversation_history(session, conversation_id=info.conversation_id, before_task_id=info.id)
    results = await approvals.build_results(session, info.id, pending_tool_call_ids(task_messages))
    return RunInput(
        prompt=None,
        history=[*earlier, *task_messages],
        task_messages=task_messages,
        deferred_results=results,
        usage=await previous_usage(session, info.id),
    )


async def prepare_resume(services: RunServices, info: TaskInfo) -> Prepared:
    """prepare_resume 단계 본문. 하루 한도를 넘었거나 준비 중 오류면 run 없이 끝낼 결과를 돌려준다.

    어느 경우든 previous에 이 task 기록을 담아 finish가 기록을 잃지 않게 한다. 읽기만 한다.
    """
    async with services.session_factory() as session:
        task_messages = load_messages((await session.get_one(Task, info.id)).message_history or [])
        try:
            if await daily_cost_exceeded(session, info.user_id, services.settings):
                outcome = Outcome(TaskStatus.BUDGET_EXCEEDED, error=DAILY_BUDGET_MESSAGE)
                return Prepared(outcome=outcome, previous=task_messages)
            run_input = await load_resume_input(session, info, task_messages)
        except Exception:
            logger.exception("task %s: 재개 준비 중 오류", info.id)
            return Prepared(outcome=Outcome(TaskStatus.FAILED, error=UNEXPECTED_MESSAGE), previous=task_messages)
    return Prepared(run_input=run_input, previous=task_messages)


async def resume_task(services: RunServices, task_id: int) -> None:
    """승인이 모두 결정된 task를 이어서 실행한다. 예외를 밖으로 내지 않는다. 여러 번 불려도 한 번만 실행된다.

    단계: claim_resume → prepare_resume → (agent.run) → finish_task → notify(이름·순서는 WORKFLOW_VERSION과 묶임).
    """
    info = await durable_step("claim_resume", claim_resume, services, task_id, current_workflow_id())
    if info is None:
        return
    prepared = await durable_step("prepare_resume", prepare_resume, services, info)
    await complete(services, info, prepared)
