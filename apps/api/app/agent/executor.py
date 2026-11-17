"""EXTERNAL 실행기(2단계 보강, BUILD_SPEC 3.3·4.2). 예전 assert_approved를 대신한다.

EXTERNAL 도구 함수는 이렇게 쓴다(예: tests/fake_tools.py, app/tools/gcal.py).

    started = await begin_execution(ctx, {"value": value})   # 함수의 인자 전부
    if isinstance(started, ToolResult):
        return started                                     # 실행하지 않은 이유, 또는 저장된 영수증
    ...외부 호출(멱등성 키 = started.operation_id)...
    return await finish_execution(ctx.deps, started, result, provider="google", resource_id=...)

begin_execution이 실행 직전에 보는 것(operation 행을 잠근 채):

    검사                                   어긋나면
    사용자·task·계획 소유권, 도구 이름      PermissionError (권한 위반. 일반 실패로 삼키지 않는다)
    승인 상태(approved), 인자 해시          PermissionError
    operation이 이미 succeeded              외부를 다시 부르지 않고 저장된 영수증 결과
    executing·unknown·reconciling           unknown(다른 시도가 있거나 결과 확인 필요, 다시 실행 안 함)
    이미 닫힌 operation(failed 등)          failed(operation_closed)
    task 취소 요청·취소                     failed(task_cancelled), operation cancelled
    실행 유효 시간(승인+5분) 지남           failed(approval_window_expired), operation expired
    도구 버전이 계획과 다름                 failed(plan_outdated), operation stale
    연결 해제·다른 연결                     failed(connection_changed), operation failed
    대상 버전                               4단계(Google ETag 조건부 요청)에서 도구가 검사한다

통과하면 operation을 executing(시도 횟수 +1)으로, step을 running으로 바꾸고 커밋한 뒤 돌려준다.
finish_execution은 결과에 따라 영수증을 남기고 operation을 succeeded/failed/unknown으로 닫는다.
화면의 "완료"는 영수증이 있을 때만이다(tracking.receipt_checked가 영수증 없는 성공을 unknown으로 바꾼다).

모델이 만든 문구는 권한 판단에 쓰지 않는다. 판단 근거는 DB의 계획·승인·operation·task·연결뿐이다.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import RunContext
from pydantic_core import to_jsonable_python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import plans
from app.agent.deps import AgentDeps
from app.agent.results import ToolResult, ToolStatus
from app.models import ActionPlan, Approval, ExecutionReceipt, Operation, Step, Task
from app.models.enums import ApprovalStatus, OperationStatus, StepStatus, TaskStatus

logger = logging.getLogger(__name__)

NOT_APPROVED_MESSAGE = "승인 기록이 없는 외부 도구 실행을 막았습니다."
TARGET_CHANGED = "target_changed"
IN_PROGRESS = (OperationStatus.EXECUTING, OperationStatus.UNKNOWN, OperationStatus.RECONCILING)
ALREADY_RUNNING_MESSAGE = "이 외부 작업은 이미 실행 중이거나 결과 확인이 필요합니다. 다시 실행하지 않았습니다."
CLOSED_MESSAGE = "이 외부 작업은 이미 끝났거나 실행할 수 없는 상태라 다시 실행하지 않았습니다."


@dataclass(frozen=True)
class Execution:
    """실행을 시작한 operation. operation_id는 외부 서비스에 보내는 멱등성 키다."""

    operation_pk: int
    operation_id: uuid.UUID
    target_version: str | None = None  # 계획에 고정한 대상 버전(일정 ETag). 도구가 조건부 요청에 쓴다


@dataclass(frozen=True)
class Refusal:
    status: OperationStatus
    error_code: str
    message: str


@dataclass
class PlanRow:
    plan: ActionPlan
    approval: Approval
    operation: Operation
    task: Task
    step: Step


# ---- 1. 실행 시작 ----


async def locked_plan(session: AsyncSession, ctx: RunContext[AgentDeps]) -> PlanRow | None:
    """이 사용자·task·호출의 가장 최근 회차 계획. operation·step 행을 잠근다."""
    deps = ctx.deps
    row = (
        await session.execute(
            select(ActionPlan, Approval, Operation, Task, Step)
            .join(Approval, Approval.plan_id == ActionPlan.id)
            .join(Operation, Operation.plan_id == ActionPlan.id)
            .join(Task, Task.id == ActionPlan.task_id)
            .join(Step, Step.id == ActionPlan.step_id)
            .where(
                ActionPlan.task_id == deps.task_id,
                ActionPlan.tool_call_id == ctx.tool_call_id,
                ActionPlan.user_id == deps.user_id,
                Task.user_id == deps.user_id,
            )
            .order_by(ActionPlan.approval_round.desc())
            .limit(1)
            .with_for_update(of=[Operation, Step])
        )
    ).one_or_none()
    return PlanRow(*row) if row is not None else None


def check_authorized(row: PlanRow, tool_name: str | None, args: dict[str, Any]) -> None:
    """도구 이름·승인 상태·인자 해시. 하나라도 어긋나면 PermissionError."""
    plan = row.plan
    supplied = plans.args_hash(plan.tool_name, plan.tool_version, to_jsonable_python(args))
    if plan.tool_name != tool_name or row.approval.status != ApprovalStatus.APPROVED or supplied != plan.args_hash:
        raise PermissionError(NOT_APPROVED_MESSAGE)


async def receipt_result(session: AsyncSession, operation_pk: int, operation_id: uuid.UUID) -> ToolResult:
    receipt = await session.scalar(select(ExecutionReceipt).where(ExecutionReceipt.operation_id == operation_pk))
    if receipt is None:  # succeeded인데 영수증이 없으면 완료로 보지 않는다
        return ToolResult.unknown(ALREADY_RUNNING_MESSAGE, error_code="no_receipt")
    return ToolResult(
        status=ToolStatus.SUCCEEDED,
        data=receipt.summary.get("data"),
        sources=list(receipt.summary.get("sources") or []),
        operation_id=str(operation_id),
        receipt_id=str(receipt.id),
    )


async def early_result(session: AsyncSession, row: PlanRow) -> ToolResult | None:
    """approved가 아닌 operation이면 실행하지 않고 돌려줄 결과. approved면 None."""
    status = row.operation.status
    if status == OperationStatus.APPROVED:
        return None
    if status == OperationStatus.SUCCEEDED:
        return await receipt_result(session, row.operation.id, row.plan.operation_id)
    if status in IN_PROGRESS:
        return ToolResult.unknown(ALREADY_RUNNING_MESSAGE, error_code="already_executing")
    if status in (OperationStatus.PLANNED, OperationStatus.AWAITING_APPROVAL):
        raise PermissionError(NOT_APPROVED_MESSAGE)
    return ToolResult.failure(CLOSED_MESSAGE, error_code="operation_closed")


async def refusal_for(session: AsyncSession, row: PlanRow, now: datetime) -> Refusal | None:
    """실행 직전 상태 검사(BUILD_SPEC 3.3). 실행해도 되면 None."""
    if row.task.status != TaskStatus.RUNNING:
        return Refusal(OperationStatus.CANCELLED, "task_cancelled", "작업이 취소되어 실행하지 않았습니다.")
    if row.approval.execute_before is None or row.approval.execute_before <= now:
        message = "승인 후 실행 가능 시간(5분)이 지나 실행하지 않았습니다. 필요하면 다시 요청해 주세요."
        return Refusal(OperationStatus.EXPIRED, "approval_window_expired", message)
    spec = plans.EXTERNAL_TOOL_SPECS.get(row.plan.tool_name)
    if spec is None or spec.version != row.plan.tool_version:
        message = "도구가 바뀌어 이 승인으로는 실행할 수 없습니다. 다시 요청해 주세요."
        return Refusal(OperationStatus.STALE, "plan_outdated", message)
    if spec.provider is not None:
        current = await plans.connection_id_for(session, row.plan.user_id, spec.provider)
        if row.plan.connection_id is None or current != row.plan.connection_id:
            message = (
                "승인 뒤 외부 계정 연결이 바뀌었거나 해제되어 실행하지 않았습니다. 연결을 확인하고 다시 요청해 주세요."
            )
            return Refusal(OperationStatus.FAILED, "connection_changed", message)
    return None


async def begin_execution(ctx: RunContext[AgentDeps], args: dict[str, Any]) -> Execution | ToolResult:
    """EXTERNAL 도구 함수의 첫 줄. 실행해도 되면 Execution, 아니면 돌려줄 ToolResult. 권한 위반은 PermissionError."""
    if not ctx.tool_call_id:
        raise PermissionError(NOT_APPROVED_MESSAGE)
    now = datetime.now(UTC)
    async with ctx.deps.db() as session:
        # task 행을 FOR SHARE로 잠근다: 취소(FOR UPDATE)와 실행 시작이 한 줄로 서서, 취소가 확인 뒤에 끼어들지 못한다.
        await session.execute(select(Task.id).where(Task.id == ctx.deps.task_id).with_for_update(read=True))
        row = await locked_plan(session, ctx)
        if row is None:
            raise PermissionError(NOT_APPROVED_MESSAGE)
        check_authorized(row, ctx.tool_name, args)
        early = await early_result(session, row)
        if early is not None:
            return early
        refusal = await refusal_for(session, row, now)
        if refusal is not None:
            close_operation(row.operation, refusal.status, refusal.error_code, now)
            await session.commit()
            return ToolResult.failure(refusal.message, error_code=refusal.error_code)
        row.operation.status = OperationStatus.EXECUTING
        row.operation.attempt_count = (row.operation.attempt_count or 0) + 1
        row.operation.started_at = now
        row.step.status = StepStatus.RUNNING
        await session.commit()
        return Execution(
            operation_pk=row.operation.id, operation_id=row.plan.operation_id, target_version=row.plan.target_version
        )


def close_operation(operation: Operation, status: OperationStatus, error_code: str | None, now: datetime) -> None:
    operation.status = status
    operation.error_code = error_code
    operation.finished_at = now


# ---- 2. 실행 결과 기록 ----


OPERATION_STATUS_OF = {
    ToolStatus.SUCCEEDED: OperationStatus.SUCCEEDED,
    ToolStatus.FAILED: OperationStatus.FAILED,
    ToolStatus.UNKNOWN: OperationStatus.UNKNOWN,
}


def operation_status_for(result: ToolResult) -> OperationStatus:
    """대상이 승인 뒤 바뀌어 실행하지 않은 결과(target_changed)는 stale(다시 승인 필요)이다."""
    if result.error_code == TARGET_CHANGED:
        return OperationStatus.STALE
    return OPERATION_STATUS_OF[result.status]


def new_receipt(operation: Operation, provider: str, resource_id: str | None, result: ToolResult) -> ExecutionReceipt:
    return ExecutionReceipt(
        operation_id=operation.id,
        user_id=operation.user_id,
        provider=provider,
        external_resource_id=resource_id,
        summary={"data": to_jsonable_python(result.data), "sources": result.sources},
        confirmed_at=datetime.now(UTC),
    )


async def finish_execution(
    deps: AgentDeps, execution: Execution, result: ToolResult, *, provider: str, resource_id: str | None = None
) -> ToolResult:
    """외부 호출 결과를 기록한다. 성공이면 영수증을 만들고 결과에 operation_id·receipt_id를 붙여 돌려준다.

    결과를 모르면(unknown: 시간 초과·연결 끊김) 실패로 단정하지 않고 바로 한 번 재조회한다(3단계 보강).
    """
    now = datetime.now(UTC)
    extra: dict[str, Any] = {"operation_id": str(execution.operation_id)}
    async with deps.db() as session:
        operation = await session.get_one(Operation, execution.operation_pk, with_for_update=True)
        if result.status == ToolStatus.SUCCEEDED:
            receipt = new_receipt(operation, provider, resource_id, result)
            session.add(receipt)
            await session.flush()
            extra["receipt_id"] = str(receipt.id)
        close_operation(operation, operation_status_for(result), result.error_code, now)
        await session.commit()
    finished = result.model_copy(update=extra)
    if finished.status == ToolStatus.UNKNOWN:
        return await reconcile_operation(deps, execution.operation_pk, finished)
    return finished


# ---- 3. 결과 재조회 (3단계 보강, BUILD_SPEC 4.2·4.3) ----

UNCERTAIN_MESSAGE = (
    "외부 서비스에서 이 작업의 결과를 확인하지 못했습니다. "
    "같은 행동을 다시 하지 말고, 확인이 필요하다고 사용자에게 알리세요."
)
NOT_EXECUTED_MESSAGE = "외부 서비스에 처리 기록이 없어 실행되지 않은 것으로 확인했습니다. 필요하면 다시 요청해 주세요."
RECONCILABLE = (OperationStatus.EXECUTING, OperationStatus.UNKNOWN, OperationStatus.RECONCILING)


async def start_reconcile(deps: AgentDeps, operation_pk: int) -> plans.PlanRef | ToolResult:
    """재조회할 operation이면 reconciling으로 바꾸고 계획 값을, 아니면 돌려줄 결과를 준다."""
    async with deps.db() as session:
        row = (
            await session.execute(
                select(Operation, ActionPlan)
                .join(ActionPlan, ActionPlan.id == Operation.plan_id)
                .where(Operation.id == operation_pk, Operation.user_id == deps.user_id)
                .with_for_update(of=[Operation])
            )
        ).one()
        operation, plan = row[0], row[1]
        if operation.status == OperationStatus.SUCCEEDED:
            return await receipt_result(session, operation.id, plan.operation_id)
        if operation.status not in RECONCILABLE:
            return ToolResult.failure(CLOSED_MESSAGE, error_code="operation_closed")
        operation.status = OperationStatus.RECONCILING
        ref = plans.PlanRef(
            plan.operation_id, plan.user_id, plan.tool_name, dict(plan.canonical_args), plan.target_resource_id
        )
        await session.commit()
        return ref


async def ask_external(deps: AgentDeps, ref: plans.PlanRef) -> plans.ReconcileOutcome:
    """도구 규격의 재조회 함수를 부른다. 없거나 실패하면 unknown(성공·실패로 단정하지 않는다)."""
    spec = plans.EXTERNAL_TOOL_SPECS.get(ref.tool_name)
    if spec is None or spec.reconcile is None:
        return plans.ReconcileOutcome("unknown")
    try:
        return await spec.reconcile(deps, ref)
    except Exception:
        logger.warning("재조회 실패: tool=%s operation=%s", ref.tool_name, ref.operation_id, exc_info=True)
        return plans.ReconcileOutcome("unknown")


async def reconcile_operation(
    deps: AgentDeps, operation_pk: int, unknown_result: ToolResult | None = None
) -> ToolResult:
    """operation_id로 외부 결과를 다시 물어 확정한다.

    처리 기록이 있으면 영수증(succeeded), 없다고 확인되면 failed(not_executed), 모르면 unknown(단정하지 않음).
    """
    started = await start_reconcile(deps, operation_pk)
    if isinstance(started, ToolResult):
        return started
    outcome = await ask_external(deps, started)
    now = datetime.now(UTC)
    extra: dict[str, Any] = {"operation_id": str(started.operation_id)}
    async with deps.db() as session:
        operation = await session.get_one(Operation, operation_pk, with_for_update=True)
        if outcome.result == "done":
            result = ToolResult.success(to_jsonable_python(outcome.data))
            provider = outcome.provider or started.tool_name
            receipt = new_receipt(operation, provider, outcome.resource_id, result)
            session.add(receipt)
            await session.flush()
            close_operation(operation, OperationStatus.SUCCEEDED, None, now)
            extra["receipt_id"] = str(receipt.id)
        elif outcome.result == "not_done":
            result = ToolResult.failure(NOT_EXECUTED_MESSAGE, error_code="not_executed")
            close_operation(operation, OperationStatus.FAILED, "not_executed", now)
        else:
            result = unknown_result or ToolResult.unknown(UNCERTAIN_MESSAGE, error_code="interrupted")
            close_operation(operation, OperationStatus.UNKNOWN, result.error_code, now)
        await session.commit()
    return result.model_copy(update=extra)


async def reconcile_step(deps: AgentDeps, step_id: int) -> ToolResult:
    """실행 도중 끊긴 EXTERNAL 호출(step running)을 다시 실행하지 않고 결과를 확정한다(복구 때 StepTracking이 부름)."""
    async with deps.db() as session:
        operation_pk = await session.scalar(
            select(Operation.id)
            .join(ActionPlan, ActionPlan.id == Operation.plan_id)
            .where(ActionPlan.step_id == step_id, ActionPlan.user_id == deps.user_id)
        )
    if operation_pk is None:  # 2단계 보강 전 승인(계획 없음)
        return ToolResult.unknown(UNCERTAIN_MESSAGE, error_code="interrupted")
    return await reconcile_operation(deps, operation_pk)
