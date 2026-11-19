"""영화 비서 C 둘째 단계: KOBIS(영화관입장권통합전산망) 오픈API로 지금 극장 상영작의 사실 칸을 모은다.

KOBIS는 한국 극장 기준 공식 데이터다. 사실 칸(순위·개봉일·누적 관객·스크린 수·장르·상영 시간·관람 등급·감독·배우)의
정본은 여기뿐이고 TMDB 값으로 채우지 않는다
(설계 2026-11-19, docs/superpowers/specs/2026-11-19-movie-recommend-design.md).

- 일별 박스오피스(상위 10편, itemPerPage 최대 10) → 영화마다 상세 1회. 기준일은 서울 어제(오늘 집계는 아직 없다).
  어제 목록이 비면(새벽 집계 전) 그 전날로 한 번 더 조회한다.
- KOBIS는 키 오류도 HTTP 200 + {"faultInfo": {...}}로 돌려준다(2026-11-19 직접 확인, errorCode 320010). 그래서 본문을
  보고 ExternalApiError로 바꾼다. 메시지에 KOBIS 본문·키를 넣지 않는다.
- 상세 하나가 실패해도 그 영화는 박스오피스 칸만으로 남긴다(장르 등은 빈 값 → 화면은 "정보 없음").

진입점: app/tools/boxoffice.py의 list_now_showing.
테스트: tests/test_kobis.py(fixture는 2026-11-19 실제 응답을 줄인 것).

고칠 때 주의:
- KobisMovie 칸 이름은 boxoffice.movie_row가 화면·recommend_movies 칸으로 옮긴다. 바꾸면 그쪽과 테스트를 같이 고친다.
- 응답 칸 이름(movieInfoResult.movieInfo의 showTm·genres·audits·directors·actors)은 실제 응답으로 확인한 값이다.

변경 이력:
- 2026-11-19 처음 작성.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app import clock
from app.agent.deps import AgentDeps
from app.tools.http import ExternalApiError, get_json

SERVICE = "KOBIS"
BASE_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest"
BOXOFFICE_URL = f"{BASE_URL}/boxoffice/searchDailyBoxOfficeList.json"
MOVIE_INFO_URL = f"{BASE_URL}/movie/searchMovieInfo.json"
# KOBIS 키 오류 코드(2026-11-19 키 없이 호출해 확인). 다른 faultInfo는 일반 거절로 본다.
INVALID_KEY_CODES = frozenset({"320010"})
# 카드에 보일 감독·배우 수. 추천 이유에 쓸 만큼만(배우 전체 목록은 길다).
PEOPLE_LIMIT = 2


@dataclass(frozen=True)
class KobisMovie:
    """박스오피스 한 줄 + 상세. 상세가 없으면 genres·directors·actors는 빈 목록, runtime·grade·year는 None."""

    code: str
    title: str
    open_date: str
    rank: int
    audience_total: int
    screens: int
    genres: list[str]
    runtime_minutes: int | None
    grade: str | None
    directors: list[str]
    actors: list[str]
    production_year: str | None
    # 상세 조회가 성공했는가. 실패면 위 상세 칸이 비어 있고, boxoffice는 이 결과를 하루 캐시에 두지 않는다.
    detail_ok: bool = True


def target_day() -> date:
    """서울 기준 어제. 오늘 박스오피스는 다음 날 집계되므로 어제가 가장 최근 값이다."""
    return clock.now_utc().astimezone(clock.SEOUL).date() - timedelta(days=1)


def check_fault(body: Any) -> None:
    """KOBIS의 200 + faultInfo 오류를 ExternalApiError로 바꾼다. 키 오류면 unauthorized(설정 안내로 이어짐)."""
    if not isinstance(body, dict) or "faultInfo" not in body:
        return
    fault = body.get("faultInfo") or {}
    if str(fault.get("errorCode")) in INVALID_KEY_CODES:
        raise ExternalApiError(f"{SERVICE} 인증에 실패했습니다. API 키를 확인해 주세요.", code="unauthorized")
    raise ExternalApiError(f"{SERVICE} 요청이 거부되었습니다.")


async def fetch_day(deps: AgentDeps, day: date) -> list[dict[str, Any]]:
    """그날 박스오피스 목록(최대 10편). 오류는 ExternalApiError(get_json이 시간 초과·5xx를 한 번 다시 시도)."""
    params = {"key": deps.settings.kobis_api_key.get_secret_value(), "targetDt": f"{day:%Y%m%d}", "itemPerPage": "10"}
    body = await get_json(deps.http, SERVICE, BOXOFFICE_URL, params=params)
    check_fault(body)
    result = body.get("boxOfficeResult") if isinstance(body, dict) else None
    rows = result.get("dailyBoxOfficeList") if isinstance(result, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


async def fetch_info(deps: AgentDeps, code: str) -> dict[str, Any] | None:
    """영화 상세. 실패하면 None(그 영화는 박스오피스 칸만으로 남는다 — 상세 하나 때문에 목록 전체를 버리지 않는다)."""
    params = {"key": deps.settings.kobis_api_key.get_secret_value(), "movieCd": code}
    try:
        body = await get_json(deps.http, SERVICE, MOVIE_INFO_URL, params=params)
        check_fault(body)
    except ExternalApiError:
        return None
    info = (body.get("movieInfoResult") or {}).get("movieInfo") if isinstance(body, dict) else None
    return info if isinstance(info, dict) else None


def names(items: Any, key: str, limit: int | None = None) -> list[str]:
    """[{key: 이름}, ...]에서 빈 값을 뺀 이름 목록."""
    found = [str(item[key]).strip() for item in items or [] if isinstance(item, dict) and item.get(key)]
    return found[:limit] if limit else found


def to_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def parse_movie(row: dict[str, Any], info: dict[str, Any] | None) -> KobisMovie:
    """박스오피스 한 줄과 상세를 합친다. 숫자 칸이 비거나 이상하면 0·None으로 둔다(지어내지 않음)."""
    detail = info or {}
    grades = names(detail.get("audits"), "watchGradeNm")
    return KobisMovie(
        code=str(row.get("movieCd", "")),
        title=str(row.get("movieNm", "")).strip(),
        open_date=str(row.get("openDt", "")).strip(),
        rank=to_int(row.get("rank")) or 0,
        audience_total=to_int(row.get("audiAcc")) or 0,
        screens=to_int(row.get("scrnCnt")) or 0,
        genres=names(detail.get("genres"), "genreNm"),
        runtime_minutes=to_int(detail.get("showTm")) if detail.get("showTm") else None,
        grade=grades[0] if grades else None,
        directors=names(detail.get("directors"), "peopleNm", PEOPLE_LIMIT),
        actors=names(detail.get("actors"), "peopleNm", PEOPLE_LIMIT),
        production_year=str(detail["prdtYear"]) if detail.get("prdtYear") else None,
        detail_ok=info is not None,
    )


async def fetch_now_showing(deps: AgentDeps) -> tuple[date, list[KobisMovie]]:
    """(기준일, 상위 영화 목록). 어제가 비면 그 전날 한 번. 둘 다 비면 그 전날 기준일과 빈 목록.

    부수 효과: KOBIS 호출(박스오피스 1~2회 + 상세 최대 10회). DB는 건드리지 않는다.
    """
    day = target_day()
    rows = await fetch_day(deps, day)
    if not rows:
        day -= timedelta(days=1)
        rows = await fetch_day(deps, day)
    infos = await asyncio.gather(*(fetch_info(deps, str(row.get("movieCd", ""))) for row in rows))
    return day, [parse_movie(row, info) for row, info in zip(rows, infos, strict=True)]
