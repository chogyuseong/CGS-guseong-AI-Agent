"""위험 등급별 도구 묶음(toolset) 3개와 origin별 조합, 타임라인 문구, 승인 카드 미리보기(preview).

- 도구의 위험 등급은 toolset의 metadata에 적는다. Pydantic AI가 이 값을 각 도구 정의(tool_def.metadata)로
  넘겨 주므로, tracking.py가 도구를 실행할 때 등급을 읽어 steps.risk_level에 복사한다.
- sequential=True: 모델이 도구를 한 번에 여러 개 불러도 하나씩 차례로 실행한다. step 순서(order)가 정해지고
  같은 task의 DB 쓰기가 겹치지 않는다.
- external_tools(EXTERNAL)는 toolset 전체가 requires_approval=True다(첫 번째 방어선). 모델이 이 도구를 부르면
  Pydantic AI가 도구를 실행하지 않고 run을 DeferredToolRequests로 끝낸다. 두 번째 방어선은 각 도구 함수 첫 줄의
  begin_execution(ctx, 인자)다(executor.py, 2단계 보강).
  4단계부터 일정 쓰기 도구 셋(create/update/delete_event)이 들어 있다.
  테스트는 build_external_toolset([fake_external])로 같은 설정의 묶음을 만들어 끼운다.
- 실행할 때 origin에 맞는 toolset만 agent.run(toolsets=...)으로 넘긴다. 넘기지 않은 도구는 모델에게 보이지도 않고
  실행되지도 않는다. (DBOS 문서: 실행 시점 FunctionToolset 전달은 내구성 실행에서도 지원된다.)

쓰는 곳: runner(run_toolsets로 agent.run, toolsets_for로 계획 초안, label_for·preview_for로 승인 카드),
tracking.StepTracking(risk_level_of·label_for로 step 기록), plans.draft_plan(toolsets_for로 도구 검증기),
app/automation/watchable.py(read_tools에서 감시 도구 찾기), 테스트(TOOLSETS_BY_ORIGIN 등을 바꿔 끼움).
run_toolsets의 거르기 함수는 data_policy.visible_tool이다.
근거: BUILD_SPEC 18절 원칙 3·7(EXTERNAL 이중 방어, 자동 작업은 READ만), DECISIONS 2026-11-16(sequential),
2026-11-17(mail_tools는 chat 전용).

고칠 때 주의(새 도구를 등록할 때, AGENTS.md "도구 규약" 순서대로):
- data_policy.TOOL_POLICIES에도 넣는다. 빠지면 tests/test_data_policy.py가 실패한다(TOOLSETS_BY_ORIGIN 전체를 본다).
- 외부 글을 돌려주는 도구면 results.EXTERNAL_CONTENT_TOOLS, EXTERNAL이면 도구 파일의 PREVIEWS·SPECS를 여기 합친다.
- SCHEDULE·WATCH 묶음에 READ가 아닌 toolset을 넣지 않는다(절대 원칙 7). read_tools에 도구를 넣으면 예약 task와
  감시에서도 바로 쓸 수 있게 된다. 감시에 맞지 않는 조회 도구는 app/automation/rules.py의 NOT_WATCHABLE_TOOLS에 넣는다.
- 묶음 구성이 바뀌면 모델에게 보이는 도구 목록이 바뀐다. tests/test_agent_run.py(chat 도구 목록)와 eval을 다시 본다.
- 도구 이름(함수 이름)을 바꾸면 이미 저장된 steps.tool_name·action_plans.tool_name, LABELS 키,
  감시의 watches.tool_name과 어긋난다.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic_ai.tools import ToolDefinition, ToolFuncEither
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

from app.agent.data_policy import visible_tool
from app.agent.deps import AgentDeps
from app.agent.plans import EXTERNAL_TOOL_SPECS
from app.agent.results import EXTERNAL_CONTENT_TOOLS as EXTERNAL_CONTENT_TOOLS
from app.models.enums import RiskLevel, TaskOrigin
from app.tools import automation, coupang, drafts, gcal, gmail, memories, notes, places, weather, websearch

logger = logging.getLogger(__name__)

# toolset metadata 안의 등급 키. risk_level_of가 같은 키로 읽는다.
RISK_LEVEL_KEY = "risk_level"

# EXTERNAL 도구가 실행 전에 "무엇을 할지"를 사람이 읽을 문장으로 만드는 함수. 승인 카드와 감시 에이전트가 본다.
type PreviewFunc = Callable[[AgentDeps, dict[str, Any]], Awaitable[str]]


def risk_metadata(level: RiskLevel) -> dict[str, Any]:
    """toolset metadata로 넣을 등급 표시. 값은 문자열(enum value)이라 도구 정의와 함께 그대로 넘어간다."""
    return {RISK_LEVEL_KEY: level.value}


def build_external_toolset(
    tools: Sequence[ToolFuncEither[AgentDeps, ...]], *, requires_approval: bool = True
) -> FunctionToolset[AgentDeps]:
    """EXTERNAL toolset을 만든다. requires_approval=False는 이중 방어를 검사하는 테스트에서만 쓴다.

    운영 코드에서 requires_approval=False로 부르면 첫 번째 방어선(프레임워크 승인)이 사라진다(절대 원칙 15:
    승인을 자동 통과시키는 코드는 테스트 밖에 두지 않는다). 테스트도 이 함수로 만들어 운영과 같은 설정을 쓴다.
    """
    return FunctionToolset[AgentDeps](
        list(tools),
        id="external_tools",
        metadata=risk_metadata(RiskLevel.EXTERNAL),
        requires_approval=requires_approval,
        sequential=True,
    )


read_tools = FunctionToolset[AgentDeps](
    [
        notes.list_todos,
        notes.list_notes,
        memories.list_memories,
        weather.get_weather,
        places.search_places,
        websearch.web_search,
        gcal.list_events,
        automation.list_schedules,  # 6단계
        automation.list_watches,  # 6단계
    ],
    id="read_tools",
    metadata=risk_metadata(RiskLevel.READ),
    sequential=True,
)
# 7단계: 메일 읽기(READ, 외부 콘텐츠). read_tools와 따로 둔 이유: 예약·감시 task는 read_tools를 받는데, 메일은
# 사용자가 보고 있는 채팅에서만 읽게 한다(감시는 명세가 금지, 예약은 더 안전한 쪽으로 작업자가 정함.
# docs/DECISIONS.md 7단계).
# 등급 metadata는 READ라 steps.risk_level도 READ다.
mail_tools = FunctionToolset[AgentDeps](
    [gmail.list_mail, gmail.read_mail],
    id="mail_tools",
    metadata=risk_metadata(RiskLevel.READ),
    sequential=True,
)
internal_tools = FunctionToolset[AgentDeps](
    # remember·forget(5단계): 3단계 복구 규칙에 따라 INTERNAL은 at-most-once다(도중에 끊긴 호출은 다시 실행하지 않음).
    # 자동화 생성·삭제(6단계)도 INTERNAL이다(명세). 자동 작업(예약·감시)에는 이 묶음이 없어 스스로 늘릴 수 없다.
    [
        notes.save_note,
        notes.add_todo,
        notes.complete_todo,
        memories.remember,
        memories.forget,
        automation.create_schedule,
        automation.delete_schedule,
        automation.create_watch,
        automation.delete_watch,
        drafts.draft_message,  # 4단계 보강: 초안만(발송 없음). 자동 작업에는 이 묶음이 없다
    ],
    id="internal_tools",
    metadata=risk_metadata(RiskLevel.INTERNAL),
    sequential=True,
)
# 일정 쓰기 도구. 함수 첫 줄은 모두 begin_execution(ctx, 인자)다(두 번째 방어선, app/tools/gcal.py).
external_tools = build_external_toolset([gcal.create_event, gcal.update_event, gcal.delete_event])
# 2026-11-18 생활 비서 A단계. 가격 감시는 후속 단계이고 검색 할당량은 모든 대화가 공유한다.
shopping_tools = FunctionToolset[AgentDeps](
    [coupang.compare_products], id="shopping_tools", metadata=risk_metadata(RiskLevel.READ), sequential=True
)

# 스스로 도는 작업(예약, 감시)에는 READ 도구만 넘긴다(절대 원칙 4). 메일(mail_tools)은 READ지만 chat에만 넘긴다.
TOOLSETS_BY_ORIGIN: dict[TaskOrigin, list[FunctionToolset[AgentDeps]]] = {
    TaskOrigin.CHAT: [read_tools, internal_tools, mail_tools, external_tools, shopping_tools],
    TaskOrigin.SCHEDULE: [read_tools],
    TaskOrigin.WATCH: [read_tools],
}

TOOL_LABELS: dict[str, Callable[[dict[str, Any]], str]] = {
    **notes.LABELS,
    **memories.LABELS,
    "get_weather": weather.get_weather_label,
    "search_places": places.search_places_label,
    "web_search": websearch.web_search_label,
    "compare_products": lambda _: "쿠팡 상품 조건 비교하는 중",
    **gcal.LABELS,
    **automation.LABELS,
    **gmail.LABELS,
    **drafts.LABELS,
}
# 결과가 외부 콘텐츠(남이 쓴 글)인 도구 목록은 app/agent/results.py의 EXTERNAL_CONTENT_TOOLS다.
# (5단계 remember 검사가 도구 모듈에서 쓰므로 순환 import를 피해 그쪽으로 옮겼다. 여기서는 다시 내보내기만 한다.)
# EXTERNAL 도구 이름 → preview 함수(승인 카드 문장). 일정 도구 셋은 app/tools/gcal.py의 PREVIEWS.
TOOL_PREVIEWS: dict[str, PreviewFunc] = {**gcal.PREVIEWS}
# EXTERNAL 도구의 실행 계획 규격(버전·연결·대상 인자). 여기에 없는 EXTERNAL 도구는 계획을 만들지 않는다(plans.py).
EXTERNAL_TOOL_SPECS.update(gcal.SPECS)


def toolsets_for(origin: TaskOrigin) -> list[FunctionToolset[AgentDeps]]:
    """origin에 맞는 도구 묶음(정의 그대로). 실행 계획 초안(plans.draft_plan)이 도구 검증기를 찾을 때도 쓴다."""
    return TOOLSETS_BY_ORIGIN[origin]


def run_toolsets(origin: TaskOrigin) -> list[AbstractToolset[AgentDeps]]:
    """agent.run에 넘길 묶음. 모델 요청마다 data_policy.visible_tool로 보일 도구를 거른다(7단계 보강).

    메일 기능 플래그가 꺼져 있으면 메일 도구를, 문맥에 읽어 온 메일이 있으면 공개 검색·장소·날씨와
    자동화 생성(create_schedule·create_watch)을 뺀다.
    FilteredToolset은 FunctionToolset을 감싸기만 해서 DBOS의 실행 시점 도구 규칙(함수 도구만 허용)을 그대로 지킨다.
    """
    return [toolset.filtered(visible_tool) for toolset in toolsets_for(origin)]


def risk_level_of(tool_def: ToolDefinition) -> RiskLevel:
    """도구 정의의 metadata에서 등급을 읽는다. 등급이 없는 도구는 가장 엄격한 EXTERNAL로 본다.

    엄격한 쪽으로 두는 이유: 등급 표시를 빠뜨린 묶음의 도구가 READ로 기록돼 복구 때 다시 실행되는 사고를 막는다
    (StepTracking은 등급으로 복구 때 재실행 여부를 정한다). 결과는 steps.risk_level에 복사된다.
    """
    value = (tool_def.metadata or {}).get(RISK_LEVEL_KEY)
    known_values = {level.value for level in RiskLevel}
    return RiskLevel(value) if value in known_values else RiskLevel.EXTERNAL


def label_for(tool_name: str, args: dict[str, Any]) -> str:
    """타임라인에 보일 문구. 문구 함수가 없거나 실패해도 기본 문구를 돌려준다.

    StepTracking(steps.label)과 runner(승인 대기 step)가 부른다. 200자 자르기는 steps.label 컬럼(String(200)) 길이다.
    """
    labeler = TOOL_LABELS.get(tool_name)
    try:
        label = labeler(args) if labeler else f"{tool_name} 실행하는 중"
    except Exception:  # 문구 때문에 도구 실행이 멈추면 안 된다
        label = f"{tool_name} 실행하는 중"
    return label[:200]


def default_preview(tool_name: str, args: dict[str, Any]) -> str:
    """preview 함수가 없거나 실패한 EXTERNAL 도구의 승인 카드 문장. 인자를 숨기지 않고 그대로 보여 준다."""
    return f"{tool_name} 도구를 다음 값으로 실행합니다: {json.dumps(args, ensure_ascii=False)}"


async def preview_for(deps: AgentDeps, tool_name: str, args: dict[str, Any]) -> str:
    """승인 카드에 보일 문장. preview 함수가 없거나 실패하면 도구 이름과 입력값을 그대로 보여 준다.

    runner가 계획의 정규화 인자(canonical_args)로 부르고, 결과는 감시 에이전트 입력과 approvals.summary·
    action_plans.preview에 쓰인다. preview 함수는 실행하지 않고 조회만 할 수 있다(예: 일정 제목 읽기).
    """
    previewer = TOOL_PREVIEWS.get(tool_name)
    if previewer is None:
        return default_preview(tool_name, args)
    try:
        return await previewer(deps, args)
    except Exception:  # 미리보기 실패가 승인 요청 자체를 막으면 안 된다
        logger.warning("preview 생성 실패: tool=%s", tool_name, exc_info=True)
        return default_preview(tool_name, args)
