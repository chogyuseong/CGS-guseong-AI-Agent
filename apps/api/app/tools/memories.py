"""기억 도구 (5단계): remember, forget = INTERNAL / list_memories = READ (toolsets.py에서 정한다).

절대 원칙 9 "기억은 명시적 요청과 실제 출처에서만 만든다"를 서버 검사로 지킨다(5단계 보강, BUILD_SPEC 5.4).
remember(content, quote)는 아래를 모두 통과해야 저장한다. 걸리면 failed와 그 이유(모델이 사용자에게 확인한다).

    1. 이 task가 내 task이고 origin이 chat이다(DB의 tasks.origin을 본다. 예약·감시에는 사용자 문장이 없다).
    2. task를 시작한 사용자 메시지(trigger_message_id)가 있다. 출처(source_message_id)는 항상 이 메시지다.
    3. 그 메시지에 기억 요청(기억/잊지 마/외워)이 있다(NO_REQUEST).
    4. quote(사용자 말을 그대로 옮긴 부분)가 그 메시지 안에 실제로 있다(QUOTE_NOT_FOUND).
       검색 결과·메일·메모·이전 대화의 글은 이 메시지에 없으므로 여기서 걸린다.
    5. quote가 따옴표 안(남의 말 인용)이거나 가정(만약, ~라면)이면 저장하지 않고 확인을 요청한다(NEEDS_CONFIRMATION).
    6. quote가 남의 정보(엄마는, 친구가)면 content에도 그 관계 말이 있어야 한다(OTHERS_INFO). 남의 사실을 사용자
       본인 사실로 바꿔 적지 않게 한다.
    7. content는 quote에서 나온 말이다: quote에 없는 숫자가 없고, 글자(공백·문장부호 제외)의 70% 이상이 quote에 있다
       (NOT_DERIVED). 정규화하면서 새 사실을 덧붙이지 않게 한다.

"외부 도구를 먼저 썼으면 저장 금지"는 쓰지 않는다(BUILD_SPEC 5.4가 금지). 4가 외부 글을 막는다.

저장 규칙(명세: 한 줄 300자, 활성 200개):
- 공백·줄바꿈·제어 문자를 공백 하나로 바꿔 한 줄로 저장한다. source_quote도 함께 저장해 기억 화면에 보여 준다.
- 이미 같은 내용(공백·대소문자 무시)이 있으면 새로 만들지 않고 그 기억을 돌려준다(already_saved=true).
- 200개가 차 있으면 거부한다(오래된 것을 몰래 지우지 않는다. 사용자가 기억 화면에서 지운다).
- 비밀번호·PIN·카드 번호·주민등록번호처럼 보이는 것은 저장하지 않는다.
"""

import re
from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.models import Memory, Message, Task
from app.models.enums import MessageRole, TaskOrigin
from app.tools.notes import short

MAX_CONTENT_LENGTH = 300
MAX_MEMORIES = 200
ONLY_USER_WORDS = "사용자가 직접 말한 내용만 기억할 수 있다."
NO_REQUEST = "사용자가 이번 메시지에서 기억해 달라고 하지 않았다. 기억할지 사용자에게 먼저 물어라."
QUOTE_NOT_FOUND = "quote가 사용자 메시지에 그대로 있지 않다. 사용자가 이번 메시지에서 한 말만 그대로 옮겨라."
NEEDS_CONFIRMATION = "인용이나 가정으로 보여 저장하지 않았다. 사용자 본인의 사실인지 확인을 요청하라."
OTHERS_INFO = "다른 사람에 대한 정보다. 누구의 정보인지 content에 그대로 남겨라(사용자 본인 정보로 바꾸지 않는다)."
NOT_DERIVED = "content에 quote에 없는 내용이 있다. 사용자가 한 말에 없는 사실을 덧붙이지 말라."
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
MemoryQuote = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_CONTENT_LENGTH,
        description="이 기억의 근거가 된 사용자 메시지 부분을 그대로 옮긴 것(바꾸지 않는다). 예: 나 매운 거 못 먹어",
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
MEMORY_REQUEST = re.compile(r"기억|잊지\s*마|외워")
HYPOTHETICAL = re.compile(r"만약|가정|예를\s*들어|[라다]면\b|[라다]면\s")
QUOTE_PAIRS = {"“": "”", "‘": "’", "「": "」", "『": "』"}
STRAIGHT_QUOTES = "\"'"
RELATION_WORDS = (
    "어머니",
    "아버지",
    "엄마",
    "아빠",
    "부모님",
    "남편",
    "아내",
    "와이프",
    "남자친구",
    "여자친구",
    "남친",
    "여친",
    "친구",
    "동생",
    "누나",
    "언니",
    "오빠",
    "형",
    "아들",
    "딸",
    "동료",
    "팀장",
    "상사",
    "선배",
    "후배",
    "할머니",
    "할아버지",
)
PARTICLES = "은|는|이|가|도|의|께서|께|한테|에게|랑|하고"
RELATION = re.compile(rf"(?<![가-힣])({'|'.join(RELATION_WORDS)})(?:{PARTICLES})?(?![가-힣])")
DERIVED_RATIO = 0.7


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


