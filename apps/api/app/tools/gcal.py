"""Google Calendar 도구. list_events는 READ(외부 콘텐츠), create/update/delete_event는 EXTERNAL(매번 승인).

공식 문서(2026-11-17 확인, docs/TOOLS.md): https://developers.google.com/workspace/calendar/api/v3/reference/events
- GET    .../calendars/primary/events            list (timeMin, timeMax, singleEvents=true, orderBy=startTime)
- GET    .../calendars/primary/events/{eventId}  get (preview가 바뀌기 전 일정을 읽는다)
- POST   .../calendars/primary/events            insert (start, end 필수)
- PATCH  .../calendars/primary/events/{eventId}  patch (바꿀 칸만)
- DELETE .../calendars/primary/events/{eventId}  delete (성공하면 빈 본문)
- 범위: calendar.events.owned 하나로 위 다섯 가지가 모두 된다(app/google_auth.py).
- 오류: 401 authError(토큰 만료 → 갱신 후 한 번 다시), 403/429 rateLimitExceeded(한도), 404 notFound, 410 deleted.
- sendUpdates는 보내지 않는다(기본값: 참석자에게 알림 메일을 보내지 않음). 메일 발송은 금지 기능이다.

EXTERNAL 도구는 두 겹으로 막는다: external_tools toolset의 requires_approval=True
+ 함수 첫 줄 begin_execution(ctx, 인자)(2단계 보강: 실행 계획·승인·operation 검사).
끝나면 finish_execution이 영수증을 남긴다(app/agent/executor.py).
안정적인 event ID(생성 중복 방지)와 ETag 조건부 요청(대상 버전)은 4단계 보강에서 더한다.
쓰기 요청은 다시 시도하지 않는다(app/tools/http.py). 단, 401은 Google이 요청을 받아들이지 않은 것이라 새 토큰으로
한 번 다시 보내도 일정이 두 번 생기지 않는다.

preview(승인 카드와 감시 에이전트가 보는 문장): update/delete는 바뀌기 전 일정을 읽어 제목·시각을 보여 준다(명세).
일정 제목은 남이 보낸 초대일 수 있는 외부 문자열이라 줄바꿈과 「」를 지우고 40자로 잘라 「」 안에 넣는다.
감시 에이전트 지시문은 「」 안을 판단 대상 데이터로만 보라고 알린다(docs/SAFETY.md).
"""

import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import quote

from pydantic import Field
from pydantic_ai import RunContext

from app import google_auth
from app.agent.deps import AgentDeps
from app.agent.executor import Execution, begin_execution, finish_execution
from app.agent.plans import ExternalToolSpec
from app.agent.prompts import SEOUL
from app.agent.results import ToolResult, ToolStatus
from app.tools import http
from app.tools.http import ExternalApiError
from app.tools.places import clip

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SERVICE = "Google 캘린더"
TIME_ZONE = "Asia/Seoul"
MAX_RANGE_DAYS = 31
MAX_EVENTS = 50
WEEKDAYS = "월화수목금토일"
LOCAL_MINUTE = "%Y-%m-%dT%H:%M"
EXTERNAL_TEXT_LIMIT = 40
NOT_FOUND_MESSAGE = "해당 일정을 찾지 못했습니다. 이미 삭제되었을 수 있습니다."
FORBIDDEN_MESSAGE = (
    "Google 캘린더에서 이 작업을 할 권한이 없습니다. Calendar API 사용 설정과 연결할 때 허용한 권한을 확인해 주세요."
)
NOT_CONNECTED_WARNING = " (주의: Google 계정이 연결되어 있지 않아 승인해도 실패합니다. 설정에서 먼저 연결해 주세요.)"
CONTROL_OR_QUOTE = re.compile(r"[\x00-\x1f\x7f「」]")

DayText = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD (서울 기준)")]
MinuteText = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", description="서울 기준 YYYY-MM-DDTHH:MM")]
EventTitle = Annotated[str, Field(min_length=1, max_length=200, description="일정 제목")]
EventLocation = Annotated[str | None, Field(min_length=1, max_length=200, description="장소(선택)")]
EventMemo = Annotated[str | None, Field(min_length=1, max_length=2000, description="메모(선택)")]
EventId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,1024}$", description="list_events 결과의 id")]


