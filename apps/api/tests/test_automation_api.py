"""6단계 자동화 화면용 API: 목록, 켜기/끄기, 삭제, 마지막 실행 결과.

GET/PATCH/DELETE /api/schedules, /api/watches. 모든 조회·수정에 user_id 조건(남의 것은 404), 상태 변경은 Origin 검사.
"""

from datetime import UTC, datetime, time, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation import rules
from app.models import Conversation, ScheduledJob, Task, User, Watch
from app.models.enums import TaskOrigin, TaskStatus
from tests.conftest import UserFactory

EVERY_DAY = list(range(7))


async def add_job(db_session: AsyncSession, user: User, **overrides: Any) -> ScheduledJob:
    values: dict[str, Any] = {
        "user_id": user.id,
        "title": "할 일 요약",
        "prompt_text": "오늘 할 일 요약해줘",
        "time_of_day": time(9, 0),
        "weekdays": EVERY_DAY,
        "next_run_at": datetime.now(UTC) + timedelta(hours=1),
    }
    values.update(overrides)
    job = ScheduledJob(**values)
    db_session.add(job)
    await db_session.commit()
    return job


async def add_watch(db_session: AsyncSession, user: User, **overrides: Any) -> Watch:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "user_id": user.id,
        "description": "비 소식",
        "tool_name": "get_weather",
        "tool_input": {"place": "서울", "date": "2026-11-18"},
        "condition_text": "강수 확률 60% 초과",
        "interval_minutes": 60,
        "next_run_at": now + timedelta(hours=1),
        "expires_at": now + timedelta(days=30),
    }
    values.update(overrides)
    watch = Watch(**values)
    db_session.add(watch)
    await db_session.commit()
    return watch


