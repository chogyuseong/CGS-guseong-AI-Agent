"""Google OAuth: 계정 연결(인증 시작·콜백·해제)과 토큰 보관·갱신. 라우터(connections)와 캘린더 도구(gcal)가 쓴다.

공식 문서(2026-11-17 확인, docs/TOOLS.md): https://developers.google.com/identity/protocols/oauth2/web-server
- 동의 화면:   GET  https://accounts.google.com/o/oauth2/v2/auth  (client_id, redirect_uri, response_type=code,
               scope, access_type=offline, state, prompt=consent, code_challenge, code_challenge_method=S256)
- 토큰 교환:   POST https://oauth2.googleapis.com/token  (grant_type=authorization_code, code, code_verifier, ...)
- 토큰 갱신:   POST 같은 주소 (grant_type=refresh_token). 실패하면 400 invalid_grant → 다시 연결해야 한다.
               외부 사용자 + '테스트' 게시 상태 앱의 refresh token은 7일 뒤 만료된다(공식 문서).
- 권한 철회:   POST https://oauth2.googleapis.com/revoke  (token)

google-auth-oauthlib 대신 httpx로 REST를 직접 부른다: 타임아웃·오류 문장·키 숨기기를 다른 외부 도구와 같은 곳
(app/tools/http.py)에서 지키고, 가짜 HTTP로 콜백·갱신·실패를 테스트하기 쉽다(docs/DECISIONS.md 4단계).

보안:
- state(CSRF 방어)와 PKCE code_verifier는 4단계 처음에는 서명된 세션 쿠키에 넣었다. 4단계 보강(DECISIONS 2026-11-17,
  BUILD_SPEC 10.5)부터는 DB oauth_states에 둔다: state는 SHA-256 해시, verifier는 Fernet 암호문, 로그인 세션 행·
  만료 10분·한 번 사용(used_at, 행 잠금)에 묶는다(save_state, consume_state).
- 토큰(access, refresh, 만료 시각)은 JSON 통째로 Fernet 암호화해 connections.encrypted_token에 둔다.
  범위(scopes)와 만료 시각만 평문 칸에 둔다(권한 확인은 복호화 없이).
- 토큰은 로그, 오류 문장, 모델, 화면 어디에도 넣지 않는다.
- 갱신은 연결 행을 FOR UPDATE로 잠근 채 한다(4단계 보강, 동시 갱신 충돌 방지, refresh_locked).
- 7단계 보강: 갱신이 invalid_grant(만료·철회)면 행을 지우지 않고 "다시 연결 필요"로 표시하고 토큰을 비우며,
  그 연결로 만든 대기 중 계획을 connection_revoked로 무효화한다(mark_reconnect_required).

진입점
- app/routers/connections.py: 시작(new_authorization·save_state), 콜백(consume_state·exchange_code·save_tokens),
  상태(find_row·usable), 해제(revoke·invalidate_pending_plans·delete_connection).
- app/tools/gcal.py·gmail.py: access_token(401이면 force_refresh로 한 번 더), gmail은 범위 부족 403에 drop_scope.
- app/agent/executor.py: CONNECTION_REMOVED·CONNECTION_REVOKED 이유를 안내 문장으로 바꾼다.

고칠 때 주의
- TOKEN_ENCRYPTION_KEY를 바꾸거나 잃으면 저장된 토큰과 진행 중 OAuth 시작 정보를 풀 수 없다. 사용자는 다시 연결해야
  하고 그 연결의 대기 계획은 무효가 된다(docs/OPERATIONS.md 4절, docs/DATA_POLICY.md "키 관리").
- CONNECTION_REMOVED·CONNECTION_REVOKED 문자열은 operations.error_code에 저장되고 executor.py와 글자로 맞물린다.
- 범위(CALENDAR_SCOPE·GMAIL_SCOPE)를 바꾸면 Google Cloud 동의 화면 설정과 docs/TOOLS.md, 화면의 권한 표시도 바꾼다.
- 안내 문구(*_MESSAGE)는 테스트가 그대로 비교한다.
- 관련 테스트: tests/test_google_connection.py, tests/test_google_reconnect.py, tests/test_gcal.py, tests/test_gmail.py.
"""

