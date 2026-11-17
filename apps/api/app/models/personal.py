"""사용자의 개인 데이터: memories, notes, todos."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable


class Memory(TimestampedTable):
    """사용자가 직접 말한 기억 한 줄. 출처 메시지를 함께 저장한다."""

    __tablename__ = "memories"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(String(300))
    source_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    # 5단계 보강: 근거가 된 사용자 메시지 부분(그대로 옮긴 것, 서버가 원문에 있는지 확인). 보강 전 기억은 비어 있다.
    source_quote: Mapped[str | None] = mapped_column(Text)


class Note(TimestampedTable):
    __tablename__ = "notes"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)


class Todo(TimestampedTable):
    __tablename__ = "todos"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    is_done: Mapped[bool] = mapped_column(Boolean, server_default="false")
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