async def test_automation_api_requires_login(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/schedules")).status_code == 401
    assert (await client.get("/api/watches")).status_code == 401


async def test_list_schedules_with_last_result(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession, make_user: UserFactory
) -> None:
    conversation = Conversation(user_id=logged_in_user.id, title=rules.AUTOMATION_CONVERSATION_TITLE)
    db_session.add(conversation)
    await db_session.flush()
    task = Task(
        conversation_id=conversation.id,
        user_id=logged_in_user.id,
        origin=TaskOrigin.SCHEDULE,
        status=TaskStatus.DONE,
        final_answer="요약 결과",
    )
    db_session.add(task)
    await db_session.flush()
    job = await add_job(db_session, logged_in_user, last_task_id=task.id, last_run_at=datetime.now(UTC))
    briefing = await add_job(
        db_session,
        logged_in_user,
        title=rules.BRIEFING_TITLE,
        prompt_text=rules.BRIEFING_PROMPT,
        time_of_day=time(7, 30),
        kind=rules.BRIEFING_KIND,
    )
    other = await make_user("other@example.com", "password-other")
    await add_job(db_session, other)

    response = await client.get("/api/schedules")

    assert response.status_code == 200
    body = response.json()
    assert body["max_active"] == rules.MAX_ACTIVE_SCHEDULES
    by_id = {s["id"]: s for s in body["schedules"]}
    assert set(by_id) == {job.id, briefing.id}
    assert by_id[job.id]["time_of_day"] == "09:00"
    assert by_id[job.id]["repeat_text"] == "매일 09:00"
    assert by_id[job.id]["last_task"] == {
        "id": task.id,
        "conversation_id": conversation.id,
        "status": "done",
        "final_answer": "요약 결과",
        "error": None,
        "updated_at": by_id[job.id]["last_task"]["updated_at"],
    }
    assert by_id[job.id]["is_briefing"] is False
    assert by_id[briefing.id]["is_briefing"] is True
    assert by_id[briefing.id]["last_task"] is None


async def test_toggle_schedule(client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession) -> None:
    job = await add_job(db_session, logged_in_user, next_run_at=datetime.now(UTC) - timedelta(days=5))

    off = await client.patch(f"/api/schedules/{job.id}", json={"is_active": False})
    on = await client.patch(f"/api/schedules/{job.id}", json={"is_active": True})

    assert off.status_code == 200 and off.json()["is_active"] is False
    assert on.status_code == 200 and on.json()["is_active"] is True
    # 다시 켜면 다음 시각을 지금부터 다시 계산한다(꺼져 있던 동안의 실행을 몰아 돌리지 않는다)
    assert datetime.fromisoformat(on.json()["next_run_at"]) > datetime.now(UTC)


async def test_enable_schedule_over_limit_is_409(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession
) -> None:
    for number in range(rules.MAX_ACTIVE_SCHEDULES):
        await add_job(db_session, logged_in_user, prompt_text=f"요약 {number}")
    off = await add_job(db_session, logged_in_user, prompt_text="꺼진 것", is_active=False)

    response = await client.patch(f"/api/schedules/{off.id}", json={"is_active": True})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "limit_reached"


async def test_other_users_schedule_is_404(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession, make_user: UserFactory
) -> None:
    other = await make_user("other@example.com", "password-other")
    job = await add_job(db_session, other)

    patched = await client.patch(f"/api/schedules/{job.id}", json={"is_active": False})
    deleted = await client.delete(f"/api/schedules/{job.id}")

    assert (patched.status_code, deleted.status_code) == (404, 404)
    assert (await db_session.get(ScheduledJob, job.id, populate_existing=True)) is not None


async def test_delete_schedule(client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession) -> None:
    job = await add_job(db_session, logged_in_user)

    response = await client.delete(f"/api/schedules/{job.id}")

    assert response.status_code == 204
    left = await db_session.scalar(select(ScheduledJob).where(ScheduledJob.id == job.id))
    assert left is None


async def test_state_changes_require_origin(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession
) -> None:
    job = await add_job(db_session, logged_in_user)
    watch = await add_watch(db_session, logged_in_user)
    evil = {"Origin": "http://evil.example"}

    responses = [
        await client.patch(f"/api/schedules/{job.id}", json={"is_active": False}, headers=evil),
        await client.delete(f"/api/schedules/{job.id}", headers=evil),
        await client.patch(f"/api/watches/{watch.id}", json={"is_active": False}, headers=evil),
        await client.delete(f"/api/watches/{watch.id}", headers=evil),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403, 403]


async def test_list_watches_with_last_result(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession, make_user: UserFactory
) -> None:
    result = {"status": "not_met", "checked_at": "2026-11-17T10:00:00+00:00", "summary": "아직 아님", "sources": []}
    watch = await add_watch(db_session, logged_in_user, last_result=result)
    other = await make_user("other@example.com", "password-other")
    await add_watch(db_session, other)

    response = await client.get("/api/watches")

    assert response.status_code == 200
    body = response.json()
    assert body["max_active"] == rules.MAX_ACTIVE_WATCHES
    [listed] = body["watches"]
    assert listed["id"] == watch.id
    assert listed["tool_name"] == "get_weather"
    assert listed["last_result"]["status"] == "not_met"
    assert listed["last_result"]["summary"] == "아직 아님"


async def test_toggle_and_delete_watch(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession
) -> None:
    watch = await add_watch(db_session, logged_in_user, next_run_at=datetime.now(UTC) - timedelta(days=2))

    off = await client.patch(f"/api/watches/{watch.id}", json={"is_active": False})
    on = await client.patch(f"/api/watches/{watch.id}", json={"is_active": True})
    deleted = await client.delete(f"/api/watches/{watch.id}")

    assert off.json()["is_active"] is False
    assert on.json()["is_active"] is True
    assert deleted.status_code == 204


async def test_expired_watch_cannot_be_turned_on(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession
) -> None:
    watch = await add_watch(
        db_session, logged_in_user, is_active=False, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    response = await client.patch(f"/api/watches/{watch.id}", json={"is_active": True})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "expired"


async def test_duplicate_watch_cannot_be_turned_on(
    client: httpx.AsyncClient, logged_in_user: User, db_session: AsyncSession
) -> None:
    await add_watch(db_session, logged_in_user)
    copy = await add_watch(db_session, logged_in_user, is_active=False)

    response = await client.patch(f"/api/watches/{copy.id}", json={"is_active": True})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "duplicate"
