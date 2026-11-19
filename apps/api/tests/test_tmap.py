"""이동 비서 B 첫 단계: plan_departure(카카오 좌표 → TMAP 대중교통 경로 → 출발 시각 계산) 테스트.

요구사항 밖 기능, 2026-11-18 사용자 결정(생활 비서 명세 3절, TMAP 선택). 실제 TMAP·카카오·모델을 부르지 않는다.
TMAP 응답은 tests/fixtures/tmap_routes_two.json(공식 문서 형식으로 만든 것, 실제 응답 아님 — fixtures/README.md).
"""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app import clock
from app.agent import data_policy
from app.tools import tmap
from app.tools.http import ExternalApiError
from app.tools.places import KEYWORD_URL
from tests.fake_http import fixture_response, json_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send

ORIGIN = {"place_name": "역삼역 2호선", "address_name": "서울 강남구 역삼동", "x": "127.0364", "y": "37.5006"}
DESTINATION = {"place_name": "잠실역 2호선", "address_name": "서울 송파구 잠실동", "x": "127.1000", "y": "37.5130"}
ARGS = {
    "origin": "역삼역",
    "destination": "잠실역",
    "arrive_by": "2026-11-19T19:00",
    "early_minutes": 15,
    "previous_end": "2026-11-19T17:40",
}


@pytest.fixture
def keys(test_settings, monkeypatch):
    test_settings.kakao_rest_api_key = SecretStr("test-kakao-key")
    test_settings.tmap_app_key = SecretStr("test-tmap-key")
    monkeypatch.setattr(clock, "now_utc", lambda: datetime(2026, 11, 18, 0, 0, tzinfo=UTC))
    # 하루 한도 카운터를 테스트마다 새로 둔다(다른 테스트의 조회가 한도에 섞이지 않게).
    monkeypatch.setattr(tmap, "LIMITER", tmap.SlidingWindowRateLimiter(limit=tmap.DAILY_LIMIT, window_seconds=86400))


def content_of(result) -> dict:
    """ToolResult.external의 내용(data.content). data는 JSON 값 타입이라 dict인지 확인해 꺼낸다."""
    assert isinstance(result.data, dict) and isinstance(result.data["content"], dict)
    return result.data["content"]


def kakao_by_query(**documents: list[dict]):
    """카카오 검색어마다 다른 결과를 돌려준다(출발지·도착지를 한 주소로 구분)."""

    def reply(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"documents": documents.get(request.url.params["query"], []), "meta": {}})

    return reply


def route_places(fake_http, origin=(ORIGIN,), destination=(DESTINATION,)) -> None:
    fake_http.route("GET", KEYWORD_URL, kakao_by_query(역삼역=list(origin), 잠실역=list(destination)))


async def test_chat_shows_departure_and_map_link(client, agent, conversation, keys, fake_http):
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, fixture_response("tmap_routes_two.json"))
    model = scripted(call("plan_departure", **ARGS), text("17:50에 출발하면 됩니다."))
    with agent.override(model=model.model()):
        task = await send(client, conversation, "내일 교육 5시 40분에 끝나고 7시 면접, 15분 전 도착 괜찮아?")

    [step] = task["steps"]
    assert step["ok"] and step["risk_level"] == "READ"
    content = step["output"]["data"]["content"]
    # 빠른 경로 3270초 = 55분(올림) → 18:45 - 55분 = 17:50, 교육 종료 17:40 뒤 10분 여유
    assert content["결론"] == "11월 19일(목) 17:50에 출발하면 18:45에 도착합니다(19:00보다 15분 일찍)."
    assert content["출발"] == "2026-11-19T17:50:00+09:00"  # 화면이 지난 출발인지 판단하는 값(15번)
    assert content["이유"] == "앞 일정이 끝나고 10분 여유가 있습니다."
    assert content["다른 경로"] == "6분 늦지만 환승이 1번 적습니다."
    assert content["출발지"] == "역삼역 2호선" and content["도착지"] == "잠실역 2호선"
    assert "2026-11-19 17:45 운행 기준" in content["기준"]
    assert [r["소요"] for r in content["경로"]] == ["55분", "61분"]
    assert content["경로"][0]["경로"] == "도보 → 수도권2호선 → 수도권9호선 → 도보"
    # 좌표는 카카오가 준 문자열 그대로(자릿수를 잘라 다른 곳을 가리키지 않게).
    map_link = "https://map.kakao.com/link/by/traffic/역삼역 2호선,37.5006,127.0364/잠실역 2호선,37.5130,127.1000"
    assert content["지도"] == map_link
    assert step["output"]["sources"] == [map_link]
    assert step["output"]["fetched_at"]