# ---- Google 호출 ----


def translate(error: ExternalApiError) -> ExternalApiError:
    """404·410은 "일정을 찾지 못함", 한도가 아닌 403은 "권한 없음"으로 바꾼다."""
    if error.status in (404, 410):
        return ExternalApiError(NOT_FOUND_MESSAGE, error.status)
    if error.status == 403 and "한도" not in error.message:
        return ExternalApiError(FORBIDDEN_MESSAGE, error.status)
    return error


async def calendar_request(deps: AgentDeps, method: str, event_id: str = "", **kwargs: Any) -> Any:
    """캘린더 API를 부른다. 401이면 토큰을 갱신해 한 번만 다시 보낸다."""
    url = f"{EVENTS_URL}/{quote(event_id, safe='')}" if event_id else EVENTS_URL
    for attempt in range(2):
        token = await google_auth.access_token(deps, force_refresh=attempt > 0)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            return await http.request_json(deps.http, SERVICE, method, url, headers=headers, **kwargs)
        except ExternalApiError as error:
            if error.status != 401 or attempt > 0:
                translated = translate(error)
                if translated is error:
                    raise
                raise translated from error
    raise AssertionError("도달하지 않는다")


async def get_event(deps: AgentDeps, event_id: str) -> dict[str, Any]:
    """일정 하나. 지운 일정은 404가 아니라 200 + status 'cancelled'로 올 수 있어 찾지 못한 것으로 본다."""
    event = await calendar_request(deps, "GET", event_id)
    if not isinstance(event, dict) or event.get("status") == "cancelled":
        raise ExternalApiError(NOT_FOUND_MESSAGE, 404)
    return event


# ---- 시각 다루기 ----


def parse_local(text: str) -> datetime:
    try:
        return datetime.strptime(text, LOCAL_MINUTE)
    except ValueError as error:
        raise ExternalApiError("시각은 YYYY-MM-DDTHH:MM 형식이어야 합니다(서울 기준).") from error


def check_range(start: str, end: str) -> tuple[datetime, datetime]:
    start_at, end_at = parse_local(start), parse_local(end)
    if end_at <= start_at:
        raise ExternalApiError("끝나는 시각이 시작 시각보다 늦어야 합니다.")
    return start_at, end_at


def event_time(value: str) -> dict[str, str]:
    return {"dateTime": f"{value}:00", "timeZone": TIME_ZONE}


def day_label(day: date) -> str:
    return f"{day.month}월 {day.day}일({WEEKDAYS[day.weekday()]})"


def describe_range(start_at: datetime, end_at: datetime) -> str:
    """'11월 20일(금) 19:00~21:00'. 날짜가 다르면 끝 날짜도 적는다."""
    head = f"{day_label(start_at)} {start_at:%H:%M}"
    if end_at.date() == start_at.date():
        return f"{head}~{end_at:%H:%M}"
    return f"{head}~{day_label(end_at)} {end_at:%H:%M}"


def seoul_time(value: str) -> datetime:
    """Google의 dateTime(오프셋 포함)을 서울 시각으로. 오프셋이 없으면 서울 시각으로 본다."""
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(SEOUL) if parsed.tzinfo else parsed.replace(tzinfo=SEOUL)


def describe_event_time(event: dict[str, Any]) -> str:
    start, end = event.get("start") or {}, event.get("end") or {}
    if start.get("dateTime") and end.get("dateTime"):
        return describe_range(seoul_time(start["dateTime"]), seoul_time(end["dateTime"]))
    first_day = date.fromisoformat(start.get("date") or "1970-01-01")
    last_day = date.fromisoformat(end.get("date") or start.get("date") or "1970-01-01") - timedelta(days=1)
    if last_day <= first_day:
        return f"{day_label(first_day)} 종일"
    return f"{day_label(first_day)}~{day_label(last_day)} 종일"  # 종일 일정의 end.date는 다음 날(포함 안 함)


def local_minute(value: str | None) -> str:
    """기존 일정의 dateTime을 도구 입력 형식(서울 YYYY-MM-DDTHH:MM)으로. 종일 일정이면 실패."""
    if not value:
        raise ExternalApiError("종일 일정의 시간을 바꾸려면 시작과 끝을 모두 지정해 주세요.")
    return seoul_time(value).strftime(LOCAL_MINUTE)


