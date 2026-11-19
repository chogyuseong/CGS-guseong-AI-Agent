"""영화 비서 C 둘째 단계: 채팅 한 번에 list_now_showing → recommend_movies가 이어지는 전체 흐름.

FunctionModel 대본(scripted)으로 모델을 대신한다. 실제 모델·KOBIS·TMDB는 부르지 않는다(가짜 HTTP + 실제 응답 fixture).
StepTracking이 남긴 실제 단계 기록을 recommend_movies(last_lookup)가 읽는지 확인한다.
"""

from datetime import UTC, datetime

import pytest

from app import clock
from app.agent.prompts import SYSTEM_PROMPT
from app.tools import boxoffice, kobis, tmdb
from tests.fake_http import fixture_response
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send


@pytest.fixture
def movie_api(fake_http, monkeypatch):
    monkeypatch.setattr(clock, "now_utc", lambda: datetime(2026, 11, 19, 0, 0, tzinfo=UTC))
    boxoffice.clear_cache()
    fake_http.route("GET", kobis.BOXOFFICE_URL, fixture_response("kobis_daily_boxoffice.json"))
    fake_http.route("GET", kobis.MOVIE_INFO_URL, fixture_response("kobis_movie_info.json"))
    fake_http.route("GET", tmdb.NOW_PLAYING_URL, fixture_response("tmdb_now_playing_kr.json"))
    fake_http.route("GET", tmdb.SEARCH_URL, fixture_response("tmdb_search_movie.json"))
    yield fake_http
    boxoffice.clear_cache()


async def test_lookup_then_recommend_card(client, agent, conversation, movie_api):
    model = scripted(
        call("list_now_showing"),
        call("recommend_movies", picks=[{"code": "20247221", "reason": "조용한 긴장감을 좋아하시면 맞아요"}]),
        text("폭설을 추천합니다."),
    )
    with agent.override(model=model.model()):
        task = await send(client, conversation, "이번 주말에 볼 영화 추천해 줘")

    lookup, pick = task["steps"]
    assert lookup["ok"] and lookup["label"] == "상영작 확인하는 중"
    assert pick["ok"] and pick["label"] == "영화 추천 정리하는 중"
    listed = lookup["output"]["data"]["content"]["영화"][0]
    card = pick["output"]["data"]
    assert card["결론"] == "폭설 추천"
    assert card["추천"][0]["장르"] == listed["장르"] == "미스터리, 드라마"
    assert card["추천"][0]["평점"] == listed["평점"]
    assert card["출처 표기"] == boxoffice.TMDB_NOTICE


async def test_recommend_outside_list_is_refused(client, agent, conversation, movie_api):
    model = scripted(
        call("list_now_showing"),
        call("recommend_movies", picks=[{"code": "00000000", "reason": "지어낸 영화"}]),
        text("다시 고르겠습니다."),
    )
    with agent.override(model=model.model()):
        task = await send(client, conversation, "영화 추천해 줘")

    _, pick = task["steps"]
    assert not pick["ok"]
    assert "00000000" in pick["output"]["user_message"]


def test_prompt_routes_movie_recommendation() -> None:
    assert "list_now_showing" in SYSTEM_PROMPT
    assert "recommend_movies" in SYSTEM_PROMPT
