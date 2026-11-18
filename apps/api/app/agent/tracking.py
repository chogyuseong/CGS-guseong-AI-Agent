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

7단계 보강(메일): list_mail·read_mail 결과는 steps.output에 내용 없이 통 수만 저장한다(output_for_storage,
BUILD_SPEC 5.2). 모델에게는 원래 결과(본문 4,000자)가 간다. 복구 때 끝난 메일 단계는 재생하지 않고 다시 읽는다
(rereads_mail, READ라 흔적이 없다).

EXTERNAL 도구(2단계): requires_approval 도구는 첫 run에서 실행되지 않으므로 이 hook도 불리지 않는다. step 행은
runner가 승인 요청을 저장할 때(approvals.save_requests) waiting_approval로 만든다. 승인 후 재개한 run에서 이 hook이
불리면 start_step은 그 행을 그대로 쓰고, 도구 첫 줄의 begin_execution이 running으로 바꾼다(2단계 보강).
EXTERNAL 도구의 성공은 영수증(receipt_id)이 있어야 한다. 없으면 receipt_checked가 unknown으로 바꾼다.
거절된 호출은 도구가 실행되지 않아 이 hook이 불리지 않고, step은 decide가 정한 rejected로 남는다.

진입점: agent.py의 메인 에이전트 capabilities에 [ResolveModelId, BudgetCapability, StepTracking, DBOSDurability]
순서로 들어간다. 감시(guard.py)·감시 판정(automation/watch.py) 에이전트에는 없다(도구를 쓰지 않는다).
테스트는 StepTracking().wrap_tool_execute(...)를 직접 부르기도 한다(test_mail_privacy.py, test_mail_automation.py).

근거: BUILD_SPEC 2.3(권한 위반·워크플로 제어 예외를 일반 도구 실패로 삼키지 않는다, 원시 예외 메시지를 보내지 않는다),
1.4 절대 원칙 8(성공은 영수증으로 확인)·9(불확실한 결과를 단정하지 않는다)·10(서버 재시작 전제),
4.6(취소). DECISIONS 2026-11-16 "예외는 한 곳(on_tool_execute_error)에서 ok=false로".

고칠 때 주의:
- step 상태 값이나 전이를 바꾸면 docs/STATE_MACHINES.md "6. 참고: step"과 tests/test_state_machines.py를 같이 고친다.
- 복구 표(위)의 분기를 바꾸면 tests/test_workflows.py의 test_recorded_*·test_interrupted_*, EXTERNAL 재조회는
  tests/test_executor.py, 장애 결과 표 docs/FAULTS.md를 확인한다.
- steps.output 저장 모양을 바꾸면 recorded_result(복구 재생)가 ToolResult.model_validate로 다시 읽을 수 있어야 하고,
  화면(apps/web의 단계 표시)도 같은 모양을 읽는다. 메일·내 경험 저장 규칙은 results.output_for_storage와
  SEALED_TOOLS가 정한다.
