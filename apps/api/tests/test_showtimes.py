"""영화 비서 C 첫 단계: compare_showtimes(붙여 넣은 시간표 + 이동 시간 → 회차 고르기) 테스트.

요구사항 밖 기능, 2026-11-18 사용자 결정(생활 비서 명세 4절). 공식 회차 API가 없어(KOBIS는 영화 정보·박스오피스뿐)
사용자가 붙여 넣은 시간표로 계산한다. 이동 시간은 사용자가 말한 값 우선, 없는 구간만 TMAP(하루 10건 한도 공유).
실제 모델·TMAP·카카오를 부르지 않는다.
"""

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from app import clock
from app.agent import data_policy
from app.tools import showtimes, tmap
from app.tools.places import KEYWORD_URL
from tests.fake_http import fixture_response, json_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send

PASTE = (
    "금요일 강남에서 6시 반에 만나서 밥 1시간 먹고 파묘 보고 11시 전에 집(잠실역) 가고 싶어. 시간표 붙여 넣을게.\n"
    "CGV 강남 파묘 19:30 (상영 100분)\n"
    "메가박스 코엑스 파묘 19:55~21:35\n"
    "식당에서 극장까지 15분, 극장에서 집까지 40분 걸려."
)
BASE = {
    "meet_place": "강남역",
    "meet_at": "2026-11-20T18:30",
    "dinner_minutes": 60,
    "home": "잠실역",
    "deadline": "2026-11-20T23:00",
    "deadline_kind": "home",
}
A = {"theater": "CGV 강남", "movie": "파묘", "start": "2026-11-20T19:30", "runtime_minutes": 100}
B = {"theater": "메가박스 코엑스", "movie": "파묘", "start": "2026-11-20T19:55", "end": "2026-11-20T21:35"}
GIVEN = {"to_theater_minutes": 15, "home_minutes": 40}


@pytest.fixture
def keys(test_settings, monkeypatch):
    test_settings.kakao_rest_api_key = SecretStr("test-kakao-key")
    test_settings.tmap_app_key = SecretStr("test-tmap-key")
    monkeypatch.setattr(clock, "now_utc", lambda: datetime(2026, 11, 18, 0, 0, tzinfo=UTC))
    monkeypatch.setattr(tmap, "LIMITER", tmap.SlidingWindowRateLimiter(limit=tmap.DAILY_LIMIT, window_seconds=86400))


def compare(*showings: dict, **changes: object):
    return call("compare_showtimes", **{**BASE, "showings": list(showings), **changes})


async def run_chat(client, agent, conversation, *calls, message: str = PASTE) -> dict:
    model = scripted(*calls, text("회차를 비교했습니다."))
    with agent.override(model=model.model()):
        return await send(client, conversation, message)


async def test_pasted_timetable_with_given_travel_picks_b_without_any_request(
    client, agent, conversation, keys, fake_http
):
    task = await run_chat(client, agent, conversation, compare({**A, **GIVEN}, {**B, **GIVEN}))

    [step] = task["steps"]
    assert step["ok"] and step["risk_level"] == "READ"
    content = step["output"]["data"]["content"]
    assert content["결론"] == "메가박스 코엑스 19:55 회차가 가장 편합니다."
    assert content["이유"] == "식사 60분, 21:35 종료, 집 도착 22:15 예상으로 조건을 모두 만족합니다."
    assert content["제외"] == [
        {"회차": "CGV 강남 19:30 회차", "이유": "식사가 35분뿐입니다(원하신 60분보다 25분 부족)."}
    ]
    assert content["좌석"] == "좌석은 극장에서 확인해 주세요."
    assert content["예매"] == "https://www.megabox.co.kr"
    assert content["이동"] == "이동 시간은 말씀하신 값입니다."
    assert [row["종료"] for row in content["계산"]] == ["21:10 예상", "21:35"]
    assert fake_http.requests == []  # 사용자가 준 값만으로 끝났다(TMAP 한도를 쓰지 않음)


async def test_showing_not_in_pasted_text_is_refused(client, agent, conversation, keys, fake_http):
    invented = {**B, "start": "2026-11-20T20:40", **GIVEN}  # 붙여 넣은 시간표에 없는 회차

    task = await run_chat(client, agent, conversation, compare({**A, **GIVEN}, invented))

    [step] = task["steps"]
    assert not step["ok"]
    assert "20:40" in step["output"]["user_message"]
    assert fake_http.requests == []


async def test_missing_travel_is_looked_up_once_per_theater_and_leg(client, agent, conversation, keys, fake_http):
    places = {
        "강남역": [{"place_name": "강남역 2호선", "address_name": "서울", "x": "127.0276", "y": "37.4979"}],
        "메가박스 코엑스": [{"place_name": "메가박스 코엑스", "address_name": "서울", "x": "127.0590", "y": "37.5125"}],
        "잠실역": [{"place_name": "잠실역 2호선", "address_name": "서울", "x": "127.1000", "y": "37.5130"}],
    }
    fake_http.route(
        "GET", KEYWORD_URL, lambda r: json_response(200, {"documents": places.get(r.url.params["query"], [])})
    )
    fake_http.route("POST", tmap.ROUTES_URL, fixture_response("tmap_routes_two.json"))
    late = {**B, "start": "2026-11-20T21:00", "end": "2026-11-20T22:40"}
    message = PASTE + "\n메가박스 코엑스 파묘 21:00~22:40"

    task = await run_chat(client, agent, conversation, compare(B, late), message=message)

    content = task["steps"][0]["output"]["data"]["content"]
    # fixture 가장 빠른 경로 55분: 식당 → 극장 55분, 극장 → 집 55분
    assert len(fake_http.sent("POST", tmap.ROUTES_URL)) == 2  # 극장 하나 × (가는 길, 귀가) 한 번씩
    assert content["이동"].startswith("이동 시간은 TMAP 대중교통 예상")
    assert content["결론"] == "조건을 모두 만족하는 회차가 없습니다."
    assert content["이유"] == "가장 가까운 메가박스 코엑스 21:00 회차: 집 도착 23:35 예상이라 23:00보다 35분 늦습니다."


async def test_daily_limit_reached_asks_for_travel_times(client, agent, conversation, keys, fake_http):
    for _ in range(tmap.DAILY_LIMIT):
        tmap.LIMITER.allow(tmap.LIMIT_KEY)
    places = [{"place_name": "강남역 2호선", "address_name": "서울", "x": "127.0276", "y": "37.4979"}]
    fake_http.route("GET", KEYWORD_URL, json_response(200, {"documents": places}))

    task = await run_chat(client, agent, conversation, compare(B))

    [step] = task["steps"]
    assert not step["ok"]
    assert "이동 시간을 알려 주시면" in step["output"]["user_message"]
    assert fake_http.sent("POST", tmap.ROUTES_URL) == []


def test_policy_registered_as_public_lookup() -> None:
    assert data_policy.TOOL_POLICIES["compare_showtimes"].accepts == data_policy.PUBLIC_ONLY
    assert data_policy.needs_check("compare_showtimes")


@pytest.mark.parametrize(
    ("theater", "url"),
    [
        ("CGV 강남", "https://www.cgv.co.kr"),
        ("롯데시네마 월드타워", "https://www.lottecinema.co.kr"),
        ("메가박스 코엑스", "https://www.megabox.co.kr"),
        ("동네 독립극장", None),
    ],
)
def test_booking_link_only_for_known_official_sites(theater: str, url: str | None) -> None:
    assert showtimes.booking_url(theater) == url
