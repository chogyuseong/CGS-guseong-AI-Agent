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
- state(CSRF 방어)와 PKCE code_verifier는 서명된 세션 쿠키에 넣고 콜백에서 한 번만 쓴다(10분 안).
- 토큰(access, refresh, 만료 시각)은 JSON 통째로 Fernet 암호화해 connections.encrypted_token에 둔다.
  범위(scopes)와 만료 시각만 평문 칸에 둔다(권한 확인은 복호화 없이).
- 토큰은 로그, 오류 문장, 모델, 화면 어디에도 넣지 않는다.
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
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.config import Settings
from app.models import Connection, OAuthState
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
STATE_TTL_SECONDS = 600
REFRESH_MARGIN = timedelta(seconds=60)  # 만료 1분 전이면 미리 갱신한다

NOT_CONNECTED_MESSAGE = "Google 계정이 연결되어 있지 않습니다. 설정에서 Google 계정을 연결해 주세요."
RECONNECT_MESSAGE = "Google 연결이 끊겼습니다(권한이 만료되었거나 취소됨). 설정에서 Google 계정을 다시 연결해 주세요."
UNAVAILABLE_MESSAGE = "서버에 Google 연결 설정이 없어 캘린더를 쓸 수 없습니다. 관리자가 .env를 확인해야 합니다."
MAIL_SCOPE_MESSAGE = "메일을 읽을 권한이 없습니다. 설정의 Google 연결에서 메일 읽기 권한을 추가해 주세요."
# 연결은 있는데 이 범위가 없을 때의 안내. 캘린더 범위가 없는 연결은 저장하지 않으므로 '연결 없음'과 같다.
MISSING_SCOPE_MESSAGES = {CALENDAR_SCOPE: NOT_CONNECTED_MESSAGE, GMAIL_SCOPE: MAIL_SCOPE_MESSAGE}


class GoogleNotConnectedError(ExternalApiError):
    def __init__(self, message: str = NOT_CONNECTED_MESSAGE) -> None:
        super().__init__(message)


class GoogleReconnectRequiredError(ExternalApiError):
    def __init__(self) -> None:
        super().__init__(RECONNECT_MESSAGE)


@dataclass(frozen=True)
class GoogleTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: list[str]

    def encrypt(self, cipher: Fernet) -> str:
        payload = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
        }
        return cipher.encrypt(json.dumps(payload).encode("utf-8")).decode("ascii")

    @classmethod
    def decrypt(cls, row: Connection, cipher: Fernet) -> "GoogleTokens":
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
    """Google 연결에 필요한 설정이 모두 있는가. TOKEN_ENCRYPTION_KEY가 없으면 연결할 수 없다."""
    has_client = bool(settings.google_client_id and settings.google_client_secret.get_secret_value())
    return has_client and bool(settings.google_redirect_uri) and token_cipher(settings) is not None