- 실행 직전 데이터 정책 검사(refusal_before_run)의 규칙은 data_policy.py에 있다. 여기서는 부르는 자리만 정한다.
- 도구 실행은 DBOS 단계가 아니므로, 이 파일의 DB 쓰기(start_step·finish_step)는 각각 짧은 트랜잭션으로 바로 커밋한다.
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
from app.agent.data_policy import refusal_before_run
from app.agent.deps import AgentDeps
from app.agent.executor import reconcile_step
from app.agent.results import SEALED_TOOLS, ToolResult, ToolStatus, output_for_storage
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
# 예상 못 한 예외의 결과 문장. 원시 예외 메시지(DB 주소, 키 조각 등)가 모델·화면으로 새지 않게 고정 문구만 쓴다
# (BUILD_SPEC 2.3). tests/test_agent_run.py가 원래 예외 문장이 output에 없는지 본다.
UNEXPECTED_ERROR_MESSAGE = "도구를 실행하는 중 예상하지 못한 오류가 발생했습니다."
# 서버가 도구 실행 도중 멈춘 뒤 복구했을 때. 모델에게도 이 문장이 도구 결과로 간다.
# "다시 하지 말라"는 문장은 모델이 같은 쓰기를 한 번 더 시도해 중복을 만드는 사고를 막으려는 것이다.
UNCERTAIN_MESSAGE = (
    "서버가 이 도구를 실행하는 도중 멈춰서 실제로 실행되었는지 확인할 수 없습니다. "
    "같은 행동을 다시 하지 말고, 확인이 필요하다고 사용자에게 알리세요."
)
# 끝난 상태(done/failed/unknown)인데 output이 없는 step을 재생할 때. 그대로 올려 보낸 예외(PASS_THROUGH_ERRORS)로
# 닫힌 step이 이 모양이다(run_and_record의 except). 실행했는지 단정하지 않으려고 unknown으로 돌려준다.
INTERRUPTED_RESULT_MESSAGE = "이전 실행에서 이 도구가 끝나지 못했습니다."
# EXTERNAL 도구가 succeeded를 돌려줬는데 receipt_id가 없을 때(receipt_checked). 영수증 없는 "완료" 표시를 막는다.
NO_RECEIPT_MESSAGE = (
    "외부 서비스 처리 영수증이 없어 완료를 확인할 수 없습니다. "
    "같은 행동을 다시 하지 말고, 확인이 필요하다고 사용자에게 알리세요."
)
# 복구 때 다시 실행하지 않고 저장 결과를 재생하는 상태. rejected·expired 호출은 도구가 실행되지 않아 보통 이 hook까지
# 오지 않는다. 그 밖의 상태(running·waiting_approval 등)는 wrap_tool_execute의 분기를 탄다. EXTERNAL은 실행으로
# 넘어가도 도구 첫 줄의 begin_execution이 승인·계획을 다시 검사한다(두 번째 방어선).
FINISHED_STEP_STATUSES = (StepStatus.DONE, StepStatus.FAILED, StepStatus.UNKNOWN)
# ToolResult.status → steps.status. 새 ToolStatus를 만들면 여기에 넣지 않으면 finish_step이 KeyError로 멈춘다.
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
    """이 task에서 같은 tool_call_id로 만든 steps 행을 찾는다. 없으면 None(첫 실행).

    복구 판정의 열쇠다: 모델 응답은 DBOS가 재생하므로 같은 호출은 같은 tool_call_id로 다시 온다.
    task_id 조건이 user_id 조건을 대신한다(task는 한 사용자 것). 세션은 읽기만 하고 닫는다.
    """
    async with deps.db() as session:
        step = await session.scalar(select(Step).where(Step.task_id == deps.task_id, Step.tool_call_id == tool_call_id))
        if step is None:
            return None
        return RecordedStep(step.id, step.status, step.risk_level, step.output)


def recorded_result(step: RecordedStep) -> ToolResult | None:
    """끝난 호출이면 저장된 결과를 돌려준다(다시 실행하지 않는다). 아직 실행할 호출이면 None.

    끝났는데 output이 없으면(예외로 닫힌 step) unknown(interrupted)이다. 저장된 예전 모양({ok, error})은
    ToolResult.accept_legacy_shape가 새 모양으로 읽는다.
    """
    if step.status not in FINISHED_STEP_STATUSES:
        return None
    if step.output is None:
        return ToolResult.unknown(INTERRUPTED_RESULT_MESSAGE, error_code="interrupted")
    return ToolResult.model_validate(step.output)


def rereads_mail(tool_name: str, step: RecordedStep) -> bool:
    """7단계 보강: 성공한 메일 단계는 저장분에 메일 내용이 없어(output_for_storage) 재생하지 않고 다시 읽는다.

    메일 도구는 READ라 다시 불러도 흔적이 없다. 동의를 철회했거나 권한이 사라졌으면 다시 읽기가 실패로 끝난다.
    """
    return tool_name in SEALED_TOOLS and step.status == StepStatus.DONE  # 2026-11-18: 내 경험도 같은 규칙


async def close_uncertain_step(deps: AgentDeps, step_id: int, risk: RiskLevel) -> ToolResult:
    """도중에 끊긴 INTERNAL·EXTERNAL 호출을 다시 실행하지 않고 닫는다.

    EXTERNAL은 외부 서비스에 operation_id로 결과를 다시 물어 확정한다(3단계 보강: 영수증 / 실행 안 됨 / 결과 불명).
    INTERNAL은 결과 불명(unknown)으로 닫는다. 다시 실행하면 할 일이 두 번 생기는 식의 중복이 날 수 있어서다
    (at-most-once). 부수 효과: finish_step으로 step을 닫고 커밋한다. EXTERNAL 재조회의 operation 상태 전이는
    executor.reconcile_step 쪽이 맡는다(docs/STATE_MACHINES.md의 reconciling).
    """
    if risk == RiskLevel.EXTERNAL:
        result = await reconcile_step(deps, step_id)
    else:
        result = ToolResult.unknown(UNCERTAIN_MESSAGE, error_code="interrupted")
    await finish_step(deps, step_id, output=result.model_dump(mode="json"), result=result, duration_ms=None)
    logger.warning("복구: 도중에 끊긴 도구 호출을 닫음 task=%s step=%s status=%s", deps.task_id, step_id, result.status)
    return result


