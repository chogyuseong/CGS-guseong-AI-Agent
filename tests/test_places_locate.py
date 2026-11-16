"""4단계: 카카오 로컬로 장소 이름을 좌표로 바꾼다.

실제 카카오 응답(tests/fixtures/kakao_*.json)으로 테스트한다.
"""

import json
from pathlib import Path

import httpx
import pytest

from tools.http import ExternalAPIError

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text("utf-8"))


@pytest.fixture
def kakao(monkeypatch, settings):
    """카카오 서버 대신 저장해 둔 실제 응답을 돌려준다."""
    settings.KAKAO_REST_API_KEY = "test-kakao-key"
    requests = []

    def install(status, body):
        def handler(request):
            requests.append(request)
            return httpx.Response(status, json=body)

        monkeypatch.setattr(
            "tools.http.make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
        )
        return requests

    return install


def test_locate_uses_first_result_and_keeps_user_wording(kakao):
    from tools.places import locate

    sent = kakao(200, fixture("kakao_keyword_seongsu.json"))
    first = fixture("kakao_keyword_seongsu.json")["documents"][0]

    location = locate("성수동")

    assert location["name"] == "성수동"  # 사용자가 말한 이름을 그대로 쓴다
    assert location["matched_place"] == first["place_name"]  # 실제로 찾은 장소는 따로 남긴다
    assert location["latitude"] == float(first["y"])  # 카카오는 y가 위도
    assert location["longitude"] == float(first["x"])
    assert location["place_url"] == first["place_url"]
    assert sent[0].headers["Authorization"] == "KakaoAK test-kakao-key"
    assert sent[0].url.params["query"] == "성수동"


def test_locate_empty_result_is_a_clear_error(kakao):
    from tools.places import locate

    kakao(200, fixture("kakao_keyword_empty.json"))

    with pytest.raises(ExternalAPIError, match="없는동네"):
        locate("없는동네")


def test_disabled_kakaomap_service_is_reported(kakao):
    from tools.places import locate

    kakao(403, fixture("kakao_disabled_403.json"))

    with pytest.raises(ExternalAPIError, match="사용 설정"):
        locate("성수동")


def test_missing_key_fails_before_network(kakao, settings):
    from tools.places import locate

    sent = kakao(200, fixture("kakao_keyword_seongsu.json"))
    settings.KAKAO_REST_API_KEY = ""

    with pytest.raises(ExternalAPIError, match="KAKAO_REST_API_KEY"):
        locate("성수동")
    assert sent == []


def test_weather_end_to_end_with_real_fixtures(monkeypatch, settings):
    """카카오 좌표 변환 + Open-Meteo 예보를 실제 응답 파일로, 실제 호출 경로(tools.http) 그대로 확인한다."""
    from datetime import date

    from agent.registry import get_tool, run_tool

    settings.KAKAO_REST_API_KEY = "test-kakao-key"
    forecast = fixture("open_meteo_forecast.json")
    by_host = {"dapi.kakao.com": fixture("kakao_keyword_seongsu.json"), "api.open-meteo.com": forecast}
    seen_hosts = []

    def handler(request):
        seen_hosts.append(request.url.host)
        return httpx.Response(200, json=by_host[request.url.host])

    monkeypatch.setattr(
        "tools.http.make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    day = forecast["daily"]["time"][0]
    monkeypatch.setattr("tools.weather.timezone.localdate", lambda: date.fromisoformat(day))

    result = run_tool(get_tool("get_weather"), None, {"place": "성수동", "date": day})

    assert seen_hosts == ["dapi.kakao.com", "api.open-meteo.com"]  # 좌표 변환 → 예보 순서
    assert result["ok"] is True
    assert result["data"]["place"] == "성수동"
    assert result["data"]["matched_place"]
    assert any("place.map.kakao.com" in url for url in result["sources"])
    assert "https://open-meteo.com/" in result["sources"]
