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
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import RunUsage
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals
from app.agent.agent import MyAgent
from app.agent.budget import (
    UNPRICED_MESSAGE,
    DailyBudgetExceededError,
    UnpricedModelError,
    build_usage_limits,
    calculate_cost,
    daily_cost_exceeded,
)
from app.agent.cancel import RUN_CANCELLED_MESSAGE, TaskCancelledError
from app.agent.deps import AgentDeps
from app.agent.durable import current_workflow_id, durable_step
from app.agent.guard import GuardAgent, GuardReview, check_action
from app.agent.history import (
    dump_messages,
    history_for_storage,
    load_conversation_history,
    load_messages,
    pending_tool_call_ids,
)
from app.agent.toolsets import label_for, preview_for, toolsets_for
from app.config import Settings
from app.models import AgentRun, Conversation, Message, Step, Task, UsageLog
from app.models.enums import MessageRole, RunKind, StepStatus, TaskOrigin, TaskStatus, UsagePurpose
from app.push import PushKind, PushNotifier

logger = logging.getLogger(__name__)

DAILY_BUDGET_MESSAGE = "오늘 쓸 수 있는 비용 한도를 다 써서 작업을 시작하지 않았습니다. 내일 다시 시도해 주세요."
TASK_BUDGET_MESSAGE = "이 작업에 정해 둔 요청 수나 토큰 한도를 넘어 중간에 멈췄습니다. 위 단계까지만 처리되었습니다."
MODEL_BEHAVIOR_MESSAGE = "모델이 도구를 올바르게 부르지 못해 작업을 끝내지 못했습니다."
UNEXPECTED_MESSAGE = "작업 중 오류가 발생해 끝내지 못했습니다."
# 승인됐지만 작업이 그 전에 끝나(하루 비용 상한, 오류) 실행하지 않은 EXTERNAL 호출의 step 결과.
NOT_EXECUTED_MESSAGE = "작업이 이 단계 전에 멈춰 실행하지 않았습니다."
# 명세: task_done 푸시는 20초 넘게 걸렸거나 origin이 schedule·watch일 때만.
TASK_DONE_NOTIFY_AFTER_SECONDS = 20.0
FAILED_STATUSES = (TaskStatus.FAILED, TaskStatus.BUDGET_EXCEEDED)
# finish가 결과를 저장할 수 있는 상태. 실행 중 취소 요청이 와도 끝난 결과는 저장한다.
IN_PROGRESS = (TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED)


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
    status: TaskStatus
    final_answer: str | None = None
    error: str | None = None
    new_messages: list[ModelMessage] = field(default_factory=list)
    deferred: DeferredToolRequests | None = None


@dataclass
class Prepared:
    """prepare 단계의 결과. run_input이 있으면 agent.run을, 없으면 outcome으로 바로 끝낸다."""

    run_input: RunInput | None = None
    outcome: Outcome | None = None
    previous: list[ModelMessage] = field(default_factory=list)  # 이 task가 이미 저장한 기록(재개일 때)


# ---- 시작과 재개: task 행 잠그기 ----


async def task_info(session: AsyncSession, task: Task) -> TaskInfo:
    request_text = None
    if task.trigger_message_id is not None:
        request_text = await session.scalar(select(Message.content).where(Message.id == task.trigger_message_id))
    return TaskInfo(
        task.id, task.user_id, task.conversation_id, task.origin, request_text=request_text, started_at=time.time()
    )


async def lock_task(session: AsyncSession, task_id: int) -> Task | None:
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
    """queued인 task만 running으로 바꾼다. 행을 잠가 같은 task가 두 번 시작되지 않게 한다."""
    async with services.session_factory() as session:
        task = await lock_task(session, task_id)
        if task is None or not (task.status == TaskStatus.QUEUED or reclaimed(task, workflow_id)):
            return None
        return await mark_claimed(session, task, workflow_id, RunKind.RUN)


async def claim_resume(services: RunServices, task_id: int, workflow_id: str | None) -> TaskInfo | None:
    """승인 대기이고 pending 승인이 없는 task만 running으로 바꾼다. 동시에 두 번 불려도 한 번만 통과한다."""
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
    """아직 끝나지 않은 마지막 회차에 결과 상태·요청 수·토큰을 채운다."""
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


