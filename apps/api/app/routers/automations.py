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

6단계 보강:
    PUT    /api/schedules/briefing     → 200 ScheduleOut. 본문 {time_of_day}. 409(활성 한도 등)
  아침 브리핑은 사용자가 이 화면에서 켤 때만 생긴다(BUILD_SPEC 8.1, DECISIONS 2026-11-17 "아침 브리핑은 사용자가
  자동화 화면에서 켤 때만"). 사용자당 하나(scheduled_jobs.kind='briefing'), 끄기·지우기는 일반 예약과 같은 PATCH·DELETE.
  목록 응답의 budget_paused: 오늘 하루 비용 한도에 닿았는지(DECISIONS 2026-11-17 "예산 부족 표시").

근거: DECISIONS 2026-11-17(GET/PATCH/DELETE만 두고 POST·이름 수정 API는 만들지 않음, 활성 10개).
자동 작업에는 READ 도구만 간다(절대 원칙 7). 이 API는 켜기·끄기·삭제만 하므로 그 경계를 바꾸지 않는다.

고칠 때 주의:
- 경로·응답 모양·오류 code를 바꾸면 gen:api 후 apps/web/lib/automations.ts(목록·PATCH·DELETE·PUT briefing)를 고친다.
  오류 code 목록은 app/schemas/automations.py의 AutomationErrorCode와 app/automation/store.py가 같아야 한다.
- 켜기 제한 값은 app/automation/rules.py(MAX_ACTIVE_*)에 있다. 응답의 max_active도 거기서 온다.
- 관련 테스트: tests/test_automation_api.py, test_briefing_consent.py, test_budget_pause.py.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import select

from app.agent.budget import daily_cost_exceeded
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

# OpenAPI에 오류 본문 모양을 드러내는 표(화면 타입 생성용). description을 바꾸면 gen:api를 다시 돌린다.
ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": AutomationErrorOut, "description": "내 것 중에 그 id가 없다(not_found)"},
    409: {"model": AutomationErrorOut, "description": "켤 수 없다(limit_reached, duplicate, expired)"},
}


def http_error(error: AutomationError) -> HTTPException:
    """store의 AutomationError → HTTP 오류. not_found만 404, 나머지(limit_reached, duplicate, expired, invalid)는 409.

    store는 남의 것도 not_found로 올려 보내므로 여기서 404가 되어 존재를 드러내지 않는다.
    """
    code = status.HTTP_404_NOT_FOUND if error.code == store.NOT_FOUND else status.HTTP_409_CONFLICT
    return HTTPException(code, detail={"code": error.code, "message": error.message})


def last_task_out(task: Task | None) -> LastTaskOut | None:
    """예약의 마지막 실행 task 요약. task가 없거나(아직 안 돌았음, 대화 삭제로 사라짐) 남의 것이면 None."""
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
    """예약 행 → 응답. time_of_day는 서울 'HH:MM', is_briefing은 kind로 판단한다(프롬프트 문장 비교는 쓰지 않는다)."""
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
    """watches.last_result(JSON)를 응답 모양으로. 모양이 맞지 않는 옛 기록은 경고만 남기고 None.

    목록 전체가 500이 되지 않게 하려는 것이다. 새 status 값을 기록하기 시작하면 WatchStatus Literal에도 더해야
    이 함수가 None으로 떨어뜨리지 않는다(app/schemas/automations.py).
    """
    if not watch.last_result:
        return None
    try:
        return WatchResultOut.model_validate(watch.last_result)
    except ValidationError:
        logger.warning("감시 %s의 last_result 모양이 예상과 다름", watch.id)
        return None


def watch_out(watch: Watch) -> WatchOut:
    """감시 행 → 응답. DB를 읽지 않는다."""
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
    """예약들의 마지막 task를 한 번의 조회로 모은다(예약마다 조회하지 않게). 읽기만 한다.

    jobs가 이미 내 것이어도 Task.user_id 조건을 다시 건다(남의 task 내용이 섞이는 사고를 한 겹 더 막는다).
    """
    ids = [job.last_task_id for job in jobs if job.last_task_id is not None]
    if not ids:
        return {}
    rows = await session.scalars(select(Task).where(Task.id.in_(ids), Task.user_id == user_id))
    return {task.id: task for task in rows}


# ---- 예약 작업 ----


# 내 예약 목록 + 마지막 실행 결과 + 오늘 비용 한도에 닿았는지(budget_paused). 읽기만 한다.
@router.get("/schedules")
async def list_schedules(request: Request, user: CurrentUser, session: DbSession) -> ScheduleListOut:
    jobs = await store.list_schedules(session, user.id)
    tasks = await last_tasks(session, user.id, jobs)
    items = [schedule_out(job, tasks.get(job.last_task_id or 0)) for job in jobs]
    paused = await daily_cost_exceeded(session, user.id, request.app.state.settings)
    return ScheduleListOut(schedules=items, max_active=rules.MAX_ACTIVE_SCHEDULES, budget_paused=paused)


# 경로 순서 주의: "/schedules/briefing"은 PUT이고 "/schedules/{schedule_id}"는 PATCH·DELETE라 지금은 겹치지 않는다.
# 같은 메서드로 {schedule_id} 경로를 더하면 이 고정 경로를 그보다 먼저 등록해야 한다.
# 성공하면 커밋한다. 아래 docstring은 OpenAPI 설명이다.
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


# 켜기·끄기. 켜기는 도구와 같은 서버 제한(활성 한도 등)을 store가 검사하고, 다음 실행 시각을 다시 계산한다.
# 오류면 커밋하지 않는다(요청 세션은 의존성이 닫으며 버린다).
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


# 삭제(되돌릴 수 없음, 화면이 한 번 묻는다). 그 예약이 만든 task·메시지는 '예약 작업' 대화방에 남는다.
@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT, responses={404: ERRORS[404]})
async def delete_schedule(schedule_id: int, user: CurrentUser, session: DbSession) -> Response:
    try:
        await store.delete_schedule(session, user.id, schedule_id)
    except AutomationError as error:
        raise http_error(error) from None
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- 감시 ----


# 내 감시 목록 + 마지막 확인 결과 + budget_paused. 읽기만 한다.
@router.get("/watches")
async def list_watches(request: Request, user: CurrentUser, session: DbSession) -> WatchListOut:
    watches = await store.list_watches(session, user.id)
    paused = await daily_cost_exceeded(session, user.id, request.app.state.settings)
    return WatchListOut(
        watches=[watch_out(w) for w in watches], max_active=rules.MAX_ACTIVE_WATCHES, budget_paused=paused
    )


# 감시 켜기·끄기. 켜기는 활성 한도·중복·만료를 store가 검사한다(만료된 감시는 다시 켤 수 없다: 409 expired).
@router.patch("/watches/{watch_id}", responses=ERRORS)
async def update_watch(watch_id: int, body: ActiveUpdate, user: CurrentUser, session: DbSession) -> WatchOut:
    try:
        watch = await store.set_watch_active(session, user.id, watch_id, active=body.is_active, now=datetime.now(UTC))
    except AutomationError as error:
        raise http_error(error) from None
    await session.commit()
    return watch_out(watch)


# 감시 삭제. 남의 것·없는 것은 404.
@router.delete("/watches/{watch_id}", status_code=status.HTTP_204_NO_CONTENT, responses={404: ERRORS[404]})
async def delete_watch(watch_id: int, user: CurrentUser, session: DbSession) -> Response:
    try:
        await store.delete_watch(session, user.id, watch_id)
    except AutomationError as error:
        raise http_error(error) from None
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
