"""대화방: 만들기, 목록, 메시지 조회, 메시지 전송.

모든 조회에 user_id 조건을 건다. 남의 대화는 "없는 대화"와 똑같이 404로 답한다
(403이면 그 id가 존재한다는 사실이 드러난다).
"""

import logging

from dbos import DBOS
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import outbox
from app.agent.outbox import run_workflow_id
from app.agent.workflows import WorkflowLauncher
from app.automation.retention import IN_PROGRESS_STATUSES
from app.db import DbSession
from app.models import AgentRun, Conversation, Message, OutboxEvent, Task
from app.models.enums import MessageRole, TaskOrigin
from app.rate_limit import SlidingWindowRateLimiter
from app.schemas.conversations import (
    DEFAULT_CONVERSATION_TITLE,
    ConversationCreate,
    ConversationOut,
    MessageCreate,
    MessageOrigin,
    MessageOut,
    TaskCreated,
)
from app.security import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def get_owned_conversation(session: AsyncSession, conversation_id: int, user_id: int) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="대화를 찾을 수 없습니다.")
    return conversation


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


def message_origin(message: Message, task_origin: TaskOrigin | None) -> MessageOrigin:
    """메시지 출처. task가 있으면 그 task의 origin, task 없는 답변은 조건 감시 알림(app/automation/check.py)뿐이다."""
    if message.role == MessageRole.USER:
        return "chat"
    if task_origin is not None:
        return task_origin.value
    return "watch"


def message_out(message: Message, task_origin: TaskOrigin | None) -> MessageOut:
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        task_id=message.task_id,
        created_at=message.created_at,
        origin=message_origin(message, task_origin),
        sources=list(message.sources or []),
    )


# 5단계 보강: 끝나지 않은 작업(IN_PROGRESS_STATUSES)이 있으면 대화를 지우지 않는다
# (실행 중인 워크플로 기록을 지워 복구를 망가뜨리지 않게).
DELETE_BUSY = "진행 중인 작업이 있어 대화를 지울 수 없습니다. 작업을 취소하거나 끝난 뒤 다시 시도해 주세요."


async def conversation_workflow_ids(session: AsyncSession, conversation_id: int) -> list[str]:
    """이 대화의 task들이 쓴 DBOS 워크플로 ID(첫 실행·재개 회차·outbox 요청)."""
    task_ids = select(Task.id).where(Task.conversation_id == conversation_id)
    runs = await session.scalars(select(AgentRun.workflow_id).where(AgentRun.task_id.in_(task_ids)))
    queued = await session.scalars(select(OutboxEvent.workflow_id).where(OutboxEvent.task_id.in_(task_ids)))
    tasks = await session.scalars(task_ids)
    return sorted({*filter(None, runs), *queued, *(run_workflow_id(task_id) for task_id in tasks)})


async def forget_workflows(workflow_ids: list[str]) -> None:
    """DBOS 시스템 테이블의 워크플로 기록(입력의 사용자 메시지, 단계 결과의 모델 기록)을 지운다.

    실패해도 대화 삭제는 이미 끝났다. 남은 기록은 보관 기간 정리(app/automation/retention.py)가 다시 지운다.
    """
    if not workflow_ids:
        return
    try:
        await DBOS.delete_workflows_async(workflow_ids)
    except Exception:
        logger.warning(
            "대화 삭제 뒤 워크플로 기록을 지우지 못함: %d건(보관 기간 정리가 다시 지운다)", len(workflow_ids)
        )


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"description": "내 대화 중에 이 id가 없다"},
        409: {"description": "진행 중인 작업(대기·실행·승인 대기·취소 요청·재조회)이 있다"},
    },
)
async def delete_conversation(conversation_id: int, user: CurrentUser, session: DbSession) -> Response:
    """대화와 그 메시지·작업·단계·승인·실행 계획·영수증, DBOS 워크플로 기록을 지운다(BUILD_SPEC 5.5).

    이 대화에서 만든 기억은 지우지 않는다(출처 링크만 끊긴다). 비용 기록은 작업 연결만 끊긴다.
    백업에서는 바로 지워지지 않는다(docs/DATA_POLICY.md).
    """
    conversation = await get_owned_conversation(session, conversation_id, user.id)
    busy = await session.scalar(
        select(Task.id).where(Task.conversation_id == conversation.id, Task.status.in_(IN_PROGRESS_STATUSES)).limit(1)
    )
    if busy is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=DELETE_BUSY)
    workflow_ids = await conversation_workflow_ids(session, conversation.id)
    await session.delete(conversation)
    await session.commit()
    await forget_workflows(workflow_ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: int, user: CurrentUser, session: DbSession) -> list[MessageOut]:
    conversation = await get_owned_conversation(session, conversation_id, user.id)
    rows = await session.execute(
        select(Message, Task.origin)
        .outerjoin(Task, Task.id == Message.task_id)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at, Message.id)
    )
    return [message_out(message, origin) for message, origin in rows]


