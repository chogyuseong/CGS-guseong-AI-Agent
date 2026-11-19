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

진입점: chat task의 모델(toolsets.internal_tools의 remember·forget, read_tools의 list_memories).
이 파일의 함수를 다른 곳도 쓴다: app/routers/memories.py(기억 화면 API: MAX_MEMORIES, delete_memory),
app/agent/prompts.py(지시문의 최근 30줄: select_memories).
근거: BUILD_SPEC 5.4(기억 검사)·5.5(삭제 안내), docs/SAFETY.md "기억 오염 방어", docs/DECISIONS.md "5단계"·"5단계 보강".

변경 이력:
- 2026-11-17 5단계: remember·forget·list_memories. 처음에는 외부 콘텐츠 도구를 쓴 작업이면 저장을 막았다
  (MEMORY_BLOCKING_TOOLS).
- 2026-11-17 5단계 보강: 그 규칙을 없애고(BUILD_SPEC 5.4가 금지) quote 원문 인용 검사(위 3~7)로 바꿨다.
  source_quote를 저장한다. 보강 전 기억은 source_quote가 비어 있다.

고칠 때 주의:
- 거부 문구 상수는 tests/test_memory_quotes.py·tests/test_memories.py가 상수 이름으로 비교한다. 문구만 바꾸면 테스트는
  그대로지만, 규칙을 바꾸면 docs/SAFETY.md의 표와 DECISIONS 5단계 보강 줄도 같이 고친다.
- MAX_MEMORIES는 기억 화면 API 응답의 limit으로도 나간다(apps/web/components/MemoryList.tsx가 개수 요약에 쓴다).
- MAX_CONTENT_LENGTH는 Field 제약과 모델에게 보이는 Field description("300자 이하") 두 곳에 있다.
- 관계 말 목록·70% 비율은 휴리스틱이다. 틀릴 때 "저장 안 하고 다시 묻기"가 되게 고른 값이므로, 느슨하게 바꾸면
  외부 글이 기억이 되는 쪽으로 틀린다. 바꾸면 tests/test_memory_quotes.py에 사례를 더한다.
