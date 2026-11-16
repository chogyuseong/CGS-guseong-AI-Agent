from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import MessageRole


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: MessageRole
    content: str
    task_id: int | None
    created_at: datetime