# ---- 결과 모양 ----


def event_view(event: dict[str, Any]) -> dict[str, Any]:
    """모델에게 넘길 칸만 남긴다. 설명(초대 본문), 참석자 이메일은 넘기지 않는다."""
    start, end = event.get("start") or {}, event.get("end") or {}
    timed = bool(start.get("dateTime"))
    return {
        "id": str(event.get("id") or ""),
        "title": clip(event.get("summary") or "(제목 없음)"),
        "start": seoul_time(start["dateTime"]).isoformat() if timed else start.get("date"),
        "end": seoul_time(end["dateTime"]).isoformat() if end.get("dateTime") else end.get("date"),
        "all_day": not timed,
        "location": clip(event.get("location")) or None,
    }


def quote_external(text: object) -> str:
    """외부 문자열을 preview에 넣는 모양: 제어 문자·줄바꿈·「」를 지우고 40자로 잘라 「」로 감싼다."""
    cleaned = " ".join(CONTROL_OR_QUOTE.sub(" ", str(text or "(제목 없음)")).split())
    return f"「{clip(cleaned, EXTERNAL_TEXT_LIMIT)}」"


def describe_event(event: dict[str, Any]) -> str:
    return f"{quote_external(event.get('summary'))}({describe_event_time(event)})"


# ---- READ: list_events ----


def check_days(date_from: str, date_to: str) -> tuple[date, date]:
    try:
        first_day, last_day = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError as error:
        raise ExternalApiError("날짜는 YYYY-MM-DD 형식의 실제 날짜여야 합니다.") from error
    if last_day < first_day or (last_day - first_day).days > MAX_RANGE_DAYS:
        raise ExternalApiError(
            f"조회 기간은 시작일부터 {MAX_RANGE_DAYS}일 이내여야 하고, 끝 날짜가 시작 날짜보다 앞설 수 없습니다."
        )
    return first_day, last_day


def list_params(first_day: date, last_day: date) -> dict[str, Any]:
    return {
        # 서울 기준 시작일 0시 ~ 종료일 다음 날 0시. 종료일 하루 전체를 포함한다.
        "timeMin": f"{first_day.isoformat()}T00:00:00+09:00",
        "timeMax": f"{(last_day + timedelta(days=1)).isoformat()}T00:00:00+09:00",
        "singleEvents": "true",  # 반복 일정을 각 회차로 펼친다
        "orderBy": "startTime",
        "maxResults": MAX_EVENTS,
        "timeZone": TIME_ZONE,
    }


async def list_events(ctx: RunContext[AgentDeps], date_from: DayText, date_to: DayText) -> ToolResult:
    """Google 캘린더(기본 캘린더)의 일정을 기간으로 조회한다(서울 기준, 최대 31일).
    일정을 수정하거나 삭제하기 전에는 반드시 이것으로 대상의 id를 먼저 찾는다."""
    try:
        params = list_params(*check_days(date_from, date_to))
        data = await calendar_request(ctx.deps, "GET", params=params)
    except ExternalApiError as error:
        return ToolResult.from_api_error(error)
    items = data.get("items") if isinstance(data, dict) else None
    events = [e for e in items or [] if isinstance(e, dict) and e.get("status") != "cancelled"]
    content: dict[str, Any] = {"date_from": date_from, "date_to": date_to, "events": [event_view(e) for e in events]}
    if not events:
        content["message"] = "이 기간에는 일정이 없습니다."
    return ToolResult.external(content, sources=[str(e.get("htmlLink") or "") for e in events])


# ---- EXTERNAL: create_event ----


def saved_result(event: Any) -> ToolResult:
    view = event_view(event) if isinstance(event, dict) else {}
    link = str(event.get("htmlLink") or "") if isinstance(event, dict) else ""
    return ToolResult(
        status=ToolStatus.SUCCEEDED, data={**view, "saved_to": "Google 캘린더"}, sources=[link] if link else []
    )


