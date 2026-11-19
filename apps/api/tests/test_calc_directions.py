"""계산 방향 테스트(2026-11-19, 분석 56번): 조건을 한쪽으로 바꾸면 결과도 그 방향으로만 움직여야 한다.

개별 사례 테스트는 숫자 하나를 고정할 뿐이라, 부호를 잘못 쓰거나 비교 방향을 뒤집은 수정(여유를 늘렸는데 출발이 늦어짐,
예산을 줄였는데 맞는 상품이 늘어남)을 놓칠 수 있다. 여기서는 여러 입력을 격자로 바꿔 가며 방향만 확인한다.
모델·외부 호출·DB 없음(순수 함수). 새 계산(쇼핑·이동·영화)을 고치면 여기 방향 규칙이 아직 맞는지 같이 본다.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.tools.departure import RouteOption, plan_departure_times
from app.tools.movie import Conditions, Legs, Showing, evaluate
from app.tools.shopping import Offer, ShoppingCriteria, compare_offers

SEOUL = ZoneInfo("Asia/Seoul")
APPOINTMENT = datetime(2026, 11, 20, 19, 0, tzinfo=SEOUL)
STATUS_ORDER = {None: 0, "여유": 0, "딱맞음": 1, "여유부족": 2, "지각": 3}


def route(minutes: int) -> RouteOption:
    return RouteOption(minutes=minutes, transfers=1, walk_minutes=5, fare=1500, summary="2호선")


def depart(arrive: datetime, early: int, minutes: int, previous: datetime | None = None):
    plan = plan_departure_times(arrive, early_minutes=early, routes=[route(minutes)], previous_end=previous)
    assert plan is not None
    return plan


# ---- 이동 ----


@pytest.mark.parametrize("minutes", [10, 55, 120])
def test_more_early_buffer_never_departs_later(minutes: int) -> None:
    departures = [depart(APPOINTMENT, early, minutes).depart_at for early in range(0, 61, 5)]
    assert departures == sorted(departures, reverse=True)


@pytest.mark.parametrize("early", [0, 15])
def test_longer_route_never_departs_later(early: int) -> None:
    departures = [depart(APPOINTMENT, early, minutes).depart_at for minutes in range(5, 181, 5)]
    assert departures == sorted(departures, reverse=True)


def test_later_appointment_never_departs_earlier() -> None:
    departures = [depart(APPOINTMENT + timedelta(minutes=step), 15, 55).depart_at for step in range(0, 121, 10)]
    assert departures == sorted(departures)


@pytest.mark.parametrize("early", [0, 15, 30])
def test_later_previous_event_never_improves_status(early: int) -> None:
    ranks = [
        STATUS_ORDER[depart(APPOINTMENT, early, 55, APPOINTMENT - timedelta(minutes=back)).status]
        for back in range(120, -1, -5)  # 앞 일정이 점점 늦게 끝난다
    ]
    assert ranks == sorted(ranks)


# ---- 영화 회차 ----

MEET = datetime(2026, 11, 20, 18, 30, tzinfo=SEOUL)
SHOWINGS = [Showing("CGV 강남", "영화 A", MEET + timedelta(minutes=offset), None, 110) for offset in range(30, 241, 15)]
LEGS = Legs(to_theater=15, home=40)


def feasible(dinner: int, deadline: datetime) -> set[str]:
    cond = Conditions(MEET, dinner, 10, deadline, "home")
    return {e.showing.label for e in (evaluate(s, LEGS, cond) for s in SHOWINGS) if not e.problems}


def test_longer_dinner_never_adds_showings() -> None:
    sets = [feasible(dinner, MEET + timedelta(hours=5)) for dinner in range(0, 181, 15)]
    assert all(later <= earlier for earlier, later in zip(sets, sets[1:], strict=False))


def test_later_deadline_never_removes_showings() -> None:
    sets = [feasible(60, MEET + timedelta(minutes=minutes)) for minutes in range(120, 421, 30)]
    assert all(earlier <= later for earlier, later in zip(sets, sets[1:], strict=False))


# ---- 쇼핑 ----

OFFERS = [
    Offer(
        title=f"무선 키보드 한글 {i}",
        store="판매처",
        url=f"https://example.com/{i}",
        price=price,
        shipping=0,
        condition="신품",
    )
    for i, price in enumerate([25_000, 29_000, 31_000, 45_000, 60_000])
]


def matched(max_price: int | None) -> int:
    criteria = ShoppingCriteria(product="무선 키보드", required=["한글"], max_price=max_price)
    return len(compare_offers(criteria, OFFERS)["조건 일치"])


def test_lower_budget_never_adds_matches() -> None:
    counts = [matched(budget) for budget in (100_000, 60_000, 45_000, 31_000, 29_000, 25_000, 20_000)]
    assert counts == sorted(counts, reverse=True)
    assert matched(None) >= counts[0]
