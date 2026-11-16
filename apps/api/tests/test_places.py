"""search_places (READ, 카카오 로컬 키워드 검색) 테스트. 응답은 tests/fixtures의 실제 녹화본(2026-11-17)."""

import httpx
import pytest

from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.agent.results import EXTERNAL_NOTICE, MAX_EXTERNAL_CHARS
from app.tools import places
from tests.conftest import payload, tool_context
from tests.fake_http import FakeHttp, fixture_json, fixture_response, json_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send

KAKAO = "https://dapi.kakao.com/v2/local/search/keyword.json"


def by_query(request: httpx.Request) -> httpx.Response:
    """동네 이름(좌표 변환)과 키워드 검색을 같은 주소로 보내므로 query로 응답을 고른다."""
    if request.url.params["query"] == "성수동":
        return fixture_response("kakao_locate_seongsu.json")
    return fixture_response("kakao_keyword_seongsu_food.json")


async def search(deps: AgentDeps, **args: object):
    return await places.search_places(tool_context(deps, "search_places"), **args)  # type: ignore[arg-type]


async def test_search_near_place_uses_center_and_radius(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", KAKAO, by_query)

    result = await search(tool_deps, query="맛집", near="성수동", radius_m=2000, size=5)

    assert result.ok is True
    assert payload(result)["notice"] == EXTERNAL_NOTICE
    found = payload(result)["content"]["places"]
    assert len(found) == 5
    assert found[0] == {
        "name": "성수베이킹스튜디오",
        "address": "서울 성동구 서울숲2길 46",
        "category": "음식점 > 간식 > 제과,베이커리",
        "phone": "02-468-0789",
        "place_url": "http://place.map.kakao.com/799001769",
        "latitude": 37.54618518238096,
        "longitude": 127.04375872148695,
        "distance_m": 70,
    }
    assert found[2]["phone"] is None  # 빈 문자열 전화번호는 None
    assert result.sources[0] == "http://place.map.kakao.com/799001769"
    locate_request, search_request = fake_http.sent("GET", KAKAO)
    assert locate_request.url.params["size"] == "1"
    assert search_request.url.params["x"] == "127.043028992489"  # 카카오는 x가 경도
    assert search_request.url.params["y"] == "37.5464440372535"
    assert search_request.url.params["radius"] == "2000"


async def test_search_without_near_sends_no_coordinates(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", KAKAO, fixture_response("kakao_keyword_seongsu_food.json"))

    result = await search(tool_deps, query="성수 맛집")

    assert result.ok is True
    [request] = fake_http.sent("GET", KAKAO)
    assert "x" not in request.url.params
    assert payload(result)["content"]["radius_m"] is None


async def test_search_timeout(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", KAKAO, httpx.ConnectTimeout("느림"))

    result = await search(tool_deps, query="맛집")

    assert result.ok is False
    assert result.error == "카카오 응답이 10초 안에 오지 않았습니다."


async def test_search_401(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", KAKAO, fixture_response("kakao_error_401.json", status=401))

    result = await search(tool_deps, query="맛집")

    assert result.ok is False
    assert "카카오 인증에 실패했습니다" in (result.error or "")


@pytest.mark.parametrize(("name", "status"), [("kakao_error_quota.json", 400), ("kakao_error_quota.json", 429)])
async def test_search_rate_limited(tool_deps: AgentDeps, fake_http: FakeHttp, name: str, status: int) -> None:
    fake_http.route("GET", KAKAO, fixture_response(name, status=status))

    result = await search(tool_deps, query="맛집")

    assert result.ok is False
    assert result.error == "카카오 호출 한도를 넘었습니다. 잠시 후 다시 시도해 주세요."


async def test_search_empty_result_is_ok_with_message(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", KAKAO, fixture_response("kakao_keyword_empty.json"))

    result = await search(tool_deps, query="없는가게이름")

    assert result.ok is True
    assert payload(result)["content"]["places"] == []
    assert "검색 결과가 없습니다" in payload(result)["content"]["message"]


async def test_search_other_400_is_rejected_not_rate_limit(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", KAKAO, json_response(400, {"msg": "invalid radius", "code": -2}))

    result = await search(tool_deps, query="맛집")

    assert result.ok is False
    assert result.error == "카카오 요청이 거부되었습니다(상태 400)."


async def test_long_external_content_is_cut_to_6000_chars(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    body = fixture_json("kakao_keyword_seongsu_food.json")
    body["documents"] = body["documents"] * 3 * 15  # 같은 가게를 아주 많이(카카오 size 최대 15개를 넘겨도 15개만 쓴다)
    for document in body["documents"]:
        document["place_name"] = "가" * 500
        document["category_name"] = "나" * 500
    fake_http.route("GET", KAKAO, json_response(200, body))

    result = await search(tool_deps, query="맛집", size=15)

    assert result.ok is True
    assert payload(result)["truncated"] is True
    assert len(payload(result)["content"]) == MAX_EXTERNAL_CHARS
    assert payload(result)["notice"] == EXTERNAL_NOTICE


async def test_search_via_agent_records_label(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_http: FakeHttp
) -> None:
    fake_http.route("GET", KAKAO, by_query)
    model = scripted(call("search_places", query="맛집", near="성수동"), text("후보입니다."))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "성수동 맛집 찾아줘")

    [step] = task["steps"]
    assert (step["status"], step["ok"], step["risk_level"]) == ("done", True, "READ")
    assert step["label"] == "카카오맵에서 성수동 맛집 찾는 중"
