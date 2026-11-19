"""이동 비서 B 첫 단계: 도착 목표에서 거꾸로 계산하는 출발 시각(결정적 코드, 모델·외부 호출 없음).

맡은 일: 경로 후보(분·환승)와 도착 목표·도착 여유·앞 일정 종료 시각으로 권장 출발 시각, 앞 일정 뒤 여유(음수면 부족),
결론·이유 문장, 빠른 경로와 환승 적은 경로의 차이 문장, 같은 이름 지점이 여러 곳인지 판정을 만든다.
진입점: app/tools/tmap.py plan_departure(채팅 도구). 테스트: tests/test_departure.py.
근거: 생활 비서 명세 3절(요구사항 밖, 2026-11-18 사용자 결정, docs/DECISIONS.md) — "계산은 코드로 한다",
"늦을 가능성이 보이면 확인된 선택지를 보여 준다", "같은 이름의 지점이 둘 이상이면 먼저 확인한다".

고칠 때 주의:
- 결론·이유 문장은 tests/test_departure.py가 글자 그대로 본다. 화면(apps/web ExternalContent)은 문장을 그대로 보인다.
- 분은 초를 올림해서 센다(TMAP totalTime은 초). 내림하면 실제보다 늦게 출발하라고 안내하게 된다.
- AMBIGUOUS_DISTANCE_M을 바꾸면 "역 출구 여러 개"가 모호로 바뀌거나 "다른 지점"을 놓친다. 테스트 두 개를 같이 본다.

변경 이력:
- 2026-11-18 처음 작성.
- 2026-11-19 앞 일정 뒤 판정을 네 상태(여유·딱맞음·여유부족·지각)로 나눔. 예전에는 약속 전에 도착해도 "N분 부족",
  앞 일정이 끝나기 전에는 나갈 수 없는데 "출발을 N분 앞당기면"이라고 했다. 자정을 넘으면 날짜·요일을 함께 쓴다.
- 2026-11-19 오늘이 아닌 출발이면 결론에 날짜를 붙인다(today 인자, 오래된 카드가 지금 지시처럼 보이지 않게).
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

# 같은 이름이 든 결과들이 이 거리보다 멀리 떨어져 있으면 서로 다른 지점으로 본다. 한 역의 출구·호선(수백 m)은
# 같은 곳, 같은 상호의 다른 지점(수 km)은 다른 곳이 되도록 1km로 잡았다.
AMBIGUOUS_DISTANCE_M = 1000
# 모호할 때 보여 줄 후보 수. 더 많으면 사용자가 고르기 어렵다.
MAX_CANDIDATES = 3
WEEKDAYS = "월화수목금토일"
# 앞 일정 뒤 판정. 여유부족 = 약속에는 맞지만 원한 사전 도착 여유가 줄어듦, 지각 = 바로 출발해도 약속보다 늦음.
# 바꾸면 tests/test_departure.py와 docs/TOOLS.md "TMAP 대중교통" 절을 같이 고친다.
type SlackStatus = Literal["여유", "딱맞음", "여유부족", "지각"]


@dataclass(frozen=True)
class RouteOption:
    """경로 하나. minutes는 총 소요(올림한 분), fare는 원(모르면 None), summary는 '도보 → 2호선 → 도보' 모양."""

    minutes: int
    transfers: int
    walk_minutes: int
    fare: int | None
    summary: str


@dataclass(frozen=True)
class DeparturePlan:
    """계산 결과. slack_minutes는 앞 일정 종료부터 권장 출발까지의 분(음수 = 부족), 앞 일정이 없으면 None."""

    target_arrival: datetime
    depart_at: datetime
    fastest: RouteOption
    fewer_transfers: RouteOption | None
    slack_minutes: int | None
    # 앞 일정이 끝나자마자 출발했을 때의 도착 예상과 판정(앞 일정이 없으면 둘 다 None).
    arrival_if_leave_now: datetime | None
    status: SlackStatus | None
    conclusion: str
    reason: str


def minutes_from_seconds(seconds: int) -> int:
    """초 → 분(올림). 0초 이하는 0분."""
    return max(0, math.ceil(seconds / 60))


def hhmm(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def day_time(moment: datetime) -> str:
    """'11월 20일(금) 23:35'. 자정을 넘는 출발·도착에서 날짜를 함께 보이려고 쓴다."""
    return f"{moment.month}월 {moment.day}일({WEEKDAYS[moment.weekday()]}) {hhmm(moment)}"


def moment_text(moment: datetime, today: date | None) -> str:
    """오늘(서울)이 아닌 날이면 '11월 19일(목) 17:50', 오늘이거나 모르면 '17:50'(2026-11-19, 15번).
    '내일' 같은 상대 표현은 쓰지 않는다: 카드를 나중에 다시 보면 틀린 말이 된다."""
    return day_time(moment) if today is not None and moment.date() != today else hhmm(moment)


def slack_status(slack: int | None, arrival_now: datetime | None, arrive_by: datetime) -> SlackStatus | None:
    """앞 일정 뒤 판정. 늦는지는 약속 시각(arrive_by)으로, 여유부족은 원한 일찍 도착 목표로 가른다."""
    if slack is None or arrival_now is None:
        return None
    if slack > 0:
        return "여유"
    if slack == 0:
        return "딱맞음"
    return "여유부족" if arrival_now <= arrive_by else "지각"


def plan_departure_times(
    arrive_by: datetime,
    *,
    early_minutes: int,
    routes: list[RouteOption],
    previous_end: datetime | None,
    today: date | None = None,
) -> DeparturePlan | None:
    """가장 빠른 경로로 권장 출발 시각을 정한다. 경로가 없으면 None(없는 소요 시간을 만들지 않는다).

    도착 목표 = arrive_by - early_minutes. 권장 출발 = 도착 목표 - 가장 빠른 경로 분.
    환승 적은 경로는 가장 빠른 경로보다 환승이 적을 때만 둔다(같거나 많으면 비교할 이유가 없다).
    today(서울 오늘)를 주면 오늘이 아닌 출발에 날짜를 붙인다. 주지 않으면 시각만(예전 모양).
    """
    if not routes:
        return None
    fastest = min(routes, key=lambda r: (r.minutes, r.transfers))
    easiest = min(routes, key=lambda r: (r.transfers, r.minutes))
    target = arrive_by - timedelta(minutes=early_minutes)
    depart = target - timedelta(minutes=fastest.minutes)
    slack = None if previous_end is None else int((depart - previous_end).total_seconds() // 60)
    arrival_now = None if previous_end is None else previous_end + timedelta(minutes=fastest.minutes)
    status = slack_status(slack, arrival_now, arrive_by)
    if status in ("여유부족", "지각") and previous_end is not None and arrival_now is not None:
        # 권장 출발이 앞 일정 종료보다 빠르다: 따를 수 없는 출발 시각 대신 끝나자마자 출발한 경우를 말한다.
        conclusion = (
            f"앞 일정이 끝나는 {moment_text(previous_end, today)}에 바로 출발하면 {hhmm(arrival_now)} 도착 예상입니다."
        )
        reason = shortage_text(status, arrive_by, target, arrival_now, early_minutes)
    else:
        conclusion = conclusion_text(arrive_by, target, depart, early_minutes, today)
        reason = reason_text(fastest, slack, previous_end)
    return DeparturePlan(
        target_arrival=target,
        depart_at=depart,
        fastest=fastest,
        fewer_transfers=easiest if easiest.transfers < fastest.transfers else None,
        slack_minutes=slack,
        arrival_if_leave_now=arrival_now,
        status=status,
        conclusion=conclusion,
        reason=reason,
    )


def shortage_text(
    status: SlackStatus, arrive_by: datetime, target: datetime, arrival_now: datetime, early_minutes: int
) -> str:
    """여유부족·지각 이유. "앞당기라"처럼 앞 일정 때문에 따를 수 없는 지시나 조회하지 않은 대안(택시)은 쓰지 않는다."""
    if status == "여유부족":
        late_for_buffer = int((arrival_now - target).total_seconds() // 60)
        spare = int((arrive_by - arrival_now).total_seconds() // 60)
        return (
            f"약속({hhmm(arrive_by)})에는 맞지만 원하신 {early_minutes}분 전 도착보다 "
            f"{late_for_buffer}분 늦습니다(여유 {spare}분)."
        )
    late = int((arrival_now - arrive_by).total_seconds() // 60)
    return f"약속({hhmm(arrive_by)})보다 {late}분 늦습니다. 약속에 맞추려면 앞 일정이 {late}분 일찍 끝나야 합니다."


def conclusion_text(
    arrive_by: datetime, target: datetime, depart: datetime, early_minutes: int, today: date | None = None
) -> str:
    """결론 한 줄: 언제 출발해 언제 도착하는지. 일찍 도착 여유가 있으면 원래 시각과 함께 밝힌다."""
    # 자정을 넘으면(출발과 도착의 날짜가 다르면) 날짜·요일을 함께 쓴다. 같은 날이면 예전처럼 시각만.
    if depart.date() != target.date():
        base = f"{day_time(depart)}에 출발하면 {day_time(target)}에 도착합니다"
    else:
        base = f"{moment_text(depart, today)}에 출발하면 {hhmm(target)}에 도착합니다"
    if early_minutes:
        return f"{base}({hhmm(arrive_by)}보다 {early_minutes}분 일찍)."
    return f"{base}."


def reason_text(fastest: RouteOption, slack: int | None, previous_end: datetime | None) -> str:
    """이유 한 줄(여유·딱맞음·앞 일정 없음). 여유부족·지각은 shortage_text가 쓴다."""
    if slack is None or previous_end is None:
        return f"가장 빠른 경로 기준 예상 {fastest.minutes}분, 환승 {fastest.transfers}회입니다."
    if slack > 0:
        return f"앞 일정이 끝나고 {slack}분 여유가 있습니다."
    return "앞 일정이 끝나자마자 출발해야 맞출 수 있습니다(여유 0분)."


def route_difference(fastest: RouteOption, other: RouteOption) -> str:
    """'6분 늦지만 환승이 1번 적습니다.'처럼 두 경로의 실제 차이를 일상적인 말로."""
    later = other.minutes - fastest.minutes
    fewer = fastest.transfers - other.transfers
    time_part = f"{later}분 늦지만" if later > 0 else "시간은 같고"
    return f"{time_part} 환승이 {fewer}번 적습니다."


def distance_m(a: dict[str, Any], b: dict[str, Any]) -> float:
    """두 카카오 결과(문서) 사이의 거리(m, 하버사인). 카카오는 y가 위도, x가 경도다."""
    lat1, lng1, lat2, lng2 = (math.radians(float(v)) for v in (a["y"], a["x"], b["y"], b["x"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def ambiguous_places(query: str, documents: list[dict[str, Any]]) -> list[dict[str, str]]:
    """이름에 query가 든 결과 중 첫 결과와 AMBIGUOUS_DISTANCE_M보다 먼 곳이 있으면 후보 목록, 아니면 [].

    잘못된 지점으로 안내하기 전에 사용자가 고르게 한다(명세 3절). 이름에 query가 없는 결과는 비교하지 않는다
    (카카오가 비슷한 다른 장소를 섞어 줄 수 있다).
    """
    named = [d for d in documents if query.replace(" ", "") in str(d.get("place_name", "")).replace(" ", "")]
    if len(named) < 2:
        return []
    first = named[0]
    if all(distance_m(first, other) <= AMBIGUOUS_DISTANCE_M for other in named[1:]):
        return []
    return [
        {"name": str(d.get("place_name", "")), "address": str(d.get("road_address_name") or d.get("address_name", ""))}
        for d in named[:MAX_CANDIDATES]
    ]
