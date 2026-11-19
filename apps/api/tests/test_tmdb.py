"""영화 비서 C 둘째 단계: TMDB 줄거리·평점 짝짓기(app/tools/tmdb.py) 테스트.

fixture는 2026-11-19 실제 TMDB 응답(now_playing KR 1쪽에서 3편, "폭설" 검색 4건). 실제 TMDB는 부르지 않는다.
"""

import pytest
from pydantic import SecretStr

from app.tools import tmdb
from app.tools.http import ExternalApiError
from app.tools.kobis import KobisMovie
from tests.fake_http import fixture_response, json_response


def movie(code: str, title: str, open_date: str) -> KobisMovie:
    return KobisMovie(code, title, open_date, 1, 0, 0, [], None, None, [], [], None)


HUNGER = movie("20267121", "헝거게임 50번째 추첨의 날", "2026-11-18")
SNOW = movie("20247221", "폭설", "2026-11-18")


def route_tmdb(fake_http) -> None:
    fake_http.route("GET", tmdb.NOW_PLAYING_URL, fixture_response("tmdb_now_playing_kr.json"))
    fake_http.route("GET", tmdb.SEARCH_URL, fixture_response("tmdb_search_movie.json"))


def test_normalize_title_ignores_spacing_and_marks():
    assert tmdb.normalize_title("헝거게임: 50번째 추첨의 날") == tmdb.normalize_title("헝거게임 50번째 추첨의 날")
    assert tmdb.normalize_title("Mean Girls") == tmdb.normalize_title("mean-girls")


async def test_matches_now_playing_by_title(tool_deps, fake_http):
    route_tmdb(fake_http)
    found, complete = await tmdb.match_movies(tool_deps, [HUNGER])
    assert complete is True
    assert found["20267121"].tmdb_id == 1300968
    assert found["20267121"].vote_average == pytest.approx(7.448)
    assert found["20267121"].vote_count == 29
    assert found["20267121"].overview
    assert fake_http.sent("GET", tmdb.SEARCH_URL) == []  # 목록에서 찾으면 검색하지 않는다


async def test_search_fallback_with_year(tool_deps, fake_http):
    route_tmdb(fake_http)
    found, _ = await tmdb.match_movies(tool_deps, [SNOW])
    assert found["20247221"].tmdb_id == 1244809  # 같은 제목 셋 중 개봉 연도가 1년 이내인 하나
    search = fake_http.sent("GET", tmdb.SEARCH_URL)[0]
    assert search.url.params["query"] == "폭설"
    assert search.url.params["language"] == "ko-KR"


async def test_search_ambiguous_skips(tool_deps, fake_http):
    route_tmdb(fake_http)
    # 2025년 개봉이라면 2024·2026 두 "폭설"이 모두 1년 이내 → 확실하지 않으니 붙이지 않는다
    found, _ = await tmdb.match_movies(tool_deps, [movie("x1", "폭설", "2025-06-01")])
    assert found == {}


async def test_search_year_far_skips(tool_deps, fake_http):
    route_tmdb(fake_http)
    assert (await tmdb.match_movies(tool_deps, [movie("x2", "폭설", "2030-01-01")]))[0] == {}


async def test_no_token_no_request(tool_deps, fake_http):
    tool_deps.settings.tmdb_read_token = SecretStr("")
    assert await tmdb.match_movies(tool_deps, [HUNGER, SNOW]) == ({}, True)  # 토큰이 없으면 완전한 결과로 본다
    assert fake_http.requests == []


async def test_bearer_header(tool_deps, fake_http):
    route_tmdb(fake_http)
    await tmdb.match_movies(tool_deps, [HUNGER])
    request = fake_http.sent("GET", tmdb.NOW_PLAYING_URL)[0]
    assert request.headers["Authorization"] == "Bearer test-tmdb-token"
    assert "test-tmdb-token" not in str(request.url)
    assert request.url.params["region"] == "KR"


async def test_unauthorized_raises(tool_deps, fake_http):
    fake_http.route("GET", tmdb.NOW_PLAYING_URL, json_response(401, {"status_code": 7}))
    with pytest.raises(ExternalApiError) as caught:
        await tmdb.match_movies(tool_deps, [HUNGER])
    assert caught.value.code == "unauthorized"


async def test_search_error_keeps_other_matches(tool_deps, fake_http):
    """검색 하나가 실패해도 이미 짝지은 영화는 남기고, 결과가 완전하지 않다고 알린다(최종 검토 Important 2)."""
    fake_http.route("GET", tmdb.NOW_PLAYING_URL, fixture_response("tmdb_now_playing_kr.json"))
    fake_http.route("GET", tmdb.SEARCH_URL, json_response(503, {}))
    found, complete = await tmdb.match_movies(tool_deps, [HUNGER, SNOW])
    assert set(found) == {"20267121"}
    assert complete is False


async def test_duplicate_kobis_titles_skip_matching(tool_deps, fake_http):
    """KOBIS 목록에 정규화 제목이 같은 영화가 둘이면(재개봉작과 신작) 어느 쪽 줄거리인지 몰라 둘 다 붙이지 않는다."""
    route_tmdb(fake_http)
    rerun = movie("19990001", "헝거게임: 50번째 추첨의 날", "1999-01-01")
    found, complete = await tmdb.match_movies(tool_deps, [HUNGER, rerun])
    assert found == {}
    assert complete is True
