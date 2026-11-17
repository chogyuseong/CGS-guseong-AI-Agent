"""6단계 보강(2): 예약·감시 발생 기록 automation_occurrences (BUILD_SPEC 8.1, 데이터 모델).

- (kind, source_id, scheduled_for)가 DB에서 유일하다. 같은 예정 시각은 몇 번 시작해도 발생 한 건, task 한 개.
- 실행했으면 status=started(+task_id), 건너뛰었으면 status=skipped와 이유(skip_reason)를 남긴다.
- 서버가 꺼져 있던 동안의 예약은 유예 시간(2시간) 안의 마지막 1건만 실행하고, 더 오래된 것은 건너뛴 이유를 남긴다.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.agent import MyAgent
from app.automation import rules
from app.automation.check import AutomationServices, run_check
from app.automation.retention import prune_expired_records
from app.config import Settings
from app.models import AutomationOccurrence, ScheduledJob, User
from tests.model_scripts import scripted, text
from tests.test_automation_runs import add_job, add_watch, tasks_of


async def occurrences(db_session: AsyncSession, kind: str, source_id: int) -> list[AutomationOccurrence]:
    rows = await db_session.scalars(
        select(AutomationOccurrence)
        .where(AutomationOccurrence.kind == kind, AutomationOccurrence.source_id == source_id)
        .order_by(AutomationOccurrence.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_schedule_run_records_one_occurrence_even_if_started_twice(
    automation_services: AutomationServices, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    due = now - timedelta(minutes=1)
    job = await add_job(db_session, logged_in_user, due)

    with agent.override(model=scripted(text("요약")).model()):
        await run_check(automation_services, now)
        await automation_services.launcher.start_schedule(job.id, due, now)

    [occurrence] = await occurrences(db_session, "schedule", job.id)
    [task] = await tasks_of(db_session, logged_in_user)
    assert occurrence.scheduled_for == due
    assert (occurrence.status, occurrence.skip_reason, occurrence.task_id) == ("started", None, task.id)
    assert occurrence.user_id == logged_in_user.id


async def test_long_missed_schedule_is_skipped_with_reason(
    automation_services: AutomationServices, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    missed = now - timedelta(days=3)
    job = await add_job(db_session, logged_in_user, missed)

    with agent.override(model=scripted(text("요약")).model()):
        await run_check(automation_services, now)

    [occurrence] = await occurrences(db_session, "schedule", job.id)
    assert (occurrence.status, occurrence.skip_reason, occurrence.task_id) == ("skipped", "missed", None)
    assert occurrence.scheduled_for == missed
    assert await tasks_of(db_session, logged_in_user) == []


async def test_watch_check_records_one_occurrence(
    automation_services: AutomationServices, agent: MyAgent, db_session: AsyncSession, logged_in_user: User
) -> None:
    now = datetime.now(UTC)
    watch = await add_watch(db_session, logged_in_user, now, tool_name="list_todos", condition_text="할 일이 있다")
    due = watch.next_run_at

    with agent.override(model=scripted(text("x")).model()):
        await run_check(automation_services, now)
        await automation_services.launcher.start_watch(watch.id, due, now)

    [occurrence] = await occurrences(db_session, "watch", watch.id)
    assert occurrence.scheduled_for == due and occurrence.status == "started" and occurrence.task_id is None


async def test_schedule_keeps_its_timezone(db_session: AsyncSession, logged_in_user: User) -> None:
    job = await add_job(db_session, logged_in_user, datetime.now(UTC) + timedelta(hours=1))

    row = await db_session.get(ScheduledJob, job.id)

    assert row is not None and row.timezone == rules.DEFAULT_TIMEZONE == "Asia/Seoul"


async def test_old_occurrences_are_pruned_with_execution_records(
    automation_services: AutomationServices,
    agent: MyAgent,
    db_session: AsyncSession,
    logged_in_user: User,
    session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
) -> None:
    now = datetime.now(UTC)
    job = await add_job(db_session, logged_in_user, now - timedelta(days=3))
    with agent.override(model=scripted(text("요약")).model()):
        await run_check(automation_services, now)
    assert await occurrences(db_session, "schedule", job.id)

    await prune_expired_records(session_factory, test_settings, now + timedelta(days=8))

    assert await occurrences(db_session, "schedule", job.id) == []
