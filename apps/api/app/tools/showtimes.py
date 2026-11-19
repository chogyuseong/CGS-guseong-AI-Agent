"""영화 비서 C 첫 단계: 붙여 넣은 시간표로 오늘 저녁에 들어가는 회차 고르기(compare_showtimes, READ, 채팅 전용).

공식 회차 API가 없다(2026-11-18 확인: KOBIS 오픈API는 박스오피스·영화·영화사·영화인 정보뿐, 극장 시간표·잔여 좌석 없음).
그래서 사용자가 붙여 넣은 시간표로 계산한다. 모델은 시간표를 회차 목록으로 옮기기만 하고, 계산·판정은 movie.py가 한다.
- 지어낸 회차 막기(2026-11-19 보강): 최근 사용자 메시지를 timetable.parse로 회차 목록(극장·영화·시작·종료·상영 시간)으로
  읽고, 모델이 옮긴 회차가 그중 한 회차와 모두 맞아야 한다(다른 줄의 시각·상영 시간, 문장 속 숫자는 근거가 아니다).
- 이동 시간: 사용자가 말한 값(to_theater_minutes·home_minutes)을 그대로 쓰고, 없는 구간만 TMAP으로 조회한다
  (극장마다 가는 길·귀가 한 번씩, tmap.LIMITER 하루 10건 공유). 지어낸 기본 이동 시간은 쓰지 않는다.
- 좌석은 확인할 수 없어 '좌석은 극장에서 확인'으로, 예매는 극장 브랜드의 공식 홈페이지 링크만
  (좌석 선택·결제는 사용자가 직접).

진입점: 모델(toolsets.travel_tools, 채팅 전용). 테스트: tests/test_showtimes.py, 계산은 tests/test_movie_plan.py.
근거: 생활 비서 명세 4절(요구사항 밖, 2026-11-18 사용자 결정 — 이동 시간은 말한 값 우선, 없는 구간만 TMAP),
docs/DECISIONS.md.

고칠 때 주의:
- 등록부: toolsets.travel_tools·TOOL_LABELS, data_policy.TOOL_POLICIES, results.EXTERNAL_CONTENT_TOOLS,
  apps/web lib/toolText.ts·components/ExternalContent.tsx(영화 카드)·StepTimeline CARD_TOOLS, prompts.SYSTEM_PROMPT.
- 결과 칸 이름(결론·이유·차선·차이·제외·계산·조건·이동·좌석·예매)은 화면 카드와 tests/test_showtimes.py가 쓴다.
- BOOKING_SITES는 공식 홈페이지 주소다. 바꾸면 테스트도 고친다.

변경 이력:
- 2026-11-18 처음 작성.
- 2026-11-19 시간표 검증을 숫자 포함 검사에서 회차 단위 대조로 바꿈(timetable.py, tests/test_showtime_paste.py).
  recent_user_messages 추가(recent_user_text는 같은 결과를 그대로 돌려줌 — jobprep이 씀).
"""

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from sqlalchemy import select

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.models import Message, Task
from app.models.enums import MessageRole
from app.tools import timetable, tmap
from app.tools.http import ExternalApiError
from app.tools.movie import Conditions, Evaluation, Legs, Showing, evaluate, pick, showing_difference

# 극장 브랜드 → 공식 홈페이지(예매는 사용자가 거기서 직접 한다). 이름에 브랜드가 없으면 링크를 만들지 않는다.
BOOKING_SITES = {
    "CGV": "https://www.cgv.co.kr",
    "롯데시네마": "https://www.lottecinema.co.kr",
    "메가박스": "https://www.megabox.co.kr",
}
# 지어낸 회차 검사에 쓰는 최근 사용자 메시지 수. 시간표를 붙여 넣고 몇 번 대화한 뒤 물어도 찾도록.
RECENT_USER_MESSAGES = 5
# 시간표 검증 실패 문구(2026-11-19). 모호한 항목은 timetable.showing_problem이 짧게 적는다.
PASTE_PREFIX = "붙여 넣은 시간표로 확정할 수 없는 회차가 있습니다: "
PASTE_HINT = "극장·영화·시각이 한 줄에 함께 있도록 다시 붙여 넣거나, 모호한 부분을 알려 주세요"
LIMIT_MESSAGE = (
    "TMAP 무료 조회(하루 10건)를 다 썼습니다. "
    "식당에서 극장까지, 극장에서 집까지 이동 시간을 알려 주시면 그 값으로 계산하겠습니다."
)


