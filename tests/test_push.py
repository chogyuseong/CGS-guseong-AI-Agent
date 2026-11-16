"""3단계: 웹 푸시. 테스트 12번(푸시 본문에 메시지 내용이 없음)이 핵심이다."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from agent.llm import FakeLLM, LLMResponse
from agent.models import Task

pytestmark = pytest.mark.django_db

SECRET_TEXT = "내 비밀번호 힌트는 강아지 이름"


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture
def vapid(settings):
    settings.VAPID_PUBLIC_KEY = "test-public"
    settings.VAPID_PRIVATE_KEY = "test-private"
    settings.VAPID_SUBJECT = "mailto:test@example.com"


@pytest.fixture
def sent(monkeypatch, vapid):
    """실제 푸시 서버 대신 보낸 내용을 모은다."""
    payloads = []

    def fake_webpush(subscription_info, data, **kwargs):
        payloads.append({"endpoint": subscription_info["endpoint"], "data": json.loads(data), **kwargs})

    monkeypatch.setattr("notifications.push.webpush", fake_webpush)
    return payloads


@pytest.fixture
def subscription(user):
    from notifications.models import PushSubscription

    return PushSubscription.objects.create(
        user=user, endpoint="https://push.example/abc", p256dh="k", auth="a"
    )


def make_task(user, conversation, text, origin=Task.Origin.CHAT):
    from chat.models import Message

    message = Message.objects.create(conversation=conversation, role=Message.Role.USER, content=text)
    task = Task.objects.create(user=user, conversation=conversation, trigger_message=message, origin=origin)
    message.task = task
    message.save()
    return task


def run(task, text):
    from agent.loop import run_task

    run_task(task.id, llm=FakeLLM([LLMResponse(text=text, stop_reason="end_turn")]))


# ── 테스트 12: 푸시에 개인 내용이 없다 ───────────────────


def test_12_push_body_never_contains_message_content(user, conversation, subscription, sent):
    task = make_task(user, conversation, SECRET_TEXT, origin=Task.Origin.SCHEDULE)
    run(task, f"답변: {SECRET_TEXT}")

    assert len(sent) == 1
    whole = json.dumps(sent[0], ensure_ascii=False)
    assert SECRET_TEXT not in whole
    assert sent[0]["data"]["title"] == "맡기신 작업이 끝났습니다"


def test_12_notify_accepts_only_fixed_kinds(user, subscription, sent):
    from notifications.push import notify

    with pytest.raises(ValueError):
        notify(user, "custom", "/")
    assert sent == []


@pytest.mark.parametrize(
    ("kind", "title"),
    [
        ("approval_needed", "승인이 필요한 작업이 있습니다"),
        ("task_done", "맡기신 작업이 끝났습니다"),
        ("task_failed", "작업을 끝내지 못했습니다"),
        ("watch_met", "지켜보던 조건이 충족되었습니다"),
        ("briefing", "오늘의 브리핑이 준비되었습니다"),
    ],
)
def test_fixed_titles(user, subscription, sent, kind, title):
    from notifications.push import notify

    notify(user, kind, "/c/1/")
    assert sent[0]["data"] == {"title": title, "body": sent[0]["data"]["body"], "url": "/c/1/"}
    assert sent[0]["data"]["title"] == title


# ── 언제 보내는가 ───────────────────────────────────────


def test_quick_chat_task_does_not_push(user, conversation, subscription, sent):
    task = make_task(user, conversation, "안녕")
    run(task, "안녕하세요")
    assert sent == []


def test_slow_chat_task_pushes_done(user, conversation, subscription, sent):
    task = make_task(user, conversation, "오래 걸리는 일")
    Task.objects.filter(id=task.id).update(created_at=timezone.now() - timedelta(seconds=30))
    run(task, "끝났어요")
    assert [p["data"]["title"] for p in sent] == ["맡기신 작업이 끝났습니다"]


def test_failed_task_pushes_failure(user, conversation, subscription, sent, settings):
    from agent.loop import run_task

    task = make_task(user, conversation, "안녕")
    run_task(task.id, llm=FakeLLM([LLMResponse(text="", stop_reason="refusal")]))
    assert [p["data"]["title"] for p in sent] == ["작업을 끝내지 못했습니다"]


def test_approval_request_pushes(user, conversation, subscription, sent, monkeypatch):
    from agent import registry
    from agent.llm import ToolCall
    from agent.loop import run_task
    from agent.models import RiskLevel
    from agent.registry import Tool, ok_result

    tool = Tool(
        name="fake_external",
        description="x",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk_level=RiskLevel.EXTERNAL,
        run=lambda user, **_: ok_result(),
        label=lambda **_: "외부",
        preview=lambda user, **_: "외부 행동을 합니다.",
    )
    monkeypatch.setitem(registry._TOOLS, tool.name, tool)
    task = make_task(user, conversation, "해줘")
    call = LLMResponse(text="", tool_calls=[ToolCall("t1", "fake_external", {})], stop_reason="tool_use")
    guard = LLMResponse(text='{"verdict": "match", "reason": "같음"}')
    run_task(task.id, llm=FakeLLM([call]), guard_llm=FakeLLM([guard]))

    assert [p["data"]["title"] for p in sent] == ["승인이 필요한 작업이 있습니다"]
    assert sent[0]["data"]["url"] == f"/c/{conversation.id}/"


# ── 실패해도 작업은 계속된다 ─────────────────────────────


@pytest.mark.parametrize("status_code", [404, 410])
def test_gone_subscription_is_deleted(user, subscription, vapid, monkeypatch, status_code):
    from pywebpush import WebPushException

    from notifications.models import PushSubscription
    from notifications.push import notify

    def gone(*args, **kwargs):
        raise WebPushException("gone", response=FakeResponse(status_code))

    monkeypatch.setattr("notifications.push.webpush", gone)
    notify(user, "task_done", "/")
    assert not PushSubscription.objects.filter(id=subscription.id).exists()


def test_other_push_errors_keep_subscription_and_do_not_raise(user, subscription, vapid, monkeypatch):
    from pywebpush import WebPushException

    from notifications.models import PushSubscription
    from notifications.push import notify

    def broken(*args, **kwargs):
        raise WebPushException("server error", response=FakeResponse(500))

    monkeypatch.setattr("notifications.push.webpush", broken)
    notify(user, "task_done", "/")  # 예외가 밖으로 나오지 않는다
    assert PushSubscription.objects.filter(id=subscription.id).exists()


def test_push_failure_does_not_fail_task(user, conversation, subscription, vapid, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("네트워크 끊김")

    monkeypatch.setattr("notifications.push.webpush", explode)
    task = make_task(user, conversation, "일", origin=Task.Origin.SCHEDULE)
    run(task, "끝")
    task.refresh_from_db()
    assert task.status == Task.Status.DONE


def test_no_vapid_keys_means_no_push(user, subscription, monkeypatch, settings):
    from notifications.push import notify

    settings.VAPID_PRIVATE_KEY = ""
    calls = []
    monkeypatch.setattr("notifications.push.webpush", lambda *a, **k: calls.append(1))
    notify(user, "task_done", "/")
    assert calls == []


def test_successful_push_updates_last_used(user, subscription, sent):
    from notifications.push import notify

    notify(user, "task_done", "/")
    subscription.refresh_from_db()
    assert subscription.last_used_at is not None


# ── 구독 API와 서비스 워커 ───────────────────────────────


def subscribe_body(endpoint="https://push.example/new"):
    return json.dumps({"endpoint": endpoint, "keys": {"p256dh": "pk", "auth": "ak"}})


def test_subscribe_and_unsubscribe(logged_in_client, user):
    from notifications.models import PushSubscription

    response = logged_in_client.post("/push/subscribe/", subscribe_body(), content_type="application/json")
    assert response.status_code == 201
    assert PushSubscription.objects.get(user=user).endpoint == "https://push.example/new"

    logged_in_client.post("/push/subscribe/", subscribe_body(), content_type="application/json")
    assert PushSubscription.objects.count() == 1  # 같은 브라우저가 두 번 구독해도 한 줄

    body = json.dumps({"endpoint": "https://push.example/new"})
    response = logged_in_client.post("/push/unsubscribe/", body, content_type="application/json")
    assert response.status_code == 204
    assert not PushSubscription.objects.exists()


def test_subscribe_rejects_bad_input(logged_in_client):
    bad = json.dumps({"endpoint": "not-a-url", "keys": {}})
    assert logged_in_client.post("/push/subscribe/", bad, content_type="application/json").status_code == 400


def test_subscribe_requires_login(client):
    response = client.post("/push/subscribe/", subscribe_body(), content_type="application/json")
    assert response.status_code == 302


def test_service_worker_is_served_at_root_scope(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    assert b"push" in b"".join(response.streaming_content if response.streaming else [response.content])


def test_settings_page_asks_permission_only_on_button(logged_in_client, settings):
    settings.VAPID_PUBLIC_KEY = "PublicKeyForTest123"  # escapejs가 '-'를 바꾸므로 하이픈 없는 값을 쓴다
    body = logged_in_client.get("/settings/").content.decode()

    assert "알림 켜기" in body
    assert "홈 화면에 추가" in body  # 아이폰 안내
    assert "PublicKeyForTest123" in body
    assert "requestPermission" in body
    # 권한 요청은 버튼 클릭 처리기 안에서만 한다(페이지를 열자마자 묻지 않는다)
    assert body.index("requestPermission") > body.index('enableButton.addEventListener("click"')


def test_settings_page_never_contains_private_key(logged_in_client, settings):
    settings.VAPID_PUBLIC_KEY = "PublicKeyForTest123"
    settings.VAPID_PRIVATE_KEY = "PrivateKeyMustNotLeak"
    body = logged_in_client.get("/settings/").content.decode()
    assert "PrivateKeyMustNotLeak" not in body


def test_generated_vapid_keys_are_usable():
    from py_vapid import Vapid

    from notifications.management.commands.generate_vapid_keys import generate_keys

    public_key, private_key = generate_keys()
    vapid = Vapid.from_string(private_key)  # pywebpush가 읽는 방식 그대로
    assert vapid.public_key is not None
    assert len(public_key) == 87  # 65바이트 공개키의 base64url(패딩 없음) 길이