- 도구를 더하면 toolsets 등록 목록, 아래 LABELS, data_policy.TOOL_POLICIES(tests/test_data_policy.py)를 함께 고친다.
"""

import re
from collections.abc import Callable
from datetime import datetime
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

# 명세 값: 기억 한 줄 300자, 활성 기억 200개.
MAX_CONTENT_LENGTH = 300
MAX_MEMORIES = 200
# 거부 문구는 도구 결과로 모델에게 간다. 무엇을 고쳐 다시 하거나 사용자에게 물을지 지시하는 문장으로 쓴다.
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

# 기억 한 줄이 지시문 안에서 여러 줄(가짜 시스템 문장)인 척하지 못하게 줄바꿈·제어 문자를 공백으로 바꾼다(SAFETY.md).
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# 비밀 정보 낱말. 기억은 이후 모든 대화의 지시문에 들어가 모델 제공자에게 매번 전송되므로 비밀을 담지 않는다.
SECRET_WORDS = re.compile(
    r"비밀\s*번호|비번|패스\s*워드|password|passwd|passcode|핀\s*번호|\bpin\b|cvc|cvv|보안\s*카드|\botp\b",
    re.IGNORECASE,
)
# 하이픈·공백을 끼워도 13자리 이상인 숫자(카드 번호 13~19자리, 주민등록번호 13자리).
# 전화번호(10~11자리)는 걸리지 않는다.
LONG_NUMBER = re.compile(r"\d(?:[\s-]?\d){12,}")
# 기억 요청 낱말(규칙 3). 대화 중 지나가듯 한 말을 모델이 마음대로 저장하는 일을 막는다.
MEMORY_REQUEST = re.compile(r"기억|잊지\s*마|외워")
# 가정 표현(규칙 5). "만약 내가 채식주의자라면"을 "채식주의자"로 저장하는 일을 막는다.
HYPOTHETICAL = re.compile(r"만약|가정|예를\s*들어|[라다]면\b|[라다]면\s")
# 따옴표(규칙 5). 따옴표 안은 남의 말 인용으로 본다.
QUOTE_PAIRS = {"“": "”", "‘": "’", "「": "」", "『": "』"}
STRAIGHT_QUOTES = "\"'"
# 관계 말(규칙 6). 이 말이 quote에 있으면 남의 정보다. 뒤에 조사(PARTICLES)가 붙어도 잡고, 그 밖의 한글이 앞뒤에
# 붙으면(예: '형태'의 '형') 다른 낱말로 보고 넘어간다(tests/test_memory_quotes.py의 "이 형태가 편해" 사례).
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
# 규칙 7의 글자 비율. 한국어 활용("못 먹어" → "못 먹음") 때문에 낱말 비교 대신 글자 비교를 쓰고, 활용으로 바뀌는
# 글자 정도만 허용하도록 정한 휴리스틱 값이다(DECISIONS 5단계 보강 2026-11-17).
DERIVED_RATIO = 0.7


def normalize(content: str) -> str:
    """제어 문자·줄바꿈·여러 칸 공백을 공백 하나로 바꾼 한 줄.

    저장(content, source_quote)과 비교(원문 인용, 중복) 모두 이 함수를 거친다. 그래서 quote의 공백·줄바꿈 차이는
    원문과 같은 글로 본다(SAFETY.md "공백 차이만 허용").
    """
    return " ".join(CONTROL_CHARS.sub(" ", content).split())


def content_problem(content: str) -> str | None:
    """빈 내용·민감 정보면 거부 문구, 아니면 None. 원문 인용 검사보다 먼저 본다.

    사용자가 직접 "비밀번호 기억해"라고 해도 거부한다. 기억은 매 요청 지시문에 실려 모델 제공자로 가기 때문이다
    (절대 원칙 11). 13자리 이상 숫자는 카드·주민등록번호로 보고, 전화번호(10~11자리)는 통과한다(LONG_NUMBER).
    """
    if not content:
        return EMPTY_REFUSAL
    if SECRET_WORDS.search(content) or LONG_NUMBER.search(content):
        return SENSITIVE_REFUSAL
    return None


def same_text(a: str, b: str) -> bool:
    """중복 판정: 공백 정리 후 대소문자를 무시하고 같으면 같은 기억이다(같은 말을 두 번 저장하지 않게)."""
    return normalize(a).casefold() == normalize(b).casefold()


def inside_quotes(source: str, start: int) -> bool:
    """source[start]가 따옴표 안에 있는가(곧은따옴표는 앞에 홀수 개, 짝 따옴표는 열린 것이 더 많으면).

    사용자가 남의 말을 옮긴 부분(예: 친구가 "나 고양이 키워"라고 했어, 기억해)을 사용자 본인 사실로 저장하지 않게 한다.
    따옴표 개수만 세는 근사라서 짝이 안 맞는 글에서는 안쪽으로 볼 수 있다. 그때는 저장하지 않고 확인을 묻는 쪽이다.
    """
    before = source[:start]
    if any(before.count(mark) % 2 == 1 for mark in STRAIGHT_QUOTES):
        return True
    return any(before.count(open_) > before.count(close) for open_, close in QUOTE_PAIRS.items())


def derived_from(content: str, quote: str) -> bool:
    """content가 quote에서 나온 말인가: 새 숫자가 없고, 글자의 70% 이상이 quote에 있다.

    모델이 정리하면서 사실을 덧붙이는 일(예: "나 매운 거 못 먹어" → "매운 거 못 먹음, 땅콩 알레르기")을 막는다.
    숫자는 하나만 달라도 뜻이 바뀌므로(날짜·금액) 비율과 따로 엄격하게 본다. 글자·숫자가 하나도 없으면 False.
    """
    if any(ch.isdigit() and ch not in quote for ch in content):
        return False
    letters = [ch for ch in content if ch.isalnum()]
    if not letters:
        return False
    return sum(ch in quote for ch in letters) / len(letters) >= DERIVED_RATIO


def quote_problem(source: str, quote: str, content: str) -> str | None:
    """기억 요청·원문 인용·인용/가정·남의 정보·새 사실 검사(BUILD_SPEC 5.4). 저장해도 되면 None.

    source는 task를 시작한 사용자 메시지 원문(chat_trigger_message)이다. 모델이 고를 수 없는 값이라 이 검사가
    프롬프트 부탁이 아니라 서버 강제가 된다. 순수 함수(DB·외부 호출 없음)라 tests/test_memory_quotes.py가 직접 부른다.
    검사 순서가 거부 문구를 정한다(인용 없음이 요청 없음보다 먼저).
    """
    source_line, quote_line = normalize(source), normalize(quote)
    # 원문 인용: 검색 결과·메일·메모·이전 대화에 숨은 "이걸 기억해라" 문장은 이번 사용자 메시지에 없어서 여기서 걸린다.
    if not quote_line or quote_line not in source_line:
        return QUOTE_NOT_FOUND
    # 기억 요청: 사용자가 기억해 달라고 하지 않은 말을 모델이 알아서 저장하는 일을 막는다.
    if not MEMORY_REQUEST.search(source_line):
        return NO_REQUEST
    start = source_line.index(quote_line)
    marks = STRAIGHT_QUOTES + "".join(QUOTE_PAIRS) + "".join(QUOTE_PAIRS.values())
    # 인용·가정: 남의 말이나 "만약 ~라면"을 사실로 저장하는 일을 막는다. 거부가 아니라 사용자 확인 요청이다.
    if inside_quotes(source_line, start) or any(m in quote_line for m in marks) or HYPOTHETICAL.search(quote_line):
        return NEEDS_CONFIRMATION
    # 남의 정보: "엄마는 땅콩 알레르기"를 content에서 "땅콩 알레르기"(사용자 본인)로 바꿔 적는 일을 막는다.
    relation = RELATION.search(quote_line)
    if relation is not None and relation.group(1) not in content:
        return OTHERS_INFO
    # 새 사실 없음: 정리하면서 원문에 없는 내용을 덧붙이는 일을 막는다.
    if not derived_from(content, quote_line):
        return NOT_DERIVED
    return None


def memory_view(memory: Memory) -> dict[str, Any]:
    """모델에게 돌려줄 기억 한 건(id·내용·저장 날짜). source_quote는 기억 화면 API만 보여 준다."""
    return {"id": memory.id, "content": memory.content, "saved_on": memory.created_at.date().isoformat()}


# ---- DB 작업 ----


async def chat_trigger_message(session: AsyncSession, deps: AgentDeps) -> Message | None:
    """내 chat task를 시작한 사용자 메시지. 조건에 맞지 않으면 None(remember는 ONLY_USER_WORDS로 거부).

    출처는 모델 인자가 아니라 DB의 tasks.trigger_message_id로만 정한다(모델이 다른 메시지를 출처로 고를 수 없게).
    읽기만 한다.
    """
    task = await session.scalar(select(Task).where(Task.id == deps.task_id, Task.user_id == deps.user_id))
    # origin을 DB와 deps 두 곳에서 본다: 예약·감시 task에는 사용자 문장이 없고, 도구 묶음에서 빠졌더라도
    # 잘못 등록되거나 직접 불렸을 때 서버가 다시 막는다(SAFETY.md 표 "origin").
    if task is None or task.origin != TaskOrigin.CHAT or deps.origin != TaskOrigin.CHAT:
        return None
    if task.trigger_message_id is None:
        return None
    message = await session.get(Message, task.trigger_message_id)
    if message is None or message.role != MessageRole.USER:
        return None
    return message


async def select_memories(
    session: AsyncSession, user_id: int, limit: int, *, created_by: datetime | None = None
) -> list[Memory]:
    """최근 것부터 limit개(user_id 조건). 읽기만 한다.

    지시문의 최근 30줄(prompts.py PROMPT_MEMORY_LINES), list_memories, 중복 검사가 함께 쓴다. 정렬을 바꾸면
    지시문에 들어가는 기억이 바뀐다.
    created_by를 주면 그 시각까지 만든 기억만(작업 상세의 '함께 보낸 기억', 2026-11-19).
    """
    query = select(Memory).where(Memory.user_id == user_id)
    if created_by is not None:
        query = query.where(Memory.created_at <= created_by)
    rows = await session.scalars(query.order_by(Memory.created_at.desc(), Memory.id.desc()).limit(limit))
    return list(rows)


async def count_memories(session: AsyncSession, user_id: int) -> int:
    """내 기억 개수(MAX_MEMORIES 검사용). 읽기만 한다."""
    return await session.scalar(select(func.count()).select_from(Memory).where(Memory.user_id == user_id)) or 0


async def save_memory(session: AsyncSession, deps: AgentDeps, content: str, quote: str) -> ToolResult:
    """서버 검사(origin, 출처 메시지, 기억 요청, 원문 인용, 새 사실 없음)를 거쳐 저장한다.

    결과: 저장(already_saved=false), 같은 기억이 이미 있으면 그 기억(already_saved=true, 새로 쓰지 않음),
    걸리면 failed. 부수 효과: 통과했을 때만 Memory 한 행을 넣고 커밋한다(한 트랜잭션, 실패 경로는 쓰기 없음).
    content는 remember가 이미 normalize했고, quote는 여기서 정리해 source_quote로 남긴다(기억 화면의 근거 줄).
    한도는 중복 확인 뒤에 본다: 200개가 차 있어도 이미 있는 기억을 다시 말하면 거부가 아니라 "이미 있음"이다.
    """
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
    """내 기억 하나를 지우고 커밋한다. 지웠으면 True, 없거나 남의 기억이면 False(user_id 조건).

    forget 도구와 기억 화면 API(DELETE, app/routers/memories.py)가 함께 쓴다. 지시문은 요청마다 DB에서 다시 읽으므로
    다음 모델 호출부터 빠진다.
    """
    result = await session.execute(delete(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
    await session.commit()
    return bool(getattr(result, "rowcount", 0))


# ---- 도구 (모델이 부르는 함수) ----


# 도구(INTERNAL). docstring과 Field description은 모델에게 보이는 설명이라 고치지 않는다.
# content만 여기서 한 줄로 정리하고, 모든 판단은 save_memory가 한다. 모델이 무엇을 말하든 서버 검사가 최종이다.
async def remember(ctx: RunContext[AgentDeps], content: MemoryContent, quote: MemoryQuote) -> ToolResult:
    """사용자가 이번 메시지에서 기억해 달라고 한 사실이나 선호를 한 줄로 기억한다.
    quote에는 근거가 된 사용자 메시지 부분을 그대로 옮긴다. content는 그 말을 짧게 정리하되 새 사실을 덧붙이지 않는다.
    다른 사람에 대한 정보면 누구의 정보인지 남긴다. 인용·가정·추측은 저장하지 않는다.
    비밀번호·카드 번호는 저장하지 않는다."""
    async with ctx.deps.db() as session:
        return await save_memory(session, ctx.deps, normalize(content), quote)


# 도구(INTERNAL). 3단계 복구 규칙상 INTERNAL은 at-most-once라 끊긴 호출을 다시 실행하지 않는다(toolsets.py 주석).
async def forget(ctx: RunContext[AgentDeps], memory_id: int) -> ToolResult:
    """저장된 기억 하나를 지운다. memory_id는 list_memories 결과의 id를 쓴다."""
    async with ctx.deps.db() as session:
        deleted = await delete_memory(session, ctx.deps.user_id, memory_id)
    if not deleted:
        return ToolResult.failure(f"id가 {memory_id}인 기억을 찾을 수 없습니다. list_memories로 id를 확인하세요.")
    return ToolResult.success({"deleted_id": memory_id})


# 도구(READ). 지시문에는 최근 30줄만 들어가므로 그보다 오래된 기억은 이 도구로 본다.
async def list_memories(ctx: RunContext[AgentDeps], limit: MemoryLimit = 50) -> ToolResult:
    """저장된 기억을 id와 함께 최근 것부터 보여 준다."""
    async with ctx.deps.db() as session:
        rows = await select_memories(session, ctx.deps.user_id, limit)
    return ToolResult.success({"memories": [memory_view(m) for m in rows]})


# ---- 타임라인 문구 (step.label) ----
# toolsets.TOOL_LABELS가 모은다. 기억 내용이 타임라인 문구에 30자까지 보인다(notes.short).

LABELS: dict[str, Callable[[dict[str, Any]], str]] = {
    "remember": lambda args: f"기억에 '{short(args.get('content', ''))}' 저장하는 중",
    "forget": lambda args: f"기억 #{args.get('memory_id', '?')} 지우는 중",
    "list_memories": lambda args: "기억 목록 확인하는 중",
}