@router.post(
    "/{conversation_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        409: {"description": "같은 client_request_id를 다른 대화방이나 다른 내용으로 이미 썼다"},
        429: {"description": "분당 전송 한도(20회)를 넘었다"},
    },
)
async def send_message(
    conversation_id: int, body: MessageCreate, request: Request, user: CurrentUser, session: DbSession
) -> TaskCreated:
    """메시지를 저장하고 task를 만든 뒤 실행을 DBOS 워크플로(run_task, ID task-{id})에 맡기고 바로 202를 돌려준다.

    응답 시점의 task는 보통 queued다(곧 running). 화면은 응답의 task_id로 GET /api/tasks/{id}를 1초마다 조회해
    진행 상황(steps)과 결과를 그리고, 대화 내용은 GET .../messages로 다시 가져온다.
    서버가 실행 도중 재시작돼도 워크플로가 이어서 실행된다.
    """
    conversation = await get_owned_conversation(session, conversation_id, user.id)
    # 다시 보낸 요청이면 전송 한도를 쓰지 않고 처음 task를 돌려준다.
    existing = await find_resent_task(session, user.id, conversation.id, body)
    if existing is not None:
        return TaskCreated(task_id=existing)
    limiter: SlidingWindowRateLimiter = request.app.state.message_rate_limiter
    if not limiter.allow(user.id):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, detail="메시지를 너무 자주 보냈습니다. 잠시 뒤 다시 보내 주세요."
        )
    task_id = await save_message_and_task(session, user.id, conversation, body)
    if task_id is None:
        # 같은 요청이 동시에 와서 다른 쪽이 먼저 저장했다. 그 task를 돌려준다(실행은 그쪽이 시작한다).
        existing = await find_resent_task(session, user.id, conversation.id, body)
        if existing is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=REQUEST_ID_CONFLICT)
        return TaskCreated(task_id=existing)
    # 커밋한 뒤에 워크플로를 시작한다(워크플로가 task 행을 볼 수 있게). 요청 세션은 여기서 더 쓰지 않는다.
    # 시작이 실패하거나 그 전에 서버가 죽으면 outbox 요청이 pending으로 남아 1분 검사·서버 시작 때 다시 시작한다.
    launcher: WorkflowLauncher = request.app.state.launcher
    await outbox.dispatch(launcher, request.app.state.session_factory, task_id=task_id)
    return TaskCreated(task_id=task_id)


REQUEST_ID_CONFLICT = "이미 다른 내용으로 보낸 요청 번호입니다. 새로 보내 주세요."


async def find_resent_task(
    session: AsyncSession, user_id: int, conversation_id: int, body: MessageCreate
) -> int | None:
    """같은 client_request_id로 이미 만든 task id. 없으면 None, 대화방이나 내용이 다르면 409."""
    row = (
        await session.execute(
            select(Task.id, Task.conversation_id, Message.content)
            .outerjoin(Message, Message.id == Task.trigger_message_id)
            .where(Task.user_id == user_id, Task.client_request_id == body.client_request_id)
        )
    ).one_or_none()
    if row is None:
        return None
    task_id, task_conversation_id, content = row
    if task_conversation_id != conversation_id or content != body.content:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=REQUEST_ID_CONFLICT)
    return task_id


async def save_message_and_task(
    session: AsyncSession, user_id: int, conversation: Conversation, body: MessageCreate
) -> int | None:
    """메시지와 task를 한 트랜잭션으로 저장하고 task id를 돌려준다. 같은 요청이 먼저 저장됐으면 None."""
    message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=body.content)
    session.add(message)
    await session.flush()
    task = Task(
        conversation_id=conversation.id,
        user_id=user_id,
        trigger_message_id=message.id,
        origin=TaskOrigin.CHAT,
        client_request_id=body.client_request_id,
    )
    session.add(task)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return None
    message.task_id = task.id
    conversation.updated_at = func.now()
    await outbox.add_run_event(session, task)  # 3단계 보강: 실행 시작 요청도 같은 트랜잭션에
    await session.commit()
    return task.id
