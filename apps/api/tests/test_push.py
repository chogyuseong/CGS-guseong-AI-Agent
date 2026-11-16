"""3단계 웹 푸시 테스트 (SPEC 테스트 12). 실제 푸시 서버 대신 FakePushSender(tests/fake_push.py)를 쓴다.

- 테스트 12: test_push_body_never_contains_message_content
"""

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import runner
from app.agent.agent import MyAgent
from app.config import Settings
from app.models import PushSubscription, User
from app.push import PUSH_BODIES, PUSH_TITLE, PushKind
from tests.fake_push import FakePushSender
from tests.fake_tools import FakeExternalCounter
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send

ENDPOINT = "https://fcm.googleapis.com/fcm/send/device-token-1"
OTHER_ENDPOINT = "https://web.push.apple.com/QGuQyavXutnMH-device-2"
P256DH = "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM"
AUTH = "tBHItJI5svbpez7KI4CCXg"
SECRET = "비밀번호는 hunter2이고 엄마 생일 선물 사기"


def subscription_body(endpoint: str = ENDPOINT) -> dict:
    """브라우저 PushSubscription.toJSON()과 같은 모양."""
    return {"endpoint": endpoint, "expirationTime": None, "keys": {"p256dh": P256DH, "auth": AUTH}}


@pytest.fixture
def push_on(test_settings: Settings) -> Settings:
    """VAPID 세 값을 채운다. 실제로 보내지는 않으므로 아무 문자열이면 된다."""
    test_settings.vapid_public_key = "test-public-key"
    test_settings.vapid_private_key = SecretStr("test-private-key")
    test_settings.vapid_subject = "mailto:test@example.com"
    return test_settings


async def subscribe(client: httpx.AsyncClient, endpoint: str = ENDPOINT) -> httpx.Response:
    return await client.post("/api/push/subscribe", json=subscription_body(endpoint))


# ---- 구독 API ----


async def test_public_key_reports_enabled_only_with_subject(
    client: httpx.AsyncClient, logged_in_user: User, test_settings: Settings
) -> None:
    test_settings.vapid_public_key = "test-public-key"
    off = (await client.get("/api/push/public-key")).json()
    test_settings.vapid_private_key = SecretStr("test-private-key")
    test_settings.vapid_subject = "mailto:test@example.com"
    on = (await client.get("/api/push/public-key")).json()

    assert off == {"enabled": False, "public_key": "test-public-key"}
    assert on == {"enabled": True, "public_key": "test-public-key"}


async def test_push_api_requires_login(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/push/public-key")).status_code == 401
    assert (await subscribe(client)).status_code == 401


async def test_subscribe_saves_and_updates_by_endpoint(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession
) -> None:
    first = await subscribe(client)
    again = await subscribe(client)  # 같은 기기에서 다시 켜도 한 행

    assert first.status_code == 204
    assert again.status_code == 204
    rows = (await db_session.scalars(select(PushSubscription))).all()
    assert len(rows) == 1
    assert rows[0].user_id == logged_in_user.id
    assert rows[0].p256dh == P256DH


async def test_subscribe_moves_endpoint_to_current_user(
    client: httpx.AsyncClient, logged_in_user: User, make_user, db_session: AsyncSession
) -> None:
    """한 브라우저에서 다른 계정으로 다시 로그인해 알림을 켜면 그 기기의 알림은 지금 사용자에게 간다."""
    other = await make_user("other@example.com", "password-other")
    db_session.add(PushSubscription(user_id=other.id, endpoint=ENDPOINT, p256dh="old", auth="old"))
    await db_session.commit()

    assert (await subscribe(client)).status_code == 204

    rows = (await db_session.scalars(select(PushSubscription).execution_options(populate_existing=True))).all()
    assert [(r.user_id, r.p256dh) for r in rows] == [(logged_in_user.id, P256DH)]


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/x",  # https가 아니다
        "https://127.0.0.1/push",  # 푸시 서비스가 아닌 곳(서버가 대신 요청을 보내게 만드는 공격 방지)
        "https://evil.example.com/fcm.googleapis.com",
        "https://googleapis.com.evil.example/x",
    ],
)
async def test_subscribe_rejects_non_push_service_endpoint(
    client: httpx.AsyncClient, logged_in_user: User, endpoint: str
) -> None:
    assert (await subscribe(client, endpoint)).status_code == 422


async def test_subscribe_rejects_bad_keys(client: httpx.AsyncClient, logged_in_user: User) -> None:
    body = subscription_body()
    body["keys"]["auth"] = "not base64url!"
    assert (await client.post("/api/push/subscribe", json=body)).status_code == 422


