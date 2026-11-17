"""6단계 보강: 감시 조건 판정(BUILD_SPEC 8.2). DB를 쓰지 않는다.

- 숫자 조건(condition_type=number): condition_spec {"field": 경로, "op": 비교, "value": 수}를
  서버가 비교한다(모델 호출 없음).
  경로는 도구 결과 data(외부 콘텐츠면 감싼 content) 안의 키를 점으로 잇는다. 목록 순번은 숫자(예: hourly.0.temperature).
  값이 목록이면 개수로 비교한다(할 일 목록 → 할 일 개수).
- 판정 결과는 met | not_met | unknown. 값이 없거나 숫자가 아니면 unknown(value_missing). 조회 실패는 tool_failed,
  판정 모델 실패는 judge_failed로 모두 unknown이다(BUILD_SPEC: 판정 오류는 false가 아니라 unknown).
"""

import operator
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agent.results import ToolResult

NUMBER = "number"
TEXT = "text"
MET = "met"
NOT_MET = "not_met"
UNKNOWN = "unknown"
TOOL_FAILED = "tool_failed"
JUDGE_FAILED = "judge_failed"
VALUE_MISSING = "value_missing"

OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
}
FIELD_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.([0-9]{1,3}|[A-Za-z_][A-Za-z0-9_]*)){0,5}$")


@dataclass(frozen=True)
class Verdict:
    status: str  # met | not_met | unknown
    reason: str | None  # unknown일 때만: tool_failed | judge_failed | value_missing
    summary: str


def number_spec_problem(spec: dict[str, Any]) -> str | None:
    """숫자 조건 모양 검사. 문제가 있으면 한국어 이유."""
    if not isinstance(spec.get("field"), str) or not FIELD_PATTERN.match(spec["field"]):
        return "숫자 조건의 값 경로는 결과의 키를 점으로 이은 것이어야 합니다. 예: precipitation_probability_max"
    if spec.get("op") not in OPERATORS:
        return "숫자 조건의 비교는 >, >=, <, <=, == 중 하나여야 합니다."
    value = spec.get("value")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "숫자 조건의 기준 값은 숫자여야 합니다."
    return None


def payload_of(result: ToolResult) -> Any:
    data = result.data
    if isinstance(data, dict) and "notice" in data and "content" in data:
        return data["content"]
    return data


def step_into(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    if isinstance(value, list) and key.isdigit() and int(key) < len(value):
        return value[int(key)]
    return None


def read_number(result: ToolResult, field: str) -> float | None:
    """경로의 값을 숫자로. 목록이면 개수, 숫자가 아니거나 없으면 None."""
    value = payload_of(result)
    for key in field.split("."):
        value = step_into(value, key)
    if isinstance(value, list):
        return len(value)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def judge_number(spec: dict[str, Any], result: ToolResult) -> Verdict:
    if not result.ok:
        return Verdict(UNKNOWN, TOOL_FAILED, "")
    actual = read_number(result, spec["field"])
    if actual is None:
        return Verdict(UNKNOWN, VALUE_MISSING, f"조회 결과에서 '{spec['field']}' 값을 찾지 못했습니다.")
    met = OPERATORS[spec["op"]](actual, spec["value"])
    shown = f"{spec['field']} = {actual:g}(기준 {spec['op']} {spec['value']:g})"
    return Verdict(MET if met else NOT_MET, None, shown)
