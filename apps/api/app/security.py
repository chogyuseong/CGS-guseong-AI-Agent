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
SESSION_MAX_AGE = timedelta(days=14)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# bcrypt는 72바이트까지만 비밀번호로 쓴다. bcrypt 5.0부터는 넘으면 ValueError를 낸다.
BCRYPT_MAX_BYTES = 72


class PasswordTooLongError(ValueError):
    """비밀번호가 bcrypt 한도(72바이트)를 넘었다."""


# ---- 비밀번호 ----


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        raise PasswordTooLongError(f"비밀번호는 UTF-8 기준 {BCRYPT_MAX_BYTES}바이트 이하여야 합니다.")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
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
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def start_session(
    db: AsyncSession, request: Request, response: Response, user_id: int, settings: Settings
) -> None:
    """지금 쿠키의 세션을 지우고 새 세션을 만든다(세션 고정 방지). 만료된 세션도 이때 정리한다."""
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
    """쿠키의 로그인 세션 행과 그 사용자. OAuth state를 이 세션에 묶을 때 쓴다(4단계 보강)."""
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
    """상태를 바꾸는 요청의 Origin 헤더가 허용된 출처와 같은지 검사한다."""

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
    키를 바꾸면 이미 저장한 토큰을 풀 수 없어 다시 연결해야 한다.
    """
    key = settings.token_encryption_key.get_secret_value()
    if not key:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        logger.warning("TOKEN_ENCRYPTION_KEY 형식이 Fernet 키가 아니다(값은 남기지 않음)")
        return None


def add_security_middleware(app: FastAPI, settings: Settings) -> None:
    # 로그인 세션은 미들웨어가 아니라 get_current_user가 DB에서 확인한다. OAuth state도 DB(4단계 보강).
    app.add_middleware(OriginCheckMiddleware, allowed_origin=settings.app_origin)
