"""승인: EXTERNAL 도구 호출을 사람이 건별로 승인·거절하는 기록과 검사.

이중 방어 (docs/SAFETY.md):
    1. 프레임워크: external_tools는 requires_approval=True다. 모델이 이 도구를 부르면 Pydantic AI가 실행하지 않고
       run을 DeferredToolRequests로 끝낸다. → save_requests가 승인 요청을 DB에 남긴다.
    2. DB: EXTERNAL 도구 함수의 첫 줄은 assert_approved(ctx)다. 이 tool_call_id의 승인이 DB에 approved로 있고
       아직 실행되지 않았을 때만 통과한다. 프레임워크 설정 하나가 바뀌거나 누가 함수를 직접 불러도 실행되지 않는다.

흐름:
    run 결과가 DeferredToolRequests ─▶ runner가 plans.draft_plan으로 인자를 정규화·고정(2단계 보강)
                                 ─▶ save_requests: steps(waiting_approval) + action_plans + operations
                                    (awaiting_approval) + approvals(pending, 계획·회차별 하나) + guard_checks
    사용자가 카드에서 결정       ─▶ decide: task·승인 행을 SELECT ... FOR UPDATE로 잠그고 pending일 때만 바꾼다.
                                    승인이면 실행 유효 시간(5분)을 정하고 operation도 approved/rejected/expired로
    모두 결정되면                ─▶ runner.resume_task가 build_results로 DeferredToolResults를 만들어 run을 잇는다
    승인된 도구 실행             ─▶ assert_approved가 step을 waiting_approval → running으로 바꾸며 "한 번만" 통과시킨다

이 모듈은 도구 파일(app/tools/*)이 assert_approved를 가져다 쓰므로 toolsets·tracking·runner를 import하지 않는다.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import DeferredToolResults, ToolApproved, ToolDenied
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.agent.plans import DECISION_WINDOW, EXECUTE_WINDOW, POLICY_VERSION, PlanDraft
from app.models import ActionPlan, AgentRun, Approval, Conversation, GuardCheck, Message, Operation, Step, Task
from app.models.enums import (
    ApprovalStatus,
    MessageRole,
    OperationStatus,
    RiskLevel,
    StepStatus,
    TaskOrigin,
    TaskStatus,
    VerdictKind,
)

# 거절을 모델에게 알리는 문장(명세). 모델은 이 문장을 도구 결과로 받고 다른 방법을 찾는다.
DENIAL_MESSAGE = "사용자가 거절함"
EXPIRED_MESSAGE = "승인을 30분 안에 받지 못해 작업을 취소했습니다. 필요하면 다시 요청해 주세요."
NOT_APPROVED_MESSAGE = "승인 기록이 없는 외부 도구 실행을 막았습니다."


# ---- 결정 결과와 오류 (라우터가 HTTP 응답으로 바꾼다) ----


class ApprovalError(Exception):
    code = "approval_error"


class ApprovalNotFoundError(ApprovalError):
    code = "not_found"


class TaskNotWaitingError(ApprovalError):
    code = "task_not_waiting"


class ApprovalExpiredError(ApprovalError):
    code = "approval_expired"


class MismatchConfirmationRequiredError(ApprovalError):
    code = "mismatch_confirmation_required"


@dataclass(frozen=True)
class Decision:
    approval_id: int
    task_id: int
    changed: bool  # False면 이미 결정된 승인이라 아무것도 바꾸지 않았다(두 번째 클릭)
    all_decided: bool  # True면 이 task에 pending 승인이 없다 → 재개할 차례


@dataclass(frozen=True)
class ApprovalRequest:
    """승인 요청 한 건. runner가 실행 계획 초안(plan)·preview·감시 결과를 미리 만들어 넘긴다.

    args는 서버가 정규화한 인자(plan.canonical_args)다. 모델의 원래 인자가 아니다.
    """

    tool_call_id: str
    plan: PlanDraft
    label: str
    summary: str
    verdict: VerdictKind
    reason: str
    guard_model: str

    @property
    def tool_name(self) -> str:
        return self.plan.tool_name

    @property
    def args(self) -> dict[str, Any]:
        return self.plan.canonical_args


# ---- 1. 승인 요청 저장 ----


async def current_round(session: AsyncSession, task_id: int) -> int:
    """지금 run의 회차(agent_runs.round). 회차 기록이 없으면(직접 만든 테스트 task) 1."""
    return await session.scalar(select(func.max(AgentRun.round)).where(AgentRun.task_id == task_id)) or 1


def plan_row(step: Step, user_id: int, round_: int, request: ApprovalRequest, now: datetime) -> ActionPlan:
    draft = request.plan
    return ActionPlan(
        operation_id=uuid.uuid4(),
        user_id=user_id,
        task_id=step.task_id,
        step_id=step.id,
        approval_round=round_,
        tool_call_id=request.tool_call_id,
        tool_name=draft.tool_name,
        tool_version=draft.tool_version,
        connection_id=draft.connection_id,
        target_resource_id=draft.target_resource_id,
        canonical_args=draft.canonical_args,
        args_hash=draft.args_hash,
        preview=request.summary,
        decision_expires_at=now + DECISION_WINDOW,
        policy_version=POLICY_VERSION,
    )


async def save_requests(
    session: AsyncSession,
    *,
    task_id: int,
    user_id: int,
    origin: TaskOrigin,
    requests: list[ApprovalRequest],
    now: datetime | None = None,
) -> None:
    """호출마다 step·실행 계획·operation·approval·guard_check를 만든다. 커밋은 호출한 쪽(runner)이 한다.

    예약·감시 작업은 승인을 만들 수 없다(명세: 자동 작업에는 READ 도구만, 승인 생성은 예외).
    """
    if origin != TaskOrigin.CHAT:
        raise ValueError(f"origin이 {origin}인 task는 승인을 만들 수 없다")
    now = now or datetime.now(UTC)
    round_ = await current_round(session, task_id)
    last_order = await session.scalar(select(func.max(Step.order)).where(Step.task_id == task_id)) or 0
    for offset, request in enumerate(requests, start=1):
        step = Step(
            task_id=task_id,
            order=last_order + offset,
            tool_name=request.tool_name,
            tool_call_id=request.tool_call_id,
            risk_level=RiskLevel.EXTERNAL,
            input=request.args,
            status=StepStatus.WAITING_APPROVAL,
            label=request.label,
        )
        session.add(step)
        await session.flush()
        await save_plan(session, step, user_id, round_, request, now)


async def save_plan(
    session: AsyncSession, step: Step, user_id: int, round_: int, request: ApprovalRequest, now: datetime
) -> None:
    """step 하나에 붙는 실행 계획·operation(awaiting_approval)·승인·감시 결과."""
    plan = plan_row(step, user_id, round_, request, now)
    session.add(plan)
    await session.flush()
    session.add(Operation(plan_id=plan.id, user_id=user_id, status=OperationStatus.AWAITING_APPROVAL))
    session.add(
        Approval(
            step_id=step.id,
            plan_id=plan.id,
            approval_round=round_,
            tool_call_id=request.tool_call_id,
            summary=request.summary,
            expires_at=plan.decision_expires_at,
        )
    )
    session.add(GuardCheck(step_id=step.id, verdict=request.verdict, reason=request.reason, model=request.guard_model))


async def set_operation_status(session: AsyncSession, plan_ids: Any, status: OperationStatus) -> None:
    """결정 전 operation(awaiting_approval)만 바꾼다. plan_ids는 id 목록이나 select 문."""
    await session.execute(
        update(Operation)
        .where(Operation.plan_id.in_(plan_ids), Operation.status == OperationStatus.AWAITING_APPROVAL)
        .values(status=status)
    )


# ---- 2. 사용자의 결정 ----


async def owned_task_id(session: AsyncSession, approval_id: int, user_id: int) -> int | None:
    return await session.scalar(
        select(Task.id)
        .join(Step, Step.task_id == Task.id)
        .join(Approval, Approval.step_id == Step.id)
        .where(Approval.id == approval_id, Task.user_id == user_id)
    )


async def pending_count(session: AsyncSession, task_id: int) -> int:
    count = await session.scalar(
        select(func.count(Approval.id))
        .join(Step, Step.id == Approval.step_id)
        .where(Step.task_id == task_id, Approval.status == ApprovalStatus.PENDING)
    )
    return count or 0


async def lock_task_and_approval(session: AsyncSession, task_id: int, approval_id: int) -> tuple[Task, Approval]:
    """task 행을 먼저, 승인 행을 다음에 잠근다. 같은 task의 결정이 한 줄로 서서 '마지막 결정'이 하나만 나온다."""
    task = await session.scalar(
        select(Task).where(Task.id == task_id).with_for_update().execution_options(populate_existing=True)
    )
    approval = await session.scalar(
        select(Approval).where(Approval.id == approval_id).with_for_update().execution_options(populate_existing=True)
    )
    assert task is not None and approval is not None
    return task, approval


async def decide(
    session: AsyncSession,
    approval_id: int,
    user_id: int,
    *,
    approve: bool,
    confirm_mismatch: bool = False,
    now: datetime | None = None,
) -> Decision:
    """승인 또는 거절. pending일 때만 바꾸고, 이미 결정된 승인은 그대로 둔 채 changed=False를 돌려준다."""
    now = now or datetime.now(UTC)
    task_id = await owned_task_id(session, approval_id, user_id)
    if task_id is None:
        raise ApprovalNotFoundError
    task, approval = await lock_task_and_approval(session, task_id, approval_id)
    if approval.status != ApprovalStatus.PENDING:
        all_decided = await pending_count(session, task_id) == 0
        await session.commit()
        return Decision(approval_id, task_id, changed=False, all_decided=all_decided)
    if task.status != TaskStatus.WAITING_APPROVAL:
        raise TaskNotWaitingError
    if approval.expires_at <= now:
        # 만료 정리 작업(6단계)이 생기기 전에도 만료된 승인은 승인되면 안 된다. 여기서 직접 검사하고 정리한다.
        await expire_task(session, task)
        await session.commit()
        raise ApprovalExpiredError
    if approve and not confirm_mismatch and await verdict_of(session, approval.step_id) == VerdictKind.MISMATCH:
        raise MismatchConfirmationRequiredError
    await apply_decision(session, approval, approve=approve, now=now)
    all_decided = await pending_count(session, task_id) == 0
    await session.commit()
    return Decision(approval_id, task_id, changed=True, all_decided=all_decided)


async def verdict_of(session: AsyncSession, step_id: int) -> VerdictKind | None:
    return await session.scalar(select(GuardCheck.verdict).where(GuardCheck.step_id == step_id))


async def apply_decision(session: AsyncSession, approval: Approval, *, approve: bool, now: datetime) -> None:
    approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    approval.decided_at = now
    if approve:
        approval.execute_before = now + EXECUTE_WINDOW
    if approval.plan_id is not None:
        status = OperationStatus.APPROVED if approve else OperationStatus.REJECTED
        await set_operation_status(session, [approval.plan_id], status)
    if not approve:
        step = await session.get_one(Step, approval.step_id)
        step.status = StepStatus.REJECTED


async def expire_task(session: AsyncSession, task: Task) -> None:
    """task의 pending 승인을 모두 expired로, 그 step도 expired로, task는 cancelled로 바꾼다."""
    step_ids = select(Step.id).where(Step.task_id == task.id)
    expired_steps = select(Approval.step_id).where(
        Approval.step_id.in_(step_ids), Approval.status == ApprovalStatus.PENDING
    )
    await session.execute(update(Step).where(Step.id.in_(expired_steps)).values(status=StepStatus.EXPIRED))
    expired_plans = select(Approval.plan_id).where(
        Approval.step_id.in_(step_ids), Approval.status == ApprovalStatus.PENDING, Approval.plan_id.is_not(None)
    )
    await set_operation_status(session, expired_plans, OperationStatus.EXPIRED)
    await session.execute(
        update(Approval)
        .where(Approval.step_id.in_(step_ids), Approval.status == ApprovalStatus.PENDING)
        .values(status=ApprovalStatus.EXPIRED)
    )
    task.status = TaskStatus.CANCELLED
    task.error = EXPIRED_MESSAGE
    session.add(
        Message(
            conversation_id=task.conversation_id, role=MessageRole.ASSISTANT, content=EXPIRED_MESSAGE, task_id=task.id
        )
    )
    conversation = await session.get_one(Conversation, task.conversation_id)
    conversation.updated_at = func.now()


# ---- 3. 재개할 때 모델에게 넘길 결정 ----


async def build_results(session: AsyncSession, task_id: int, pending_ids: list[str]) -> DeferredToolResults:
    """결과를 기다리는 호출(pending_ids)마다 승인=ToolApproved, 거절=ToolDenied('사용자가 거절함')."""
    rows = await session.scalars(
        select(Approval)
        .join(Step, Step.id == Approval.step_id)
        .where(Step.task_id == task_id, Approval.tool_call_id.in_(pending_ids))
    )
    decisions: dict[str, ToolApproved | ToolDenied] = {}
    for approval in rows:
        if approval.status == ApprovalStatus.APPROVED:
            decisions[approval.tool_call_id] = ToolApproved()
        elif approval.status == ApprovalStatus.REJECTED:
            decisions[approval.tool_call_id] = ToolDenied(DENIAL_MESSAGE)
    missing = set(pending_ids) - set(decisions)
    if missing:
        raise ValueError(f"결정되지 않은 승인이 있어 재개할 수 없다: {sorted(missing)}")
    return DeferredToolResults(approvals=dict(decisions))


# ---- 4. 두 번째 방어선 ----


async def assert_approved(ctx: RunContext[AgentDeps]) -> None:
    """EXTERNAL 도구 함수의 첫 줄. DB에 이 호출의 승인이 approved이고 아직 실행 전일 때만 통과한다.

    통과하면서 step을 waiting_approval → running으로 바꾼다(행 잠금 안에서). 같은 승인으로 두 번 실행하려 하면
    두 번째는 step이 이미 running/done이라 PermissionError다. 테스트 밖에서 이 검사를 건너뛰는 코드는 없다.
    """
    if not ctx.tool_call_id:
        raise PermissionError(NOT_APPROVED_MESSAGE)
    async with ctx.deps.db() as session:
        row = (
            await session.execute(
                select(Approval, Step)
                .join(Step, Step.id == Approval.step_id)
                .join(Task, Task.id == Step.task_id)
                .where(
                    Approval.tool_call_id == ctx.tool_call_id,
                    Step.task_id == ctx.deps.task_id,
                    Task.user_id == ctx.deps.user_id,
                )
                .with_for_update(of=[Approval, Step])
            )
        ).one_or_none()
        if row is None or not is_runnable(row[0], row[1], ctx.tool_name):
            raise PermissionError(NOT_APPROVED_MESSAGE)
        row[1].status = StepStatus.RUNNING
        await session.commit()


def is_runnable(approval: Approval, step: Step, tool_name: str | None) -> bool:
    return (
        approval.status == ApprovalStatus.APPROVED
        and step.status == StepStatus.WAITING_APPROVAL
        and step.tool_name == tool_name
    )
