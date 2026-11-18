"""기억 API (5단계, 기억 화면용). 명세 API 목록의 GET/DELETE /memories.

    GET    /api/memories              → 200 MemoryListOut: 내 기억 전부(최근 것부터) + 각 기억의 출처 메시지
    DELETE /api/memories/{memory_id}  → 204 / 남의 것이거나 없으면 404
    DELETE /api/memories              → 200 {deleted: n} 내 기억 전체 삭제(확인은 화면이 한 번 묻는다)

- 모든 조회·삭제에 user_id 조건. 남의 기억은 "없는 기억"과 똑같이 404(존재 여부를 드러내지 않는다).
- 상태 변경(DELETE)은 앱 전체 Origin 검사 미들웨어를 거친다(app/security.py).
- 삭제는 행을 지운다(명세). 지운 기억은 다음 모델 요청의 지시문부터 빠진다(app/agent/prompts.py).
- 기억은 이 API로 만들거나 고치지 않는다. 기억은 채팅의 remember 도구로만, 서버 검사를 거쳐 생긴다.
  (절대 원칙 9: 기억은 명시적 요청과 실제 출처에서만. 규칙은 docs/SAFETY.md "기억 오염 방어")
- 대화를 지워도 기억은 남고 출처 링크만 끊긴다(memories.source_message_id ON DELETE SET NULL → source=null).

고칠 때 주의:
- 경로·응답 모양을 바꾸면 gen:api 후 apps/web/lib/memories.ts(목록·하나 삭제·전체 삭제)를 고친다.
- 응답의 limit은 app/tools/memories.py의 MAX_MEMORIES(저장 한도)다. 값을 바꾸면 화면 문구도 확인한다.
- 관련 테스트: tests/test_memories.py, test_memory_quotes.py, test_conversation_delete.py.
"""

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select

from app.db import DbSession
from app.models import Memory, Message
from app.schemas.memories import MemoriesDeleted, MemoryListOut, MemoryOut, MemorySourceOut
from app.security import CurrentUser
from app.tools.memories import MAX_MEMORIES, delete_memory

router = APIRouter(prefix="/memories", tags=["memories"])


def source_out(source: Message | None) -> MemorySourceOut | None:
    """기억을 만든 사용자 메시지("원래 메시지 보기"). 출처 메시지가 없으면 None."""
    if source is None:  # 출처 대화를 지웠다(기억은 남는다)
        return None
    return MemorySourceOut(
        message_id=source.id,
        conversation_id=source.conversation_id,
        task_id=source.task_id,
        content=source.content,
        created_at=source.created_at,
    )


def memory_out(memory: Memory, source: Message | None) -> MemoryOut:
    """기억 행 → 응답. source_quote는 서버가 원문에 있는지 확인한 인용이다(5단계 보강 전 기억은 None)."""
    return MemoryOut(
        id=memory.id,
        content=memory.content,
        created_at=memory.created_at,
        source_quote=memory.source_quote,
        source=source_out(source),
    )


# 내 기억 전부(최근 것부터). 출처 메시지는 outer join이라 출처가 지워진 기억도 빠지지 않는다.
# 소유자 조건은 Memory.user_id 하나다(messages 테이블에는 user_id가 없다).
@router.get("")
async def list_memories(user: CurrentUser, session: DbSession) -> MemoryListOut:
    rows = await session.execute(
        select(Memory, Message)
        .outerjoin(Message, Memory.source_message_id == Message.id)
        .where(Memory.user_id == user.id)
        .order_by(Memory.created_at.desc(), Memory.id.desc())
    )
    return MemoryListOut(memories=[memory_out(memory, source) for memory, source in rows], limit=MAX_MEMORIES)


# 기억 하나 삭제. delete_memory가 user_id 조건으로 지우고 커밋한다. 지운 행이 없으면(없음·남의 것) 404.
@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "내 기억 중에 그 id가 없다"}},
)
async def delete_one(memory_id: int, user: CurrentUser, session: DbSession) -> Response:
    if not await delete_memory(session, user.id, memory_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="기억을 찾을 수 없습니다.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# 내 기억 전체 삭제(커밋). 되돌릴 수 없어 화면이 한 번 묻는다. 0개여도 200 {deleted: 0}.
@router.delete("")
async def delete_all(user: CurrentUser, session: DbSession) -> MemoriesDeleted:
    result = await session.execute(delete(Memory).where(Memory.user_id == user.id))
    await session.commit()
    return MemoriesDeleted(deleted=int(getattr(result, "rowcount", 0) or 0))
