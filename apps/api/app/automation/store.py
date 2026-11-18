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

진입점: app/tools/automation.py(create_·list_·delete_schedule/watch, 만들기는 채팅 도구로만),
app/routers/automations.py(목록, PATCH 켜기·끄기, DELETE, PUT 아침 브리핑), check.py('예약 작업' 대화방).
근거: BUILD_SPEC 8절, DECISIONS 2026-11-17 "6단계"(중복 거절, 서버 제한, 다시 켤 때 같은 제한)와
"6단계 보강"(브리핑은 사용자가 켤 때만, 숫자 조건, 단발성 날짜 만료).

변경 이력:
- 2026-11-17 6단계: 예약·감시 만들기·켜기·끄기·지우기, '예약 작업' 대화방.
- 2026-11-17 6단계 보강: 아침 브리핑 자동 생성을 없애고 enable_briefing(사용자가 켤 때만), 숫자 조건(check_condition),
  조회 인자 date가 있는 감시는 그 날이 끝나면 만료(watch_expiry).

고칠 때 주의:
- 모든 조회·수정에 user_id 조건을 건다. 남의 id는 "없음(not_found)"으로 답해 있는지조차 드러내지 않는다.
- 제한 값은 rules.py가 정본이다. 오류 code를 더하면 routers/automations.py의 http_error(404/409)와
  AutomationErrorOut 설명, 화면 문구를 확인한다. message는 모델·화면에 그대로 보인다.
- 테스트: tests/test_automation_tools.py, test_automation_api.py, test_briefing_consent.py, test_watch_conditions.py.
"""

from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation import conditions, rules
from app.automation.watchable import WatchToolError, check_watch_tool
from app.models import Conversation, ScheduledJob, User, Watch


class AutomationError(Exception):
    """code: not_found | limit_reached | duplicate | expired | invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# 라우터가 이 code만 404로, 나머지는 409로 바꾼다(routers/automations.py http_error).
NOT_FOUND = "not_found"


async def lock_user(session: AsyncSession, user_id: int) -> None:
    """사용자 행을 FOR UPDATE로 잠근다(커밋까지 유지). 같은 사용자의 만들기·켜기를 한 줄로 세운다.

    잠그지 않으면 동시에 온 두 요청이 둘 다 "9개라 괜찮다"를 보고 11개가 되거나 같은 감시가 두 개 생긴다.
    """
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())


# ---- 예약 작업 ----


async def active_schedule_count(session: AsyncSession, user_id: int) -> int:
    query = select(func.count()).where(ScheduledJob.user_id == user_id, ScheduledJob.is_active.is_(True))
    return await session.scalar(query.select_from(ScheduledJob)) or 0


async def find_same_schedule(
    session: AsyncSession, user_id: int, prompt_text: str, time_of_day: time, weekdays: list[int]
) -> ScheduledJob | None:
    """같은 시각·같은 요일 집합·같은 프롬프트(공백·대소문자 무시)인 예약. 꺼진 것도 찾는다.

    weekdays는 clean_weekdays로 정렬된 값이어야 한다(비교가 정렬된 목록끼리다).
    """
    rows = await session.scalars(
        select(ScheduledJob).where(ScheduledJob.user_id == user_id, ScheduledJob.time_of_day == time_of_day)
    )
    key = rules.same_text_key(prompt_text)
    return next((r for r in rows if sorted(r.weekdays) == weekdays and rules.same_text_key(r.prompt_text) == key), None)


async def ensure_schedule_room(session: AsyncSession, user_id: int) -> None:
    """켜진 예약이 상한(10개)이면 limit_reached. lock_user 뒤에 불러야 동시 요청에서도 맞다."""
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
    """예약 작업을 만든다. 시각은 서울 기준 'HH:MM', 요일은 0(월)~6(일).

    순서: 형식 검사 → 사용자 잠금 → 중복 → 상한 → 다음 실행 시각 계산. 실패는 AutomationError(아무것도 쓰지 않음).
    flush만 하고 커밋은 도구가 한다. 하루 1회 제한은 "시각 하나 + 요일 목록" 구조로 지켜진다.
    """
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
    """내 예약 하나. 없거나 남의 것이면 not_found. 세션 캐시 대신 최신 값을 읽는다."""
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
    """예약을 지운다. 이미 잡힌 실행은 끝까지 돌고, 그 task와 결과 메시지는 대화방에 남는다(DECISIONS 6단계 보강)."""
    await session.delete(await owned_schedule(session, user_id, job_id))
    await session.flush()


async def list_schedules(session: AsyncSession, user_id: int) -> list[ScheduledJob]:
    """내 예약 전부(꺼진 것 포함), 시각 순."""
    rows = await session.scalars(
        select(ScheduledJob).where(ScheduledJob.user_id == user_id).order_by(ScheduledJob.time_of_day, ScheduledJob.id)
    )
    return list(rows)


# ---- 감시 ----


