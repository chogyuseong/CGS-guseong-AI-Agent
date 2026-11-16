from datetime import datetime

from sqlalchemy import DateTime, String
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
