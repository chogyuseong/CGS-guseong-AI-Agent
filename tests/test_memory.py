"""5단계: 기억. 사용자가 직접 말한 것만 기억하고(서버가 검사), 화면에서 보고 지울 수 있다.

피클 사례: 메일·프로필·연락처를 종합해 말한 적 없는 자녀의 이름과 생일을 파악했다.
그래서 기억은 '추론'이 아니라 '사용자가 이번 메시지에서 직접 말한 문장'에서만 만든다.
"""

import pytest

from agent.llm import FakeLLM, LLMResponse, ToolCall
from agent.models import RiskLevel, Step, Task

pytestmark = pytest.mark.django_db


def make_task(user, conversation, text, origin=Task.Origin.CHAT):
    from chat.models import Message

    message = Message.objects.create(conversation=conversation, role=Message.Role.USER, content=text)
    task = Task.objects.create(user=user, conversation=conversation, trigger_message=message, origin=origin)
    message.task = task
    message.save()
    return task


def remember(task, content):
    from agent.registry import get_tool, run_tool

    return run_tool(get_tool("remember"), task.user, {"content": content}, task=task)


# ── remember: 서버 검사 ──────────────────────────────────


def test_remember_saves_with_source_message(user, conversation):
    from memory.models import Memory

    task = make_task(user, conversation, "나는 커피는 라떼를 좋아해. 기억해 줘")
    result = remember(task, "커피는 라떼를 좋아함")

    assert result["ok"] is True
    memory = Memory.objects.get(user=user)
    assert memory.content == "커피는 라떼를 좋아함"
    assert memory.source_message == task.trigger_message  # 출처는 항상 이 Task를 시작한 메시지


def test_remember_rejects_content_not_in_message(user, conversation):
    """사용자가 말하지 않은 추측(예: 사는 곳)은 저장하지 않는다."""
    from memory.models import Memory

    task = make_task(user, conversation, "나는 커피는 라떼를 좋아해. 기억해 줘")
    result = remember(task, "사용자는 서울 강남에 산다")

    assert result["ok"] is False
    assert "직접 말한 내용만" in result["error"]
    assert not Memory.objects.exists()


def test_13_remember_refused_for_non_chat_task(user, conversation):
    from memory.models import Memory

    task = make_task(user, conversation, "커피는 라떼", origin=Task.Origin.SCHEDULE)
    result = remember(task, "커피는 라떼")

    assert result["ok"] is False
    assert not Memory.objects.exists()


@pytest.mark.parametrize("tool_name", ["web_search", "list_events"])
def test_13_remember_refused_after_external_content_tool(user, conversation, tool_name):
    """웹 검색 결과나 일정 제목 같은 외부 글이 섞인 뒤에는 기억을 만들지 않는다."""
    from memory.models import Memory

    task = make_task(user, conversation, "커피는 라떼를 좋아해. 기억해")
    Step.objects.create(
        task=task, order=1, tool_name=tool_name, risk_level=RiskLevel.READ, status="done", ok=True
    )
    result = remember(task, "커피는 라떼를 좋아함")

    assert result["ok"] is False
    assert not Memory.objects.exists()


def test_13_remember_refused_without_user_message(user, conversation):
    from memory.models import Memory

    task = Task.objects.create(user=user, conversation=conversation)  # 시작 메시지가 없는 Task
    result = remember(task, "커피는 라떼")

    assert result["ok"] is False
    assert not Memory.objects.exists()


def test_remember_needs_task_context(user):
    from agent.registry import get_tool, run_tool

    result = run_tool(get_tool("remember"), user, {"content": "커피는 라떼"})  # Task 없이 부름
    assert result["ok"] is False


def test_active_memory_limit_200(user, conversation):
    from memory.models import Memory

    task = make_task(user, conversation, "커피는 라떼를 좋아해")
    Memory.objects.bulk_create(
        [Memory(user=user, content=f"기억 {i}", source_message=task.trigger_message) for i in range(200)]
    )
    result = remember(task, "커피는 라떼를 좋아함")

    assert result["ok"] is False and "200" in result["error"]


