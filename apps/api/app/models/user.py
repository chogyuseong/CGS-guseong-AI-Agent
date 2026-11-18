"""계정과 로그인 세션: users, sessions.

진입점: 계정은 app/cli.py create-user로만 만든다. 세션은 app/security.py가 로그인 때 만들고 로그아웃 때 지운다.
근거: BUILD_SPEC 10.3(서버 저장형 세션), DECISIONS 2026-11-16(bcrypt), 2026-11-17(세션을 sessions 테이블로).

고칠 때 주의:
- 컬럼을 바꾸면 Alembic 마이그레이션(`uv run python -m alembic revision --autogenerate`)과
  docs/DATA_POLICY.md 표를 함께 본다.
- users를 지우면 거의 모든 테이블이 ondelete=CASCADE로 함께 지워진다(dbos 스키마 기록은 7일 정리, DATA_POLICY.md).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable


class User(TimestampedTable):
    """로그인 계정. 회원가입 화면은 없고 create-user 명령으로만 만든다.

    password_hash는 bcrypt 해시다(app/security.py). 평문 비밀번호는 저장하지 않는다.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # (이력) 6단계에 있던 briefing_seeded_at(아침 브리핑 자동 생성 시각)은 6단계 보강에서 지웠다. 브리핑은 이제
    # 사용자가 켤 때만 만든다(마이그레이션 ee5651ce6b47, app/automation/store.py enable_briefing).
    # 7단계 보강: 메일 데이터 처리 동의 시각(BUILD_SPEC 5.2 "메일 원문: 사용자 동의 후 LLM 전송").
    # null이면 메일 도구가 Gmail을 부르지 않는다(app/tools/gmail.py mail_refusal). 철회하면 다시 null.
    # 동의·철회는 PUT/DELETE /api/connections/google/mail-consent(app/routers/connections.py).
    mail_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(TimestampedTable):
    """서버 저장형 로그인 세션(BUILD_SPEC 10.3). Django의 DB 세션 백엔드와 같은 방식이다.

    쿠키에는 무작위 토큰만 들어가고, DB에는 그 SHA-256 해시만 저장한다(DB가 새어도 쿠키를 만들 수 없다).
    로그아웃하면 행을 지우므로, 미리 복사해 둔 쿠키도 바로 쓸 수 없게 된다.
    """

    __tablename__ = "sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
