"""get_weather(place, date): 장소를 카카오 로컬로 좌표 변환한 뒤 Open-Meteo 예보를 조회한다. READ 등급.

명세: "카카오 로컬로 좌표 변환 후 Open-Meteo". Open-Meteo 지오코딩 API(GeoNames)는 '성수동', '강남역' 같은
동네·역 이름을 잘 찾지 못해 명세대로 카카오를 쓴다(docs/DECISIONS.md 4단계).

Open-Meteo 공식 문서(2026-11-17 확인, docs/TOOLS.md): https://open-meteo.com/en/docs
- GET https://api.open-meteo.com/v1/forecast, 키 없음
- latitude, longitude, timezone(daily를 요청하면 필수), start_date/end_date(yyyy-mm-dd), 예보는 16일까지
- 오류: HTTP 400 + {"error": true, "reason": "..."}
- 비상업 무료(한도는 docs/TOOLS.md). 출처로 Open-Meteo를 항상 붙인다.

진입점: 모델(toolsets.read_tools의 get_weather, 예약·감시 task 포함). 감시는 이 도구를 주기적으로 실행해
결과의 precipitation_probability_max 같은 키를 숫자 조건으로 비교한다(app/automation/conditions.py).
외부 호출: 카카오 키워드 검색 1회(places.locate) + Open-Meteo 1회. 날짜가 범위 밖이면 둘 다 부르지 않는다.
데이터 정책: public만 받는다(data_policy.TOOL_POLICIES). 메일을 읽은 작업·문맥, 조회한 일정 제목이 인자에 있으면 막힌다.
근거: docs/DECISIONS.md 4단계(2026-11-17), docs/TOOLS.md "get_weather".

고칠 때 주의:
- summarize의 결과 키(summary, min_c, max_c, precipitation_probability_max, hourly)는 감시의 condition_spec.field가
  가리키는 경로다. 바꾸면 이미 저장된 감시가 값을 못 읽어 unknown이 되고,
  automation.py NumberField 설명의 예시도 틀린다.
- 등록부 네 곳: toolsets.read_tools와 TOOL_LABELS(get_weather_label), data_policy.TOOL_POLICIES,
  results.EXTERNAL_CONTENT_TOOLS. FORECAST_URL·OPEN_METEO_SOURCE는 evals/fake_externals.py·evals/evaluators.py가 쓴다.
- 요청 칸을 바꾸면 tests/fixtures/open_meteo_*.json, tests/test_weather.py, docs/TOOLS.md를 같이 고친다.
- 오늘 날짜는 반드시 today_seoul()로 얻는다. tests/test_weather.py가 이 이름을 바꿔 끼우고, eval은 app.clock을 고정한다.
"""

from datetime import date, timedelta
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from app import clock
from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.tools import http, places
from app.tools.http import ExternalApiError
from app.tools.places import Location, clip

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_SOURCE = "https://open-meteo.com/"
SERVICE = "날씨(Open-Meteo)"
FORECAST_DAYS = 16  # 오늘 포함 16일까지 예보가 있다
HOURLY_STEP = 3  # 시간대별 요약은 3시간 간격(0, 3, ..., 21시) 8개
# 모델이 '내일'처럼 말로 넣으면 Field 단계에서 돌려보낸다.
# 감시도 실제 날짜가 있어야 만료일을 정한다(store.watch_expiry).
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

# WMO 날씨 코드 → 한국어. Open-Meteo 문서의 "WMO Weather interpretation codes" 표 기준.
WEATHER_CODES = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "서리 안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비", 56: "약한 어는 이슬비", 57: "강한 어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "약한 어는 비", 67: "강한 어는 비",
    71: "약한 눈", 73: "눈", 75: "많은 눈", 77: "싸락눈",
    80: "약한 소나기", 81: "소나기", 82: "강한 소나기", 85: "약한 눈 소나기", 86: "강한 눈 소나기",
    95: "뇌우", 96: "약한 우박을 동반한 뇌우", 99: "강한 우박을 동반한 뇌우",
}  # fmt: skip

PlaceName = Annotated[str, Field(min_length=1, max_length=50, description="동네, 역, 도시 이름. 예: 성수동")]
ForecastDate = Annotated[str, Field(pattern=DATE_PATTERN, description="YYYY-MM-DD (서울 기준). '내일'은 실제 날짜로")]


def today_seoul() -> date:
    """서울 기준 오늘(app.clock). 테스트가 바꿔 끼운다."""
    return clock.today_seoul()


def describe_weather(code: object) -> str:
    """WMO 코드를 한국어로. 표에 없는 코드나 정수가 아닌 값(빈 칸)은 "알 수 없음"(예외 없음)."""
    return WEATHER_CODES.get(code, "알 수 없음") if isinstance(code, int) else "알 수 없음"