class ShowingIn(BaseModel):
    """붙여 넣은 시간표의 회차 하나(모델이 옮긴다). 시각은 서울 YYYY-MM-DDTHH:MM."""

    theater: str = Field(min_length=1, max_length=40, description="극장 이름. 예: 메가박스 코엑스")
    movie: str = Field(min_length=1, max_length=60)
    start: str = Field(description="시작 시각 YYYY-MM-DDTHH:MM")
    end: str | None = Field(default=None, description="시간표에 적힌 종료 시각(없으면 비움)")
    runtime_minutes: int | None = Field(default=None, ge=30, le=300, description="시간표에 적힌 상영 시간(분)")
    to_theater_minutes: int | None = Field(default=None, ge=0, le=180, description="사용자가 말한 식당→극장 이동(분)")
    home_minutes: int | None = Field(default=None, ge=0, le=240, description="사용자가 말한 극장→집 이동(분)")


PlaceName = Annotated[str, Field(min_length=1, max_length=50)]
LocalTime = Annotated[str, Field(description="서울 시각 YYYY-MM-DDTHH:MM")]


def booking_url(theater: str) -> str | None:
    return next((url for brand, url in BOOKING_SITES.items() if brand in theater), None)


async def recent_user_messages(deps: AgentDeps) -> list[str]:
    """이 작업의 대화방에서 최근 사용자 메시지들(새 것부터). 읽기만 한다. user_id 조건을 건다.

    2026-11-19: 시간표 검증(timetable.parse)이 메시지마다 극장·영화 문맥을 새로 시작하도록 메시지 단위로 돌려준다.
    """
    async with deps.db() as session:
        conversation_id = await session.scalar(
            select(Task.conversation_id).where(Task.id == deps.task_id, Task.user_id == deps.user_id)
        )
        rows = await session.scalars(
            select(Message.content)
            .where(Message.conversation_id == conversation_id, Message.role == MessageRole.USER)
            .order_by(Message.id.desc())
            .limit(RECENT_USER_MESSAGES)
        )
        return list(rows)


async def recent_user_text(deps: AgentDeps) -> str:
    """최근 사용자 메시지들을 줄바꿈으로 이은 글(새 것부터). 취업 준비(jobprep)가 공고 인용 검사에 쓴다.

    고칠 때 주의: 반환 모양(새 것부터, 줄바꿈으로 이음)을 바꾸면 jobprep 테스트를 같이 본다.
    """
    return "\n".join(await recent_user_messages(deps))


async def paste_problem(deps: AgentDeps, items: list[tuple[ShowingIn, Showing]]) -> str | None:
    """모델이 옮긴 회차마다 붙여 넣은 시간표의 한 회차와 극장·영화·시작·종료·상영 시간이 맞는지 본다(timetable).

    처음 맞지 않는 회차의 짧은 이유, 모두 맞으면 None. 읽기만 하고 외부 호출은 없다(경로 조회 전에 부른다).
    """
    theaters = [s.theater for _, s in items]
    movies = [s.movie for _, s in items]
    slots = timetable.parse(await recent_user_messages(deps), theaters, movies)
    for _, s in items:
        if problem := timetable.showing_problem(slots, s.theater, s.movie, s.start, s.end, s.runtime_minutes):
            return problem
    return None


def to_showing(item: ShowingIn) -> Showing | None:
    start, end = tmap.parse_local(item.start), tmap.parse_local(item.end) if item.end else None
    if start is None or (item.end and end is None):
        return None
    return Showing(item.theater.strip(), item.movie.strip(), start, end, item.runtime_minutes)


