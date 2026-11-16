"""승인 카드의 승인·거절 API.

- 모든 조회에 user_id 조건. 남의 승인·없는 승인은 404.
- 상태 변경은 approvals.decide 안에서 task·승인 행을 SELECT ... FOR UPDATE로 잠그고 pending일 때만 바꾼다.
  이미 결정된 승인에 다시 요청하면(두 번 클릭) 아무것도 바꾸지 않고 200 changed=false로 현재 상태를 돌려준다.
- 그 task의 마지막 결정이면 resume_task 워크플로(ID task-{id}-resume-{n})를 시작만 하고 바로 응답한다(3단계).
  응답의 task_status는 그 시점 상태(보통 waiting_approval 또는 running)다. 화면은 task 폴링으로 끝을 확인한다.
- 오류 본문은 {"detail": {"code", "message"}}이고 OpenAPI에 ApprovalErrorOut 모델로 드러난다.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import approvals
from app.agent.workflows import start_resume_if_waiting
from app.db import DbSession
from app.models import Approval, GuardCheck, Step, Task
from app.models.enums import VerdictKind
from app.schemas.tasks import ApprovalDecisionOut, ApprovalErrorOut, ApprovalOut, ApproveRequest, GuardOut
from app.security import CurrentUser

router = APIRouter(prefix="/approvals", tags=["approvals"])

NOT_FOUND_RESPONSE: dict[str, Any] = {
    "model": ApprovalErrorOut,
    "description": "detail.code가 not_found. 없는 승인이거나 다른 사용자의 승인",
}
EXPIRED_RESPONSE: dict[str, Any] = {
    "model": ApprovalErrorOut,
    "description": "detail.code가 approval_expired. 만료되어 작업이 취소되었다",
}
APPROVE_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: NOT_FOUND_RESPONSE,
    409: {
        "model": ApprovalErrorOut,
        "description": "detail.code가 mismatch_confirmation_required(감시 판정 mismatch인데 confirm_mismatch 없음) "
        "또는 task_not_waiting(작업이 승인 대기 상태가 아님)",
    },
    410: EXPIRED_RESPONSE,
}
# 거절에는 mismatch 확인이 필요 없으므로 409는 task_not_waiting뿐이다.
REJECT_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: NOT_FOUND_RESPONSE,
    409: {"model": ApprovalErrorOut, "description": "detail.code가 task_not_waiting(작업이 승인 대기 상태가 아님)"},
    410: EXPIRED_RESPONSE,
}
ERROR_STATUS = {
    approvals.ApprovalNotFoundError: status.HTTP_404_NOT_FOUND,
    approvals.MismatchConfirmationRequiredError: status.HTTP_409_CONFLICT,
    approvals.TaskNotWaitingError: status.HTTP_409_CONFLICT,
    approvals.ApprovalExpiredError: status.HTTP_410_GONE,
}
ERROR_MESSAGES = {
    approvals.ApprovalNotFoundError: "승인 요청을 찾을 수 없습니다.",
    approvals.MismatchConfirmationRequiredError: "요청과 다를 수 있습니다. 그래도 진행할지 한 번 더 확인해 주세요.",
    approvals.TaskNotWaitingError: "이 작업은 더 이상 승인을 기다리지 않습니다.",
    approvals.ApprovalExpiredError: "승인 시간이 지나 작업이 취소되었습니다.",
}


def approval_view(approval: Approval, step: Step, guard: GuardCheck | None) -> ApprovalOut:
    return ApprovalOut(
        id=approval.id,
        step_id=step.id,
        tool_name=step.tool_name,
        tool_input=step.input,
        status=approval.status,
        summary=approval.summary,
        expires_at=approval.expires_at,
        decided_at=approval.decided_at,
        guard=GuardOut(verdict=guard.verdict, reason=guard.reason) if guard else None,
        needs_mismatch_confirmation=guard is not None and guard.verdict == VerdictKind.MISMATCH,
    )


def approval_query() -> Select[Approval, Step, GuardCheck]:
    """승인 + step + 감시 결과(없으면 None, outer join)."""
    return (
        select(Approval, Step, GuardCheck)
        .join(Step, Step.id == Approval.step_id)
        .outerjoin(GuardCheck, GuardCheck.step_id == Step.id)
    )


async def approvals_of_task(session: AsyncSession, task_id: int) -> list[ApprovalOut]:
    """작업의 승인 카드 목록(step 순서). 호출하는 쪽이 작업 소유자를 이미 확인했다."""
    rows = await session.execute(approval_query().where(Step.task_id == task_id).order_by(Step.order))
    return [approval_view(approval, step, guard) for approval, step, guard in rows]


async def decision_view(session: AsyncSession, decision: approvals.Decision) -> ApprovalDecisionOut:
    session.expire_all()  # 재개 워크플로가 다른 세션에서 바꾼 값을 다시 읽는다
    approval, step, guard = (await session.execute(approval_query().where(Approval.id == decision.approval_id))).one()
    task = await session.get_one(Task, decision.task_id)
    return ApprovalDecisionOut(
        approval=approval_view(approval, step, guard), changed=decision.changed, task_status=task.status
    )


async def decide_and_resume(
    request: Request, session: AsyncSession, approval_id: int, user_id: int, *, approve: bool, confirm: bool
) -> ApprovalDecisionOut:
    try:
        decision = await approvals.decide(session, approval_id, user_id, approve=approve, confirm_mismatch=confirm)
    except approvals.ApprovalError as error:
        await session.rollback()
        detail = {"code": error.code, "message": ERROR_MESSAGES.get(type(error), "승인을 처리할 수 없습니다.")}
        raise HTTPException(ERROR_STATUS.get(type(error), status.HTTP_409_CONFLICT), detail=detail) from error
    if decision.all_decided:
        # 여러 요청이 동시에 와도 같은 워크플로 ID라 한 번만 실행되고, resume_task 안의 행 잠금이 한 번 더 막는다.
        state = request.app.state
        await start_resume_if_waiting(state.launcher, state.session_factory, decision.task_id)
    return await decision_view(session, decision)


@router.post("/{approval_id}/approve", responses=APPROVE_ERROR_RESPONSES)
async def approve_action(
    approval_id: int, request: Request, user: CurrentUser, session: DbSession, body: ApproveRequest | None = None
) -> ApprovalDecisionOut:
    """승인. 감시 판정이 mismatch면 body에 confirm_mismatch=true가 있어야 한다."""
    confirm = body.confirm_mismatch if body else False
    return await decide_and_resume(request, session, approval_id, user.id, approve=True, confirm=confirm)


@router.post("/{approval_id}/reject", responses=REJECT_ERROR_RESPONSES)
async def reject_action(
    approval_id: int, request: Request, user: CurrentUser, session: DbSession
) -> ApprovalDecisionOut:
    """거절. 모델은 '사용자가 거절함'을 도구 결과로 받고 다른 방법을 찾는다."""
    return await decide_and_resume(request, session, approval_id, user.id, approve=False, confirm=False)
