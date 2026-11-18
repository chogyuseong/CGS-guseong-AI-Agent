"""승인 카드의 승인·거절 API.

- 모든 조회에 user_id 조건. 남의 승인·없는 승인은 404.
- 상태 변경은 approvals.decide 안에서 task·승인 행을 SELECT ... FOR UPDATE로 잠그고 pending일 때만 바꾼다.
  이미 결정된 승인에 다시 요청하면(두 번 클릭) 아무것도 바꾸지 않고 200 changed=false로 현재 상태를 돌려준다.
- 그 task의 마지막 결정이면 resume_task 워크플로(ID task-{id}-resume-{n})를 시작만 하고 바로 응답한다(3단계).
  응답의 task_status는 그 시점 상태(보통 waiting_approval 또는 running)다. 화면은 task 폴링으로 끝을 확인한다.
- 오류 본문은 {"detail": {"code", "message"}}이고 OpenAPI에 ApprovalErrorOut 모델로 드러난다.

엔드포인트(모두 로그인 필요, POST라 앱 전체 Origin 검사 미들웨어를 거친다):
    POST /api/approvals/{id}/approve  본문 {confirm_mismatch?} → 200 ApprovalDecisionOut / 404 / 409 / 410
    POST /api/approvals/{id}/reject                            → 200 ApprovalDecisionOut / 404 / 409 / 410
승인 카드 목록은 이 라우터가 아니라 GET /api/tasks/{id}의 approvals로 나간다(approvals_of_task를 tasks.py가 부른다).

근거: BUILD_SPEC 4.2(operation 상태), DECISIONS 2026-11-17(decide 잠금 순서 task → 승인, mismatch 409 강제,
오류 code 분기, 재개 워크플로 ID 번호, outbox 3단계 보강). 상태 전이 표는 docs/STATE_MACHINES.md.

고칠 때 주의:
- 경로·응답 모양·오류 code를 바꾸면 `pnpm --dir apps/web gen:api` 후 apps/web/lib/approvals.ts
  (approve·reject 호출, approvalErrorCode 분기, applyDecision)를 같이 고친다.
- 오류 code 목록은 app/schemas/tasks.py의 ApprovalErrorCode와 app/agent/approvals.py의 예외 code가 같아야 한다.
- 재개 시작 경로(outbox.dispatch, start_resume_if_waiting)를 바꾸면 docs/STATE_MACHINES.md·docs/FAULTS.md도 본다.
- 관련 테스트: tests/test_approvals.py, test_approval_card_api.py, test_faults.py, test_workflows.py, test_openapi.py.
"""

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import approvals, outbox, plans
from app.agent.workflows import start_resume_if_waiting
from app.db import DbSession
from app.models import ActionPlan, Approval, ExecutionReceipt, GuardCheck, Operation, Step, Task
from app.models.enums import VerdictKind
from app.schemas.tasks import ApprovalDecisionOut, ApprovalErrorOut, ApprovalOut, ApproveRequest, GuardOut
from app.security import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/approvals", tags=["approvals"])

# 아래 *_RESPONSE 표는 OpenAPI에 오류 본문 모양을 드러내려고 둔다(화면 타입 생성에 쓰인다, DECISIONS 2026-11-17).
# description 문자열도 생성 파일에 들어가므로 바꾸면 gen:api를 다시 돌린다.
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
# decide가 던지는 예외 → HTTP 상태. 남의 승인도 404로 묶어 존재를 드러내지 않는다.
# 표에 없는 ApprovalError는 decide_and_resume에서 409로 떨어진다.
ERROR_STATUS = {
    approvals.ApprovalNotFoundError: status.HTTP_404_NOT_FOUND,
    approvals.MismatchConfirmationRequiredError: status.HTTP_409_CONFLICT,
    approvals.TaskNotWaitingError: status.HTTP_409_CONFLICT,
    approvals.ApprovalExpiredError: status.HTTP_410_GONE,
}
# 사용자에게 보일 문장. 화면은 문구가 아니라 detail.code로 분기하므로 문장은 자유롭게 고쳐도 된다.
ERROR_MESSAGES = {
    approvals.ApprovalNotFoundError: "승인 요청을 찾을 수 없습니다.",
    approvals.MismatchConfirmationRequiredError: "요청과 다를 수 있습니다. 그래도 진행할지 한 번 더 확인해 주세요.",
    approvals.TaskNotWaitingError: "이 작업은 더 이상 승인을 기다리지 않습니다.",
    approvals.ApprovalExpiredError: "승인 시간이 지나 작업이 취소되었습니다.",
}


