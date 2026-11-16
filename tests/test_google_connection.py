"""4단계: Google OAuth 연결. 토큰은 암호화해 저장하고, state와 PKCE 값을 세션으로 확인한다."""

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from django.utils import timezone

from tools.http import ExternalAPIError

pytestmark = pytest.mark.django_db
SCOPE = "https://www.googleapis.com/auth/calendar.events"


@pytest.fixture(autouse=True)
def google_settings(settings):
    settings.TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
    settings.GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
    settings.GOOGLE_CLIENT_SECRET = "test-client-secret"
    settings.GOOGLE_REDIRECT_URI = "http://localhost:8000/connections/google/callback/"
    return settings


@pytest.fixture
def google_api(monkeypatch):
    """구글 토큰 서버 대신 응답한다."""
    sent = []

    def install(*responses):
        queue = list(responses)

        def handler(request):
            sent.append(request)
            status, body = queue.pop(0)
            return httpx.Response(status, json=body)

        monkeypatch.setattr(
            "tools.http.make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
        )
        return sent

    return install


def connect(user, *, token="access-1", refresh="refresh-1", expires_in=3600):
    from accounts import google

    return google.save_tokens(
        user,
        {
            "token": token,
            "refresh_token": refresh,
            "expiry": (timezone.now() + timedelta(seconds=expires_in)).isoformat(),
            "scopes": [SCOPE],
        },
    )


# ── 암호화 ───────────────────────────────────────────────


def test_tokens_are_encrypted_at_rest(user):
    from accounts.models import Connection

    connect(user, token="plain-access-token", refresh="plain-refresh-token")
    stored = Connection.objects.get(user=user).encrypted_token

    assert "plain-access-token" not in stored
    assert "plain-refresh-token" not in stored


def test_encrypt_roundtrip_and_missing_key(settings):
    from accounts import crypto

    secret = {"token": "abc"}
    assert crypto.decrypt_json(crypto.encrypt_json(secret)) == secret
    settings.TOKEN_ENCRYPTION_KEY = ""
    with pytest.raises(ExternalAPIError, match="TOKEN_ENCRYPTION_KEY"):
        crypto.encrypt_json(secret)


def test_scopes_are_stored_in_plain_column_for_checks(user):
    from accounts import google

    connect(user)
    assert google.has_scope(user, SCOPE) is True
    assert google.has_scope(user, "https://www.googleapis.com/auth/gmail.readonly") is False


# ── 액세스 토큰과 갱신 ───────────────────────────────────


def test_valid_token_is_used_without_refresh(user, google_api):
    from accounts import google

    sent = google_api()
    connect(user, token="still-valid")
    assert google.access_token(user) == "still-valid"
    assert sent == []


