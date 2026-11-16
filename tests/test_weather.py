"""4단계: get_weather. Open-Meteo 실제 응답(tests/fixtures/open_meteo_forecast.json)으로 테스트한다."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from tools.http import ExternalAPIError

FIXTURE = json.loads(Path(__file__).parent.joinpath("fixtures/open_meteo_forecast.json").read_text("utf-8"))
SEONGSU = {
    "name": "성수동",
    "address": "서울 성동구 성수동1가",
    "latitude": 37.5446,
    "longitude": 127.0557,
    "place_url": "",
}


@pytest.fixture
def located(monkeypatch):
    monkeypatch.setattr("tools.places.locate", lambda place: SEONGSU)


@pytest.fixture
def forecast_api(monkeypatch):
    """Open-Meteo 호출을 가로채 실제 응답 파일을 돌려준다."""
    calls = []

    def fake_get_json(service, url, params=None, headers=None):
        calls.append({"url": url, "params": params})
        return FIXTURE

    monkeypatch.setattr("tools.weather.http.get_json", fake_get_json)
    return calls


def fixture_date():
    return FIXTURE["daily"]["time"][0]


def run(place="성수동", day=None):
    from agent.registry import get_tool, run_tool

    return run_tool(get_tool("get_weather"), None, {"place": place, "date": day or fixture_date()})


def test_registered_as_read_tool():
    from agent.registry import get_tool

    tool = get_tool("get_weather")
    assert tool.risk_level == "READ"
    assert tool.input_schema["required"] == ["place", "date"]


def test_summarizes_real_response(located, forecast_api, freeze_seoul_date):
    freeze_seoul_date(date.fromisoformat(fixture_date()) - timedelta(days=1))
    result = run()

    assert result["ok"] is True
    data = result["data"]
    assert data["place"] == "성수동"
    assert data["date"] == fixture_date()
    assert data["min_c"] == FIXTURE["daily"]["temperature_2m_min"][0]
    assert data["max_c"] == FIXTURE["daily"]["temperature_2m_max"][0]
    assert data["precipitation_probability_max"] == FIXTURE["daily"]["precipitation_probability_max"][0]
    assert len(data["hourly"]) == 8  # 3시간 간격
    assert set(data["hourly"][0]) == {"time", "temp_c", "precipitation_probability", "weather"}
    assert data["hourly"][0]["time"] == "00:00"


def test_has_sources_and_fetched_at(located, forecast_api, freeze_seoul_date):
    freeze_seoul_date(date.fromisoformat(fixture_date()))
    result = run()

    assert "https://open-meteo.com/" in result["sources"]
    assert result["fetched_at"]


def test_requests_seoul_timezone_and_exact_date(located, forecast_api, freeze_seoul_date):
    freeze_seoul_date(date.fromisoformat(fixture_date()))
    run()

    params = forecast_api[0]["params"]
    assert forecast_api[0]["url"] == "https://api.open-meteo.com/v1/forecast"
    assert params["timezone"] == "Asia/Seoul"
    assert params["start_date"] == params["end_date"] == fixture_date()
    assert (params["latitude"], params["longitude"]) == (37.5446, 127.0557)


def test_weather_codes_become_korean(located, forecast_api, freeze_seoul_date):
    from tools.weather import describe_weather

    assert describe_weather(0) == "맑음"
    assert describe_weather(63) == "비"
    assert describe_weather(75) == "많은 눈"
    assert describe_weather(12345) == "알 수 없음"


@pytest.mark.parametrize("days_from_today", [-1, 16])
def test_date_out_of_forecast_range_fails_without_calling_api(
    located, forecast_api, freeze_seoul_date, days_from_today
):
    today = date(2026, 11, 16)
    freeze_seoul_date(today)
    result = run(day=(today + timedelta(days=days_from_today)).isoformat())

    assert result["ok"] is False
    assert forecast_api == []


def test_bad_date_format_fails(located, forecast_api):
    result = run(day="내일")
    assert result["ok"] is False
    assert forecast_api == []


def test_unknown_place_fails(monkeypatch, forecast_api):
    def not_found(place):
        raise ExternalAPIError(f"'{place}' 위치를 찾지 못했습니다.")

    monkeypatch.setattr("tools.places.locate", not_found)
    result = run(place="없는동네")

    assert result["ok"] is False
    assert "없는동네" in result["error"]
    assert forecast_api == []


@pytest.mark.parametrize(
    "message",
    ["날씨 응답이 10초 안에 오지 않았습니다.", "날씨 인증에 실패했습니다.", "날씨 호출 한도를 넘었습니다."],
)
def test_api_failures_become_ok_false(located, monkeypatch, freeze_seoul_date, message):
    freeze_seoul_date(date.fromisoformat(fixture_date()))

    def fail(*args, **kwargs):
        raise ExternalAPIError(message)

    monkeypatch.setattr("tools.weather.http.get_json", fail)
    result = run()

    assert result == {**result, "ok": False, "error": message}


def test_empty_forecast_fails(located, monkeypatch, freeze_seoul_date):
    freeze_seoul_date(date.fromisoformat(fixture_date()))
    monkeypatch.setattr("tools.weather.http.get_json", lambda *a, **k: {"daily": {"time": []}})

    result = run()
    assert result["ok"] is False


@pytest.fixture
def freeze_seoul_date(monkeypatch):
    def freeze(day):
        monkeypatch.setattr("tools.weather.timezone.localdate", lambda: day)

    return freeze


def test_today_uses_real_seoul_date_by_default():
    from tools import weather

    assert weather.timezone.localdate() == timezone.localdate()
