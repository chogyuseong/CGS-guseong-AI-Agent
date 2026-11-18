"""대화방과 화면에 보이는 메시지: conversations, messages.

진입점: app/routers/conversations.py(목록·만들기·삭제·메시지 보내기), runner·cancel·자동화가 답변 메시지를 더한다.
보관: docs/DATA_POLICY.md "대화 메시지" 줄(대화를 지울 때까지).

고칠 때 주의:
- 컬럼을 바꾸면 Alembic 마이그레이션(`uv run python -m alembic revision --autogenerate`)을 만들고 생성 파일을 읽는다.
  API 응답 모양(app/schemas/conversations.py)이 바뀌면 `pnpm --dir apps/web gen:api`.
- 새로 저장하는 내용은 docs/DATA_POLICY.md 표에 넣는다.
"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable, enum_type
from app.models.enums import MessageRole


class Conversation(TimestampedTable):
    """대화방. 사용자 1명이 여러 개를 가진다.

    지우면 메시지·task(와 그 아래 단계·계획·영수증)가 CASCADE로 함께 지워진다. 끝나지 않은 task가 있으면
    API가 409로 막는다(app/routers/conversations.py, retention.IN_PROGRESS_STATUSES). 기억은 남는다(출처 링크만 끊김).
    updated_at은 대화 목록 정렬 기준이라 새 메시지·답변을 남기는 곳이 직접 갱신한다.
    """

    __tablename__ = "conversations"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))


class Message(TimestampedTable):
    """사용자와 에이전트의 말 한 줄. 화면에 보인 그대로이며 대화를 지울 때까지 남는다(자동 정리 없음).

    모델에게 보내는 기록은 여기가 아니라 tasks.message_history다(history.py). 화면 대화와 모델 기록은 따로 저장된다.
    """

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