async def test_tmap_request_sends_only_coordinates_and_time(keys, fake_http, tool_deps):
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, fixture_response("tmap_routes_two.json"))

    await tmap.plan_departure_result(tool_deps, **ARGS)

    [request] = fake_http.sent("POST", tmap.ROUTES_URL)
    sent = json.loads(request.content)
    assert set(sent) == {"startX", "startY", "endX", "endY", "count", "lang", "format", "searchDttm"}
    assert (sent["startX"], sent["startY"], sent["endX"], sent["endY"]) == (
        "127.0364",
        "37.5006",
        "127.1000",
        "37.5130",
    )
    # 첫 추정 출발 시각(도착 목표 18:45 - 60분)의 운행 기준으로 조회한다.
    assert sent["searchDttm"] == "202611191745"
    assert request.headers["appKey"] == "test-tmap-key"


async def test_ambiguous_place_asks_before_routing(keys, fake_http, tool_deps):
    branches = (
        {"place_name": "스타벅스 강남역점", "address_name": "서울 강남구", "x": "127.0276", "y": "37.4979"},
        {"place_name": "스타벅스 홍대점", "address_name": "서울 마포구", "x": "126.9236", "y": "37.5563"},
    )
    fake_http.route("GET", KEYWORD_URL, kakao_by_query(스타벅스=list(branches), 잠실역=[DESTINATION]))

    result = await tmap.plan_departure_result(tool_deps, **{**ARGS, "origin": "스타벅스"})

    content = content_of(result)
    assert content["확인 필요"] == "출발지 '스타벅스'가 여러 곳입니다. 어느 곳인지 알려 주세요."
    assert [c["name"] for c in content["후보"]] == ["스타벅스 강남역점", "스타벅스 홍대점"]
    assert fake_http.sent("POST", tmap.ROUTES_URL) == []  # 잘못된 지점으로 경로를 조회하지 않는다


async def test_no_route_is_not_an_api_failure(keys, fake_http, tool_deps):
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, json_response(200, {"result": {"status": 11, "message": "가까움"}}))

    result = await tmap.plan_departure_result(tool_deps, **ARGS)

    assert result.ok
    assert content_of(result)["결론"] == "대중교통 경로를 찾지 못했습니다."
    assert (
        content_of(result)["이유"]
        == "출발지와 도착지가 너무 가까워 대중교통 경로가 없습니다. 걸어서 가는 편이 낫습니다."
    )


async def test_timeout_is_failed_not_unknown_and_not_retried(keys, fake_http, tool_deps):
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, httpx.ReadTimeout("지연"))

    result = await tmap.plan_departure_result(tool_deps, **ARGS)

    assert result.status == "failed"  # 조회용 POST라 '결과 불명'이 아니다
    assert len(fake_http.sent("POST", tmap.ROUTES_URL)) == 1


async def test_auth_error_is_failed(keys, fake_http, tool_deps):
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, json_response(401, {"error": {"code": "INVALID_API_KEY"}}))

    result = await tmap.plan_departure_result(tool_deps, **ARGS)

    assert result.status == "failed"


