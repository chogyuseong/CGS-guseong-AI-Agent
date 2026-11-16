"""외부 서비스와의 연결: connections(OAuth 토큰), push_subscriptions(웹 푸시 구독)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable


class Connection(TimestampedTable):
    """외부 서비스 OAuth 토큰. 토큰은 Fernet으로 암호화한 문자열만 저장한다(4단계)."""

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    encrypted_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushSubscription(TimestampedTable):
    __tablename__ = "push_subscriptions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
