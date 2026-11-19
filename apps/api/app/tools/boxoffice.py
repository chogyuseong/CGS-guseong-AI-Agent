"""영화 비서 C 둘째 단계: 지금 극장 상영작 조회(list_now_showing)와 추천 카드(recommend_movies). READ, 채팅 전용.

설계: docs/superpowers/specs/2026-11-19-movie-recommend-design.md (사용자 결정: 범위 "지금 극장에서 뭘 볼지", 데이터 C안
KOBIS + TMDB). 첫 단계(회차 고르기, showtimes.compare_showtimes)는 그대로 두고 그 앞에 붙는다.

- list_now_showing: KOBIS 상위 10편의 사실 칸(kobis.py) + TMDB 줄거리·평점(tmdb.py)을 외부 콘텐츠로 돌려준다.
  KOBIS 키가 없으면 외부 요청 없이 안내. TMDB가 실패하거나 토큰이 없으면 KOBIS 정보만으로 성공(출처.TMDB=false).
  같은 기준일의 완전한 결과만 프로세스 메모리에 둔다(CACHE, 재시작하면 비움). 한 번 조회의 외부 호출은 최대
  24회(KOBIS 박스오피스 2 + 상세 10, TMDB now_playing 2 + 검색 10)이고, 캐시가 차면 그날은 다시 부르지 않는다
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

import json
from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from sqlalchemy import select

from app.agent.deps import AgentDeps
from app.agent.results import MAX_EXTERNAL_CHARS, ToolResult
from app.models import Step, Task
from app.models.enums import StepStatus
from app.tools import kobis, tmdb
from app.tools.http import ExternalApiError
from app.tools.kobis import KobisMovie
from app.tools.showtimes import BOOKING_SITES
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
    """테스트가 쓴다(다른 테스트의 조회 결과가 섞이지 않게). 운영에서는 API를 다시 시작하면 비워진다."""
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
        # 평가가 0건이면 평점 0.0은 "낮은 평점"이 아니라 "아직 평가 없음"이다. 오해를 막으려고 칸을 뺀다
        # (2026-11-19 실제 조회에서 개봉 직후 영화 두 편이 0.0으로 보였다).
        if info.vote_count > 0:
            row["평점"] = f"{info.vote_average:.1f}"
            row["평가 수"] = info.vote_count
    return row


async def tmdb_matches(deps: AgentDeps, movies: list[KobisMovie]) -> tuple[dict[str, TmdbInfo], bool]:
    """(짝, 완전한가). TMDB 오류는 삼키고 KOBIS만으로 계속한다 — 줄거리는 덤이고 사실은 KOBIS에 있다.

    TMDB 일시 오류는 완전하지 않은 결과다(다음 조회 때 다시 시도). 토큰이 없는 것은 오류가 아니라 완전하다.
    """
    try:
        return await tmdb.match_movies(deps, movies)
    except ExternalApiError:
        return {}, False


def fit_external(content: dict[str, Any]) -> dict[str, Any]:
    """6,000자(results.MAX_EXTERNAL_CHARS)를 넘으면 줄거리를 뺀다. 넘은 채로 두면 ToolResult.external이 잘린 문자열로
    바꿔 recommend_movies가 목록을 다시 읽지 못하고 "먼저 조회하라"는 엉뚱한 안내가 나간다(2026-11-19 최종 검토 Minor).
    사실 칸·평점은 짧아서 남긴다. 줄거리는 덤이다.
    """
    if len(json.dumps(content, ensure_ascii=False)) <= MAX_EXTERNAL_CHARS:
        return content
    movies = [{k: v for k, v in m.items() if k != "줄거리"} for m in content["영화"]]
    return {**content, "영화": movies}


def remember(requested: date, entry: tuple[dict[str, Any], list[str]]) -> None:
    """완전한 결과만 하루 캐시에 둔다. 최근 CACHE_DAYS개만 남긴다."""
    for old in sorted(CACHE)[: max(0, len(CACHE) - CACHE_DAYS + 1)]:
        del CACHE[old]
    CACHE[requested] = entry


async def lookup_now_showing(deps: AgentDeps) -> ToolResult:
    """도구 본문. 예외를 밖으로 던지지 않는다. 같은 기준일이면 캐시를 쓴다(외부 요청 없음)."""
    if not deps.settings.kobis_api_key.get_secret_value().strip():
        return ToolResult.failure(NO_KEY_MESSAGE, error_code="not_configured")
    requested = kobis.target_day()
    if requested in CACHE:
        return ToolResult.external(*CACHE[requested])
    try:
        day, movies = await kobis.fetch_now_showing(deps)
    except ExternalApiError as error:
        return ToolResult.from_api_error(error)
    found, tmdb_complete = await tmdb_matches(deps, movies)
    used_tmdb = bool(found)
    content = {
        "기준일": day.isoformat(),
        "영화": [movie_row(m, found.get(m.code)) for m in movies],
        "출처": {"KOBIS": True, "TMDB": used_tmdb},
    }
    entry = (fit_external(content), [KOBIS_SOURCE, *([TMDB_SOURCE] if used_tmdb else [])])
    # 캐시하지 않는 경우(2026-11-19 최종 검토): 새벽에 전날 목록으로 대신한 결과(곧 어제 집계가 나온다),
    # 상세 조회 실패·TMDB 일시 오류로 생긴 반쪽 결과. 하루 내내 옛 목록·빈 칸이 보이지 않게 다음 조회 때 다시 묻는다.
    if day == requested and tmdb_complete and all(m.detail_ok for m in movies):
        remember(requested, entry)
    return ToolResult.external(*entry)


# 도구(READ). docstring은 모델에게 보이는 도구 설명이다.
async def list_now_showing(ctx: RunContext[AgentDeps]) -> ToolResult:
    """지금 한국 극장에서 상영 중인 영화(전날 박스오피스 상위 10편)를 조회한다.

    영화마다 code, 제목, 순위, 누적 관객, 장르, 상영 시간, 등급, 감독, 배우와 (있으면) 줄거리·평점을 돌려준다.
    영화를 추천하기 전에 먼저 부른다. 추천은 이 목록 안에서만 recommend_movies로 한다.
    """
    return await lookup_now_showing(ctx.deps)


def list_now_showing_label(args: dict[str, Any]) -> str:
    return "상영작 확인하는 중"


# ---- recommend_movies ---------------------------------------------------------------------------------------------

# TMDB 이용 조건의 출처 표기 문구(2026-11-19 확인: "uses TMDB and the TMDB APIs but is not endorsed, certified, or
# otherwise approved by TMDB"). 화면은 이 문구와 TMDB 로고를 함께 보인다. 바꾸면 화면 테스트도 고친다.
TMDB_NOTICE = "이 서비스는 TMDB API를 사용하지만 TMDB의 보증이나 인증을 받지 않았습니다."
NEXT_STEP = "보고 싶은 영화의 시간표를 붙여 주시면 회차를 골라 드릴게요."
NEED_LOOKUP = "먼저 상영작을 조회해야 합니다. list_now_showing을 부른 뒤 그 목록에서 골라 주세요."
# 카드 사실 칸(정본은 단계 기록의 KOBIS 값, 평점만 TMDB). 줄거리는 넣지 않는다 — 외부 글을 카드로 다시 내보내지 않는다.
FACT_KEYS = ("장르", "상영 시간", "등급", "감독", "배우", "누적 관객", "평점")
# 예매는 극장 브랜드 공식 홈페이지 링크만(회차 고르기와 같은 목록, showtimes.BOOKING_SITES).
BOOKING_LINKS = tuple({"극장": name, "주소": url} for name, url in BOOKING_SITES.items())


class Pick(BaseModel):
    """모델이 고른 영화 하나: list_now_showing 결과의 code와 추천 이유."""

    code: str = Field(min_length=1, max_length=20, description="list_now_showing 결과의 code")
    reason: str = Field(min_length=1, max_length=120, description="이 사용자에게 추천하는 이유(취향·조건에 맞춰)")


async def last_lookup(deps: AgentDeps) -> dict[str, Any] | None:
    """이번 작업에서 마지막으로 성공한 list_now_showing의 결과 내용(기준일·영화·출처). 없거나 구조가 아니면 None.

    기준을 메모리 캐시가 아니라 단계 기록으로 둔 이유: 사용자가 본 목록과 같고, 재시작·복구 뒤에도 남고,
    외부 요청이 없다.
    불변 조건: Task.user_id 조건으로 남의 작업 기록을 읽지 않는다. 읽기만 한다.
    """
    async with deps.db() as session:
        output = await session.scalar(
            select(Step.output)
            .join(Task, Task.id == Step.task_id)
            .where(
                Step.task_id == deps.task_id,
                Task.user_id == deps.user_id,
                Step.tool_name == "list_now_showing",
                Step.status == StepStatus.DONE,
            )
            .order_by(Step.order.desc())
            .limit(1)
        )
    data = output.get("data") if isinstance(output, dict) else None
    content = data.get("content") if isinstance(data, dict) else None
    return content if isinstance(content, dict) and isinstance(content.get("영화"), list) else None


def pick_problem(picks: list[Pick], movies: dict[str, dict[str, Any]]) -> str | None:
    """목록 밖 code·중복 code를 거절하는 문장. 모델이 목록에서 다시 고르게 어느 code인지 알려 준다."""
    codes = [p.code for p in picks]
    if len(set(codes)) != len(codes):
        return "같은 영화를 두 번 고르셨습니다. 서로 다른 영화로 골라 주세요."
    missing = [code for code in codes if code not in movies]
    if missing:
        return f"조회한 상영작 목록에 없는 code입니다: {', '.join(missing)}. 목록 안에서 골라 주세요."
    return None


def recommend_card(lookup: dict[str, Any], picks: list[Pick], taste: str | None) -> dict[str, Any]:
    """카드 내용. 사실 칸은 단계 기록에서 채운다(모델 인자에는 code와 이유뿐)."""
    movies = {str(m.get("code")): m for m in lookup["영화"] if isinstance(m, dict)}
    rows = [
        {"제목": movies[p.code].get("제목", ""), "이유": p.reason}
        | {key: movies[p.code].get(key, UNKNOWN) for key in FACT_KEYS}
        for p in picks
    ]
    card: dict[str, Any] = {
        "결론": f"{rows[0]['제목']} 추천",
        "기준일": lookup.get("기준일"),
        "추천": rows,
        "다음": NEXT_STEP,
        "예매": list(BOOKING_LINKS),
    }
    if taste:
        card["조건"] = taste
    if (lookup.get("출처") or {}).get("TMDB"):
        card["출처 표기"] = TMDB_NOTICE
    return card


async def recommend_result(deps: AgentDeps, picks: list[Pick], taste: str | None) -> ToolResult:
    """도구 본문. 외부 요청 없음. 예외를 밖으로 던지지 않는다."""
    lookup = await last_lookup(deps)
    if lookup is None:
        return ToolResult.failure(NEED_LOOKUP, error_code="invalid")
    movies = {str(m.get("code")): m for m in lookup["영화"] if isinstance(m, dict)}
    if problem := pick_problem(picks, movies):
        return ToolResult.failure(problem, error_code="invalid")
    return ToolResult.success(recommend_card(lookup, picks, taste))


# 도구(READ). docstring은 모델에게 보이는 도구 설명이다.
async def recommend_movies(
    ctx: RunContext[AgentDeps],
    picks: Annotated[list[Pick], Field(min_length=1, max_length=3, description="추천할 영화 1~3편, 좋은 순서대로")],
    taste: Annotated[str | None, Field(max_length=60, description="사용자가 말한 조건 요약(예: 친구와 가볍게)")] = None,
) -> ToolResult:
    """list_now_showing으로 조회한 상영작 중 1~3편을 이유와 함께 추천 카드로 보여 준다.

    code는 반드시 이번 list_now_showing 결과에서 고른다. 장르·상영 시간·등급·평점은 서버가 채우므로 이유에는
    이 사용자의 취향(기억)과 이번 조건에 왜 맞는지를 쓴다.
    회차는 사용자가 시간표를 붙여 넣으면 compare_showtimes로 고른다.
    """
    return await recommend_result(ctx.deps, picks, taste)


def recommend_movies_label(args: dict[str, Any]) -> str:
    return "영화 추천 정리하는 중"
