"""EXTERNAL 실행기(2단계 보강, BUILD_SPEC 3.3·4.2). 예전 assert_approved를 대신한다.

맡은 일: 승인된 실행 계획을 "한 번만, 승인한 인자 그대로" 실행하게 하는 서버 쪽 두 번째 방어선과,
실행 결과를 영수증·operation 상태로 남기는 일, 결과를 모를 때 외부에 다시 묻는 일(재조회).

진입점(누가 부르는지):
    begin_execution·finish_execution  EXTERNAL 도구 함수 본문(app/tools/gcal.py의 일정 3개, tests/fake_tools.py)
    reconcile_operation               finish_execution(결과 unknown일 때 곧바로 한 번), reconcile_step
    reconcile_step                    tracking.py(복구 때 실행 도중 끊긴 EXTERNAL step을 다시 실행하지 않고 확정)

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

근거: BUILD_SPEC 3.3(DB 승인 검사), 3.5(대상 변경), 4.1~4.3(보장 범위·외부 작업 상태·중복 방지),
절대 원칙 3·4·5·6·10. 결정은 docs/DECISIONS.md의 "2단계 보강"(2026-11-17 assert_approved → begin_execution,
영수증), "3단계 보강"(2026-11-17 재조회, 취소와 실행 시작의 경합 FOR SHARE), "4단계 보강"(2026-11-17 일정 ETag·
target_changed → stale) 절.

변경 이력:
    2026-11-17 2단계 보강: assert_approved를 begin_execution·finish_execution(영수증)으로 바꿈
    2026-11-17 3단계 보강: 결과 재조회(reconcile_*), task FOR SHARE 잠금
    2026-11-17 4단계 보강: Execution.target_version, target_changed → operation stale
    2026-11-17 7단계 보강: connection_revoked(invalid_grant로 무효화된 계획) 안내 문구

고칠 때 주의:
- operation·step 상태 전이를 바꾸면 docs/STATE_MACHINES.md 2절(operation)·step 절을 같이 고친다. 그 표의 "누가" 칸에
  begin_execution, refusal_for, early_result, receipt_result, finish_execution, operation_status_for, start_reconcile,
  reconcile_operation이 적혀 있어 이름을 바꾸면 tests/test_state_machines.py가 실패한다.
- 장애 지점별 동작(재조회, 영수증 재사용)을 바꾸면 docs/FAULTS.md 표와 tests/test_faults.py·tests/test_fake_service.py.
  검사 순서·거절 코드는 tests/test_executor.py, 취소 경합은 tests/test_cancel_race.py, 일정 도구는 tests/test_gcal_*.py.
- error_code 문자열은 다른 파일과 글자로 맞물린다: "connection_removed"·"connection_revoked"는 google_auth.py의
  CONNECTION_REMOVED·CONNECTION_REVOKED, TARGET_CHANGED는 app/tools/gcal.py가 돌려주는 "target_changed".
  operations.error_code에는 정해진 코드만 넣는다(자유 문장 금지, docs/FAULTS.md "로그·기록 민감 정보").
- begin_execution의 호출 모양(인자 전부를 dict로)을 바꾸면 AGENTS.md "도구 규약" 3번과 모든 EXTERNAL 도구를 고친다.
- 도구 실행은 DBOS 단계가 아니므로 이 파일만 바꿀 때 WORKFLOW_VERSION은 올리지 않는다. 테이블 칸을 바꾸면 마이그레이션.
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

# 권한 위반(PermissionError) 메시지. 무엇이 어긋났는지(해시, 승인 상태, 소유자)는 일부러 말하지 않는다.
NOT_APPROVED_MESSAGE = "승인 기록이 없는 외부 도구 실행을 막았습니다."
# 도구가 "승인 뒤 대상이 바뀌어 보내지 않음"을 알리는 error_code. app/tools/gcal.py의 문자열과 같아야 한다.
TARGET_CHANGED = "target_changed"
# 이미 실행을 시작했거나 결과를 확인 중인 상태. 이 상태에서 다시 실행하면 외부 효과가 두 번 생길 수 있다.
IN_PROGRESS = (OperationStatus.EXECUTING, OperationStatus.UNKNOWN, OperationStatus.RECONCILING)
ALREADY_RUNNING_MESSAGE = "이 외부 작업은 이미 실행 중이거나 결과 확인이 필요합니다. 다시 실행하지 않았습니다."
# 4단계 보강: 사용자가 연결을 해제해 google_auth.invalidate_pending_plans가 cancelled(connection_removed)로 닫은 경우.
CONNECTION_REMOVED_MESSAGE = (
    "외부 계정 연결이 해제되어 이 승인으로는 실행할 수 없습니다. 다시 연결한 뒤 새로 요청해 주세요."
)
# 7단계 보강: 토큰 갱신이 invalid_grant(만료·Google에서 권한 철회)라 대기 중 계획을 무효화한 경우.
CONNECTION_REVOKED_MESSAGE = (
    "Google 연결이 만료되었거나 권한이 취소되어 이 승인으로는 실행할 수 없습니다. 다시 연결한 뒤 새로 요청해 주세요."
)
CLOSED_MESSAGE = "이 외부 작업은 이미 끝났거나 실행할 수 없는 상태라 다시 실행하지 않았습니다."


@dataclass(frozen=True)
class Execution:
    """실행을 시작한 operation. operation_id는 외부 서비스에 보내는 멱등성 키다.

    operation_id는 계획을 저장할 때 서버가 만든 UUID라 복구·재시도에도 같은 값이다(일정 생성은 이 값으로 event id를
    만든다, gcal.stable_event_id). 도구는 finish_execution에 이 객체를 그대로 넘긴다.
    """

    operation_pk: int
    operation_id: uuid.UUID
    target_version: str | None = None  # 계획에 고정한 대상 버전(일정 ETag). 도구가 조건부 요청에 쓴다


@dataclass(frozen=True)
class Refusal:
    """실행 직전 검사에서 거절할 때 operation을 닫을 상태·코드와 모델에게 돌려줄 문장."""

    status: OperationStatus
    error_code: str
    message: str


@dataclass
class PlanRow:
    """한 번의 조회로 함께 읽은 계획·승인·operation·task·step(operation·step은 잠긴 상태)."""

    plan: ActionPlan
    approval: Approval
    operation: Operation
    task: Task
    step: Step


# ---- 1. 실행 시작 ----


async def locked_plan(session: AsyncSession, ctx: RunContext[AgentDeps]) -> PlanRow | None:
    """이 사용자·task·호출의 가장 최근 회차 계획. operation·step 행을 잠근다.

    모델이 만든 tool_call_id는 전체에서 유일하지 않으므로(BUILD_SPEC 3.2) task_id·user_id로 범위를 좁히고, 같은 id가
    여러 회차에 있으면 가장 최근 approval_round를 쓴다. 없으면 None(→ 호출한 쪽이 PermissionError).
    부수 효과: operation·step을 FOR UPDATE로 잠근다(같은 계획의 동시 실행이 한 줄로 선다). 커밋은 호출한 쪽.
    """
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
                # 계획과 task 양쪽에 user_id를 건다: 다른 사용자의 계획을 tool_call_id만으로 실행하는 것을 막는다.
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
    """도구 이름·승인 상태·인자 해시. 하나라도 어긋나면 PermissionError.

    해시는 계획에 적힌 도구 이름·버전으로 지금 받은 인자를 다시 계산해 비교한다. 재개 때 override_args로 승인된
    인자가 들어오므로 정상 경로에서는 늘 같고, 다르면 기록 속 모델 인자로 실행하려 한 것이다(승인 고정 인자 위반).
    이 검사는 early_result보다 먼저다: succeeded인 계획이라도 인자가 다르면 영수증을 돌려주지 않고 막는다.
    """
    plan = row.plan
    supplied = plans.args_hash(plan.tool_name, plan.tool_version, to_jsonable_python(args))
    if plan.tool_name != tool_name or row.approval.status != ApprovalStatus.APPROVED or supplied != plan.args_hash:
        raise PermissionError(NOT_APPROVED_MESSAGE)


async def receipt_result(session: AsyncSession, operation_pk: int, operation_id: uuid.UUID) -> ToolResult:
    """이미 succeeded인 operation의 결과를 저장된 영수증으로 다시 만든다(외부를 다시 부르지 않는다).

    영수증 저장 뒤 워크플로가 끝나기 전에 죽은 경우(docs/FAULTS.md 지점 4)에 쓴다. 영수증이 없으면 unknown(no_receipt):
    절대 원칙 10(성공 표시는 영수증에 근거)을 지키려는 것이다. DB를 읽기만 한다.
    """
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
    """approved가 아닌 operation이면 실행하지 않고 돌려줄 결과. approved면 None.

    approved만 실행으로 넘어가므로 operation 하나는 많아야 한 번 executing이 된다(at-most-once).
    planned·awaiting_approval은 승인 전이라 PermissionError다. 끝난 operation은 상태를 바꾸지 않고 결과만 돌려준다.
    connection_* 코드는 google_auth.invalidate_pending_plans가 남긴 이유라 일반 "끝남" 대신 다시 연결하라고 안내한다.
    """
    status = row.operation.status
    if status == OperationStatus.APPROVED:
        return None
    if status == OperationStatus.SUCCEEDED:
        return await receipt_result(session, row.operation.id, row.plan.operation_id)
    if status in IN_PROGRESS:
        # 다른 시도가 실행 중이거나 결과를 모르는 상태. 다시 보내면 중복 효과가 생길 수 있어 unknown으로 둔다.
        return ToolResult.unknown(ALREADY_RUNNING_MESSAGE, error_code="already_executing")
    if status in (OperationStatus.PLANNED, OperationStatus.AWAITING_APPROVAL):
        raise PermissionError(NOT_APPROVED_MESSAGE)
    if row.operation.error_code == "connection_removed":
        return ToolResult.failure(CONNECTION_REMOVED_MESSAGE, error_code="connection_removed")
    if row.operation.error_code == "connection_revoked":
        return ToolResult.failure(CONNECTION_REVOKED_MESSAGE, error_code="connection_revoked")
    return ToolResult.failure(CLOSED_MESSAGE, error_code="operation_closed")


# 검사 순서가 곧 우선순위다: 취소 → 실행 유효 시간 → 도구 버전 → 연결. 대상 버전(ETag)은 도구가 실행 중에 본다.
async def refusal_for(session: AsyncSession, row: PlanRow, now: datetime) -> Refusal | None:
    """실행 직전 상태 검사(BUILD_SPEC 3.3). 실행해도 되면 None.

    거절하면 begin_execution이 operation을 Refusal.status로 닫는다(다시 실행되지 않는다). DB는 연결 id만 읽는다.
    고칠 때 주의: "5분" 문구는 plans.EXECUTE_WINDOW와 같아야 한다.
    """
    # 취소 요청 뒤에 외부 효과를 새로 만들지 않는다(task는 FOR SHARE로 잠겨 취소가 이 검사 뒤에 끼어들 수 없다).
    if row.task.status != TaskStatus.RUNNING:
        return Refusal(OperationStatus.CANCELLED, "task_cancelled", "작업이 취소되어 실행하지 않았습니다.")
    # 오래전 승인으로 사용자가 잊은 행동을 실행하는 것을 막는다. 유효 시간이 없으면 만료로 본다(fail closed).
    if row.approval.execute_before is None or row.approval.execute_before <= now:
        message = "승인 후 실행 가능 시간(5분)이 지나 실행하지 않았습니다. 필요하면 다시 요청해 주세요."
        return Refusal(OperationStatus.EXPIRED, "approval_window_expired", message)
    # 승인한 뒤 도구가 보내는 요청 모양이 바뀌었으면(규격 version 올림·도구 삭제) 사용자가 본 것과 다른 일이 된다.
    spec = plans.EXTERNAL_TOOL_SPECS.get(row.plan.tool_name)
    if spec is None or spec.version != row.plan.tool_version:
        message = "도구가 바뀌어 이 승인으로는 실행할 수 없습니다. 다시 요청해 주세요."
        return Refusal(OperationStatus.STALE, "plan_outdated", message)
    # 승인 뒤 다른 계정으로 다시 연결했으면 사용자가 생각한 계정이 아닌 곳에 쓰게 된다. 계획 때 연결이 없었어도 거절.
    if spec.provider is not None:
        current = await plans.connection_id_for(session, row.plan.user_id, spec.provider)
        if row.plan.connection_id is None or current != row.plan.connection_id:
            message = (
                "승인 뒤 외부 계정 연결이 바뀌었거나 해제되어 실행하지 않았습니다. 연결을 확인하고 다시 요청해 주세요."
            )
            return Refusal(OperationStatus.FAILED, "connection_changed", message)
    return None


async def begin_execution(ctx: RunContext[AgentDeps], args: dict[str, Any]) -> Execution | ToolResult:
    """EXTERNAL 도구 함수의 첫 줄. 실행해도 되면 Execution, 아니면 돌려줄 ToolResult. 권한 위반은 PermissionError.

    args는 도구 함수가 받은 인자 전부여야 한다(빠지면 해시가 달라 PermissionError).
    PermissionError는 tracking이 삼키지 않는다: 승인 없는 실행 시도를 일반 실패로 숨기지 않으려는 것이다.
    부수 효과: 한 트랜잭션에서 task FOR SHARE → operation·step FOR UPDATE를 잡고, 거절이면 operation을 닫고,
    통과면 operation executing(attempt_count +1, started_at)·step running으로 바꿔 커밋한다(외부 호출 전 커밋).
    """
    # tool_call_id가 없으면 프레임워크 밖에서 함수를 직접 부른 것이다. 계획을 찾을 수 없으니 막는다.
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
            # step은 running을 거치지 않는다. step 결과는 이 ToolResult로 tracking이 기록한다.
            close_operation(row.operation, refusal.status, refusal.error_code, now)
            await session.commit()
            return ToolResult.failure(refusal.message, error_code=refusal.error_code)
        row.operation.status = OperationStatus.EXECUTING
        row.operation.attempt_count = (row.operation.attempt_count or 0) + 1
        row.operation.started_at = now
        row.step.status = StepStatus.RUNNING
        # 외부 호출 전에 커밋한다: 이후 죽으면 executing으로 남아 복구가 다시 보내지 않고 재조회한다(reconcile_step).
        await session.commit()
        return Execution(
            operation_pk=row.operation.id, operation_id=row.plan.operation_id, target_version=row.plan.target_version
        )


def close_operation(operation: Operation, status: OperationStatus, error_code: str | None, now: datetime) -> None:
    """operation을 끝 상태(또는 unknown)로 기록한다. 커밋은 호출한 쪽. error_code에는 정해진 코드만 넣는다."""
    operation.status = status
    operation.error_code = error_code
    operation.finished_at = now


# ---- 2. 실행 결과 기록 ----


# 도구 결과 상태 → operation 상태. ToolStatus에 값을 더하면 여기도 더한다(없으면 operation_status_for가 KeyError).
OPERATION_STATUS_OF = {
    ToolStatus.SUCCEEDED: OperationStatus.SUCCEEDED,
    ToolStatus.FAILED: OperationStatus.FAILED,
    ToolStatus.UNKNOWN: OperationStatus.UNKNOWN,
}


def operation_status_for(result: ToolResult) -> OperationStatus:
    """대상이 승인 뒤 바뀌어 실행하지 않은 결과(target_changed)는 stale(다시 승인 필요)이다.

    BUILD_SPEC 3.5: 다른 기기에서 고친 일정을 조용히 덮어쓰지 않고 계획을 stale로 만든 뒤 새 계획·새 승인을 받는다.
    """
    if result.error_code == TARGET_CHANGED:
        return OperationStatus.STALE
    return OPERATION_STATUS_OF[result.status]


def new_receipt(operation: Operation, provider: str, resource_id: str | None, result: ToolResult) -> ExecutionReceipt:
    """영수증 행(아직 add 전). summary의 data·sources는 receipt_result가 같은 결과를 다시 만들 때 쓴다.

    summary에는 결과 내용(일정 제목 등)이 남는다. 보관은 영수증 90일(automation/retention.py, docs/DATA_POLICY.md).
    """
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
    불변 조건: succeeded와 영수증은 같은 트랜잭션에서 함께 생긴다(영수증 없는 succeeded를 만들지 않는다).
    부수 효과: operation FOR UPDATE, 영수증 INSERT, 커밋. unknown이면 reconcile_operation이 외부를 한 번 더 부른다.
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

# 모델에게 주는 문장이다(도구 결과). 같은 행동을 다시 시도해 외부 효과가 두 번 생기는 것을 막으려고 지시를 넣었다.
# tracking.py에도 같은 이름의 문구가 따로 있다(복구 때 끊긴 EXTERNAL 아닌 호출용). 문장을 고치면 둘 다 본다.
UNCERTAIN_MESSAGE = (
    "외부 서비스에서 이 작업의 결과를 확인하지 못했습니다. "
    "같은 행동을 다시 하지 말고, 확인이 필요하다고 사용자에게 알리세요."
)
NOT_EXECUTED_MESSAGE = "외부 서비스에 처리 기록이 없어 실행되지 않은 것으로 확인했습니다. 필요하면 다시 요청해 주세요."
# 재조회할 수 있는 상태. 값은 IN_PROGRESS와 같지만 뜻이 달라 따로 둔다(실행 거부 대상 / 재조회 대상).
RECONCILABLE = (OperationStatus.EXECUTING, OperationStatus.UNKNOWN, OperationStatus.RECONCILING)


async def start_reconcile(deps: AgentDeps, operation_pk: int) -> plans.PlanRef | ToolResult:
    """재조회할 operation이면 reconciling으로 바꾸고 계획 값을, 아니면 돌려줄 결과를 준다.

    succeeded면 영수증 결과, 이미 끝난 상태면 failed(operation_closed). 이 사용자의 operation이 없으면 .one()이
    예외를 던진다(호출하는 쪽은 자기 계획에서 찾은 pk만 넘긴다). 부수 효과: operation FOR UPDATE, reconciling 커밋.
    PlanRef는 세션 밖에서 외부 조회에 쓰려고 값을 복사한 것이다(외부 호출 동안 트랜잭션을 열어 두지 않는다).
    실행 유효 시간이 지나도 재조회는 한다(BUILD_SPEC 3.4: 이미 제출된 호출의 결과 확인).
    """
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
    """도구 규격의 재조회 함수를 부른다. 없거나 실패하면 unknown(성공·실패로 단정하지 않는다).

    재조회 함수가 없는 도구는 "처리 기록 없음"을 확인할 방법이 없어 unknown이다(DECISIONS 3단계 보강).
    예외는 모두 삼키고 경고 로그만 남긴다. 예외 메시지는 install_log_redaction이 지운다(외부 오류 속 사용자 글).
    """
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
    unknown_result: finish_execution이 넘긴 도구의 원래 unknown 결과. 여전히 모르면 그 문장·코드를 그대로 쓴다.
    외부 쓰기를 다시 보내지 않는다(조회만). 부수 효과: 조회 뒤 operation FOR UPDATE, 영수증 INSERT, 커밋.
    unknown으로 남으면 runner가 task를 needs_review로 끝낸다(runner.has_unresolved_operation).
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
    """실행 도중 끊긴 EXTERNAL 호출(step running)을 다시 실행하지 않고 결과를 확정한다(복구 때 StepTracking이 부름).

    step → 계획 → operation을 user_id 조건으로 찾는다. 계획이 없으면(2단계 보강 전 승인) 조회할 키가 없어 unknown.
    """
    async with deps.db() as session:
        operation_pk = await session.scalar(
            select(Operation.id)
            .join(ActionPlan, ActionPlan.id == Operation.plan_id)
            .where(ActionPlan.step_id == step_id, ActionPlan.user_id == deps.user_id)
        )
    if operation_pk is None:  # 2단계 보강 전 승인(계획 없음)
        return ToolResult.unknown(UNCERTAIN_MESSAGE, error_code="interrupted")
    return await reconcile_operation(deps, operation_pk)
