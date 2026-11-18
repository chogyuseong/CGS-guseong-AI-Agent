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

진입점(누가 부르는지):
- 도구 함수 4개: app/agent/toolsets.py가 list_events는 read_tools에, create/update/delete_event는 external_tools
  (build_external_toolset, requires_approval=True)에 넣는다. external_tools는 chat task에만 간다(예약·감시 없음).
- LABELS → toolsets.TOOL_LABELS(타임라인 문구), PREVIEWS → toolsets.TOOL_PREVIEWS
  (toolsets.preview_for가 승인 카드 문장을 만들 때 부른다. 실패하면 기본 문장),
  SPECS → plans.EXTERNAL_TOOL_SPECS(실행 계획 규격).
- SPECS의 target_version(current_etag)은 plans.with_target_version이 계획을 만들 때, reconcile_*는
  executor.ask_external이 결과 불명·복구 때 부른다(예외는 거기서 삼켜 unknown이 된다).
- evals/fake_externals.py가 EVENTS_URL을 가져다 가짜 Google 응답을 붙인다.

변경 이력:
- 2026-11-17 4단계: 도구 4개, preview, 외부 문자열 「」 표시(DECISIONS "일정 도구").
- 2026-11-17 4단계 보강: 고정 event id(ma + operation_id)·409 연결·reconcile_create, 수정·삭제의 ETag 고정과 If-Match,
  412·etag 불일치 → target_changed, 참석자·반복 시리즈 거부, reconcile_update·reconcile_delete. 규격 version 1 → 2
  (DECISIONS "4단계 보강" 2026-11-17 줄, BUILD_SPEC 3.5·4.3·7.3).

고칠 때 주의:
- 보내는 요청 모양(본문 칸, 헤더, 고정 id 규칙)을 바꾸면 SPECS의 ExternalToolSpec version을 올린다. 올리지 않으면
  예전 모양으로 승인받은 계획이 새 모양으로 실행된다(올리면 plan_outdated로 다시 승인).
- error_code "target_changed"는 app/agent/executor.py의 TARGET_CHANGED와 같은 문자열이어야 operation이 stale이 된다.
- 도구를 더하거나 이름을 바꾸면: toolsets 등록, LABELS, PREVIEWS(EXTERNAL), SPECS(EXTERNAL),
  app/agent/data_policy.py의 TOOL_POLICIES(tests/test_data_policy.py가 빠진 것을 잡는다), 외부 글을 돌려주면
  app/agent/results.py의 EXTERNAL_CONTENT_TOOLS, docs/TOOLS.md "Calendar API" 절, docs/STATE_MACHINES.md(상태가 바뀌면).
- 도구 함수의 docstring과 Field(description)는 모델에게 보이는 도구 설명이다. 바꾸면 eval을 다시 돌린다(docs/EVALS.md).
- 녹화·형식 응답: tests/fixtures/google_event*.json, google_events_*.json, google_error_*.json(목록은
  tests/fixtures/README.md).