def test_expired_token_is_refreshed_and_saved(user, google_api):
    from accounts import google

    sent = google_api((200, {"access_token": "new-access", "expires_in": 3599, "token_type": "Bearer"}))
    connect(user, token="old-access", expires_in=-10)

    assert google.access_token(user) == "new-access"
    form = parse_qs(sent[0].content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["refresh-1"]
    assert str(sent[0].url) == "https://oauth2.googleapis.com/token"
    assert google.access_token(user) == "new-access"  # 저장됐으므로 다시 갱신하지 않는다
    assert len(sent) == 1


def test_refresh_keeps_refresh_token_when_google_omits_it(user, google_api):
    from accounts import crypto
    from accounts.models import Connection

    google_api((200, {"access_token": "new-access", "expires_in": 3599}))
    connect(user, expires_in=-10)

    from accounts import google

    google.access_token(user)
    data = crypto.decrypt_json(Connection.objects.get(user=user).encrypted_token)
    assert data["refresh_token"] == "refresh-1"


def test_revoked_or_expired_refresh_token_asks_to_reconnect(user, google_api):
    """테스트 상태 앱은 refresh token이 7일 뒤 만료된다(구글 공식 문서). 다시 연결하라고 안내한다."""
    from accounts import google

    google_api((400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
    connect(user, expires_in=-10)

    with pytest.raises(ExternalAPIError, match="다시 연결"):
        google.access_token(user)


def test_not_connected(user):
    from accounts import google

    with pytest.raises(ExternalAPIError, match="연결"):
        google.access_token(user)


# ── OAuth 시작과 콜백 ───────────────────────────────────


def test_start_redirects_to_google_with_offline_access_and_pkce(logged_in_client):
    response = logged_in_client.get("/connections/google/start/")

    assert response.status_code == 302
    url = urlparse(response["Location"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert query["access_type"] == ["offline"]
    assert query["scope"] == [SCOPE]
    assert query["redirect_uri"] == ["http://localhost:8000/connections/google/callback/"]
    assert query["code_challenge_method"] == ["S256"]
    saved = logged_in_client.session["google_oauth"]
    assert saved["state"] == query["state"][0]
    assert saved["code_verifier"]  # 콜백에서 다시 넣어야 토큰 교환이 된다


def test_callback_with_wrong_state_is_rejected(logged_in_client, user):
    from accounts.models import Connection

    logged_in_client.get("/connections/google/start/")
    response = logged_in_client.get("/connections/google/callback/?state=forged&code=abc")

    assert response.status_code == 400
    assert not Connection.objects.filter(user=user).exists()


def test_callback_exchanges_code_with_saved_verifier(logged_in_client, user, monkeypatch):
    from accounts import google
    from accounts.models import Connection

    logged_in_client.get("/connections/google/start/")
    saved = dict(logged_in_client.session["google_oauth"])
    seen = {}

    def fake_exchange(code, *, state, code_verifier, scopes):
        seen.update(code=code, state=state, code_verifier=code_verifier)
        return {
            "token": "access-x",
            "refresh_token": "refresh-x",
            "expiry": (timezone.now() + timedelta(hours=1)).isoformat(),
            "scopes": scopes,
        }

    monkeypatch.setattr(google, "exchange_code", fake_exchange)
    response = logged_in_client.get(f"/connections/google/callback/?state={saved['state']}&code=the-code")

    assert response.status_code == 302
    assert seen == {"code": "the-code", "state": saved["state"], "code_verifier": saved["code_verifier"]}
    assert Connection.objects.get(user=user).scopes == [SCOPE]
    assert "google_oauth" not in logged_in_client.session  # 한 번 쓴 state는 지운다


def test_callback_when_user_denies(logged_in_client, user):
    from accounts.models import Connection

    logged_in_client.get("/connections/google/start/")
    state = logged_in_client.session["google_oauth"]["state"]
    response = logged_in_client.get(f"/connections/google/callback/?state={state}&error=access_denied")

    assert response.status_code == 302
    assert not Connection.objects.filter(user=user).exists()


def test_disconnect_deletes_connection_and_revokes(logged_in_client, user, google_api):
    from accounts.models import Connection

    sent = google_api((200, {}))
    connect(user)
    response = logged_in_client.post("/connections/google/disconnect/")

    assert response.status_code == 302
    assert not Connection.objects.filter(user=user).exists()
    assert str(sent[0].url).startswith("https://oauth2.googleapis.com/revoke")


def test_disconnect_still_deletes_when_revoke_fails(logged_in_client, user, google_api):
    from accounts.models import Connection

    google_api((400, {"error": "invalid_token"}))
    connect(user)
    logged_in_client.post("/connections/google/disconnect/")
    assert not Connection.objects.filter(user=user).exists()


# ── 연결 화면 ───────────────────────────────────────────


def test_connections_page_shows_state(logged_in_client, user):
    page = logged_in_client.get("/connections/").content.decode()
    assert "연결하기" in page

    connect(user)
    page = logged_in_client.get("/connections/").content.decode()
    assert "연결됨" in page
    assert "다시 연결" in page
    assert "연결 해제" in page
    assert "access-1" not in page and "refresh-1" not in page  # 토큰은 화면에 내보내지 않는다


def test_connections_page_warns_when_google_is_not_configured(logged_in_client, settings):
    settings.GOOGLE_CLIENT_ID = ""
    page = logged_in_client.get("/connections/").content.decode()
    assert "GOOGLE_CLIENT_ID" in page


def test_connection_views_require_login(client):
    assert client.get("/connections/").status_code == 302
    assert client.get("/connections/google/start/").status_code == 302


def test_token_never_in_logs(user, google_api, caplog):
    from accounts import google

    google_api((400, {"error": "invalid_grant"}))
    connect(user, token="secret-access-zzz", refresh="secret-refresh-zzz", expires_in=-10)
    with pytest.raises(ExternalAPIError):
        google.access_token(user)
    logged = json.dumps([r.getMessage() for r in caplog.records], ensure_ascii=False)
    assert "secret-access-zzz" not in logged and "secret-refresh-zzz" not in logged
