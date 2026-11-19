"""영화 비서 C 둘째 단계: KOBIS 박스오피스·영화 상세 조회(app/tools/kobis.py) 테스트.

fixture는 2026-11-19 실제 응답을 줄인 것(tests/fixtures/README.md). 실제 KOBIS는 부르지 않는다.
"""

from datetime import UTC, date, datetime

import httpx
import pytest
from pydantic import SecretStr

from app import clock
from app.tools import kobis
from app.tools.http import ExternalApiError
from tests.fake_http import fixture_response, json_response

EMPTY_DAY = {"boxOfficeResult": {"boxofficeType": "일별 박스오피스", "showRange": "", "dailyBoxOfficeList": []}}


@pytest.fixture
def kobis_key(test_settings, monkeypatch):
    test_settings.kobis_api_key = SecretStr("test-kobis-key")
    # 서울 2026-11-19 09:00 → 기준일은 어제 2026-11-18
    monkeypatch.setattr(clock, "now_utc", lambda: datetime(2026, 11, 19, 0, 0, tzinfo=UTC))


def route_kobis(fake_http, box=None, info=None) -> None:
    fake_http.route("GET", kobis.BOXOFFICE_URL, box or fixture_response("kobis_daily_boxoffice.json"))
    fake_http.route("GET", kobis.MOVIE_INFO_URL, info or fixture_response("kobis_movie_info.json"))


async def test_parses_boxoffice_and_info(tool_deps, fake_http, kobis_key):
    route_kobis(fake_http)
    day, movies = await kobis.fetch_now_showing(tool_deps)
    assert day == date(2026, 11, 18)
    assert [m.code for m in movies] == ["20247221", "20268264", "20267121"]
    first = movies[0]
    assert first.title == "폭설"
    assert first.rank == 1
    assert first.open_date == "2026-11-18"
    assert first.audience_total == 63697
    assert first.screens == 1039
    assert first.genres == ["미스터리", "드라마"]
    assert first.runtime_minutes == 88
    assert first.grade == "15세이상관람가"
    assert first.directors == ["박선우", "홍의정"]
    assert first.actors == ["김윤석", "구교환"]  # 상위 2명
    request = fake_http.sent("GET", kobis.BOXOFFICE_URL)[0]
    assert request.url.params["targetDt"] == "20261118"
    assert request.url.params["key"] == "test-kobis-key"


async def test_falls_back_to_previous_day(tool_deps, fake_http, kobis_key):
    route_kobis(fake_http)
    fake_http.route(
        "GET",
        kobis.BOXOFFICE_URL,
        json_response(200, EMPTY_DAY),
        fixture_response("kobis_daily_boxoffice.json"),
    )
    day, movies = await kobis.fetch_now_showing(tool_deps)
    sent = fake_http.sent("GET", kobis.BOXOFFICE_URL)
    assert [r.url.params["targetDt"] for r in sent] == ["20261118", "20261117"]
    assert day == date(2026, 11, 17)
    assert len(movies) == 3


async def test_fault_info_is_error(tool_deps, fake_http, kobis_key):
    fault = {"faultInfo": {"message": "유효하지않은 키값입니다.", "errorCode": "320010"}}
    route_kobis(fake_http, box=json_response(200, fault))
    with pytest.raises(ExternalApiError) as caught:
        await kobis.fetch_now_showing(tool_deps)
    assert caught.value.code == "unauthorized"
    assert "test-kobis-key" not in caught.value.message
    assert "KOBIS" in caught.value.message


async def test_info_failure_keeps_movie(tool_deps, fake_http, kobis_key):
    route_kobis(fake_http, info=json_response(404, {}))
    _, movies = await kobis.fetch_now_showing(tool_deps)
    assert len(movies) == 3
    assert movies[0].genres == []
    assert movies[0].runtime_minutes is None
    assert movies[0].grade is None
    assert movies[0].title == "폭설"  # 박스오피스 칸은 남는다
    assert movies[0].detail_ok is False


async def test_timeout_is_retryable(tool_deps, fake_http, kobis_key):
    route_kobis(fake_http, box=httpx.ReadTimeout("느림"))
    fake_http.route("GET", kobis.BOXOFFICE_URL, httpx.ReadTimeout("느림"))
    with pytest.raises(ExternalApiError) as caught:
        await kobis.fetch_now_showing(tool_deps)
    assert caught.value.code == "timeout"
