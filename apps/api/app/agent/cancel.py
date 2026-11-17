"""작업 취소(1단계 보강, BUILD_SPEC 4.6).

취소는 이미 끝난 행동을 되돌리지 않는다. 취소 요청 뒤에 새로 시작하는 일(모델 호출, 도구 실행)만 막는다.

    queued, waiting_approval  → 바로 cancelled. 결정 전 승인은 cancelled, 실행하지 않은 단계는 실패로 닫고
                                답변을 남긴다.
                                늦게 시작한 워크플로는 claim에서 queued가 아니라 아무것도 하지 않는다.
    running                   → cancel_requested. 실행 쪽(tracking.StepTracking)이 다음 모델 호출·새 도구 실행 직전에
                                확인하고 TaskCancelledError로 멈춘다 → runner가 cancelled로 저장한다.
                                마지막 답을 이미 받았으면 그대로 done(이미 한 일을 숨기지 않는다).
    cancel_requested, cancelled → 그대로(같은 요청을 다시 보내도 같은 결과)
    그 밖의 끝난 상태          → CancelNotAllowedError(409)
"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.approvals import set_operation_status
from app.agent.deps import AgentDeps
from app.agent.results import not_executed_output
from app.models import Approval, Conversation, Message, Step, Task
from app.models.enums import ApprovalStatus, MessageRole, OperationStatus, StepStatus, TaskStatus

CANCELLED_REPLY = "작업을 취소했습니다. 취소 전에 끝난 단계는 위 기록에 그대로 남아 있습니다."
RUN_CANCELLED_MESSAGE = "취소 요청에 따라 멈췄습니다. 위 단계까지만 처리되었습니다."
NOT_EXECUTED_ON_CANCEL = "작업을 취소해 실행하지 않았습니다."
CANCEL_IMMEDIATELY = (TaskStatus.QUEUED, TaskStatus.WAITING_APPROVAL)
ALREADY_CANCELLING = (TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELLED)


class TaskCancelledError(Exception):
    """실행 쪽이 취소 요청을 발견했다. 다음 모델 호출·도구 실행을 시작하지 않는다."""


class CancelNotAllowedError(Exception):
    """이미 끝난 작업이라 취소할 것이 없다."""


async def is_cancel_requested(deps: AgentDeps) -> bool:
    async with deps.db() as session:
        current = await session.scalar(select(Task.status).where(Task.id == deps.task_id))
    return current == TaskStatus.CANCEL_REQUESTED


async def raise_if_cancel_requested(deps: AgentDeps) -> None:
    if await is_cancel_requested(deps):
        raise TaskCancelledError(deps.task_id)


async def lock_owned_task(session: AsyncSession, task_id: int, user_id: int) -> Task | None:
    return await session.scalar(
        select(Task)
        .where(Task.id == task_id, Task.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def request_cancel(session: AsyncSession, task_id: int, user_id: int) -> TaskStatus | None:
    """취소를 요청하고 바뀐(또는 지금) 상태를 돌려준다. 내 task가 아니면 None."""
    task = await lock_owned_task(session, task_id, user_id)
    if task is None:
        return None
    if task.status in ALREADY_CANCELLING:
        return task.status
    if task.status == TaskStatus.RUNNING:
        task.status = TaskStatus.CANCEL_REQUESTED
    elif task.status in CANCEL_IMMEDIATELY:
        await close_for_cancel(session, task)
    else:
        raise CancelNotAllowedError(task.status)
    status = task.status
    await session.commit()
    return status


async def close_for_cancel(session: AsyncSession, task: Task) -> None:
    """실행하지 않을 승인·단계를 닫고 task를 cancelled로 바꾼 뒤 답변을 남긴다(커밋은 부른 쪽)."""
    step_ids = select(Step.id).where(Step.task_id == task.id)
    pending_plans = select(Approval.plan_id).where(
        Approval.step_id.in_(step_ids), Approval.status == ApprovalStatus.PENDING, Approval.plan_id.is_not(None)
    )
    await set_operation_status(session, pending_plans, OperationStatus.CANCELLED)
    await session.execute(
        update(Approval)
        .where(Approval.step_id.in_(step_ids), Approval.status == ApprovalStatus.PENDING)
        .values(status=ApprovalStatus.CANCELLED, decided_at=func.now())
    )
    await session.execute(
        update(Step)
        .where(Step.task_id == task.id, Step.status == StepStatus.WAITING_APPROVAL)
        .values(status=StepStatus.FAILED, ok=False, output=not_executed_output(NOT_EXECUTED_ON_CANCEL))
    )
    task.status = TaskStatus.CANCELLED
    task.error = CANCELLED_REPLY
    session.add(
        Message(
            conversation_id=task.conversation_id, role=MessageRole.ASSISTANT, content=CANCELLED_REPLY, task_id=task.id
        )
    )
    conversation = await session.get_one(Conversation, task.conversation_id)
    conversation.updated_at = func.now()
