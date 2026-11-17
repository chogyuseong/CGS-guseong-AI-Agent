"""자동화 화면 API (6단계). 명세 API 목록의 GET/PATCH/DELETE /schedules, /watches.

    GET    /api/schedules              → 200 ScheduleListOut (내 예약 작업 + 마지막 실행 결과)
    PATCH  /api/schedules/{id}         → 200 ScheduleOut. 본문 {is_active}. 404 not_found / 409 limit_reached
    DELETE /api/schedules/{id}         → 204 / 404 not_found
    GET    /api/watches                → 200 WatchListOut (내 감시 + 마지막 확인 결과)
    PATCH  /api/watches/{id}           → 200 WatchOut. 404 not_found / 409 limit_reached·duplicate·expired
    DELETE /api/watches/{id}           → 204 / 404 not_found

- 만들기는 채팅의 create_schedule·create_watch로만 한다(명세 API 목록에 POST가 없다).
- 켜기는 도구와 같은 서버 제한(활성 10개, 중복, 만료)을 거친다(app/automation/store.py).
- 모든 조회·수정에 user_id 조건. 남의 것은 없는 것과 똑같이 404. 상태 변경은 앱 전체 Origin 검사를 거친다.
- 오류 본문은 {"detail": {"code", "message"}}(AutomationErrorOut).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import ValidationError
from sqlalchemy import select

from app.automation import rules, store
from app.automation.store import AutomationError
from app.db import DbSession
from app.models import ScheduledJob, Task, Watch
from app.schemas.automations import (
    ActiveUpdate,
    AutomationErrorOut,
    BriefingUpdate,
    LastTaskOut,
    ScheduleListOut,
    ScheduleOut,
    WatchListOut,
    WatchOut,
    WatchResultOut,
)
from app.security import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["automations"])

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": AutomationErrorOut, "description": "내 것 중에 그 id가 없다(not_found)"},
    409: {"model": AutomationErrorOut, "description": "켤 수 없다(limit_reached, duplicate, expired)"},
}


def http_error(error: AutomationError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if error.code == store.NOT_FOUND else status.HTTP_409_CONFLICT
    return HTTPException(code, detail={"code": error.code, "message": error.message})


def last_task_out(task: Task | None) -> LastTaskOut | None:
    if task is None:
        return None
    return LastTaskOut(
        id=task.id,
        conversation_id=task.conversation_id,
        status=task.status,
        final_answer=task.final_answer,
        error=task.error,
        updated_at=task.updated_at,
    )


def schedule_out(job: ScheduledJob, last_task: Task | None) -> ScheduleOut:
    return ScheduleOut(
        id=job.id,
        title=job.title,
        prompt_text=job.prompt_text,
        time_of_day=job.time_of_day.strftime("%H:%M"),
        weekdays=list(job.weekdays),
        repeat_text=rules.repeat_text(job.time_of_day, job.weekdays),
        is_active=job.is_active,
        next_run_at=job.next_run_at,
        last_run_at=job.last_run_at,
        is_briefing=job.kind == rules.BRIEFING_KIND,
        last_task=last_task_out(last_task),
    )


def result_out(watch: Watch) -> WatchResultOut | None:
    if not watch.last_result:
        return None
    try:
        return WatchResultOut.model_validate(watch.last_result)
    except ValidationError:
        logger.warning("감시 %s의 last_result 모양이 예상과 다름", watch.id)
        return None


def watch_out(watch: Watch) -> WatchOut:
    return WatchOut(
        id=watch.id,
        description=watch.description,
        tool_name=watch.tool_name,
        tool_input=watch.tool_input,
        condition_text=watch.condition_text,
        interval_minutes=watch.interval_minutes,
        repeat=watch.repeat,
        is_active=watch.is_active,
        next_run_at=watch.next_run_at,
        expires_at=watch.expires_at,
        last_notified_at=watch.last_notified_at,
        last_result=result_out(watch),
    )


async def last_tasks(session: DbSession, user_id: int, jobs: list[ScheduledJob]) -> dict[int, Task]:
    ids = [job.last_task_id for job in jobs if job.last_task_id is not None]
    if not ids:
        return {}
    rows = await session.scalars(select(Task).where(Task.id.in_(ids), Task.user_id == user_id))
    return {task.id: task for task in rows}


# ---- 예약 작업 ----


@router.get("/schedules")
async def list_schedules(user: CurrentUser, session: DbSession) -> ScheduleListOut:
    jobs = await store.list_schedules(session, user.id)
    tasks = await last_tasks(session, user.id, jobs)
    items = [schedule_out(job, tasks.get(job.last_task_id or 0)) for job in jobs]
    return ScheduleListOut(schedules=items, max_active=rules.MAX_ACTIVE_SCHEDULES)


@router.put("/schedules/briefing", responses={409: ERRORS[409]})
async def enable_briefing(body: BriefingUpdate, user: CurrentUser, session: DbSession) -> ScheduleOut:
    """6단계 보강: 아침 브리핑 켜기(없으면 만들고, 있으면 시각을 바꾸고 켠다). 끄기·지우기는 PATCH·DELETE."""
    try:
        job = await store.enable_briefing(session, user.id, time_text=body.time_of_day, now=datetime.now(UTC))
    except AutomationError as error:
        raise http_error(error) from None
    await session.commit()
    tasks = await last_tasks(session, user.id, [job])
    return schedule_out(job, tasks.get(job.last_task_id or 0))


@router.patch("/schedules/{schedule_id}", responses=ERRORS)
async def update_schedule(schedule_id: int, body: ActiveUpdate, user: CurrentUser, session: DbSession) -> ScheduleOut:
    try:
        job = await store.set_schedule_active(
            session, user.id, schedule_id, active=body.is_active, now=datetime.now(UTC)
        )
    except AutomationError as error:
        raise http_error(error) from None
    await session.commit()
    tasks = await last_tasks(session, user.id, [job])
    return schedule_out(job, tasks.get(job.last_task_id or 0))


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT, responses={404: ERRORS[404]})
async def delete_schedule(schedule_id: int, user: CurrentUser, session: DbSession) -> Response:
    try:
        await store.delete_schedule(session, user.id, schedule_id)
    except AutomationError as error:
        raise http_error(error) from None
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- 감시 ----


@router.get("/watches")
async def list_watches(user: CurrentUser, session: DbSession) -> WatchListOut:
    watches = await store.list_watches(session, user.id)
    return WatchListOut(watches=[watch_out(w) for w in watches], max_active=rules.MAX_ACTIVE_WATCHES)


@router.patch("/watches/{watch_id}", responses=ERRORS)
async def update_watch(watch_id: int, body: ActiveUpdate, user: CurrentUser, session: DbSession) -> WatchOut:
    try:
        watch = await store.set_watch_active(session, user.id, watch_id, active=body.is_active, now=datetime.now(UTC))
    except AutomationError as error:
        raise http_error(error) from None
    await session.commit()
    return watch_out(watch)


@router.delete("/watches/{watch_id}", status_code=status.HTTP_204_NO_CONTENT, responses={404: ERRORS[404]})
async def delete_watch(watch_id: int, user: CurrentUser, session: DbSession) -> Response:
    try:
        await store.delete_watch(session, user.id, watch_id)
    except AutomationError as error:
        raise http_error(error) from None
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
