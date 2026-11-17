"""로그인, 로그아웃, 현재 사용자. 회원가입은 없다(create-user 명령으로만 계정을 만든다)."""

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.config import Settings
from app.db import DbSession
from app.rate_limit import SlidingWindowRateLimiter
from app.schemas.auth import LoginRequest, UserOut
from app.security import CurrentUser, dummy_password_hash, end_session, start_session, verify_password
from app.users import find_user_by_email

router = APIRouter(prefix="/auth", tags=["auth"])

# 같은 이메일(대소문자 무시)로 1분에 이 횟수만큼 실패하면, 1분이 지날 때까지 맞는 비밀번호도 429로 막는다.
# 무차별 대입을 막는 것이 목적이라 성공한 로그인은 세지 않는다.
# 계정이 있든 없든 같은 한도라 응답으로 계정 존재를 알 수 없다. IP는 프록시 뒤에서 믿을 수 없어 쓰지 않는다.
LOGIN_ATTEMPTS_PER_MINUTE = 5


def build_login_rate_limiter() -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(limit=LOGIN_ATTEMPTS_PER_MINUTE)


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response, session: DbSession) -> UserOut:
    limiter: SlidingWindowRateLimiter = request.app.state.login_rate_limiter
    limit_key = body.email.strip().lower()
    if limiter.blocked(limit_key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="잠시 후 다시 시도해 주세요.")
    user = await find_user_by_email(session, body.email)
    # 계정이 없어도 해시 검사를 한 번 해서 응답 시간이 같게 한다.
    password_hash = user.password_hash if user else dummy_password_hash()
    password_ok = verify_password(body.password, password_hash)
    if user is None or not password_ok:
        limiter.record(limit_key)
        # 이메일이 틀렸는지 비밀번호가 틀렸는지 알려 주지 않는다.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="이메일 또는 비밀번호가 맞지 않습니다.")
    settings: Settings = request.app.state.settings
    await start_session(session, request, response, user.id, settings)
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, session: DbSession) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    await end_session(session, request, response, request.app.state.settings)
    return response


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
