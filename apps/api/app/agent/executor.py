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

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import RunContext
from pydantic_core import to_jsonable_python
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import plans
from app.agent.deps import AgentDeps
from app.agent.results import ToolResult, ToolStatus
from app.models import ActionPlan, Approval, ExecutionReceipt, Operation, Step, Task
from app.models.enums import ApprovalStatus, OperationStatus, StepStatus, TaskStatus

NOT_APPROVED_MESSAGE = "승인 기록이 없는 외부 도구 실행을 막았습니다."
IN_PROGRESS = (OperationStatus.EXECUTING, OperationStatus.UNKNOWN, OperationStatus.RECONCILING)
ALREADY_RUNNING_MESSAGE = "이 외부 작업은 이미 실행 중이거나 결과 확인이 필요합니다. 다시 실행하지 않았습니다."
CLOSED_MESSAGE = "이 외부 작업은 이미 끝났거나 실행할 수 없는 상태라 다시 실행하지 않았습니다."


@dataclass(frozen=True)
class Execution:
    """실행을 시작한 operation. operation_id는 외부 서비스에 보내는 멱등성 키다."""

    operation_pk: int
    operation_id: uuid.UUID


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


async def receipt_result(session: AsyncSession, row: PlanRow) -> ToolResult:
    receipt = await session.scalar(select(ExecutionReceipt).where(ExecutionReceipt.operation_id == row.operation.id))
    if receipt is None:  # succeeded인데 영수증이 없으면 완료로 보지 않는다
        return ToolResult.unknown(ALREADY_RUNNING_MESSAGE, error_code="no_receipt")
    return ToolResult(
        status=ToolStatus.SUCCEEDED,
        data=receipt.summary.get("data"),
        sources=list(receipt.summary.get("sources") or []),
        operation_id=str(row.plan.operation_id),
        receipt_id=str(receipt.id),
    )


async def early_result(session: AsyncSession, row: PlanRow) -> ToolResult | None:
    """approved가 아닌 operation이면 실행하지 않고 돌려줄 결과. approved면 None."""
    status = row.operation.status
    if status == OperationStatus.APPROVED:
        return None
    if status == OperationStatus.SUCCEEDED:
        return await receipt_result(session, row)
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
        return Execution(operation_pk=row.operation.id, operation_id=row.plan.operation_id)


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


async def finish_execution(
    deps: AgentDeps, execution: Execution, result: ToolResult, *, provider: str, resource_id: str | None = None
) -> ToolResult:
    """외부 호출 결과를 기록한다. 성공이면 영수증을 만들고 결과에 operation_id·receipt_id를 붙여 돌려준다."""
    now = datetime.now(UTC)
    extra: dict[str, Any] = {"operation_id": str(execution.operation_id)}
    async with deps.db() as session:
        operation = await session.get_one(Operation, execution.operation_pk, with_for_update=True)
        if result.status == ToolStatus.SUCCEEDED:
            receipt = ExecutionReceipt(
                operation_id=operation.id,
                user_id=operation.user_id,
                provider=provider,
                external_resource_id=resource_id,
                summary={"data": to_jsonable_python(result.data), "sources": result.sources},
                confirmed_at=now,
            )
            session.add(receipt)
            await session.flush()
            extra["receipt_id"] = str(receipt.id)
        close_operation(operation, OPERATION_STATUS_OF[result.status], result.error_code, now)
        await session.commit()
    return result.model_copy(update=extra)


async def mark_interrupted(deps: AgentDeps, step_id: int) -> None:
    """실행 도중 끊긴 EXTERNAL 호출(step running)의 operation을 unknown으로 둔다(실패와 구분, 자동 재시도 금지)."""
    async with deps.db() as session:
        plan_ids = select(ActionPlan.id).where(ActionPlan.step_id == step_id, ActionPlan.user_id == deps.user_id)
        await session.execute(
            update(Operation)
            .where(Operation.plan_id.in_(plan_ids), Operation.status == OperationStatus.EXECUTING)
            .values(status=OperationStatus.UNKNOWN, error_code="interrupted")
        )
        await session.commit()
