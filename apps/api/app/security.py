"""보안: 비밀번호 해시, 세션 쿠키, Origin 검사(CSRF 방어), OAuth 토큰 암호화(Fernet, 4단계).

세션(0단계 보강, BUILD_SPEC 10.3): 서버 저장형. 로그인하면 무작위 토큰을 쿠키(myagent_session)에 넣고
DB sessions 테이블에는 토큰의 SHA-256 해시만 저장한다. Django의 DB 세션 백엔드와 같은 방식이다.
- 로그인할 때 지금 쿠키의 세션을 지우고 새 토큰을 만든다(세션 고정 방지).
- 로그아웃하면 행을 지운다. 서명 쿠키 방식과 달리 복사해 둔 쿠키도 바로 무효가 된다.
- 만료: 로그인 후 14일(고정). 만료된 행은 같은 사용자가 다시 로그인할 때 지운다.
Google 연결의 OAuth state는 4단계 보강부터 DB(oauth_states)에 로그인 세션·만료·한 번 사용으로 묶어 둔다
(app/google_auth.py). 예전 서명 쿠키(myagent_oauth)는 쓰지 않는다.

CSRF: 상태를 바꾸는 요청(GET/HEAD/OPTIONS 외)은 Origin 헤더가 APP_ORIGIN과 정확히 같아야 한다.
다르거나 없으면 403(Origin이 없는 요청도 거부한다). SameSite=Lax 쿠키와 함께 이중으로 막는다.

진입점: create_app이 add_security_middleware를 부른다. 라우터는 CurrentUser 의존성으로 로그인을 확인하고,
로그인·로그아웃은 app/routers/auth.py가 start_session·end_session을 부른다. 토큰 암호화는 app/google_auth.py,
payload 암호화는 app/payload_crypto.py가 쓴다.
근거: docs/DECISIONS.md 2026-11-16(Origin 검사·bcrypt·쿠키 Secure 조건), 2026-11-17 "0단계 보강"(서버 저장형 세션),
2026-11-17 OAuth state를 DB로.

고칠 때 주의
- 새 라우터는 Origin 검사를 따로 붙일 필요가 없다(미들웨어가 앱 전체에 건다). 대신 GET으로 상태를 바꾸는
  엔드포인트를 만들면 이 검사를 피하게 되므로 만들지 않는다.
- 쿠키 이름·속성을 바꾸면 웹(apps/web)과 e2e, delete_cookie의 속성도 같이 맞춘다(속성이 다르면 지워지지 않는다).
- 암호화 키를 바꾸거나 잃었을 때의 결과와 순서는 docs/OPERATIONS.md 4절. 키 값은 로그에 남기지 않는다.
- 관련 테스트: tests/test_auth.py(로그인·세션·Origin), tests/test_payload_crypto.py.
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated

import bcrypt
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.db import DbSession
from app.models import User, UserSession

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "myagent_session"
# 로그인 유지 기간(고정, 쓸 때마다 늘리지 않는다). 0단계 결정의 14일을 서버 저장형으로 바꾼 뒤에도 유지했다.
SESSION_MAX_AGE = timedelta(days=14)
# 상태를 바꾸지 않는 메서드. 이 밖의 요청만 Origin 검사를 받는다(GET으로 상태를 바꾸지 않는다는 전제).
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# bcrypt는 72바이트까지만 비밀번호로 쓴다. bcrypt 5.0부터는 넘으면 ValueError를 낸다.
BCRYPT_MAX_BYTES = 72


class PasswordTooLongError(ValueError):
    """비밀번호가 bcrypt 한도(72바이트)를 넘었다."""


# ---- 비밀번호 ----


def hash_password(password: str) -> str:
    """bcrypt 해시(솔트 포함 문자열). 72바이트를 넘으면 PasswordTooLongError.

    잘라서 해시하지 않고 거부한다(DECISIONS 2026-11-16). 잘라 쓰면 72바이트 뒤가 무시되어, 앞부분만 같은
    다른 비밀번호로도 로그인된다.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        raise PasswordTooLongError(f"비밀번호는 UTF-8 기준 {BCRYPT_MAX_BYTES}바이트 이하여야 합니다.")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """맞으면 True. 72바이트를 넘는 입력은 예외 대신 False(그런 비밀번호로는 계정을 만들 수 없다)."""
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        return False
    return bcrypt.checkpw(encoded, password_hash.encode("ascii"))


@lru_cache
def dummy_password_hash() -> str:
    """없는 이메일로 로그인할 때도 해시 검사를 한 번 해서, 응답 시간으로 계정 존재 여부를 알 수 없게 한다."""
    return hash_password("dummy-password-for-timing")


# ---- 세션 ----


