"""5단계 보강: 실행 기록 보관 기간 정리(BUILD_SPEC 5.2, docs/DATA_POLICY.md).

- EXECUTION_RETENTION_DAYS(기본 7일)가 지난 끝난 작업: 모델 기록(tasks.message_history), 단계 입력·출력,
  실행 계획 인자(canonical_args), 처리한 outbox 요청, DBOS 워크플로 기록(입력·단계 결과)을 지운다.
  대화 메시지(사용자에게 보인 질문·답)와 작업·단계의 상태·문구, 승인 카드 문장, 비용 기록은 남는다.
- RECEIPT_RETENTION_DAYS(기본 90일)가 지난 실행 영수증을 지운다.
- 끝나지 않은 작업(대기·실행·승인 대기·취소 요청·재조회)과 결과를 모르는 외부 작업은 건드리지 않는다
  (실행 중인 워크플로 기록을 지우면 복구가 망가진다).
1분 검사가 한 시간에 한 번 부른다(`check.run_check`). 한 번에 BATCH개까지만 정리하고 남은 것은 다음 번에 한다.

- 6단계 보강: 예정 시각이 EXECUTION_RETENTION_DAYS보다 오래된 자동화 발생 기록(automation_occurrences)도 지운다.

진입점: check.run_check(매시 RETENTION_MINUTE분, automation_check 워크플로 안). IN_PROGRESS_STATUSES는
routers/conversations.py의 대화 삭제 409 검사도 쓴다.
근거: BUILD_SPEC 5.2(데이터 보관), DECISIONS 2026-11-17 "5단계 보강"(보관 기간 정리, 남기는 것)과
2026-11-18(끝난 작업에 approved operation이 남지 않게 runner.close_unexecuted_steps가 닫는다).

고칠 때 주의:
- 지우는 범위나 기간을 바꾸면 docs/DATA_POLICY.md 표, 설정 화면 문구(apps/web/components/DataRetentionNotice.tsx,
  7일·90일 고정 문구), .env.example을 같이 고친다.
- task 상태를 더하면 IN_PROGRESS_STATUSES에 넣을지 정한다(빠지면 진행 중인 작업의 복구 기록을 지운다).
  docs/STATE_MACHINES.md와 맞춘다.
- prune_records 단계 이름·위치를 바꾸면 workflows.WORKFLOW_VERSION을 올린다(1분 검사 워크플로의 단계다).
- 테스트는 run_check(services, now)의 now 분을 RETENTION_MINUTE로 맞춰 부른다(tests/test_retention.py).
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

# 끝나지 않은 task 상태. 이 작업들의 기록·워크플로는 복구에 필요해 지우지 않는다. 대화 삭제(409)도 같은 목록이다.
IN_PROGRESS_STATUSES = (
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
    TaskStatus.WAITING_APPROVAL,
    TaskStatus.CANCEL_REQUESTED,
    TaskStatus.RECONCILING,
)
# 아직 결과를 다시 조회할 수 있어야 하는 외부 작업(계획 인자가 필요하다)
OPEN_OPERATION_STATUSES = (OperationStatus.APPROVED, OperationStatus.EXECUTING, OperationStatus.UNKNOWN)
# 끝난 것으로 보고 지우는 DBOS 워크플로 상태(설치된 DBOS의 WorkflowStatusString 값).
# PENDING·ENQUEUED·DELAYED·SUSPENDED는 아직 돌 수 있어 넣지 않는다(지우면 복구가 망가진다).
FINISHED_WORKFLOW_STATUSES = ["SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"]
# 한 번에 정리하는 최대 건수(작업·영수증·워크플로 각각, DECISIONS 2026-11-17 "한 번에 200건까지").
# 밀려 있으면 몇 시간에 걸쳐 끝난다(docs/DATA_POLICY.md).
BATCH = 200
# 매시 17분 검사에서 정리한다(DECISIONS 2026-11-17 5단계 보강).
# 그 분의 검사가 돌지 않으면(서버 꺼짐) 그 시간은 건너뛰고 다음 시간에 한다.
RETENTION_MINUTE = 17


@dataclass(frozen=True)
class PrunedRows:
    """prune_records 단계 결과(DBOS에 기록). 모양을 바꾸면 WORKFLOW_VERSION을 올린다."""

    tasks_pruned: int
    receipts_deleted: int
    active_workflow_ids: list[str]  # 지우면 안 되는 워크플로(끝나지 않은 작업·대기 중인 시작 요청)


@dataclass(frozen=True)
class RetentionReport:
    """정리 한 번의 결과(로그용)."""

    tasks_pruned: int
    receipts_deleted: int
    workflows_deleted: int


async def prune_expired_records(
    factory: async_sessionmaker[AsyncSession], settings: Settings, now: datetime
) -> RetentionReport:
    """DB 정리는 DBOS 단계로, 워크플로 기록 삭제는 DBOS가 스스로 단계로 기록한다(워크플로 안에서도 한 번만).

    지킬 워크플로 목록은 DB 정리와 같은 트랜잭션에서 읽은 값이다. 예외를 내지 않는 것은 워크플로 삭제뿐이고,
    DB 오류는 1분 검사로 올라간다.
    """
    rows = await durable_step("prune_records", prune_rows, factory, settings, now)
    cutoff = now - timedelta(days=settings.execution_retention_days)
    workflows = await forget_finished_workflows(cutoff, set(rows.active_workflow_ids))
    return RetentionReport(rows.tasks_pruned, rows.receipts_deleted, workflows)


async def prune_rows(factory: async_sessionmaker[AsyncSession], settings: Settings, now: datetime) -> PrunedRows:
    """보관 기간이 지난 작업 내용·영수증·발생 기록을 한 트랜잭션으로 정리하고 커밋한다.

    사용자 구분 없이 전체를 본다(운영 정리 작업이라 user_id 조건이 없다). 남기는 것은 모듈 설명 참고.
    """
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
    """작업 내용만 비운다(행은 남김). 단계의 상태·문구, 계획의 args_hash, 승인 카드 문장은 남긴다.

    PENDING outbox 요청은 아직 시작해야 할 요청이라 지우지 않는다.
    """
    if not task_ids:
        return
    await session.execute(update(Task).where(Task.id.in_(task_ids)).values(message_history=None))
    await session.execute(update(Step).where(Step.task_id.in_(task_ids)).values(input={}, output=None))
    await session.execute(update(ActionPlan).where(ActionPlan.task_id.in_(task_ids)).values(canonical_args={}))
    await session.execute(
        delete(OutboxEvent).where(OutboxEvent.task_id.in_(task_ids), OutboxEvent.status != OutboxStatus.PENDING)
    )


async def delete_old_receipts(session: AsyncSession, cutoff: datetime) -> int:
    """확인 시각(confirmed_at)이 cutoff보다 오래된 실행 영수증을 BATCH개까지 지우고 지운 수를 돌려준다."""
    old = select(ExecutionReceipt.id).where(ExecutionReceipt.confirmed_at < cutoff).limit(BATCH)
    result = await session.execute(delete(ExecutionReceipt).where(ExecutionReceipt.id.in_(old)))
    return getattr(result, "rowcount", 0) or 0


async def active_workflow_ids(session: AsyncSession) -> set[str]:
    """지우면 안 되는 워크플로 ID: 끝나지 않은 작업의 실행·재개 워크플로와 대기 중인 outbox 요청의 워크플로.

    agent_runs에 아직 없는(시작 전) 작업도 막으려고 run_workflow_id(task id)를 같이 넣는다.
    """
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
