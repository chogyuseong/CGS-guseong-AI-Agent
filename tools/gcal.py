"""
Google Calendar 도구. list_events는 READ, create/update/delete_event는 EXTERNAL(매번 승인).

공식 문서(2026-11-16 확인): https://developers.google.com/workspace/calendar/api/v3/reference/events
- GET    .../calendars/primary/events                (list: timeMin, timeMax, singleEvents, orderBy=startTime)
- POST   .../calendars/primary/events                (insert: start, end 필수)
- PATCH  .../calendars/primary/events/{eventId}      (patch)
- DELETE .../calendars/primary/events/{eventId}      (delete: 성공하면 빈 본문)
- 위 네 가지 모두 범위 https://www.googleapis.com/auth/calendar.events 하나로 된다.
- list 응답의 summary가 캘린더 이름이다. preview에 캘린더 이름을 넣을 때 쓴다.

google-api-python-client 대신 tools/http로 REST를 직접 부른다(DECISIONS.md 참고).
EXTERNAL 도구의 run은 approvals.execute_approved를 거쳐서만 실행된다.
"""

from datetime import date, datetime, timedelta

from agent.models import RiskLevel
from agent.registry import Tool, error_result, ok_result, register
from tools import http
from tools.http import ExternalAPIError

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SERVICE = "Google 캘린더"
TIME_ZONE = "Asia/Seoul"
MAX_RANGE_DAYS = 31
MAX_EVENTS = 50
WEEKDAYS = "월화수목금토일"
LOCAL_MINUTE = "%Y-%m-%dT%H:%M"  # 도구 입력 형식: 서울 기준 'YYYY-MM-DDTHH:MM'
DATETIME_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
NOT_FOUND = "해당 일정을 찾지 못했습니다. 이미 삭제되었을 수 있습니다."


# ── 공통 ─────────────────────────────────────────────────


def _call(user, method, url, **kwargs):
    from accounts import google  # accounts 앱이 tools보다 먼저 로드되지 않을 수 있어 여기서 불러온다

    headers = {"Authorization": f"Bearer {google.access_token(user)}"}
    try:
        return http.request_json(SERVICE, method, url, headers=headers, **kwargs)
    except ExternalAPIError as error:
        if error.status in (404, 410):
            raise ExternalAPIError(NOT_FOUND, error.status) from error
        raise


def _get_event(user, event_id):
    """일정 하나를 가져온다. 실제로 확인한 동작: 지운 일정은 404가 아니라 200 + status 'cancelled'로 온다.
    그대로 쓰면 이미 지운 일정에 '삭제합니다' 승인 카드가 뜨므로 찾지 못한 것으로 처리한다.
    """
    event = _call(user, "GET", f"{EVENTS_URL}/{event_id}")
    if event.get("status") == "cancelled":
        raise ExternalAPIError(NOT_FOUND, 404)
    return event


def _parse_local(text):
    try:
        return datetime.strptime(text, LOCAL_MINUTE)
    except (TypeError, ValueError) as error:
        raise ExternalAPIError("시각은 YYYY-MM-DDTHH:MM 형식이어야 합니다(서울 기준).") from error


def _check_range(start, end):
    """시작과 끝을 확인한다. 끝이 시작보다 빠르면 실행 전에 막는다."""
    start_at, end_at = _parse_local(start), _parse_local(end)
    if end_at <= start_at:
        raise ExternalAPIError("끝나는 시각이 시작 시각보다 늦어야 합니다.")
    return start_at, end_at


def _event_time(value):
    return {"dateTime": f"{value}:00", "timeZone": TIME_ZONE}


def _describe_time(start_at, end_at):
    """'11월 20일(금) 19:00~21:00'. 날짜가 다르면 끝 날짜도 적는다."""
    head = f"{start_at.month}월 {start_at.day}일({WEEKDAYS[start_at.weekday()]}) {start_at:%H:%M}"
    if end_at.date() == start_at.date():
        return f"{head}~{end_at:%H:%M}"
    return f"{head}~{end_at.month}월 {end_at.day}일({WEEKDAYS[end_at.weekday()]}) {end_at:%H:%M}"