def hash_token(token: str) -> str:
    """세션 토큰의 SHA-256. DB에는 이것만 둔다(DB가 새어도 쿠키를 만들 수 없게).

    토큰이 무작위 256비트라 솔트 없는 해시로 충분하다(DECISIONS 0단계 보강).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def start_session(
    db: AsyncSession, request: Request, response: Response, user_id: int, settings: Settings
) -> None:
    """지금 쿠키의 세션을 지우고 새 세션을 만든다(세션 고정 방지). 만료된 세션도 이때 정리한다.

    세션 고정: 공격자가 미리 심어 둔 쿠키 값이 로그인 뒤에도 살아 있으면 그 쿠키로 피해자 계정을 쓸 수 있다.
    부수 효과: sessions 행 삭제·추가 후 커밋, 응답에 쿠키(HttpOnly, SameSite=Lax, https면 Secure)를 심는다.
    """
    now = datetime.now(UTC)
    old_token = request.cookies.get(SESSION_COOKIE_NAME)
    if old_token:
        await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(old_token)))
    await db.execute(delete(UserSession).where(UserSession.user_id == user_id, UserSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    db.add(UserSession(user_id=user_id, token_hash=hash_token(token), expires_at=now + SESSION_MAX_AGE))
    await db.commit()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=int(SESSION_MAX_AGE.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


async def end_session(db: AsyncSession, request: Request, response: Response, settings: Settings) -> None:
    """로그아웃: 이 쿠키의 세션 행을 지우고 커밋한 뒤 쿠키를 지운다.

    행을 지우므로 복사해 둔 쿠키도 바로 쓸 수 없다. 이 세션에 묶인 OAuth 시작 정보도 쓸 수 없게 된다
    (consume_state가 session_id를 조건으로 건다).
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
        await db.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, httponly=True, samesite="lax", secure=settings.cookie_secure)


async def user_from_request(request: Request, db: AsyncSession) -> User | None:
    """쿠키의 토큰으로 만료되지 않은 세션의 사용자를 찾는다. 없으면 None."""
    found = await session_from_request(request, db)
    return found[0] if found else None


async def session_from_request(request: Request, db: AsyncSession) -> tuple[User, UserSession] | None:
    """쿠키의 로그인 세션 행과 그 사용자. OAuth state를 이 세션에 묶을 때 쓴다(4단계 보강).

    쿠키가 없거나, 행이 없거나(로그아웃·다른 곳에서 다시 로그인), 만료되었으면 None. 읽기만 한다.
    만료 검사를 쿼리에 두므로 만료된 행이 지워지기 전에도 로그인으로 통과하지 않는다.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    query = (
        select(User, UserSession)
        .join(UserSession, UserSession.user_id == User.id)
        .where(UserSession.token_hash == hash_token(token), UserSession.expires_at > datetime.now(UTC))
    )
    row = (await db.execute(query)).one_or_none()
    return (row[0], row[1]) if row else None


async def get_current_user(request: Request, session: DbSession) -> User:
    """로그인한 사용자를 돌려준다. 없으면 401. Django의 @login_required와 같은 역할의 FastAPI 의존성."""
    user = await user_from_request(request, session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# ---- Origin 검사 ----


class OriginCheckMiddleware:
    """상태를 바꾸는 요청의 Origin 헤더가 허용된 출처와 같은지 검사한다.

    막는 사고: 다른 사이트가 로그인한 사용자의 브라우저로 이 앱에 POST를 보내 승인·삭제 등을 대신 하는 것(CSRF).
    Origin이 없는 요청도 거부한다(브라우저는 POST에 항상 붙인다. curl은 -H "Origin: ..."가 필요, DECISIONS 2026-11-16).
    비교는 정확한 문자열 일치다(끝의 '/'만 허용 출처에서 지운다). 응답 본문은 고정 문구다.
    """

    def __init__(self, app: ASGIApp, allowed_origin: str) -> None:
        self.app = app
        self.allowed_origin = allowed_origin.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            origin = Headers(scope=scope).get("origin")
            if origin != self.allowed_origin:
                response = JSONResponse({"detail": "허용되지 않은 출처의 요청입니다."}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ---- 토큰 암호화 (4단계) ----


def token_cipher(settings: Settings) -> Fernet | None:
    """OAuth 토큰을 DB에 저장할 때 쓰는 Fernet. TOKEN_ENCRYPTION_KEY가 비었거나 형식이 틀리면 None.

    None이면 토큰을 안전하게 저장할 수 없으므로 Google 연결 자체를 막는다(키를 대신 만들지 않는다).
    키를 바꾸면 이미 저장한 토큰을 풀 수 없어 다시 연결해야 한다(docs/OPERATIONS.md 4절).
    형식 오류 로그에는 키 이름만 남긴다(값을 로그에 남기면 키가 새는 사고). 부를 때마다 새 Fernet을 만든다.
    """
    key = settings.token_encryption_key.get_secret_value()
    if not key:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        logger.warning("TOKEN_ENCRYPTION_KEY 형식이 Fernet 키가 아니다(값은 남기지 않음)")
        return None


def payload_cipher(settings: Settings) -> Fernet | None:
    """7단계 보강: 복구 payload(DBOS 기록, 메일 결과의 모델 기록)를 암호화하는 Fernet. 키가 없거나 틀리면 None.

    None이면 메일 원문을 안전하게 보관할 수 없으므로 메일 기능을 켜지 않는다(app/agent/data_policy.py mail_enabled).
    TOKEN_ENCRYPTION_KEY와 다른 키다. 바꾸면 끝나지 않은 워크플로를 복구할 수 없다(docs/OPERATIONS.md 3·4절).
    """
    key = settings.payload_encryption_key.get_secret_value()
    if not key:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        logger.warning("PAYLOAD_ENCRYPTION_KEY 형식이 Fernet 키가 아니다(값은 남기지 않음)")
        return None


def add_security_middleware(app: FastAPI, settings: Settings) -> None:
    """앱 전체에 Origin 검사를 건다. 라우터별 의존성이 아닌 이유: 새 라우터에서 빠뜨릴 수 없게(DECISIONS 2026-11-16)."""
    # 로그인 세션은 미들웨어가 아니라 get_current_user가 DB에서 확인한다. OAuth state도 DB(4단계 보강).
    app.add_middleware(OriginCheckMiddleware, allowed_origin=settings.app_origin)