async def create_event(
    ctx: RunContext[AgentDeps],
    title: EventTitle,
    start: MinuteText,
    end: MinuteText,
    location: EventLocation = None,
    description: EventMemo = None,
) -> ToolResult:
    """Google 캘린더(기본 캘린더)에 일정을 만든다. 사용자 승인 후에만 실행된다. 승인 전에는 만들었다고 말하지 않는다.
    시각은 서울 기준이다. 사용자가 끝 시각을 말하지 않았으면 시작 1시간 뒤로 넣는다."""
    args = {"title": title, "start": start, "end": end, "location": location, "description": description}
    started = await begin_execution(ctx, args)
    if isinstance(started, ToolResult):
        return started
    body: dict[str, Any] = {"summary": title, "start": event_time(start), "end": event_time(end)}
    body.update({k: v for k, v in (("location", location), ("description", description)) if v})
    try:
        check_range(start, end)
        event = await calendar_request(ctx.deps, "POST", json=body)
    except ExternalApiError as error:
        return await finished(ctx.deps, started, ToolResult.from_api_error(error))
    return await finished(ctx.deps, started, saved_result(event), event_id_of(event))


def event_id_of(event: Any) -> str | None:
    if not isinstance(event, dict):
        return None
    return str(event.get("id") or "") or None


async def finished(deps: AgentDeps, started: Execution, result: ToolResult, event_id: str | None = None) -> ToolResult:
    """실행 결과를 operation·영수증으로 기록한다(성공이면 영수증, 실패·불명이면 그 상태)."""
    return await finish_execution(deps, started, result, provider=google_auth.PROVIDER, resource_id=event_id)


async def connection_warning(deps: AgentDeps) -> str:
    return "" if await google_auth.is_connected(deps) else NOT_CONNECTED_WARNING


async def preview_create_event(deps: AgentDeps, args: dict[str, Any]) -> str:
    """'11월 20일(금) 19:00~21:00 '저녁 약속' 일정을 기본 캘린더에 만듭니다.'(명세 예)"""
    title, location = clip(args.get("title"), 100), args.get("location")
    try:
        when = describe_range(*check_range(str(args.get("start")), str(args.get("end"))))
    except ExternalApiError as error:
        return f"'{title}' 일정을 기본 캘린더에 만들려고 하지만 실행되지 않습니다: {error.message}"
    where = f" 장소: {clip(location, 100)}." if location else ""
    return f"{when} '{title}' 일정을 기본 캘린더에 만듭니다.{where}{await connection_warning(deps)}"


# ---- EXTERNAL: update_event ----


async def patch_body(deps: AgentDeps, event_id: str, changes: dict[str, str | None]) -> dict[str, Any]:
    """바꿀 칸만 담는다. 시작이나 끝 하나만 바꿔도 기존 값과 합쳐 순서를 확인한다."""
    body: dict[str, Any] = {
        key: changes[name]
        for key, name in (("summary", "title"), ("location", "location"), ("description", "description"))
        if changes.get(name)
    }
    start, end = changes.get("start"), changes.get("end")
    if start or end:
        current = (await get_event(deps, event_id)) if not (start and end) else {}
        new_start = start or local_minute((current.get("start") or {}).get("dateTime"))
        new_end = end or local_minute((current.get("end") or {}).get("dateTime"))
        check_range(new_start, new_end)
        body.update(start=event_time(new_start), end=event_time(new_end))
    if not body:
        raise ExternalApiError("바꿀 내용이 없습니다.")
    return body


async def update_event(
    ctx: RunContext[AgentDeps],
    event_id: EventId,
    title: EventTitle | None = None,
    start: MinuteText | None = None,
    end: MinuteText | None = None,
    location: EventLocation = None,
    description: EventMemo = None,
) -> ToolResult:
    """Google 캘린더 일정을 수정한다. event_id는 list_events로 찾는다. 바꿀 칸만 넣는다. 사용자 승인 후 실행."""
    changes = {"title": title, "start": start, "end": end, "location": location, "description": description}
    started = await begin_execution(ctx, {"event_id": event_id, **changes})
    if isinstance(started, ToolResult):
        return started
    try:
        body = await patch_body(ctx.deps, event_id, changes)
        event = await calendar_request(ctx.deps, "PATCH", event_id, json=body)
    except ExternalApiError as error:
        return await finished(ctx.deps, started, ToolResult.from_api_error(error))
    return await finished(ctx.deps, started, saved_result(event), event_id)


