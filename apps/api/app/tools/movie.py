"""영화 비서 C 첫 단계: 회차가 오늘 저녁 일정에 들어가는지 시간 순서대로 계산(결정적 코드, 모델·외부 호출 없음).

순서: 만남 → 식사 → 극장까지 이동 → 입장 여유 → 상영 → 종료 → 귀가 이동. 회차마다 식사 가능 시간·종료(공식 시간표 값이
없으면 러닝타임으로 계산하고 '예상'으로 표시)·집 도착을 구하고, 조건(식사 시간, 마감)을 사용자의 말로 판정한다.
진입점: app/tools/showtimes.py compare_showtimes(채팅 도구). 테스트: tests/test_movie_plan.py.
근거: 생활 비서 명세 4절(요구사항 밖, 2026-11-18 사용자 결정) — "23시 전 영화 종료와 23시 전 집 도착을 구분",
"1순위와 차선의 실제 차이를 한 줄로", "제외 이유를 사용자의 원래 조건으로 설명".

고칠 때 주의:
- 판정·결론·차이 문장은 tests/test_movie_plan.py가 글자 그대로 본다.
  화면(ExternalContent 영화 카드)은 문장을 그대로 보인다.
- 순위 규칙(pick): 조건을 모두 만족하는 회차 중 '가장 빠듯한 여유'(식사 남는 분과 마감까지 남는 분 중 작은 값)가 큰 것.
  바꾸면 1순위가 달라지므로 테스트와 docs/TOOLS.md를 같이 고친다.

변경 이력:
- 2026-11-18 처음 작성.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

DeadlineKind = Literal["home", "end"]


@dataclass(frozen=True)
class Showing:
    """회차 하나. end는 시간표에 적힌 종료 시각(없으면 None), runtime_minutes는 상영 시간(분, 없으면 None)."""

    theater: str
    movie: str
    start: datetime
    end: datetime | None
    runtime_minutes: int | None

    @property
    def label(self) -> str:
        return f"{self.theater} {self.start:%H:%M} 회차"


@dataclass(frozen=True)
class Legs:
    """이동 시간(분): 식사 장소에서 극장까지, 극장에서 집까지."""

    to_theater: int
    home: int


@dataclass(frozen=True)
class Conditions:
    """사용자 조건. deadline_kind가 home이면 집 도착 마감, end면 영화 종료 마감이다."""

    meet_at: datetime
    dinner_minutes: int
    entry_buffer: int
    deadline: datetime
    deadline_kind: DeadlineKind


@dataclass(frozen=True)
class Evaluation:
    """회차 하나의 계산 결과. problems가 비면 조건을 모두 만족한다. margin은 정렬용 '가장 빠듯한 여유'(분)."""

    showing: Showing
    dinner_available: int
    end: datetime | None
    end_estimated: bool
    home_arrival: datetime | None
    problems: list[str] = field(default_factory=list)
    margin: int = 0


@dataclass(frozen=True)
class Choice:
    best: Evaluation | None
    second: Evaluation | None
    excluded: list[Evaluation]
    conclusion: str
    reason: str


def minutes_between(earlier: datetime, later: datetime) -> int:
    return int((later - earlier).total_seconds() // 60)


def end_of(showing: Showing) -> tuple[datetime | None, bool]:
    """(종료 시각, 예상인지). 시간표의 종료가 있으면 그 값, 없으면 시작 + 상영 시간(예상).

    둘 다 없으면 (None, False): 끝나는 시각을 지어내지 않는다.
    """
    if showing.end is not None:
        return showing.end, False
    if showing.runtime_minutes is not None:
        return showing.start + timedelta(minutes=showing.runtime_minutes), True
    return None, False


def evaluate(showing: Showing, legs: Legs, cond: Conditions) -> Evaluation:
    """회차 하나를 계산하고 조건을 판정한다. 식사 가능 시간 = 시작 - 입장 여유 - 극장까지 이동 - 만남 시각."""
    leave_dinner = showing.start - timedelta(minutes=cond.entry_buffer + legs.to_theater)
    dinner = minutes_between(cond.meet_at, leave_dinner)
    end, estimated = end_of(showing)
    home = end + timedelta(minutes=legs.home) if end is not None else None
    problems: list[str] = []
    if dinner < cond.dinner_minutes:
        short = cond.dinner_minutes - dinner
        problems.append(f"식사가 {dinner}분뿐입니다(원하신 {cond.dinner_minutes}분보다 {short}분 부족).")
    if end is None or home is None:
        problems.append("시간표에 종료 시각이나 상영 시간이 없어 끝나는 시각을 알 수 없습니다.")
        return Evaluation(showing, dinner, None, False, None, problems, -(10**6))
    checked = home if cond.deadline_kind == "home" else end
    late = minutes_between(cond.deadline, checked)
    if late > 0:
        what = "집 도착" if cond.deadline_kind == "home" else "영화 종료"
        problems.append(f"{what} {checked:%H:%M} 예상이라 {cond.deadline:%H:%M}보다 {late}분 늦습니다.")
    margin = min(dinner - cond.dinner_minutes, -late)
    return Evaluation(showing, dinner, end, estimated, home, problems, margin)


def pick(evaluations: list[Evaluation]) -> Choice:
    """1순위·차선·제외. 조건을 만족하는 회차가 없으면 best=None이고 가장 가까운 회차의 이유를 말한다.

    없는 후보를 만들지 않는다: 만족하는 회차가 하나면 차선은 None이다.
    """
    ok = sorted((e for e in evaluations if not e.problems), key=lambda e: (-e.margin, e.showing.start))
    excluded = [e for e in evaluations if e.problems]
    if not ok:
        closest = max(evaluations, key=lambda e: e.margin)
        reason = f"가장 가까운 {closest.showing.label}: {' '.join(closest.problems)}"
        return Choice(None, None, excluded, "조건을 모두 만족하는 회차가 없습니다.", reason)
    best = ok[0]
    conclusion = f"{best.showing.label}가 가장 편합니다."
    return Choice(best, ok[1] if len(ok) > 1 else None, excluded, conclusion, why(best))


def why(e: Evaluation) -> str:
    """이유 한 줄: 식사 시간, 종료(예상이면 표시), 집 도착."""
    assert e.end is not None and e.home_arrival is not None
    end = f"{e.end:%H:%M}" + (" 예상" if e.end_estimated else "")
    return f"식사 {e.dinner_available}분, {end} 종료, 집 도착 {e.home_arrival:%H:%M} 예상으로 조건을 모두 만족합니다."


def showing_difference(best: Evaluation, second: Evaluation) -> str:
    """두 회차의 실제 차이(식사가 더 남는 쪽, 귀가가 빠른 쪽)를 한 줄로. 차이가 없는 항목은 말하지 않는다.

    예: "CGV 강남 20:30 회차는 식사가 15분 더 남고, 메가박스 코엑스 20:15 회차는 귀가가 15분 빠릅니다."
    """
    clauses: list[tuple[str, str]] = []  # (이어 쓰는 꼴, 끝맺는 꼴)
    more, less = sorted((best, second), key=lambda e: -e.dinner_available)
    if more.dinner_available > less.dinner_available:
        head = f"{more.showing.label}는 식사가 {more.dinner_available - less.dinner_available}분 더 "
        clauses.append((head + "남고", head + "남습니다."))
    if best.home_arrival and second.home_arrival and best.home_arrival != second.home_arrival:
        early, late = sorted((best, second), key=lambda e: e.home_arrival or e.showing.start)
        assert early.home_arrival and late.home_arrival
        head = f"{early.showing.label}는 귀가가 {minutes_between(early.home_arrival, late.home_arrival)}분 "
        clauses.append((head + "빠르고", head + "빠릅니다."))
    if not clauses:
        return "식사 시간과 귀가 시각이 같습니다."
    return ", ".join([c[0] for c in clauses[:-1]] + [clauses[-1][1]])
