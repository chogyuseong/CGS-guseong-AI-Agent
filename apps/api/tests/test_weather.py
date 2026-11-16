"""get_weather (READ, 카카오 좌표 변환 + Open-Meteo) 테스트. 네트워크 없이 tests/fixtures의 응답으로 확인한다.

정상, 타임아웃, 401, 429, 빈 결과를 ToolResult로 확인하고, 에이전트 실행으로 step 1개(ok, label)를 확인한다.
"""

from datetime import date

import httpx
import pytest

from app.agent.agent import MyAgent
from app.agent.deps import AgentDeps
from app.agent.results import EXTERNAL_NOTICE
from app.tools import weather
from tests.conftest import payload, tool_context
from tests.fake_http import FakeHttp, fixture_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send

KAKAO = "https://dapi.kakao.com/v2/local/search/keyword.json"
FORECAST = "https://api.open-meteo.com/v1/forecast"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """fixture의 예보 날짜(2026-11-20)가 언제 실행해도 '예보 가능한 범위'에 들도록 오늘을 고정한다."""
    monkeypatch.setattr(weather, "today_seoul", lambda: date(2026, 11, 17))


def recorded_routes(fake_http: FakeHttp) -> None:
    fake_http.route("GET", KAKAO, fixture_response("kakao_locate_seongsu.json"))
    fake_http.route("GET", FORECAST, fixture_response("open_meteo_forecast.json"))


async def get_weather(deps: AgentDeps, place: str = "성수동", day: str = "2026-11-20"):
    return await weather.get_weather(tool_context(deps, "get_weather"), place=place, date=day)


async def test_weather_summary_from_recorded_responses(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    recorded_routes(fake_http)

    result = await get_weather(tool_deps)

    assert result.ok is True
    assert payload(result)["notice"] == EXTERNAL_NOTICE
    content = payload(result)["content"]
    assert content["place"] == "성수동"
    assert content["matched_place"] == "서울숲카페거리"
    assert (content["min_c"], content["max_c"], content["precipitation_probability_max"]) == (-2.9, 2.6, 2)
    assert content["summary"] == "맑음"
    assert [h["time"] for h in content["hourly"]] == [
        "00:00",
        "03:00",
        "06:00",
        "09:00",
        "12:00",
        "15:00",
        "18:00",
        "21:00",
    ]
    assert "https://open-meteo.com/" in result.sources
    assert result.fetched_at is not None
    [kakao] = fake_http.sent("GET", KAKAO)
    assert kakao.headers["Authorization"] == "KakaoAK test-kakao-key"
    [forecast] = fake_http.sent("GET", FORECAST)
    assert forecast.url.params["timezone"] == "Asia/Seoul"
    assert forecast.url.params["start_date"] == "2026-11-20"
    assert (forecast.url.params["latitude"], forecast.url.params["longitude"]) == (
        "37.5464440372535",
        "127.043028992489",
    )


async def test_weather_timeout_is_failure_after_one_retry(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    recorded_routes(fake_http)
    fake_http.route("GET", FORECAST, httpx.ReadTimeout("느림"))

    result = await get_weather(tool_deps)

    assert result.ok is False
    assert "초 안에 오지 않았습니다" in (result.error or "")
    assert len(fake_http.sent("GET", FORECAST)) == 2  # 조회(GET)는 한 번만 다시 시도한다


async def test_weather_kakao_401_is_auth_failure(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", KAKAO, fixture_response("kakao_error_401.json", status=401))

    result = await get_weather(tool_deps)

    assert result.ok is False
    assert "카카오 인증에 실패했습니다" in (result.error or "")
    assert "test-kakao-key" not in (result.error or "")
    assert fake_http.sent("GET", FORECAST) == []


async def test_weather_429_is_rate_limit(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    recorded_routes(fake_http)
    fake_http.route("GET", FORECAST, fixture_response("open_meteo_error_429.json", status=429))

    result = await get_weather(tool_deps)

    assert result.ok is False
    assert "호출 한도를 넘었습니다" in (result.error or "")


async def test_weather_kakao_quota_400_minus_10_is_rate_limit(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    """카카오는 한도 초과를 429가 아니라 400 + code -10으로 알린다(공식 에러 코드 표)."""
    fake_http.route("GET", KAKAO, fixture_response("kakao_error_quota.json", status=400))

    result = await get_weather(tool_deps)

    assert result.ok is False
    assert "호출 한도를 넘었습니다" in (result.error or "")


async def test_weather_unknown_place_is_failure(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    fake_http.route("GET", KAKAO, fixture_response("kakao_keyword_empty.json"))

    result = await get_weather(tool_deps, place="없는동네")

    assert result.ok is False
    assert "'없는동네' 위치를 찾지 못했습니다" in (result.error or "")


async def test_weather_empty_forecast_is_failure(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    recorded_routes(fake_http)
    fake_http.route("GET", FORECAST, fixture_response("open_meteo_empty.json"))

    result = await get_weather(tool_deps)

    assert result.ok is False
    assert "예보를 받지 못했습니다" in (result.error or "")


@pytest.mark.parametrize("day", ["2026-11-16", "2026-12-03", "내일"])
async def test_weather_out_of_range_date_does_not_call_api(tool_deps: AgentDeps, fake_http: FakeHttp, day: str) -> None:
    result = await get_weather(tool_deps, day=day)

    assert result.ok is False
    assert fake_http.requests == []


async def test_weather_without_kakao_key_does_not_call_api(tool_deps: AgentDeps, fake_http: FakeHttp) -> None:
    tool_deps.settings.kakao_rest_api_key = type(tool_deps.settings.kakao_rest_api_key)("")

    result = await get_weather(tool_deps)

    assert result.ok is False
    assert "KAKAO_REST_API_KEY" in (result.error or "")
    assert fake_http.requests == []


async def test_weather_via_agent_records_one_read_step(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, fake_http: FakeHttp
) -> None:
    recorded_routes(fake_http)
    model = scripted(call("get_weather", place="성수동", date="2026-11-20"), text("맑고 춥습니다."))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "금요일 성수동 날씨 어때?")

    assert task["status"] == "done"
    [step] = task["steps"]
    assert step["tool_name"] == "get_weather"
    assert step["risk_level"] == "READ"
    assert (step["status"], step["ok"]) == ("done", True)
    assert step["label"] == "성수동 2026-11-20 날씨 확인하는 중"
