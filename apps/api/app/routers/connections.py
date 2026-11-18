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
7단계 보강:
    PUT /api/connections/google/mail-consent    → 204 메일 데이터 처리 동의(users.mail_consent_at)
    DELETE /api/connections/google/mail-consent → 204 동의 철회. 동의 전에는 메일 도구가 Gmail을 부르지 않는다
    메일 기능 플래그(PAYLOAD_ENCRYPTION_KEY)가 꺼져 있으면 start {"scope": "mail"}은 409 mail_disabled
    토큰이 만료·철회되면(invalid_grant) 연결 행은 남기고 reconnect_required=true로 보인다
    (google_auth.mark_reconnect_required)

콜백 reason 코드: not_configured(서버 설정 없음), not_logged_in, access_denied(사용자가 거부),
google_error(그 밖의 거부),
invalid_state(state 불일치·만료·재사용·다른 로그인 세션), invalid_request(code 없음), exchange_failed(토큰 교환 실패),
scope_missing(캘린더 권한을 빼고 허용함), mail_scope_missing(메일 권한 추가에서 메일을 빼고 허용함).

콜백은 Google이 브라우저를 보내는 주소라 JSON 오류 대신 화면으로 리디렉션한다. 오류 내용(토큰, 응답 본문)은
주소에 넣지 않고 코드만 넣는다.
GOOGLE_REDIRECT_URI는 화면 주소를 거치는 {APP_ORIGIN}/api/connections/google/callback이어야 세션 쿠키가 함께 온다
(Next.js가 /api/*를 API로 넘긴다).

인증·소유자: callback을 뺀 모든 경로는 로그인 필요(start는 CurrentUser 대신 session_from_request로
로그인 세션 행까지 읽어 state를 그 세션에 묶는다). 모든 조회·삭제는 user.id 기준이다.
콜백은 GET이라 Origin 검사가 없고, 대신 state(로그인 세션·10분·한 번 사용, PKCE)가
다른 사람의 code를 내 계정에 붙이는 공격(OAuth CSRF)을 막는다.
근거: BUILD_SPEC 9.3(OAuth 시작은 POST), DECISIONS 2026-11-17(범위 calendar.events.owned, 토큰 암호화 키 없으면 막기,
연결 해제는 철회 실패해도 DB 토큰 삭제, 해제 시 대기 계획 무효화, POST start 전환, 메일 동의 users.mail_consent_at).

고칠 때 주의:
- 경로·응답 모양·오류 code를 바꾸면 gen:api 후 apps/web/lib/connections.ts(목록·start·해제·mail-consent)를 고친다.
- 콜백의 리디렉션 쿼리(google=connected|error, reason, added=mail)는 OpenAPI에 드러나지 않는다. 바꾸면 연결 화면
  (apps/web의 /connections 페이지)이 읽는 쿼리를 직접 찾아 함께 고친다.
- 연결 해제가 대기 계획을 cancelled로 바꾸는 부분은 operation 상태 전이다(docs/STATE_MACHINES.md).
- 관련 테스트: tests/test_google_connection.py, test_google_reconnect.py, test_mail_privacy.py, test_openapi.py.
"""

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, update

from app import google_auth
from app.agent.data_policy import mail_enabled
from app.config import Settings
from app.db import DbSession
from app.google_auth import GoogleTokens
from app.models import User
from app.schemas.connections import ConnectionErrorOut, ConnectionOut, GoogleStartOut, GoogleStartRequest
from app.security import CurrentUser, session_from_request, token_cipher
from app.tools.http import ExternalApiError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connections", tags=["connections"])

# 콜백 뒤 돌아갈 화면 경로(apps/web의 연결 화면). 화면 경로를 옮기면 같이 바꾼다.
CONNECTIONS_PAGE = "/connections"
MAIL_SCOPE_PARAM = "mail"  # POST /google/start {"scope": "mail"}, 콜백 성공 시 ?google=connected&added=mail


def page_redirect(settings: Settings, **params: str) -> RedirectResponse:
    """연결 화면으로 302. 목적지는 설정의 APP_ORIGIN으로 고정한다.

    요청에서 받은 주소로 보내지 않으므로 열린 리디렉션(공격자 사이트로 보내기)이 생기지 않는다.
    params에는 고정 코드만 넣는다(토큰·Google 응답 본문은 주소·로그에 남지 않게).
    """
    url = f"{settings.app_origin.rstrip('/')}{CONNECTIONS_PAGE}?{urlencode(params)}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


def error_redirect(settings: Settings, reason: str) -> RedirectResponse:
    """?google=error&reason=<코드>. reason은 이 파일 머리의 '콜백 reason 코드' 목록 중 하나여야 한다."""
    return page_redirect(settings, google="error", reason=reason)


# 연결 상태(지금은 google 한 줄). 읽기만 한다. 토큰은 응답에 넣지 않는다.
# 다시 연결이 필요한 행(invalid_grant)은 connected·calendar·mail을 모두 false로, reconnect_required=true로 보인다.
@router.get("")
async def list_connections(request: Request, user: CurrentUser, session: DbSession) -> list[ConnectionOut]:
    settings: Settings = request.app.state.settings
    row = await google_auth.find_row(session, user.id)
    scopes = list(row.scopes or []) if row else []
    usable = google_auth.usable(row)  # 7단계 보강: 다시 연결이 필요하면 연결·권한 모두 없음으로 보인다
    return [
        ConnectionOut(
            provider="google",
            available=google_auth.google_available(settings),
            connected=usable,
            calendar=usable and google_auth.CALENDAR_SCOPE in scopes,
            mail=usable and google_auth.GMAIL_SCOPE in scopes,
            scopes=scopes,
            # 6단계: 처음 연결한 시각(행을 만든 시각). 토큰 갱신(updated_at)으로는 바뀌지 않는다.
            # 연결된 채로 [다시 연결]하면 같은 행을 고치므로 그대로, 연결 해제 뒤 다시 연결하면 새 시각이다.
            connected_at=row.created_at if row else None,
            reconnect_required=row is not None and not usable,
            mail_enabled=mail_enabled(settings),
            mail_consent=user.mail_consent_at is not None,
        )
    ]


# 메일 동의(커밋). mail_consent_at IS NULL 조건으로 처음 동의 시각을 지킨다(두 번 눌러도 시각이 바뀌지 않음).
# 동의는 Google 권한과 별개다: 도구는 플래그 → 동의 순으로 확인한다(app/tools/gmail.py mail_refusal).
# 아래 docstring은 OpenAPI 설명이다.
@router.put("/google/mail-consent", status_code=status.HTTP_204_NO_CONTENT)
async def give_mail_consent(user: CurrentUser, session: DbSession) -> Response:
    """7단계 보강: 메일 데이터 처리 동의.

    메일 내용이 답변을 만들려고 AI 모델 제공자에게 전송됨을 화면에서 알린 뒤 받는다.
    같은 요청을 다시 보내도 처음 동의 시각을 바꾸지 않는다.
    """
    await session.execute(
        update(User).where(User.id == user.id, User.mail_consent_at.is_(None)).values(mail_consent_at=func.now())
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# 동의 철회(커밋). 이미 철회한 상태여도 204. 아래 docstring은 OpenAPI 설명이다.
@router.delete("/google/mail-consent", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw_mail_consent(user: CurrentUser, session: DbSession) -> Response:
    """동의 철회. 이후 메일 도구는 Gmail을 부르지 않는다. Google 쪽 메일 권한은 그대로다(연결 해제는 따로)."""
    await session.execute(update(User).where(User.id == user.id).values(mail_consent_at=None))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# OpenAPI에 409 본문 모양(ConnectionErrorOut)을 드러내는 표. description을 바꾸면 gen:api를 다시 돌린다.
NOT_CONFIGURED_RESPONSE: dict[int | str, dict[str, Any]] = {
    409: {
        "model": ConnectionErrorOut,
        "description": "detail.code가 not_configured(서버에 Google 설정이 없음) 또는 mail_disabled(메일 기능 꺼짐)",
    }
}


# 연결 시작. 401(로그인 없음) / 409 not_configured(Google 설정 또는 TOKEN_ENCRYPTION_KEY 없음: 토큰을 평문으로
# 저장하지 않으려고 연결 자체를 막는다) / 409 mail_disabled. 성공하면 oauth_states 행을 커밋한다.
# POST라 Origin 검사를 거친다(다른 사이트가 내 브라우저로 state를 만들게 하지 못한다).
# 아래 docstring은 OpenAPI 설명이다.
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
    if add_mail and not mail_enabled(settings):
        # 7단계 보강: 메일 기록을 암호화할 키가 없으면 메일 권한을 받지 않는다(기능 플래그).
        detail = {"code": "mail_disabled", "message": "서버에서 메일 기능이 꺼져 있습니다."}
        raise HTTPException(status.HTTP_409_CONFLICT, detail=detail)
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
    """콜백 요청을 검사해 (오류 코드, 사용자, 인증 요청)을 돌려준다. state는 여기서 한 번 쓰이고 끝난다.

    오류 코드가 None이면 사용자·인증 요청이 모두 있다. 검사 순서에 뜻이 있다:
    - state 소비(consume_state + 커밋)를 Google의 error 확인보다 먼저 한다. 거부·오류로 돌아온 state도 다시 못 쓴다.
    - state가 맞지 않으면(invalid_state) Google에 아무 요청도 보내지 않는다
      (남이 만든 code를 내 계정에 붙이는 공격 방지).
    - 토큰 암호화 키가 없으면 state를 읽지 않아 pending이 None이다(Google 오류가 없으면 invalid_state).
    부수 효과: oauth_states 행 소비를 커밋한다.
    """
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


# Google이 브라우저를 보내는 콜백. 언제나 302로 연결 화면에 돌아간다(JSON 오류를 내지 않는다).
# 순서: 요청 검사(state 소비) → 토큰 교환(PKCE verifier) → 캘린더 권한 확인 → 암호화 저장(커밋) → 화면으로.
# 캘린더 권한을 빼고 허용하면 받은 토큰을 바로 철회하고 저장하지 않는다(쓸 수 없는 토큰을 쥐고 있지 않게).
# 교환 실패 로그에는 사용자 id만 남긴다(응답 본문·토큰 금지).
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
    """받은 토큰을 암호화해 저장하고 커밋한다. 이 사용자의 연결 행 하나를 만들거나 고친다(google_auth.save_tokens).

    Google은 다시 연결할 때 refresh token을 주지 않을 수 있다. 그때 이전 refresh token을 이어 쓰지 않으면
    access token이 만료되는 순간 연결이 끊긴다.
    """
    cipher = token_cipher(settings)
    assert cipher is not None  # google_available에서 이미 확인했다
    previous = await google_auth.load_tokens_or_none(session, user_id, cipher)
    if tokens.refresh_token is None and previous is not None:
        # Google이 refresh token을 다시 주지 않은 경우 이전 것을 이어 쓴다
        tokens = GoogleTokens(tokens.access_token, previous.refresh_token, tokens.expires_at, tokens.scopes)
    await google_auth.save_tokens(session, user_id, tokens, cipher)
    await session.commit()


# 연결 해제. provider가 google이 아니면 404. 연결이 없어도 204(여러 번 눌러도 같은 결과).
# 순서: Google 쪽 철회 시도(실패해도 계속) → 이 연결로 만든 대기 실행 계획 무효화(connection_removed) → 행 삭제 → 커밋.
# (BUILD_SPEC 10.5) 무효화를 빼면 해제 뒤에도 이미 받은 승인으로 외부 실행이 시도된다. 대화·작업 기록은 지우지 않는다.
# 메일 동의(users.mail_consent_at)는 여기서 지우지 않는다.
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
        await google_auth.invalidate_pending_plans(session, user.id, row.id, google_auth.CONNECTION_REMOVED)
    await google_auth.delete_connection(session, user.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
