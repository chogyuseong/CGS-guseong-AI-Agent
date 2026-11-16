"""1단계: 채팅 화면에서 메시지를 보내면 Task가 만들어지고 루프가 실행된다."""

import pytest

from agent.llm import FakeLLM, LLMResponse

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM([LLMResponse(text="네, 추가했어요.", tool_calls=[], stop_reason="end_turn")])
    monkeypatch.setattr("agent.loop.get_llm_client", lambda: llm)
    return llm


def test_sending_message_creates_task_and_answer(logged_in_client, conversation, fake_llm):
    from agent.models import Task

    response = logged_in_client.post(f"/c/{conversation.id}/messages/", {"content": "우유 사기 추가해줘"})

    assert response.status_code == 302
    task = Task.objects.get(conversation=conversation)
    assert task.trigger_message.content == "우유 사기 추가해줘"
    assert task.status == "done"
    page = logged_in_client.get(f"/c/{conversation.id}/").content.decode()
    assert "네, 추가했어요." in page


def test_empty_message_is_ignored(logged_in_client, conversation, fake_llm):
    from agent.models import Task

    logged_in_client.post(f"/c/{conversation.id}/messages/", {"content": "   "})
    assert not Task.objects.exists()


def test_cannot_post_to_someone_elses_conversation(client, other_user, conversation, fake_llm):
    client.force_login(other_user)
    response = client.post(f"/c/{conversation.id}/messages/", {"content": "안녕"})
    assert response.status_code == 404


def test_get_is_not_allowed(logged_in_client, conversation):
    response = logged_in_client.get(f"/c/{conversation.id}/messages/")
    assert response.status_code == 405


def test_more_than_20_messages_per_minute_is_rejected(logged_in_client, conversation, fake_llm):
    from chat.models import Message

    for i in range(20):
        Message.objects.create(conversation=conversation, role=Message.Role.USER, content=f"{i}")
    response = logged_in_client.post(f"/c/{conversation.id}/messages/", {"content": "21번째"})

    assert response.status_code == 429


def test_missing_api_key_shows_friendly_failure(logged_in_client, conversation, settings):
    from agent.models import Task

    settings.ANTHROPIC_API_KEY = ""
    logged_in_client.post(f"/c/{conversation.id}/messages/", {"content": "안녕"})

    task = Task.objects.get()
    assert task.status == "failed"
    page = logged_in_client.get(f"/c/{conversation.id}/").content.decode()
    assert "API 키" in page
