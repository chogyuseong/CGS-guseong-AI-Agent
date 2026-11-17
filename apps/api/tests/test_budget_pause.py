"""6단계 보강(5): 예산 부족 표시(BUILD_SPEC 6단계 완료 증거: paused_budget 또는 동등한 상태).

- 하루 비용 한도에 닿으면 감시 확인은 모델·도구를 부르지 않고 last_result.status=paused_budget,
  발생 기록은 skipped(budget).
- 예약 실행은 모델 없이 budget_exceeded로 끝난다(기존, test_automation_runs.py).
- 자동화 목록 API는 budget_paused=true를 함께 준다. 화면은 "오늘은 쉬는 중, 내일 다시"를 보여 준다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.check import AutomationServices, run_check
from app.config import Settings
from app.models import AutomationOccurrence, User, Watch
from tests.conftest import spent_today
from tests.test_automation_runs import add_watch, refreshed


async def use_up_budget(db_session: AsyncSession, user: User, settings: Settings) -> None:
    db_session.add(spent_today(user.id, Decimal(settings.daily_cost_limit_usd)))
    await db_session.commit()


async def test_watch_is_paused_by_budget(
    automation_services: AutomationServices, db_session: AsyncSession, logged_in_user: User, test_settings: Settings
) -> None:
    await use_up_budget(db_session, logged_in_user, test_settings)
    now = datetime.now(UTC)
    watch = await add_watch(db_session, logged_in_user, now)

    await run_check(automation_services, now)

    watch = await refreshed(db_session, Watch, watch.id)
    assert watch.last_result is not None and watch.last_result["status"] == "paused_budget"
    assert watch.next_run_at == now + timedelta(minutes=watch.interval_minutes)
    occurrence = await db_session.scalar(select(AutomationOccurrence).where(AutomationOccurrence.source_id == watch.id))
    assert occurrence is not None and (occurrence.status, occurrence.skip_reason) == ("skipped", "budget")


async def test_automation_lists_report_budget_pause(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User, test_settings: Settings
) -> None:
    assert (await client.get("/api/schedules")).json()["budget_paused"] is False
    assert (await client.get("/api/watches")).json()["budget_paused"] is False

    await use_up_budget(db_session, logged_in_user, test_settings)

    assert (await client.get("/api/schedules")).json()["budget_paused"] is True
    assert (await client.get("/api/watches")).json()["budget_paused"] is True
