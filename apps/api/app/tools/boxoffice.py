"""영화 비서 C 둘째 단계: 지금 극장 상영작 조회(list_now_showing)와 추천 카드(recommend_movies). READ, 채팅 전용.

설계: docs/superpowers/specs/2026-11-19-movie-recommend-design.md (사용자 결정: 범위 "지금 극장에서 뭘 볼지", 데이터 C안
KOBIS + TMDB). 첫 단계(회차 고르기, showtimes.compare_showtimes)는 그대로 두고 그 앞에 붙는다.

- list_now_showing: KOBIS 상위 10편의 사실 칸(kobis.py) + TMDB 줄거리·평점(tmdb.py)을 외부 콘텐츠로 돌려준다.
  KOBIS 키가 없으면 외부 요청 없이 안내. TMDB가 실패하거나 토큰이 없으면 KOBIS 정보만으로 성공(출처.TMDB=false).
  같은 기준일 결과는 프로세스 메모리에 둔다(CACHE, 재시작하면 비움). 하루 외부 호출을 약 13회로 묶는다
  (두 API 모두 하루 한도가 문서에 없다, 2026-11-19 확인).
- 줄거리는 OVERVIEW_CHARS로 자른다: 10편 결과 JSON이 results.MAX_EXTERNAL_CHARS(6,000자) 안에 들어야
  잘린 문자열이 아니라 구조로 남고, recommend_movies가 단계 기록에서 영화 목록을 다시 읽을 수 있다
  (test_ten_movies_fit_external_limit).

진입점: 모델(toolsets.travel_tools). 테스트: tests/test_boxoffice.py.

고칠 때 주의:
- 결과 칸 이름(기준일·영화·출처, 영화의 code·제목·개봉일·순위·누적 관객·장르·상영 시간·등급·감독·배우·
  줄거리·평점·평가 수)은
  recommend_movies(같은 파일)와 화면 apps/web ExternalContent(MovieRecommendCard), 테스트가 읽는다.
- 등록부: toolsets.travel_tools·TOOL_LABELS, data_policy.TOOL_POLICIES, results.EXTERNAL_CONTENT_TOOLS,
  apps/web lib/toolText.ts·StepTimeline CARD_TOOLS, prompts.SYSTEM_PROMPT(바꾸면 eval 재실행),
  tests/test_agent_run.py 도구 목록.

변경 이력:
- 2026-11-19 처음 작성.
"""

from datetime import date
from typing import Any

from pydantic_ai import RunContext

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.tools import kobis, tmdb
from app.tools.http import ExternalApiError
from app.tools.kobis import KobisMovie
from app.tools.tmdb import TmdbInfo

KOBIS_SOURCE = "https://www.kobis.or.kr"
TMDB_SOURCE = "https://www.themoviedb.org"
UNKNOWN = "정보 없음"
# 줄거리 최대 글자 수(넘으면 "…"). 10편이 6,000자 안에 들도록 고른 값(위 모듈 설명).
OVERVIEW_CHARS = 150
NO_KEY_MESSAGE = "설정에 KOBIS 키가 없어 상영작을 조회할 수 없습니다. .env에 KOBIS_API_KEY를 넣어 주세요."
# 기준일(요청한 날) → (내용, 출처). 최근 CACHE_DAYS개만 남긴다.
CACHE: dict[date, tuple[dict[str, Any], list[str]]] = {}
CACHE_DAYS = 2


def clear_cache() -> None:
    """테스트와 키 교체 뒤에 쓴다(다른 테스트의 조회 결과가 섞이지 않게)."""
    CACHE.clear()


def joined(items: list[str]) -> str:
    return ", ".join(items) if items else UNKNOWN


def clip(text: str) -> str:
    return text if len(text) <= OVERVIEW_CHARS else text[:OVERVIEW_CHARS] + "…"


def movie_row(movie: KobisMovie, info: TmdbInfo | None) -> dict[str, Any]:
    """카드·모델에 줄 영화 한 줄. 사실 칸은 KOBIS만, TMDB는 줄거리·평점·평가 수만(없으면 키를 뺀다)."""
    row: dict[str, Any] = {
        "code": movie.code,
        "제목": movie.title,
        "개봉일": movie.open_date or UNKNOWN,
        "순위": movie.rank,
        "누적 관객": f"{movie.audience_total:,}명",
        "장르": joined(movie.genres),
        "상영 시간": f"{movie.runtime_minutes}분" if movie.runtime_minutes else UNKNOWN,
        "등급": movie.grade or UNKNOWN,
        "감독": joined(movie.directors),
        "배우": joined(movie.actors),
    }
    if info is not None:
        if info.overview:
            row["줄거리"] = clip(info.overview)
        row["평점"] = f"{info.vote_average:.1f}"
        row["평가 수"] = info.vote_count
    return row


async def tmdb_matches(deps: AgentDeps, movies: list[KobisMovie]) -> tuple[dict[str, TmdbInfo], bool]:
    """(짝, TMDB를 썼는가). TMDB 오류는 삼키고 KOBIS만으로 계속한다 — 줄거리는 덤이고 사실은 KOBIS에 있다."""
    try:
        found = await tmdb.match_movies(deps, movies)
    except ExternalApiError:
        return {}, False
    return found, bool(found)


async def lookup_now_showing(deps: AgentDeps) -> ToolResult:
    """도구 본문. 예외를 밖으로 던지지 않는다. 같은 기준일이면 캐시를 쓴다(외부 요청 없음)."""
    if not deps.settings.kobis_api_key.get_secret_value().strip():
        return ToolResult.failure(NO_KEY_MESSAGE, error_code="not_configured")
    requested = kobis.target_day()
    if requested not in CACHE:
        try:
            day, movies = await kobis.fetch_now_showing(deps)
        except ExternalApiError as error:
            return ToolResult.from_api_error(error)
        found, used_tmdb = await tmdb_matches(deps, movies)
        content = {
            "기준일": day.isoformat(),
            "영화": [movie_row(m, found.get(m.code)) for m in movies],
            "출처": {"KOBIS": True, "TMDB": used_tmdb},
        }
        for old in sorted(CACHE)[: max(0, len(CACHE) - CACHE_DAYS + 1)]:
            del CACHE[old]
        CACHE[requested] = (content, [KOBIS_SOURCE, *([TMDB_SOURCE] if used_tmdb else [])])
    content, sources = CACHE[requested]
    return ToolResult.external(content, sources)


# 도구(READ). docstring은 모델에게 보이는 도구 설명이다.
async def list_now_showing(ctx: RunContext[AgentDeps]) -> ToolResult:
    """지금 한국 극장에서 상영 중인 영화(전날 박스오피스 상위 10편)를 조회한다.

    영화마다 code, 제목, 순위, 누적 관객, 장르, 상영 시간, 등급, 감독, 배우와 (있으면) 줄거리·평점을 돌려준다.
    영화를 추천하기 전에 먼저 부른다. 추천은 이 목록 안에서만 recommend_movies로 한다.
    """
    return await lookup_now_showing(ctx.deps)


def list_now_showing_label(args: dict[str, Any]) -> str:
    return "상영작 확인하는 중"