def _event_summary(event):
    """구글 일정 하나를 LLM과 화면에 필요한 필드만 남긴다."""
    start, end = event.get("start", {}), event.get("end", {})
    all_day = "date" in start and "dateTime" not in start
    return {
        "id": event.get("id"),
        "title": event.get("summary", "(제목 없음)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "all_day": all_day,
        "location": event.get("location"),
        "link": event.get("htmlLink"),
    }


def _describe_event_time(event):
    """기존 일정의 시각을 사람이 읽는 문구로. 종일 일정과 시간 일정을 모두 다룬다."""
    summary = _event_summary(event)
    if summary["all_day"]:
        day = date.fromisoformat(summary["start"])
        return f"{day.month}월 {day.day}일({WEEKDAYS[day.weekday()]}) 종일"
    start_at = datetime.fromisoformat(summary["start"]).replace(tzinfo=None)
    end_at = datetime.fromisoformat(summary["end"]).replace(tzinfo=None)
    return _describe_time(start_at, end_at)


def _describe_event(event):
    return f"'{event.get('summary', '(제목 없음)')}' ({_describe_event_time(event)})"


def _calendar_name(user):
    """기본 캘린더 이름. 이름을 못 가져와도 preview는 만들어야 하므로 기본 문구로 대신한다."""
    try:
        return _call(user, "GET", EVENTS_URL, params={"maxResults": 1}).get("summary") or "기본 캘린더"
    except ExternalAPIError:
        return "기본 캘린더"


# ── READ: list_events ────────────────────────────────────


def list_events(user, date_from, date_to):
    try:
        start_day, end_day = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (TypeError, ValueError):
        return error_result("날짜는 YYYY-MM-DD 형식이어야 합니다.")
    if end_day < start_day or (end_day - start_day).days > MAX_RANGE_DAYS:
        return error_result(f"조회 기간은 시작일부터 {MAX_RANGE_DAYS}일 이내여야 합니다.")
    params = {
        # 서울 기준 시작일 0시 ~ 종료일 다음 날 0시. 종료일 하루 전체를 포함한다.
        "timeMin": f"{start_day.isoformat()}T00:00:00+09:00",
        "timeMax": f"{(end_day + timedelta(days=1)).isoformat()}T00:00:00+09:00",
        "singleEvents": "true",  # 반복 일정을 각 회차로 펼친다
        "orderBy": "startTime",
        "maxResults": MAX_EVENTS,
        "timeZone": TIME_ZONE,
    }
    try:
        data = _call(user, "GET", EVENTS_URL, params=params)
    except ExternalAPIError as error:
        return error_result(str(error))
    events = [_event_summary(item) for item in data.get("items", [])]
    # 일정이 없는 것은 실패가 아니라 정상 답이다("그날은 일정이 없습니다").
    payload = {"calendar": data.get("summary"), "date_from": date_from, "date_to": date_to, "events": events}
    return ok_result(payload, sources=[e["link"] for e in events if e["link"]])


# ── EXTERNAL: create_event ───────────────────────────────


def create_event(user, title, start, end, location=None, description=None):
    try:
        _check_range(start, end)
        body = {"summary": title, "start": _event_time(start), "end": _event_time(end)}
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        event = _call(user, "POST", EVENTS_URL, json=body)
    except ExternalAPIError as error:
        return error_result(str(error))
    created = _event_summary(event)
    return ok_result(
        {**created, "saved_to": "Google 캘린더"}, sources=[created["link"]] if created["link"] else []
    )


def preview_create(user, title, start, end, location=None, description=None):
    start_at, end_at = _check_range(start, end)
    where = f" 장소: {location}." if location else ""
    return f"{_describe_time(start_at, end_at)} '{title}' 일정을 {_calendar_name(user)}에 만듭니다.{where}"


# ── EXTERNAL: update_event ───────────────────────────────


def update_event(user, event_id, title=None, start=None, end=None, location=None, description=None):
    try:
        body = _patch_body(user, event_id, title, start, end, location, description)
        event = _call(user, "PATCH", f"{EVENTS_URL}/{event_id}", json=body)
    except ExternalAPIError as error:
        return error_result(str(error))
    updated = _event_summary(event)
    return ok_result(
        {**updated, "saved_to": "Google 캘린더"}, sources=[updated["link"]] if updated["link"] else []
    )


def _patch_body(user, event_id, title, start, end, location, description):
    """바꿀 필드만 담는다. 시작이나 끝 하나만 바꿔도 기존 값과 합쳐 순서를 확인한다."""
    body = {key: value for key, value in (("summary", title), ("location", location)) if value}
    if description:
        body["description"] = description
    if start or end:
        current = _event_summary(_get_event(user, event_id))
        new_start = start or _to_local_minute(current["start"])
        new_end = end or _to_local_minute(current["end"])
        _check_range(new_start, new_end)
        body.update(start=_event_time(new_start), end=_event_time(new_end))
    if not body:
        raise ExternalAPIError("바꿀 내용이 없습니다.")
    return body


def _to_local_minute(value):
    if not value or "T" not in value:
        raise ExternalAPIError("종일 일정의 시간은 시작과 끝을 모두 지정해 주세요.")
    return datetime.fromisoformat(value).strftime(LOCAL_MINUTE)


def preview_update(user, event_id, title=None, start=None, end=None, location=None, description=None):
    """바뀌기 전과 후를 함께 보여 준다(명세)."""
    before = _get_event(user, event_id)
    changes = []
    if title:
        changes.append(f"제목: '{before.get('summary', '')}' → '{title}'")
    if start or end:
        old = _event_summary(before)
        new_start = _parse_local(start or _to_local_minute(old["start"]))
        new_end = _parse_local(end or _to_local_minute(old["end"]))
        changes.append(f"시간: {_describe_event_time(before)} → {_describe_time(new_start, new_end)}")
    if location:
        changes.append(f"장소: '{before.get('location') or '없음'}' → '{location}'")
    if description:
        changes.append("메모 내용 변경")
    return f"{_describe_event(before)} 일정을 수정합니다. " + ", ".join(changes) + "."


# ── EXTERNAL: delete_event ───────────────────────────────


def delete_event(user, event_id):
    try:
        _call(user, "DELETE", f"{EVENTS_URL}/{event_id}")
    except ExternalAPIError as error:
        return error_result(str(error))
    return ok_result({"deleted_id": event_id, "deleted_from": "Google 캘린더"})


def preview_delete(user, event_id):
    """지워질 일정의 제목과 시각을 보여 준다(명세). 다른 일정을 지우는 실수를 사람이 잡을 수 있게."""
    event = _get_event(user, event_id)
    return f"{_describe_event(event)} 일정을 {_calendar_name(user)}에서 삭제합니다."


# ── 등록 ─────────────────────────────────────────────────

_TEXT = {"type": "string", "minLength": 1, "maxLength": 200}
_WHEN = {"type": "string", "pattern": DATETIME_PATTERN, "description": "서울 기준 YYYY-MM-DDTHH:MM"}

register(
    Tool(
        name="list_events",
        description=(
            "Google 캘린더의 일정을 기간으로 조회한다(서울 기준, 최대 31일). "
            "일정을 수정하거나 삭제하기 전에는 반드시 이것으로 대상의 id를 먼저 찾는다."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "pattern": DATE_PATTERN},
                "date_to": {"type": "string", "pattern": DATE_PATTERN},
            },
            "required": ["date_from", "date_to"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.READ,
        run=list_events,
        label=lambda date_from="", date_to="", **_: f"캘린더 일정 확인 ({date_from}~{date_to})",
        external_content=True,  # 일정 제목은 다른 사람이 보낸 초대일 수 있다. 지시문으로 받지 않는다
    )
)

register(
    Tool(
        name="create_event",
        description=(
            "Google 캘린더에 일정을 만든다. 사용자 승인 후에만 실행된다. "
            "승인 전에는 만들었다고 말하지 않는다."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": _TEXT,
                "start": _WHEN,
                "end": _WHEN,
                "location": _TEXT,
                "description": {"type": "string", "maxLength": 2000},
            },
            "required": ["title", "start", "end"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.EXTERNAL,
        run=create_event,
        label=lambda title="", **_: f"캘린더에 일정 만들기: {title}",
        preview=preview_create,
    )
)

register(
    Tool(
        name="update_event",
        description=(
            "Google 캘린더 일정을 수정한다. event_id는 list_events로 찾는다. "
            "바꿀 필드만 넣는다. 사용자 승인 후 실행."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "minLength": 1, "maxLength": 1024},
                "title": _TEXT,
                "start": _WHEN,
                "end": _WHEN,
                "location": _TEXT,
                "description": {"type": "string", "maxLength": 2000},
            },
            "required": ["event_id"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.EXTERNAL,
        run=update_event,
        label=lambda title="", **_: f"캘린더 일정 수정{': ' + title if title else ''}",
        preview=preview_update,
    )
)

register(
    Tool(
        name="delete_event",
        description="Google 캘린더 일정을 삭제한다. event_id는 list_events로 찾는다. 사용자 승인 후 실행.",
        input_schema={
            "type": "object",
            "properties": {"event_id": {"type": "string", "minLength": 1, "maxLength": 1024}},
            "required": ["event_id"],
            "additionalProperties": False,
        },
        risk_level=RiskLevel.EXTERNAL,
        run=delete_event,
        label=lambda **_: "캘린더 일정 삭제",
        preview=preview_delete,
    )
)
