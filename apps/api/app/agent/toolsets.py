"""위험 등급별 도구 묶음(toolset) 3개와 origin별 조합.

- 도구의 위험 등급은 toolset의 metadata에 적는다. Pydantic AI가 이 값을 각 도구 정의(tool_def.metadata)로
  넘겨 주므로, tracking.py가 도구를 실행할 때 등급을 읽어 steps.risk_level에 복사한다.
- sequential=True: 모델이 도구를 한 번에 여러 개 불러도 하나씩 차례로 실행한다. step 순서(order)가 정해지고
  같은 task의 DB 쓰기가 겹치지 않는다.
- external_tools는 2단계에서 채운다. 모든 도구에 requires_approval=True가 걸리도록 toolset에 미리 설정해 둔다.
- 실행할 때 origin에 맞는 toolset만 agent.run(toolsets=...)으로 넘긴다. 넘기지 않은 도구는 모델에게 보이지도 않고
  실행되지도 않는다. (DBOS 문서: 실행 시점 FunctionToolset 전달은 내구성 실행에서도 지원된다.)
"""

from typing import Any

from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from app.agent.deps import AgentDeps
from app.models.enums import RiskLevel, TaskOrigin
from app.tools import notes

RISK_LEVEL_KEY = "risk_level"


def risk_metadata(level: RiskLevel) -> dict[str, Any]:
    return {RISK_LEVEL_KEY: level.value}


read_tools = FunctionToolset[AgentDeps](
    [notes.list_todos, notes.list_notes],
    id="read_tools",
    metadata=risk_metadata(RiskLevel.READ),
    sequential=True,
)
internal_tools = FunctionToolset[AgentDeps](
    [notes.save_note, notes.add_todo, notes.complete_todo],
    id="internal_tools",
    metadata=risk_metadata(RiskLevel.INTERNAL),
    sequential=True,
)
external_tools = FunctionToolset[AgentDeps](
    [],
    id="external_tools",
    metadata=risk_metadata(RiskLevel.EXTERNAL),
    requires_approval=True,
    sequential=True,
)

# 스스로 도는 작업(예약, 감시)에는 READ 도구만 넘긴다(절대 원칙 4).
TOOLSETS_BY_ORIGIN: dict[TaskOrigin, list[FunctionToolset[AgentDeps]]] = {
    TaskOrigin.CHAT: [read_tools, internal_tools, external_tools],
    TaskOrigin.SCHEDULE: [read_tools],
    TaskOrigin.WATCH: [read_tools],
}

TOOL_LABELS = {**notes.LABELS}


def toolsets_for(origin: TaskOrigin) -> list[FunctionToolset[AgentDeps]]:
    return TOOLSETS_BY_ORIGIN[origin]


def risk_level_of(tool_def: ToolDefinition) -> RiskLevel:
    """도구 정의의 metadata에서 등급을 읽는다. 등급이 없는 도구는 가장 엄격한 EXTERNAL로 본다."""
    value = (tool_def.metadata or {}).get(RISK_LEVEL_KEY)
    known_values = {level.value for level in RiskLevel}
    return RiskLevel(value) if value in known_values else RiskLevel.EXTERNAL


def label_for(tool_name: str, args: dict[str, Any]) -> str:
    """타임라인에 보일 문구. 문구 함수가 없거나 실패해도 기본 문구를 돌려준다."""
    labeler = TOOL_LABELS.get(tool_name)
    try:
        label = labeler(args) if labeler else f"{tool_name} 실행하는 중"
    except Exception:  # 문구 때문에 도구 실행이 멈추면 안 된다
        label = f"{tool_name} 실행하는 중"
    return label[:200]
