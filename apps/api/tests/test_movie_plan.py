"""영화 비서 C 첫 단계: 만남 → 식사 → 극장 이동 → 입장 → 상영 종료 → 귀가를 시간 순서대로 계산(코드).

요구사항 밖 기능, 2026-11-18 사용자 결정(생활 비서 명세 4절, 이동 시간은 사용자가 말한 값 우선·없는 구간만 TMAP).
첫 성공 장면(명세): 더 일찍 시작하는 A회차는 식사가 35분뿐이고,
더 늦은 B회차는 식사 60분과 23시 전 귀가를 모두 만족한다.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.tools.movie import Conditions, Legs, Showing, evaluate, pick, showing_difference

SEOUL = ZoneInfo("Asia/Seoul")


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 20, hour, minute, tzinfo=SEOUL)


# 금요일 강남 18:30 만남, 식사 60분, 입장 여유 10분, 23시 전 집 도착
COND = Conditions(meet_at=at(18, 30), dinner_minutes=60, entry_buffer=10, deadline=at(23, 0), deadline_kind="home")
LEGS = Legs(to_theater=15, home=40)
A = Showing(theater="CGV 강남", movie="파묘", start=at(19, 30), end=None, runtime_minutes=100)
# B: 18:30 + 식사 60 + 이동 15 + 입장 10 = 19:55 시작, 21:35 종료, 집 도착 22:15
B = Showing(theater="메가박스 코엑스", movie="파묘", start=at(19, 55), end=at(21, 35), runtime_minutes=None)


def test_spec_first_success_recommends_b_and_explains_a_in_users_terms() -> None:
    a, b = evaluate(A, LEGS, COND), evaluate(B, LEGS, COND)

    assert a.dinner_available == 35
    assert a.problems == ["식사가 35분뿐입니다(원하신 60분보다 25분 부족)."]
    assert b.dinner_available == 60 and b.problems == []
    assert b.home_arrival == at(22, 15)

    choice = pick([a, b])
    assert choice.best == b and choice.second is None and choice.excluded == [a]
    assert choice.conclusion == "메가박스 코엑스 19:55 회차가 가장 편합니다."
    assert choice.reason == "식사 60분, 21:35 종료, 집 도착 22:15 예상으로 조건을 모두 만족합니다."


def test_end_from_runtime_is_marked_estimated() -> None:
    a = evaluate(A, LEGS, COND)

    assert a.end == at(21, 10) and a.end_estimated


def test_late_home_arrival_is_explained_with_minutes() -> None:
    late = Showing(theater="CGV 강남", movie="파묘", start=at(21, 0), end=None, runtime_minutes=120)

    result = evaluate(late, LEGS, COND)

    assert result.problems == ["집 도착 23:40 예상이라 23:00보다 40분 늦습니다."]


def test_movie_end_deadline_is_different_from_home_deadline() -> None:
    cond = Conditions(meet_at=at(18, 30), dinner_minutes=60, entry_buffer=10, deadline=at(23, 0), deadline_kind="end")
    late = Showing(theater="CGV 강남", movie="파묘", start=at(21, 0), end=at(22, 50), runtime_minutes=None)

    result = evaluate(late, LEGS, cond)

    assert result.problems == []  # 23:40에 집에 닿지만 '영화 종료' 마감이라 통과
    assert result.home_arrival == at(23, 30)


def test_unknown_end_cannot_be_judged() -> None:
    unknown = Showing(theater="CGV 강남", movie="파묘", start=at(20, 30), end=None, runtime_minutes=None)

    result = evaluate(unknown, LEGS, COND)

    assert result.problems == ["시간표에 종료 시각이나 상영 시간이 없어 끝나는 시각을 알 수 없습니다."]


def test_two_feasible_showings_give_second_and_real_difference() -> None:
    # early: 식사 90분(30분 남음), 집 22:50(마감 10분 전) → 빠듯한 여유 10
    # later: 식사 65분(5분 남음), 집 22:35(마감 25분 전) → 빠듯한 여유 5
    early = Showing(theater="CGV 강남", movie="파묘", start=at(20, 30), end=at(22, 10), runtime_minutes=None)
    later = Showing(theater="메가박스 코엑스", movie="파묘", start=at(20, 15), end=at(21, 55), runtime_minutes=None)

    first = evaluate(early, Legs(to_theater=20, home=40), COND)
    second = evaluate(later, Legs(to_theater=30, home=40), COND)
    choice = pick([first, second])

    assert choice.best == first and choice.second == second
    assert choice.best is not None and choice.second is not None
    assert showing_difference(choice.best, choice.second) == (
        "CGV 강남 20:30 회차는 식사가 25분 더 남고, 메가박스 코엑스 20:15 회차는 귀가가 15분 빠릅니다."
    )


def test_nothing_feasible_says_so_and_shows_closest_reason() -> None:
    choice = pick([evaluate(A, LEGS, COND)])

    assert choice.best is None
    assert choice.conclusion == "조건을 모두 만족하는 회차가 없습니다."
    assert choice.reason == "가장 가까운 CGV 강남 19:30 회차: 식사가 35분뿐입니다(원하신 60분보다 25분 부족)."


def test_single_feasible_showing_has_no_invented_second() -> None:
    choice = pick([evaluate(B, LEGS, COND)])

    assert choice.best is not None and choice.second is None and choice.excluded == []
