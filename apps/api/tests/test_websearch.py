"""web_search (READ, 외부 콘텐츠, NAVER API HUB 웹문서 검색) 테스트. 정상 응답은 실제 녹화본(2026-11-17)."""

import httpx

from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.agent.results import EXTERNAL_NOTICE
from app.tools import websearch
from tests.conftest import payload, tool_context
from tests.fake_http import FakeHttp, fixture_response, json_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send

WEBKR = "https://naverapihub.apigw.ntruss.com/search/v1/webkr"


async def search(deps: AgentDeps, query: str = "성수동 맛집", size: int = 5):
    return await websearch.web_search(tool_deps_context(deps), query=query, size=size)


def tool_deps_context(deps: AgentDeps):
    return tool_context(deps, "web_search")


async def test_web_search_strips_tags_and_wraps_external(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", WEBKR, fixture_response("naver_webkr_seongsu_food.json"))

    result = await search(tool_deps)

    assert result.ok is True
    assert payload(result)["notice"] == EXTERNAL_NOTICE
    items = payload(result)["content"]["items"]
    assert len(items) == 5
    assert items[0]["title"] == "소문난성수감자탕 별관> 여행지"  # <b> 태그와 &gt; 엔티티 정리
    assert "<b>" not in items[0]["summary"]
    assert items[0]["link"].startswith("https://korean.visitkorea.or.kr/")
    assert result.sources[0] == items[0]["link"]
    [request] = fake_http.sent("GET", WEBKR)
    assert request.headers["X-NCP-APIGW-API-KEY-ID"] == "test-naver-id"
    assert request.headers["X-NCP-APIGW-API-KEY"] == "test-naver-secret"
    assert request.url.params["display"] == "5"


async def test_web_search_timeout(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", WEBKR, httpx.ReadTimeout("느림"))

    result = await search(tool_deps)

    assert result.ok is False
    assert result.error == "네이버 검색 응답이 10초 안에 오지 않았습니다."


async def test_web_search_401(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", WEBKR, fixture_response("naver_error_401.json", status=401))

    result = await search(tool_deps)

    assert result.ok is False
    assert "네이버 검색 인증에 실패했습니다" in (result.error or "")
    assert "test-naver-secret" not in (result.error or "")


async def test_web_search_429(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", WEBKR, fixture_response("naver_error_429.json", status=429))

    result = await search(tool_deps)

    assert result.ok is False
    assert result.error == "네이버 검색 호출 한도를 넘었습니다. 잠시 후 다시 시도해 주세요."


async def test_web_search_empty_is_ok_with_message(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", WEBKR, fixture_response("naver_webkr_empty.json"))

    result = await search(tool_deps, query="아무도검색하지않는말")

    assert result.ok is True
    assert payload(result)["content"]["items"] == []
    assert "검색 결과가 없습니다" in payload(result)["content"]["message"]


async def test_web_search_server_error_retries_once(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route(
        "GET", WEBKR, json_response(500, {"errorCode": "SE99"}), fixture_response("naver_webkr_seongsu_food.json")
    )

    result = await search(tool_deps)

    assert result.ok is True
    assert len(fake_http.sent("GET", WEBKR)) == 2


async def test_web_search_without_keys_does_not_call_api(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    tool_deps.settings.naver_client_id = ""

    result = await search(tool_deps)

    assert result.ok is False
    assert "NAVER_CLIENT_ID" in (result.error or "")
    assert fake_http.requests == []


async def test_web_search_via_agent_records_label(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_http: FakeHttp
) -> None:
    fake_http.route("GET", WEBKR, fixture_response("naver_webkr_seongsu_food.json"))
    model = scripted(call("web_search", query="성수동 맛집"), text("검색 결과입니다."))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "성수동 맛집 웹에서 찾아줘")

    [step] = task["steps"]
    assert (step["status"], step["ok"], step["risk_level"]) == ("done", True, "READ")
    assert step["label"] == "네이버에서 '성수동 맛집' 검색하는 중"
