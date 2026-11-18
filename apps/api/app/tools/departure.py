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
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# 같은 이름이 든 결과들이 이 거리보다 멀리 떨어져 있으면 서로 다른 지점으로 본다. 한 역의 출구·호선(수백 m)은
# 같은 곳, 같은 상호의 다른 지점(수 km)은 다른 곳이 되도록 1km로 잡았다.
AMBIGUOUS_DISTANCE_M = 1000
# 모호할 때 보여 줄 후보 수. 더 많으면 사용자가 고르기 어렵다.
MAX_CANDIDATES = 3


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
    conclusion: str
    reason: str


def minutes_from_seconds(seconds: int) -> int:
    """초 → 분(올림). 0초 이하는 0분."""
    return max(0, math.ceil(seconds / 60))


def hhmm(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def plan_departure_times(
    arrive_by: datetime, *, early_minutes: int, routes: list[RouteOption], previous_end: datetime | None
) -> DeparturePlan | None:
    """가장 빠른 경로로 권장 출발 시각을 정한다. 경로가 없으면 None(없는 소요 시간을 만들지 않는다).

    도착 목표 = arrive_by - early_minutes. 권장 출발 = 도착 목표 - 가장 빠른 경로 분.
    환승 적은 경로는 가장 빠른 경로보다 환승이 적을 때만 둔다(같거나 많으면 비교할 이유가 없다).
    """
    if not routes:
        return None
    fastest = min(routes, key=lambda r: (r.minutes, r.transfers))
    easiest = min(routes, key=lambda r: (r.transfers, r.minutes))
    target = arrive_by - timedelta(minutes=early_minutes)
    depart = target - timedelta(minutes=fastest.minutes)
    slack = None if previous_end is None else int((depart - previous_end).total_seconds() // 60)
    return DeparturePlan(
        target_arrival=target,
        depart_at=depart,
        fastest=fastest,
        fewer_transfers=easiest if easiest.transfers < fastest.transfers else None,
        slack_minutes=slack,
        conclusion=conclusion_text(arrive_by, target, depart, early_minutes),
        reason=reason_text(fastest, slack, previous_end),
    )


def conclusion_text(arrive_by: datetime, target: datetime, depart: datetime, early_minutes: int) -> str:
    """결론 한 줄: 언제 출발해 언제 도착하는지. 일찍 도착 여유가 있으면 원래 시각과 함께 밝힌다."""
    base = f"{hhmm(depart)}에 출발하면 {hhmm(target)}에 도착합니다"
    if early_minutes:
        return f"{base}({hhmm(arrive_by)}보다 {early_minutes}분 일찍)."
    return f"{base}."


def reason_text(fastest: RouteOption, slack: int | None, previous_end: datetime | None) -> str:
    """이유 한 줄: 앞 일정 뒤 여유·부족(있을 때), 없으면 경로 기준.

    부족하면 '늦을 수 있어요'로 끝내지 않고 몇 분 부족한지, 바로 출발하면 언제 도착하는지, 무엇을 바꾸면 되는지를 쓴다
    (명세 3절). 택시 같은 조회하지 않은 대안은 만들지 않는다.
    """
    if slack is None or previous_end is None:
        return f"가장 빠른 경로 기준 예상 {fastest.minutes}분, 환승 {fastest.transfers}회입니다."
    if slack > 0:
        return f"앞 일정이 끝나고 {slack}분 여유가 있습니다."
    if slack == 0:
        return "앞 일정이 끝나자마자 출발해야 맞출 수 있습니다(여유 0분)."
    arrival = previous_end + timedelta(minutes=fastest.minutes)
    short = -slack
    return (
        f"앞 일정이 끝나고 바로 출발해도 {short}분 부족합니다({hhmm(arrival)} 도착 예상). "
        f"출발을 {short}분 앞당기면 목표 도착 시각을 맞출 수 있습니다."
    )


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
