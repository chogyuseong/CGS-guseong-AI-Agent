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
- 메일 도구(list_mail·read_mail) 결과는 앞 500자만, PAYLOAD_ENCRYPTION_KEY로 암호화해 저장한다
  (7단계 보강, seal_mail_returns). 모델에게 넘기기 직전에만 푼다(open_mail_returns, runner.execute_agent).
- 답변 없이 끝난 task(실패, 상한 초과)는 끝에 "중단됨" 응답을 붙여 저장한다. 이미 실행된 도구 호출과 결과는
  남기므로 다음 task의 모델이 "그때 할 일을 이미 추가했다"는 사실을 안다.

진입점(모두 runner): prepare_run(chat task만. 예약 task는 이어받지 않는다, DECISIONS 2026-11-17)과
prepare_resume이 load_conversation_history, execute_agent가 open_mail_returns, finish 단계가 history_for_storage,
prepare_resume이 pending_tool_call_ids. evals/harness.py와 테스트가 dump_messages·load_messages를 쓴다.
근거: DECISIONS 2026-11-16(task 단위로 자르기, 10개·40,000자), 2026-11-17(승인 대기 기록은 결과 없는 호출까지 저장),
7단계 보강(메일 결과 암호화, app/payload_crypto.py).

고칠 때 주의:
- tasks.message_history의 모양(Pydantic AI 메시지 JSON)은 보관 정리(app/automation/retention.py, 7일 뒤 비움)와
  docs/DATA_POLICY.md "모델 기록" 줄의 설명과 맞아야 한다. 새 내용을 저장하면 그 표에 넣는다.
- 봉할 도구 이름 목록은 results.SEALED_TOOLS(메일 + 내 경험, 2026-11-18) 하나만 본다. 늘리면 그쪽만 고친다.
- seal/open의 data 모양({"notice", "sealed"|"content", "truncated"})을 바꾸면 이미 저장된 기록을 못 풀 수 있다.
  옛 모양도 그대로 돌려주게 두었다(봉하지 않은 결과는 opened_content가 손대지 않는다).
- 한도(MAX_TASKS·MAX_CHARS)를 늘리면 TASK_MAX_TOKENS 상한과 하루 예산에 닿는 task가 늘어난다(위 계산 참고).
- 테스트: tests/test_history.py(자르기·저장 규칙), tests/test_payload_crypto.py(메일 봉하기·풀기).
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
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
from pydantic_core import to_jsonable_python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.results import EXTERNAL_NOTICE, SEALED_TOOLS, ToolResult, clip_mail_output
from app.models import Task
from app.models.enums import TaskStatus
from app.payload_crypto import open_json, seal_json

