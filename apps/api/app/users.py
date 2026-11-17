"""사용자 계정 조회와 생성. 로그인 라우터와 create-user 명령이 같이 쓴다."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.security import hash_password


def normalize_email(email: str) -> str:
    """앞뒤 공백을 지우고 소문자로 맞춘다. 저장과 로그인에서 같은 규칙을 쓴다."""
    return email.strip().lower()


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == normalize_email(email)))


async def create_user_if_missing(session: AsyncSession, email: str, password: str) -> User | None:
    """계정을 만들고 커밋한다. 이미 있는 이메일이면 아무것도 바꾸지 않고 None을 돌려준다."""
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
