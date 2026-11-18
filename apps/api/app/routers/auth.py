"""로그인, 로그아웃, 현재 사용자. 회원가입은 없다(create-user 명령으로만 계정을 만든다).

    POST /api/auth/login   본문 {email, password} → 200 UserOut + 세션 쿠키 / 401 / 429
    POST /api/auth/logout  → 204 (세션 행 삭제 + 쿠키 지움, 로그인 안 했어도 204)
    GET  /api/auth/me      → 200 UserOut / 401

- 세션은 서버 저장형(BUILD_SPEC 10.3): 쿠키 myagent_session, DB에는 토큰 해시만. 규칙은 app/security.py 머리 주석.
- login·logout은 POST라 앱 전체 Origin 검사(미들웨어)를 거친다. 로그인 전이라도 Origin이 다르면 403이다.
- 근거: DECISIONS 2026-11-16(bcrypt, 더미 해시로 응답 시간 맞추기, 401 문구에 무엇이 틀렸는지 넣지 않기,
  Origin 없는 상태 변경 요청도 403).

고칠 때 주의:
- 경로·응답 모양을 바꾸면 gen:api 후 apps/web/lib/auth.ts(/api/auth/me, /api/auth/login 호출)를 같이 고친다.
- 로그인 한도(LOGIN_ATTEMPTS_PER_MINUTE)는 앱 시작 때 build_login_rate_limiter로 app.state에 한 번 만든다(app/main.py).
  서버 프로세스 메모리에만 있어 재시작하면 초기화되고, 서버를 여러 개 띄우면 서버마다 따로 센다.
- 관련 테스트: tests/test_auth.py.
"""

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
    """로그인 실패 한도기. app/main.py의 create_app이 앱마다 한 번 만들어 app.state.login_rate_limiter에 둔다."""
    return SlidingWindowRateLimiter(limit=LOGIN_ATTEMPTS_PER_MINUTE)


# 로그인. 성공하면 start_session이 이전 쿠키의 세션을 지우고 새 세션을 커밋한다(세션 고정 방지).
# 429는 한도에 걸린 이메일이면 비밀번호가 맞아도 낸다(무차별 대입 방지). 실패만 세므로 성공은 한도를 쓰지 않는다.
# 401은 계정 없음·비밀번호 틀림을 같은 문구로 낸다(계정 존재 노출 방지).
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


# 로그아웃. CurrentUser를 요구하지 않아 세션이 없거나 만료돼도 204로 쿠키를 지운다.
# 세션 행을 지우므로 복사해 둔 쿠키도 바로 무효다. (2026-11-18 기준 apps/web/lib에는 이 API를 부르는 곳이 없다.)
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, session: DbSession) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    await end_session(session, request, response, request.app.state.settings)
    return response


# 현재 사용자. 화면(apps/web/lib/auth.ts)은 401을 오류가 아니라 "로그인 안 함"(null)으로 읽는다.
@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