# ---- 1. 인증 시작 ----


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_authorization(settings: Settings, *, add_mail: bool = False) -> tuple[str, str, str]:
    """Google 동의 화면 주소, state, PKCE code_verifier를 만든다. state·verifier는 save_state로 DB에 둔다.

    add_mail=True('메일 읽기 권한 추가')면 캘린더 범위와 gmail.readonly를 함께 요청한다. 이미 허용한 범위는
    include_granted_scopes=true로 합쳐지고(점진적 권한 부여), 처음 연결이어도 캘린더가 같이 요청되어 연결이 성립한다.
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
    return {"client_id": settings.google_client_id, "client_secret": settings.google_client_secret.get_secret_value()}


async def exchange_code(client: httpx.AsyncClient, settings: Settings, code: str, verifier: str) -> GoogleTokens:
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
    """refresh token으로 access token을 새로 받는다. 400(invalid_grant: 만료·철회)이면 다시 연결해야 한다."""
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
    """Google 쪽 권한 철회. 실패해도(이미 만료된 토큰 등) 예외를 내지 않는다.

    토큰은 주소(쿼리)가 아니라 본문으로 보낸다. 주소는 프록시·서버 로그에 남을 수 있다.
    """
    try:
        await http.request_json(client, SERVICE, "POST", REVOKE_URL, data={"token": token})
    except ExternalApiError:
        logger.info("Google 권한 철회 실패(무시하고 우리 쪽 토큰은 지운다)")


# ---- 3. DB 보관 ----


async def find_row(session: AsyncSession, user_id: int) -> Connection | None:
    return await session.scalar(
        select(Connection).where(Connection.user_id == user_id, Connection.provider == PROVIDER)
    )


async def save_tokens(session: AsyncSession, user_id: int, tokens: GoogleTokens, cipher: Fernet) -> None:
    """연결을 만들거나 바꾼다. 커밋은 호출한 쪽이 한다."""
    row = await find_row(session, user_id)
    if row is None:
        row = Connection(user_id=user_id, provider=PROVIDER, encrypted_token="", scopes=[])
        session.add(row)
    row.encrypted_token = tokens.encrypt(cipher)
    row.scopes = tokens.scopes
    row.expires_at = tokens.expires_at


async def load_tokens(session: AsyncSession, user_id: int, cipher: Fernet) -> GoogleTokens | None:
    """저장된 토큰. 없으면 None. 풀 수 없으면(키가 바뀜) 다시 연결해야 한다."""
    row = await find_row(session, user_id)
    if row is None:
        return None
    try:
        return GoogleTokens.decrypt(row, cipher)
    except (InvalidToken, ValueError, KeyError) as error:
        logger.warning("저장된 Google 토큰을 풀 수 없음: user=%s", user_id)
        raise GoogleReconnectRequiredError from error


async def load_tokens_or_none(session: AsyncSession, user_id: int, cipher: Fernet) -> GoogleTokens | None:
    """풀 수 없는 토큰도 None으로 본다(연결 해제·다시 연결할 때)."""
    try:
        return await load_tokens(session, user_id, cipher)
    except GoogleReconnectRequiredError:
        return None


async def delete_connection(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(Connection).where(Connection.user_id == user_id, Connection.provider == PROVIDER))


# ---- 4. 도구가 쓰는 access token ----


def require_cipher(settings: Settings) -> Fernet:
    cipher = token_cipher(settings)
    if cipher is None or not google_available(settings):
        raise ExternalApiError(UNAVAILABLE_MESSAGE)
    return cipher


async def is_connected(deps: AgentDeps) -> bool:
    """캘린더 권한이 있는 연결이 있는가(DB만 본다). preview가 "승인해도 실패한다"를 미리 알리는 데 쓴다."""
    cipher = token_cipher(deps.settings)
    if cipher is None:
        return False
    async with deps.db() as session:
        row = await find_row(session, deps.user_id)
    return row is not None and CALENDAR_SCOPE in (row.scopes or [])


async def access_token(deps: AgentDeps, *, scope: str = CALENDAR_SCOPE, force_refresh: bool = False) -> str:
    """scope(캘린더 또는 메일) 권한이 있는 access token. 만료가 가까우면(또는 force_refresh면) 갱신해 저장한다.

    연결이 없으면 "연결해 주세요", 연결은 있는데 메일 범위가 없으면 "메일 읽기 권한을 추가해 주세요"
    (어느 쪽이든 Google에 요청을 보내지 않는다).
    갱신이 invalid_grant로 실패하면 연결을 지우고 GoogleReconnectRequiredError(“다시 연결해 주세요”).
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
    갱신이 invalid_grant면 연결을 지운다(다시 연결 필요).
    """
    async with deps.db() as session:
        row = await session.scalar(
            select(Connection)
            .where(Connection.user_id == deps.user_id, Connection.provider == PROVIDER)
            .with_for_update()
        )
        if row is None:
            raise GoogleNotConnectedError
        try:
            current = GoogleTokens.decrypt(row, cipher)
        except (InvalidToken, ValueError, KeyError) as error:
            raise GoogleReconnectRequiredError from error
        if current.access_token != used_access and current.expires_at - REFRESH_MARGIN > datetime.now(UTC):
            return current.access_token
        try:
            refreshed = await refresh_tokens(deps.http, deps.settings, current)
        except GoogleReconnectRequiredError:
            await session.delete(row)
            await session.commit()
            raise
        await save_tokens(session, deps.user_id, refreshed, cipher)
        await session.commit()
        return refreshed.access_token
