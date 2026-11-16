"""기억 도구 (5단계): remember, forget = INTERNAL / list_memories = READ (toolsets.py에서 정한다).

절대 원칙 6 "기억은 사용자가 직접 말한 문장에서만 만든다"를 프롬프트가 아니라 서버 검사로 지킨다.
remember는 아래를 모두 통과해야 저장한다. 하나라도 걸리면 ok=false와 '사용자가 직접 말한 내용만 기억할 수 있다'.

    1. 이 task가 내 task이고 origin이 chat이다(DB의 tasks.origin을 본다. 예약·감시에는 사용자 문장이 없다).
    2. task를 시작한 사용자 메시지(trigger_message_id)가 있다. 기억의 출처(source_message_id)는 항상 이 메시지다.
    3. 모델의 지금 문맥(ctx.messages)에 외부 콘텐츠 도구의 호출이나 결과가 없다.
       ctx.messages에는 이번 run의 메시지뿐 아니라 이어받은 기록(같은 대화의 이전 task들)과, 승인 재개 run이면
       같은 task의 앞선 run 기록도 들어 있다. 외부 글이 문맥에 남아 있는 동안은 무엇이 사용자 말이고 무엇이
       남의 글인지 서버가 구분할 수 없으므로 기억을 만들지 않는다.
       외부 콘텐츠 = EXTERNAL_CONTENT_TOOLS의 이름이거나, 결과가 ToolResult.external로 감싸져 있는 것.
       호출만 있어도(결과가 실패여도, 같은 응답에서 remember보다 먼저 불렀어도) 거부한다.
    4. DB의 steps에도 이 task가 외부 콘텐츠 도구를 부른 기록이 없다(명세 문장 그대로의 검사. 3과 겹치지만
       메시지 기록이 어떤 이유로 잘려도 이 task의 기록은 DB에 남아 있다).

저장 규칙(명세: 한 줄 300자, 활성 200개, source_message_id = trigger_message_id):
- 공백·줄바꿈·제어 문자를 공백 하나로 바꿔 한 줄로 저장한다(지시문에 "- 내용" 한 줄씩 들어간다).
- 이미 같은 내용(공백·대소문자 무시)이 있으면 새로 만들지 않고 그 기억을 돌려준다(already_saved=true).
- 200개가 차 있으면 거부한다(오래된 것을 몰래 지우지 않는다. 사용자가 기억 화면에서 지운다).
- 비밀번호·PIN·카드 번호·주민등록번호처럼 보이는 것은 저장하지 않는다(절대 원칙 2·8의 정신).
"""

import re
from collections.abc import Callable, Sequence
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, RetryPromptPart, ToolCallPart, ToolReturnPart
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.agent.results import EXTERNAL_CONTENT_TOOLS, ToolResult, is_wrapped_external
from app.models import Memory, Step, Task
from app.models.enums import TaskOrigin
from app.tools.notes import short

MAX_CONTENT_LENGTH = 300
MAX_MEMORIES = 200
ONLY_USER_WORDS = "사용자가 직접 말한 내용만 기억할 수 있다."
EXTERNAL_REASON = (
    "이 대화에서 검색 결과·일정·날씨·장소 같은 외부 내용을 읽은 뒤라 저장하지 않았다. "
    "새 대화에서 다시 말해 달라고 안내하라."
)
SENSITIVE_REFUSAL = "비밀번호, PIN, 카드 번호, 주민등록번호 같은 정보는 기억에 저장하지 않는다."
LIMIT_REFUSAL = (
    f"기억은 {MAX_MEMORIES}개까지 저장한다. 기억 화면에서 필요 없는 기억을 지운 뒤 다시 요청해 달라고 안내하라."
)
EMPTY_REFUSAL = "기억할 내용이 비어 있다."

MemoryContent = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_CONTENT_LENGTH,
        description=(
            "기억할 한 줄(300자 이하). 사용자가 이번 메시지에서 한 말의 단어를 그대로 쓴다. 예: 매운 거 못 먹음"
        ),
    ),
]
MemoryLimit = Annotated[int, Field(ge=1, le=MAX_MEMORIES, description="가져올 최대 개수")]

CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
SECRET_WORDS = re.compile(
    r"비밀\s*번호|비번|패스\s*워드|password|passwd|passcode|핀\s*번호|\bpin\b|cvc|cvv|보안\s*카드|\botp\b",
    re.IGNORECASE,
)
# 하이픈·공백을 끼워도 13자리 이상인 숫자(카드 번호 13~19자리, 주민등록번호 13자리).
# 전화번호(10~11자리)는 걸리지 않는다.
LONG_NUMBER = re.compile(r"\d(?:[\s-]?\d){12,}")


def normalize(content: str) -> str:
    """제어 문자·줄바꿈·여러 칸 공백을 공백 하나로 바꾼 한 줄."""
    return " ".join(CONTROL_CHARS.sub(" ", content).split())


def content_problem(content: str) -> str | None:
    if not content:
        return EMPTY_REFUSAL
    if SECRET_WORDS.search(content) or LONG_NUMBER.search(content):
        return SENSITIVE_REFUSAL
    return None


def same_text(a: str, b: str) -> bool:
    return normalize(a).casefold() == normalize(b).casefold()


def memory_view(memory: Memory) -> dict[str, Any]:
    return {"id": memory.id, "content": memory.content, "saved_on": memory.created_at.date().isoformat()}


