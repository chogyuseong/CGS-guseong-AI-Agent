"""영화 비서 C 둘째 단계: 상영작 조회 도구 list_now_showing(app/tools/boxoffice.py) 테스트.

KOBIS·TMDB는 가짜 HTTP와 2026-11-19 실제 응답 fixture로만 부른다.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from app import clock
from app.agent.results import MAX_EXTERNAL_CHARS
from app.tools import boxoffice, kobis, tmdb
from tests.conftest import tool_context
from tests.fake_http import fixture_json, fixture_response, json_response


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(clock, "now_utc", lambda: datetime(2026, 11, 19, 0, 0, tzinfo=UTC))
    boxoffice.clear_cache()
    yield
    boxoffice.clear_cache()


def route_all(fake_http, tmdb_reply=None) -> None:
    fake_http.route("GET", kobis.BOXOFFICE_URL, fixture_response("kobis_daily_boxoffice.json"))
    fake_http.route("GET", kobis.MOVIE_INFO_URL, fixture_response("kobis_movie_info.json"))
    fake_http.route("GET", tmdb.NOW_PLAYING_URL, tmdb_reply or fixture_response("tmdb_now_playing_kr.json"))
    fake_http.route("GET", tmdb.SEARCH_URL, fixture_response("tmdb_search_movie.json"))


async def lookup(deps):
    return await boxoffice.list_now_showing(tool_context(deps, "list_now_showing"))


def wrapped(result) -> dict:
    """ToolResult.external의 data(notice·content·truncated). data는 JSON 값 타입이라 dict인지 확인해 꺼낸다."""
    assert isinstance(result.data, dict)
    return result.data


def content_of(result) -> dict:
    content = wrapped(result)["content"]
    assert isinstance(content, dict)
    return content


async def test_no_kobis_key_guides_without_request(tool_deps, fake_http):
    tool_deps.settings.kobis_api_key = SecretStr("")
    result = await lookup(tool_deps)
    assert not result.ok
    assert result.error_code == "not_configured"
    assert "KOBIS_API_KEY" in (result.user_message or "")
    assert fake_http.requests == []


async def test_returns_wrapped_movies(tool_deps, fake_http):
    route_all(fake_http)
    result = await lookup(tool_deps)
    assert result.ok
    assert wrapped(result)["truncated"] is False
    content = content_of(result)
    assert content["기준일"] == "2026-11-18"
    assert content["출처"] == {"KOBIS": True, "TMDB": True}
    first, _, third = content["영화"]
    assert first["code"] == "20247221"
    assert first["제목"] == "폭설"
    assert first["순위"] == 1
    assert first["장르"] == "미스터리, 드라마"
    assert first["상영 시간"] == "88분"
    assert first["등급"] == "15세이상관람가"
    assert first["감독"] == "박선우, 홍의정"
    assert first["누적 관객"] == "63,697명"
    assert first["평점"] == "6.0"  # 검색으로 짝지은 2026년 "폭설"
    assert third["제목"] == "헝거게임 50번째 추첨의 날"
    assert third["평가 수"] == 29
    assert "https://www.kobis.or.kr" in result.sources
    assert "https://www.themoviedb.org" in result.sources


async def test_tmdb_failure_keeps_kobis(tool_deps, fake_http):
    route_all(fake_http, tmdb_reply=json_response(401, {"status_code": 7}))
    result = await lookup(tool_deps)
    assert result.ok
    content = content_of(result)
    assert content["출처"] == {"KOBIS": True, "TMDB": False}
    assert all("줄거리" not in m and "평점" not in m for m in content["영화"])
    assert "https://www.themoviedb.org" not in result.sources


async def test_missing_detail_shows_unknown(tool_deps, fake_http):
    route_all(fake_http)
    fake_http.route("GET", kobis.MOVIE_INFO_URL, json_response(404, {}))
    content = content_of(await lookup(tool_deps))
    assert content["영화"][0]["장르"] == "정보 없음"
    assert content["영화"][0]["상영 시간"] == "정보 없음"


async def test_ten_movies_fit_external_limit(tool_deps, fake_http):
    box = fixture_json("kobis_daily_boxoffice.json")
    row = box["boxOfficeResult"]["dailyBoxOfficeList"][0]
    box["boxOfficeResult"]["dailyBoxOfficeList"] = [
        {**row, "movieCd": f"2026{i:04d}", "movieNm": "아주 긴 영화 제목 " * 3 + str(i)} for i in range(10)
    ]
    info = fixture_json("kobis_movie_info.json")
    genres = [{"genreNm": "가족"}, {"genreNm": "어드벤처"}, {"genreNm": "판타지"}]
    info["movieInfoResult"]["movieInfo"]["genres"] = genres
    playing = fixture_json("tmdb_now_playing_kr.json")
    playing["results"] = [
        {**playing["results"][0], "id": i, "title": item["movieNm"], "overview": "줄거리" * 200}
        for i, item in enumerate(box["boxOfficeResult"]["dailyBoxOfficeList"])
    ]
    route_all(fake_http, tmdb_reply=json_response(200, playing))
    fake_http.route("GET", kobis.BOXOFFICE_URL, json_response(200, box))
    fake_http.route("GET", kobis.MOVIE_INFO_URL, json_response(200, info))
    result = await lookup(tool_deps)
    assert wrapped(result)["truncated"] is False
    assert len(content_of(result)["영화"]) == 10
    assert len(json.dumps(content_of(result), ensure_ascii=False)) <= MAX_EXTERNAL_CHARS


async def test_overview_clipped(tool_deps, fake_http):
    playing = fixture_json("tmdb_now_playing_kr.json")
    for item in playing["results"]:
        item["overview"] = "가" * 500
    route_all(fake_http, tmdb_reply=json_response(200, playing))
    movies = content_of(await lookup(tool_deps))["영화"]
    overview = movies[2]["줄거리"]
    assert len(overview) == boxoffice.OVERVIEW_CHARS + 1 and overview.endswith("…")


async def test_cache_avoids_second_request(tool_deps, fake_http):
    route_all(fake_http)
    await lookup(tool_deps)
    count = len(fake_http.requests)
    second = await lookup(tool_deps)
    assert second.ok
    assert len(fake_http.requests) == count


async def test_kobis_failure_retryable(tool_deps, fake_http):
    route_all(fake_http)
    fake_http.route("GET", kobis.BOXOFFICE_URL, httpx.ReadTimeout("느림"))
    result = await lookup(tool_deps)
    assert not result.ok
    assert result.retryable is True


def test_label() -> None:
    assert boxoffice.list_now_showing_label({}) == "상영작 확인하는 중"
