"""4단계: search_places. 실제 카카오 응답(성수동 근처 1km 카페 5곳)으로 테스트한다."""

import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SEONGSU = json.loads((FIXTURES / "kakao_keyword_seongsu.json").read_text("utf-8"))
CAFES = json.loads((FIXTURES / "kakao_keyword_cafe_near_seongsu.json").read_text("utf-8"))
EMPTY = json.loads((FIXTURES / "kakao_keyword_empty.json").read_text("utf-8"))


@pytest.fixture
def kakao(monkeypatch, settings):
    """카카오 서버 대신 응답을 차례로 돌려준다. 보낸 요청을 모아 둔다."""
    settings.KAKAO_REST_API_KEY = "test-kakao-key"
    sent = []

    def install(*responses):
        queue = list(responses)

        def handler(request):
            sent.append(request)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            status, body = item
            return httpx.Response(status, json=body)

        monkeypatch.setattr(
            "tools.http.make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
        )
        return sent

    return install


def search(**kwargs):
    from agent.registry import get_tool, run_tool, validate_input

    tool = get_tool("search_places")
    problem = validate_input(tool, kwargs)
    assert problem is None, problem
    return run_tool(tool, None, kwargs)


def test_registered_as_read_tool_with_limits():
    from agent.registry import get_tool, validate_input

    tool = get_tool("search_places")
    assert tool.risk_level == "READ"
    assert validate_input(tool, {"query": "카페", "radius_m": 20001})  # 카카오 최대 반경 20km
    assert validate_input(tool, {"query": "카페", "size": 16})  # 카카오 최대 15개
    assert validate_input(tool, {"query": "카페", "near": "성수동", "radius_m": 500, "size": 3}) is None


def test_search_near_a_place_uses_its_coordinates(kakao):
    sent = kakao((200, SEONGSU), (200, CAFES))

    result = search(query="카페", near="성수동", radius_m=1000, size=5)

    assert result["ok"] is True
    near_request, search_request = sent
    assert near_request.url.params["query"] == "성수동"
    first = SEONGSU["documents"][0]
    assert search_request.url.params["x"] == first["x"]
    assert search_request.url.params["y"] == first["y"]
    assert search_request.url.params["radius"] == "1000"
    assert search_request.url.params["size"] == "5"


def test_result_fields_from_real_response(kakao):
    kakao((200, SEONGSU), (200, CAFES))

    result = search(query="카페", near="성수동", radius_m=1000, size=5)

    places = result["data"]["places"]
    assert len(places) == len(CAFES["documents"])
    first, raw = places[0], CAFES["documents"][0]
    assert first == {
        "name": raw["place_name"],
        "address": raw["road_address_name"] or raw["address_name"],
        "category": raw["category_name"],
        "phone": raw["phone"] or None,
        "place_url": raw["place_url"],
        "latitude": float(raw["y"]),
        "longitude": float(raw["x"]),
        "distance_m": int(raw["distance"]),
    }
    assert result["data"]["near"] == "성수동"


def test_empty_phone_becomes_none(kakao):
    kakao((200, SEONGSU), (200, CAFES))
    places = search(query="카페", near="성수동")["data"]["places"]

    no_phone = [p for p, raw in zip(places, CAFES["documents"], strict=True) if not raw["phone"]]
    assert no_phone and all(p["phone"] is None for p in no_phone)  # 실제 응답에 전화번호 없는 가게가 있다


def test_sources_are_each_place_url(kakao):
    kakao((200, SEONGSU), (200, CAFES))
    result = search(query="카페", near="성수동")

    assert result["sources"] == [d["place_url"] for d in CAFES["documents"]]
    assert result["fetched_at"]


def test_search_without_near_has_no_coordinates(kakao):
    # 저장한 응답은 좌표를 보내고 받은 것이라 distance가 있다. 좌표 없이 검색하면 distance가 비어 온다.
    without_distance = {**CAFES, "documents": [{**d, "distance": ""} for d in CAFES["documents"]]}
    sent = kakao((200, without_distance))

    result = search(query="성수동 카페")

    assert result["ok"] is True
    assert len(sent) == 1
    assert "x" not in sent[0].url.params
    assert all(p["distance_m"] is None for p in result["data"]["places"])  # 좌표 없이 찾으면 거리가 없다


def test_defaults_are_2km_and_5_results(kakao):
    sent = kakao((200, SEONGSU), (200, CAFES))
    search(query="카페", near="성수동")

    assert sent[1].url.params["radius"] == "2000"
    assert sent[1].url.params["size"] == "5"


# ── 실패 4가지: 타임아웃, 401, 429, 빈 결과 ──────────────


def test_timeout_is_ok_false(kakao):
    kakao(httpx.ReadTimeout("느림"), httpx.ReadTimeout("느림"))
    result = search(query="성수동 카페")

    assert result["ok"] is False
    assert "10초" in result["error"]


def test_401_is_ok_false(kakao):
    kakao((401, {"code": -401, "msg": "wrong appKey"}))
    result = search(query="성수동 카페")

    assert result["ok"] is False
    assert "키" in result["error"]


@pytest.mark.parametrize("response", [(429, {}), (400, {"code": -10, "msg": "API limit has been exceeded."})])
def test_rate_limit_is_ok_false(kakao, response):
    kakao(response)
    result = search(query="성수동 카페")

    assert result["ok"] is False
    assert "한도" in result["error"]


def test_empty_result_is_ok_false(kakao):
    kakao((200, EMPTY))
    result = search(query="없는가게이름")

    assert result["ok"] is False
    assert "없는가게이름" in result["error"]


def test_unknown_near_place_is_ok_false(kakao):
    sent = kakao((200, EMPTY))
    result = search(query="카페", near="없는동네")

    assert result["ok"] is False
    assert len(sent) == 1  # 위치를 못 찾으면 장소 검색은 하지 않는다
