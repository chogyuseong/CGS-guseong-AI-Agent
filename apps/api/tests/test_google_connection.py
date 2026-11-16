"""Google 계정 연결(OAuth) API 테스트. Google 서버 대신 가짜 HTTP(공식 문서 형식의 응답, 실제 응답 아님).

흐름: GET /api/connections/google/start → 302 Google 동의 화면(state·PKCE를 세션에 저장)
     → Google이 GET /api/connections/google/callback?code&state로 돌려보냄 → state 검증 → 토큰 교환
     → 토큰을 Fernet으로 암호화해 connections에 저장 → 302 {APP_ORIGIN}/connections?google=connected
"""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.google_auth import CALENDAR_SCOPE
from app.models import Connection, User
from tests.conftest import TEST_FERNET_KEY, TEST_GOOGLE_REDIRECT, TEST_ORIGIN
from tests.fake_http import FakeHttp, fixture_response, json_response

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CONNECTED = f"{TEST_ORIGIN}/connections?google=connected"


def error_redirect(reason: str) -> str:
    return f"{TEST_ORIGIN}/connections?google=error&reason={reason}"


async def start(client: httpx.AsyncClient) -> dict[str, list[str]]:
    response = await client.get("/api/connections/google/start")
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    return parse_qs(location.query)


async def callback(client: httpx.AsyncClient, **params: str) -> httpx.Response:
    return await client.get("/api/connections/google/callback", params=params)


async def stored(db_session: AsyncSession, user: User) -> Connection | None:
    return await db_session.scalar(
        select(Connection)
        .where(Connection.user_id == user.id, Connection.provider == "google")
        .execution_options(populate_existing=True)
    )


async def test_connections_requires_login(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/connections")

    assert response.status_code == 401


async def test_connections_lists_google_not_connected(
    client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp
) -> None:
    response = await client.get("/api/connections")

    assert response.status_code == 200
    assert response.json() == [
        {
            "provider": "google",
            "available": True,
            "connected": False,
            "calendar": False,
            "scopes": [],
            "connected_at": None,
        }
    ]


async def test_start_redirects_to_google_with_state_and_pkce(
    client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp
) -> None:
    query = await start(client)

    assert query["client_id"] == ["test-google-client.apps.googleusercontent.com"]
    assert query["redirect_uri"] == [TEST_GOOGLE_REDIRECT]
    assert query["response_type"] == ["code"]
    assert query["scope"] == [CALENDAR_SCOPE]  # 캘린더 일정에 필요한 최소 범위 하나만(메일은 7단계)
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["state"][0]) >= 32
    assert fake_http.requests == []  # 시작 단계에서는 Google에 요청을 보내지 않는다


