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
           └─ 도구가 예상 못 한 예외를 내면 on_tool_execute_error가 ToolResult(ok=False)로 바꾼다(한 곳에서).
      3. finish_step: output, ok, status(done/failed), duration_ms를 채우고 커밋
      ▼
    도구 결과(ToolResult)가 모델에게 돌아간다

왜 hook인가: Pydantic AI 2.x 공식 문서의 Hooks(도구 실행 wrap_tool_execute)가 "도구 호출을 감싸는" 가장 단순한
방법이다. 도구마다 기록 코드를 넣지 않아도 되고, 새 도구를 추가해도 기록이 빠질 수 없다.

3단계(DBOS) 대비: start_step은 (task_id, tool_call_id)로 이미 있는 행을 먼저 찾는다. 워크플로가 재실행되어 같은
도구 호출이 다시 기록되려 해도 행이 두 개 생기지 않는다.
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
from pydantic_ai.tools import ToolDefinition
from pydantic_core import to_jsonable_python
from sqlalchemy import func, select

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.agent.toolsets import label_for, risk_level_of
from app.models import Step
from app.models.enums import StepStatus

logger = logging.getLogger(__name__)

# 이 예외들은 ok=false로 바꾸지 않고 그대로 올려 보낸다.
# - PermissionError: 2단계 assert_approved의 두 번째 방어선. 삼키면 승인 없는 실행을 숨기게 된다.
# - 나머지: Pydantic AI가 흐름 제어에 쓰는 예외(재시도 요청, 승인 대기, 실행 건너뛰기 등).
PASS_THROUGH_ERRORS = (PermissionError, ModelRetry, ApprovalRequired, CallDeferred, SkipToolExecution, ToolFailed)
UNEXPECTED_ERROR_MESSAGE = "도구를 실행하는 중 예상하지 못한 오류가 발생했습니다."


async def start_step(deps: AgentDeps, call: ToolCallPart, tool_def: ToolDefinition, args: dict[str, Any]) -> int:
    """steps 행을 running으로 만들고 id를 돌려준다. 같은 tool_call_id 행이 있으면 그 행을 쓴다."""
    async with deps.db() as session:
        existing = await session.scalar(
            select(Step.id).where(Step.task_id == deps.task_id, Step.tool_call_id == call.tool_call_id)
        )
        if existing is not None:
            return existing
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


def describe_result(result: object) -> tuple[dict[str, Any], bool]:
    """도구 결과를 (저장할 JSON, ok)로 바꾼다. ToolResult가 아닌 값은 성공으로 보고 그대로 감싼다."""
    if isinstance(result, ToolResult):
        return result.model_dump(mode="json"), result.ok
    return {"ok": True, "data": to_jsonable_python(result)}, True


async def finish_step(
    deps: AgentDeps, step_id: int, *, output: dict[str, Any] | None, ok: bool, duration_ms: int
) -> None:
    async with deps.db() as session:
        step = await session.get_one(Step, step_id)
        step.output = output
        step.ok = ok
        step.status = StepStatus.DONE if ok else StepStatus.FAILED
        step.duration_ms = duration_ms
        await session.commit()


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


@dataclass
class StepTracking(AbstractCapability[AgentDeps]):
    """도구 실행을 감싸 steps에 기록하고, 예상 못 한 예외를 ok=false 결과로 바꾸는 capability."""

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        step_id = await start_step(ctx.deps, call, tool_def, args)
        started = time.perf_counter()
        try:
            result = await handler(args)
        except BaseException:
            # 승인 거부(PermissionError) 같은 예외도 step에는 실패로 남기고 그대로 올려 보낸다.
            await finish_step(ctx.deps, step_id, output=None, ok=False, duration_ms=elapsed_ms(started))
            raise
        output, ok = describe_result(result)
        await finish_step(ctx.deps, step_id, output=output, ok=ok, duration_ms=elapsed_ms(started))
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
        return ToolResult.failure(UNEXPECTED_ERROR_MESSAGE)