async def start_step(deps: AgentDeps, call: ToolCallPart, tool_def: ToolDefinition, args: dict[str, Any]) -> int:
    """steps 행을 running으로 만들고 바로 커밋한 뒤 id를 돌려준다.

    실행 전에 커밋하는 이유: 화면이 진행 중을 보고, 실행 도중 서버가 죽으면 running 행이 남아 복구 때
    "끊긴 호출"로 알아볼 수 있다(close_uncertain_step). 위험 등급은 toolset metadata에서 복사한다.
    order는 이 task의 최댓값 + 1이다. 같은 task에서 도구가 동시에 시작되면 같은 값이 나올 수 있으니,
    병렬 도구 실행을 켤 때 다시 본다(agent.py의 DBOSDurability는 parallel_execution_mode="sequential").
    """
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
    """도구 결과를 ToolResult로 맞춘다. ToolResult가 아닌 값은 성공으로 보고 그대로 감싼다.

    도구 규약상 모든 도구가 ToolResult를 돌려주므로 보통은 그대로 지나간다. EXTERNAL 도구가 ToolResult가 아닌 값을
    돌려주면 영수증이 없으므로 다음 receipt_checked에서 unknown이 된다.
    """
    if isinstance(result, ToolResult):
        return result
    return ToolResult.success(to_jsonable_python(result))


async def finish_step(
    deps: AgentDeps, step_id: int, *, output: dict[str, Any] | None, result: ToolResult | None, duration_ms: int | None
) -> None:
    """step을 결과로 닫고 커밋한다. result가 None이면 도구가 예외로 끝났다(실패). unknown 결과는 step도 unknown이다.

    output은 저장용으로 따로 받는다(메일 결과는 내용 없이 저장, results.output_for_storage). ok는 succeeded일 때만
    True다(예전 화면·API 호환). 같은 행을 여러 번 닫아도 마지막 값이 남을 뿐 예외는 없다.
    """
    async with deps.db() as session:
        step = await session.get_one(Step, step_id)
        step.output = output
        step.ok = result is not None and result.ok
        step.status = StepStatus.FAILED if result is None else STEP_STATUS_OF[result.status]
        step.duration_ms = duration_ms
        await session.commit()


def receipt_checked(result: ToolResult, risk: RiskLevel) -> ToolResult:
    """EXTERNAL 도구의 성공은 영수증이 있어야 완료다(절대 원칙 10). 없으면 결과 불명으로 바꾼다.

    (AGENTS.md 절대 원칙 10 = BUILD_SPEC 1.4의 8번.) 도구가 finish_execution을 거치지 않고 succeeded를 만들어
    "완료"로 보이는 사고를 막는다. 바꾼 결과는 모델에게도 간다. 같은 검사가 실행기 쪽(executor)에도 있다.
    """
    if risk == RiskLevel.EXTERNAL and result.status == ToolStatus.SUCCEEDED and not result.receipt_id:
        return ToolResult.unknown(NO_RECEIPT_MESSAGE, error_code="no_receipt")
    return result


def elapsed_ms(started: float) -> int:
    """steps.duration_ms용 경과 시간. time.perf_counter 기준이라 벽시계 변경에 흔들리지 않는다."""
    return int((time.perf_counter() - started) * 1000)


