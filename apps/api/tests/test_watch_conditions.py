"""6단계 보강(3): 감시 판정(BUILD_SPEC 8.2).

- 숫자 조건(condition_type=number)은 서버가 비교한다. 판정 모델을 부르지 않는다(비용 없음).
  값이 목록이면 그 개수로 비교한다(예: 할 일 목록의 개수).
- 조회 실패·판정 실패·값 없음은 '조건 아님'이 아니라 unknown(이유와 함께). 알리지 않는다.
- 다시 알림: 조건이 거짓→참으로 바뀔 때만, 마지막 알림에서 12시간 이상 지났을 때만. 계속 참이면 다시 알리지 않는다.
- 단발성 날짜: 조회 인자의 date는 만들 때의 실제 날짜(YYYY-MM-DD)로 고정하고, 그 날이 끝나면 감시가 만료된다.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.agent.results import ToolResult
from app.automation import conditions, rules
from app.automation.check import AutomationServices, run_check
from app.automation.watch import WatchAgent
from app.models import Conversation, Message, Todo, UsageLog, User, Watch
from tests.test_automation_runs import add_watch, broken_verdict_model, refreshed, verdict_model
from tests.test_automation_tools import create_watch

AT_LEAST_TWO = {"field": "todos", "op": ">=", "value": 2}


async def notices(db_session: AsyncSession, user: User) -> list[Message]:
    rows = await db_session.scalars(
        select(Message).join(Conversation).where(Conversation.user_id == user.id).order_by(Message.id)
    )
    return list(rows)


async def set_todos(db_session: AsyncSession, user: User, count: int) -> None:
    for todo in await db_session.scalars(select(Todo).where(Todo.user_id == user.id)):
        await db_session.delete(todo)
    db_session.add_all([Todo(user_id=user.id, title=f"할 일 {i}") for i in range(count)])
    await db_session.commit()


async def number_watch(db_session: AsyncSession, user: User, now: datetime, **overrides: Any) -> Watch:
    return await add_watch(db_session, user, now, condition_type="number", condition_spec=AT_LEAST_TWO, **overrides)


async def test_number_condition_is_compared_on_server_without_model(
    automation_services: AutomationServices, watch_agent: WatchAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    await set_todos(db_session, logged_in_user, 2)
    now = datetime.now(UTC)
    watch = await number_watch(db_session, logged_in_user, now)
    seen: list[list[ModelMessage]] = []

    with watch_agent.override(model=verdict_model(False, "모델이 부르면 안 됨", seen)):
        await run_check(automation_services, now)

    assert seen == []
    watch = await refreshed(db_session, Watch, watch.id)
    assert watch.last_result is not None and watch.last_result["status"] == "met"
    assert "2" in watch.last_result["summary"]
    assert watch.is_active is False  # repeat=false: 한 번 알리고 끈다
    assert len(await notices(db_session, logged_in_user)) == 1
    usage = await db_session.scalar(
        select(func.count()).select_from(UsageLog).where(UsageLog.user_id == logged_in_user.id)
    )
    assert usage == 0


async def test_number_condition_not_met(
    automation_services: AutomationServices, db_session: AsyncSession, logged_in_user: User
) -> None:
    await set_todos(db_session, logged_in_user, 1)
    now = datetime.now(UTC)
    watch = await number_watch(db_session, logged_in_user, now)

    await run_check(automation_services, now)

    watch = await refreshed(db_session, Watch, watch.id)
    assert watch.last_result is not None and watch.last_result["status"] == "not_met"
    assert watch.last_known_met is False and watch.is_active is True


async def test_missing_value_is_unknown_not_false(
    automation_services: AutomationServices, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    watch = await add_watch(
        db_session,
        logged_in_user,
        now,
        condition_type="number",
        condition_spec={"field": "nothing_here", "op": ">", "value": 1},
    )

    await run_check(automation_services, now)

    watch = await refreshed(db_session, Watch, watch.id)
    assert watch.last_result is not None
    assert (watch.last_result["status"], watch.last_result["reason"]) == ("unknown", "value_missing")
    assert watch.last_known_met is None and watch.is_active is True
    assert await notices(db_session, logged_in_user) == []


async def test_judge_failure_is_unknown(
    automation_services: AutomationServices, watch_agent: WatchAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    watch = await add_watch(db_session, logged_in_user, now)

    with watch_agent.override(model=broken_verdict_model()):
        await run_check(automation_services, now)

    watch = await refreshed(db_session, Watch, watch.id)
    assert watch.last_result is not None
    assert (watch.last_result["status"], watch.last_result["reason"]) == ("unknown", "judge_failed")


async def test_tool_failure_is_unknown(
    automation_services: AutomationServices, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    watch = await add_watch(
        db_session, logged_in_user, now, tool_name="get_weather", tool_input={"place": "서울", "date": "2026-11-18"}
    )

    await run_check(automation_services, now)  # 테스트 설정에는 카카오 키가 없어 조회가 실패한다

    watch = await refreshed(db_session, Watch, watch.id)
    assert watch.last_result is not None
    assert (watch.last_result["status"], watch.last_result["reason"]) == ("unknown", "tool_failed")


async def test_repeat_notifies_only_on_change_and_after_12_hours(
    automation_services: AutomationServices, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    watch = await number_watch(db_session, logged_in_user, now, repeat=True, interval_minutes=60)

    async def check_at(hours: float, todos: int) -> None:
        await set_todos(db_session, logged_in_user, todos)
        moment = now + timedelta(hours=hours)
        row = await refreshed(db_session, Watch, watch.id)
        row.next_run_at = moment - timedelta(minutes=1)
        await db_session.commit()
        await run_check(automation_services, moment)

    await check_at(0, 2)  # 참 → 알림 1
    await check_at(1, 3)  # 계속 참 → 다시 알리지 않음
    await check_at(2, 0)  # 거짓
    await check_at(3, 2)  # 다시 참이지만 12시간이 안 됨 → 알리지 않음
    await check_at(4, 0)
    await check_at(13, 2)  # 거짓 → 참, 12시간 지남 → 알림 2

    assert len(await notices(db_session, logged_in_user)) == 2
    assert (await refreshed(db_session, Watch, watch.id)).is_active is True


# ---- 만들 때 검사(도구) ----


async def test_create_watch_with_number_condition(tool_deps: AgentDeps, db_session: AsyncSession) -> None:
    result = await create_watch(
        tool_deps,
        tool_name="list_todos",
        tool_input={},
        condition_text="할 일이 2개 이상",
        number_field="todos",
        number_op=">=",
        number_value=2,
    )

    assert result.ok, result.error
    watch = await db_session.scalar(select(Watch).where(Watch.user_id == tool_deps.user_id))
    assert watch is not None
    assert (watch.condition_type, watch.condition_spec) == ("number", AT_LEAST_TWO)


async def test_create_watch_rejects_partial_number_condition(tool_deps: AgentDeps) -> None:
    result = await create_watch(tool_deps, tool_name="list_todos", tool_input={}, number_field="todos")

    assert result.ok is False and "숫자 조건" in (result.error or "")


@pytest.mark.parametrize("date", ["내일", "2026-13-01", "2020-01-01"])
async def test_create_watch_rejects_relative_or_past_dates(tool_deps: AgentDeps, date: str) -> None:
    result = await create_watch(tool_deps, tool_input={"place": "서울", "date": date})

    assert result.ok is False and "날짜" in (result.error or "")


async def test_one_shot_date_is_fixed_and_expires_after_that_day(
    tool_deps: AgentDeps, db_session: AsyncSession
) -> None:
    day = (datetime.now(rules.SEOUL) + timedelta(days=1)).date()

    result = await create_watch(tool_deps, tool_input={"place": "서울", "date": day.isoformat()})

    assert result.ok, result.error
    watch = await db_session.scalar(select(Watch).where(Watch.user_id == tool_deps.user_id))
    assert watch is not None and watch.tool_input["date"] == day.isoformat()
    local = watch.expires_at.astimezone(rules.SEOUL)
    assert (local.date(), local.hour, local.minute) == (day + timedelta(days=1), 0, 0)


def test_read_number_from_wrapped_weather_result() -> None:
    result = ToolResult.external({"date": "2026-11-18", "precipitation_probability_max": 70}, sources=[])

    assert conditions.read_number(result, "precipitation_probability_max") == 70
    assert conditions.read_number(result, "date") is None
    assert conditions.read_number(ToolResult.success({"todos": [1, 2, 3]}), "todos") == 3