async def test_callback_stores_encrypted_tokens(
    client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    fake_http.route("POST", TOKEN_URL, fixture_response("google_token_exchange.json"))
    query = await start(client)

    response = await callback(client, code="auth-code-123", state=query["state"][0])

    assert response.status_code == 302
    assert response.headers["location"] == CONNECTED
    row = await stored(db_session, logged_in_user)
    assert row is not None
    assert row.scopes == [CALENDAR_SCOPE]
    assert row.expires_at is not None
    assert "ya29.test-access-token" not in row.encrypted_token  # 평문 토큰은 DB에 없다
    assert "1//test-refresh-token" in Fernet(TEST_FERNET_KEY.encode()).decrypt(row.encrypted_token.encode()).decode()
    [token_request] = fake_http.sent("POST", TOKEN_URL)
    form = parse_qs(token_request.content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["auth-code-123"]
    assert form["redirect_uri"] == [TEST_GOOGLE_REDIRECT]
    assert form["client_secret"] == ["test-google-secret"]
    assert len(form["code_verifier"][0]) >= 43
    listed = (await client.get("/api/connections")).json()[0]
    assert (listed["connected"], listed["calendar"], listed["scopes"]) == (True, True, [CALENDAR_SCOPE])


async def test_callback_state_can_be_used_only_once(
    client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp
) -> None:
    fake_http.route("POST", TOKEN_URL, fixture_response("google_token_exchange.json"))
    state = (await start(client))["state"][0]
    await callback(client, code="auth-code-123", state=state)

    again = await callback(client, code="auth-code-123", state=state)

    assert again.headers["location"] == error_redirect("invalid_state")
    assert len(fake_http.sent("POST", TOKEN_URL)) == 1


async def test_callback_with_wrong_state_saves_nothing(
    client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    await start(client)

    response = await callback(client, code="auth-code-123", state="attacker-state")

    assert response.headers["location"] == error_redirect("invalid_state")
    assert fake_http.requests == []
    assert await stored(db_session, logged_in_user) is None


async def test_callback_without_start_is_invalid_state(
    client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp
) -> None:
    response = await callback(client, code="auth-code-123", state="anything")

    assert response.headers["location"] == error_redirect("invalid_state")


async def test_callback_user_denied(client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp) -> None:
    query = await start(client)

    response = await callback(client, error="access_denied", state=query["state"][0])

    assert response.headers["location"] == error_redirect("access_denied")
    assert fake_http.requests == []


async def test_callback_without_calendar_scope_saves_nothing(
    client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    """세부 동의 화면에서 캘린더 권한을 빼고 허용하면 저장하지 않고 받은 토큰은 철회한다."""
    fake_http.route("POST", TOKEN_URL, fixture_response("google_token_exchange_no_calendar.json"))
    fake_http.route("POST", REVOKE_URL, httpx.Response(200))
    query = await start(client)

    response = await callback(client, code="auth-code-123", state=query["state"][0])

    assert response.headers["location"] == error_redirect("scope_missing")
    assert await stored(db_session, logged_in_user) is None
    assert len(fake_http.sent("POST", REVOKE_URL)) == 1


async def test_callback_exchange_failure(
    client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp, db_session: AsyncSession
) -> None:
    fake_http.route("POST", TOKEN_URL, json_response(400, {"error": "invalid_grant"}))
    query = await start(client)

    response = await callback(client, code="expired-code", state=query["state"][0])

    assert response.headers["location"] == error_redirect("exchange_failed")
    assert await stored(db_session, logged_in_user) is None


async def test_callback_after_logout_is_rejected(
    client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp
) -> None:
    query = await start(client)
    await client.post("/api/auth/logout")

    response = await callback(client, code="auth-code-123", state=query["state"][0])

    assert response.headers["location"] == error_redirect("not_logged_in")
    assert fake_http.requests == []


async def test_start_without_encryption_key_is_not_available(
    client: httpx.AsyncClient, logged_in_user: User, fake_http: FakeHttp, test_settings: Settings
) -> None:
    """TOKEN_ENCRYPTION_KEY가 비어 있으면 토큰을 안전하게 저장할 수 없으므로 연결을 시작하지 않는다."""
    test_settings.token_encryption_key = SecretStr("")

    listed = (await client.get("/api/connections")).json()[0]
    response = await client.get("/api/connections/google/start")

    assert listed["available"] is False
    assert response.status_code == 302
    assert response.headers["location"] == error_redirect("not_configured")


@pytest.mark.parametrize("revoke_reply", [httpx.Response(200), httpx.Response(400), httpx.ConnectError("끊김")])
async def test_disconnect_revokes_and_deletes(
    client: httpx.AsyncClient,
    logged_in_user: User,
    fake_http: FakeHttp,
    db_session: AsyncSession,
    revoke_reply: httpx.Response | Exception,
) -> None:
    """Google 쪽 철회가 실패해도(이미 만료된 토큰 등) 우리 DB의 토큰은 지운다."""
    fake_http.route("POST", TOKEN_URL, fixture_response("google_token_exchange.json"))
    fake_http.route("POST", REVOKE_URL, revoke_reply)
    query = await start(client)
    await callback(client, code="auth-code-123", state=query["state"][0])

    response = await client.delete("/api/connections/google")

    assert response.status_code == 204
    assert await stored(db_session, logged_in_user) is None
    [revoke] = fake_http.sent("POST", REVOKE_URL)
    assert parse_qs(revoke.content.decode())["token"] == ["1//test-refresh-token"]


async def test_disconnect_unknown_provider_is_404(client: httpx.AsyncClient, logged_in_user: User) -> None:
    response = await client.delete("/api/connections/kakao")

    assert response.status_code == 404


async def test_disconnect_requires_origin(client: httpx.AsyncClient, logged_in_user: User) -> None:
    response = await client.delete("/api/connections/google", headers={"Origin": "http://evil.example"})

    assert response.status_code == 403