def test_content_over_300_chars_is_rejected_by_schema():
    from agent.registry import get_tool, validate_input

    assert validate_input(get_tool("remember"), {"content": "가" * 301})


def test_matching_ignores_korean_particles():
    from tools.memory_tools import is_grounded

    assert is_grounded("커피는 라떼를 좋아함", "나 커피 라떼 좋아해") is True
    assert is_grounded("딸 이름은 지우", "오늘 날씨 어때") is False
    assert is_grounded("", "아무 말") is False


# ── forget, list_memories ────────────────────────────────


def test_forget_deletes_row(user, conversation):
    from agent.registry import get_tool, run_tool
    from memory.models import Memory

    task = make_task(user, conversation, "커피는 라떼")
    memory = Memory.objects.create(user=user, content="커피는 라떼", source_message=task.trigger_message)
    result = run_tool(get_tool("forget"), user, {"memory_id": memory.id})

    assert result["ok"] is True
    assert not Memory.objects.filter(id=memory.id).exists()  # is_active=False가 아니라 행 삭제


def test_forget_cannot_touch_others(user, other_user, conversation):
    from agent.registry import get_tool, run_tool
    from memory.models import Memory

    task = make_task(user, conversation, "커피는 라떼")
    memory = Memory.objects.create(user=user, content="커피는 라떼", source_message=task.trigger_message)
    result = run_tool(get_tool("forget"), other_user, {"memory_id": memory.id})

    assert result["ok"] is False
    assert Memory.objects.filter(id=memory.id).exists()


def test_list_memories(user, conversation):
    from agent.registry import get_tool, run_tool
    from memory.models import Memory

    task = make_task(user, conversation, "커피는 라떼")
    Memory.objects.create(user=user, content="커피는 라떼", source_message=task.trigger_message)
    result = run_tool(get_tool("list_memories"), user, {})

    assert result["ok"] is True
    assert result["data"]["memories"][0]["content"] == "커피는 라떼"


def test_tool_levels():
    from agent.registry import get_tool

    assert get_tool("remember").risk_level == "INTERNAL"
    assert get_tool("forget").risk_level == "INTERNAL"
    assert get_tool("list_memories").risk_level == "READ"


# ── 프롬프트에 기억 넣기 ─────────────────────────────────


def run(task, responses):
    from agent.loop import run_task

    llm = FakeLLM(responses)
    run_task(task.id, llm=llm)
    return llm


def test_memories_are_in_system_prompt(user, conversation):
    from memory.models import Memory

    first = make_task(user, conversation, "커피는 라떼")
    Memory.objects.create(user=user, content="커피는 라떼를 좋아함", source_message=first.trigger_message)
    task = make_task(user, conversation, "카페 추천해줘")
    llm = run(task, [LLMResponse(text="네")])

    assert "커피는 라떼를 좋아함" in llm.calls[0]["system"]


def test_only_recent_30_memories_go_into_prompt(user, conversation):
    from memory.models import Memory

    first = make_task(user, conversation, "기억들")
    for i in range(35):
        Memory.objects.create(user=user, content=f"기억-{i:02d}", source_message=first.trigger_message)
    task = make_task(user, conversation, "안녕")
    system = run(task, [LLMResponse(text="네")]).calls[0]["system"]

    assert "기억-34" in system and "기억-05" in system
    assert "기억-04" not in system and "기억-00" not in system  # 넘치면 오래된 것부터 뺀다


def test_deleted_memory_disappears_from_next_prompt(user, conversation):
    from memory.models import Memory

    first = make_task(user, conversation, "커피는 라떼")
    memory = Memory.objects.create(
        user=user, content="커피는 라떼를 좋아함", source_message=first.trigger_message
    )
    memory.delete()
    task = make_task(user, conversation, "카페 추천해줘")

    assert "커피는 라떼를 좋아함" not in run(task, [LLMResponse(text="네")]).calls[0]["system"]