async def test_missing_key_sends_nothing(keys, fake_http, tool_deps):
    tool_deps.settings.tmap_app_key = SecretStr("")

    result = await tmap.plan_departure_result(tool_deps, **ARGS)

    assert result.status == "failed" and "TMAP_APP_KEY" in (result.user_message or "")
    assert fake_http.requests == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"arrive_by": "2026-11-17T19:00"}, "이미 지난 시각"),
        ({"previous_end": "2026-11-19T19:30"}, "앞 일정이 도착 시각보다 늦게"),
        ({"arrive_by": "내일 7시"}, "YYYY-MM-DDTHH:MM"),
    ],
)
async def test_bad_times_are_refused_before_any_request(keys, fake_http, tool_deps, changes, message):
    result = await tmap.plan_departure_result(tool_deps, **{**ARGS, **changes})

    assert result.status == "failed" and message in (result.user_message or "")
    assert fake_http.requests == []


def test_place_names_go_through_the_public_data_check() -> None:
    # 메일을 읽은 문맥이거나 조회한 일정 제목이 인자에 있으면 StepTracking이 실행 전에 막는다(기존 정책,
    # tests/test_data_policy.py가 그 동작을 본다). 이 도구가 그 검사 대상(공개 데이터만 받는 외부 도구)인지 확인한다.
    assert data_policy.TOOL_POLICIES["plan_departure"].accepts == data_policy.PUBLIC_ONLY
    assert data_policy.needs_check("plan_departure")  # 메일 문맥에서 숨김·실행 전 차단 대상(data_policy.visible_tool)


async def test_free_daily_limit_stops_before_sending(keys, fake_http, tool_deps):
    # TMAP Free 요금제는 대중교통 하루 10건(2026-11-18 사용자가 확인한 요금표). 넘으면 보내지 않고 이유를 말한다.
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, fixture_response("tmap_routes_two.json"))
    for _ in range(tmap.DAILY_LIMIT):
        assert (await tmap.plan_departure_result(tool_deps, **ARGS)).ok

    result = await tmap.plan_departure_result(tool_deps, **ARGS)

    assert result.status == "failed" and result.error_code == "rate_limited"
    assert "하루 10건" in (result.user_message or "")
    assert len(fake_http.sent("POST", tmap.ROUTES_URL)) == tmap.DAILY_LIMIT


async def test_ambiguous_place_does_not_use_the_daily_limit(keys, fake_http, tool_deps):
    branches = (
        {"place_name": "스타벅스 강남역점", "address_name": "서울 강남구", "x": "127.0276", "y": "37.4979"},
        {"place_name": "스타벅스 홍대점", "address_name": "서울 마포구", "x": "126.9236", "y": "37.5563"},
    )
    fake_http.route("GET", KEYWORD_URL, kakao_by_query(스타벅스=list(branches), 잠실역=[DESTINATION]))

    for _ in range(tmap.DAILY_LIMIT + 2):
        await tmap.plan_departure_result(tool_deps, **{**ARGS, "origin": "스타벅스"})

    assert not tmap.LIMITER.blocked(tmap.LIMIT_KEY)  # TMAP을 부르지 않은 질문은 한도를 쓰지 않는다


# ---- 2026-11-19: '모른다'를 '경로가 없다'로 바꾸지 않는다(합성 응답, 실제 공급자 장애 사례가 아님) ----

UNKNOWN_BODIES = [
    {},
    {"error": {"id": "500", "code": "9999", "message": "x"}},
    {"result": {"status": 99, "message": "알 수 없음"}},
    {"metaData": {"plan": {"itineraries": [{"totalTime": -60}, {"totalTime": 0}]}}},
]


@pytest.mark.parametrize("body", UNKNOWN_BODIES)
def test_unreadable_response_is_not_no_route(body):
    with pytest.raises(ExternalApiError) as caught:
        tmap.parse_routes(body)
    assert caught.value.code == "bad_response"
    assert "경로가 없다는 뜻은 아닙니다" in caught.value.message


