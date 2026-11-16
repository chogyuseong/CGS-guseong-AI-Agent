"""
기억 도구: remember(INTERNAL), forget(INTERNAL), list_memories(READ).

피클 사례에서 에이전트가 메일·프로필·연락처를 종합해 말한 적 없는 자녀의 이름과 생일을 알아냈다.
그래서 remember는 프롬프트의 부탁이 아니라 서버 검사로 막는다(절대 원칙 6).
  1) 채팅으로 시작한 Task여야 한다(예약 작업·조건 감시는 사용자 문장이 없다).
  2) 이 Task에서 외부 내용 도구(웹 검색, 일정 조회, 메일)를 아직 쓰지 않았어야 한다.
     외부 글이 섞인 뒤에는 무엇이 사용자 말이고 무엇이 남의 글인지 구분할 수 없다.
  3) 저장할 내용의 핵심 단어가 이 Task를 시작한 사용자 메시지에 실제로 들어 있어야 한다.
출처(source_message)는 항상 그 시작 메시지라서, 기억 화면에서 원래 말을 확인할 수 있다.
"""

import re

from agent.models import RiskLevel, Task
from agent.registry import Tool, error_result, get_tool, ok_result, register
from memory.models import Memory

MAX_ACTIVE_MEMORIES = 200
MAX_CONTENT_LENGTH = 300
GROUNDED_RATIO = 0.6  # 핵심 단어의 60% 이상이 사용자 메시지에 있어야 한다
ONLY_USER_WORDS = "사용자가 직접 말한 내용만 기억할 수 있습니다."

# 단어 끝에서 떼어 낼 조사·어미. 긴 것부터 확인한다. "커피는" → "커피", "좋아함" → "좋아"
_PARTICLES = "은 는 이 가 을 를 에 의 도 만 로 와 과 랑 께 에서 으로 에게 한테 까지 부터"
_ENDINGS = "요 다 함 음 해 하다 이다 입니다 해요 이야"
_SUFFIXES = sorted(f"{_PARTICLES} {_ENDINGS}".split(), key=len, reverse=True)
_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


def key_words(text):
    """조사를 뗀 2글자 이상의 단어들. 한국어 형태소 분석기 없이 쓰는 단순한 규칙이다."""
    words = []
    for word in _WORD.findall(text or ""):
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 2:
                word = word[: -len(suffix)]
                break
        if len(word) >= 2:
            words.append(word)
    return words


def is_grounded(content, message):
    """저장할 내용의 핵심 단어가 사용자 메시지에 충분히 들어 있는가."""
    words = key_words(content)
    if not words:
        return False
    found = sum(1 for word in words if word in message)
    return found / len(words) >= GROUNDED_RATIO


def _used_external_content(task):
    names = task.steps.values_list("tool_name", flat=True)
    return any(getattr(get_tool(name), "external_content", False) for name in names)


def remember(user, content, task=None):
    trigger = task.trigger_message if task else None
    if task is None or task.origin != Task.Origin.CHAT or trigger is None or trigger.role != "user":
        return error_result(ONLY_USER_WORDS)
    if _used_external_content(task):
        return error_result(
            f"{ONLY_USER_WORDS} 이번 작업에서 웹 검색이나 일정 같은 외부 내용을 이미 읽었습니다."
        )
    if not is_grounded(content, trigger.content):
        return error_result(f"{ONLY_USER_WORDS} 저장하려는 내용이 이번 메시지에 없습니다.")
    if Memory.objects.filter(user=user, is_active=True).count() >= MAX_ACTIVE_MEMORIES:
        return error_result(
            f"기억은 {MAX_ACTIVE_MEMORIES}개까지 저장합니다. 기억 화면에서 오래된 것을 지워 주세요."
        )
    memory = Memory.objects.create(user=user, content=content.strip(), source_message=trigger)
    return ok_result({"id": memory.id, "content": memory.content, "saved_to": "앱 기억"})


def forget(user, memory_id):
    deleted, _ = Memory.objects.filter(user=user, id=memory_id).delete()  # 끄는 게 아니라 행을 지운다
    if not deleted:
        return error_result("해당 기억을 찾지 못했습니다. list_memories로 id를 확인하세요.")
    return ok_result({"deleted_id": memory_id})


def list_memories(user):
    memories = Memory.objects.filter(user=user, is_active=True).order_by("-created_at")[:50]
    return ok_result(
        {
            "memories": [
                {"id": m.id, "content": m.content, "saved_on": f"{m.created_at:%Y-%m-%d}"} for m in memories
            ]
        }
    )


register(
    Tool(
        name="remember",
        description=(
            "사용자가 이번 메시지에서 직접 말한 사실이나 선호를 한 줄로 기억한다. "
            "사용자의 말에 있는 단어를 그대로 쓴다. 추측하거나 여러 정보를 종합한 내용은 저장하지 않는다. "
            "건강·금융·가족 정보는 사용자가 '기억해'라고 했을 때만."
        ),
        input_schema={
            "type": "object",
            "properties": {"content": {"type": "string", "minLength": 2, "maxLength": MAX_CONTENT_LENGTH}},
            "required": ["content"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.INTERNAL,
        run=remember,
        label=lambda content="", **_: f"기억하기: {content[:20]}",
        needs_task=True,
    )
)

register(
    Tool(
        name="forget",
        description="저장된 기억 하나를 지운다. id는 list_memories로 찾는다.",
        input_schema={
            "type": "object",
            "properties": {"memory_id": {"type": "integer"}},
            "required": ["memory_id"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.INTERNAL,
        run=forget,
        label=lambda memory_id=None, **_: f"기억 지우기 (#{memory_id})",
    )
)

register(
    Tool(
        name="list_memories",
        description="저장된 기억을 id와 함께 최근 것부터 보여 준다.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk_level=RiskLevel.READ,
        run=list_memories,
        label=lambda **_: "기억 목록 확인",
    )
)
