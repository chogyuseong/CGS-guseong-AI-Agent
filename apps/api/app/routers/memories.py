"""기억 API (5단계, 기억 화면용). 명세 API 목록의 GET/DELETE /memories.

    GET    /api/memories              → 200 MemoryListOut: 내 기억 전부(최근 것부터) + 각 기억의 출처 메시지
    DELETE /api/memories/{memory_id}  → 204 / 남의 것이거나 없으면 404
    DELETE /api/memories              → 200 {deleted: n} 내 기억 전체 삭제(확인은 화면이 한 번 묻는다)

- 모든 조회·삭제에 user_id 조건. 남의 기억은 "없는 기억"과 똑같이 404(존재 여부를 드러내지 않는다).
- 상태 변경(DELETE)은 앱 전체 Origin 검사 미들웨어를 거친다(app/security.py).
- 삭제는 행을 지운다(명세). 지운 기억은 다음 모델 요청의 지시문부터 빠진다(app/agent/prompts.py).
- 기억은 이 API로 만들거나 고치지 않는다. 기억은 채팅의 remember 도구로만, 서버 검사를 거쳐 생긴다.
"""

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select

from app.db import DbSession
from app.models import Memory, Message
from app.schemas.memories import MemoriesDeleted, MemoryListOut, MemoryOut, MemorySourceOut
from app.security import CurrentUser
from app.tools.memories import MAX_MEMORIES, delete_memory

router = APIRouter(prefix="/memories", tags=["memories"])


def memory_out(memory: Memory, source: Message) -> MemoryOut:
    return MemoryOut(
        id=memory.id,
        content=memory.content,
        created_at=memory.created_at,
        source_quote=memory.source_quote,
        source=MemorySourceOut(
            message_id=source.id,
            conversation_id=source.conversation_id,
            task_id=source.task_id,
            content=source.content,
            created_at=source.created_at,
        ),
    )


@router.get("")
async def list_memories(user: CurrentUser, session: DbSession) -> MemoryListOut:
    rows = await session.execute(
        select(Memory, Message)
        .join(Message, Memory.source_message_id == Message.id)
        .where(Memory.user_id == user.id)
        .order_by(Memory.created_at.desc(), Memory.id.desc())
    )
    return MemoryListOut(memories=[memory_out(memory, source) for memory, source in rows], limit=MAX_MEMORIES)


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "내 기억 중에 그 id가 없다"}},
)
async def delete_one(memory_id: int, user: CurrentUser, session: DbSession) -> Response:
    if not await delete_memory(session, user.id, memory_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="기억을 찾을 수 없습니다.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("")
async def delete_all(user: CurrentUser, session: DbSession) -> MemoriesDeleted:
    result = await session.execute(delete(Memory).where(Memory.user_id == user.id))
    await session.commit()
    return MemoriesDeleted(deleted=int(getattr(result, "rowcount", 0) or 0))
