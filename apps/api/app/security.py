"""보안: 비밀번호 해시, 세션 쿠키, Origin 검사(CSRF 방어), OAuth 토큰 암호화(Fernet, 4단계).

세션: Starlette의 SessionMiddleware(서명된 쿠키)를 쓴다. 쿠키에는 user_id만 들어가고,
SESSION_SECRET으로 서명돼 있어 브라우저에서 값을 바꾸면 서명이 깨져 무시된다(내용은 암호화되지 않음).
Django의 signed_cookies 세션 백엔드와 같은 방식이다.

CSRF: 상태를 바꾸는 요청(GET/HEAD/OPTIONS 외)은 Origin 헤더가 APP_ORIGIN과 정확히 같아야 한다.
다르거나 없으면 403. SameSite=Lax 쿠키와 함께 이중으로 막는다.
"""

import logging
from functools import lru_cache
from typing import Annotated

import bcrypt
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, HTTPException, Request, status
from starlette.datastructures import Headers
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.db import DbSession
from app.models import User

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "myagent_session"
SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60  # 14일
SESSION_USER_KEY = "user_id"
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


def start_session(request: Request, user_id: int) -> None:
    # 로그인 전 세션 내용을 지우고 새로 시작한다(세션 고정 공격 방지).
    request.session.clear()
    request.session[SESSION_USER_KEY] = user_id


def end_session(request: Request) -> None:
    request.session.clear()


async def get_current_user(request: Request, session: DbSession) -> User:
    """로그인한 사용자를 돌려준다. 없으면 401. Django의 @login_required와 같은 역할의 FastAPI 의존성."""
    user_id = request.session.get(SESSION_USER_KEY)
    user = await session.get(User, user_id) if isinstance(user_id, int) else None
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
    # 나중에 추가한 미들웨어가 바깥쪽에서 먼저 실행된다: Origin 검사 -> 세션 -> 라우터.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        session_cookie=SESSION_COOKIE_NAME,
        max_age=SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.add_middleware(OriginCheckMiddleware, allowed_origin=settings.app_origin)
