"""예약 작업·감시를 만들고, 켜고 끄고, 지우는 DB 작업. 서버 제한은 모두 여기서 강제한다.

자동화 도구(app/tools/automation.py)와 화면 API(app/routers/automations.py)가 같은 함수를 쓰므로,
채팅으로 만들든 화면에서 다시 켜든 같은 제한이 걸린다.

- 만들기·켜기 전에 사용자 행을 FOR UPDATE로 잠근다. 같은 사용자의 요청이 동시에 와도 활성 개수 검사와 중복 검사가
  한 줄로 서서 상한(10개)을 넘거나 같은 것이 두 개 생기지 않는다.
- 중복(Django판에서 같은 감시를 두 번 만든 버그):
  예약 = 같은 시각 + 같은 요일 + 같은 프롬프트(공백·대소문자 무시). 꺼진 것도 같은 것으로 본다(켜라고 안내).
  감시 = 켜진 감시 중 같은 도구 + 같은 입력 + 같은 조건(공백·대소문자 무시).
- 실패는 AutomationError(code, 한국어 message). 도구는 ToolResult.failure로, 화면 API는 404·409로 바꾼다.
- 커밋은 호출한 쪽이 한다.
"""

from datetime import datetime, time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation import rules
from app.automation.watchable import WatchToolError, check_watch_tool
from app.models import Conversation, ScheduledJob, User, Watch


