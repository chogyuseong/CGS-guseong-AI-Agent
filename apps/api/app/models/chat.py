from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable, enum_type
from app.models.enums import MessageRole


class Conversation(TimestampedTable):
    """대화방. 사용자 1명이 여러 개를 가진다."""

    __tablename__ = "conversations"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))


class Message(TimestampedTable):
    """사용자와 에이전트의 말 한 줄."""

    __tablename__ = "messages"

    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[MessageRole] = mapped_column(enum_type(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text)
    # tasks.trigger_message_id와 서로를 가리키는 순환 참조라서 use_alter로 테이블을 다 만든 뒤 FK를 추가한다.
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL", use_alter=True), index=True)
    # 8단계 추가: 조건 감시 알림의 출처 링크(감시 도구 결과의 sources). 그 밖의 메시지는 null.
    # 본문의 "출처:" 줄을 파싱하지 않게 따로 둔다(요약 글은 외부 글의 영향을 받은 모델 출력이라 줄을 꾸밀 수 있다).
    sources: Mapped[list[str] | None] = mapped_column(JSONB)
    # 6단계 보강: 예약 작업 답의 제안 [{title, proposed_user_message, reason}] (app/agent/replies.py). 그 밖은 null.
    suggestions: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)