@dataclass(frozen=True)
class CardRow:
    """카드 한 장에 필요한 행들. 계획·operation·영수증은 2단계 보강 전 승인에는 없다(None)."""

    approval: Approval
    step: Step
    guard: GuardCheck | None
    plan: ActionPlan | None
    operation: Operation | None
    receipt_id: int | None


def account_text(plan: ActionPlan | None) -> str | None:
    """연결이 필요한 도구면 계획 때 연결 상태. 계정 주소는 아직 저장하지 않는다(4단계 OAuth 보강)."""
    spec = plans.EXTERNAL_TOOL_SPECS.get(plan.tool_name) if plan else None
    if plan is None or spec is None or spec.provider is None:
        return None
    service = plans.PROVIDER_LABELS.get(spec.provider, spec.provider)
    return f"연결된 {service} 계정" if plan.connection_id is not None else f"{service} 계정이 연결되어 있지 않음"


def target_text(plan: ActionPlan | None) -> str:
    """카드의 '어디에 하는 일인지'. 도구 규격의 target_label, 계획·규격이 없으면 기본 규격의 문구("외부 서비스").

    기본 문구는 ExternalToolSpec의 기본값에서 가져오므로 그 기본값을 바꾸면 옛 승인 카드 문구도 바뀐다.
    """
    spec = plans.EXTERNAL_TOOL_SPECS.get(plan.tool_name) if plan else None
    return spec.target_label if spec else plans.ExternalToolSpec(version="0").target_label


def approval_view(row: CardRow) -> ApprovalOut:
    """DB 행 묶음 → 승인 카드 응답. DB를 읽거나 쓰지 않는다.

    receipt_confirmed는 영수증 행이 있을 때만 true다. 화면의 "실행 완료"는 이 값에만 기대야 한다
    (절대 원칙 10: 성공 표시는 실행 영수증에 근거). operation 상태만으로 완료를 보이면 unknown을 성공으로 오인한다.
    """
    approval, step, guard = row.approval, row.step, row.guard
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
        target=target_text(row.plan),
        account=account_text(row.plan),
        execute_before=approval.execute_before,
        operation_status=row.operation.status if row.operation else None,
        receipt_confirmed=row.receipt_id is not None,
    )


def approval_query() -> Select[Approval, Step, GuardCheck, ActionPlan, Operation, int]:
    """승인 + step + 감시 결과 + 계획·operation·영수증 id(없으면 None, outer join).

    user_id 조건이 없다. 부르는 쪽이 task 소유자를 이미 확인했거나(approvals_of_task), decide가 소유자를
    확인한 승인 id로만 부른다(decision_view). 새 호출처를 만들면 소유자 조건을 반드시 붙인다(남의 카드 노출).
    계획·operation·영수증이 한 승인에 하나씩이라는 전제로 행이 늘지 않는다. 1:N이 되면 카드가 중복된다.
    """
    return (
        select(Approval, Step, GuardCheck, ActionPlan, Operation, ExecutionReceipt.id)
        .join(Step, Step.id == Approval.step_id)
        .outerjoin(GuardCheck, GuardCheck.step_id == Step.id)
        .outerjoin(ActionPlan, ActionPlan.id == Approval.plan_id)
        .outerjoin(Operation, Operation.plan_id == ActionPlan.id)
        .outerjoin(ExecutionReceipt, ExecutionReceipt.operation_id == Operation.id)
    )


async def approvals_of_task(session: AsyncSession, task_id: int) -> list[ApprovalOut]:
    """작업의 승인 카드 목록(step 순서). 호출하는 쪽이 작업 소유자를 이미 확인했다."""
    rows = await session.execute(approval_query().where(Step.task_id == task_id).order_by(Step.order))
    return [approval_view(CardRow(*row)) for row in rows]


