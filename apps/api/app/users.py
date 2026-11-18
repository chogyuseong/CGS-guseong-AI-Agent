"""사용자 계정 조회와 생성. 로그인 라우터(app/routers/auth.py)와 create-user 명령(app/cli.py)이 같이 쓴다.

회원 가입 API는 없다(개인 비서라 계정은 명령줄로만 만든다).
고칠 때 주의: 이메일 정규화 규칙을 바꾸면 이미 저장된 이메일과 맞지 않아 로그인이 안 될 수 있다
(데이터 마이그레이션 필요). 로그인 실패 제한 키(routers/auth.py)도 같은 규칙으로 이메일을 맞춘다.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.security import hash_password


def normalize_email(email: str) -> str:
    """앞뒤 공백을 지우고 소문자로 맞춘다. 저장과 로그인에서 같은 규칙을 쓴다."""
    return email.strip().lower()


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    """정규화한 이메일로 계정을 찾는다. 없으면 None(읽기만, 커밋 없음)."""
    return await session.scalar(select(User).where(User.email == normalize_email(email)))


async def create_user_if_missing(session: AsyncSession, email: str, password: str) -> User | None:
    """계정을 만들고 커밋한다. 이미 있는 이메일이면 아무것도 바꾸지 않고 None을 돌려준다.

    비밀번호를 덮어쓰지 않는 이유: e2e 준비 단계가 create-user를 반복 실행한다(app/cli.py).
    비밀번호가 bcrypt 한도를 넘으면 hash_password가 PasswordTooLongError를 낸다(cli는 미리 검사한다).
    """
    if await find_user_by_email(session, email) is not None:
        return None
    user = User(email=normalize_email(email), password_hash=hash_password(password))
    session.add(user)
    try:
        # 6단계 보강: 아침 브리핑은 만들지 않는다(사용자가 자동화 화면에서 켠다, BUILD_SPEC 8.1).
        await session.commit()
    except IntegrityError:
        # 같은 이메일로 거의 동시에 두 번 실행된 경우. 먼저 만든 쪽을 그대로 둔다.
        await session.rollback()
        return None
    await session.refresh(user)
    return user
