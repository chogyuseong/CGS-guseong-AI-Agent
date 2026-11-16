"""이전 task 기록 이어받기.

왜: Django판에서 모델이 이전 작업에서 자기가 한 일(도구 호출)을 몰라 같은 일을 두 번 한 버그가 있었다.
그래서 새 task는 같은 대화의 이전 task들이 남긴 Pydantic AI 메시지 기록(도구 호출과 결과 포함)을 이어받는다.

자르는 규칙 (도구 호출과 그 결과의 짝이 깨지지 않게):
- 자르는 단위는 "task 하나의 기록 전체"다. task 기록은 사용자 요청으로 시작해 답변으로 끝나므로
  task 경계에서 자르면 도구 호출(ToolCallPart)과 결과(ToolReturnPart)가 항상 같이 남거나 같이 빠진다.
- 최근 MAX_TASKS(10)개 task까지만, 그리고 JSON 글자 수 합이 MAX_CHARS(40,000)를 넘지 않게 오래된 task부터 뺀다.
  40,000자는 한국어 기준 대략 2~4만 토큰이다. TASK_MAX_TOKENS(60,000)는 한 task의 모든 요청 토큰 합이라,
  매 요청에 다시 실리는 이전 기록을 이 정도로 묶어야 도구를 몇 번 부르는 task가 상한 안에서 끝난다.
- 가장 최근 task 하나만으로도 한도를 넘으면 그 task도 빼고 빈 기록으로 시작한다(일부만 넣으면 짝이 깨진다).

저장 규칙(finalize_for_storage):
- 실패한 run의 중간 메시지(state='interrupted')와 결과 없이 끝난 마지막 도구 호출은 저장하지 않는다.
- 답변 없이 끝난 task(실패, 상한 초과)는 끝에 "중단됨" 응답을 붙여 저장한다. 이미 실행된 도구 호출과 결과는
  남기므로 다음 task의 모델이 "그때 할 일을 이미 추가했다"는 사실을 안다.
"""

import json
from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task
from app.models.enums import TaskStatus

UNFINISHED_STATUSES = (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
MAX_TASKS = 10
MAX_CHARS = 40_000
INTERRUPTED_NOTE = "(이 작업은 답변을 마치기 전에 중단되었다. 위의 도구 호출 결과까지만 실제로 일어난 일이다.)"


def dump_messages(messages: Sequence[ModelMessage]) -> list[Any]:
    return ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")


def load_messages(data: list[Any]) -> list[ModelMessage]:
    return ModelMessagesTypeAdapter.validate_python(data)


def without_unfinished_tail(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """중단된 메시지와, 결과 없이 끝난 마지막 도구 호출 응답을 뺀다."""
    cleaned = without_interrupted(messages)
    while cleaned and isinstance(cleaned[-1], ModelResponse):
        if not any(isinstance(p, ToolCallPart) for p in cleaned[-1].parts):
            break
        cleaned.pop()
    return cleaned


def finalize_for_storage(messages: Sequence[ModelMessage], *, finished: bool) -> list[ModelMessage]:
    cleaned = without_unfinished_tail(messages)
    if cleaned and not finished and isinstance(cleaned[-1], ModelRequest):
        cleaned.append(ModelResponse(parts=[TextPart(INTERRUPTED_NOTE)]))
    return cleaned


def without_interrupted(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    return [m for m in messages if getattr(m, "state", "complete") != "interrupted"]


def history_for_storage(
    previous: Sequence[ModelMessage], new: Sequence[ModelMessage], *, status: TaskStatus
) -> list[ModelMessage]:
    """task.message_history에 저장할 기록. 재개한 run이면 previous(이 task가 이미 저장한 기록)에 이어 붙인다.

    승인 대기(waiting_approval)일 때는 결과 없는 도구 호출을 지우지 않는다. 재개할 때 그 호출에 승인·거절 결과를
    붙여야 하기 때문이다. 다른 상태는 finalize_for_storage 규칙을 따른다.
    """
    combined = [*previous, *without_interrupted(new)]
    if status == TaskStatus.WAITING_APPROVAL:
        return combined
    return finalize_for_storage(combined, finished=status == TaskStatus.DONE)


def pending_tool_call_ids(messages: Sequence[ModelMessage]) -> list[str]:
    """결과(ToolReturnPart 등)가 아직 없는 도구 호출의 id. 승인 대기 중인 호출이 여기에 남는다."""
    answered = {
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart | RetryPromptPart)
    }
    return [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_call_id not in answered
    ]


def build_history(
    task_histories: Sequence[list[ModelMessage]], *, max_tasks: int = MAX_TASKS, max_chars: int = MAX_CHARS
) -> list[ModelMessage]:
    """task별 기록(오래된 것부터)을 받아 최근 것부터 한도 안에서 고르고, 다시 시간 순서로 이어 붙인다."""
    chosen: list[list[ModelMessage]] = []
    used_chars = 0
    for messages in reversed(task_histories[-max_tasks:] if max_tasks > 0 else []):
        size = len(json.dumps(dump_messages(messages), ensure_ascii=False))
        if used_chars + size > max_chars:
            break
        chosen.append(messages)
        used_chars += size
    return [message for messages in reversed(chosen) for message in messages]


async def load_conversation_history(
    session: AsyncSession, *, conversation_id: int, before_task_id: int
) -> list[ModelMessage]:
    """같은 대화에서 이 task보다 먼저 만든 task들의 기록을 한도 안에서 이어 붙인다.

    아직 끝나지 않은 task(실행 중, 승인 대기)는 넣지 않는다. 승인 대기 task의 기록은 결과 없는 도구 호출로 끝나서
    다음 task의 모델이 "실행됐는지 모르는 호출"을 보게 된다. 취소된 task처럼 그런 호출이 남은 기록은
    finalize_for_storage로 정리해서 넣는다(호출과 결과의 짝이 깨지지 않게).
    """
    rows = await session.scalars(
        select(Task.message_history)
        .where(
            Task.conversation_id == conversation_id,
            Task.id < before_task_id,
            Task.message_history.is_not(None),
            Task.status.not_in(UNFINISHED_STATUSES),
        )
        .order_by(Task.id.desc())
        .limit(MAX_TASKS)
    )
    histories = [finalize_for_storage(load_messages(data), finished=False) for data in reversed(rows.all()) if data]
    return build_history(histories)