def check_date(text: str) -> date:
    """yyyy-mm-dd이고 예보가 있는 범위(오늘~15일 뒤, 서울 기준)인지 확인한다. 아니면 API를 부르지 않는다.

    형식이 틀리거나 범위 밖이면 ExternalApiError(→ get_weather가 failed로 바꾼다). 카카오·Open-Meteo 호출 전에
    거절해, 예보가 없는 날짜로 외부 호출을 쓰지 않고 모델에게 고칠 방법을 문장으로 준다. "오늘"은 today_seoul() 기준.
    """
    try:
        target = date.fromisoformat(text)
    except ValueError as error:
        raise ExternalApiError("날짜는 YYYY-MM-DD 형식이어야 합니다. '내일'은 실제 날짜로 바꿔 주세요.") from error
    today = today_seoul()
    if not today <= target < today + timedelta(days=FORECAST_DAYS):
        raise ExternalApiError(f"날씨 예보는 오늘부터 {FORECAST_DAYS - 1}일 뒤까지만 조회할 수 있습니다.")
    return target


def forecast_params(location: Location, target: date) -> dict[str, Any]:
    """Open-Meteo 요청 칸. 하루만 받는다(start_date = end_date). daily를 요청하므로 timezone이 필수다(공식 문서)."""
    return {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": "Asia/Seoul",
        "start_date": target.isoformat(),
        "end_date": target.isoformat(),
        "daily": "temperature_2m_min,temperature_2m_max,precipitation_probability_max,weather_code",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
    }


def first(values: object) -> Any:
    """하루만 요청했으므로 daily 배열의 첫 값이 그 날이다. 목록이 아니거나 비면 None."""
    return values[0] if isinstance(values, list) and values else None


def hourly_summary(hourly: dict[str, Any]) -> list[dict[str, Any]]:
    """24시간 값을 3시간 간격 8개로 줄인다(모델에게 넘기는 토큰을 줄이려고). 칸 길이가 달라도 없는 값은 None."""
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    rain = hourly.get("precipitation_probability") or []
    codes = hourly.get("weather_code") or []
    return [
        {
            "time": str(times[i])[11:16],  # "2026-11-20T06:00" → "06:00"
            "temp_c": temps[i] if i < len(temps) else None,
            "precipitation_probability": rain[i] if i < len(rain) else None,
            "weather": describe_weather(codes[i] if i < len(codes) else None),
        }
        for i in range(0, len(times), HOURLY_STEP)
    ]


def summarize(location: Location, target: date, forecast: Any) -> dict[str, Any]:
    """예보 응답을 모델·감시가 읽을 요약으로 바꾼다. daily가 비면 ExternalApiError(날씨는 답을 못 한 것이라 failed).

    검색과 달리 "결과 없음"을 정상 답으로 두지 않는다(docs/TOOLS.md 빈 결과 규칙).
    """
    daily = forecast.get("daily") if isinstance(forecast, dict) else None
    if not isinstance(daily, dict) or not daily.get("time"):
        raise ExternalApiError("해당 날짜의 예보를 받지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return {
        "place": location.name,
        "matched_place": location.matched_place,  # 좌표를 잡은 실제 장소(출처 확인용)
        "address": location.address,
        "date": target.isoformat(),
        "summary": describe_weather(first(daily.get("weather_code"))),
        "min_c": first(daily.get("temperature_2m_min")),
        "max_c": first(daily.get("temperature_2m_max")),
        "precipitation_probability_max": first(daily.get("precipitation_probability_max")),
        "hourly": hourly_summary(forecast.get("hourly") or {}),
    }


# 도구(READ). docstring은 모델에게 보이는 도구 설명이라 고치지 않는다.
# 순서: 날짜 검사 → 좌표(카카오) → 예보(Open-Meteo) → 요약. 어느 단계의 ExternalApiError든 ToolResult로 바꾼다.
# 결과는 ToolResult.external로 감싼다(좌표를 잡은 장소 이름은 남이 쓴 글이라서, DECISIONS 4단계).
async def get_weather(ctx: RunContext[AgentDeps], place: PlaceName, date: ForecastDate) -> ToolResult:
    """장소와 날짜의 날씨 예보(날씨, 최저·최고 기온, 강수 확률, 3시간 간격 요약)를 조회한다.
    날짜는 서울 기준 YYYY-MM-DD이고 오늘부터 15일 뒤까지 된다. '내일', '금요일'은 실제 날짜로 바꿔 넣는다."""
    try:
        target = check_date(date)
        location = await places.locate(ctx.deps, place)
        forecast = await http.get_json(ctx.deps.http, SERVICE, FORECAST_URL, params=forecast_params(location, target))
        summary = summarize(location, target, forecast)
    except ExternalApiError as error:
        return ToolResult.from_api_error(error)
    return ToolResult.external(summary, sources=[location.place_url, OPEN_METEO_SOURCE])


def get_weather_label(args: dict[str, Any]) -> str:
    """타임라인 문구(toolsets.TOOL_LABELS에 이름으로 등록)."""
    return f"{clip(args.get('place'), 20)} {clip(args.get('date'), 10)} 날씨 확인하는 중"
