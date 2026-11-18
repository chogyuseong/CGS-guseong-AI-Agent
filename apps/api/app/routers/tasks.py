"""작업(task) 조회. 화면의 진행 타임라인과 작업 기록 탭이 쓴다.

모든 조회에 user_id 조건을 걸고, 남의 작업은 404로 답한다.
작업 취소(POST /tasks/{id}/cancel)는 1단계 보강에서 만들었다(규칙은 app/agent/cancel.py).

    GET  /api/tasks?limit=30          → 200 [TaskSummary] (최신순, limit 1~100)
    GET  /api/tasks/{id}              → 200 TaskDetail(steps + 모든 상태의 approvals) / 404
    POST /api/tasks/{id}/cancel       → 200 CancelOut / 404 / 409(이미 끝남)

- 모두 로그인 필요. 취소는 POST라 앱 전체 Origin 검사를 거친다.
- 화면은 메시지 전송 뒤 GET /api/tasks/{id}를 폴링한다(진행 중 1초, 승인 대기 3초: DECISIONS 2026-11-17).
- approvals가 pending만이 아니라 모든 승인을 주는 것은 DECISIONS 2026-11-17 결정이다.

고칠 때 주의:
- 경로·응답 모양을 바꾸면 gen:api 후 apps/web/lib/chat.ts(작업 조회·목록·취소)를 고친다.
- task 상태 값·취소 전이를 바꾸면 docs/STATE_MACHINES.md와 tests/test_state_machines.py를 같이 고친다.
- 관련 테스트: tests/test_cancel.py, test_cancel_race.py, test_chat_api.py, test_approvals.py.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.agent.cancel import CancelNotAllowedError, request_cancel
from app.db import DbSession
from app.models import Message, Step, Task
from app.routers.approvals import approvals_of_task
from app.schemas.tasks import CancelOut, StepOut, TaskDetail, TaskSummary
from app.security import CurrentUser

router = APIRouter(prefix="/tasks", tags=["tasks"])


# 작업 기록 탭. step 개수는 상관 서브쿼리로 한 번에 센다(작업마다 따로 조회하지 않게). 읽기만 한다.
# 아래 docstring은 OpenAPI 설명이다.
@router.get("")
async def list_tasks(
    user: CurrentUser, session: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> list[TaskSummary]:
    """최근 작업 목록(최신순)."""
    step_count = select(func.count(Step.id)).where(Step.task_id == Task.id).correlate(Task).scalar_subquery()
    rows = await session.execute(
        select(Task, Message.content, step_count)
        .outerjoin(Message, Message.id == Task.trigger_message_id)
        .where(Task.user_id == user.id)
        .order_by(Task.created_at.desc(), Task.id.desc())
        .limit(limit)
    )
    return [summary_of(task, request_text, count) for task, request_text, count in rows]


def summary_of(task: Task, request_text: str | None, step_count: int) -> TaskSummary:
    """작업 기록 한 줄. request_text는 시작 메시지 내용이고, 메시지 없이 시작한 예약·감시 작업은 None이다."""
    return TaskSummary(
        id=task.id,
        conversation_id=task.conversation_id,
        origin=task.origin,
        status=task.status,
        request_text=request_text,
        final_answer=task.final_answer,
        error=task.error,
        step_count=step_count,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


# 작업 상세(폴링 대상). 남의 작업·없는 작업은 똑같이 404. 승인 카드는 소유 확인 뒤 approvals_of_task가 만든다
# (그 함수는 user_id 조건이 없으므로 이 순서를 지킨다). 읽기만 한다. 아래 docstring은 OpenAPI 설명이다.
@router.get("/{task_id}")
async def get_task(task_id: int, user: CurrentUser, session: DbSession) -> TaskDetail:
    """작업 상태와 단계(steps), 승인 카드(approvals: 모든 상태, step 순서)."""
    task = await session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="작업을 찾을 수 없습니다.")
    steps = await session.scalars(select(Step).where(Step.task_id == task.id).order_by(Step.order))
    return TaskDetail(
        id=task.id,
        conversation_id=task.conversation_id,
        origin=task.origin,
        status=task.status,
        final_answer=task.final_answer,
        error=task.error,
        request_count=task.request_count,
        total_tokens=task.total_tokens,
        created_at=task.created_at,
        updated_at=task.updated_at,
        steps=[StepOut.model_validate(step) for step in steps],
        approvals=await approvals_of_task(session, task.id),
    )


# 작업 취소. request_cancel이 user_id 조건으로 task를 찾고 상태를 바꾼다(잠금·커밋은 app/agent/cancel.py).
# None이면 없거나 남의 작업이라 404, CancelNotAllowedError면 이미 끝나 409. 아래 docstring은 OpenAPI 설명이다.
@router.post(
    "/{task_id}/cancel",
    responses={404: {"description": "없거나 남의 작업"}, 409: {"description": "이미 끝난 작업"}},
)
async def cancel_task(task_id: int, user: CurrentUser, session: DbSession) -> CancelOut:
    """작업 취소. 대기 중이면 바로 cancelled, 실행 중이면 cancel_requested(다음 단계 전에 멈춘다).

    이미 끝난 단계(할 일 추가, 승인된 외부 행동)는 되돌리지 않는다.
    """
    try:
        current = await request_cancel(session, task_id, user.id)
    except CancelNotAllowedError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="이미 끝난 작업이라 취소할 수 없습니다.") from None
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="작업을 찾을 수 없습니다.")
    return CancelOut(task_id=task_id, status=current)
