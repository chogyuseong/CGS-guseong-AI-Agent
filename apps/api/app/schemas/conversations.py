from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models.enums import MessageRole

DEFAULT_CONVERSATION_TITLE = "새 대화"
MAX_MESSAGE_LENGTH = 4000

# 앞뒤 공백을 지운 뒤 1~4000자. 공백만 보내면 422.
MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_LENGTH)]
TitleText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class ConversationCreate(BaseModel):
    # 비우거나 생략하면 '새 대화'
    title: TitleText | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: MessageText


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: MessageRole
    content: str
    task_id: int | None
    created_at: datetime


class TaskCreated(BaseModel):
    """메시지 전송 응답(202). 화면은 이 task_id로 GET /api/tasks/{id}를 조회한다."""

    task_id: int
