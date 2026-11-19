"""영화 비서 C 둘째 단계: KOBIS 상영작에 TMDB의 줄거리·평점을 짝지어 붙인다.

TMDB에서는 줄거리(overview)·평점(vote_average)·평가 수(vote_count)만 쓴다. 장르·등급 같은 사실 칸은 KOBIS가 정본이다
(설계 docs/superpowers/specs/2026-11-19-movie-recommend-design.md "빌더 점검 뒤 정한 것").

짝짓기는 확실할 때만 한다 — 틀린 영화의 줄거리를 보여 주는 것보다 줄거리가 없는 편이 낫다.
1. now_playing(region=KR, ko-KR) 목록에서 정규화한 제목이 같은 것.
2. 없으면 검색(search/movie)에서 제목이 같고 개봉 연도가 KOBIS 개봉 연도와 1년 이내인 후보가 **하나뿐**일 때.
   (2026-11-19 실제 "폭설" 검색: 같은 제목이 2026·2024·2019년에 셋 — 연도로 가려야 한다.)

인증: "API Read Access Token"을 Bearer 헤더로 보낸다(주소에 키를 붙이지 않는다 — 로그·기록에 남지 않게).
토큰이 없으면 외부 요청 없이 빈 결과. TMDB 오류는 ExternalApiError로 던지고, 호출 쪽(boxoffice)이 KOBIS만으로 계속한다.
이용 조건(2026-11-19 확인): 비상업 무료, 출처 표기(로고 + 문구) 의무, 캐시 6개월 이내, AI 학습 금지(추론 입력은 해당 없음).

진입점: app/tools/boxoffice.py. 테스트: tests/test_tmdb.py(fixture는 2026-11-19 실제 응답).

고칠 때 주의: 짝짓기 규칙(연도 차 1년, 후보 하나)을 바꾸면 test_search_* 와 docs/TOOLS.md를 같이 고친다.

변경 이력:
- 2026-11-19 처음 작성.
"""

import re
from dataclasses import dataclass
from typing import Any

from app.agent.deps import AgentDeps
from app.tools.http import get_json
from app.tools.kobis import KobisMovie

SERVICE = "TMDB"
NOW_PLAYING_URL = "https://api.themoviedb.org/3/movie/now_playing"
SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
LANGUAGE = "ko-KR"
REGION = "KR"
# now_playing은 쪽당 20편. 상위 박스오피스 영화는 대개 앞쪽에 있어 2쪽까지만 본다(호출 수를 줄이려고).
MAX_PAGES = 2
# 같은 제목 검색 결과를 같은 영화로 볼 개봉 연도 차. 해외 개봉과 한국 개봉이 해를 넘기는 경우를 위해 1년.
YEAR_TOLERANCE = 1
TITLE_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass(frozen=True)
class TmdbInfo:
    tmdb_id: int
    overview: str
    vote_average: float
    vote_count: int


def normalize_title(title: str) -> str:
    """공백·문장부호를 빼고 소문자로. "헝거게임: 50번째"와 "헝거게임 50번째"를 같게 본다."""
    return TITLE_NOISE.sub("", title).lower()


def year_of(value: Any) -> int | None:
    text = str(value or "")
    return int(text[:4]) if text[:4].isdigit() else None


def to_info(item: dict[str, Any]) -> TmdbInfo:
    return TmdbInfo(
        tmdb_id=int(item.get("id") or 0),
        overview=str(item.get("overview") or "").strip(),
        vote_average=float(item.get("vote_average") or 0.0),
        vote_count=int(item.get("vote_count") or 0),
    )


def auth_headers(deps: AgentDeps) -> dict[str, str]:
    return {"Authorization": f"Bearer {deps.settings.tmdb_read_token.get_secret_value()}", "accept": "application/json"}


def results_of(body: Any) -> list[dict[str, Any]]:
    results = body.get("results") if isinstance(body, dict) else None
    return [item for item in results or [] if isinstance(item, dict)]


async def now_playing(deps: AgentDeps) -> list[dict[str, Any]]:
    """한국 상영작 1~2쪽. 첫 쪽의 total_pages가 1이면 한 번만 부른다."""
    items: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        params = {"region": REGION, "language": LANGUAGE, "page": str(page)}
        body = await get_json(deps.http, SERVICE, NOW_PLAYING_URL, params=params, headers=auth_headers(deps))
        items += results_of(body)
        if not isinstance(body, dict) or int(body.get("total_pages") or 1) <= page:
            break
    return items


async def search_one(deps: AgentDeps, movie: KobisMovie) -> TmdbInfo | None:
    """검색 결과에서 제목이 같고 개봉 연도가 가까운 후보가 하나뿐이면 그것. 아니면 None(확실하지 않음)."""
    open_year = year_of(movie.open_date)
    if open_year is None:
        return None
    params = {"query": movie.title, "language": LANGUAGE, "region": REGION}
    results = results_of(await get_json(deps.http, SERVICE, SEARCH_URL, params=params, headers=auth_headers(deps)))
    wanted = normalize_title(movie.title)
    candidates = [
        item
        for item in results
        if normalize_title(str(item.get("title") or "")) == wanted
        and (year := year_of(item.get("release_date"))) is not None
        and abs(year - open_year) <= YEAR_TOLERANCE
    ]
    return to_info(candidates[0]) if len(candidates) == 1 else None


async def match_movies(deps: AgentDeps, movies: list[KobisMovie]) -> dict[str, TmdbInfo]:
    """KOBIS 영화 코드 → TMDB 정보. 짝이 없는 영화는 빠진다. 토큰이 없으면 외부 요청 없이 {}.

    부수 효과: TMDB 호출(now_playing 1~2회 + 짝 없는 영화마다 검색 1회). 오류는 ExternalApiError로 던진다.
    """
    if not deps.settings.tmdb_read_token.get_secret_value().strip():
        return {}
    by_title: dict[str, list[dict[str, Any]]] = {}
    for item in await now_playing(deps):
        by_title.setdefault(normalize_title(str(item.get("title") or "")), []).append(item)
    found: dict[str, TmdbInfo] = {}
    for movie in movies:
        same = by_title.get(normalize_title(movie.title), [])
        info = to_info(same[0]) if len(same) == 1 else await search_one(deps, movie)
        if info is not None:
            found[movie.code] = info
    return found
