"""
get_weather(place, date): 장소를 카카오로 좌표 변환한 뒤 Open-Meteo 예보를 조회한다. READ 등급.

Open-Meteo 공식 문서(2026-11-16 확인): https://open-meteo.com/en/docs
- GET https://api.open-meteo.com/v1/forecast, 키 없음
- latitude, longitude, timezone(daily 요청 시 필수), start_date/end_date(yyyy-mm-dd), 예보는 최대 16일
- 비상업 무료: 분당 600, 시간당 5,000, 일 10,000회. CC-BY 4.0 라이선스라 출처를 붙인다.
"""

from datetime import date, timedelta

from django.utils import timezone

from agent.models import RiskLevel
from agent.registry import Tool, error_result, ok_result, register
from tools import http, places
from tools.http import ExternalAPIError

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_SOURCE = "https://open-meteo.com/"
FORECAST_DAYS = 16  # 오늘 포함 16일까지 예보가 있다
HOURLY_STEP = 3  # 시간대별 요약은 3시간 간격(0, 3, ..., 21시)

# WMO 날씨 코드 → 한국어. Open-Meteo 문서의 "WMO Weather interpretation codes" 표 기준.
WEATHER_CODES = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "서리 안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비", 56: "약한 어는 이슬비", 57: "강한 어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "약한 어는 비", 67: "강한 어는 비",
    71: "약한 눈", 73: "눈", 75: "많은 눈", 77: "싸락눈",
    80: "약한 소나기", 81: "소나기", 82: "강한 소나기", 85: "약한 눈 소나기", 86: "강한 눈 소나기",
    95: "뇌우", 96: "약한 우박을 동반한 뇌우", 97: "강한 뇌우", 99: "강한 우박을 동반한 뇌우",
}  # fmt: skip


def describe_weather(code):
    return WEATHER_CODES.get(code, "알 수 없음")


def get_weather(user, place, date):
    try:
        target = _check_date(date)
        location = places.locate(place)
        forecast = http.get_json("날씨(Open-Meteo)", FORECAST_URL, params=_params(location, target))
        summary = _summarize(location, target, forecast)
    except ExternalAPIError as error:
        return error_result(str(error))
    sources = [url for url in (location.get("place_url"), OPEN_METEO_SOURCE) if url]
    return ok_result(summary, sources=sources)


def _check_date(text):
    """yyyy-mm-dd이고 예보가 있는 범위(오늘~15일 뒤, 서울 기준)인지 확인한다."""
    try:
        target = date.fromisoformat(text)
    except (TypeError, ValueError) as error:
        raise ExternalAPIError(
            "날짜는 YYYY-MM-DD 형식이어야 합니다. '내일'은 실제 날짜로 바꿔 주세요."
        ) from error
    today = timezone.localdate()
    if not today <= target < today + timedelta(days=FORECAST_DAYS):
        raise ExternalAPIError(f"날씨 예보는 오늘부터 {FORECAST_DAYS - 1}일 뒤까지만 조회할 수 있습니다.")
    return target


def _params(location, target):
    return {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "timezone": "Asia/Seoul",
        "start_date": target.isoformat(),
        "end_date": target.isoformat(),
        "daily": "temperature_2m_min,temperature_2m_max,precipitation_probability_max",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
    }


def _summarize(location, target, forecast):
    daily = forecast.get("daily") or {}
    if not daily.get("time"):
        raise ExternalAPIError("해당 날짜의 예보를 받지 못했습니다.")
    hourly = forecast.get("hourly") or {}
    times = hourly.get("time") or []
    return {
        "place": location["name"],
        "matched_place": location.get("matched_place", ""),  # 좌표를 잡은 실제 장소(출처 확인용)
        "address": location["address"],
        "date": target.isoformat(),
        "min_c": daily["temperature_2m_min"][0],
        "max_c": daily["temperature_2m_max"][0],
        "precipitation_probability_max": daily["precipitation_probability_max"][0],
        "hourly": [
            {
                "time": times[i][11:16],  # "2026-11-17T06:00" → "06:00"
                "temp_c": hourly["temperature_2m"][i],
                "precipitation_probability": hourly["precipitation_probability"][i],
                "weather": describe_weather(hourly["weather_code"][i]),
            }
            for i in range(0, len(times), HOURLY_STEP)
        ],
    }


register(
    Tool(
        name="get_weather",
        description=(
            "장소와 날짜의 날씨 예보(최저/최고 기온, 강수 확률, 3시간 간격 요약)를 조회한다. "
            "날짜는 YYYY-MM-DD이며 오늘부터 15일 뒤까지 가능하다. '내일'은 실제 날짜로 바꿔서 넣는다."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "place": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 50,
                    "description": "동네, 역, 도시 이름",
                },
                "date": {"type": "string", "description": "YYYY-MM-DD (서울 기준)"},
            },
            "required": ["place", "date"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ,
        run=get_weather,
        label=lambda place="", date="", **_: f"{place} {date} 날씨 확인",
    )
)
