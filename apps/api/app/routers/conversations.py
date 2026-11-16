"""대화방: 만들기, 목록, 메시지 조회, 메시지 전송.

모든 조회에 user_id 조건을 건다. 남의 대화는 "없는 대화"와 똑같이 404로 답한다
(403이면 그 id가 존재한다는 사실이 드러난다).
"""

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import RunServices, run_task
from app.db import DbSession
from app.models import Conversation, Message, Task
from app.models.enums import MessageRole, TaskOrigin
from app.rate_limit import SlidingWindowRateLimiter
from app.schemas.conversations import (
    DEFAULT_CONVERSATION_TITLE,
    ConversationCreate,
    ConversationOut,
    MessageCreate,
    MessageOut,
    TaskCreated,
)
from app.security import CurrentUser

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def get_owned_conversation(session: AsyncSession, conversation_id: int, user_id: int) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="대화를 찾을 수 없습니다.")
    return conversation


def run_services(request: Request) -> RunServices:
    state = request.app.state
    return RunServices(
        agent=state.agent, guard=state.guard_agent, session_factory=state.session_factory, settings=state.settings
    )


@router.get("")
async def list_conversations(user: CurrentUser, session: DbSession) -> list[ConversationOut]:
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    return [ConversationOut.model_validate(row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(body: ConversationCreate, user: CurrentUser, session: DbSession) -> ConversationOut:
    conversation = Conversation(user_id=user.id, title=body.title or DEFAULT_CONVERSATION_TITLE)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: int, user: CurrentUser, session: DbSession) -> list[MessageOut]:
    conversation = await get_owned_conversation(session, conversation_id, user.id)
    rows = await session.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at, Message.id)
    )
    return [MessageOut.model_validate(row) for row in rows]


@router.post(
    "/{conversation_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    responses={429: {"description": "분당 전송 한도(20회)를 넘었다"}},
)
async def send_message(
    conversation_id: int, body: MessageCreate, request: Request, user: CurrentUser, session: DbSession
) -> TaskCreated:
    """메시지를 저장하고 task를 만들어 실행한다.

    1단계: 에이전트 실행이 끝날 때까지 기다렸다가 202를 돌려준다.
    3단계: 실행을 DBOS 워크플로에 맡기고 바로 202를 돌려준다(응답 모양은 같다).
    화면은 응답의 task_id로 GET /api/tasks/{id}를, 대화 내용은 GET .../messages를 조회한다.
    """
    conversation = await get_owned_conversation(session, conversation_id, user.id)
    limiter: SlidingWindowRateLimiter = request.app.state.message_rate_limiter
    if not limiter.allow(user.id):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, detail="메시지를 너무 자주 보냈습니다. 잠시 뒤 다시 보내 주세요."
        )
    message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=body.content)
    session.add(message)
    await session.flush()
    task = Task(conversation_id=conversation.id, user_id=user.id, trigger_message_id=message.id, origin=TaskOrigin.CHAT)
    session.add(task)
    await session.flush()
    message.task_id = task.id
    conversation.updated_at = func.now()
    await session.commit()
    # 요청 세션은 여기서 더 쓰지 않는다. 실행기는 자기 세션을 따로 연다.
    await run_task(run_services(request), task.id, prompt=body.content)
    return TaskCreated(task_id=task.id)