async def route_minutes(deps: AgentDeps, origin: str, destination: str, when: datetime) -> int:
    """가장 빠른 대중교통 경로 분(TMAP 1건). 장소가 모호하거나 없거나 경로가 없으면 ExternalApiError(설명)."""
    places = [await tmap.find_place(deps, origin), await tmap.find_place(deps, destination)]
    for name, place in zip((origin, destination), places, strict=True):
        if not isinstance(place, dict):
            raise ExternalApiError(f"'{name}' 위치를 하나로 정하지 못했습니다. 역이나 지점 이름으로 알려 주세요.")
    routes, no_route = tmap.parse_routes(await tmap.fetch_routes(deps, places[0], places[1], when))  # type: ignore[arg-type]
    if not routes:
        raise ExternalApiError(f"'{origin}'에서 '{destination}'까지 {no_route}")
    return min(r.minutes for r in routes)


async def legs_for(
    deps: AgentDeps, items: list[tuple[ShowingIn, Showing]], meet_place: str, home: str, cond: Conditions
):
    """극장별 이동 시간. 말한 값이 있으면 그것, 없으면 TMAP(극장마다 가는 길·귀가 한 번씩). (legs, TMAP을 썼는지)."""
    legs: dict[str, Legs] = {}
    used_tmap = False
    for theater in dict.fromkeys(s.theater for _, s in items):
        given = [i for i, s in items if s.theater == theater]
        to = next((i.to_theater_minutes for i in given if i.to_theater_minutes is not None), None)
        back = next((i.home_minutes for i in given if i.home_minutes is not None), None)
        if to is None:
            to = await route_minutes(deps, meet_place, theater, cond.meet_at + timedelta(minutes=cond.dinner_minutes))
            used_tmap = True
        if back is None:
            back = await route_minutes(deps, theater, home, cond.deadline - timedelta(minutes=60))
            used_tmap = True
        legs[theater] = Legs(to_theater=to, home=back)
    return legs, used_tmap


def row(e: Evaluation) -> dict[str, str]:
    end = f"{e.end:%H:%M}" + (" 예상" if e.end_estimated else "") if e.end else "알 수 없음"
    home = f"{e.home_arrival:%H:%M}" if e.home_arrival else "알 수 없음"
    return {"회차": e.showing.label, "식사": f"{e.dinner_available}분", "종료": end, "집 도착": home}


def content_of(evaluations: list[Evaluation], cond: Conditions, used_tmap: bool) -> dict[str, Any]:
    choice = pick(evaluations)
    content: dict[str, Any] = {"결론": choice.conclusion, "이유": choice.reason}
    if choice.best and choice.second:
        content["차선"] = choice.second.showing.label
        content["차이"] = showing_difference(choice.best, choice.second)
    content["제외"] = [{"회차": e.showing.label, "이유": " ".join(e.problems)} for e in choice.excluded]
    content["계산"] = [row(e) for e in evaluations]
    deadline = "집 도착" if cond.deadline_kind == "home" else "영화 종료"
    content["조건"] = (
        f"만남 {cond.meet_at:%H:%M} · 식사 {cond.dinner_minutes}분 · 입장 여유 {cond.entry_buffer}분 · "
        f"{deadline} {cond.deadline:%H:%M}까지"
    )
    content["이동"] = (
        "이동 시간은 TMAP 대중교통 예상(말씀하신 구간은 그 값)입니다. 실제 교통 상황에 따라 달라질 수 있습니다."
        if used_tmap
        else "이동 시간은 말씀하신 값입니다."
    )
    content["좌석"] = "좌석은 극장에서 확인해 주세요."
    content["예매"] = booking_url(choice.best.showing.theater) if choice.best else None
    return content


def conditions_of(
    meet_at: str, dinner_minutes: int, deadline: str, deadline_kind: Literal["home", "end"], entry_buffer: int
) -> Conditions | str:
    """조건 또는 거절 이유(문자열)."""
    meet, until = tmap.parse_local(meet_at), tmap.parse_local(deadline)
    if meet is None or until is None:
        return tmap.TIME_FORMAT_MESSAGE
    if until <= meet:
        return "마감 시각이 만남 시각보다 이릅니다. 시각을 확인해 주세요."
    return Conditions(meet, dinner_minutes, entry_buffer, until, deadline_kind)


