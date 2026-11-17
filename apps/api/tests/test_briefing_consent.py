"""6단계 보강(1): 아침 브리핑은 사용자가 켠 뒤에만 실행한다(BUILD_SPEC 8.1).

PUT /api/schedules/briefing {"time_of_day": "HH:MM"}: 없으면 만들고, 있으면 시각을 바꾸고 켠다(브리핑은 한 개).
끄기·지우기는 다른 예약과 같은 PATCH·DELETE. 계정 생성과 1분 검사는 브리핑을 만들지 않는다.
"""

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation import rules
from app.models import ScheduledJob, User
from tests.test_automation_api import add_job


async def put_briefing(client: httpx.AsyncClient, time_of_day: str) -> httpx.Response:
    return await client.put("/api/schedules/briefing", json={"time_of_day": time_of_day})


async def test_enable_briefing_creates_one_daily_job_at_chosen_time(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession
) -> None:
    first = await put_briefing(client, "07:30")
    second = await put_briefing(client, "08:10")

    assert first.status_code == 200, first.text
    assert second.status_code == 200
    body = second.json()
    assert body["is_briefing"] is True and body["is_active"] is True
    assert body["repeat_text"] == "매일 08:10"
    jobs = (await db_session.scalars(select(ScheduledJob).where(ScheduledJob.user_id == logged_in_user.id))).all()
    assert len(jobs) == 1 and jobs[0].kind == "briefing"
    local = jobs[0].next_run_at.astimezone(rules.SEOUL)
    assert (local.hour, local.minute) == (8, 10)


async def test_turning_briefing_back_on_reactivates_same_job(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession
) -> None:
    job_id = (await put_briefing(client, "07:30")).json()["id"]
    assert (await client.patch(f"/api/schedules/{job_id}", json={"is_active": False})).status_code == 200

    again = await put_briefing(client, "07:30")

    assert again.json()["id"] == job_id and again.json()["is_active"] is True


async def test_enable_briefing_rejects_bad_time_and_full_limit(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession
) -> None:
    assert (await put_briefing(client, "25:00")).status_code == 422
    for index in range(rules.MAX_ACTIVE_SCHEDULES):
        await add_job(db_session, logged_in_user, title=f"예약 {index}", prompt_text=f"요약 {index}")

    full = await put_briefing(client, "07:30")

    assert full.status_code == 409
    assert full.json()["detail"]["code"] == "limit_reached"


async def test_enable_briefing_requires_login_and_origin(client: httpx.AsyncClient) -> None:
    assert (await put_briefing(client, "07:30")).status_code in (401, 403)
