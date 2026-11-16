"""task 하나를 처음부터 끝까지 실행한다.

호출 흐름 (POST /api/conversations/{id}/messages → run_task):

    1. mark_running        task를 queued → running으로 바꾼다(이미 다른 상태면 아무것도 하지 않는다).
    2. 하루 비용 검사       오늘 비용이 상한 이상이면 모델을 부르지 않고 budget_exceeded로 끝낸다.
    3. 이전 기록 불러오기   같은 대화의 이전 task 기록(도구 호출 포함)을 한도 안에서 이어 붙인다(history.py).
    4. execute_agent       agent.run(...)을 실행한다. origin에 맞는 도구만, 요청·토큰 상한을 걸고.
                           어떤 예외가 나도 여기서 잡아 결과(Outcome)로 바꾼다. 서버는 죽지 않는다.
    5. save_outcome        한 트랜잭션에서 task 상태·답변·기록, usage_logs(모델 호출마다 1행), 답변 메시지를 저장한다.

1단계에서는 API가 이 함수를 기다렸다가 응답한다. 3단계에서는 DBOS 워크플로(workflows.py)가 이 함수를 부르고
API는 바로 202를 돌려준다. 그래서 이 함수는 HTTP 요청 객체를 모르게 만들었다(RunServices만 받는다).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.agent import MyAgent
from app.agent.budget import build_usage_limits, calculate_cost, daily_cost_exceeded
from app.agent.deps import AgentDeps
from app.agent.history import dump_messages, finalize_for_storage, load_conversation_history
from app.agent.toolsets import toolsets_for
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
    """run_task에 필요한 것들. 앱 시작 때 만든 것을 넘긴다."""

    agent: MyAgent
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings


@dataclass(frozen=True)
class TaskInfo:
    id: int
    user_id: int
    conversation_id: int
    origin: TaskOrigin


@dataclass
class Outcome:
    status: TaskStatus
    final_answer: str | None = None
    error: str | None = None
    new_messages: list[ModelMessage] = field(default_factory=list)


async def mark_running(session: AsyncSession, task_id: int) -> TaskInfo | None:
    """queued인 task만 running으로 바꾼다. 행을 잠가 같은 task가 두 번 시작되지 않게 한다."""
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None or task.status != TaskStatus.QUEUED:
        return None
    task.status = TaskStatus.RUNNING
    await session.commit()
    return TaskInfo(id=task.id, user_id=task.user_id, conversation_id=task.conversation_id, origin=task.origin)


async def execute_agent(services: RunServices, info: TaskInfo, prompt: str, history: list[ModelMessage]) -> Outcome:
    deps = AgentDeps(user_id=info.user_id, task_id=info.id, origin=info.origin, db=services.session_factory)
    with capture_run_messages() as captured:
        try:
            result = await services.agent.run(
                prompt,
                deps=deps,
                message_history=history,
                toolsets=toolsets_for(info.origin),
                usage_limits=build_usage_limits(services.settings),
            )
            return Outcome(TaskStatus.DONE, final_answer=result.output, new_messages=captured[len(history) :])
        except UsageLimitExceeded:
            return Outcome(TaskStatus.BUDGET_EXCEEDED, error=TASK_BUDGET_MESSAGE, new_messages=captured[len(history) :])
        except UnexpectedModelBehavior:
            logger.warning("task %s: 모델 응답 이상으로 실패", info.id, exc_info=True)
            return Outcome(TaskStatus.FAILED, error=MODEL_BEHAVIOR_MESSAGE, new_messages=captured[len(history) :])
        except Exception:
            logger.exception("task %s: 예상하지 못한 오류로 실패", info.id)
            return Outcome(TaskStatus.FAILED, error=UNEXPECTED_MESSAGE, new_messages=captured[len(history) :])


def usage_rows(info: TaskInfo, messages: Sequence[ModelMessage]) -> list[UsageLog]:
    """모델 응답 하나마다 usage_logs 한 행. 중단된 응답도 토큰은 이미 썼으므로 기록한다."""
    rows = []
    for response in (m for m in messages if isinstance(m, ModelResponse)):
        model = response.model_name or "unknown"
        input_tokens, output_tokens = response.usage.input_tokens, response.usage.output_tokens
        rows.append(
            UsageLog(
                user_id=info.user_id,
                task_id=info.id,
                purpose=UsagePurpose.AGENT,
                model=model[:100],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=calculate_cost(model, input_tokens, output_tokens),
            )
        )
    return rows


async def save_outcome(session: AsyncSession, info: TaskInfo, outcome: Outcome) -> None:
    task = await session.get_one(Task, info.id)
    responses = [m for m in outcome.new_messages if isinstance(m, ModelResponse)]
    task.status = outcome.status
    task.final_answer = outcome.final_answer
    task.error = outcome.error
    task.request_count = len(responses)
    task.total_tokens = sum(r.usage.total_tokens for r in responses)
    finished = outcome.status == TaskStatus.DONE
    stored = finalize_for_storage(outcome.new_messages, finished=finished)
    task.message_history = dump_messages(stored) if stored else None
    session.add_all(usage_rows(info, outcome.new_messages))
    reply = outcome.final_answer if finished else outcome.error
    session.add(
        Message(conversation_id=info.conversation_id, role=MessageRole.ASSISTANT, content=reply or "", task_id=info.id)
    )
    conversation = await session.get_one(Conversation, info.conversation_id)
    conversation.updated_at = func.now()
    await session.commit()


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
    outcome = await execute_agent(services, info, prompt, history)
    async with services.session_factory() as session:
        await save_outcome(session, info, outcome)
