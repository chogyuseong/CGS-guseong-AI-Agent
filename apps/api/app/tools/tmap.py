"""이동 비서 B 첫 단계: 도착 목표에서 거꾸로 계산하는 출발 시각(plan_departure, READ, 채팅 전용).

흐름: 장소 이름 → 카카오 키워드 검색(좌표, 같은 이름 여러 곳이면 먼저 묻기) → TMAP 대중교통 경로(searchDttm 시각 지정)
→ departure.plan_departure_times(코드 계산) → 결론·이유·다른 경로·지도 링크.

공식 문서(2026-11-18 확인, docs/TOOLS.md "TMAP 대중교통"):
- POST https://apis.openapi.sk.com/transit/routes, 헤더 appKey. 본문 startX·startY·endX·endY(WGS84 경도·위도),
  count(1~10), lang(0 국문), format(json),
  searchDttm(yyyymmddhhmi, "사용자가 설정한 시각에 이동수단의 운행여부를 안내").
- 응답 metaData.plan.itineraries[]: totalTime(초), transferCount, totalWalkTime(초),
  fare.regular.totalFare(원),
  legs[]: mode(WALK·BUS·SUBWAY…), route(노선 이름), sectionTime(초).
- 오류 코드 11(가까움)·12·13(정류장 매핑 실패)·14(기타)는 HTTP 200, 21~24는 400, 31·32는 500. 오류 본문의 정확한 모양은
  문서에 없어 실제 호출로 확인하지 못했다(경로가 없으면 result.status를 찾아 보고, 없으면 일반 '경로 없음').
- 요금(2026-11-18 사용자가 SK Open API 요금표 확인): Free 대중교통 하루 10건, 넘으면 자동 차단. Premium은 건당 0.88원
  후불. 한 번의 도구 호출에 TMAP 요청 1회만 보내고, 앱도 하루 10건을 세어 넘기 전에 멈춘다(LIMITER).
지도 링크(카카오맵 "URL로 바로 사용하기"): https://map.kakao.com/link/by/traffic/이름,위도,경도/이름,위도,경도
(traffic = 대중교통, 경유지 지정 불가).

데이터 정책: 장소 이름은 카카오로, 좌표와 시각만 TMAP으로 간다. 일정 제목·메모·참석자는 인자에 없고, 공개 데이터만
받는 외부 도구라 메일을 읽었거나 일정 제목이 든 인자면 StepTracking이 실행 전에 막는다(data_policy.TOOL_POLICIES).
진입점: 모델(toolsets.travel_tools, 채팅 전용 — 예약·감시가 할당량을 쓰지 않게).
근거: 생활 비서 명세 3절(요구사항 밖, 2026-11-18 사용자 결정, TMAP 선택), docs/DECISIONS.md.

고칠 때 주의:
- 등록부: toolsets.travel_tools·TOOL_LABELS, data_policy.TOOL_POLICIES, results.EXTERNAL_CONTENT_TOOLS,
  evals/evaluators.PUBLIC_HOSTS, apps/web lib/toolText.ts·components/ExternalContent.tsx(결과 카드),
  prompts.SYSTEM_PROMPT.
- 결과 칸 이름(결론·이유·다른 경로·출발지·도착지·기준·경로·지도·확인 필요·후보)은 화면 카드와
  tests/test_tmap.py가 쓴다.
- 응답 칸 이름이 바뀌면 tests/fixtures/tmap_routes_two.json(문서 형식으로 만든 것)과 README를 같이 고친다.

변경 이력:
- 2026-11-18 처음 작성.
- 2026-11-19 빈 응답·오류 본문·모르는 상태·0초 이하 소요를 "경로 없음"이 아니라 응답 확인 실패(bad_response)로 바꿈.
- 2026-11-19 실제 출발이 첫 조회 시각과 30분 이상 다르면 그 시각으로 한 번 다시 조회(refine_plan).
  다시 못 하면 차이를 기준 줄에 밝힘.
"""

from datetime import datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_ai import RunContext

from app import clock
from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.rate_limit import SlidingWindowRateLimiter
from app.tools import http
from app.tools.departure import (
    DeparturePlan,
    RouteOption,
    ambiguous_places,
    hhmm,
    minutes_from_seconds,
    plan_departure_times,
    route_difference,
)
from app.tools.http import ExternalApiError
from app.tools.places import clip, keyword_search

