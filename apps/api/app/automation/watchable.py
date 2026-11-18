"""감시에 쓸 수 있는 도구와 그 실행.

감시는 등록할 때(create_watch, 화면에서 다시 켤 때)와 실행할 때 두 번 도구를 검사한다.
- 도구는 read_tools(READ 등급 묶음)에 있는 것만, 메일 도구와 자동화 목록 도구는 뺀다(명세 watch 제한).
  INTERNAL·EXTERNAL 도구는 read_tools에 없으므로 이름이 무엇이든 찾을 수 없다.
- 실행 때 다시 검사하므로 DB의 tool_name이 어떤 이유로 바뀌어 있어도 READ가 아닌 도구는 실행되지 않는다.
- 인자는 도구의 Pydantic 검증기(모델이 부를 때와 같은 규칙)로 검사한다.

read_tools는 app/agent/toolsets.py에 있고 toolsets가 자동화 도구(app/tools/automation.py → store → 이 모듈)를
import하므로, 순환 import를 피해 함수 안에서 불러온다.

진입점: store.add_watch(check_watch_tool, 등록 때), check.execute_watch_tool(run_watch_tool, watch_tool 단계).
근거: 절대 원칙 7(예약·감시 모델에는 READ 도구만), DECISIONS 2026-11-17 "6단계 > 서버 제한"(두 번 검사).

고칠 때 주의:
- read_tools에 새 도구를 넣으면 자동으로 감시에 쓸 수 있게 된다. 감시에 맞지 않는 조회 도구(자기 설정을 보는 것,
  메일처럼 민감한 것)는 rules.NOT_WATCHABLE_TOOLS에 더한다.
- 여기서 도구를 직접 부르므로 메인 Agent run의 StepTracking(데이터 정책 검사, 단계 기록)을 거치지 않는다.
  READ 도구가 ctx.deps 밖의 것(ctx.model, 메시지 기록 등)에 기대게 바뀌면 감시 실행이 깨진다.
- 실행 때 검사를 빼지 않는다: DB의 tool_name만 믿으면 READ가 아닌 도구가 자동으로 돌 수 있다.
- 감시 전용 도구(WATCH_ONLY_TOOLS)는 read_tools 밖이라 채팅·예약 모델에게 보이지 않는다. 새로 더하면
  data_policy.TOOL_POLICIES에도 등록한다(tests/test_data_policy.py가 이 목록까지 본다).

변경 이력:
- 2026-11-18 쿠팡 목표가 감시(coupang_price): 사용자가 등록한 감시만 쓰는 READ 도구. WATCH_ONLY_TOOLS로 이름을 모았다.
"""

import logging
from typing import Any

from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import Tool
from pydantic_ai.usage import RunUsage

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.automation import rules

logger = logging.getLogger(__name__)

# 예상 못 한 예외일 때 last_result.error에 남는 고정 문구. 예외 내용은 화면에 보이지 않고 로그에만 남는다.
TOOL_FAILED_MESSAGE = "감시 도구를 실행하지 못했습니다."
# 감시에서만 쓰는 READ 도구(채팅·예약 도구 묶음에는 없다). 2026-11-18 쿠팡 목표가.
WATCH_ONLY_TOOLS = ("coupang_price",)


class WatchToolError(Exception):
    """감시에 쓸 수 없는 도구이거나 인자가 틀렸다. message는 사용자·모델에게 보일 한국어 문장."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def watchable_tool(tool_name: str) -> Tool[AgentDeps]:
    """감시에 쓸 수 있는 도구를 돌려준다. 아니면 WatchToolError.

    read_tools 묶음에서만 찾으므로 INTERNAL·EXTERNAL 도구는 이름이 같아도 찾을 수 없다(절대 원칙 7).
    메일은 따로 먼저 막아 더 분명한 안내를 준다.
    """
    from app.agent.toolsets import read_tools  # 순환 import 회피(모듈 설명)
    from app.tools.coupang import coupang_price

    # 2026-11-18 목표가 조회는 사용자 등록을 거친 감시 전용 READ다. 일반 채팅·예약 도구에는 노출하지 않는다.
    if tool_name in WATCH_ONLY_TOOLS:
        return Tool(coupang_price)

    if tool_name in rules.MAIL_TOOLS:
        raise WatchToolError("메일 도구로는 감시를 만들 수 없습니다.")
    tool = read_tools.tools.get(tool_name)
    if tool is None or tool_name in rules.NOT_WATCHABLE_TOOLS:
        names = ", ".join(sorted(set(read_tools.tools) - rules.NOT_WATCHABLE_TOOLS))
        raise WatchToolError(f"감시에는 조회(READ) 도구만 쓸 수 있습니다. 쓸 수 있는 도구: {names}")
    return tool


def validated_input(tool: Tool[AgentDeps], tool_input: dict[str, Any]) -> dict[str, Any]:
    """도구 인자 검증기로 검사한 인자. 틀리면 WatchToolError.

    모델이 도구를 부를 때와 같은 Pydantic 규칙이다. 오류 안내에는 틀린 칸 이름만 넣는다(입력 값은 넣지 않는다).
    """
    try:
        return tool.function_schema.validator.validate_python(tool_input)
    except ValidationError as error:
        fields = ", ".join(str(item["loc"][0]) for item in error.errors() if item["loc"]) or "입력"
        raise WatchToolError(f"감시 도구({tool.name})의 입력이 올바르지 않습니다: {fields}") from None


def check_watch_tool(tool_name: str, tool_input: dict[str, Any]) -> None:
    """등록 전 검사: 도구 종류, 인자 모양, 인자 크기(rules.MAX_TOOL_INPUT_CHARS). 통과하면 None."""
    if len(rules.input_key(tool_input)) > rules.MAX_TOOL_INPUT_CHARS:
        raise WatchToolError("감시 도구 입력이 너무 깁니다.")
    validated_input(watchable_tool(tool_name), tool_input)


def tool_context(deps: AgentDeps, tool_name: str) -> RunContext[AgentDeps]:
    """도구 함수에 넘길 RunContext. READ 도구는 ctx.deps만 쓴다(모델은 부르지 않으므로 TestModel은 자리 채우기)."""
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage(), tool_name=tool_name, tool_call_id="watch")


async def run_watch_tool(deps: AgentDeps, tool_name: str, tool_input: dict[str, Any]) -> ToolResult:
    """감시 도구를 실행한다. 검사에 걸리거나 예외가 나면 ok=false. 예외를 밖으로 내지 않는다.

    부를 때마다 도구 종류·인자를 다시 검사한다(등록 뒤 DB 값이 바뀌었거나 도구 인자 규칙이 바뀐 경우).
    도구가 ToolResult가 아닌 것을 돌려주면 실패로 본다. 실패는 unknown 판정이 되고 알림은 없다.
    """
    try:
        tool = watchable_tool(tool_name)
        args = validated_input(tool, tool_input)
        result = await tool.function_schema.call(args, tool_context(deps, tool_name))
    except WatchToolError as error:
        return ToolResult.failure(error.message)
    except Exception:
        logger.warning("감시 도구 실행 실패: tool=%s", tool_name, exc_info=True)
        return ToolResult.failure(TOOL_FAILED_MESSAGE)
    return result if isinstance(result, ToolResult) else ToolResult.failure(TOOL_FAILED_MESSAGE)