@pytest.mark.parametrize("seconds", [-60, 0])
def test_non_positive_total_time_is_not_a_route(seconds):
    assert tmap.route_from({"totalTime": seconds}) is None


def test_documented_no_route_status_stays_no_route():
    routes, reason = tmap.parse_routes({"result": {"status": 12}})
    assert routes == [] and reason == "출발지 근처 정류장을 찾지 못했습니다."


async def test_unreadable_response_fails_without_departure_card(keys, fake_http, tool_deps):
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, json_response(200, {}))

    result = await tmap.plan_departure_result(tool_deps, **ARGS)

    assert not result.ok
    assert result.error_code == "bad_response"
    assert "경로가 없다는 뜻은 아닙니다" in (result.user_message or "")


# ---- 2026-11-19: 실제 출발 시각과 조회 기준 시각이 크게 다르면 그 시각으로 한 번 다시 조회한다 ----

LONG_TRIP: dict[str, Any] = {"origin": "역삼역", "destination": "잠실역", "arrive_by": "2026-11-19T19:00"}


def one_route(minutes: int) -> httpx.Response:
    itinerary = {"totalTime": minutes * 60, "transferCount": 1, "totalWalkTime": 300, "legs": []}
    return json_response(200, {"metaData": {"plan": {"itineraries": [itinerary]}}})


def search_times(fake_http) -> list[str]:
    return [json.loads(r.content)["searchDttm"] for r in fake_http.sent("POST", tmap.ROUTES_URL)]


async def test_close_guess_needs_one_query(keys, fake_http, tool_deps):
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, one_route(55))

    result = await tmap.plan_departure_result(tool_deps, **LONG_TRIP)

    assert search_times(fake_http) == ["202611191800"]  # 첫 추정(도착-60분)과 실제 출발 18:05가 5분 차이
    assert content_of(result)["결론"] == "11월 19일(목) 18:05에 출발하면 19:00에 도착합니다."


async def test_far_guess_is_queried_again_at_real_departure(keys, fake_http, tool_deps):
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, one_route(100), one_route(90))

    result = await tmap.plan_departure_result(tool_deps, **LONG_TRIP)

    assert search_times(fake_http) == ["202611191800", "202611191720"]  # 17:20 출발 기준으로 다시
    content = content_of(result)
    assert content["결론"] == "11월 19일(목) 17:30에 출발하면 19:00에 도착합니다."  # 다시 조회한 소요(90분)로 계산
    assert "2026-11-19 17:20 운행 기준" in content["기준"]


async def test_far_guess_without_quota_says_the_gap(keys, fake_http, tool_deps, monkeypatch):
    monkeypatch.setattr(tmap, "LIMITER", tmap.SlidingWindowRateLimiter(limit=1, window_seconds=86400))
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, one_route(100))

    result = await tmap.plan_departure_result(tool_deps, **LONG_TRIP)

    assert len(fake_http.sent("POST", tmap.ROUTES_URL)) == 1
    content = content_of(result)
    assert content["결론"] == "11월 19일(목) 17:20에 출발하면 19:00에 도착합니다."
    assert "18:00 출발 기준으로 조회해 권장 출발(17:20)과 40분 차이" in content["기준"]


async def test_late_case_requeries_at_previous_end(keys, fake_http, tool_deps):
    """앞 일정 때문에 늦게 나가면 실제로 나가는 시각(앞 일정 종료)으로 다시 묻는다."""
    route_places(fake_http)
    fake_http.route("POST", tmap.ROUTES_URL, one_route(55), one_route(55))
    late = {**LONG_TRIP, "early_minutes": 15, "previous_end": "2026-11-19T18:40"}

    await tmap.plan_departure_result(tool_deps, **late)

    assert search_times(fake_http) == ["202611191745", "202611191840"]