- 테스트: tests/test_gcal.py, test_gcal_create.py, test_gcal_change.py, test_calendar_flow.py.
"""

import re
import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import quote

from pydantic import Field
from pydantic_ai import RunContext

from app import google_auth
from app.agent.deps import AgentDeps
from app.agent.executor import Execution, begin_execution, finish_execution
from app.agent.plans import ExternalToolSpec, PlanRef, ReconcileOutcome
from app.agent.prompts import SEOUL
from app.agent.results import ToolResult, ToolStatus
from app.tools import http
from app.tools.http import ExternalApiError
from app.tools.places import clip

# 기본 캘린더(primary)만 다룬다(DECISIONS 2026-11-17 "일정 도구"). evals/fake_externals.py도 이 주소를 쓴다.
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SERVICE = "Google 캘린더"  # 오류 문장 앞머리(http.py가 사용자에게 보이는 문장에 넣는다)
TIME_ZONE = "Asia/Seoul"
# 아래 두 값은 docs/TOOLS.md list_events 표에 적힌 값이다(바꾸면 표와 list_events 도구 설명의 "최대 31일"도 고친다).
MAX_RANGE_DAYS = 31  # 한 번 조회할 수 있는 날짜 범위
MAX_EVENTS = 50  # maxResults. 모델에게 가는 결과는 ToolResult.external이 다시 6,000자로 자른다
WEEKDAYS = "월화수목금토일"
LOCAL_MINUTE = "%Y-%m-%dT%H:%M"  # 도구 입력 시각 모양. MinuteText의 pattern과 같아야 한다
EXTERNAL_TEXT_LIMIT = 40  # preview에 넣는 외부 문자열 길이(감시 에이전트 입력에 주입 문장이 길게 들어가지 않게)
# 고정 event id 앞머리. base32hex(0-9a-v) 글자여야 한다(events/insert 문서). 바꾸면 SPECS create_event version을 올린다.
EVENT_ID_PREFIX = "ma"
DUPLICATE_STATUS = 409  # insert: 같은 id의 일정이 이미 있음
SERVICE_KEY = "google"  # reconcile이 돌려주는 provider(영수증의 provider 칸). google_auth.PROVIDER와 같은 값
PRECONDITION_FAILED = 412  # If-Match(ETag) 불일치: 승인 뒤 다른 곳에서 일정이 바뀌었다
STALE_MESSAGE = (
    "승인한 뒤 다른 곳에서 일정이 바뀌어 실행하지 않았습니다. 바뀐 일정을 다시 확인하고 새로 승인받아야 합니다."
)
TARGET_UNKNOWN_MESSAGE = (
    "승인할 때 일정 정보를 확인하지 못해 실행하지 않았습니다. 일정을 다시 조회해 새로 요청해 주세요."
)
ID_CONFLICT_MESSAGE = "같은 번호의 다른 일정이 이미 있어 새로 만들지 않았습니다. 캘린더를 확인해 주세요."
NOT_FOUND_MESSAGE = "해당 일정을 찾지 못했습니다. 이미 삭제되었을 수 있습니다."
FORBIDDEN_MESSAGE = (
    "Google 캘린더에서 이 작업을 할 권한이 없습니다. Calendar API 사용 설정과 연결할 때 허용한 권한을 확인해 주세요."
)
NOT_CONNECTED_WARNING = " (주의: Google 계정이 연결되어 있지 않아 승인해도 실패합니다. 설정에서 먼저 연결해 주세요.)"
# preview의 외부 문자열에서 지울 글자. 「」를 지우는 이유: 일정 제목에 「」를 넣어
# 감싸기 밖으로 빠져나온 척하는 주입을 막는다.
CONTROL_OR_QUOTE = re.compile(r"[\x00-\x1f\x7f「」]")

# 인자 타입. Field(pattern·길이)는 Pydantic AI가 모델 입력을 검사하는 데 쓰고 description은 모델에게 보인다(코드 값).
# EventId의 글자 제한은 주소 경로 조작을 막는다(주소에 넣을 때 quote로 한 번 더 인코딩한다).
DayText = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD (서울 기준)")]
MinuteText = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", description="서울 기준 YYYY-MM-DDTHH:MM")]
EventTitle = Annotated[str, Field(min_length=1, max_length=200, description="일정 제목")]
EventLocation = Annotated[str | None, Field(min_length=1, max_length=200, description="장소(선택)")]
EventMemo = Annotated[str | None, Field(min_length=1, max_length=2000, description="메모(선택)")]
EventId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,1024}$", description="list_events 결과의 id")]


# ---- Google 호출 ----


def translate(error: ExternalApiError) -> ExternalApiError:
    """404·410은 "일정을 찾지 못함", 한도가 아닌 403은 "권한 없음"으로 바꾼다.

    http.read_response의 일반 문장("인증에 실패했습니다", "요청이 거부되었습니다")보다 사용자가 할 일을 알려 준다.
    한도 403은 read_response가 이미 "호출 한도" 문장(code rate_limited)으로 바꿔 두었으므로 문장으로 걸러 그대로 둔다
    (http.py의 한도 문장에서 "한도"를 빼면 이 판정이 깨진다). status·outcome_unknown은 그대로라 결과 등급은 같다.
    """
    if error.status in (404, 410):
        return ExternalApiError(NOT_FOUND_MESSAGE, error.status)
    if error.status == 403 and "한도" not in error.message:
        return ExternalApiError(FORBIDDEN_MESSAGE, error.status)
    return error


async def calendar_request(
    deps: AgentDeps, method: str, event_id: str = "", *, if_match: str | None = None, **kwargs: Any
) -> Any:
    """캘린더 API를 부른다. 401이면 토큰을 갱신해 한 번만 다시 보낸다. if_match는 조건부 수정·삭제(ETag).

    결과: JSON 본문(삭제처럼 본문이 없으면 {}). 실패는 ExternalApiError로 던진다(도구가 ToolResult로 바꾼다).
    - 연결 없음·다시 연결 필요는 google_auth.access_token이 던진다(GoogleNotConnectedError 등, Google에 보내지 않음).
    - 401 재전송은 쓰기에도 한다: 401은 Google이 요청을 받아들이지 않은 것이라 일정이 두 번 생기지 않는다.
      그 밖의 재시도는 http.request_json 규칙(GET만 1회)을 따른다.
    부수 효과: 외부 호출, 토큰 갱신 때 connections 행 갱신(google_auth).
    event_id는 quote(safe='')로 다시 인코딩한다(인자 pattern과 함께 '/'·'..'로 다른 경로를 부르는 것을 막는다).
    """
    url = f"{EVENTS_URL}/{quote(event_id, safe='')}" if event_id else EVENTS_URL
    for attempt in range(2):
        token = await google_auth.access_token(deps, force_refresh=attempt > 0)
        headers = {"Authorization": f"Bearer {token}"}
        if if_match:
            headers["If-Match"] = if_match
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
    """일정 하나. 지운 일정은 404가 아니라 200 + status 'cancelled'로 올 수 있어 찾지 못한 것으로 본다.

    preview, checked_target(실행 직전 검사), current_etag(계획의 대상 버전)가 쓴다. 지운 일정을 "있음"으로 보면
    이미 없는 일정을 고치는 승인 카드가 정상처럼 보이므로 404 ExternalApiError로 통일한다(DECISIONS 2026-11-17).
    """
    event = await calendar_request(deps, "GET", event_id)
    if not isinstance(event, dict) or event.get("status") == "cancelled":
        raise ExternalApiError(NOT_FOUND_MESSAGE, 404)
    return event


# ---- 시각 다루기 ----


def parse_local(text: str) -> datetime:
    """서울 기준 'YYYY-MM-DDTHH:MM'을 시간대 없는 datetime으로. 모양은 맞는데 없는 날짜(2월 30일)면 ExternalApiError.

    ExternalApiError로 던지는 이유: 도구가 외부 오류와 같은 경로(ToolResult.from_api_error → failed)로 처리하게.
    status가 없어 outcome_unknown도 아니므로 실패로 확정된다(외부에 보내기 전 검사).
    """
    try:
        return datetime.strptime(text, LOCAL_MINUTE)
    except ValueError as error:
        raise ExternalApiError("시각은 YYYY-MM-DDTHH:MM 형식이어야 합니다(서울 기준).") from error


def check_range(start: str, end: str) -> tuple[datetime, datetime]:
    """시작·끝을 읽고 끝이 시작보다 늦은지 확인한다. 실행(create·patch_body)과 preview가 같은 규칙을 쓴다."""
    start_at, end_at = parse_local(start), parse_local(end)
    if end_at <= start_at:
        raise ExternalApiError("끝나는 시각이 시작 시각보다 늦어야 합니다.")
    return start_at, end_at


def event_time(value: str) -> dict[str, str]:
    """Google 요청 본문의 시각 칸. 오프셋 없이 timeZone을 함께 보내 서울 시각으로 해석되게 한다(DECISIONS "일정 도구").

    고칠 때 주의: 모양을 바꾸면 요청 모양이 바뀌므로 SPECS의 version을 올린다.
    """
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
    """Google 일정의 시각을 승인 카드용 문장으로. 시각 일정은 describe_range, 종일 일정(start.date)은 '… 종일'.

    date가 없거나 비어 있으면 1970-01-01로 채워 예외 없이 문장을 만든다.
    """
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
    """모델에게 넘길 칸만 남긴다. 설명(초대 본문), 참석자 이메일은 넘기지 않는다.

    막는 사고: 남이 보낸 초대 본문에 숨은 주입 문장, 참석자 주소(개인정보)가 이후 모든 모델 요청에 따라붙는 것
    (DECISIONS 2026-11-17 "외부 도구 결과에는 모델이 쓸 칸만"). list_events 결과와 쓰기 성공 결과(saved_result)가 쓴다.
    고칠 때 주의: "title" 칸 이름은 data_policy.event_titles가 읽는다(일정 제목이 공개 검색어로 나가는 것을 막는 검사).
    """
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
    """외부 문자열을 preview에 넣는 모양: 제어 문자·줄바꿈·「」를 지우고 40자로 잘라 「」로 감싼다.

    막는 사고: 일정 제목에 숨은 주입 문장이 감시 에이전트 입력(preview)에 길게 들어가 판정을 흔드는 것.
    감시 지시문은 「」 안을 데이터로 보라고 한다(docs/SAFETY.md).
    모델이 넣은 값은 '…'로 감싸 구분한다(DECISIONS 2026-11-17).
    다른 도구도 외부 문자열을 preview에 넣을 때 이 모양을 따른다(AGENTS.md 도구 규약 6).
    """
    cleaned = " ".join(CONTROL_OR_QUOTE.sub(" ", str(text or "(제목 없음)")).split())
    return f"「{clip(cleaned, EXTERNAL_TEXT_LIMIT)}」"


def describe_event(event: dict[str, Any]) -> str:
    """승인 카드의 대상 일정: 「제목」(시각). 사람이 다른 일정을 고르거나 지우는 실수를 잡게 한다(명세)."""
    return f"{quote_external(event.get('summary'))}({describe_event_time(event)})"


# ---- READ: list_events ----


def check_days(date_from: str, date_to: str) -> tuple[date, date]:
    """조회 범위 검사(실제 날짜, 순서, MAX_RANGE_DAYS 이내).
    어기면 ExternalApiError → list_events가 failed로 돌려준다."""
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
    """events.list 쿼리. singleEvents=true라 반복 일정은 회차별 id로 오고, 그 회차는 수정·삭제할 수 있다
    (시리즈 원본은 policy_refusal이 막는다, DECISIONS 4단계 보강)."""
    return {
        # 서울 기준 시작일 0시 ~ 종료일 다음 날 0시. 종료일 하루 전체를 포함한다.
        "timeMin": f"{first_day.isoformat()}T00:00:00+09:00",
        "timeMax": f"{(last_day + timedelta(days=1)).isoformat()}T00:00:00+09:00",
        "singleEvents": "true",  # 반복 일정을 각 회차로 펼친다
        "orderBy": "startTime",
        "maxResults": MAX_EVENTS,
        "timeZone": TIME_ZONE,
    }


# READ 도구(read_tools). docstring은 모델에게 보이는 도구 설명이라 여기 주석으로 적는다.
# - 결과: 성공은 ToolResult.external(지시문 무시 안내로 감싸고 6,000자, 출처는 htmlLink). 빈 기간도 성공 + message
#   (DECISIONS 2026-11-17: 빈 결과는 정상 답). 범위 오류·연결 없음·Google 오류는 failed(조회라 unknown은 없다).
# - status 'cancelled' 일정은 뺀다(get_event와 같이 지운 일정으로 본다).
# - 결과 등급은 user_private(data_policy.TOOL_POLICIES).
#   결과의 제목은 이후 공개 검색어 검사(data_policy.event_titles)에 쓰인다.
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
    """쓰기 성공 결과(만들기·고치기, 409 연결, 재조회). succeeded지만 영수증은 finish_execution이 남긴다.

    finish_execution을 거치지 않은 성공은 StepTracking이 unknown으로 바꾸므로 도구는 반드시 finished()로 돌려준다.
    """
    view = event_view(event) if isinstance(event, dict) else {}
    link = str(event.get("htmlLink") or "") if isinstance(event, dict) else ""
    return ToolResult(
        status=ToolStatus.SUCCEEDED, data={**view, "saved_to": "Google 캘린더"}, sources=[link] if link else []
    )


# EXTERNAL 도구(external_tools, requires_approval=True). docstring은 모델에게 보이는 도구 설명이라 여기 주석으로 적는다.
# 불변 조건(순서): begin_execution(인자 전부) → 외부 호출 → finished(finish_execution). begin_execution은 승인 때 고정한
#   인자 해시와 다르면 PermissionError(유일하게 밖으로 던지는 예외), 끝난 operation이면 저장된 결과, 실행 전 거절이면
#   failed를 돌려준다(ToolResult면 외부 호출 없이 그대로 반환). args에서 인자를 빼면 해시가 달라져 실행되지 않는다.
# 멱등성: event id를 operation_id로 고정한다(stable_event_id). 같은 operation이 다시 보내도 Google이 409로 막는다.
# 결과:
# - succeeded: 만들어짐, 또는 409인데 같은 id의 일정이 계획과 같음(앞선 시도가 실제로 만든 것).
# - failed: 시각 순서 오류(Google에 보내지 않음), 409인데 내용이 다름(id_conflict), 4xx, 연결 없음.
# - unknown: 쓰기 시간 초과·연결 끊김·5xx(http.py outcome_unknown).
#   finish_execution이 곧바로 reconcile_create로 확인한다.
# 409 분기는 finish_execution 전에 Google을 한 번 더 조회한다(existing_result).
# 요청 본문 칸을 바꾸면 SPECS version을 올린다.
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
    event_id = stable_event_id(started.operation_id)
    body: dict[str, Any] = {"id": event_id, "summary": title, "start": event_time(start), "end": event_time(end)}
    body.update({k: v for k, v in (("location", location), ("description", description)) if v})
    try:
        check_range(start, end)
        event = await calendar_request(ctx.deps, "POST", json=body)
    except ExternalApiError as error:
        if error.status == DUPLICATE_STATUS:
            return await finished(ctx.deps, started, await existing_result(ctx.deps, event_id, args), event_id)
        return await finished(ctx.deps, started, ToolResult.from_api_error(error))
    return await finished(ctx.deps, started, saved_result(event), event_id_of(event) or event_id)


def stable_event_id(operation_id: uuid.UUID) -> str:
    """operation마다 고정된 event id. 16진수(0-9a-f)는 base32hex(0-9a-v)에 들어가고 34자라 규칙(5~1024자)에 맞다.

    Google 쪽 멱등성 키 역할이다: 같은 operation의 두 번째 insert는 409가 되어 일정이 두 번 생기지 않고,
    응답을 잃어도 이 id로 조회해 실제로 만들었는지 확인할 수 있다(reconcile_create). DECISIONS 4단계 보강 2026-11-17.
    고칠 때 주의: 규칙을 바꾸면 이미 승인·실행된 operation의 재조회가 엉뚱한 id를 보게 된다.
    SPECS create_event version을 올린다.
    """
    return f"{EVENT_ID_PREFIX}{operation_id.hex}"


def event_matches(event: Any, args: dict[str, Any]) -> bool:
    """이 일정이 계획한 내용(제목·시작·끝·장소)과 같은가. 같은 id라도 내용이 다르면 우리 결과로 보지 않는다.

    막는 사고: 내용이 다른 일정을 "만들었다"는 영수증으로 남기는 것(성공 표시는 실제 결과에 근거, 절대 원칙 10).
    args는 승인 때 고정된 인자(canonical_args 또는 도구 인자)다.
    메모(description)는 비교하지 않고, 장소는 계획에 있을 때만 본다.
    종일 일정(dateTime 없음)은 우리가 만든 모양이 아니라 False.
    """
    if not isinstance(event, dict):
        return False
    start, end = (event.get("start") or {}).get("dateTime"), (event.get("end") or {}).get("dateTime")
    if not (start and end) or event.get("summary") != args.get("title"):
        return False
    planned_start = parse_local(str(args.get("start"))).replace(tzinfo=SEOUL)
    planned_end = parse_local(str(args.get("end"))).replace(tzinfo=SEOUL)
    same_place = not args.get("location") or event.get("location") == args.get("location")
    return seoul_time(start) == planned_start and seoul_time(end) == planned_end and same_place


async def existing_result(deps: AgentDeps, event_id: str, args: dict[str, Any]) -> ToolResult:
    """409(같은 id가 이미 있음): 그 일정을 읽어 계획과 같을 때만 성공으로 연결한다(BUILD_SPEC 4.3).

    결과: 같으면 succeeded(앞선 시도의 결과), 다르거나 취소됐으면 failed(id_conflict), 조회 실패면 그 오류(failed).
    부수 효과: Google GET 한 번(조회라 다시 보내도 흔적이 없다).
    """
    try:
        event = await calendar_request(deps, "GET", event_id)
    except ExternalApiError as error:
        return ToolResult.from_api_error(error)
    if event_matches(event, args) and event.get("status") != "cancelled":
        return saved_result(event)
    return ToolResult.failure(ID_CONFLICT_MESSAGE, error_code="id_conflict")


async def reconcile_create(deps: AgentDeps, ref: PlanRef) -> ReconcileOutcome:
    """복구·응답 유실 뒤 결과 확인: 고정 id로 일정을 조회한다. 없으면 만들지 않은 것, 내용이 같으면 만든 것.

    SPECS create_event의 reconcile. executor.ask_external이 부른다(결과 불명일 때 finish_execution이 곧바로 한 번,
    복구 때 끊긴 step이면 reconcile_step). 쓰기를 다시 보내지 않고 조회만 한다.
    결과: done(영수증), not_done(404·410 → failed not_executed), unknown(내용이 다름).
    그 밖의 오류는 던지고, ask_external이 삼켜 unknown으로 둔다(실패로 단정하지 않는다).
    """
    event_id = stable_event_id(ref.operation_id)
    try:
        event = await calendar_request(deps, "GET", event_id)
    except ExternalApiError as error:
        if error.status in (404, 410):
            return ReconcileOutcome("not_done")
        raise
    if event_matches(event, ref.canonical_args):
        return ReconcileOutcome("done", resource_id=event_id, data=saved_result(event).data, provider=SERVICE_KEY)
    return ReconcileOutcome("unknown")


def event_id_of(event: Any) -> str | None:
    """Google 응답의 일정 id(영수증의 resource_id). 없으면 None."""
    if not isinstance(event, dict):
        return None
    return str(event.get("id") or "") or None


async def finished(deps: AgentDeps, started: Execution, result: ToolResult, event_id: str | None = None) -> ToolResult:
    """실행 결과를 operation·영수증으로 기록한다(성공이면 영수증, 실패·불명이면 그 상태).

    begin_execution이 Execution을 돌려준 뒤에는 모든 반환 경로가 이 함수를 거쳐야 한다(실행 전 거절도 포함).
    빠뜨리면 operation이 executing으로 남고, 성공은 영수증이 없어 StepTracking이 unknown으로 바꾼다.
    부수 효과: DB 커밋(executor.finish_execution). 결과가 unknown이면 그 안에서 reconcile_*로 Google을 한 번 조회한다.
    event_id는 영수증의 resource_id다(실패면 넣지 않아도 된다).
    """
    return await finish_execution(deps, started, result, provider=google_auth.PROVIDER, resource_id=event_id)


async def connection_warning(deps: AgentDeps) -> str:
    """연결이 없으면 승인 카드에 붙일 경고. 승인 요청은 막지 않는다(DECISIONS 제안: 도구 숨기기는 아직 안 함)."""
    return "" if await google_auth.is_connected(deps) else NOT_CONNECTED_WARNING


async def preview_create_event(deps: AgentDeps, args: dict[str, Any]) -> str:
    """'11월 20일(금) 19:00~21:00 '저녁 약속' 일정을 기본 캘린더에 만듭니다.'(명세 예)

    PREVIEWS에 등록된 승인 카드 문장. 사람과 감시 에이전트(app/agent/guard.py)가 이 문장으로 판단한다.
    실행하지 않고 설명만 한다(Google 호출 없음, 연결 확인 DB 조회만). 제목·장소는 모델이 넣은 값이라 '…'로 감싼다.
    시각이 잘못됐으면 예외 대신 "실행되지 않습니다" 문장을 돌려준다(실행해도 check_range가 failed로 막는다).
    """
    title, location = clip(args.get("title"), 100), args.get("location")
    try:
        when = describe_range(*check_range(str(args.get("start")), str(args.get("end"))))
    except ExternalApiError as error:
        return f"'{title}' 일정을 기본 캘린더에 만들려고 하지만 실행되지 않습니다: {error.message}"
    where = f" 장소: {clip(location, 100)}." if location else ""
    return f"{when} '{title}' 일정을 기본 캘린더에 만듭니다.{where}{await connection_warning(deps)}"


# ---- EXTERNAL: update_event ----


def patch_body(current: dict[str, Any], changes: dict[str, str | None]) -> dict[str, Any]:
    """바꿀 칸만 담는다. 시작이나 끝 하나만 바꿔도 지금 일정의 값과 합쳐 순서를 확인한다.

    PATCH는 보낸 칸만 바꾸므로 None인 인자는 넣지 않는다(칸을 비우는 기능은 없다).
    시각을 바꿀 때는 start·end를 함께 보낸다. 종일 일정의 한쪽만 바꾸면 local_minute가 ExternalApiError(failed).
    바꿀 칸이 없으면 ExternalApiError → Google에 보내지 않고 failed.
    고칠 때 주의: 본문 칸이 바뀌면 SPECS version을 올린다.
    """
    body: dict[str, Any] = {
        key: changes[name]
        for key, name in (("summary", "title"), ("location", "location"), ("description", "description"))
        if changes.get(name)
    }
    start, end = changes.get("start"), changes.get("end")
    if start or end:
        new_start = start or local_minute((current.get("start") or {}).get("dateTime"))
        new_end = end or local_minute((current.get("end") or {}).get("dateTime"))
        check_range(new_start, new_end)
        body.update(start=event_time(new_start), end=event_time(new_end))
    if not body:
        raise ExternalApiError("바꿀 내용이 없습니다.")
    return body


# ---- 4단계 보강: 기존 일정을 바꾸기 전 검사(대상 버전, 참석자, 반복 시리즈) ----


def policy_refusal(event: dict[str, Any], action: str) -> ToolResult | None:
    """참석자가 있는 일정·남이 만든 초대·반복 일정 시리즈 원본은 바꾸지 않는다(BUILD_SPEC 7.3).

    막는 사고: 남과 공유된 일정을 바꿔 다른 사람의 캘린더까지 바뀌는 것, 반복 시리즈 전체를 한 번에 바꾸거나 지우는 것.
    결과: 거부면 failed(has_attendees 또는 recurring_series), 괜찮으면 None. 실행 직전(checked_target)과
    승인 카드 경고(policy_warning)가 같은 규칙을 쓴다. error_code를 바꾸면 policy_warning도 고친다.
    """
    others = [a for a in event.get("attendees") or [] if isinstance(a, dict) and not a.get("self")]
    organizer = event.get("organizer")
    invited = isinstance(organizer, dict) and not organizer.get("self")
    if others or invited:
        return ToolResult.failure(
            f"참석자가 있는 일정은 {action}하지 않습니다. 캘린더에서 직접 바꿔 주세요.", error_code="has_attendees"
        )
    if event.get("recurrence"):
        return ToolResult.failure(
            f"반복 일정 전체는 {action}하지 않습니다. 한 번의 일정만 골라 다시 요청해 주세요.",
            error_code="recurring_series",
        )
    return None


def policy_warning(event: dict[str, Any], action: str) -> str:
    """승인 카드에 붙일 경고(실행하면 거부될 일정)."""
    refusal = policy_refusal(event, action)
    if refusal is None:
        return ""
    if refusal.error_code == "has_attendees":
        return f" (참석자가 있는 일정이라 승인해도 {action}하지 않습니다.)"
    return f" (반복 일정 전체라 승인해도 {action}하지 않습니다.)"


def stale_result() -> ToolResult:
    """승인 뒤 대상이 바뀌어 보내지 않음. error_code "target_changed"는 executor.TARGET_CHANGED와 같아야 한다
    (finish_execution이 operation을 stale로 닫는다. 모델은 다시 조회해 새 계획·새 승인을 받는다, BUILD_SPEC 3.5)."""
    return ToolResult.failure(STALE_MESSAGE, error_code="target_changed")


def write_error(error: ExternalApiError) -> ToolResult:
    """412(If-Match 불일치)는 대상이 바뀐 것(stale), 나머지는 보통 오류.

    412는 Google이 요청을 반영하지 않았다는 확인이라 unknown이 아니라 failed다.
    시간 초과·5xx는 from_api_error가 unknown으로 둔다.
    """
    return stale_result() if error.status == PRECONDITION_FAILED else ToolResult.from_api_error(error)


async def checked_target(
    deps: AgentDeps, event_id: str, version: str | None, action: str
) -> dict[str, Any] | ToolResult:
    """지금 일정을 읽어 정책과 버전을 확인한다. 바꿔도 되면 그 일정, 아니면 실행하지 않을 이유.

    version은 계획을 만들 때 고정한 etag(Execution.target_version). 검사 순서와 결과:
    조회 실패 → 그 오류(failed), 참석자·반복 → failed, version 없음 → target_unknown(보지 못한 일정을 바꾸지 않는다),
    etag 다름 → target_changed(승인한 것과 다른 일정을 바꾸지 않는다).
    여기서 같아도 보내기 직전까지 바뀔 수 있어 요청에 If-Match를 다시 건다(DECISIONS 4단계 보강 2026-11-17).
    """
    try:
        current = await get_event(deps, event_id)
    except ExternalApiError as error:
        return ToolResult.from_api_error(error)
    refusal = policy_refusal(current, action)
    if refusal is not None:
        return refusal
    if version is None:
        return ToolResult.failure(TARGET_UNKNOWN_MESSAGE, error_code="target_unknown")
    if current.get("etag") != version:
        return stale_result()
    return current


async def current_etag(deps: AgentDeps, event_id: str) -> str | None:
    """계획을 만들 때 고정할 대상 버전. 일정을 읽지 못하면 None.

    SPECS update/delete_event의 target_version. 실행 계획 초안을 만들 때 plans.with_target_version이 부른다.
    None이어도 승인 요청은 만들어지고, 실행 때 checked_target이 target_unknown으로 거부한다.
    """
    try:
        event = await get_event(deps, event_id)
    except ExternalApiError:
        return None
    etag = event.get("etag")
    return str(etag) if etag else None


# EXTERNAL 도구. docstring은 모델에게 보이는 도구 설명이라 여기 주석으로 적는다.
# 순서: begin_execution(event_id + 바꿀 칸 전부) → checked_target(지금 일정 GET, 정책·etag)
#   → PATCH(If-Match) → finished.
# 결과: succeeded(바뀐 일정), failed(거부·target_changed·target_unknown·바꿀 칸 없음·4xx),
#   unknown(PATCH 시간 초과·5xx → finish_execution 안에서 reconcile_update).
# 중복·덮어쓰기는 멱등성 키 대신 operation(한 번만 executing)과 If-Match(대상 버전)로 막는다
# (data_policy CALENDAR_WRITE).
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
    current = await checked_target(ctx.deps, event_id, started.target_version, "수정")
    if isinstance(current, ToolResult):
        return await finished(ctx.deps, started, current)
    try:
        body = patch_body(current, changes)
        event = await calendar_request(ctx.deps, "PATCH", event_id, if_match=started.target_version, json=body)
    except ExternalApiError as error:
        return await finished(ctx.deps, started, write_error(error))
    return await finished(ctx.deps, started, saved_result(event), event_id)


def describe_changes(before: dict[str, Any] | None, args: dict[str, Any]) -> str:
    """바뀌기 전과 후(명세). 이전 값을 못 읽었으면 바뀔 값만 적는다.

    이전 값(Google에서 읽은 외부 문자열)은 quote_external의 「」, 새 값(모델이 넣은 값)은 '…'로 구분한다.
    메모(최대 2,000자)는 내용 없이 "메모 내용 변경"만 적는다(입력값은 승인 카드의 접힌 입력값에서 볼 수 있다).
    """
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
    """시간 변경 문장. 한쪽만 바꾸면 이전 일정 값과 합쳐 보여 준다(patch_body와 같은 규칙).
    잘못된 시각이면 예외 대신 "(올바르지 않음: …)"을 넣어 사람이 승인 전에 알아보게 한다."""
    try:
        start = args.get("start") or local_minute(((before or {}).get("start") or {}).get("dateTime"))
        end = args.get("end") or local_minute(((before or {}).get("end") or {}).get("dateTime"))
        after = describe_range(*check_range(start, end))
    except ExternalApiError as error:
        after = f"(올바르지 않음: {error.message})"
    return f"{describe_event_time(before)} → {after}" if before else after


async def preview_update_event(deps: AgentDeps, args: dict[str, Any]) -> str:
    """승인 카드 문장: 「대상 일정」(시각) + 정책 경고 + 바뀌기 전→후. 실행하지 않는다(Google GET 한 번).

    일정을 읽지 못해도(연결 없음, 404, cancelled) 승인 요청을 막지 않고 이유를 문장에 넣는다(DECISIONS 2026-11-17).
    event_id는 모델 입력이라 40자로 자른다.
    """
    event_id = clip(args.get("event_id"), 40)
    try:
        before: dict[str, Any] | None = await get_event(deps, str(args.get("event_id")))
        head = f"{describe_event(before)} 일정을 수정합니다.{policy_warning(before, '수정')}"
    except ExternalApiError as error:
        before, head = None, f"일정(ID {event_id})을 수정합니다. 일정 정보를 불러오지 못했습니다: {error.message}"
    return f"{head} {describe_changes(before, args)}."


# ---- EXTERNAL: delete_event ----


# EXTERNAL 도구. docstring은 모델에게 보이는 도구 설명이라 여기 주석으로 적는다.
# 순서: begin_execution({"event_id"}) → checked_target(정책·etag) → DELETE(If-Match) → finished(resource_id=event_id).
# 결과: succeeded(deleted_result), failed(거부·target_changed·target_unknown·404 "찾지 못함"·4xx),
#   unknown(DELETE 시간 초과·5xx → finish_execution 안에서 reconcile_delete).
async def delete_event(ctx: RunContext[AgentDeps], event_id: EventId) -> ToolResult:
    """Google 캘린더 일정을 삭제한다. event_id는 list_events로 찾는다. 사용자 승인 후 실행."""
    started = await begin_execution(ctx, {"event_id": event_id})
    if isinstance(started, ToolResult):
        return started
    current = await checked_target(ctx.deps, event_id, started.target_version, "삭제")
    if isinstance(current, ToolResult):
        return await finished(ctx.deps, started, current)
    try:
        await calendar_request(ctx.deps, "DELETE", event_id, if_match=started.target_version)
    except ExternalApiError as error:
        return await finished(ctx.deps, started, write_error(error))
    return await finished(ctx.deps, started, deleted_result(event_id), event_id)


def deleted_result(event_id: str) -> ToolResult:
    """삭제 성공 결과. delete_event와 reconcile_delete가 같은 모양을 쓴다(영수증 data가 경로와 무관하게 같게)."""
    return ToolResult.success({"deleted_id": event_id, "deleted_from": "Google 캘린더"})


async def reconcile_delete(deps: AgentDeps, ref: PlanRef) -> ReconcileOutcome:
    """시간 초과 뒤 확인: 일정이 없어졌으면(404·410·취소됨) 삭제된 것, 그대로 있으면 실행되지 않은 것.

    SPECS delete_event의 reconcile(executor.ask_external이 부른다). 조회만 하고 DELETE를 다시 보내지 않는다.
    한계: 다른 곳에서 지웠어도 done으로 본다(어느 쪽이든 일정은 없어졌다). 그 밖의 오류는 던져 unknown이 된다.
    대상 id는 계획의 target_resource_id를 먼저 쓴다(승인 때 고정한 값).
    """
    event_id = ref.target_resource_id or str(ref.canonical_args.get("event_id"))
    try:
        event = await calendar_request(deps, "GET", event_id)
    except ExternalApiError as error:
        if error.status in (404, 410):
            return ReconcileOutcome("done", event_id, deleted_result(event_id).data, SERVICE_KEY)
        raise
    if isinstance(event, dict) and event.get("status") == "cancelled":
        return ReconcileOutcome("done", event_id, deleted_result(event_id).data, SERVICE_KEY)
    return ReconcileOutcome("not_done")


def has_requested_changes(event: dict[str, Any], args: dict[str, Any]) -> bool:
    """요청한(값이 있는) 칸이 모두 일정에 그 값으로 들어 있는가. 시각은 서울 시각으로 맞춰 비교한다.
    args는 승인 때 고정된 canonical_args다. 비어 있는 칸은 보지 않는다(PATCH도 보내지 않은 칸)."""
    for key, name in (("summary", "title"), ("location", "location"), ("description", "description")):
        if args.get(name) and event.get(key) != args[name]:
            return False
    for name in ("start", "end"):
        value = (event.get(name) or {}).get("dateTime")
        if args.get(name) and (not value or seoul_time(value) != parse_local(args[name]).replace(tzinfo=SEOUL)):
            return False
    return True


async def reconcile_update(deps: AgentDeps, ref: PlanRef) -> ReconcileOutcome:
    """시간 초과 뒤 확인: 요청한 칸이 모두 그 값이면 반영된 것. 아니면 판단할 수 없어 unknown(덮어쓰지 않음).

    SPECS update_event의 reconcile. not_done을 돌려주지 않는 이유: 값이 다르면 우리 PATCH가 안 들어간 것인지,
    들어간 뒤 다른 곳에서 다시 바뀐 것인지 구별할 수 없다. 조회 오류는 그대로 던지고 ask_external이 unknown으로 둔다.
    """
    event_id = ref.target_resource_id or str(ref.canonical_args.get("event_id"))
    event = await calendar_request(deps, "GET", event_id)
    if isinstance(event, dict) and has_requested_changes(event, ref.canonical_args):
        return ReconcileOutcome("done", event_id, saved_result(event).data, SERVICE_KEY)
    return ReconcileOutcome("unknown")


async def preview_delete_event(deps: AgentDeps, args: dict[str, Any]) -> str:
    """지워질 일정의 제목과 시각(명세). 다른 일정을 지우는 실수를 사람이 잡을 수 있게.

    실행하지 않는다(Google GET 한 번). 읽지 못하면 이유를 넣은 문장을 돌려주고 승인 요청은 막지 않는다.
    메일 속 주입으로 delete_event가 불려도 사람·감시 에이전트가 이 문장으로 잡는다(tests/test_mail_flow.py 주입 방어).
    """
    try:
        event = await get_event(deps, str(args.get("event_id")))
    except ExternalApiError as error:
        event_id = clip(args.get("event_id"), 40)
        return f"일정(ID {event_id})을 기본 캘린더에서 삭제합니다. 일정 정보를 불러오지 못했습니다: {error.message}"
    return f"{describe_event(event)} 일정을 기본 캘린더에서 삭제합니다.{policy_warning(event, '삭제')}"


# ---- 타임라인 문구 ----
# toolsets.TOOL_LABELS로 모여 steps.label이 된다(작업 기록 화면). 수정·삭제 문구에는 일정 id·제목을 넣지 않는다.
# create_event 제목은 모델이 넣은 값이라 30자로 자른다.

LABELS = {
    "list_events": lambda args: f"캘린더 일정 확인하는 중 ({args.get('date_from', '?')}~{args.get('date_to', '?')})",
    "create_event": lambda args: f"캘린더에 '{clip(args.get('title'), 30)}' 일정 만드는 중",
    "update_event": lambda args: "캘린더 일정 수정하는 중",
    "delete_event": lambda args: "캘린더 일정 삭제하는 중",
}
# 실행 계획 규격(2단계 보강, app/agent/plans.py). 보내는 요청 모양이 바뀌면 version을 올린다.
# toolsets가 plans.EXTERNAL_TOOL_SPECS에 넣는다. 규격이 없는 EXTERNAL 도구는 계획을 만들지 않아 실행할 수 없다.
# target_arg: 기존 대상을 가리키는 인자(만들기는 없음). target_version: 계획 때 고정할 대상 버전(etag).
# version을 올리면 옛 계획은 실행되지 않는다(executor.refusal_for의 plan_outdated → 다시 승인).
# reconcile: 결과 불명·복구 때 Google을 다시 조회하는 함수(없으면 unknown으로 남는다).
CALENDAR_TARGET = "Google 캘린더(기본 캘린더)"
SPECS = {
    # 4단계 보강: 고정 event id를 보내므로 요청 모양이 바뀌었다(1 → 2). 재조회 함수가 있다.
    "create_event": ExternalToolSpec(
        "2", provider=google_auth.PROVIDER, target_label=CALENDAR_TARGET, reconcile=reconcile_create
    ),
    # 4단계 보강: If-Match(ETag) 조건부 요청·참석자/반복 일정 거부로 요청 모양이 바뀌었다(1 → 2).
    "update_event": ExternalToolSpec(
        "2",
        provider=google_auth.PROVIDER,
        target_arg="event_id",
        target_label=CALENDAR_TARGET,
        reconcile=reconcile_update,
        target_version=current_etag,
    ),
    "delete_event": ExternalToolSpec(
        "2",
        provider=google_auth.PROVIDER,
        target_arg="event_id",
        target_label=CALENDAR_TARGET,
        reconcile=reconcile_delete,
        target_version=current_etag,
    ),
}
# 승인 카드 문장(toolsets.TOOL_PREVIEWS). EXTERNAL 도구를 더하면 여기에도 넣는다(없으면 도구 이름·입력값 그대로).
PREVIEWS = {
    "create_event": preview_create_event,
    "update_event": preview_update_event,
    "delete_event": preview_delete_event,
}