@dataclass
class StepTracking(AbstractCapability[AgentDeps]):
    """도구 실행을 감싸 steps에 기록하고, 예상 못 한 예외를 ok=false 결과로 바꾸는 capability.

    1단계 보강: 모델 호출·새 도구 실행 직전에 취소 요청을 확인한다.
    상태가 없는 dataclass라 Agent 하나에 한 번 만들어 모든 run이 같이 쓴다. 필요한 값은 ctx.deps에서만 읽는다.
    """

    async def before_model_request(
        self, ctx: RunContext[AgentDeps], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """취소 요청이 있으면 TaskCancelledError로 run을 멈춘다(runner가 cancelled로 끝낸다). 요청은 바꾸지 않는다."""
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
        """도구 호출 하나를 기록하며 실행한다. 복구 때는 파일 머리의 표대로 재생·재실행·닫기 중 하나를 고른다.

        결과는 모델에게 갈 값(보통 ToolResult)이다. 취소·승인 거부 같은 흐름 제어 예외는 그대로 올라간다.
        """
        recorded = await find_step(ctx.deps, call.tool_call_id)
        if recorded is None:
            # 1단계 보강: 취소 요청 뒤에는 새 도구를 시작하지 않는다(실행 직전 확인, BUILD_SPEC 4.6).
            await raise_if_cancel_requested(ctx.deps)
            step_id = await start_step(ctx.deps, call, tool_def, args)
        else:
            replayed = None if rereads_mail(call.tool_name, recorded) else recorded_result(recorded)
            if replayed is not None:
                return replayed  # 복구 재생: 이미 끝난 호출은 다시 실행하지 않는다
            # running인 INTERNAL·EXTERNAL = 지난 실행이 도중에 끊겼다. 다시 실행하면 중복 쓰기가 될 수 있다.
            if recorded.status == StepStatus.RUNNING and recorded.risk_level != RiskLevel.READ:
                return await close_uncertain_step(ctx.deps, recorded.id, recorded.risk_level)
            step_id = recorded.id  # running READ(다시 조회) 또는 waiting_approval(승인 후 첫 실행), 메일 다시 읽기
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
        """데이터 정책 검사 → 실행 → 영수증 검사 → 저장 순서로 step 하나를 끝낸다. 모든 경로에서 step을 닫는다.

        불변 조건: 정책 거부는 도구를 부르지 않는다. 저장분(stored)과 모델에게 가는 결과(result)는 메일일 때만 다르다.
        """
        started = time.perf_counter()
        # 4단계 보강: 공개 검색 도구에 개인 데이터(메일·일정 내용)가 실려 나가면 실행하지 않는다(data_policy.py).
        # 7단계 보강: 메일을 읽은 문맥에서는 예약·감시를 만들지 않는다(모델에게 숨긴 도구를 불러도 여기서 막힘).
        refusal = await refusal_before_run(ctx, tool_name, args)
        if refusal is not None:
            stored = refusal.model_dump(mode="json")
            await finish_step(ctx.deps, step_id, output=stored, result=refusal, duration_ms=elapsed_ms(started))
            return refusal
        try:
            result = await handler(args)
        except BaseException:
            # 승인 거부(PermissionError) 같은 예외도 step에는 실패로 남기고 그대로 올려 보낸다.
            # 일반 예외는 on_tool_execute_error가 결과로 바꿔 handler가 돌려주므로 여기 오지 않는다
            # (test_agent_run.py의 test_unexpected_tool_exception_becomes_failed_result: output에 user_message가 있다).
            # output=None으로 닫힌 step은 복구 때 unknown(interrupted)으로 재생된다(recorded_result).
            await finish_step(ctx.deps, step_id, output=None, result=None, duration_ms=elapsed_ms(started))
            raise
        original = describe_result(result)
        described = receipt_checked(original, risk)
        if described is not original:
            result = described  # 영수증 없는 외부 성공: 모델에게도 결과 불명으로 알린다
        # 7단계 보강: 메일 도구 결과는 내용 없이 저장한다(모델에게 돌려주는 result는 그대로 4,000자까지).
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
        """도구 안에서 난 예외를 한 곳에서 처리한다. 내부 오류 내용은 모델과 화면에 보내지 않고 로그에만 남긴다.

        PASS_THROUGH_ERRORS는 다시 던진다: 권한 위반(PermissionError)을 일반 실패로 삼키면 승인 없는 실행 시도가
        묻히고, 흐름 제어 예외를 삼키면 승인 대기·재시도·취소가 동작하지 않는다(BUILD_SPEC 2.3).
        그 밖의 예외는 failed(unexpected, retryable=False)다. 반쯤 실행됐을 수 있어 다시 해도 된다고 알리지 않는다.
        """
        if isinstance(error, PASS_THROUGH_ERRORS):
            raise error
        logger.error("도구 실행 중 예외: tool=%s task=%s", call.tool_name, ctx.deps.task_id, exc_info=error)
        return ToolResult.failure(UNEXPECTED_ERROR_MESSAGE, error_code="unexpected")