import base64
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.cancel import NOT_STARTED
from app.agent.deps import AgentDeps
from app.config import Settings
from app.models import ActionPlan, Connection, OAuthState, Operation
from app.models.enums import OperationStatus
from app.security import token_cipher
from app.tools import http
from app.tools.http import ExternalApiError

logger = logging.getLogger(__name__)

PROVIDER = "google"
SERVICE = "Google"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
# 기본 캘린더(내가 소유한 캘린더)의 일정 보기·만들기·수정·삭제에 필요한 가장 좁은 범위.
# events.list/insert/patch/delete 문서의 허용 범위에 모두 들어 있다.
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
# 7단계: 메일 읽기. '메일 읽기 권한 추가'(start?scope=mail)를 눌렀을 때만 요청한다(명세).
# 보내기·수정 범위는 쓰지 않는다.
# Google 분류상 restricted 범위다(docs/TOOLS.md). 점진적 권한 부여(include_granted_scopes=true)로 캘린더 권한과
# 합친 토큰을 받는다. 합친 권한은 철회하면 한꺼번에 사라진다(메일만 따로 철회할 수 없다, 공식 문서).
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
# OAuth 시작 정보의 수명(10분). 동의 화면에서 돌아오기에 충분하고, 새어 나간 state를 오래 쓸 수 없게 짧게 둔다.
STATE_TTL_SECONDS = 600
REFRESH_MARGIN = timedelta(seconds=60)  # 만료 1분 전이면 미리 갱신한다

NOT_CONNECTED_MESSAGE = "Google 계정이 연결되어 있지 않습니다. 설정에서 Google 계정을 연결해 주세요."
RECONNECT_MESSAGE = "Google 연결이 끊겼습니다(권한이 만료되었거나 취소됨). 설정에서 Google 계정을 다시 연결해 주세요."
UNAVAILABLE_MESSAGE = "서버에 Google 연결 설정이 없어 캘린더를 쓸 수 없습니다. 관리자가 .env를 확인해야 합니다."
MAIL_SCOPE_MESSAGE = "메일을 읽을 권한이 없습니다. 설정의 Google 연결에서 메일 읽기 권한을 추가해 주세요."
# 실행 계획 무효화 이유(operations.error_code). 실행기(executor.early_result)가 안내 문장으로 바꾼다.
CONNECTION_REMOVED = "connection_removed"  # 사용자가 연결 해제
CONNECTION_REVOKED = "connection_revoked"  # 7단계 보강: 만료·Google에서 권한 철회(invalid_grant)
# 연결은 있는데 이 범위가 없을 때의 안내. 캘린더 범위가 없는 연결은 저장하지 않으므로 '연결 없음'과 같다.
MISSING_SCOPE_MESSAGES = {CALENDAR_SCOPE: NOT_CONNECTED_MESSAGE, GMAIL_SCOPE: MAIL_SCOPE_MESSAGE}


class GoogleNotConnectedError(ExternalApiError):
    """연결이 없거나 필요한 범위가 없다. Google에 요청을 보내기 전에 낸다(도구는 실패 ToolResult로 바꾼다)."""

    def __init__(self, message: str = NOT_CONNECTED_MESSAGE) -> None:
        super().__init__(message)


class GoogleReconnectRequiredError(ExternalApiError):
    """연결 행은 있지만 토큰을 쓸 수 없다(invalid_grant, refresh token 없음, 키가 바뀌어 못 풂).

    사용자가 다시 연결해야 한다.
    """

    def __init__(self) -> None:
        super().__init__(RECONNECT_MESSAGE)


