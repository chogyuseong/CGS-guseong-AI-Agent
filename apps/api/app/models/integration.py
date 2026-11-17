"""외부 서비스와의 연결: connections(OAuth 토큰), push_subscriptions(웹 푸시 구독)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable, enum_type
from app.models.enums import NotificationStatus


class Connection(TimestampedTable):
    """외부 서비스 OAuth 토큰. 토큰은 Fernet으로 암호화한 문자열만 저장한다(4단계)."""

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    encrypted_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 7단계 보강: 토큰 갱신이 invalid_grant(만료·Google에서 권한 철회)로 실패한 시각. 채워져 있으면 토큰은 지워져 있고
    # "다시 연결 필요" 상태다(처음부터 연결 안 된 것과 구분, BUILD_SPEC 10.5). 다시 연결하면 null.
    reconnect_required_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushSubscription(TimestampedTable):
    __tablename__ = "push_subscriptions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(TimestampedTable):
    """푸시 한 건의 기록(3단계 보강, BUILD_SPEC 8.4). 같은 (user, notification_key)는 한 번만 보낸다.

    본문은 kind별 고정 문구라 저장하지 않는다. 사용자 글·일정 제목 같은 내용은 여기 없다.
    """

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "notification_key"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    notification_key: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))
    url: Mapped[str] = mapped_column(String(200))
    status: Mapped[NotificationStatus] = mapped_column(
        enum_type(NotificationStatus, "notification_status"), server_default=NotificationStatus.PENDING.value
    )
    sent_count: Mapped[int] = mapped_column(Integer, server_default="0")


class OAuthState(TimestampedTable):
    """Google 연결 시작 한 번의 state(4단계 보강, BUILD_SPEC 10.5). 로그인 세션·만료(10분)·한 번 사용에 묶는다.

    state는 SHA-256 해시만, PKCE code_verifier는 Fernet으로 암호화해 둔다. 콜백이 쓰면 used_at을 채운다.
    로그인 세션을 지우면(로그아웃) 함께 지워진다.
    """

    __tablename__ = "oauth_states"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    encrypted_verifier: Mapped[str] = mapped_column(Text)
    add_mail: Mapped[bool] = mapped_column(Boolean, server_default="false")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
