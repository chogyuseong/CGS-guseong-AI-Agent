"""Step 기록: 도구 호출 한 번마다 steps 행 하나를 남긴다.

호출 흐름 (모델이 add_todo를 부른 경우):

    모델 응답: ToolCallPart(add_todo, {"title": "우유 사기"}, tool_call_id="abc")
      │
      ├─ Pydantic AI가 인자를 검증한다 ── 실패(없는 도구, 잘못된 인자) → 모델에게 다시 시도하라고 알림.
      │                                   도구가 실행되지 않았으므로 step도 만들지 않는다.
      ▼
    StepTracking.wrap_tool_execute   ← Pydantic AI capability의 "도구 실행 감싸기" hook
      1. start_step: steps 행을 status=running으로 만들고 커밋(화면이 진행 중임을 볼 수 있게)
      2. handler(args): 실제 도구 함수 실행
           └─ 도구가 예상 못 한 예외를 내면 on_tool_execute_error가 ToolResult(status=failed)로 바꾼다(한 곳에서).
      3. finish_step: output, ok, status(done/failed), duration_ms를 채우고 커밋
      ▼
    도구 결과(ToolResult)가 모델에게 돌아간다

왜 hook인가: Pydantic AI 2.x 공식 문서의 Hooks(도구 실행 wrap_tool_execute)가 "도구 호출을 감싸는" 가장 단순한
방법이다. 도구마다 기록 코드를 넣지 않아도 되고, 새 도구를 추가해도 기록이 빠질 수 없다.

3단계(DBOS 복구): 서버가 죽었다 살아나면 워크플로가 처음부터 다시 실행된다. 모델 요청은 DBOS 단계라 기록된
응답이 그대로 돌아오고(같은 tool_call_id), 도구 실행은 단계가 아니라서 이 hook이 다시 불린다. 그래서 hook은
(task_id, tool_call_id)로 steps 행을 먼저 찾아 아래처럼 처리한다. 도구가 같은 호출로 두 번 실행되지 않는다.

    행 상태                     처리
    없음                         새 행(running)을 만들고 실행한다(보통의 첫 실행)
    done / failed(결과 있음)     실행하지 않고 저장된 결과(output)를 그대로 돌려준다(복구 재생)
    running, READ 도구           조회만 하므로 다시 실행한다
    running, INTERNAL            지난 실행이 도중에 끊겼다. 반영됐는지 알 수 없으므로 다시 실행하지 않고
                                 unknown + "실행 여부를 확인할 수 없음"으로 닫는다(at-most-once, docs/SAFETY.md)
    running, EXTERNAL            다시 실행하지 않고 외부 서비스에 operation_id로 결과를 묻는다(3단계 보강,
                                 executor.reconcile_step): 처리됨 → 영수증, 처리 기록 없음 → 실패, 모름 → unknown
    waiting_approval             승인된 EXTERNAL 호출의 첫 실행. 그대로 실행한다(executor.begin_execution이 검사)

7단계(메일): list_mail·read_mail 결과는 output_for_storage로 앞 500자만 steps.output에 저장한다(명세 [보안]).
모델에게는 원래 결과(본문 4,000자)가 간다. 복구 재생 때는 저장된 500자 결과가 돌아간다(READ라 다시 읽으면 된다).

EXTERNAL 도구(2단계): requires_approval 도구는 첫 run에서 실행되지 않으므로 이 hook도 불리지 않는다. step 행은
runner가 승인 요청을 저장할 때(approvals.save_requests) waiting_approval로 만든다. 승인 후 재개한 run에서 이 hook이
불리면 start_step은 그 행을 그대로 쓰고, 도구 첫 줄의 begin_execution이 running으로 바꾼다(2단계 보강).
EXTERNAL 도구의 성공은 영수증(receipt_id)이 있어야 한다. 없으면 receipt_checked가 unknown으로 바꾼다.
거절된 호출은 도구가 실행되지 않아 이 hook이 불리지 않고, step은 decide가 정한 rejected로 남는다.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import WrapToolExecuteHandler
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, SkipToolExecution, ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition
from pydantic_core import to_jsonable_python
from sqlalchemy import func, select

from app.agent.cancel import TaskCancelledError, raise_if_cancel_requested
from app.agent.deps import AgentDeps
from app.agent.executor import reconcile_step
from app.agent.results import ToolResult, ToolStatus, output_for_storage
from app.agent.toolsets import label_for, risk_level_of
from app.models import Step
from app.models.enums import RiskLevel, StepStatus

logger = logging.getLogger(__name__)

# 이 예외들은 failed 결과로 바꾸지 않고 그대로 올려 보낸다.
# - PermissionError: 실행기(begin_execution)의 두 번째 방어선. 삼키면 승인 없는 실행을 숨기게 된다.
# - 나머지: Pydantic AI가 흐름 제어에 쓰는 예외(재시도 요청, 승인 대기, 실행 건너뛰기 등).
# - TaskCancelledError: 1단계 보강. 취소 요청을 발견하면 도구를 실행하지 않고 run 전체를 멈춘다.
PASS_THROUGH_ERRORS = (
    PermissionError,
    TaskCancelledError,
    ModelRetry,
    ApprovalRequired,
    CallDeferred,
    SkipToolExecution,
    ToolFailed,
)
UNEXPECTED_ERROR_MESSAGE = "도구를 실행하는 중 예상하지 못한 오류가 발생했습니다."
# 서버가 도구 실행 도중 멈춘 뒤 복구했을 때. 모델에게도 이 문장이 도구 결과로 간다.
UNCERTAIN_MESSAGE = (
    "서버가 이 도구를 실행하는 도중 멈춰서 실제로 실행되었는지 확인할 수 없습니다. "
    "같은 행동을 다시 하지 말고, 확인이 필요하다고 사용자에게 알리세요."
)
INTERRUPTED_RESULT_MESSAGE = "이전 실행에서 이 도구가 끝나지 못했습니다."
NO_RECEIPT_MESSAGE = (
    "외부 서비스 처리 영수증이 없어 완료를 확인할 수 없습니다. "
    "같은 행동을 다시 하지 말고, 확인이 필요하다고 사용자에게 알리세요."
)
FINISHED_STEP_STATUSES = (StepStatus.DONE, StepStatus.FAILED, StepStatus.UNKNOWN)
STEP_STATUS_OF = {
    ToolStatus.SUCCEEDED: StepStatus.DONE,
    ToolStatus.FAILED: StepStatus.FAILED,
    ToolStatus.UNKNOWN: StepStatus.UNKNOWN,
}


@dataclass(frozen=True)
class RecordedStep:
    """이미 있는 steps 행(복구 때 다시 불린 호출)."""

    id: int
    status: StepStatus
    risk_level: RiskLevel
    output: dict[str, Any] | None


async def find_step(deps: AgentDeps, tool_call_id: str) -> RecordedStep | None:
    async with deps.db() as session:
        step = await session.scalar(select(Step).where(Step.task_id == deps.task_id, Step.tool_call_id == tool_call_id))
        if step is None:
            return None
        return RecordedStep(step.id, step.status, step.risk_level, step.output)


def recorded_result(step: RecordedStep) -> ToolResult | None:
    """끝난 호출이면 저장된 결과를 돌려준다(다시 실행하지 않는다). 아직 실행할 호출이면 None."""
    if step.status not in FINISHED_STEP_STATUSES:
        return None
    if step.output is None:
        return ToolResult.unknown(INTERRUPTED_RESULT_MESSAGE, error_code="interrupted")
    return ToolResult.model_validate(step.output)


async def close_uncertain_step(deps: AgentDeps, step_id: int, risk: RiskLevel) -> ToolResult:
    """도중에 끊긴 INTERNAL·EXTERNAL 호출을 다시 실행하지 않고 닫는다.

    EXTERNAL은 외부 서비스에 operation_id로 결과를 다시 물어 확정한다(3단계 보강: 영수증 / 실행 안 됨 / 결과 불명).
    INTERNAL은 결과 불명(unknown)으로 닫는다.
    """
    if risk == RiskLevel.EXTERNAL:
        result = await reconcile_step(deps, step_id)
    else:
        result = ToolResult.unknown(UNCERTAIN_MESSAGE, error_code="interrupted")
    await finish_step(deps, step_id, output=result.model_dump(mode="json"), result=result, duration_ms=None)
    logger.warning("복구: 도중에 끊긴 도구 호출을 닫음 task=%s step=%s status=%s", deps.task_id, step_id, result.status)
    return result


async def start_step(deps: AgentDeps, call: ToolCallPart, tool_def: ToolDefinition, args: dict[str, Any]) -> int:
    """steps 행을 running으로 만들고 id를 돌려준다."""
    async with deps.db() as session:
        last_order = await session.scalar(select(func.max(Step.order)).where(Step.task_id == deps.task_id))
        step = Step(
            task_id=deps.task_id,
            order=(last_order or 0) + 1,
            tool_name=call.tool_name,
            tool_call_id=call.tool_call_id,
            risk_level=risk_level_of(tool_def),
            input=to_jsonable_python(args),
            status=StepStatus.RUNNING,
            label=label_for(call.tool_name, args),
        )
        session.add(step)
        await session.commit()
        return step.id


def describe_result(result: object) -> ToolResult:
    """도구 결과를 ToolResult로 맞춘다. ToolResult가 아닌 값은 성공으로 보고 그대로 감싼다."""
    if isinstance(result, ToolResult):
        return result
    return ToolResult.success(to_jsonable_python(result))


async def finish_step(
    deps: AgentDeps, step_id: int, *, output: dict[str, Any] | None, result: ToolResult | None, duration_ms: int | None
) -> None:
    """result가 None이면 도구가 예외로 끝났다(실패). unknown 결과는 step도 unknown이다."""
    async with deps.db() as session:
        step = await session.get_one(Step, step_id)
        step.output = output
        step.ok = result is not None and result.ok
        step.status = StepStatus.FAILED if result is None else STEP_STATUS_OF[result.status]
        step.duration_ms = duration_ms
        await session.commit()


def receipt_checked(result: ToolResult, risk: RiskLevel) -> ToolResult:
    """EXTERNAL 도구의 성공은 영수증이 있어야 완료다(절대 원칙 10). 없으면 결과 불명으로 바꾼다."""
    if risk == RiskLevel.EXTERNAL and result.status == ToolStatus.SUCCEEDED and not result.receipt_id:
        return ToolResult.unknown(NO_RECEIPT_MESSAGE, error_code="no_receipt")
    return result


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


@dataclass
class StepTracking(AbstractCapability[AgentDeps]):
    """도구 실행을 감싸 steps에 기록하고, 예상 못 한 예외를 ok=false 결과로 바꾸는 capability.

    1단계 보강: 모델 호출·새 도구 실행 직전에 취소 요청을 확인한다.
    """

    async def before_model_request(
        self, ctx: RunContext[AgentDeps], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        await raise_if_cancel_requested(ctx.deps)
        return request_context

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        recorded = await find_step(ctx.deps, call.tool_call_id)
        if recorded is None:
            # 1단계 보강: 취소 요청 뒤에는 새 도구를 시작하지 않는다(실행 직전 확인, BUILD_SPEC 4.6).
            await raise_if_cancel_requested(ctx.deps)
            step_id = await start_step(ctx.deps, call, tool_def, args)
        else:
            replayed = recorded_result(recorded)
            if replayed is not None:
                return replayed  # 복구 재생: 이미 끝난 호출은 다시 실행하지 않는다
            if recorded.status == StepStatus.RUNNING and recorded.risk_level != RiskLevel.READ:
                return await close_uncertain_step(ctx.deps, recorded.id, recorded.risk_level)
            step_id = recorded.id
        return await self.run_and_record(ctx, call.tool_name, risk_level_of(tool_def), step_id, args, handler)

    async def run_and_record(
        self,
        ctx: RunContext[AgentDeps],
        tool_name: str,
        risk: RiskLevel,
        step_id: int,
        args: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        started = time.perf_counter()
        try:
            result = await handler(args)
        except BaseException:
            # 승인 거부(PermissionError) 같은 예외도 step에는 실패로 남기고 그대로 올려 보낸다.
            await finish_step(ctx.deps, step_id, output=None, result=None, duration_ms=elapsed_ms(started))
            raise
        original = describe_result(result)
        described = receipt_checked(original, risk)
        if described is not original:
            result = described  # 영수증 없는 외부 성공: 모델에게도 결과 불명으로 알린다
        # 7단계: 메일 도구 결과는 500자만 저장한다(모델에게 돌려주는 result는 그대로 4,000자까지).
        stored = output_for_storage(tool_name, described.model_dump(mode="json"))
        await finish_step(ctx.deps, step_id, output=stored, result=described, duration_ms=elapsed_ms(started))
        return result

    async def on_tool_execute_error(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        error: Exception,
    ) -> Any:
        """도구 안에서 난 예외를 한 곳에서 처리한다. 내부 오류 내용은 모델과 화면에 보내지 않고 로그에만 남긴다."""
        if isinstance(error, PASS_THROUGH_ERRORS):
            raise error
        logger.error("도구 실행 중 예외: tool=%s task=%s", call.tool_name, ctx.deps.task_id, exc_info=error)
        return ToolResult.failure(UNEXPECTED_ERROR_MESSAGE, error_code="unexpected")
