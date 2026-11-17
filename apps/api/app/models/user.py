from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable


class User(TimestampedTable):
    """로그인 계정. 회원가입 화면은 없고 create-user 명령으로만 만든다."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # 6단계 추가: 아침 브리핑 기본 예약을 만들어 준 시각. 한 번 만들면 채워지고, 사용자가 브리핑을 끄거나 지워도
    # 다시 만들지 않는다(null인 계정에만 만든다. app/automation/store.py).
    briefing_seeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(TimestampedTable):
    """서버 저장형 로그인 세션(BUILD_SPEC 10.3). Django의 DB 세션 백엔드와 같은 방식이다.

    쿠키에는 무작위 토큰만 들어가고, DB에는 그 SHA-256 해시만 저장한다(DB가 새어도 쿠키를 만들 수 없다).
    로그아웃하면 행을 지우므로, 미리 복사해 둔 쿠키도 바로 쓸 수 없게 된다.
    """

    __tablename__ = "sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
