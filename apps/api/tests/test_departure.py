"""이동 비서 B 첫 단계: 도착 목표에서 거꾸로 계산하는 출발 시각(코드 계산, 모델 없음).

요구사항 밖 기능, 2026-11-18 사용자 결정(생활 비서 명세 3절, docs/DECISIONS.md). 시각 차이·부족 시간·경로 비교 문장은
모델이 아니라 이 계산이 정한다. 숫자는 명세 예시 그대로다(19시 면접, 15분 전 도착, 이동 55분 → 17:50 출발, 여유 10분).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.tools.departure import RouteOption, ambiguous_places, plan_departure_times, route_difference

SEOUL = ZoneInfo("Asia/Seoul")


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 19, hour, minute, tzinfo=SEOUL)


def route(minutes: int, transfers: int = 1) -> RouteOption:
    return RouteOption(minutes=minutes, transfers=transfers, walk_minutes=8, fare=1550, summary="도보 → 2호선 → 도보")


def test_spec_example_departs_at_1750_with_ten_minutes_to_spare() -> None:
    plan = plan_departure_times(at(19, 0), early_minutes=15, routes=[route(55)], previous_end=at(17, 40))

    assert plan is not None
    assert plan.target_arrival == at(18, 45)
    assert plan.depart_at == at(17, 50)
    assert plan.slack_minutes == 10
    assert plan.conclusion == "17:50에 출발하면 18:45에 도착합니다(19:00보다 15분 일찍)."
    assert plan.reason == "앞 일정이 끝나고 10분 여유가 있습니다."


def test_shortage_says_how_many_minutes_and_what_to_change() -> None:
    plan = plan_departure_times(at(19, 0), early_minutes=15, routes=[route(63)], previous_end=at(17, 50))

    assert plan is not None
    assert plan.depart_at == at(17, 42)
    assert plan.slack_minutes == -8
    assert plan.reason == (
        "앞 일정이 끝나고 바로 출발해도 8분 부족합니다(18:53 도착 예상). "
        "출발을 8분 앞당기면 목표 도착 시각을 맞출 수 있습니다."
    )


def test_exactly_on_time_has_zero_slack() -> None:
    plan = plan_departure_times(at(19, 0), early_minutes=15, routes=[route(55)], previous_end=at(17, 50))

    assert plan is not None and plan.slack_minutes == 0
    assert plan.reason == "앞 일정이 끝나자마자 출발해야 맞출 수 있습니다(여유 0분)."


def test_without_previous_event_there_is_no_slack_claim() -> None:
    plan = plan_departure_times(at(19, 0), early_minutes=0, routes=[route(55, transfers=2)], previous_end=None)

    assert plan is not None
    assert plan.slack_minutes is None
    assert plan.conclusion == "18:05에 출발하면 19:00에 도착합니다."
    assert plan.reason == "가장 빠른 경로 기준 예상 55분, 환승 2회입니다."


def test_fastest_route_is_used_and_fewer_transfer_route_is_explained() -> None:
    fast, easy = route(55, transfers=2), route(61, transfers=1)
    plan = plan_departure_times(at(19, 0), early_minutes=15, routes=[easy, fast], previous_end=None)

    assert plan is not None
    assert plan.fastest == fast
    assert plan.fewer_transfers == easy
    assert route_difference(fast, easy) == "6분 늦지만 환승이 1번 적습니다."


def test_fewer_transfer_route_is_omitted_when_fastest_already_has_fewest() -> None:
    plan = plan_departure_times(at(19, 0), early_minutes=0, routes=[route(55, 0), route(70, 0)], previous_end=None)

    assert plan is not None and plan.fewer_transfers is None


def test_no_routes_means_no_plan() -> None:
    assert plan_departure_times(at(19, 0), early_minutes=0, routes=[], previous_end=None) is None


def place(name: str, lat: float, lng: float) -> dict:
    return {"place_name": name, "address_name": "서울", "y": str(lat), "x": str(lng)}


def test_same_brand_branches_far_apart_are_ambiguous() -> None:
    documents = [place("스타벅스 강남역점", 37.4979, 127.0276), place("스타벅스 홍대점", 37.5563, 126.9236)]

    candidates = ambiguous_places("스타벅스", documents)

    assert [c["name"] for c in candidates] == ["스타벅스 강남역점", "스타벅스 홍대점"]


def test_exits_of_one_station_are_not_ambiguous() -> None:
    documents = [place("역삼역 2호선", 37.5006, 127.0364), place("역삼역 3번출구", 37.5010, 127.0370)]

    assert ambiguous_places("역삼역", documents) == []


@pytest.mark.parametrize("documents", [[], [place("강남역 2호선", 37.4979, 127.0276)]])
def test_zero_or_one_result_is_not_ambiguous(documents: list[dict]) -> None:
    assert ambiguous_places("강남역", documents) == []
