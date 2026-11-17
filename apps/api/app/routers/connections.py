"""외부 서비스 연결 API (4단계: Google). OAuth 처리는 app/google_auth.py.

화면의 '연결' 흐름 (화면은 링크로 이동만 한다. fetch로 부르지 않는다):
    GET /api/connections                  → [{provider, available, connected, calendar, mail, scopes, connected_at}]
    브라우저 이동: GET /api/connections/google/start
        → 302 Google 동의 화면 (state·PKCE code_verifier를 세션 쿠키에 저장)
    Google → 브라우저 이동: GET /api/connections/google/callback?code=...&state=...
        → state 검증, 토큰 교환, 암호화 저장
        → 302 {APP_ORIGIN}/connections?google=connected
           또는 {APP_ORIGIN}/connections?google=error&reason=<코드>
'메일 읽기 권한 추가'(7단계):
    브라우저 이동: GET /api/connections/google/start?scope=mail
        → 302 Google 동의 화면(캘린더 + gmail.readonly, include_granted_scopes=true)
    콜백 → 302 {APP_ORIGIN}/connections?google=connected&added=mail
           메일만 빼고 허용하면 캘린더 연결은 저장하고 ?google=error&reason=mail_scope_missing
    메일 권한만 따로 빼는 API는 없다. Google은 합친 권한을 한꺼번에만 철회한다(연결 해제 = 캘린더·메일 모두 철회).
'연결 해제':
    DELETE /api/connections/google        → 204 (Google 쪽 권한 철회를 시도하고, 실패해도 우리 DB의 토큰은 지운다)

reason 코드: not_configured(서버 설정 없음), not_logged_in, access_denied(사용자가 거부), google_error(그 밖의 거부),
invalid_state(state 불일치·만료·재사용), invalid_request(code 없음, 모르는 scope 값), exchange_failed(토큰 교환 실패),
scope_missing(캘린더 권한을 빼고 허용함), mail_scope_missing(메일 권한 추가에서 메일을 빼고 허용함).

콜백은 Google이 브라우저를 보내는 주소라 JSON 오류 대신 화면으로 리디렉션한다. 오류 내용(토큰, 응답 본문)은
주소에 넣지 않고 코드만 넣는다.
GOOGLE_REDIRECT_URI는 화면 주소를 거치는 {APP_ORIGIN}/api/connections/google/callback이어야 세션 쿠키가 함께 온다
(Next.js가 /api/*를 API로 넘긴다).
"""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app import google_auth
from app.config import Settings
from app.db import DbSession
from app.google_auth import GoogleTokens
from app.models import User
from app.schemas.connections import ConnectionOut
from app.security import CurrentUser, token_cipher, user_from_request
from app.tools.http import ExternalApiError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connections", tags=["connections"])

CONNECTIONS_PAGE = "/connections"
MAIL_SCOPE_PARAM = "mail"  # GET /google/start?scope=mail, 콜백 성공 시 ?google=connected&added=mail


def page_redirect(settings: Settings, **params: str) -> RedirectResponse:
    url = f"{settings.app_origin.rstrip('/')}{CONNECTIONS_PAGE}?{urlencode(params)}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


def error_redirect(settings: Settings, reason: str) -> RedirectResponse:
    return page_redirect(settings, google="error", reason=reason)


@router.get("")
async def list_connections(request: Request, user: CurrentUser, session: DbSession) -> list[ConnectionOut]:
    settings: Settings = request.app.state.settings
    row = await google_auth.find_row(session, user.id)
    scopes = list(row.scopes or []) if row else []
    return [
        ConnectionOut(
            provider="google",
            available=google_auth.google_available(settings),
            connected=row is not None,
            calendar=google_auth.CALENDAR_SCOPE in scopes,
            mail=google_auth.GMAIL_SCOPE in scopes,
            scopes=scopes,
            # 6단계: 처음 연결한 시각(행을 만든 시각). 토큰 갱신(updated_at)으로는 바뀌지 않는다.
            # 연결된 채로 [다시 연결]하면 같은 행을 고치므로 그대로, 연결 해제 뒤 다시 연결하면 새 시각이다.
            connected_at=row.created_at if row else None,
        )
    ]


