from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable


class User(TimestampedTable):
    """로그인 계정. 회원가입 화면은 없고 create-user 명령으로만 만든다."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