def test_other_users_memories_are_not_used(user, other_user, conversation):
    from chat.models import Conversation
    from memory.models import Memory

    other_conversation = Conversation.objects.create(user=other_user)
    theirs = make_task(other_user, other_conversation, "비밀 기억")
    Memory.objects.create(user=other_user, content="남의 비밀 기억", source_message=theirs.trigger_message)
    task = make_task(user, conversation, "안녕")

    assert "남의 비밀 기억" not in run(task, [LLMResponse(text="네")]).calls[0]["system"]


def test_memory_saved_in_this_task_does_not_change_its_prompt(user, conversation):
    """같은 Task 안에서 remember를 해도 시스템 프롬프트가 바뀌지 않는다(프롬프트 캐시 유지)."""
    task = make_task(user, conversation, "나는 커피는 라떼를 좋아해. 기억해 줘")
    call = LLMResponse(text="", tool_calls=[ToolCall("t1", "remember", {"content": "커피는 라떼를 좋아함"})])
    llm = run(task, [call, LLMResponse(text="기억했어요.")])

    assert llm.calls[0]["system"] == llm.calls[1]["system"]


def test_remember_through_the_loop(user, conversation):
    """루프가 remember에 Task 정보를 넘겨, 실제 대화 흐름에서 저장된다."""
    from memory.models import Memory

    task = make_task(user, conversation, "나는 커피는 라떼를 좋아해. 기억해 줘")
    call = LLMResponse(text="", tool_calls=[ToolCall("t1", "remember", {"content": "커피는 라떼를 좋아함"})])
    run(task, [call, LLMResponse(text="기억했어요.")])

    assert Memory.objects.get(user=user).source_message == task.trigger_message


# ── 기억 화면 ───────────────────────────────────────────


@pytest.fixture
def saved_memory(user, conversation):
    from memory.models import Memory

    task = make_task(user, conversation, "나는 커피는 라떼를 좋아해. 기억해 줘")
    return Memory.objects.create(
        user=user, content="커피는 라떼를 좋아함", source_message=task.trigger_message
    )


def test_memory_page_lists_with_original_message(logged_in_client, saved_memory):
    page = logged_in_client.get("/memory/").content.decode()

    assert "커피는 라떼를 좋아함" in page
    assert "나는 커피는 라떼를 좋아해. 기억해 줘" in page  # 원래 메시지 보기
    assert f"/memory/{saved_memory.id}/delete/" in page
    assert "/memory/delete-all/" in page


def test_memory_page_empty_state(logged_in_client):
    page = logged_in_client.get("/memory/").content.decode()
    assert "기억해" in page  # 어떻게 기억을 남기는지 안내


def test_delete_one(logged_in_client, saved_memory):
    from memory.models import Memory

    response = logged_in_client.post(f"/memory/{saved_memory.id}/delete/")
    assert response.status_code == 302
    assert not Memory.objects.exists()


def test_delete_all(logged_in_client, saved_memory, user, other_user):
    from chat.models import Conversation
    from memory.models import Memory

    other_conversation = Conversation.objects.create(user=other_user)
    theirs = make_task(other_user, other_conversation, "남의 기억")
    Memory.objects.create(user=other_user, content="남의 기억", source_message=theirs.trigger_message)

    logged_in_client.post("/memory/delete-all/")

    assert not Memory.objects.filter(user=user).exists()
    assert Memory.objects.filter(user=other_user).count() == 1  # 남의 기억은 지우지 않는다


def test_cannot_delete_others_memory(client, other_user, saved_memory):
    from memory.models import Memory

    client.force_login(other_user)
    assert client.post(f"/memory/{saved_memory.id}/delete/").status_code == 404
    assert Memory.objects.filter(id=saved_memory.id).exists()


def test_memory_views_require_post_and_login(logged_in_client, saved_memory):
    from django.test import Client

    assert logged_in_client.get(f"/memory/{saved_memory.id}/delete/").status_code == 405
    anonymous = Client()  # logged_in_client와 같은 객체가 아닌, 로그인하지 않은 새 브라우저
    assert anonymous.get("/memory/").status_code == 302
    assert anonymous.post("/memory/delete-all/").status_code == 302