@router.get("/google/start", response_class=RedirectResponse, status_code=status.HTTP_302_FOUND)
async def google_start(request: Request, session: DbSession, scope: str | None = None) -> RedirectResponse:
    """화면이 링크 이동으로 여는 주소라 로그인하지 않았으면 JSON 401 대신 연결 화면으로 돌려보낸다(6단계).

    scope=mail이면 '메일 읽기 권한 추가'(7단계): 캘린더 + gmail.readonly를 요청한다. 다른 값은 invalid_request.
    """
    settings: Settings = request.app.state.settings
    if await session_user(request, session) is None:
        return error_redirect(settings, "not_logged_in")
    if scope not in (None, MAIL_SCOPE_PARAM):
        return error_redirect(settings, "invalid_request")
    if not google_auth.google_available(settings):
        return error_redirect(settings, "not_configured")
    url, pending = google_auth.new_authorization(settings, add_mail=scope == MAIL_SCOPE_PARAM)
    request.session[google_auth.SESSION_KEY] = pending
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


async def session_user(request: Request, session: DbSession) -> User | None:
    """로그인하지 않았으면 401 대신 None(콜백은 화면으로 리디렉션해야 한다)."""
    return await user_from_request(request, session)


def callback_problem(
    request: Request, user: User | None, settings: Settings, pending: object
) -> tuple[str | None, str | None]:
    """콜백 요청을 검사해 (오류 코드, code_verifier)를 돌려준다. pending은 세션에서 꺼낸(지운) 인증 요청이다."""
    params = request.query_params
    if user is None:
        return "not_logged_in", None
    verifier = google_auth.verifier_for(pending, params.get("state"))
    if params.get("error"):
        return ("access_denied" if params.get("error") == "access_denied" else "google_error"), None
    if verifier is None:
        return "invalid_state", None
    if not google_auth.google_available(settings):
        return "not_configured", None
    if not params.get("code"):
        return "invalid_request", None
    return None, verifier


@router.get("/google/callback", response_class=RedirectResponse, status_code=status.HTTP_302_FOUND)
async def google_callback(request: Request, session: DbSession) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    user = await session_user(request, session)
    pending = request.session.pop(google_auth.SESSION_KEY, None)  # state는 한 번만 쓴다
    problem, verifier = callback_problem(request, user, settings, pending)
    if problem is not None or user is None or verifier is None:
        return error_redirect(settings, problem or "invalid_state")
    client = request.app.state.http_client
    try:
        tokens = await google_auth.exchange_code(client, settings, request.query_params["code"], verifier)
    except ExternalApiError:
        logger.warning("Google 토큰 교환 실패: user=%s", user.id)
        return error_redirect(settings, "exchange_failed")
    if not tokens.has_calendar:
        await google_auth.revoke(client, tokens.access_token)
        return error_redirect(settings, "scope_missing")
    await store(session, user.id, tokens, settings)
    return finished_redirect(settings, tokens, wanted_mail=google_auth.requested_mail(pending))


def finished_redirect(settings: Settings, tokens: GoogleTokens, *, wanted_mail: bool) -> RedirectResponse:
    """저장 뒤 화면으로. 메일을 요청했는데 세부 동의에서 메일만 빼면 캘린더 연결은 남기고 mail_scope_missing을 알린다.

    받은 토큰을 철회하지 않는다: 합친 권한의 철회는 캘린더 권한까지 함께 없앤다(공식 문서).
    """
    if not wanted_mail:
        return page_redirect(settings, google="connected")
    if not tokens.has_mail:
        return error_redirect(settings, "mail_scope_missing")
    return page_redirect(settings, google="connected", added=MAIL_SCOPE_PARAM)


async def store(session: DbSession, user_id: int, tokens: GoogleTokens, settings: Settings) -> None:
    cipher = token_cipher(settings)
    assert cipher is not None  # google_available에서 이미 확인했다
    previous = await google_auth.load_tokens_or_none(session, user_id, cipher)
    if tokens.refresh_token is None and previous is not None:
        # Google이 refresh token을 다시 주지 않은 경우 이전 것을 이어 쓴다
        tokens = GoogleTokens(tokens.access_token, previous.refresh_token, tokens.expires_at, tokens.scopes)
    await google_auth.save_tokens(session, user_id, tokens, cipher)
    await session.commit()


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(provider: str, request: Request, user: CurrentUser, session: DbSession) -> Response:
    if provider != google_auth.PROVIDER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="알 수 없는 연결입니다.")
    cipher = token_cipher(request.app.state.settings)
    tokens = await google_auth.load_tokens_or_none(session, user.id, cipher) if cipher else None
    if tokens is not None:
        await google_auth.revoke(request.app.state.http_client, tokens.refresh_token or tokens.access_token)
    await google_auth.delete_connection(session, user.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