ROUTES_URL = "https://apis.openapi.sk.com/transit/routes"
MAP_LINK = "https://map.kakao.com/link/by/traffic"
SERVICE = "TMAP"
SEOUL = ZoneInfo("Asia/Seoul")
NO_KEY_MESSAGE = "TMAP 키가 설정되지 않았습니다. .env의 TMAP_APP_KEY를 채워 주세요."
TIME_FORMAT_MESSAGE = "시각은 YYYY-MM-DDTHH:MM(서울 시각) 모양으로 주세요."
# 같은 이름 확인에 쓰는 검색 결과 수. 많을수록 다른 지점을 잘 찾지만 응답이 커진다.
PLACE_CANDIDATES = 5
# 경로 후보 수(TMAP count 1~10). 빠른 경로와 환승 적은 경로를 고르기에 충분한 수.
ROUTE_COUNT = 5
# 첫 추정 이동 시간(분). searchDttm에 "도착 목표 - 이 값"을 넣어 그 무렵 운행 기준으로 조회한다(한 번만 조회).
FIRST_GUESS_MINUTES = 60
# 실제로 나갈 시각이 첫 조회 시각과 이만큼(분) 이상 다르면 그 시각으로 한 번 다시 조회한다(2026-11-19).
# 배차·막차는 시각에 따라 달라 18:00 운행 기준 소요로 17:20 출발을 권하면 근거가 맞지 않는다. 하루 10건 한도를 아끼려고
# 한 번만, 차이가 클 때만 다시 묻는다. 바꾸면 tests/test_tmap.py의 test_*_guess_* 를 같이 본다.
REQUERY_GAP_MINUTES = 30
# TMAP Free 요금제의 대중교통 하루 호출 수(2026-11-18 사용자가 확인한 요금표, 넘으면 TMAP이 자동 차단).
# 앱도 최근 24시간 호출을 세어 넘기 전에 멈춘다(달력 하루보다 보수적). 단일 API 프로세스 기준이라 재시작하면
# 카운터가 비고, 다른 클라이언트의 호출은 세지 못한다. 요금제를 바꾸면 이 값과 docs/TOOLS.md를 같이 고친다.
DAILY_LIMIT = 10
LIMIT_KEY = "tmap-transit"
LIMITER = SlidingWindowRateLimiter(limit=DAILY_LIMIT, window_seconds=24 * 3600)
LIMIT_MESSAGE = (
    "TMAP 무료 요금제의 대중교통 조회(하루 10건)를 다 썼습니다. 내일 다시 시도하거나, 지도에서 직접 확인해 주세요."
)
# 도착 여유 상한(분). 두 시간보다 일찍 도착하려는 요청은 잘못 알아들었을 가능성이 크다.
MAX_EARLY_MINUTES = 120
# TMAP 오류 코드(HTTP 200) → 사용자 문장.
NO_ROUTE_REASONS = {
    11: "출발지와 도착지가 너무 가까워 대중교통 경로가 없습니다. 걸어서 가는 편이 낫습니다.",
    12: "출발지 근처 정류장을 찾지 못했습니다.",
    13: "도착지 근처 정류장을 찾지 못했습니다.",
}
# 경로 응답을 해석하지 못했을 때(빈 응답·오류 본문·모르는 상태·0분 이하 경로뿐). "경로 없음"과 구분한다.
# 문구를 바꾸면 tests/test_tmap.py의 '경로가 없다는 뜻은 아닙니다' 검사를 같이 고친다.
UNREADABLE_MESSAGE = "TMAP 경로 응답을 확인하지 못했습니다(경로가 없다는 뜻은 아닙니다). 잠시 후 다시 시도해 주세요."
MODE_NAMES = {"WALK": "도보", "BUS": "버스", "SUBWAY": "지하철", "EXPRESSBUS": "고속버스", "TRAIN": "기차"}

PlaceName = Annotated[str, Field(min_length=1, max_length=50, description="장소 이름이나 주소. 예: 역삼역")]
LocalTime = Annotated[str, Field(description="서울 시각 YYYY-MM-DDTHH:MM. 예: 2026-11-19T19:00")]
EarlyMinutes = Annotated[int, Field(ge=0, le=MAX_EARLY_MINUTES, description="몇 분 일찍 도착할지")]


