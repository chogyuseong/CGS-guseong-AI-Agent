"""
Google OAuth 연결과 액세스 토큰 관리.

공식 문서(2026-11-16 확인): https://developers.google.com/identity/protocols/oauth2/web-server
- 처음 연결: google-auth-oauthlib의 Flow. access_type=offline이면 refresh token을 받는다(처음 동의 때 한 번).
- state를 세션에 저장해 콜백에서 확인한다(CSRF 방어).
- 라이브러리가 PKCE code_verifier를 자동으로 만든다(문서에는 없고 라이브러리 코드로 확인).
  콜백에서 같은 값을 넣어야 토큰 교환이 되므로 state와 함께 세션에 저장한다.
- 액세스 토큰은 약 1시간짜리다. 만료되면 refresh token으로 토큰 엔드포인트에 직접 요청해 갱신한다.
- 외부 사용자 유형 + 테스트 상태 앱의 refresh token은 7일 뒤 만료된다. 그때는 다시 연결하라고 안내한다.
"""

import os
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.utils import timezone
from google_auth_oauthlib.flow import Flow

from accounts import crypto
from accounts.models import Connection
from tools import http
from tools.http import ExternalAPIError

# include_granted_scopes로 예전에 허락한 범위가 함께 돌아오면 oauthlib가 경고 예외를 낸다. 그 검사를 끈다.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.events"
# 메일 읽기는 사용자가 '메일 읽기 권한 추가'를 눌렀을 때만 요청한다(명세). 보내기·수정 범위는 쓰지 않는다.
SCOPE_GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
SERVICE = "Google"
REFRESH_MARGIN = timedelta(minutes=2)  # 만료 직전 토큰으로 요청하다 실패하지 않게 미리 갱신한다
NOT_CONNECTED = "Google 계정이 연결되어 있지 않습니다. 설정 > 외부 서비스 연결에서 연결해 주세요."
RECONNECT = (
    "Google 연결이 만료되었습니다(테스트 상태 앱은 7일마다 만료됩니다). "
    "설정 > 외부 서비스 연결에서 다시 연결해 주세요."
)


def is_configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.GOOGLE_REDIRECT_URI)


def make_flow(scopes, state=None, code_verifier=None):
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=scopes,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )


def start(session, scopes):
    """동의 화면 주소를 만들고, 콜백에서 확인할 값을 세션에 저장한다."""
    flow = make_flow(scopes)
    url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["google_oauth"] = {"state": state, "code_verifier": flow.code_verifier, "scopes": scopes}
    return url


def exchange_code(code, *, state, code_verifier, scopes):
    """인가 코드를 토큰으로 바꾼다. 돌려주는 dict는 save_tokens가 그대로 저장한다."""
    flow = make_flow(scopes, state=state, code_verifier=code_verifier)
    try:
        flow.fetch_token(code=code, timeout=http.TIMEOUT_SECONDS)
    except Exception as error:  # oauthlib, requests 예외가 섞여 온다. 원문에는 코드가 들어 있어 쓰지 않는다
        raise ExternalAPIError("Google 인증을 마치지 못했습니다. 다시 연결해 주세요.") from error
    credentials = flow.credentials
    expiry = credentials.expiry.replace(tzinfo=UTC) if credentials.expiry else None
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "expiry": expiry.isoformat() if expiry else None,
        "scopes": list(credentials.granted_scopes or scopes),
    }


def save_tokens(user, tokens):
    """토큰을 암호화해 저장한다. 구글이 refresh token을 다시 주지 않으면 예전 것을 유지한다."""
    existing = Connection.objects.filter(user=user, provider=Connection.Provider.GOOGLE).first()
    if not tokens.get("refresh_token") and existing:
        tokens = {
            **tokens,
            "refresh_token": crypto.decrypt_json(existing.encrypted_token).get("refresh_token"),
        }
    connection, _ = Connection.objects.update_or_create(
        user=user,
        provider=Connection.Provider.GOOGLE,
        defaults={
            "encrypted_token": crypto.encrypt_json(tokens),
            "scopes": tokens.get("scopes") or [],
            "expires_at": _parse(tokens.get("expiry")),
        },
    )
    return connection


def has_scope(user, scope):
    connection = Connection.objects.filter(user=user, provider=Connection.Provider.GOOGLE).first()
    return bool(connection and scope in connection.scopes)


def access_token(user):
    """유효한 액세스 토큰을 돌려준다. 만료가 가까우면 갱신해서 저장한 뒤 돌려준다."""
    connection = Connection.objects.filter(user=user, provider=Connection.Provider.GOOGLE).first()
    if connection is None:
        raise ExternalAPIError(NOT_CONNECTED)
    tokens = crypto.decrypt_json(connection.encrypted_token)
    expiry = _parse(tokens.get("expiry"))
    if tokens.get("token") and expiry and expiry - REFRESH_MARGIN > timezone.now():
        return tokens["token"]
    return _refresh(user, tokens)


def _refresh(user, tokens):
    if not tokens.get("refresh_token"):
        raise ExternalAPIError(RECONNECT)
    try:
        data = http.request_json(
            SERVICE,
            "POST",
            TOKEN_URI,
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "refresh_token": tokens["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
    except ExternalAPIError as error:
        if error.status in (400, 401):  # invalid_grant: 만료되었거나 사용자가 권한을 해제함
            raise ExternalAPIError(RECONNECT, error.status) from error
        raise
    expiry = timezone.now() + timedelta(seconds=int(data.get("expires_in", 3600)))
    save_tokens(user, {**tokens, "token": data["access_token"], "expiry": expiry.isoformat()})
    return data["access_token"]


def disconnect(user):
    """연결을 해제한다. 구글 쪽 권한 철회는 최선을 다하되, 실패해도 저장된 토큰은 지운다."""
    connection = Connection.objects.filter(user=user, provider=Connection.Provider.GOOGLE).first()
    if connection is None:
        return
    try:
        tokens = crypto.decrypt_json(connection.encrypted_token)
        token = tokens.get("refresh_token") or tokens.get("token")
        if token:
            http.request_json(SERVICE, "POST", REVOKE_URI, params={"token": token})
    except ExternalAPIError:
        pass  # 이미 만료된 토큰이면 철회가 실패한다. 우리 쪽 기록을 지우는 것이 더 중요하다
    connection.delete()


def _parse(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
