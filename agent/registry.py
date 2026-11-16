"""
도구 등록과 위험 등급.

실행 경로는 두 개뿐이다.
  run_tool:     READ, INTERNAL 도구. EXTERNAL을 넣으면 PermissionError.
  run_external: EXTERNAL 도구. 승인된 Approval 레코드가 없으면 PermissionError.
                approvals.execute_approved 한 곳에서만 부른다.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from django.utils import timezone
from jsonschema import Draft202012Validator

from agent.models import Approval, RiskLevel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str  # LLM이 읽는 한국어 설명
    input_schema: dict  # JSON Schema
    risk_level: str
    run: Callable  # run(user, **kwargs) -> dict
    label: Callable  # label(**kwargs) -> str, 타임라인에 보일 한국어 한 줄
    external_content: bool = False  # 외부에서 가져온 글이 결과에 들어가는가(주입 방어 대상)
    preview: Callable | None = None  # EXTERNAL 전용: 실행하지 않고 할 일을 설명


_TOOLS: dict[str, Tool] = {}


def register(tool):
    if tool.name in _TOOLS:
        raise ValueError(f"같은 이름의 도구가 이미 있습니다: {tool.name}")
    if tool.risk_level == RiskLevel.EXTERNAL and tool.preview is None:
        # 사용자는 preview 문장을 보고 승인한다. 설명할 수 없는 외부 행동은 등록 자체를 막는다.
        raise ValueError(f"EXTERNAL 도구는 preview가 있어야 합니다: {tool.name}")
    _TOOLS[tool.name] = tool
    return tool


def get_tool(name):
    """없는 이름이면 None. LLM이 지어낸 도구 이름에도 서버가 죽지 않게 한다."""
    return _TOOLS.get(name)


def tools_for_origin(origin):
    """채팅은 전체, 예약 작업과 조건 감시는 READ 도구만 (절대 원칙 4)."""
    # 이름순으로 정렬한다. 도구 목록 순서가 바뀌지 않아야 프롬프트 캐시가 유지된다.
    tools = sorted(_TOOLS.values(), key=lambda tool: tool.name)
    if origin == "chat":
        return tools
    return [tool for tool in tools if tool.risk_level == RiskLevel.READ]


def to_api_schema(tool):
    return {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}


def validate_input(tool, tool_input):
    """입력이 스키마에 맞으면 None, 아니면 LLM에 돌려줄 오류 문장."""
    errors = sorted(Draft202012Validator(tool.input_schema).iter_errors(tool_input), key=str)
    if not errors:
        return None
    first = errors[0]
    where = ".".join(str(part) for part in first.absolute_path) or "입력 전체"
    return f"입력 형식이 맞지 않습니다({where}): {first.message}"


def run_tool(tool, user, tool_input):
    """READ와 INTERNAL 도구를 실행한다. 도구가 예외를 던져도 ok=false 결과로 바꿔 돌려준다."""
    if tool.risk_level == RiskLevel.EXTERNAL:
        # 프롬프트가 아니라 코드로 막는다. 외부 행동은 승인 레코드를 확인하는 경로로만 실행된다.
        raise PermissionError(f"{tool.name}은 승인이 필요한 도구라 이 경로로 실행할 수 없습니다.")
    return _call(tool, user, tool_input)


def run_external(tool, approval):
    """EXTERNAL 도구를 실행한다. 승인된 Approval이 그 도구의 Step을 가리킬 때만 실행된다.

    approvals.execute_approved가 이미 검사하지만, 실수로 다른 곳에서 불러도 뚫리지 않게 한 번 더 막는다.
    """
    if (
        tool.risk_level != RiskLevel.EXTERNAL
        or approval is None
        or approval.status != Approval.Status.APPROVED
        or approval.step.tool_name != tool.name
    ):
        raise PermissionError("승인되지 않은 외부 행동은 실행할 수 없습니다.")
    step = approval.step
    return _call(tool, step.task.user, step.input_json)


def preview_text(tool, user, tool_input):
    """승인 카드에 보일 문장. preview가 실패해도 무엇을 하려는지는 보여 준다."""
    try:
        return str(tool.preview(user, **tool_input))[:500]
    except Exception:
        logger.exception("preview 생성 실패: %s", tool.name)
        return f"{tool.name} 실행 (설명을 만들지 못했습니다. 입력값을 확인하세요.)"


def _call(tool, user, tool_input):
    try:
        result = tool.run(user, **tool_input)
    except Exception:
        # 내부 오류 문구에는 경로나 개인 정보가 섞일 수 있어 LLM에는 일반 문장만 보낸다.
        logger.exception("도구 실행 중 예외: %s", tool.name)
        return error_result("도구를 실행하는 중 문제가 생겼습니다.")
    return _normalize(result)


def ok_result(data=None, sources=None):
    return {
        "ok": True,
        "data": data,
        "error": None,
        "sources": sources or [],
        "fetched_at": timezone.now().isoformat(),
    }


def error_result(message):
    return {
        "ok": False,
        "data": None,
        "error": message,
        "sources": [],
        "fetched_at": timezone.now().isoformat(),
    }


def _normalize(result):
    """도구가 빠뜨린 키를 채워 결과 형식을 항상 같게 만든다."""
    if not isinstance(result, dict):
        return error_result("도구 결과 형식이 올바르지 않습니다.")
    normalized = error_result(None) | result
    normalized["ok"] = bool(result.get("ok"))
    return normalized
