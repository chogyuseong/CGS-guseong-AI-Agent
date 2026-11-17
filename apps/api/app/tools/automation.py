"""자동화 도구 (6단계): create_schedule·delete_schedule·create_watch·delete_watch = INTERNAL,
list_schedules·list_watches = READ (toolsets.py에서 정한다. 명세 도구 목록).

- '매주/매일 ~해줘'는 create_schedule, '~하면 알려줘'는 create_watch(시스템 프롬프트). 등록 결과에 주기와 만료일을 넣어
  모델이 그대로 말할 수 있게 한다.
- 서버 제한(활성 10개, 감시 간격 30분 이상, READ 도구만, 메일 불가, 중복 거절)은 app/automation/store.py가 강제한다.
  인자 제약(Field)은 모델에게 보이는 안내이자 첫 번째 검사이고, 도구 함수를 직접 불러도 store가 다시 막는다.
- 만든 예약·감시는 자동 작업이라 READ 도구만으로 실행된다(절대 원칙 4). 자동 작업에는 이 생성 도구가 없으므로
  예약 작업이 스스로 예약·감시를 늘릴 수 없다.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.automation import rules, store
from app.automation.store import AutomationError
from app.models import ScheduledJob, Watch
from app.tools.notes import short

ScheduleTitle = Annotated[str, Field(min_length=1, max_length=100, description="예약 이름. 예: 다음 주 일정 요약")]
SchedulePrompt = Annotated[
    str,
    Field(
        min_length=1, max_length=1000, description="정한 시각에 비서에게 시킬 요청 문장. 예: 다음 주 일정을 요약해줘"
    ),
]
TimeOfDay = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$", description="서울 기준 24시간 'HH:MM'. 예: 21:00")]
Weekdays = Annotated[
    list[int],
    Field(
        min_length=1,
        max_length=7,
        description="실행 요일 번호 목록. 0=월 1=화 2=수 3=목 4=금 5=토 6=일. 매일이면 0~6 전부",
    ),
]
WatchDescription = Annotated[str, Field(min_length=1, max_length=200, description="감시 이름. 예: 내일 서울 비 소식")]
WatchToolName = Annotated[
    str, Field(min_length=1, max_length=100, description="주기적으로 실행할 조회 도구 이름. 예: get_weather")
]
WatchToolInput = Annotated[
    dict[str, Any], Field(description="그 도구에 넣을 인자. 예: {'place': '서울', 'date': '2026-11-18'}")
]
Condition = Annotated[
    str, Field(min_length=1, max_length=500, description="알려 줄 조건. 예: 강수 확률이 60%를 넘는다")
]
Interval = Annotated[
    int,
    Field(
        ge=rules.MIN_WATCH_INTERVAL_MINUTES,
        le=rules.MAX_WATCH_INTERVAL_MINUTES,
        description="확인 간격(분). 30 이상 1440 이하",
    ),
]
Repeat = Annotated[
    bool, Field(description="true면 조건이 맞아 알린 뒤 12시간 뒤부터 다시 감시, false면 한 번 알리고 끝")
]


def now_utc() -> datetime:
    return datetime.now(UTC)


def schedule_view(job: ScheduledJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "title": job.title,
        "prompt_text": job.prompt_text,
        "repeat_text": rules.repeat_text(job.time_of_day, job.weekdays),
        "is_active": job.is_active,
        "next_run": rules.seoul_text(job.next_run_at) if job.is_active else None,
    }


def watch_view(watch: Watch) -> dict[str, Any]:
    return {
        "id": watch.id,
        "description": watch.description,
        "tool_name": watch.tool_name,
        "tool_input": watch.tool_input,
        "condition_text": watch.condition_text,
        "interval_minutes": watch.interval_minutes,
        "repeat": watch.repeat,
        "is_active": watch.is_active,
        "expires_on": rules.seoul_text(watch.expires_at)[:10],
    }


async def create_schedule(
    ctx: RunContext[AgentDeps],
    title: ScheduleTitle,
    prompt_text: SchedulePrompt,
    time_of_day: TimeOfDay,
    weekdays: Weekdays,
) -> ToolResult:
    """정한 요일·시각(서울)마다 요청 문장을 자동으로 실행하는 예약 작업을 만든다.
    하루 한 번까지, 켜 둔 예약은 10개까지. 예약 실행은 조회만 하고 결과를 '예약 작업' 대화방에 남긴다.
    만든 뒤 반복 주기와 다음 실행 시각을 사용자에게 말한다."""
    async with ctx.deps.db() as session:
        try:
            job = await store.add_schedule(
                session,
                ctx.deps.user_id,
                title=title,
                prompt_text=prompt_text,
                time_text=time_of_day,
                weekdays=weekdays,
                now=now_utc(),
            )
        except AutomationError as error:
            return ToolResult.failure(error.message, error_code=error.code)
        await session.commit()
    return ToolResult.success({**schedule_view(job), "saved_to": "앱 예약 작업(자동화 화면)"})


async def list_schedules(ctx: RunContext[AgentDeps]) -> ToolResult:
    """내 예약 작업 목록(꺼진 것 포함)과 각 반복 주기·다음 실행 시각."""
    async with ctx.deps.db() as session:
        rows = await store.list_schedules(session, ctx.deps.user_id)
    return ToolResult.success({"schedules": [schedule_view(job) for job in rows]})


async def delete_schedule(ctx: RunContext[AgentDeps], schedule_id: int) -> ToolResult:
    """예약 작업 하나를 지운다. schedule_id는 list_schedules 결과의 id."""
    async with ctx.deps.db() as session:
        try:
            await store.delete_schedule(session, ctx.deps.user_id, schedule_id)
        except AutomationError as error:
            return ToolResult.failure(error.message, error_code=error.code)
        await session.commit()
    return ToolResult.success({"deleted_id": schedule_id})


async def create_watch(
    ctx: RunContext[AgentDeps],
    description: WatchDescription,
    tool_name: WatchToolName,
    tool_input: WatchToolInput,
    condition_text: Condition,
    interval_minutes: Interval,
    repeat: Repeat = False,
) -> ToolResult:
    """조회 도구를 정한 간격으로 실행해 조건이 맞으면 알려 주는 감시를 만든다. 조회(READ) 도구만 쓸 수 있고 메일 도구는
    안 된다. 간격 30분 이상, 켜 둔 감시 10개까지, 30일 뒤 만료. 날짜는 실제 날짜(YYYY-MM-DD)로 넣는다.
    만든 뒤 확인 간격과 만료일을 사용자에게 말한다."""
    fields = {
        "description": description,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "condition_text": condition_text,
        "interval_minutes": interval_minutes,
        "repeat": repeat,
    }
    async with ctx.deps.db() as session:
        try:
            watch = await store.add_watch(session, ctx.deps.user_id, fields=fields, now=now_utc())
        except AutomationError as error:
            return ToolResult.failure(error.message, error_code=error.code)
        await session.commit()
    return ToolResult.success({**watch_view(watch), "saved_to": "앱 조건 감시(자동화 화면)"})


async def list_watches(ctx: RunContext[AgentDeps]) -> ToolResult:
    """내 감시 목록(꺼진 것 포함)과 각 간격·만료일."""
    async with ctx.deps.db() as session:
        rows = await store.list_watches(session, ctx.deps.user_id)
    return ToolResult.success({"watches": [watch_view(watch) for watch in rows]})


async def delete_watch(ctx: RunContext[AgentDeps], watch_id: int) -> ToolResult:
    """감시 하나를 지운다. watch_id는 list_watches 결과의 id."""
    async with ctx.deps.db() as session:
        try:
            await store.delete_watch(session, ctx.deps.user_id, watch_id)
        except AutomationError as error:
            return ToolResult.failure(error.message, error_code=error.code)
        await session.commit()
    return ToolResult.success({"deleted_id": watch_id})


# ---- 타임라인 문구 (step.label) ----

LABELS: dict[str, Callable[[dict[str, Any]], str]] = {
    "create_schedule": lambda args: f"예약 작업 '{short(args.get('title', ''))}' 만드는 중",
    "list_schedules": lambda args: "예약 작업 목록 확인하는 중",
    "delete_schedule": lambda args: f"예약 작업 #{args.get('schedule_id', '?')} 지우는 중",
    "create_watch": lambda args: f"감시 '{short(args.get('description', ''))}' 만드는 중",
    "list_watches": lambda args: "감시 목록 확인하는 중",
    "delete_watch": lambda args: f"감시 #{args.get('watch_id', '?')} 지우는 중",
}