@dataclass(frozen=True)
class GoogleTokens:
    """복호화한 토큰 묶음. 메모리에서만 쓰고 로그·repr로 남기지 않는다(dataclass repr에 토큰이 보이므로 찍지 말 것)."""

    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: list[str]

    def encrypt(self, cipher: Fernet) -> str:
        """access·refresh·만료 시각을 JSON으로 묶어 암호화한다. 범위는 넣지 않는다(평문 칸 connections.scopes)."""
        payload = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
        }
        return cipher.encrypt(json.dumps(payload).encode("utf-8")).decode("ascii")

    @classmethod
    def decrypt(cls, row: Connection, cipher: Fernet) -> "GoogleTokens":
        """행에서 토큰을 푼다. 키가 다르면 InvalidToken, 내용이 깨졌으면 ValueError·KeyError.

        부르는 쪽(load_tokens, refresh_locked)이 GoogleReconnectRequiredError로 바꾼다.
        encrypted_token이 빈 문자열(다시 연결 필요로 표시된 행)이어도 InvalidToken이 난다.
        """
        payload = json.loads(cipher.decrypt(row.encrypted_token.encode("ascii")))
        expires_at = datetime.fromisoformat(payload["expires_at"])
        return cls(payload["access_token"], payload.get("refresh_token"), expires_at, list(row.scopes or []))

    @property
    def has_calendar(self) -> bool:
        return CALENDAR_SCOPE in self.scopes

    @property
    def has_mail(self) -> bool:
        return GMAIL_SCOPE in self.scopes


def google_available(settings: Settings) -> bool:
    """Google 연결에 필요한 설정이 모두 있는가. TOKEN_ENCRYPTION_KEY가 없으면 연결할 수 없다.

    키 없이 연결을 허용하면 토큰을 평문으로 저장하게 되므로 아예 막는다(키를 대신 만들지 않는다, DECISIONS 2026-11-17).
    """
    has_client = bool(settings.google_client_id and settings.google_client_secret.get_secret_value())
    return has_client and bool(settings.google_redirect_uri) and token_cipher(settings) is not None


# ---- 1. 인증 시작 ----


