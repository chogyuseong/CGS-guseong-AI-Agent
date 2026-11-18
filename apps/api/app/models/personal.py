"""사용자의 개인 데이터: memories, notes, todos.

진입점: 도구 app/tools/notes.py(메모·할 일), app/tools/memories.py(remember·forget·list_memories),
화면 API app/routers/memories.py. 기억은 prompts.memory_instruction이 최근 30줄을 매 요청 지시문에 넣는다.
모든 조회·수정에 user_id 조건을 건다(AGENTS.md 도구 규약 2).

고칠 때 주의:
- 컬럼을 바꾸면 Alembic 마이그레이션(`uv run python -m alembic revision --autogenerate`)과 docs/DATA_POLICY.md
  "기억" 줄을 함께 본다. 세 테이블 모두 자동 정리(retention.py) 대상이 아니다. 메모·할 일은 DATA_POLICY 표에 줄이 없다.
- memories.content 길이(String(300), 명세 "한 줄 300자")는 app/tools/memories.py의 MAX_CONTENT_LENGTH와 같아야 한다.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable


class Memory(TimestampedTable):
    """사용자가 직접 말한 기억 한 줄. 출처 메시지를 함께 저장한다.

    절대 원칙 9(기억은 명시적 요청과 실제 출처에서만): remember 서버 검사(app/tools/memories.py quote_problem)가
    source_quote가 사용자 메시지 원문에 있는지 확인한 뒤에만 행을 만든다. 규칙은 docs/SAFETY.md "기억 오염 방어".
    """

    __tablename__ = "memories"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(String(300))
    # 5단계 보강: 대화를 지워도 기억은 남긴다(사용자가 명시적으로 남긴 것). 출처 링크만 끊고 근거 인용은 유지한다.
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), index=True)
    # 5단계 보강: 근거가 된 사용자 메시지 부분(그대로 옮긴 것, 서버가 원문에 있는지 확인). 보강 전 기억은 비어 있다.
    source_quote: Mapped[str | None] = mapped_column(Text)


class Note(TimestampedTable):
    """메모 한 건(save_note, INTERNAL). 지우는 도구·화면은 아직 없다(사용자 행을 지우면 CASCADE로 지워진다)."""

    __tablename__ = "notes"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)


class Todo(TimestampedTable):
    """할 일 한 건(add_todo·complete_todo, INTERNAL). 완료하면 is_done과 done_at을 함께 채운다.

    이미 완료한 할 일은 다시 완료해도 done_at을 덮어쓰지 않는다(처음 완료한 시각, app/tools/notes.py).
    """

    __tablename__ = "todos"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    is_done: Mapped[bool] = mapped_column(Boolean, server_default="false")
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
