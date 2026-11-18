"""기억 화면 응답(app/routers/memories.py). 화면 사용처는 apps/web/lib/memories.ts.

기억을 만들거나 고치는 요청 모델은 없다(기억은 채팅의 remember 도구로만 생긴다, 절대 원칙 9).
고칠 때 주의: 필드를 바꾸면 gen:api, apps/web/lib/memories.ts, tests/test_memories.py, test_memory_quotes.py.
"""

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
    # 5단계 보강: 근거가 된 사용자 메시지 부분(서버가 원문에 있는지 확인한 인용). 보강 전 기억은 null.
    source_quote: str | None = None
    # 출처 대화를 지웠으면 null(기억은 남는다)
    source: MemorySourceOut | None


class MemoryListOut(BaseModel):
    """GET /api/memories 응답. memories는 최근 것부터. limit은 저장할 수 있는 최대 개수(200)."""

    memories: list[MemoryOut]
    limit: int


class MemoriesDeleted(BaseModel):
    """DELETE /api/memories(전체 삭제) 응답. 지운 개수."""

    deleted: int
