"""외부 서비스와의 연결: connections(OAuth 토큰), push_subscriptions(웹 푸시 구독),
notifications(푸시 한 건 기록, 3단계 보강), oauth_states(Google 연결 시작, 4단계 보강).

진입점: connections·oauth_states는 app/google_auth.py와 app/routers/connections.py, push_subscriptions는
app/routers/push.py(구독)·app/push.py(보내기), notifications는 app/push.py(PushNotifier.notify).

고칠 때 주의:
- 컬럼을 바꾸면 Alembic 마이그레이션(`uv run python -m alembic revision --autogenerate`)과 docs/DATA_POLICY.md 표
  ("Google 토큰", "푸시 구독")를 함께 본다.
- 토큰·PKCE verifier는 암호문으로만, OAuth state는 해시로만 저장한다(절대 원칙 15). 평문 컬럼을 더하지 않는다.
- notifications에는 내용(사용자 글·일정 제목)을 넣지 않는다. 본문은 kind별 고정 문구다(app/push.py).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable, enum_type
from app.models.enums import NotificationStatus


class Connection(TimestampedTable):
    """외부 서비스 OAuth 토큰. 토큰은 Fernet으로 암호화한 문자열만 저장한다(4단계).

    사용자·provider마다 하나(지금은 Google뿐). scopes에 Gmail 범위가 있으면 메일 도구를 쓸 수 있다.
    연결을 해제하면 행을 지우고, 승인 대기 계획을 무효로 한다(google_auth).
    action_plans.connection_id는 외래 키가 아니라, 해제·재연결 뒤에도 계획에 남은 id로
    "연결이 바뀌었다"를 알아본다(models/execution.py 머리).
    """

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
    """브라우저 웹 푸시 구독 하나(기기·브라우저마다 하나, endpoint 고유). 알림을 끄면 지운다.

    p256dh·auth는 브라우저가 준 구독 키다(보낼 때 pywebpush에 넘긴다). 만료된 구독은 보낼 때 지운다(app/push.py).
    """

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