def outcome_of(output: str | DeferredToolRequests, new_messages: list[ModelMessage]) -> Outcome:
    if not isinstance(output, DeferredToolRequests):
        return Outcome(TaskStatus.DONE, final_answer=output, new_messages=new_messages)
    if output.calls:
        # 승인이 아닌 "외부 실행 대기"(CallDeferred)는 이 앱에 없다. 생기면 실패로 끝낸다.
        logger.error("처리할 수 없는 지연 호출: %s", [c.tool_name for c in output.calls])
        return Outcome(TaskStatus.FAILED, error=UNEXPECTED_MESSAGE, new_messages=new_messages)
    return Outcome(TaskStatus.WAITING_APPROVAL, new_messages=new_messages, deferred=output)


def deps_for(services: RunServices, info: TaskInfo) -> AgentDeps:
    return AgentDeps(
        user_id=info.user_id,
        task_id=info.id,
        origin=info.origin,
        db=services.session_factory,
        settings=services.settings,
        http=services.http,
    )


async def execute_agent(services: RunServices, info: TaskInfo, run_input: RunInput) -> Outcome:
    deps = deps_for(services, info)
    history = run_input.history
    with capture_run_messages() as captured:
        try:
            result = await services.agent.run(
                run_input.prompt,
                deps=deps,
                message_history=history,
                deferred_tool_results=run_input.deferred_results,
                toolsets=toolsets_for(info.origin),
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


async def prepare_requests(
    services: RunServices, info: TaskInfo, deferred: DeferredToolRequests
) -> tuple[list[approvals.ApprovalRequest], list[GuardReview]]:
    """승인 대상 호출마다 preview 문장을 만들고 감시 에이전트에게 검사받는다.

    감시 에이전트에는 원래 요청·도구 이름·도구 입력·preview만 넘긴다. 도구 결과와 메시지 기록은 넘기지 않는다.
    """
    deps = deps_for(services, info)
    requests, reviews = [], []
    for call in deferred.approvals:
        args = call.args_as_dict()
        summary = await preview_for(deps, call.tool_name, args)
        scope = replace(
            deps.budget_scope, key_prefix=f"guard:{info.id}:{call.tool_call_id}", purpose=UsagePurpose.GUARD
        )
        review = await check_action(
            services.guard,
            scope,
            request_text=info.request_text or "",
            tool_name=call.tool_name,
            tool_input=args,
            preview=summary,
        )
        reviews.append(review)
        requests.append(
            approvals.ApprovalRequest(
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                args=args,
                label=label_for(call.tool_name, args),
                summary=summary,
                verdict=review.verdict,
                reason=review.reason,
                guard_model=review.model,
            )
        )
    return requests, reviews


# ---- 저장 ----


def usage_log(info: TaskInfo, purpose: UsagePurpose, model: str, input_tokens: int, output_tokens: int) -> UsageLog:
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
    """
    output: dict[str, Any] = {"ok": False, "error": NOT_EXECUTED_MESSAGE}
    await session.execute(
        update(Step)
        .where(Step.task_id == task_id, Step.status == StepStatus.WAITING_APPROVAL)
        .values(status=StepStatus.FAILED, ok=False, output=output)
    )


async def save_outcome(
    session: AsyncSession, info: TaskInfo, outcome: Outcome, previous: Sequence[ModelMessage] = ()
) -> None:
    """task 상태·기록·사용량을 저장하고 커밋한다. 재개한 run이면 요청 수·토큰·기록을 앞선 run에 더한다."""
    task = await session.get_one(Task, info.id)
    responses = [m for m in outcome.new_messages if isinstance(m, ModelResponse)]
    task.status = outcome.status
    await record_run_finish(session, info.id, outcome.status, responses)
    task.final_answer = outcome.final_answer
    task.error = outcome.error
    task.request_count = (task.request_count or 0) + len(responses)
    task.total_tokens = (task.total_tokens or 0) + sum(r.usage.total_tokens for r in responses)
    stored = history_for_storage(previous, outcome.new_messages, status=outcome.status)
    task.message_history = dump_messages(stored) if stored else None
    session.add_all(usage_rows(info, outcome.new_messages))
    if outcome.status != TaskStatus.WAITING_APPROVAL:
        # 승인 대기 중에는 답변을 남기지 않는다. 화면은 승인 카드를 보여 준다.
        await close_unexecuted_steps(session, info.id)
        reply = outcome.final_answer if outcome.status == TaskStatus.DONE else outcome.error
        session.add(
            Message(
                conversation_id=info.conversation_id, role=MessageRole.ASSISTANT, content=reply or "", task_id=info.id
            )
        )
    conversation = await session.get_one(Conversation, info.conversation_id)
    conversation.updated_at = func.now()
    await session.commit()


def push_kind_for(info: TaskInfo, status: TaskStatus, *, finished_at: float) -> PushKind | None:
    """명세의 푸시 규칙. 승인 필요·실패는 항상, 완료는 20초 넘게 걸렸거나 자동 작업일 때만."""
    if status == TaskStatus.WAITING_APPROVAL:
        return PushKind.APPROVAL_NEEDED
    if status in FAILED_STATUSES:
        return PushKind.TASK_FAILED
    if status == TaskStatus.DONE:
        slow = finished_at - info.started_at > TASK_DONE_NOTIFY_AFTER_SECONDS
        return PushKind.TASK_DONE if slow or info.origin != TaskOrigin.CHAT else None
    return None


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
    if outcome.deferred is not None:
        requests, reviews = await prepare_requests(services, info, outcome.deferred)
    async with services.session_factory() as session:
        task = await lock_task(session, info.id)
        if task is None or task.status not in IN_PROGRESS:
            return None
        if task.status == TaskStatus.CANCEL_REQUESTED and outcome.status == TaskStatus.WAITING_APPROVAL:
            # 취소 요청 뒤에는 승인 요청을 만들지 않는다.
            requests = []
            outcome = Outcome(TaskStatus.CANCELLED, error=RUN_CANCELLED_MESSAGE, new_messages=outcome.new_messages)
        if requests:
            await approvals.save_requests(session, task_id=info.id, origin=info.origin, requests=requests)
        session.add_all(guard_usage_rows(info, reviews))
        await save_outcome(session, info, outcome, previous)
    return push_kind_for(info, outcome.status, finished_at=time.time())


async def send_push(services: RunServices, info: TaskInfo, kind: PushKind) -> int:
    return await services.notifier.notify(info.user_id, kind, f"/chat/{info.conversation_id}")


async def complete(
    services: RunServices, info: TaskInfo, prepared: Prepared, done_kind: PushKind | None = None
) -> None:
    """prepare 결과로 run을 실행(또는 바로 끝내기)하고, 결과를 저장한 뒤 푸시를 보낸다.

    done_kind: 완료(task_done) 푸시 대신 보낼 고정 문구 종류(6단계: 아침 브리핑은 briefing).
    """
    outcome = prepared.outcome
    if prepared.run_input is not None:
        outcome = await execute_agent(services, info, prepared.run_input)
    assert outcome is not None
    kind = await durable_step("finish_task", finish, services, info, outcome, prepared.previous)
    if kind == PushKind.TASK_DONE and done_kind is not None:
        kind = done_kind
    if kind is not None:
        await durable_step("notify", send_push, services, info, kind)


# ---- 공개 함수 ----


async def prepare_run(services: RunServices, info: TaskInfo, prompt: str) -> Prepared:
    """하루 비용 검사와 이전 기록. 6단계: 예약·감시 task는 이전 기록을 이어받지 않는다(실행마다 독립).

    '예약 작업' 대화방에는 지난 실행들의 검색 결과·일정 제목 같은 외부 글이 쌓이므로, 넘기면 주입 문장이
    다음 자동 실행으로 계속 옮겨 가고 토큰도 늘어난다.
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
    """task 하나를 실행하고 결과를 저장한다. 예외를 밖으로 내지 않는다."""
    info = await durable_step("claim_task", claim_run, services, task_id, current_workflow_id())
    if info is None:
        return
    prepared = await durable_step("prepare_run", prepare_run, services, info, prompt)
    await complete(services, info, prepared, done_kind)


async def previous_usage(session: AsyncSession, task_id: int) -> RunUsage:
    """이 task가 앞선 run에서 쓴 요청 수와 토큰(usage_logs의 agent 행 합계)."""
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
    """승인이 모두 결정된 task를 이어서 실행한다. 예외를 밖으로 내지 않는다. 여러 번 불려도 한 번만 실행된다."""
    info = await durable_step("claim_resume", claim_resume, services, task_id, current_workflow_id())
    if info is None:
        return
    prepared = await durable_step("prepare_resume", prepare_resume, services, info)
    await complete(services, info, prepared)