# 이어받지 않는 task 상태: 기록이 아직 자라는 중이거나 결과 없는 도구 호출로 끝나 있다(load_conversation_history 참고).
# 이 목록에 없는 cancel_requested·reconciling 같은 상태의 기록은 finalize_for_storage로 짝을 맞춰 넣는다.
UNFINISHED_STATUSES = (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
# 이어받을 task 수·글자 수 한도. 근거는 파일 머리 "자르는 규칙"(DECISIONS 2026-11-16).
MAX_TASKS = 10
MAX_CHARS = 40_000
# 아래 세 문장은 모델에게 보이는 기록 속 안내다(사용자 화면이 아니다). 모델이 "실제로 한 일"을 오해하지 않게 한다.
INTERRUPTED_NOTE = "(이 작업은 답변을 마치기 전에 중단되었다. 위의 도구 호출 결과까지만 실제로 일어난 일이다.)"
DROPPED_MAIL_NOTE = "(메일 내용은 저장하지 않았다. 다시 봐야 하면 read_mail로 다시 읽는다.)"
UNREADABLE_MAIL_NOTE = "(저장된 메일 내용을 풀 수 없다. 다시 봐야 하면 read_mail로 다시 읽는다.)"


def dump_messages(messages: Sequence[ModelMessage]) -> list[Any]:
    """메시지 목록을 JSONB에 넣을 수 있는 값으로 바꾼다(tasks.message_history 저장, 글자 수 계산)."""
    return ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")


def load_messages(data: list[Any]) -> list[ModelMessage]:
    """저장된 JSON을 메시지 객체로 되돌린다. 모양이 틀리면 pydantic ValidationError(잡지 않는다)."""
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
    """저장·이어받기용으로 끝을 정리한다. finished=False이고 요청으로 끝나면 INTERRUPTED_NOTE 응답을 붙인다.

    붙이는 이유: 다음 task의 모델이 그 작업이 중단됐고 위 도구 결과까지만 실제로 일어났다는 것을 알게 한다.
    load_conversation_history는 끝난 task도 finished=False로 부른다(답변으로 끝난 기록에는 아무것도 붙지 않는다).
    """
    cleaned = without_unfinished_tail(messages)
    if cleaned and not finished and isinstance(cleaned[-1], ModelRequest):
        cleaned.append(ModelResponse(parts=[TextPart(INTERRUPTED_NOTE)]))
    return cleaned


def without_interrupted(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """state='interrupted'(도중에 끊긴 응답)를 뺀다. state 속성이 없는 메시지는 완료로 본다."""
    return [m for m in messages if getattr(m, "state", "complete") != "interrupted"]


def history_for_storage(
    previous: Sequence[ModelMessage], new: Sequence[ModelMessage], *, status: TaskStatus, cipher: Fernet | None
) -> list[ModelMessage]:
    """task.message_history에 저장할 기록. 재개한 run이면 previous(이 task가 이미 저장한 기록)에 이어 붙인다.

    승인 대기(waiting_approval)일 때는 결과 없는 도구 호출을 지우지 않는다. 재개할 때 그 호출에 승인·거절 결과를
    붙여야 하기 때문이다. 다른 상태는 finalize_for_storage 규칙을 따른다.
    cipher: PAYLOAD_ENCRYPTION_KEY. 메일 도구 결과를 암호화한다(없으면 메일 내용을 비운다).
    """
    combined = seal_mail_returns([*previous, *without_interrupted(new)], cipher)
    if status == TaskStatus.WAITING_APPROVAL:
        return combined
    return finalize_for_storage(combined, finished=status == TaskStatus.DONE)


def map_mail_returns(messages: Sequence[ModelMessage], change: Callable[[ToolReturnPart], Any]) -> list[ModelMessage]:
    """메일 도구(list_mail·read_mail)와 내 경험(list_experiences, 2026-11-18) 결과 부분만 change로 바꾼다.

    대상 목록은 results.SEALED_TOOLS. 호출과 결과의 짝(tool_call_id)은 그대로.
    """

    def part_of(part: Any) -> Any:
        if isinstance(part, ToolReturnPart) and part.tool_name in SEALED_TOOLS:
            return replace(part, content=change(part))
        return part

    return [replace(m, parts=[part_of(p) for p in m.parts]) if isinstance(m, ModelRequest) else m for m in messages]


def seal_mail_returns(messages: Sequence[ModelMessage], cipher: Fernet | None) -> list[ModelMessage]:
    """7단계 보강: 저장하는 기록의 메일 결과는 앞 500자(results.clip_mail_output)를 암호화해 둔다.

    data는 {"notice", "sealed": 암호문, "truncated"}가 된다. notice가 그대로라 remember 검사(외부 콘텐츠)는 계속 걸린다.
    이미 봉한 결과(재개 run의 previous)는 다시 봉하지 않는다. 키가 없으면 내용을 비운다(평문 보관 금지).
    """
    return map_mail_returns(messages, lambda part: sealed_content(part, cipher))


def sealed_content(part: ToolReturnPart, cipher: Fernet | None) -> Any:
    """메일 결과 하나를 봉한 모양으로 바꾼다. ToolResult가 아닌 값은 성공 결과로 감싼 뒤 다룬다.

    cipher가 None이면 내용을 DROPPED_MAIL_NOTE로 바꾼다: 키 없이 평문 메일을 DB에 남기는 사고를 막는다.
    """
    content = to_jsonable_python(part.content)
    if not isinstance(content, dict):
        content = ToolResult.success(content).model_dump(mode="json")
    data = content.get("data")
    if not isinstance(data, dict) or "content" not in data:
        return content  # 실패 결과(내용 없음) 또는 이미 봉한 결과
    clipped = clip_mail_output(part.tool_name, content)["data"]
    if cipher is None:
        return {**content, "data": {"notice": EXTERNAL_NOTICE, "content": DROPPED_MAIL_NOTE, "truncated": True}}
    sealed = seal_json(cipher, clipped["content"])
    return {**content, "data": {"notice": EXTERNAL_NOTICE, "sealed": sealed, "truncated": clipped["truncated"]}}


def open_mail_returns(messages: Sequence[ModelMessage], cipher: Fernet | None) -> list[ModelMessage]:
    """모델에게 넘기기 직전(runner.execute_agent, DBOS 단계 밖)에 봉한 메일 결과를 푼다.

    DBOS prepare 단계의 결과(이전 기록)에는 암호문만 남고, 평문은 이 run의 메모리에서만 쓴다.
    풀지 못하면(키가 바뀜·없음) 내용 대신 "다시 읽어야 한다"는 안내를 넣는다.
    """
    return map_mail_returns(messages, lambda part: opened_content(part, cipher))


def opened_content(part: ToolReturnPart, cipher: Fernet | None) -> Any:
    """봉한 메일 결과 하나를 푼다. sealed가 없는 결과(실패, 옛 모양, 이미 비운 것)는 그대로 돌려준다.

    풀지 못해도 예외를 내지 않는다: 키가 바뀌었다고 대화 전체가 멈추면 안 되고, 모델은 다시 읽으면 된다.
    """
    content = part.content
    if not isinstance(content, dict):
        return content
    data = content.get("data")
    if not isinstance(data, dict) or "sealed" not in data:
        return content
    try:
        if cipher is None:
            raise InvalidToken
        text = open_json(cipher, str(data["sealed"]))
    except (InvalidToken, ValueError):
        text = UNREADABLE_MAIL_NOTE
    return {**content, "data": {"notice": EXTERNAL_NOTICE, "content": text, "truncated": data.get("truncated", True)}}


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
    """task별 기록(오래된 것부터)을 받아 최근 것부터 한도 안에서 고르고, 다시 시간 순서로 이어 붙인다.

    한도를 넘는 task를 만나면 거기서 멈춘다. 그보다 오래된 task는 작아도 넣지 않아,
    고른 기록은 항상 최근 쪽으로 이어진다.
    글자 수는 ensure_ascii=False JSON 기준이라 한국어 한 글자가 1로 센다.
    """
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
    대화 소유자 확인은 부른 쪽(runner가 task에서 읽은 conversation_id)이 한다. 읽기만 하고 커밋하지 않는다.
    메일 결과는 봉한 채로 돌려준다(푸는 곳은 runner.execute_agent의 open_mail_returns뿐).
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
