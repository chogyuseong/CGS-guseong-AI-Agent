"""task 하나를 실행하고(run_task), 승인이 모두 결정되면 이어서 실행한다(resume_task).

run_task 흐름 (POST /api/conversations/{id}/messages → run_task):

    1. mark_running        task를 queued → running으로 바꾼다(이미 다른 상태면 아무것도 하지 않는다).
    2. 하루 비용 검사       오늘 비용이 상한 이상이면 모델을 부르지 않고 budget_exceeded로 끝낸다.
    3. 이전 기록 불러오기   같은 대화의 이전 task 기록(도구 호출 포함)을 한도 안에서 이어 붙인다(history.py).
    4. execute_agent       agent.run(...)을 실행한다. origin에 맞는 도구만, 요청·토큰 상한을 걸고.
                           어떤 예외가 나도 여기서 잡아 결과(Outcome)로 바꾼다. 서버는 죽지 않는다.
    5. finish              결과가 승인 대기(DeferredToolRequests)면 호출마다 preview와 감시 에이전트 검사를 만들고,
                           한 트랜잭션에서 승인 요청(approvals.save_requests), task 상태·기록, usage_logs,
                           답변 메시지(승인 대기가 아닐 때만)를 저장한다.

resume_task 흐름 (POST /api/approvals/{id}/approve|reject가 마지막 결정을 받았을 때):

    1. claim_for_resume    waiting_approval이고 pending 승인이 없는 task만 running으로 바꾼다(행 잠금).
                           승인 버튼을 동시에 두 번 눌러도 여기서 하나만 통과한다.
    2. 하루 비용 검사
    3. 재개 입력 만들기     이전 대화 기록 + 이 task가 저장한 기록,
                           승인=ToolApproved / 거절=ToolDenied('사용자가 거절함'),
                           이 task가 이미 쓴 사용량(usage_logs 합계) → 상한을 이어서 계산한다.
    4~5. run_task와 같다(재개한 run이 또 EXTERNAL을 부르면 다시 승인 대기가 된다).

지금은 API가 이 함수들을 기다렸다가 응답한다. 3단계에서는 DBOS 워크플로(workflows.py)의 run_task·resume_task가
이 함수들을 부르고 API는 바로 응답한다. 그래서 HTTP 요청 객체를 모르게 만들었다(RunServices만 받는다).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import RunUsage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import approvals
from app.agent.agent import MyAgent
from app.agent.budget import build_usage_limits, calculate_cost, daily_cost_exceeded
from app.agent.deps import AgentDeps
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
from app.models import Conversation, Message, Task, UsageLog
from app.models.enums import MessageRole, TaskOrigin, TaskStatus, UsagePurpose

logger = logging.getLogger(__name__)

DAILY_BUDGET_MESSAGE = "오늘 쓸 수 있는 비용 한도를 다 써서 작업을 시작하지 않았습니다. 내일 다시 시도해 주세요."
TASK_BUDGET_MESSAGE = "이 작업에 정해 둔 요청 수나 토큰 한도를 넘어 중간에 멈췄습니다. 위 단계까지만 처리되었습니다."
MODEL_BEHAVIOR_MESSAGE = "모델이 도구를 올바르게 부르지 못해 작업을 끝내지 못했습니다."
UNEXPECTED_MESSAGE = "작업 중 오류가 발생해 끝내지 못했습니다."


@dataclass(frozen=True)
class RunServices:
    """run_task·resume_task에 필요한 것들. 앱 시작 때 만든 것을 넘긴다."""

    agent: MyAgent
    guard: GuardAgent
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings


@dataclass(frozen=True)
class TaskInfo:
    id: int
    user_id: int
    conversation_id: int
    origin: TaskOrigin
    # 사용자의 원래 요청 문장. 감시 에이전트의 입력이 된다. 메시지 없이 시작한 task는 None.
    request_text: str | None = None


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


# ---- 시작과 재개: task 행 잠그기 ----


async def task_info(session: AsyncSession, task: Task) -> TaskInfo:
    request_text = None
    if task.trigger_message_id is not None:
        request_text = await session.scalar(select(Message.content).where(Message.id == task.trigger_message_id))
    return TaskInfo(task.id, task.user_id, task.conversation_id, task.origin, request_text=request_text)


async def mark_running(session: AsyncSession, task_id: int) -> TaskInfo | None:
    """queued인 task만 running으로 바꾼다. 행을 잠가 같은 task가 두 번 시작되지 않게 한다."""
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None or task.status != TaskStatus.QUEUED:
        return None
    task.status = TaskStatus.RUNNING
    info = await task_info(session, task)
    await session.commit()
    return info


async def claim_for_resume(session: AsyncSession, task_id: int) -> TaskInfo | None:
    """승인 대기이고 pending 승인이 없는 task만 running으로 바꾼다. 동시에 두 번 불려도 한 번만 통과한다."""
    task = await session.scalar(
        select(Task).where(Task.id == task_id).with_for_update().execution_options(populate_existing=True)
    )
    if task is None or task.status != TaskStatus.WAITING_APPROVAL:
        return None
    if await approvals.pending_count(session, task_id) > 0:
        return None
    task.status = TaskStatus.RUNNING
    info = await task_info(session, task)
    await session.commit()
    return info


# ---- agent.run ----


def outcome_of(output: str | DeferredToolRequests, new_messages: list[ModelMessage]) -> Outcome:
    if not isinstance(output, DeferredToolRequests):
        return Outcome(TaskStatus.DONE, final_answer=output, new_messages=new_messages)
    if output.calls:
        # 승인이 아닌 "외부 실행 대기"(CallDeferred)는 이 앱에 없다. 생기면 실패로 끝낸다.
        logger.error("처리할 수 없는 지연 호출: %s", [c.tool_name for c in output.calls])
        return Outcome(TaskStatus.FAILED, error=UNEXPECTED_MESSAGE, new_messages=new_messages)
    return Outcome(TaskStatus.WAITING_APPROVAL, new_messages=new_messages, deferred=output)


async def execute_agent(services: RunServices, info: TaskInfo, run_input: RunInput) -> Outcome:
    deps = AgentDeps(user_id=info.user_id, task_id=info.id, origin=info.origin, db=services.session_factory)
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
        except UsageLimitExceeded:
            return Outcome(TaskStatus.BUDGET_EXCEEDED, error=TASK_BUDGET_MESSAGE, new_messages=captured[len(history) :])
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
    deps = AgentDeps(user_id=info.user_id, task_id=info.id, origin=info.origin, db=services.session_factory)
    requests, reviews = [], []
    for call in deferred.approvals:
        args = call.args_as_dict()
        summary = await preview_for(deps, call.tool_name, args)
        review = await check_action(
            services.guard,
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


async def save_outcome(
    session: AsyncSession, info: TaskInfo, outcome: Outcome, previous: Sequence[ModelMessage] = ()
) -> None:
    """task 상태·기록·사용량을 저장하고 커밋한다. 재개한 run이면 요청 수·토큰·기록을 앞선 run에 더한다."""
    task = await session.get_one(Task, info.id)
    responses = [m for m in outcome.new_messages if isinstance(m, ModelResponse)]
    task.status = outcome.status
    task.final_answer = outcome.final_answer
    task.error = outcome.error
    task.request_count = (task.request_count or 0) + len(responses)
    task.total_tokens = (task.total_tokens or 0) + sum(r.usage.total_tokens for r in responses)
    stored = history_for_storage(previous, outcome.new_messages, status=outcome.status)
    task.message_history = dump_messages(stored) if stored else None
    session.add_all(usage_rows(info, outcome.new_messages))
    if outcome.status != TaskStatus.WAITING_APPROVAL:
        # 승인 대기 중에는 답변을 남기지 않는다. 화면은 승인 카드를 보여 준다.
        reply = outcome.final_answer if outcome.status == TaskStatus.DONE else outcome.error
        session.add(
            Message(
                conversation_id=info.conversation_id, role=MessageRole.ASSISTANT, content=reply or "", task_id=info.id
            )
        )
    conversation = await session.get_one(Conversation, info.conversation_id)
    conversation.updated_at = func.now()
    await session.commit()


async def finish(services: RunServices, info: TaskInfo, outcome: Outcome, previous: Sequence[ModelMessage]) -> None:
    """결과를 저장한다. 승인 대기면 승인 요청·감시 결과·감시 사용량도 같은 트랜잭션에 넣는다."""
    requests, reviews = [], []
    if outcome.deferred is not None and info.origin != TaskOrigin.CHAT:
        # 자동 작업(예약·감시)은 READ 도구만 받으므로 여기에 올 수 없다. 와도 승인을 만들지 않고 실패로 끝낸다.
        outcome = Outcome(TaskStatus.FAILED, error=UNEXPECTED_MESSAGE, new_messages=outcome.new_messages)
    if outcome.deferred is not None:
        requests, reviews = await prepare_requests(services, info, outcome.deferred)
    async with services.session_factory() as session:
        if requests:
            await approvals.save_requests(session, task_id=info.id, origin=info.origin, requests=requests)
        session.add_all(guard_usage_rows(info, reviews))
        await save_outcome(session, info, outcome, previous)


# ---- 공개 함수 ----


async def run_task(services: RunServices, task_id: int, *, prompt: str) -> None:
    """task 하나를 실행하고 결과를 저장한다. 예외를 밖으로 내지 않는다."""
    async with services.session_factory() as session:
        info = await mark_running(session, task_id)
        if info is None:
            return
        if await daily_cost_exceeded(session, info.user_id, services.settings):
            await save_outcome(session, info, Outcome(TaskStatus.BUDGET_EXCEEDED, error=DAILY_BUDGET_MESSAGE))
            return
        history = await load_conversation_history(session, conversation_id=info.conversation_id, before_task_id=info.id)
    outcome = await execute_agent(services, info, RunInput(prompt=prompt, history=history))
    await finish(services, info, outcome, previous=[])


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


async def load_resume_input(session: AsyncSession, info: TaskInfo) -> RunInput:
    task = await session.get_one(Task, info.id)
    task_messages = load_messages(task.message_history or [])
    earlier = await load_conversation_history(session, conversation_id=info.conversation_id, before_task_id=info.id)
    results = await approvals.build_results(session, info.id, pending_tool_call_ids(task_messages))
    return RunInput(
        prompt=None,
        history=[*earlier, *task_messages],
        task_messages=task_messages,
        deferred_results=results,
        usage=await previous_usage(session, info.id),
    )


async def resume_task(services: RunServices, task_id: int) -> None:
    """승인이 모두 결정된 task를 이어서 실행한다. 예외를 밖으로 내지 않는다. 여러 번 불려도 한 번만 실행된다."""
    async with services.session_factory() as session:
        info = await claim_for_resume(session, task_id)
        if info is None:
            return
        task_messages = load_messages((await session.get_one(Task, info.id)).message_history or [])
        try:
            if await daily_cost_exceeded(session, info.user_id, services.settings):
                await save_outcome(
                    session, info, Outcome(TaskStatus.BUDGET_EXCEEDED, error=DAILY_BUDGET_MESSAGE), task_messages
                )
                return
            run_input = await load_resume_input(session, info)
        except Exception:
            logger.exception("task %s: 재개 준비 중 오류", info.id)
            await session.rollback()
            await save_outcome(session, info, Outcome(TaskStatus.FAILED, error=UNEXPECTED_MESSAGE), task_messages)
            return
    outcome = await execute_agent(services, info, run_input)
    await finish(services, info, outcome, previous=run_input.task_messages)
