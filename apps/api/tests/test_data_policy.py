"""4단계 보강(5): 데이터 전송 등급과 정책 (BUILD_SPEC 2.2, 17절).

- 모든 도구에 정책이 있다: 보내는 곳, 받을 수 있는 데이터 등급, 결과 등급, 타임아웃, 재시도, 중복 방지, 보관, 표시.
- secret(토큰·키)은 어떤 도구도 받지 않는다.
- 공개 검색 도구(web_search, search_places, get_weather)는 public만 받는다.
  같은 작업에서 메일을 읽었으면 공개 검색을 막고, 일정 제목이 검색어에 들어 있으면 막는다(자동 허용하지 않음).
- 막으면 실행하지 않고 failed(data_policy) + "공개 검색어를 직접 입력해 달라"는 안내.
"""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import data_policy
from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.toolsets import TOOLSETS_BY_ORIGIN
from app.automation.watchable import WATCH_ONLY_TOOLS
from app.models import Step
from app.models.enums import RiskLevel, StepStatus
from app.tools.websearch import WEBKR_URL
from tests.fake_http import FakeHttp, fixture_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send
from tests.test_gcal import EVENTS, connect_google


def all_tool_names() -> set[str]:
    """모델에게 주는 도구 전부 + 감시만 쓰는 도구(watchable.WATCH_ONLY_TOOLS, 2026-11-18 쿠팡 목표가).

    감시 전용 도구도 외부로 요청을 보내므로 정책 등록 누락을 같이 잡는다.
    """
    given = {name for toolsets in TOOLSETS_BY_ORIGIN.values() for toolset in toolsets for name in toolset.tools}
    return given | set(WATCH_ONLY_TOOLS)


def test_every_tool_has_a_policy() -> None:
    missing = all_tool_names() - set(data_policy.TOOL_POLICIES)
    assert missing == set()


def test_no_tool_accepts_secrets_and_public_tools_accept_only_public() -> None:
    for name, policy in data_policy.TOOL_POLICIES.items():
        assert data_policy.DataClass.SECRET not in policy.accepts, name
        assert policy.timeout_seconds > 0 and policy.retry and policy.dedupe and policy.retention, name
    for name in ("web_search", "search_places", "get_weather", "compare_products", "coupang_price"):
        assert data_policy.TOOL_POLICIES[name].accepts == frozenset({data_policy.DataClass.PUBLIC})
        assert data_policy.TOOL_POLICIES[name].service is not None


async def add_done_step(db_session: AsyncSession, deps: AgentDeps, order: int, tool: str, output: dict) -> None:
    db_session.add(
        Step(
            task_id=deps.task_id,
            order=order,
            tool_name=tool,
            tool_call_id=f"c-{order}",
            risk_level=RiskLevel.READ,
            input={},
            output=output,
            status=StepStatus.DONE,
            label="x",
        )
    )
    await db_session.commit()


async def test_public_search_is_blocked_after_reading_mail(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    assert await data_policy.outbound_refusal(tool_deps, "web_search", {"query": "성수동 맛집"}) is None
    await add_done_step(db_session, tool_deps, 1, "read_mail", {"status": "succeeded", "data": {}})

    refusal = await data_policy.outbound_refusal(tool_deps, "web_search", {"query": "성수동 맛집"})

    assert refusal is not None
    assert refusal.error_code == "data_policy"
    assert "공개해도 되는 검색어" in (refusal.user_message or "")
    shopping_refusal = await data_policy.outbound_refusal(tool_deps, "compare_products", {"product": "KB100"})
    assert shopping_refusal is not None and shopping_refusal.error_code == "data_policy"
    # 앱 안의 도구(할 일)와 내 캘린더 쓰기는 막지 않는다.
    assert await data_policy.outbound_refusal(tool_deps, "add_todo", {"title": "x"}) is None


async def test_event_title_in_search_query_is_blocked(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    events = {"data": {"content": {"events": [{"title": "팀 회의"}, {"title": "휴"}]}}, "status": "succeeded"}
    await add_done_step(db_session, tool_deps, 1, "list_events", events)

    blocked = await data_policy.outbound_refusal(
        tool_deps, "search_places", {"query": "팀 회의 장소", "near": "성수동"}
    )
    allowed = await data_policy.outbound_refusal(tool_deps, "search_places", {"query": "카페", "near": "성수동"})

    assert blocked is not None and blocked.error_code == "data_policy"
    assert allowed is None  # 한 글자 제목("휴")은 비교하지 않는다(우연히 겹치는 것을 막지 않으려고)


async def test_agent_run_records_blocked_search_without_sending(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_http: FakeHttp, tool_deps: AgentDeps
) -> None:
    await connect_google(tool_deps)
    fake_http.route("GET", EVENTS, fixture_response("google_events_list.json"))
    model = scripted(
        call("list_events", date_from="2026-11-20", date_to="2026-11-20"),
        call("web_search", query="팀 회의 준비물"),
        text("일정 내용은 외부 검색에 보내지 않았습니다."),
    )
    with agent.override(model=model.model()):
        task = await send(client, conversation, "금요일 일정 보고 관련해서 검색해줘")

    search = task["steps"][1]
    assert search["tool_name"] == "web_search"
    assert search["status"] == "failed"
    assert search["output"]["error_code"] == "data_policy"
    assert fake_http.sent("GET", WEBKR_URL) == []
    assert task["status"] == "done"


def test_prompt_forbids_unsourced_place_details() -> None:
    assert "영업시간" in SYSTEM_PROMPT and "주차" in SYSTEM_PROMPT and "조용함" in SYSTEM_PROMPT
