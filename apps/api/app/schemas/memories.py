from datetime import datetime

from pydantic import BaseModel


class MemorySourceOut(BaseModel):
    """기억의 출처: 그 기억을 만든 task를 시작한 사용자 메시지("원래 메시지 보기")."""

    message_id: int
    conversation_id: int
    task_id: int | None
    content: str
    created_at: datetime


class MemoryOut(BaseModel):
    id: int
    content: str
    created_at: datetime
    source: MemorySourceOut


class MemoryListOut(BaseModel):
    """GET /api/memories 응답. memories는 최근 것부터. limit은 저장할 수 있는 최대 개수(200)."""

    memories: list[MemoryOut]
    limit: int


class MemoriesDeleted(BaseModel):
    """DELETE /api/memories(전체 삭제) 응답. 지운 개수."""

    deleted: int
