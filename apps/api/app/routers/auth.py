"""로그인, 로그아웃, 현재 사용자. 회원가입은 없다(create-user 명령으로만 계정을 만든다)."""

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.db import DbSession
from app.schemas.auth import LoginRequest, UserOut
from app.security import CurrentUser, dummy_password_hash, end_session, start_session, verify_password
from app.users import find_user_by_email

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(body: LoginRequest, request: Request, session: DbSession) -> UserOut:
    user = await find_user_by_email(session, body.email)
    # 계정이 없어도 해시 검사를 한 번 해서 응답 시간이 같게 한다.
    password_hash = user.password_hash if user else dummy_password_hash()
    password_ok = verify_password(body.password, password_hash)
    if user is None or not password_ok:
        # 이메일이 틀렸는지 비밀번호가 틀렸는지 알려 주지 않는다.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="이메일 또는 비밀번호가 맞지 않습니다.")
    start_session(request, user.id)
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    end_session(request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