async def decision_view(session: AsyncSession, decision: approvals.Decision) -> ApprovalDecisionOut:
    """결정 직후의 카드와 task 상태. 읽기만 한다.

    task_status는 재개가 끝난 상태가 아니라 이 순간의 상태다(보통 waiting_approval·running). 화면은 폴링으로 끝을 본다.
    decision.approval_id는 decide가 user_id로 확인한 승인이라 여기서 소유자를 다시 보지 않는다.
    """
    session.expire_all()  # 재개 워크플로가 다른 세션에서 바꾼 값을 다시 읽는다
    row = (await session.execute(approval_query().where(Approval.id == decision.approval_id))).one()
    task = await session.get_one(Task, decision.task_id)
    return ApprovalDecisionOut(approval=approval_view(CardRow(*row)), changed=decision.changed, task_status=task.status)


async def decide_and_resume(
    request: Request, session: AsyncSession, approval_id: int, user_id: int, *, approve: bool, confirm: bool
) -> ApprovalDecisionOut:
    """승인·거절 공통 처리: 결정(잠금·커밋은 approvals.decide 안) → 마지막 결정이면 재개 시작 → 카드 응답.

    오류면 세션을 되돌리고 {"code", "message"} 본문의 HTTPException으로 바꾼다(404·409·410).
    재개 워크플로는 시작만 하고 기다리지 않는다. 시작 실패는 로그만 남기고 200으로 답한다:
    결정과 같은 트랜잭션에 남은 outbox 요청을 1분 검사·서버 시작이 다시 처리하므로 사용자가 다시 누를 필요가 없다.
    """
    try:
        decision = await approvals.decide(session, approval_id, user_id, approve=approve, confirm_mismatch=confirm)
    except approvals.ApprovalError as error:
        await session.rollback()
        detail = {"code": error.code, "message": ERROR_MESSAGES.get(type(error), "승인을 처리할 수 없습니다.")}
        raise HTTPException(ERROR_STATUS.get(type(error), status.HTTP_409_CONFLICT), detail=detail) from error
    if decision.all_decided:
        # 결정과 함께 커밋된 outbox 요청을 처리한다(3단계 보강). 여러 요청이 동시에 와도 같은 워크플로 ID라
        # 한 번만 실행되고, resume_task 안의 행 잠금이 한 번 더 막는다. outbox 요청이 없는 옛 기록은 아래가 잇는다.
        state = request.app.state
        await outbox.dispatch(state.launcher, state.session_factory, task_id=decision.task_id)
        try:
            await start_resume_if_waiting(state.launcher, state.session_factory, decision.task_id)
        except Exception:  # 시작 실패는 outbox 요청이 다시 처리한다. 승인 응답은 막지 않는다.
            logger.warning("재개 시작 실패: task=%s (outbox가 다시 시도)", decision.task_id)
    return await decision_view(session, decision)


# 승인. mismatch 판정이면 서버도 confirm_mismatch를 강제한다(화면 버그로 '한 번 더 묻기'를 건너뛰는 사고 방지).
# 같은 승인에 두 번 눌러도 200 changed=false라 실행이 두 번 생기지 않는다(decide의 FOR UPDATE + pending 검사).
# 아래 docstring은 OpenAPI 설명이라 고치면 gen:api 결과가 바뀐다.
@router.post("/{approval_id}/approve", responses=APPROVE_ERROR_RESPONSES)
async def approve_action(
    approval_id: int, request: Request, user: CurrentUser, session: DbSession, body: ApproveRequest | None = None
) -> ApprovalDecisionOut:
    """승인. 감시 판정이 mismatch면 body에 confirm_mismatch=true가 있어야 한다."""
    confirm = body.confirm_mismatch if body else False
    return await decide_and_resume(request, session, approval_id, user.id, approve=True, confirm=confirm)


# 거절. 마지막 결정이면 승인과 같은 재개 경로를 탄다(거절 결과를 모델에 넘겨야 task가 끝난다).
# 아래 docstring은 OpenAPI 설명이다.
@router.post("/{approval_id}/reject", responses=REJECT_ERROR_RESPONSES)
async def reject_action(
    approval_id: int, request: Request, user: CurrentUser, session: DbSession
) -> ApprovalDecisionOut:
    """거절. 모델은 '사용자가 거절함'을 도구 결과로 받고 다른 방법을 찾는다."""
    return await decide_and_resume(request, session, approval_id, user.id, approve=False, confirm=False)