def inside_quotes(source: str, start: int) -> bool:
    """source[start]가 따옴표 안에 있는가(곧은따옴표는 앞에 홀수 개, 짝 따옴표는 열린 것이 더 많으면)."""
    before = source[:start]
    if any(before.count(mark) % 2 == 1 for mark in STRAIGHT_QUOTES):
        return True
    return any(before.count(open_) > before.count(close) for open_, close in QUOTE_PAIRS.items())


def derived_from(content: str, quote: str) -> bool:
    """content가 quote에서 나온 말인가: 새 숫자가 없고, 글자의 70% 이상이 quote에 있다."""
    if any(ch.isdigit() and ch not in quote for ch in content):
        return False
    letters = [ch for ch in content if ch.isalnum()]
    if not letters:
        return False
    return sum(ch in quote for ch in letters) / len(letters) >= DERIVED_RATIO


def quote_problem(source: str, quote: str, content: str) -> str | None:
    """기억 요청·원문 인용·인용/가정·남의 정보·새 사실 검사(BUILD_SPEC 5.4). 저장해도 되면 None."""
    source_line, quote_line = normalize(source), normalize(quote)
    if not quote_line or quote_line not in source_line:
        return QUOTE_NOT_FOUND
    if not MEMORY_REQUEST.search(source_line):
        return NO_REQUEST
    start = source_line.index(quote_line)
    marks = STRAIGHT_QUOTES + "".join(QUOTE_PAIRS) + "".join(QUOTE_PAIRS.values())
    if inside_quotes(source_line, start) or any(m in quote_line for m in marks) or HYPOTHETICAL.search(quote_line):
        return NEEDS_CONFIRMATION
    relation = RELATION.search(quote_line)
    if relation is not None and relation.group(1) not in content:
        return OTHERS_INFO
    if not derived_from(content, quote_line):
        return NOT_DERIVED
    return None


def memory_view(memory: Memory) -> dict[str, Any]:
    return {"id": memory.id, "content": memory.content, "saved_on": memory.created_at.date().isoformat()}


# ---- DB 작업 ----


async def chat_trigger_message(session: AsyncSession, deps: AgentDeps) -> Message | None:
    """내 chat task를 시작한 사용자 메시지. 조건에 맞지 않으면 None."""
    task = await session.scalar(select(Task).where(Task.id == deps.task_id, Task.user_id == deps.user_id))
    if task is None or task.origin != TaskOrigin.CHAT or deps.origin != TaskOrigin.CHAT:
        return None
    if task.trigger_message_id is None:
        return None
    message = await session.get(Message, task.trigger_message_id)
    if message is None or message.role != MessageRole.USER:
        return None
    return message


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


async def save_memory(session: AsyncSession, deps: AgentDeps, content: str, quote: str) -> ToolResult:
    """서버 검사(origin, 출처 메시지, 기억 요청, 원문 인용, 새 사실 없음)를 거쳐 저장한다."""
    source = await chat_trigger_message(session, deps)
    if source is None:
        return ToolResult.failure(ONLY_USER_WORDS)
    if problem := content_problem(content):
        return ToolResult.failure(problem)
    if problem := quote_problem(source.content, quote, content):
        return ToolResult.failure(f"{ONLY_USER_WORDS} {problem}", error_code="memory_not_supported")
    existing = await select_memories(session, deps.user_id, MAX_MEMORIES)
    duplicate = next((m for m in existing if same_text(m.content, content)), None)
    if duplicate is not None:
        return ToolResult.success({**memory_view(duplicate), "already_saved": True})
    if await count_memories(session, deps.user_id) >= MAX_MEMORIES:
        return ToolResult.failure(LIMIT_REFUSAL)
    memory = Memory(user_id=deps.user_id, content=content, source_message_id=source.id, source_quote=normalize(quote))
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return ToolResult.success({**memory_view(memory), "already_saved": False, "saved_to": "앱 기억"})


async def delete_memory(session: AsyncSession, user_id: int, memory_id: int) -> bool:
    result = await session.execute(delete(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
    await session.commit()
    return bool(getattr(result, "rowcount", 0))


# ---- 도구 (모델이 부르는 함수) ----


async def remember(ctx: RunContext[AgentDeps], content: MemoryContent, quote: MemoryQuote) -> ToolResult:
    """사용자가 이번 메시지에서 기억해 달라고 한 사실이나 선호를 한 줄로 기억한다.
    quote에는 근거가 된 사용자 메시지 부분을 그대로 옮긴다. content는 그 말을 짧게 정리하되 새 사실을 덧붙이지 않는다.
    다른 사람에 대한 정보면 누구의 정보인지 남긴다. 인용·가정·추측은 저장하지 않는다.
    비밀번호·카드 번호는 저장하지 않는다."""
    async with ctx.deps.db() as session:
        return await save_memory(session, ctx.deps, normalize(content), quote)


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
