"""5단계 보강: 실행 기록 보관 기간 정리(BUILD_SPEC 5.2, docs/DATA_POLICY.md).

- EXECUTION_RETENTION_DAYS(기본 7일)가 지난 끝난 작업: 모델 기록(tasks.message_history), 단계 입력·출력,
  실행 계획 인자(canonical_args), 처리한 outbox 요청, DBOS 워크플로 기록(입력·단계 결과)을 지운다.
  대화 메시지(사용자에게 보인 질문·답)와 작업·단계의 상태·문구, 승인 카드 문장, 비용 기록은 남는다.
- RECEIPT_RETENTION_DAYS(기본 90일)가 지난 실행 영수증을 지운다.
- 끝나지 않은 작업(대기·실행·승인 대기·취소 요청·재조회)과 결과를 모르는 외부 작업은 건드리지 않는다
  (실행 중인 워크플로 기록을 지우면 복구가 망가진다).
1분 검사가 한 시간에 한 번 부른다(`check.run_check`). 한 번에 BATCH개까지만 정리하고 남은 것은 다음 번에 한다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from dbos import DBOS
from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.durable import durable_step
from app.agent.outbox import run_workflow_id
from app.config import Settings
from app.models import (
    ActionPlan,
    AgentRun,
    AutomationOccurrence,
    ExecutionReceipt,
    Operation,
    OutboxEvent,
    Step,
    Task,
)
from app.models.enums import OperationStatus, OutboxStatus, TaskStatus

logger = logging.getLogger(__name__)

IN_PROGRESS_STATUSES = (
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
    TaskStatus.WAITING_APPROVAL,
    TaskStatus.CANCEL_REQUESTED,
    TaskStatus.RECONCILING,
)
# 아직 결과를 다시 조회할 수 있어야 하는 외부 작업(계획 인자가 필요하다)
OPEN_OPERATION_STATUSES = (OperationStatus.APPROVED, OperationStatus.EXECUTING, OperationStatus.UNKNOWN)
FINISHED_WORKFLOW_STATUSES = ["SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"]
BATCH = 200
RETENTION_MINUTE = 17  # 매시 17분 검사에서 정리한다


@dataclass(frozen=True)
class PrunedRows:
    tasks_pruned: int
    receipts_deleted: int
    active_workflow_ids: list[str]  # 지우면 안 되는 워크플로(끝나지 않은 작업·대기 중인 시작 요청)


@dataclass(frozen=True)
class RetentionReport:
    tasks_pruned: int
    receipts_deleted: int
    workflows_deleted: int


async def prune_expired_records(
    factory: async_sessionmaker[AsyncSession], settings: Settings, now: datetime
) -> RetentionReport:
    """DB 정리는 DBOS 단계로, 워크플로 기록 삭제는 DBOS가 스스로 단계로 기록한다(워크플로 안에서도 한 번만)."""
    rows = await durable_step("prune_records", prune_rows, factory, settings, now)
    cutoff = now - timedelta(days=settings.execution_retention_days)
    workflows = await forget_finished_workflows(cutoff, set(rows.active_workflow_ids))
    return RetentionReport(rows.tasks_pruned, rows.receipts_deleted, workflows)


async def prune_rows(factory: async_sessionmaker[AsyncSession], settings: Settings, now: datetime) -> PrunedRows:
    async with factory() as session:
        task_ids = await expired_task_ids(session, now - timedelta(days=settings.execution_retention_days))
        await clear_task_payloads(session, task_ids)
        receipts = await delete_old_receipts(session, now - timedelta(days=settings.receipt_retention_days))
        await session.execute(
            delete(AutomationOccurrence).where(
                AutomationOccurrence.scheduled_for < now - timedelta(days=settings.execution_retention_days)
            )
        )
        active = await active_workflow_ids(session)
        await session.commit()
    return PrunedRows(len(task_ids), receipts, sorted(active))


async def expired_task_ids(session: AsyncSession, cutoff: datetime) -> list[int]:
    """보관 기간이 지난 끝난 작업 중 아직 지울 것이 남은 것(이미 정리한 작업은 다시 고르지 않는다)."""
    open_operation = exists().where(
        ActionPlan.task_id == Task.id,
        Operation.plan_id == ActionPlan.id,
        Operation.status.in_(OPEN_OPERATION_STATUSES),
    )
    has_payload = or_(
        Task.message_history.is_not(None),
        exists().where(Step.task_id == Task.id, or_(Step.output.is_not(None), Step.input != {})),
        exists().where(OutboxEvent.task_id == Task.id, OutboxEvent.status != OutboxStatus.PENDING),
    )
    rows = await session.scalars(
        select(Task.id)
        .where(Task.status.not_in(IN_PROGRESS_STATUSES), Task.updated_at < cutoff, ~open_operation, has_payload)
        .order_by(Task.id)
        .limit(BATCH)
    )
    return list(rows)


async def clear_task_payloads(session: AsyncSession, task_ids: list[int]) -> None:
    if not task_ids:
        return
    await session.execute(update(Task).where(Task.id.in_(task_ids)).values(message_history=None))
    await session.execute(update(Step).where(Step.task_id.in_(task_ids)).values(input={}, output=None))
    await session.execute(update(ActionPlan).where(ActionPlan.task_id.in_(task_ids)).values(canonical_args={}))
    await session.execute(
        delete(OutboxEvent).where(OutboxEvent.task_id.in_(task_ids), OutboxEvent.status != OutboxStatus.PENDING)
    )


async def delete_old_receipts(session: AsyncSession, cutoff: datetime) -> int:
    old = select(ExecutionReceipt.id).where(ExecutionReceipt.confirmed_at < cutoff).limit(BATCH)
    result = await session.execute(delete(ExecutionReceipt).where(ExecutionReceipt.id.in_(old)))
    return getattr(result, "rowcount", 0) or 0


async def active_workflow_ids(session: AsyncSession) -> set[str]:
    open_tasks = select(Task.id).where(Task.status.in_(IN_PROGRESS_STATUSES))
    runs = await session.scalars(select(AgentRun.workflow_id).where(AgentRun.task_id.in_(open_tasks)))
    pending = await session.scalars(select(OutboxEvent.workflow_id).where(OutboxEvent.status == OutboxStatus.PENDING))
    tasks = await session.scalars(open_tasks)
    return {*filter(None, runs), *pending, *(run_workflow_id(task_id) for task_id in tasks)}


async def forget_finished_workflows(cutoff: datetime, keep: set[str]) -> int:
    """보관 기간 전에 끝난 DBOS 워크플로 기록을 지운다(작업·예약·감시·1분 검사 모두). 실패하면 다음 번에 다시 한다."""
    try:
        finished = await DBOS.list_workflows_async(
            status=FINISHED_WORKFLOW_STATUSES,
            completed_before=cutoff.isoformat(),
            limit=BATCH,
            load_input=False,
            load_output=False,
        )
        ids = [status.workflow_id for status in finished if status.workflow_id not in keep]
        if ids:
            await DBOS.delete_workflows_async(ids)
        return len(ids)
    except Exception:
        logger.warning("보관 기간이 지난 워크플로 기록을 지우지 못함(다음 정리 때 다시 한다)")
        return 0