async def compare_showtimes_result(
    deps: AgentDeps,
    *,
    meet_place: str,
    meet_at: str,
    dinner_minutes: int,
    home: str,
    deadline: str,
    deadline_kind: Literal["home", "end"],
    showings: list[ShowingIn],
    entry_buffer_minutes: int = 10,
) -> ToolResult:
    """도구 본문. 외부 호출 전에 시각·지어낸 회차를 거절한다. 예외를 밖으로 던지지 않는다."""
    cond = conditions_of(meet_at, dinner_minutes, deadline, deadline_kind, entry_buffer_minutes)
    if isinstance(cond, str):
        return ToolResult.failure(cond, error_code="invalid")
    pairs = [(item, to_showing(item)) for item in showings]
    if any(s is None for _, s in pairs):
        return ToolResult.failure(tmap.TIME_FORMAT_MESSAGE, error_code="invalid")
    items = [(item, s) for item, s in pairs if s is not None]
    if problem := await paste_problem(deps, items):
        # 추천·예매 링크를 만들지 않고 카카오·TMAP도 부르지 않는다(지어내거나 섞인 회차로 계산하지 않게).
        return ToolResult.failure(f"{PASTE_PREFIX}{problem}. {PASTE_HINT}", error_code="invalid")
    try:
        legs, used_tmap = await legs_for(deps, items, meet_place, home, cond)
    except ExternalApiError as error:
        if error.code == "rate_limited":
            return ToolResult.failure(LIMIT_MESSAGE, error_code="rate_limited")
        return ToolResult.from_api_error(error)
    evaluations = [evaluate(s, legs[s.theater], cond) for _, s in items]
    content = content_of(evaluations, cond, used_tmap)
    return ToolResult.external(content, [content["예매"]] if content["예매"] else [])


# 도구(READ). docstring은 모델에게 보이는 도구 설명이다. 계산은 코드가 한다(모델이 시각을 빼고 더하지 않는다).
async def compare_showtimes(
    ctx: RunContext[AgentDeps],
    meet_place: PlaceName,
    meet_at: LocalTime,
    dinner_minutes: Annotated[int, Field(ge=0, le=240, description="식사에 쓰고 싶은 분")],
    home: PlaceName,
    deadline: LocalTime,
    deadline_kind: Annotated[
        Literal["home", "end"], Field(description="home=그 시각까지 집 도착, end=그 시각까지 영화 종료")
    ],
    showings: Annotated[list[ShowingIn], Field(min_length=1, max_length=10)],
    entry_buffer_minutes: Annotated[int, Field(ge=0, le=60, description="상영 전 입장 여유(분)")] = 10,
) -> ToolResult:
    """사용자가 붙여 넣은 영화 시간표에서 만남·식사·귀가 조건에 가장 편한 회차를 고른다.
    showings에는 붙여 넣은 시간표에 적힌 회차만 그대로 옮긴다(없는 회차·종료 시각을 만들지 않는다).
    사용자가 이동 시간을 말했으면 to_theater_minutes·home_minutes에 넣는다. 결론·이유·제외 문장을 바꾸지 말고 전한다.
    시간표가 없으면 이 도구를 쓰지 말고 극장 시간표를 붙여 넣어 달라고 한다."""
    return await compare_showtimes_result(
        ctx.deps,
        meet_place=meet_place,
        meet_at=meet_at,
        dinner_minutes=dinner_minutes,
        home=home,
        deadline=deadline,
        deadline_kind=deadline_kind,
        showings=showings,
        entry_buffer_minutes=entry_buffer_minutes,
    )


def compare_showtimes_label(args: dict[str, Any]) -> str:
    return f"영화 회차 {len(args.get('showings') or [])}개 비교하는 중"