class AutomationError(Exception):
    """code: not_found | limit_reached | duplicate | expired | invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


NOT_FOUND = "not_found"


async def lock_user(session: AsyncSession, user_id: int) -> None:
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())


# ---- 예약 작업 ----


async def active_schedule_count(session: AsyncSession, user_id: int) -> int:
    query = select(func.count()).where(ScheduledJob.user_id == user_id, ScheduledJob.is_active.is_(True))
    return await session.scalar(query.select_from(ScheduledJob)) or 0


async def find_same_schedule(
    session: AsyncSession, user_id: int, prompt_text: str, time_of_day: time, weekdays: list[int]
) -> ScheduledJob | None:
    rows = await session.scalars(
        select(ScheduledJob).where(ScheduledJob.user_id == user_id, ScheduledJob.time_of_day == time_of_day)
    )
    key = rules.same_text_key(prompt_text)
    return next((r for r in rows if sorted(r.weekdays) == weekdays and rules.same_text_key(r.prompt_text) == key), None)


async def ensure_schedule_room(session: AsyncSession, user_id: int) -> None:
    if await active_schedule_count(session, user_id) >= rules.MAX_ACTIVE_SCHEDULES:
        raise AutomationError(
            "limit_reached",
            f"켜 둔 예약 작업은 {rules.MAX_ACTIVE_SCHEDULES}개까지입니다. "
            "자동화 화면에서 필요 없는 예약을 끄거나 지워 주세요.",
        )


async def add_schedule(
    session: AsyncSession,
    user_id: int,
    *,
    title: str,
    prompt_text: str,
    time_text: str,
    weekdays: list[int],
    now: datetime,
) -> ScheduledJob:
    """예약 작업을 만든다. 시각은 서울 기준 'HH:MM', 요일은 0(월)~6(일)."""
    time_of_day, days = rules.parse_time_of_day(time_text), rules.clean_weekdays(weekdays)
    if time_of_day is None:
        raise AutomationError("invalid", "시각은 24시간 'HH:MM' 형식이어야 합니다. 예: 21:00")
    if days is None:
        raise AutomationError("invalid", "요일은 0(월)~6(일) 숫자를 하나 이상 넣어야 합니다. 매일이면 0~6 전부.")
    await lock_user(session, user_id)
    same = await find_same_schedule(session, user_id, prompt_text, time_of_day, days)
    if same is not None:
        state = "켜져" if same.is_active else "꺼진 채로"
        raise AutomationError("duplicate", f"같은 예약 작업(#{same.id} '{same.title}')이 이미 {state} 있습니다.")
    await ensure_schedule_room(session, user_id)
    job = ScheduledJob(
        user_id=user_id,
        title=title,
        prompt_text=prompt_text,
        time_of_day=time_of_day,
        weekdays=days,
        next_run_at=rules.next_schedule_run(time_of_day, days, now),
        is_active=True,
    )
    session.add(job)
    await session.flush()
    return job


async def owned_schedule(session: AsyncSession, user_id: int, job_id: int) -> ScheduledJob:
    job = await session.scalar(
        select(ScheduledJob)
        .where(ScheduledJob.id == job_id, ScheduledJob.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise AutomationError(NOT_FOUND, f"id가 {job_id}인 예약 작업을 찾을 수 없습니다.")
    return job


async def set_schedule_active(
    session: AsyncSession, user_id: int, job_id: int, *, active: bool, now: datetime
) -> ScheduledJob:
    """켜기·끄기. 켤 때는 다음 시각을 지금부터 다시 계산한다(꺼져 있던 동안의 실행을 몰아 돌리지 않는다)."""
    await lock_user(session, user_id)
    job = await owned_schedule(session, user_id, job_id)
    if active and not job.is_active:
        await ensure_schedule_room(session, user_id)
        job.next_run_at = rules.next_schedule_run(job.time_of_day, job.weekdays, now, job.timezone)
    job.is_active = active
    await session.flush()
    return job


async def delete_schedule(session: AsyncSession, user_id: int, job_id: int) -> None:
    await session.delete(await owned_schedule(session, user_id, job_id))
    await session.flush()


async def list_schedules(session: AsyncSession, user_id: int) -> list[ScheduledJob]:
    rows = await session.scalars(
        select(ScheduledJob).where(ScheduledJob.user_id == user_id).order_by(ScheduledJob.time_of_day, ScheduledJob.id)
    )
    return list(rows)


# ---- 감시 ----


async def active_watches(session: AsyncSession, user_id: int) -> list[Watch]:
    rows = await session.scalars(select(Watch).where(Watch.user_id == user_id, Watch.is_active.is_(True)))
    return list(rows)


def same_watch(watch: Watch, tool_name: str, tool_input: dict[str, Any], condition_text: str) -> bool:
    return (
        watch.tool_name == tool_name
        and rules.input_key(watch.tool_input) == rules.input_key(tool_input)
        and rules.same_text_key(watch.condition_text) == rules.same_text_key(condition_text)
    )


async def ensure_watch_allowed(
    session: AsyncSession,
    user_id: int,
    tool_name: str,
    tool_input: dict[str, Any],
    condition_text: str,
    *,
    skip_id: int = 0,
) -> None:
    """활성 개수(10개)와 중복을 검사한다. skip_id는 다시 켜는 감시 자신."""
    others = [w for w in await active_watches(session, user_id) if w.id != skip_id]
    same = next((w for w in others if same_watch(w, tool_name, tool_input, condition_text)), None)
    if same is not None:
        raise AutomationError("duplicate", f"같은 감시(#{same.id} '{same.description}')가 이미 켜져 있습니다.")
    if len(others) >= rules.MAX_ACTIVE_WATCHES:
        raise AutomationError(
            "limit_reached",
            f"켜 둔 감시는 {rules.MAX_ACTIVE_WATCHES}개까지입니다. "
            "자동화 화면에서 필요 없는 감시를 끄거나 지워 주세요.",
        )


def check_interval(interval_minutes: int) -> None:
    low, high = rules.MIN_WATCH_INTERVAL_MINUTES, rules.MAX_WATCH_INTERVAL_MINUTES
    if not low <= interval_minutes <= high:
        raise AutomationError("invalid", f"감시 간격은 {low}분 이상 {high}분(하루) 이하여야 합니다.")


async def add_watch(session: AsyncSession, user_id: int, *, fields: dict[str, Any], now: datetime) -> Watch:
    """감시를 만든다. fields: description, tool_name, tool_input, condition_text, interval_minutes, repeat.

    첫 확인은 다음 1분 검사 때(next_run_at=now), 만료는 30일 뒤(명세 기본값).
    """
    check_interval(fields["interval_minutes"])
    try:
        check_watch_tool(fields["tool_name"], fields["tool_input"])
    except WatchToolError as error:
        raise AutomationError("invalid", error.message) from None
    await lock_user(session, user_id)
    await ensure_watch_allowed(session, user_id, fields["tool_name"], fields["tool_input"], fields["condition_text"])
    watch = Watch(user_id=user_id, **fields, next_run_at=now, expires_at=now + rules.WATCH_LIFETIME, is_active=True)
    session.add(watch)
    await session.flush()
    return watch


async def owned_watch(session: AsyncSession, user_id: int, watch_id: int) -> Watch:
    watch = await session.scalar(
        select(Watch).where(Watch.id == watch_id, Watch.user_id == user_id).execution_options(populate_existing=True)
    )
    if watch is None:
        raise AutomationError(NOT_FOUND, f"id가 {watch_id}인 감시를 찾을 수 없습니다.")
    return watch


async def set_watch_active(session: AsyncSession, user_id: int, watch_id: int, *, active: bool, now: datetime) -> Watch:
    """켜기·끄기. 만료된 감시는 다시 켤 수 없다(새로 만든다). 켜면 다음 검사 때 바로 확인한다."""
    await lock_user(session, user_id)
    watch = await owned_watch(session, user_id, watch_id)
    if active and not watch.is_active:
        if watch.expires_at <= now:
            raise AutomationError("expired", "만료된 감시는 다시 켤 수 없습니다. 채팅에서 새로 만들어 주세요.")
        await ensure_watch_allowed(
            session, user_id, watch.tool_name, watch.tool_input, watch.condition_text, skip_id=watch.id
        )
        watch.next_run_at = now
    watch.is_active = active
    await session.flush()
    return watch


async def delete_watch(session: AsyncSession, user_id: int, watch_id: int) -> None:
    await session.delete(await owned_watch(session, user_id, watch_id))
    await session.flush()


async def list_watches(session: AsyncSession, user_id: int) -> list[Watch]:
    rows = await session.scalars(select(Watch).where(Watch.user_id == user_id).order_by(Watch.created_at, Watch.id))
    return list(rows)


# ---- '예약 작업' 대화방과 아침 브리핑 ----


async def automation_conversation(session: AsyncSession, user_id: int) -> Conversation:
    """사용자의 '예약 작업' 대화방(가장 먼저 만든 것). 없으면 만든다."""
    found = await session.scalar(
        select(Conversation)
        .where(Conversation.user_id == user_id, Conversation.title == rules.AUTOMATION_CONVERSATION_TITLE)
        .order_by(Conversation.id)
        .limit(1)
    )
    if found is not None:
        return found
    conversation = Conversation(user_id=user_id, title=rules.AUTOMATION_CONVERSATION_TITLE)
    session.add(conversation)
    await session.flush()
    return conversation


async def find_briefing(session: AsyncSession, user_id: int) -> ScheduledJob | None:
    return await session.scalar(
        select(ScheduledJob)
        .where(ScheduledJob.user_id == user_id, ScheduledJob.kind == rules.BRIEFING_KIND)
        .order_by(ScheduledJob.id)
        .limit(1)
    )


async def enable_briefing(session: AsyncSession, user_id: int, *, time_text: str, now: datetime) -> ScheduledJob:
    """6단계 보강: 사용자가 켠 아침 브리핑(매일, 서울). 있으면 시각을 바꾸고 켜고, 없으면 만든다(사용자당 하나).

    계정 생성·1분 검사는 브리핑을 만들지 않는다(BUILD_SPEC 8.1: 계정 생성만으로 유료 실행을 시작하지 않는다).
    """
    time_of_day = rules.parse_time_of_day(time_text)
    if time_of_day is None:
        raise AutomationError("invalid", "시각은 24시간 'HH:MM' 형식이어야 합니다. 예: 07:30")
    await lock_user(session, user_id)
    job = await find_briefing(session, user_id)
    if job is None or not job.is_active:
        await ensure_schedule_room(session, user_id)
    days: list[int] = list(rules.EVERY_DAY)
    if job is None:
        job = ScheduledJob(
            user_id=user_id,
            kind=rules.BRIEFING_KIND,
            title=rules.BRIEFING_TITLE,
            prompt_text=rules.BRIEFING_PROMPT,
            weekdays=days,
        )
        session.add(job)
    job.time_of_day = time_of_day
    job.weekdays = days
    job.is_active = True
    job.next_run_at = rules.next_schedule_run(time_of_day, days, now, job.timezone or rules.DEFAULT_TIMEZONE)
    await session.flush()
    return job