def describe_changes(before: dict[str, Any] | None, args: dict[str, Any]) -> str:
    """바뀌기 전과 후(명세). 이전 값을 못 읽었으면 바뀔 값만 적는다."""
    changes = []
    if args.get("title"):
        old = f"{quote_external(before.get('summary'))} → " if before else ""
        changes.append(f"제목: {old}'{clip(args['title'], 100)}'")
    if args.get("start") or args.get("end"):
        changes.append(f"시간: {time_change(before, args)}")
    if args.get("location"):
        old = f"{quote_external(before.get('location') or '없음')} → " if before else ""
        changes.append(f"장소: {old}'{clip(args['location'], 100)}'")
    if args.get("description"):
        changes.append("메모 내용 변경")
    return ", ".join(changes) or "바꿀 내용 없음"


def time_change(before: dict[str, Any] | None, args: dict[str, Any]) -> str:
    try:
        start = args.get("start") or local_minute(((before or {}).get("start") or {}).get("dateTime"))
        end = args.get("end") or local_minute(((before or {}).get("end") or {}).get("dateTime"))
        after = describe_range(*check_range(start, end))
    except ExternalApiError as error:
        after = f"(올바르지 않음: {error.message})"
    return f"{describe_event_time(before)} → {after}" if before else after


async def preview_update_event(deps: AgentDeps, args: dict[str, Any]) -> str:
    event_id = clip(args.get("event_id"), 40)
    try:
        before: dict[str, Any] | None = await get_event(deps, str(args.get("event_id")))
        head = f"{describe_event(before)} 일정을 수정합니다."
    except ExternalApiError as error:
        before, head = None, f"일정(ID {event_id})을 수정합니다. 일정 정보를 불러오지 못했습니다: {error.message}"
    return f"{head} {describe_changes(before, args)}."


# ---- EXTERNAL: delete_event ----


async def delete_event(ctx: RunContext[AgentDeps], event_id: EventId) -> ToolResult:
    """Google 캘린더 일정을 삭제한다. event_id는 list_events로 찾는다. 사용자 승인 후 실행."""
    started = await begin_execution(ctx, {"event_id": event_id})
    if isinstance(started, ToolResult):
        return started
    try:
        await calendar_request(ctx.deps, "DELETE", event_id)
    except ExternalApiError as error:
        return await finished(ctx.deps, started, ToolResult.from_api_error(error))
    result = ToolResult.success({"deleted_id": event_id, "deleted_from": "Google 캘린더"})
    return await finished(ctx.deps, started, result, event_id)


async def preview_delete_event(deps: AgentDeps, args: dict[str, Any]) -> str:
    """지워질 일정의 제목과 시각(명세). 다른 일정을 지우는 실수를 사람이 잡을 수 있게."""
    try:
        event = await get_event(deps, str(args.get("event_id")))
    except ExternalApiError as error:
        event_id = clip(args.get("event_id"), 40)
        return f"일정(ID {event_id})을 기본 캘린더에서 삭제합니다. 일정 정보를 불러오지 못했습니다: {error.message}"
    return f"{describe_event(event)} 일정을 기본 캘린더에서 삭제합니다."


# ---- 타임라인 문구 ----

LABELS = {
    "list_events": lambda args: f"캘린더 일정 확인하는 중 ({args.get('date_from', '?')}~{args.get('date_to', '?')})",
    "create_event": lambda args: f"캘린더에 '{clip(args.get('title'), 30)}' 일정 만드는 중",
    "update_event": lambda args: "캘린더 일정 수정하는 중",
    "delete_event": lambda args: "캘린더 일정 삭제하는 중",
}
# 실행 계획 규격(2단계 보강, app/agent/plans.py). 보내는 요청 모양이 바뀌면 version을 올린다.
CALENDAR_TARGET = "Google 캘린더(기본 캘린더)"
SPECS = {
    "create_event": ExternalToolSpec("1", provider=google_auth.PROVIDER, target_label=CALENDAR_TARGET),
    "update_event": ExternalToolSpec(
        "1", provider=google_auth.PROVIDER, target_arg="event_id", target_label=CALENDAR_TARGET
    ),
    "delete_event": ExternalToolSpec(
        "1", provider=google_auth.PROVIDER, target_arg="event_id", target_label=CALENDAR_TARGET
    ),
}
PREVIEWS = {
    "create_event": preview_create_event,
    "update_event": preview_update_event,
    "delete_event": preview_delete_event,
}
