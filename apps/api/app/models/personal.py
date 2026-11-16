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