# ---- 외부 콘텐츠 검사 ----


def is_external_part(part: object) -> bool:
    if isinstance(part, ToolCallPart | ToolReturnPart | RetryPromptPart) and part.tool_name in EXTERNAL_CONTENT_TOOLS:
        return True
    return isinstance(part, ToolReturnPart) and is_wrapped_external(part.content)


def context_has_external_content(messages: Sequence[ModelMessage]) -> bool:
    """모델 문맥(이어받은 기록 + 이번 run)에 외부 콘텐츠 도구의 호출이나 결과가 있는가."""
    return any(is_external_part(part) for message in messages for part in message.parts)


async def task_used_external_tool(session: AsyncSession, task_id: int) -> bool:
    found = await session.scalar(
        select(Step.id).where(Step.task_id == task_id, Step.tool_name.in_(EXTERNAL_CONTENT_TOOLS)).limit(1)
    )
    return found is not None


# ---- DB 작업 ----


async def chat_trigger_message_id(session: AsyncSession, deps: AgentDeps) -> int | None:
    """내 chat task를 시작한 사용자 메시지 id. 조건에 맞지 않으면 None."""
    task = await session.scalar(select(Task).where(Task.id == deps.task_id, Task.user_id == deps.user_id))
    if task is None or task.origin != TaskOrigin.CHAT or deps.origin != TaskOrigin.CHAT:
        return None
    return task.trigger_message_id


async def select_memories(session: AsyncSession, user_id: int, limit: int) -> list[Memory]:
    """최근 것부터 limit개."""
    rows = await session.scalars(
        select(Memory)
        .where(Memory.user_id == user_id)
        .order_by(Memory.created_at.desc(), Memory.id.desc())
        .limit(limit)
    )
    return list(rows)


async def count_memories(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(select(func.count()).select_from(Memory).where(Memory.user_id == user_id)) or 0


async def save_memory(session: AsyncSession, deps: AgentDeps, content: str) -> ToolResult:
    """서버 검사(origin, 출처 메시지, 이 task의 외부 콘텐츠 도구)를 거쳐 저장한다."""
    source_message_id = await chat_trigger_message_id(session, deps)
    if source_message_id is None:
        return ToolResult.failure(ONLY_USER_WORDS)
    if await task_used_external_tool(session, deps.task_id):
        return ToolResult.failure(f"{ONLY_USER_WORDS} {EXTERNAL_REASON}")
    if problem := content_problem(content):
        return ToolResult.failure(problem)
    existing = await select_memories(session, deps.user_id, MAX_MEMORIES)
    duplicate = next((m for m in existing if same_text(m.content, content)), None)
    if duplicate is not None:
        return ToolResult.success({**memory_view(duplicate), "already_saved": True})
    if await count_memories(session, deps.user_id) >= MAX_MEMORIES:
        return ToolResult.failure(LIMIT_REFUSAL)
    memory = Memory(user_id=deps.user_id, content=content, source_message_id=source_message_id)
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return ToolResult.success({**memory_view(memory), "already_saved": False, "saved_to": "앱 기억"})


async def delete_memory(session: AsyncSession, user_id: int, memory_id: int) -> bool:
    result = await session.execute(delete(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
    await session.commit()
    return bool(getattr(result, "rowcount", 0))


# ---- 도구 (모델이 부르는 함수) ----


async def remember(ctx: RunContext[AgentDeps], content: MemoryContent) -> ToolResult:
    """사용자가 이번 메시지에서 직접 말한 사실이나 선호를 한 줄로 기억한다.
    추측하거나 여러 정보를 종합한 내용은 저장하지 않는다. 건강·금융·가족 정보는 사용자가 '기억해'라고 했을 때만
    저장한다. 비밀번호·카드 번호는 저장하지 않는다."""
    if context_has_external_content(ctx.messages):
        return ToolResult.failure(f"{ONLY_USER_WORDS} {EXTERNAL_REASON}")
    async with ctx.deps.db() as session:
        return await save_memory(session, ctx.deps, normalize(content))


async def forget(ctx: RunContext[AgentDeps], memory_id: int) -> ToolResult:
    """저장된 기억 하나를 지운다. memory_id는 list_memories 결과의 id를 쓴다."""
    async with ctx.deps.db() as session:
        deleted = await delete_memory(session, ctx.deps.user_id, memory_id)
    if not deleted:
        return ToolResult.failure(f"id가 {memory_id}인 기억을 찾을 수 없습니다. list_memories로 id를 확인하세요.")
    return ToolResult.success({"deleted_id": memory_id})


async def list_memories(ctx: RunContext[AgentDeps], limit: MemoryLimit = 50) -> ToolResult:
    """저장된 기억을 id와 함께 최근 것부터 보여 준다."""
    async with ctx.deps.db() as session:
        rows = await select_memories(session, ctx.deps.user_id, limit)
    return ToolResult.success({"memories": [memory_view(m) for m in rows]})


# ---- 타임라인 문구 (step.label) ----

LABELS: dict[str, Callable[[dict[str, Any]], str]] = {
    "remember": lambda args: f"기억에 '{short(args.get('content', ''))}' 저장하는 중",
    "forget": lambda args: f"기억 #{args.get('memory_id', '?')} 지우는 중",
    "list_memories": lambda args: "기억 목록 확인하는 중",
}
