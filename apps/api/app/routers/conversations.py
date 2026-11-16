"""대화방 조회(읽기 전용).

0단계에서는 "다른 사용자의 대화는 404" 테스트를 위해 조회 두 개만 만든다.
대화 만들기와 메시지 전송은 1단계에서 에이전트와 함께 만든다.
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.db import DbSession
from app.models import Conversation, Message
from app.schemas.conversations import ConversationOut, MessageOut
from app.security import CurrentUser

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(user: CurrentUser, session: DbSession) -> list[ConversationOut]:
    rows = await session.scalars(
        select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc())
    )
    return [ConversationOut.model_validate(row) for row in rows]


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: int, user: CurrentUser, session: DbSession) -> list[MessageOut]:
    # 남의 대화는 "없는 대화"와 똑같이 404로 답한다(403이면 그 id가 존재한다는 사실이 드러난다).
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id)
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="대화를 찾을 수 없습니다.")
    rows = await session.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at, Message.id)
    )
    return [MessageOut.model_validate(row) for row in rows]