def parse_local(value: str) -> datetime | None:
    """'2026-11-19T19:00' → 서울 시각. 모양이 다르면 None(모델이 '내일 7시'를 그대로 넘긴 경우)."""
    try:
        moment = datetime.strptime(value.strip(), "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return moment.replace(tzinfo=SEOUL)


def time_problem(arrive_by: datetime | None, previous_end: datetime | None, previous_raw: str | None) -> str | None:
    """외부 호출 전에 거절할 시각 문제. 없으면 None."""
    if arrive_by is None or (previous_raw is not None and previous_end is None):
        return TIME_FORMAT_MESSAGE
    if arrive_by <= clock.now_utc():
        return "도착 시각이 이미 지난 시각입니다. 날짜를 확인해 주세요."
    if previous_end is not None and previous_end >= arrive_by:
        return "앞 일정이 도착 시각보다 늦게 끝납니다. 시각을 확인해 주세요."
    return None


async def find_place(deps: AgentDeps, name: str) -> dict[str, Any] | list[dict[str, str]] | None:
    """장소 하나(카카오 문서) 또는 모호할 때 후보 목록, 못 찾으면 None. 외부 호출 1회(카카오)."""
    documents = await keyword_search(deps, name, size=PLACE_CANDIDATES)
    if not documents:
        return None
    candidates = ambiguous_places(name, documents)
    return candidates or documents[0]


def leg_name(leg: dict[str, Any]) -> str:
    """구간 이름: 도보는 '도보', 대중교통은 노선 이름(없으면 수단 이름). 외부 문자열은 40자로 자른다."""
    mode = str(leg.get("mode", ""))
    if mode == "WALK":
        return "도보"
    return clip(leg.get("route"), 40) or MODE_NAMES.get(mode, mode or "이동")


def route_from(itinerary: dict[str, Any]) -> RouteOption | None:
    """TMAP 경로 하나 → RouteOption. 총 소요가 없거나 0초 이하면 None(모르는 소요·0분 경로를 만들지 않는다).

    0분 이하를 받으면 권장 출발 = 도착 목표가 되어 "지금 나가도 된다"는 틀린 안내가 된다(2026-11-19 합성 응답으로 확인).
    """
    total = itinerary.get("totalTime")
    if not isinstance(total, int | float) or isinstance(total, bool) or total <= 0:
        return None
    fare = ((itinerary.get("fare") or {}).get("regular") or {}).get("totalFare")
    legs = [leg for leg in itinerary.get("legs") or [] if isinstance(leg, dict)]
    return RouteOption(
        minutes=minutes_from_seconds(int(total)),
        transfers=int(itinerary.get("transferCount") or 0),
        walk_minutes=minutes_from_seconds(int(itinerary.get("totalWalkTime") or 0)),
        fare=int(fare) if isinstance(fare, int | float) and not isinstance(fare, bool) else None,
        summary=" → ".join(leg_name(leg) for leg in legs) or "경로 정보 없음",
    )


def parse_routes(body: Any) -> tuple[list[RouteOption], str | None]:
    """응답 → (경로들, 경로가 없을 때 이유). 문서상 plan은 metaData 아래지만 바로 아래에 와도 읽는다.

    "경로 없음"은 문서에 있는 상태 코드(NO_ROUTE_REASONS)일 때만이다. 빈 응답·오류 본문·모르는 상태·쓸 수 있는 경로가
    하나도 없는 목록은 ExternalApiError(bad_response)로 올린다(2026-11-19): '모른다'를 '경로가 없다'로 바꾸면
    사용자가 대중교통을 포기하거나 다른 수단을 고르게 된다. 재시도는 하지 않는다(하루 10건 한도).
    """
    body = body if isinstance(body, dict) else {}
    meta = body.get("metaData") if isinstance(body.get("metaData"), dict) else {}
    plan = meta.get("plan") or body.get("plan") or {}
    itineraries = plan.get("itineraries") if isinstance(plan, dict) else None
    routes = [r for r in (route_from(i) for i in itineraries or [] if isinstance(i, dict)) if r is not None]
    if routes:
        return routes, None
    status = (body.get("result") or {}).get("status") if isinstance(body.get("result"), dict) else None
    if isinstance(status, int) and status in NO_ROUTE_REASONS:
        return [], NO_ROUTE_REASONS[status]
    raise ExternalApiError(UNREADABLE_MESSAGE, code="bad_response")


async def fetch_routes(deps: AgentDeps, start: dict[str, Any], end: dict[str, Any], when: datetime) -> Any:
    """TMAP 경로 조회 1회(조회용 POST: 실패해도 '결과 불명'이 아니다). 좌표와 시각만 보낸다. 키는 헤더에만.

    하루 한도(LIMITER)를 넘으면 보내지 않고 ExternalApiError(rate_limited). 보낸 요청은 실패해도 한 건으로 센다
    (TMAP도 실패한 요청을 사용량에 넣을 수 있어 보수적으로 센다).
    """
    if not LIMITER.allow(LIMIT_KEY):
        raise ExternalApiError(LIMIT_MESSAGE, code="rate_limited")
    body = {
        "startX": str(start["x"]),
        "startY": str(start["y"]),
        "endX": str(end["x"]),
        "endY": str(end["y"]),
        "count": ROUTE_COUNT,
        "lang": 0,
        "format": "json",
        "searchDttm": when.astimezone(SEOUL).strftime("%Y%m%d%H%M"),
    }
    headers = {"appKey": deps.settings.tmap_app_key.get_secret_value(), "accept": "application/json"}
    return await http.request_json(deps.http, SERVICE, "POST", ROUTES_URL, json=body, headers=headers, read_only=True)


def map_link(start: dict[str, Any], end: dict[str, Any]) -> str:
    """카카오맵 대중교통 길찾기 링크(이름,위도,경도). 이름의 쉼표·슬래시는 링크 구조를 깨므로 공백으로 바꾼다.

    좌표는 카카오가 준 문자열 그대로 쓴다(float 서식으로 바꾸면 자릿수가 잘려 수십 m 어긋난 곳을 가리킨다).
    """

    def point(doc: dict[str, Any]) -> str:
        name = clip(doc.get("place_name"), 40).replace(",", " ").replace("/", " ")
        return f"{name},{str(doc['y']).strip()},{str(doc['x']).strip()}"

    return f"{MAP_LINK}/{point(start)}/{point(end)}"


def route_row(route: RouteOption) -> dict[str, str]:
    return {
        "경로": route.summary,
        "소요": f"{route.minutes}분",
        "환승": f"{route.transfers}회",
        "도보": f"{route.walk_minutes}분",
        "요금": f"{route.fare:,}원" if route.fare is not None else "요금 미확인",
    }


def plan_content(
    plan: DeparturePlan,
    start: dict[str, Any],
    end: dict[str, Any],
    when: datetime,
    gap_note: str | None = None,
    leave: datetime | None = None,
) -> dict[str, Any]:
    """카드 내용: 결론·이유·(다른 경로)·장소·기준·경로 목록·지도 링크.

    gap_note는 다시 조회하지 못했을 때 조회 시각과 실제 출발의 차이 설명(기준 줄 끝에 붙는다).
    """
    routes = [plan.fastest] + ([plan.fewer_transfers] if plan.fewer_transfers else [])
    content: dict[str, Any] = {"결론": plan.conclusion, "이유": plan.reason}
    if plan.fewer_transfers:
        content["다른 경로"] = route_difference(plan.fastest, plan.fewer_transfers)
    content |= {
        "출발지": clip(start.get("place_name")),
        "도착지": clip(end.get("place_name")),
        "기준": f"TMAP 대중교통, {when.astimezone(SEOUL):%Y-%m-%d %H:%M} 운행 기준 예상 소요"
        "(실제 교통 상황에 따라 달라질 수 있음)" + (f". {gap_note}" if gap_note else ""),
        "경로": [route_row(r) for r in routes],
        "지도": map_link(start, end),
    }
    if leave is not None:
        # 실제로 나갈 시각(ISO, 서울). 화면이 이미 지난 출발인지 판단해 "지난 계산"을 표시한다(2026-11-19, 15번).
        # 칸 이름을 바꾸면 apps/web ExternalContent TravelCard와 테스트를 같이 고친다.
        content["출발"] = leave.isoformat()
    return content


def leave_time(plan: DeparturePlan, previous: datetime | None) -> datetime:
    """실제로 나갈 시각: 앞 일정 때문에 권장 출발에 못 나가면(여유부족·지각) 앞 일정 종료, 아니면 권장 출발."""
    return previous if previous is not None and plan.status in ("여유부족", "지각") else plan.depart_at


async def refine_plan(
    deps: AgentDeps,
    places: tuple[dict[str, Any], dict[str, Any]],
    plan: DeparturePlan,
    when: datetime,
    times: tuple[datetime, int, datetime | None],
) -> tuple[DeparturePlan, datetime, str | None]:
    """(계획, 조회 기준 시각, 차이 설명). 실제 출발이 조회 시각과 REQUERY_GAP_MINUTES 이상 다르면 그 시각으로 한 번 더
    조회해 다시 계산한다. 한도·오류·경로 없음으로 다시 못 하면 첫 계획을 쓰고 몇 분 차이인지 밝힌다.
    부수 효과: TMAP 조회 0~1회(하루 한도에 센다)."""
    arrive, early, previous = times
    today = clock.now_utc().astimezone(SEOUL).date()
    leave = leave_time(plan, previous)
    gap = abs(int((leave - when).total_seconds() // 60))
    if gap < REQUERY_GAP_MINUTES:
        return plan, when, None
    note = f"운행 정보는 {hhmm(when)} 출발 기준으로 조회해 권장 출발({hhmm(leave)})과 {gap}분 차이가 있습니다"
    try:
        routes, _ = parse_routes(await fetch_routes(deps, places[0], places[1], leave))
    except ExternalApiError:
        return plan, when, note
    second = plan_departure_times(arrive, early_minutes=early, routes=routes, previous_end=previous, today=today)
    return (second, leave, None) if second is not None else (plan, when, note)


def place_question(role: str, name: str, candidates: list[dict[str, str]]) -> ToolResult:
    content = {"확인 필요": f"{role} '{name}'가 여러 곳입니다. 어느 곳인지 알려 주세요.", "후보": candidates}
    return ToolResult.external(content)


async def plan_departure_result(
    deps: AgentDeps,
    *,
    origin: str,
    destination: str,
    arrive_by: str,
    early_minutes: int = 0,
    previous_end: str | None = None,
) -> ToolResult:
    """도구 본문(테스트가 직접 부른다). 시각·키 문제는 외부 호출 전에 거절한다. 예외를 밖으로 던지지 않는다."""
    arrive, previous = parse_local(arrive_by), parse_local(previous_end) if previous_end else None
    if problem := time_problem(arrive, previous, previous_end):
        return ToolResult.failure(problem, error_code="invalid")
    assert arrive is not None
    if not deps.settings.tmap_app_key.get_secret_value().strip():
        return ToolResult.failure(NO_KEY_MESSAGE, error_code="not_configured")
    try:
        found = [await find_place(deps, origin), await find_place(deps, destination)]
        for role, name, place in (("출발지", origin, found[0]), ("도착지", destination, found[1])):
            if place is None:
                return ToolResult.failure(f"{role} '{name}'를 찾지 못했습니다. 역이나 건물 이름으로 다시 말해 주세요.")
            if isinstance(place, list):
                return place_question(role, name, place)
        start, end = found
        assert isinstance(start, dict) and isinstance(end, dict)
        when = arrive - timedelta(minutes=early_minutes + FIRST_GUESS_MINUTES)
        routes, no_route = parse_routes(await fetch_routes(deps, start, end, when))
    except ExternalApiError as error:
        return ToolResult.from_api_error(error)
    today = clock.now_utc().astimezone(SEOUL).date()
    plan = plan_departure_times(arrive, early_minutes=early_minutes, routes=routes, previous_end=previous, today=today)
    if plan is None:
        return ToolResult.external({"결론": "대중교통 경로를 찾지 못했습니다.", "이유": no_route or ""})
    plan, when, gap_note = await refine_plan(deps, (start, end), plan, when, (arrive, early_minutes, previous))
    content = plan_content(plan, start, end, when, gap_note, leave_time(plan, previous))
    return ToolResult.external(content, [content["지도"]])


# 도구(READ). docstring은 모델에게 보이는 도구 설명이다. 계산은 코드가 한다(모델이 시각을 빼고 더하지 않는다).
async def plan_departure(
    ctx: RunContext[AgentDeps],
    origin: PlaceName,
    destination: PlaceName,
    arrive_by: LocalTime,
    early_minutes: EarlyMinutes = 0,
    previous_end: LocalTime | None = None,
) -> ToolResult:
    """대중교통으로 도착 시각에 맞추려면 언제 출발해야 하는지, 앞 일정 뒤 시간이 충분한지 계산한다.
    origin·destination에는 장소 이름이나 주소만 넣는다(일정 제목·메모·참석자는 넣지 않는다).
    출발지가 앞 일정 장소면 그 장소를 origin으로, 그 일정이 끝나는 시각을 previous_end로 넣는다.
    결과의 결론·이유 문장을 바꾸지 말고 그대로 전한다. '확인 필요'가 오면 후보 중 어느 곳인지 사용자에게 묻는다."""
    return await plan_departure_result(
        ctx.deps,
        origin=origin,
        destination=destination,
        arrive_by=arrive_by,
        early_minutes=early_minutes,
        previous_end=previous_end,
    )


def plan_departure_label(args: dict[str, Any]) -> str:
    return f"'{clip(args.get('destination'), 20)}'까지 출발 시각 계산하는 중"
