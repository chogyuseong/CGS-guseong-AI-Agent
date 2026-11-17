"""외부 서비스 연결 API (4단계: Google). OAuth 처리는 app/google_auth.py.

화면의 '연결' 흐름:
    GET /api/connections                  → [{provider, available, connected, calendar, mail, scopes, connected_at}]
    POST /api/connections/google/start {scope?}  (4단계 보강: 상태를 만드므로 POST, Origin 검사)
        → 200 {authorization_url}. state는 DB(oauth_states)에 해시로, 로그인 세션·10분·한 번 사용에 묶고
          PKCE code_verifier는 암호화해 둔다. 화면이 그 주소로 이동한다
    Google → 브라우저 이동: GET /api/connections/google/callback?code=...&state=...
        → state 검증, 토큰 교환, 암호화 저장
        → 302 {APP_ORIGIN}/connections?google=connected
           또는 {APP_ORIGIN}/connections?google=error&reason=<코드>
'메일 읽기 권한 추가'(7단계):
    POST /api/connections/google/start {"scope": "mail"}
        → 200 {authorization_url}: 캘린더 + gmail.readonly, include_granted_scopes=true
    콜백 → 302 {APP_ORIGIN}/connections?google=connected&added=mail
           메일만 빼고 허용하면 캘린더 연결은 저장하고 ?google=error&reason=mail_scope_missing
    메일 권한만 따로 빼는 API는 없다. Google은 합친 권한을 한꺼번에만 철회한다(연결 해제 = 캘린더·메일 모두 철회).
'연결 해제':
    DELETE /api/connections/google        → 204 (Google 쪽 권한 철회를 시도하고, 실패해도 우리 DB의 토큰은 지운다)
        4단계 보강: 이 연결로 만든 대기 중 실행 계획(결정 전·승인 후 실행 전)은 cancelled(connection_removed)로 무효화

콜백 reason 코드: not_configured(서버 설정 없음), not_logged_in, access_denied(사용자가 거부),
google_error(그 밖의 거부),
invalid_state(state 불일치·만료·재사용·다른 로그인 세션), invalid_request(code 없음), exchange_failed(토큰 교환 실패),
scope_missing(캘린더 권한을 빼고 허용함), mail_scope_missing(메일 권한 추가에서 메일을 빼고 허용함).

콜백은 Google이 브라우저를 보내는 주소라 JSON 오류 대신 화면으로 리디렉션한다. 오류 내용(토큰, 응답 본문)은
주소에 넣지 않고 코드만 넣는다.
GOOGLE_REDIRECT_URI는 화면 주소를 거치는 {APP_ORIGIN}/api/connections/google/callback이어야 세션 쿠키가 함께 온다
(Next.js가 /api/*를 API로 넘긴다).
"""

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import google_auth
from app.agent.cancel import NOT_STARTED
from app.config import Settings
from app.db import DbSession
from app.google_auth import GoogleTokens
from app.models import ActionPlan, Operation, User
from app.models.enums import OperationStatus
from app.schemas.connections import ConnectionErrorOut, ConnectionOut, GoogleStartOut, GoogleStartRequest
from app.security import CurrentUser, session_from_request, token_cipher
from app.tools.http import ExternalApiError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connections", tags=["connections"])

CONNECTIONS_PAGE = "/connections"
MAIL_SCOPE_PARAM = "mail"  # POST /google/start {"scope": "mail"}, 콜백 성공 시 ?google=connected&added=mail
CONNECTION_REMOVED = "connection_removed"


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


NOT_CONFIGURED_RESPONSE: dict[int | str, dict[str, Any]] = {
    409: {"model": ConnectionErrorOut, "description": "detail.code가 not_configured(서버에 Google 설정이 없음)"}
}


@router.post("/google/start", responses=NOT_CONFIGURED_RESPONSE)
async def google_start(request: Request, session: DbSession, body: GoogleStartRequest | None = None) -> GoogleStartOut:
    """Google 동의 화면 주소를 만든다. state를 이 로그인 세션에 묶어 DB에 남긴다(만료 10분, 한 번 사용)."""
    settings: Settings = request.app.state.settings
    found = await session_from_request(request, session)
    if found is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    cipher = token_cipher(settings)
    if not google_auth.google_available(settings) or cipher is None:
        detail = {"code": "not_configured", "message": "서버에 Google 연결 설정이 없습니다."}
        raise HTTPException(status.HTTP_409_CONFLICT, detail=detail)
    user, login_session = found
    add_mail = body is not None and body.scope == MAIL_SCOPE_PARAM
    url, state, verifier = google_auth.new_authorization(settings, add_mail=add_mail)
    await google_auth.save_state(
        session,
        user_id=user.id,
        session_id=login_session.id,
        state=state,
        verifier=verifier,
        add_mail=add_mail,
        cipher=cipher,
    )
    await session.commit()
    return GoogleStartOut(authorization_url=url)


async def callback_problem(
    request: Request, session: DbSession, settings: Settings
) -> tuple[str | None, User | None, google_auth.PendingAuthorization | None]:
    """콜백 요청을 검사해 (오류 코드, 사용자, 인증 요청)을 돌려준다. state는 여기서 한 번 쓰이고 끝난다."""
    params = request.query_params
    found = await session_from_request(request, session)
    if found is None:
        return "not_logged_in", None, None
    user, login_session = found
    cipher = token_cipher(settings)
    pending = None
    if cipher is not None:
        pending = await google_auth.consume_state(
            session, user_id=user.id, session_id=login_session.id, state=params.get("state"), cipher=cipher
        )
        await session.commit()
    if params.get("error"):
        return ("access_denied" if params.get("error") == "access_denied" else "google_error"), user, None
    if pending is None:
        return "invalid_state", user, None
    if not google_auth.google_available(settings):
        return "not_configured", user, None
    if not params.get("code"):
        return "invalid_request", user, None
    return None, user, pending


@router.get("/google/callback", response_class=RedirectResponse, status_code=status.HTTP_302_FOUND)
async def google_callback(request: Request, session: DbSession) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    problem, user, pending = await callback_problem(request, session, settings)
    if problem is not None or user is None or pending is None:
        return error_redirect(settings, problem or "invalid_state")
    client = request.app.state.http_client
    try:
        tokens = await google_auth.exchange_code(client, settings, request.query_params["code"], pending.verifier)
    except ExternalApiError:
        logger.warning("Google 토큰 교환 실패: user=%s", user.id)
        return error_redirect(settings, "exchange_failed")
    if not tokens.has_calendar:
        await google_auth.revoke(client, tokens.access_token)
        return error_redirect(settings, "scope_missing")
    await store(session, user.id, tokens, settings)
    return finished_redirect(settings, tokens, wanted_mail=pending.add_mail)


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
    row = await google_auth.find_row(session, user.id)
    if row is not None:
        await invalidate_pending_plans(session, user.id, row.id)
    await google_auth.delete_connection(session, user.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def invalidate_pending_plans(session: AsyncSession, user_id: int, connection_id: int) -> None:
    """이 연결로 만든 실행 계획 중 아직 실행하지 않은 것을 무효화한다(BUILD_SPEC 10.5). 커밋은 호출한 쪽."""
    plan_ids = select(ActionPlan.id).where(ActionPlan.user_id == user_id, ActionPlan.connection_id == connection_id)
    await session.execute(
        update(Operation)
        .where(Operation.plan_id.in_(plan_ids), Operation.status.in_(NOT_STARTED))
        .values(status=OperationStatus.CANCELLED, error_code=CONNECTION_REMOVED)
    )