async def test_unsubscribe_deletes_only_my_endpoint(
    client: httpx.AsyncClient, logged_in_user: User, make_user, db_session: AsyncSession
) -> None:
    other = await make_user("other@example.com", "password-other")
    db_session.add(PushSubscription(user_id=other.id, endpoint=OTHER_ENDPOINT, p256dh=P256DH, auth=AUTH))
    await db_session.commit()
    await subscribe(client)

    mine = await client.post("/api/push/unsubscribe", json={"endpoint": ENDPOINT})
    others = await client.post("/api/push/unsubscribe", json={"endpoint": OTHER_ENDPOINT})

    assert mine.status_code == 204
    assert others.status_code == 204  # 남의 것은 조용히 무시(존재 여부를 드러내지 않는다)
    rows = (await db_session.scalars(select(PushSubscription))).all()
    assert [r.endpoint for r in rows] == [OTHER_ENDPOINT]


# ---- notify ----


async def test_push_body_never_contains_message_content(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    push_on: Settings,
    push_sender: FakePushSender,
    fake_external_counter: FakeExternalCounter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """테스트 12: 승인 필요·완료·실패 푸시 모두 본문은 고정 문구이고, 요청 내용·도구 입력·답변이 들어가지 않는다."""
    monkeypatch.setattr(runner, "TASK_DONE_NOTIFY_AFTER_SECONDS", -1.0)  # 빨리 끝나도 완료 푸시를 보내게
    await subscribe(client)
    model = scripted(call("fake_external", value=7), text(f"끝났습니다. {SECRET}"))
    with agent.override(model=model.model()):
        task = await send(client, conversation, SECRET)
        await client.post(f"/api/approvals/{task['approvals'][0]['id']}/approve")
    with agent.override(model=scripted(call("no_such_tool")).model()):
        await send(client, conversation, SECRET)  # 계속 없는 도구만 불러 실패

    payloads = push_sender.payloads()
    assert [p["kind"] for p in payloads] == ["approval_needed", "task_done", "task_failed"]
    for payload in payloads:
        assert set(payload) == {"title", "body", "url", "kind"}
        assert payload["title"] == PUSH_TITLE
        assert payload["body"] == PUSH_BODIES[PushKind(payload["kind"])]
        assert payload["url"] == f"/chat/{conversation}"
    raw = " ".join(body for _, body in push_sender.sent)
    for fragment in ["hunter2", "비밀번호", "생일", "value", "fake_external", "끝났습니다"]:
        assert fragment not in raw


async def test_fast_chat_task_does_not_push_done(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, push_on: Settings, push_sender: FakePushSender
) -> None:
    """명세: task_done은 20초 넘게 걸렸거나 자동 작업일 때만."""
    await subscribe(client)
    with agent.override(model=scripted(text("네")).model()):
        await send(client, conversation, "안녕")

    assert push_sender.sent == []


async def test_no_push_when_vapid_subject_is_empty(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    test_settings: Settings,
    push_sender: FakePushSender,
    fake_external_counter: FakeExternalCounter,
) -> None:
    test_settings.vapid_public_key = "test-public-key"
    test_settings.vapid_private_key = SecretStr("test-private-key")
    test_settings.vapid_subject = ""
    await subscribe(client)
    with agent.override(model=scripted(call("fake_external", value=1), text("끝")).model()):
        task = await send(client, conversation, "외부 도구 실행해줘")

    assert task["status"] == "waiting_approval"
    assert push_sender.sent == []


async def test_expired_subscription_is_deleted_and_failure_does_not_break_task(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    push_on: Settings,
    push_sender: FakePushSender,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
) -> None:
    await subscribe(client, ENDPOINT)
    await subscribe(client, OTHER_ENDPOINT)
    push_sender.expire(ENDPOINT)  # 404·410: 만료된 구독
    push_sender.failures[OTHER_ENDPOINT] = RuntimeError("푸시 서버 응답 500")

    with agent.override(model=scripted(call("fake_external", value=1), text("끝")).model()):
        task = await send(client, conversation, "외부 도구 실행해줘")

    assert task["status"] == "waiting_approval"  # 발송 실패가 작업을 실패시키지 않는다
    rows = (await db_session.scalars(select(PushSubscription).execution_options(populate_existing=True))).all()
    assert [r.endpoint for r in rows] == [OTHER_ENDPOINT]  # 만료된 것만 지운다


async def test_notify_updates_last_used_at(
    app: FastAPI, push_on: Settings, push_sender: FakePushSender, logged_in_user: User, db_session: AsyncSession
) -> None:
    from app.agent.workflows import services_from_state

    db_session.add(PushSubscription(user_id=logged_in_user.id, endpoint=ENDPOINT, p256dh=P256DH, auth=AUTH))
    await db_session.commit()

    sent = await services_from_state(app.state).notifier.notify(logged_in_user.id, PushKind.APPROVAL_NEEDED, "/chat/1")

    assert sent == 1
    row = await db_session.scalar(select(PushSubscription).execution_options(populate_existing=True))
    assert row is not None and row.last_used_at is not None