def code_challenge(verifier: str) -> str:
    """PKCE S256: verifier의 SHA-256을 패딩 없는 base64url로(RFC 7636). 가로챈 인가 코드만으로는 토큰을 못 받게 한다."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_authorization(settings: Settings, *, add_mail: bool = False) -> tuple[str, str, str]:
    """Google 동의 화면 주소, state, PKCE code_verifier를 만든다. state·verifier는 save_state로 DB에 둔다.

    add_mail=True('메일 읽기 권한 추가')면 캘린더 범위와 gmail.readonly를 함께 요청한다. 이미 허용한 범위는
    include_granted_scopes=true로 합쳐지고(점진적 권한 부여), 처음 연결이어도 캘린더가 같이 요청되어 연결이 성립한다.
    DB·외부 호출 없음. state·verifier 원문은 돌려줄 뿐 어디에도 남기지 않는다(DB에는 해시·암호문만).
    """
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # PKCE: 43~128자
    scopes = [CALENDAR_SCOPE, GMAIL_SCOPE] if add_mail else [CALENDAR_SCOPE]
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",  # refresh token을 받는다
        "prompt": "consent",  # 다시 연결할 때도 refresh token을 새로 받는다
        "include_granted_scopes": "true",  # 이미 허용한 범위와 합친 토큰(점진적 권한 부여)
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}", state, verifier


def hash_state(state: str) -> str:
    """state의 SHA-256. DB에는 이것만 둔다(DB가 새어도 진행 중인 연결 요청을 가로챌 수 없게)."""
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


async def save_state(
    session: AsyncSession, *, user_id: int, session_id: int, state: str, verifier: str, add_mail: bool, cipher: Fernet
) -> None:
    """시작한 연결 요청을 남긴다(state는 해시, verifier는 암호화). 커밋은 호출한 쪽."""
    session.add(
        OAuthState(
            user_id=user_id,
            session_id=session_id,
            state_hash=hash_state(state),
            encrypted_verifier=cipher.encrypt(verifier.encode("ascii")).decode("ascii"),
            add_mail=add_mail,
            expires_at=datetime.now(UTC) + timedelta(seconds=STATE_TTL_SECONDS),
        )
    )


@dataclass(frozen=True)
class PendingAuthorization:
    verifier: str
    add_mail: bool


async def consume_state(
    session: AsyncSession, *, user_id: int, session_id: int, state: str | None, cipher: Fernet
) -> PendingAuthorization | None:
    """콜백의 state가 이 사용자·이 로그인 세션이 시작한 것이고, 만료 전이고, 처음 쓰는 것일 때만 verifier를 준다.

    행을 잠그고 used_at을 채운다(같은 state로 두 번 들어와도 한 번만 통과). 커밋은 호출한 쪽.
    막는 사고: 다른 사람이 시작한 연결 요청의 콜백을 내 브라우저로 열게 해 남의 Google 계정을 내 계정에 붙이는 것
    (login CSRF), 같은 콜백 주소를 다시 열어 인가 코드를 두 번 쓰는 것. 다른 로그인 세션·만료·이미 쓴 state면 None.
    """
    if not state:
        return None
    row = await session.scalar(
        select(OAuthState)
        .where(
            OAuthState.state_hash == hash_state(state),
            OAuthState.user_id == user_id,
            OAuthState.session_id == session_id,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if row is None or row.used_at is not None or row.expires_at <= now:
        return None
    row.used_at = now
    try:
        verifier = cipher.decrypt(row.encrypted_verifier.encode("ascii")).decode("ascii")
    except InvalidToken:
        return None
    return PendingAuthorization(verifier=verifier, add_mail=row.add_mail)


# ---- 2. 토큰 교환·갱신·철회 (Google 서버) ----


def tokens_from(body: Any, *, previous_refresh: str | None = None) -> GoogleTokens:
    """토큰 응답 본문을 GoogleTokens로. access_token이 없으면 ExternalApiError(응답 내용은 문장에 넣지 않는다).

    expires_in이 없으면 1시간으로 본다(Google access token의 보통 수명). 범위는 응답의 scope(공백 구분).
    """
    if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
        raise ExternalApiError("Google 토큰 응답을 해석하지 못했습니다.")
    expires_in = int(body.get("expires_in") or 3600)
    return GoogleTokens(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token") or previous_refresh,  # 갱신 응답에는 refresh token이 없다
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        scopes=str(body.get("scope") or "").split(),
    )


def client_form(settings: Settings) -> dict[str, str]:
    """토큰 엔드포인트에 보낼 클라이언트 자격(본문 form). client_secret이 들어 있으니 로그에 남기지 않는다."""
    return {"client_id": settings.google_client_id, "client_secret": settings.google_client_secret.get_secret_value()}


async def exchange_code(client: httpx.AsyncClient, settings: Settings, code: str, verifier: str) -> GoogleTokens:
    """콜백의 인가 코드를 토큰으로 바꾼다(외부 호출, DB 없음). 실패하면 ExternalApiError(http.request_json).

    redirect_uri는 동의 화면 요청 때와 글자까지 같아야 Google이 받아 준다(GOOGLE_REDIRECT_URI 하나를 같이 쓴다).
    """
    form = {
        **client_form(settings),
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": settings.google_redirect_uri,
    }
    body = await http.request_json(client, SERVICE, "POST", TOKEN_URL, data=form)
    return tokens_from(body)


async def refresh_tokens(client: httpx.AsyncClient, settings: Settings, tokens: GoogleTokens) -> GoogleTokens:
    """refresh token으로 access token을 새로 받는다. 400(invalid_grant: 만료·철회)이면 다시 연결해야 한다.

    외부 호출만 하고 저장은 하지 않는다(refresh_locked가 잠금 안에서 저장). refresh token이 없거나 400이면
    GoogleReconnectRequiredError, 그 밖의 실패(시간 초과·5xx·429)는 ExternalApiError 그대로(재연결 표시 안 함).
    갱신 응답에 scope가 없으면 이전 범위를 이어받는다.
    """
    if not tokens.refresh_token:
        raise GoogleReconnectRequiredError
    form = {**client_form(settings), "grant_type": "refresh_token", "refresh_token": tokens.refresh_token}
    try:
        body = await http.request_json(client, SERVICE, "POST", TOKEN_URL, data=form)
    except ExternalApiError as error:
        if error.status == 400:
            raise GoogleReconnectRequiredError from error
        raise
    refreshed = tokens_from(body, previous_refresh=tokens.refresh_token)
    return refreshed if refreshed.scopes else replace(refreshed, scopes=tokens.scopes)


async def revoke(client: httpx.AsyncClient, token: str) -> None:
    """Google 쪽 권한 철회(외부 호출, BUILD_SPEC 10.5 "가능한 공급자 revoke").

    실패해도(이미 만료된 토큰 등) 예외를 내지 않는다.

    실패를 삼키는 이유: 해제는 우리 쪽 토큰 삭제가 본체다. Google이 실패해도 우리 DB에서 지우는 것을 막지 않는다.

    토큰은 주소(쿼리)가 아니라 본문으로 보낸다. 주소는 프록시·서버 로그에 남을 수 있다.
    """
    try:
        await http.request_json(client, SERVICE, "POST", REVOKE_URL, data={"token": token})
    except ExternalApiError:
        logger.info("Google 권한 철회 실패(무시하고 우리 쪽 토큰은 지운다)")


# ---- 3. DB 보관 ----


async def find_row(session: AsyncSession, user_id: int) -> Connection | None:
    """이 사용자의 Google 연결 행(사용자당 하나). 잠그지 않는다. user_id 조건이 사용자 간 경계다."""
    return await session.scalar(
        select(Connection).where(Connection.user_id == user_id, Connection.provider == PROVIDER)
    )


async def save_tokens(session: AsyncSession, user_id: int, tokens: GoogleTokens, cipher: Fernet) -> None:
    """연결을 만들거나 바꾼다. 커밋은 호출한 쪽이 한다.

    범위(scopes)는 응답에 온 것으로 덮어쓴다(점진적 권한 부여라 이미 허용한 범위도 응답에 들어 있다).
    """
    row = await find_row(session, user_id)
    if row is None:
        row = Connection(user_id=user_id, provider=PROVIDER, encrypted_token="", scopes=[])
        session.add(row)
    row.encrypted_token = tokens.encrypt(cipher)
    row.scopes = tokens.scopes
    row.expires_at = tokens.expires_at
    row.reconnect_required_at = None  # 7단계 보강: 다시 연결하면 "다시 연결 필요" 표시를 지운다


async def load_tokens(session: AsyncSession, user_id: int, cipher: Fernet) -> GoogleTokens | None:
    """저장된 토큰. 없으면 None. 다시 연결이 필요하거나 풀 수 없으면(키가 바뀜) GoogleReconnectRequiredError.

    읽기만 한다(풀 수 없어도 여기서는 표시를 남기지 않는다. 표시는 갱신 경로 refresh_locked가 잠금 안에서 남긴다).
    경고 로그에는 user id만 남긴다(토큰·예외 문장 없음).
    """
    row = await find_row(session, user_id)
    if row is None:
        return None
    if row.reconnect_required_at is not None:
        raise GoogleReconnectRequiredError
    try:
        return GoogleTokens.decrypt(row, cipher)
    except (InvalidToken, ValueError, KeyError) as error:
        logger.warning("저장된 Google 토큰을 풀 수 없음: user=%s", user_id)
        raise GoogleReconnectRequiredError from error


async def load_tokens_or_none(session: AsyncSession, user_id: int, cipher: Fernet) -> GoogleTokens | None:
    """풀 수 없는 토큰도 None으로 본다(연결 해제·다시 연결할 때).

    해제는 토큰을 못 풀어도 우리 쪽 행은 지워야 하고, 다시 연결은 이전 refresh token이 없으면 새 것만 쓰면 된다.
    """
    try:
        return await load_tokens(session, user_id, cipher)
    except GoogleReconnectRequiredError:
        return None


async def delete_connection(session: AsyncSession, user_id: int) -> None:
    """연결 행을 지운다(사용자가 해제할 때). 커밋은 호출한 쪽. 먼저 invalidate_pending_plans를 불러야 한다
    (행이 사라지면 connection_id로 대기 계획을 찾을 수 없다)."""
    await session.execute(delete(Connection).where(Connection.user_id == user_id, Connection.provider == PROVIDER))


async def invalidate_pending_plans(session: AsyncSession, user_id: int, connection_id: int, reason: str) -> None:
    """이 연결로 만든 실행 계획 중 아직 실행하지 않은 것을 무효화한다(BUILD_SPEC 10.5). 커밋은 호출한 쪽.

    reason: connection_removed(사용자가 연결 해제), connection_revoked(7단계 보강: 만료·Google에서 권한 철회).
    막는 사고: 연결을 끊거나 권한이 사라지기 전에 승인해 둔 일정 작업이, 다시 연결한 뒤 승인 당시와 다른 연결로
    실행되는 것. NOT_STARTED(승인 대기·승인됨, 외부 요청 전)만 바꾼다. executing·unknown은 이미 나갔을 수 있어
    결과를 숨기지 않도록 그대로 둔다(app/agent/cancel.py).
    상태 전이를 바꾸면 docs/STATE_MACHINES.md와 tests/test_state_machines.py도 같이 고친다.
    """
    plan_ids = select(ActionPlan.id).where(ActionPlan.user_id == user_id, ActionPlan.connection_id == connection_id)
    await session.execute(
        update(Operation)
        .where(Operation.plan_id.in_(plan_ids), Operation.status.in_(NOT_STARTED))
        .values(status=OperationStatus.CANCELLED, error_code=reason)
    )


async def mark_reconnect_required(session: AsyncSession, row: Connection) -> None:
    """7단계 보강: 토큰이 만료·철회되었다. 행은 남겨 "다시 연결 필요"로 표시하고(처음부터 연결 안 된 것과 구분),
    쓸 수 없는 토큰은 지우고, 이 연결로 만든 대기 중 계획은 무효화한다. 커밋은 호출한 쪽.

    범위(scopes)는 남기지만 usable()이 False가 되어 화면·도구 모두 연결 없음으로 다룬다. 다시 연결하면
    save_tokens가 표시를 지운다."""
    row.encrypted_token = ""
    row.reconnect_required_at = datetime.now(UTC)
    await invalidate_pending_plans(session, row.user_id, row.id, CONNECTION_REVOKED)


async def reconnect_needed(session: AsyncSession, row: Connection) -> GoogleReconnectRequiredError:
    """표시를 남기고 커밋한 뒤 올릴 예외를 돌려준다(행을 지우지 않는다, 7단계 보강).

    예외를 돌려주기만 하는 이유: 부르는 쪽이 `raise await reconnect_needed(...) from error`로 원인을 이어 붙인다.
    잠금 안(refresh_locked)에서 불리므로 커밋이 잠금도 푼다.
    """
    await mark_reconnect_required(session, row)
    await session.commit()
    return GoogleReconnectRequiredError()


async def drop_scope(deps: AgentDeps, scope: str) -> None:
    """7단계 보강: Google이 범위 부족(403 insufficientPermissions)이라고 답하면 그 범위를 연결 기록에서 뺀다.

    메일 권한이 빠진 연결로 다시 요청하지 않고, 연결 화면이 '메일 읽기 권한 추가'를 다시 보여 준다.
    행을 잠그고 바꾼 뒤 자기 세션으로 커밋한다(동시 갱신이 범위를 되살리지 않게). 범위가 이미 없으면 아무것도 안 한다.
    """
    async with deps.db() as session:
        row = await session.scalar(
            select(Connection)
            .where(Connection.user_id == deps.user_id, Connection.provider == PROVIDER)
            .with_for_update()
        )
        if row is not None and scope in (row.scopes or []):
            row.scopes = [item for item in row.scopes if item != scope]
            await session.commit()


# ---- 4. 도구가 쓰는 access token ----


def require_cipher(settings: Settings) -> Fernet:
    """도구용: 설정이 모자라면 ExternalApiError(".env 확인" 안내). 사용자 탓이 아닌 서버 설정 문제라 문구가 다르다."""
    cipher = token_cipher(settings)
    if cipher is None or not google_available(settings):
        raise ExternalApiError(UNAVAILABLE_MESSAGE)
    return cipher


async def is_connected(deps: AgentDeps) -> bool:
    """캘린더 권한이 있는 연결이 있는가(DB만 본다). preview가 "승인해도 실패한다"를 미리 알리는 데 쓴다.

    토큰을 풀거나 Google에 묻지 않으므로 True여도 실제 호출은 실패할 수 있다(만료·철회는 갱신 때 알게 된다).
    """
    cipher = token_cipher(deps.settings)
    if cipher is None:
        return False
    async with deps.db() as session:
        row = await find_row(session, deps.user_id)
    return usable(row) and row is not None and CALENDAR_SCOPE in (row.scopes or [])


def usable(row: Connection | None) -> bool:
    """연결이 있고 다시 연결이 필요하지 않은가(7단계 보강). 연결 화면의 calendar·mail 표시도 이 값으로 가린다."""
    return row is not None and row.reconnect_required_at is None


async def access_token(deps: AgentDeps, *, scope: str = CALENDAR_SCOPE, force_refresh: bool = False) -> str:
    """scope(캘린더 또는 메일) 권한이 있는 access token. 만료가 가까우면(또는 force_refresh면) 갱신해 저장한다.

    연결이 없으면 "연결해 주세요", 연결은 있는데 메일 범위가 없으면 "메일 읽기 권한을 추가해 주세요"
    (어느 쪽이든 Google에 요청을 보내지 않는다).
    갱신이 invalid_grant로 실패하면 토큰을 지우고 "다시 연결 필요"로 표시한 뒤 GoogleReconnectRequiredError(7단계 보강).
    처음 읽기는 잠그지 않는다(대부분 갱신이 필요 없다). 갱신이 필요할 때만 refresh_locked로 잠근다.
    force_refresh: 도구가 Google API 401을 받은 뒤 한 번 더 시도할 때(gcal·gmail의 attempt > 0).
    돌려준 토큰은 Authorization 헤더에만 쓰고 로그·결과·모델에 넣지 않는다.
    """
    cipher = require_cipher(deps.settings)
    async with deps.db() as session:
        tokens = await load_tokens(session, deps.user_id, cipher)
    if tokens is None or not tokens.has_calendar:
        raise GoogleNotConnectedError
    if scope not in tokens.scopes:
        raise GoogleNotConnectedError(MISSING_SCOPE_MESSAGES.get(scope, NOT_CONNECTED_MESSAGE))
    if not force_refresh and tokens.expires_at - REFRESH_MARGIN > datetime.now(UTC):
        return tokens.access_token
    return await refresh_locked(deps, cipher, used_access=tokens.access_token)


async def refresh_locked(deps: AgentDeps, cipher: Fernet, *, used_access: str) -> str:
    """연결 행을 잠근 채 갱신한다(4단계 보강, BUILD_SPEC 10.5 동시 갱신 충돌 방지).

    기다리는 사이 다른 요청이 이미 갱신했으면(토큰이 바뀌었고 아직 유효) 그 토큰을 쓰고 Google에 다시 요청하지 않는다.
    갱신이 invalid_grant거나 토큰을 풀 수 없으면 행은 남기고 "다시 연결 필요"로 표시한다(7단계 보강).
    막는 사고: 두 도구 호출이 동시에 갱신해 한쪽이 받은 토큰을 다른 쪽 저장이 덮어쓰거나, Google에 갱신 요청이
    겹치는 것. Google 호출(외부)이 잠금을 쥔 채 일어나므로 같은 사용자의 다른 갱신은 그동안 기다린다.
    used_access: 부르는 쪽이 방금 쓴(만료됐거나 401을 받은) 토큰. 행의 토큰이 이것과 다르면 이미 누가 갱신한 것이다.
    부수 효과: 새 토큰 저장 또는 재연결 표시 후 커밋. 시간 초과 등 그 밖의 실패는 커밋 없이 예외
    (잠금은 세션 종료로 풀림).
    """
    async with deps.db() as session:
        row = await session.scalar(
            select(Connection)
            .where(Connection.user_id == deps.user_id, Connection.provider == PROVIDER)
            .with_for_update()
        )
        if row is None:
            raise GoogleNotConnectedError
        if row.reconnect_required_at is not None:
            raise GoogleReconnectRequiredError
        try:
            current = GoogleTokens.decrypt(row, cipher)
        except (InvalidToken, ValueError, KeyError) as error:
            raise await reconnect_needed(session, row) from error
        if current.access_token != used_access and current.expires_at - REFRESH_MARGIN > datetime.now(UTC):
            return current.access_token
        try:
            refreshed = await refresh_tokens(deps.http, deps.settings, current)
        except GoogleReconnectRequiredError as error:
            raise await reconnect_needed(session, row) from error
        await save_tokens(session, deps.user_id, refreshed, cipher)
        await session.commit()
        return refreshed.access_token
