"""6단계 보강: 감시 조건 판정(BUILD_SPEC 8.2). DB를 쓰지 않는다.

- 숫자 조건(condition_type=number): condition_spec {"field": 경로, "op": 비교, "value": 수}를
  서버가 비교한다(모델 호출 없음).
  경로는 도구 결과 data(외부 콘텐츠면 감싼 content) 안의 키를 점으로 잇는다. 목록 순번은 숫자(예: hourly.0.temperature).
  값이 목록이면 개수로 비교한다(할 일 목록 → 할 일 개수).
- 판정 결과는 met | not_met | unknown. 값이 없거나 숫자가 아니면 unknown(value_missing). 조회 실패는 tool_failed,
  판정 모델 실패는 judge_failed로 모두 unknown이다(BUILD_SPEC: 판정 오류는 false가 아니라 unknown).

진입점: store.check_condition(감시 등록 때 number_spec_problem), check.verdict_for(확인 때 judge_number).
숫자 조건은 도구 create_watch의 number_field·number_op·number_value(app/tools/automation.py)로 들어온다.
근거: DECISIONS 2026-11-17 "6단계 보강"(숫자 조건, unknown은 알리지 않고 last_known_met을 바꾸지 않음).

고칠 때 주의:
- condition_spec 키(field·op·value)는 DB(watches.condition_spec)에 저장된다. 바꾸면 이미 저장된 감시가
  값을 못 읽어 unknown이 되므로 데이터 이전이 필요하다.
- status·reason 값(met·not_met·unknown, tool_failed·judge_failed·value_missing)은 last_result로 저장되어
  app/schemas/automations.py(WatchStatus·WatchUnknownReason)와 apps/web/lib/automations.ts가 읽는다.
- 도구 결과 모양(ToolResult.data, 외부 콘텐츠 감싸기 {notice, content})이 바뀌면 payload_of도 같이 고친다.
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
# 값 경로: 키는 식별자, 목록 순번은 3자리까지, 점으로 최대 6단계. 모델이 넣는 인자라 모양을 좁게 받는다.
FIELD_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.([0-9]{1,3}|[A-Za-z_][A-Za-z0-9_]*)){0,5}$")


@dataclass(frozen=True)
class Verdict:
    """판정 한 건. check.finish_watch가 last_result로 저장하고 알림 여부를 정한다."""

    status: str  # met | not_met | unknown
    reason: str | None  # unknown일 때만: tool_failed | judge_failed | value_missing
    summary: str


def number_spec_problem(spec: dict[str, Any]) -> str | None:
    """숫자 조건 모양 검사. 문제가 있으면 한국어 이유(모델·사용자에게 그대로 보인다), 없으면 None.

    bool은 int의 하위형이라 따로 막는다(True가 1로 비교되지 않게).
    """
    if not isinstance(spec.get("field"), str) or not FIELD_PATTERN.match(spec["field"]):
        return "숫자 조건의 값 경로는 결과의 키를 점으로 이은 것이어야 합니다. 예: precipitation_probability_max"
    if spec.get("op") not in OPERATORS:
        return "숫자 조건의 비교는 >, >=, <, <=, == 중 하나여야 합니다."
    value = spec.get("value")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "숫자 조건의 기준 값은 숫자여야 합니다."
    return None


def payload_of(result: ToolResult) -> Any:
    """경로를 찾을 대상. 외부 콘텐츠 결과(ToolResult.external)는 감싼 안쪽 content를 본다.

    6,000자를 넘어 잘린 외부 결과는 content가 잘린 JSON 문자열이라(results.wrap_external) 값을 못 찾고
    value_missing(unknown)이 된다.
    """
    data = result.data
    if isinstance(data, dict) and "notice" in data and "content" in data:
        return data["content"]
    return data


def step_into(value: Any, key: str) -> Any:
    """경로 한 칸 내려가기. 없거나 범위 밖이면 None(예외를 내지 않는다)."""
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
    """숫자 조건을 서버가 비교한다(모델 호출·비용 없음). 조회 실패·값 없음은 unknown(조건 아님이 아니다).

    spec은 등록 때 number_spec_problem을 통과한 값이라 키가 있다고 본다.
    """
    if not result.ok:
        return Verdict(UNKNOWN, TOOL_FAILED, "")
    actual = read_number(result, spec["field"])
    if actual is None:
        return Verdict(UNKNOWN, VALUE_MISSING, f"조회 결과에서 '{spec['field']}' 값을 찾지 못했습니다.")
    met = OPERATORS[spec["op"]](actual, spec["value"])
    shown = f"{spec['field']} = {actual:g}(기준 {spec['op']} {spec['value']:g})"
    return Verdict(MET if met else NOT_MET, None, shown)