async def active_watches(session: AsyncSession, user_id: int) -> list[Watch]:
    """켜진 감시(상한·중복 검사용). 만료됐지만 1분 검사가 아직 끄지 않은 것도 켜진 것으로 센다."""
    rows = await session.scalars(select(Watch).where(Watch.user_id == user_id, Watch.is_active.is_(True)))
    return list(rows)


def same_watch(watch: Watch, tool_name: str, tool_input: dict[str, Any], condition_text: str) -> bool:
    """같은 도구 + 같은 입력(키 순서 무시) + 같은 조건 문장(공백·대소문자 무시).

    숫자 조건(condition_spec)은 비교하지 않는다(조건 문장이 같으면 같은 감시로 본다).
    """
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
    """감시 간격 30~1,440분. 도구 인자 검증(Interval)과 겹치지만 서버 제한은 여기서 한 번 더 강제한다."""
    low, high = rules.MIN_WATCH_INTERVAL_MINUTES, rules.MAX_WATCH_INTERVAL_MINUTES
    if not low <= interval_minutes <= high:
        raise AutomationError("invalid", f"감시 간격은 {low}분 이상 {high}분(하루) 이하여야 합니다.")


def check_condition(fields: dict[str, Any]) -> None:
    """6단계 보강: condition_spec이 있으면 숫자 조건(서버 비교), 없으면 문장 조건(판정 모델)."""
    spec = fields.get("condition_spec")
    if spec is None:
        fields["condition_type"] = conditions.TEXT
        return
    problem = conditions.number_spec_problem(spec)
    if problem is not None:
        raise AutomationError("invalid", problem)
    fields["condition_type"] = conditions.NUMBER


def watch_expiry(tool_input: dict[str, Any], now: datetime) -> datetime:
    """기본 30일 뒤. 조회 인자에 date가 있으면 만들 때의 실제 날짜로 고정하고 그 날이 끝나면(서울 자정) 만료한다.

    '내일 비 오면'의 내일이 매번 다음 날로 밀리지 않게 한다(BUILD_SPEC 8.2).
    """
    lifetime = now + rules.WATCH_LIFETIME
    if "date" not in tool_input:
        return lifetime
    day = rules.parse_date(tool_input["date"])
    if day is None:
        raise AutomationError(
            "invalid",
            "조회 인자의 날짜는 'YYYY-MM-DD' 실제 날짜여야 합니다. '내일'은 오늘 기준 실제 날짜로 바꿔 넣습니다.",
        )
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=rules.SEOUL)
    if end <= now:
        raise AutomationError("invalid", "이미 지난 날짜로는 감시를 만들 수 없습니다.")
    return min(lifetime, end)


async def add_watch(session: AsyncSession, user_id: int, *, fields: dict[str, Any], now: datetime) -> Watch:
    """감시를 만든다. fields: description, tool_name, tool_input, condition_text, interval_minutes, repeat,
    condition_spec(숫자 조건, 없으면 None).

    첫 확인은 다음 1분 검사 때(next_run_at=now), 만료는 30일 뒤(명세 기본값) 또는 고정 날짜가 끝날 때.
    """
    check_interval(fields["interval_minutes"])
    check_condition(fields)
    expires_at = watch_expiry(fields["tool_input"], now)
    try:
        check_watch_tool(fields["tool_name"], fields["tool_input"])
    except WatchToolError as error:
        raise AutomationError("invalid", error.message) from None
    await lock_user(session, user_id)
    await ensure_watch_allowed(session, user_id, fields["tool_name"], fields["tool_input"], fields["condition_text"])
    watch = Watch(user_id=user_id, **fields, next_run_at=now, expires_at=expires_at, is_active=True)
    session.add(watch)
    await session.flush()
    return watch


async def owned_watch(session: AsyncSession, user_id: int, watch_id: int) -> Watch:
    """내 감시 하나. 없거나 남의 것이면 not_found."""
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
    """감시를 지운다. 확인 도중이면 check.finish_watch가 행이 없음을 보고 결과를 저장하지 않고 알리지 않는다."""
    await session.delete(await owned_watch(session, user_id, watch_id))
    await session.flush()


async def list_watches(session: AsyncSession, user_id: int) -> list[Watch]:
    """내 감시 전부(꺼진·만료된 것 포함), 만든 순."""
    rows = await session.scalars(select(Watch).where(Watch.user_id == user_id).order_by(Watch.created_at, Watch.id))
    return list(rows)


# ---- '예약 작업' 대화방과 아침 브리핑 ----


async def automation_conversation(session: AsyncSession, user_id: int) -> Conversation:
    """사용자의 '예약 작업' 대화방(가장 먼저 만든 것). 없으면 만든다(flush만, 커밋은 부른 쪽).

    별도 컬럼 없이 제목으로 찾는다(DECISIONS 2026-11-17). 그래서 사용자가 이 제목으로 대화를 만들지 못하게
    schemas/conversations.py가 막는다. 제목을 바꾸면 기존 대화방을 못 찾아 새로 만든다.
    """
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
    """내 아침 브리핑(kind='briefing', 가장 먼저 만든 것). 없으면 None."""
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
