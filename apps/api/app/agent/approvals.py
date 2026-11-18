"""승인: EXTERNAL 도구 호출을 사람이 건별로 승인·거절하는 기록과 검사.

이중 방어 (docs/SAFETY.md):
    1. 프레임워크: external_tools는 requires_approval=True다. 모델이 이 도구를 부르면 Pydantic AI가 실행하지 않고
       run을 DeferredToolRequests로 끝낸다. → save_requests가 승인 요청을 DB에 남긴다.
    2. DB: EXTERNAL 도구 함수의 첫 줄은 executor.begin_execution(ctx, 인자)다(2단계 보강). 실행 계획·승인·operation을
       BUILD_SPEC 3.3대로 모두 검사한다. 프레임워크 설정 하나가 바뀌거나 누가 함수를 직접 불러도 실행되지 않는다.

흐름:
    run 결과가 DeferredToolRequests ─▶ runner가 plans.draft_plan으로 인자를 정규화·고정(2단계 보강)
                                 ─▶ save_requests: steps(waiting_approval) + action_plans + operations
                                    (awaiting_approval) + approvals(pending, 계획·회차별 하나) + guard_checks
    사용자가 카드에서 결정       ─▶ decide: task·승인 행을 SELECT ... FOR UPDATE로 잠그고 pending일 때만 바꾼다.
                                    승인이면 실행 유효 시간(5분)을 정하고 operation도 approved/rejected/expired로
    모두 결정되면                ─▶ runner.resume_task가 build_results로 DeferredToolResults를 만들어 run을 잇는다
    승인된 도구 실행             ─▶ 저장된 인자(override_args)로 실행, executor.begin_execution이 operation을
                                    approved → executing으로 바꾸며 "한 번만" 통과시킨다. 결과는 finish_execution

이 모듈은 cancel.py가 가져다 쓰므로 toolsets·tracking·runner를 import하지 않는다.

진입점(누가 부르는지):
    save_requests      runner.finish(승인 대기 결과를 task 결과와 같은 트랜잭션에 저장), tests/plan_helpers.py
    decide             routers/approvals.py(POST 승인·거절·고쳐서 다시). ApprovalError를 detail {code, message}로 바꾼다
    request_revision   routers/approvals.py(고쳐서 다시), evals/harness.py(고쳐서 다시 사례). 거절만 한다
    pending_count      runner(재개 claim), workflows.py(재개 판단)
    build_results      runner.load_resume_input(재개 run의 입력)
    expire_task        automation/check.expire_overdue_approvals(1분 검사), decide(만료 뒤 결정 요청)
    set_operation_status  cancel.close_for_cancel, runner.close_unexecuted_steps(결정 전·실행 전 operation 닫기)

근거: BUILD_SPEC 3.1~3.4(실행 계획, DB 승인 검사, 승인 결정), 4.4(outbox), 절대 원칙 3·4·7.
결정: docs/DECISIONS.md 2단계(2026-11-17 decide 잠금 순서 task → 승인, 만료를 API에서 직접 검사, mismatch는 서버도
confirm_mismatch 강제, 오류 code), "2단계 보강"(계획:operation:승인 = 1:1:1, override_args),
"3단계 보강"(outbox 재개 요청).

고칠 때 주의:
- 승인·operation·step·task 상태 전이를 바꾸면 docs/STATE_MACHINES.md를 같이 고친다. "누가" 칸에 save_plan,
  save_requests, apply_decision, decide, expire_task가 적혀 있어 이름을 바꾸면 tests/test_state_machines.py가 실패한다.
- ApprovalError의 code 값은 app/schemas/tasks.py의 ApprovalErrorCode(Literal), routers/approvals.py의 ERROR_STATUS·
  ERROR_MESSAGES, 웹 apps/web/lib/approvals.ts와 맞물린다. 바꾸면 `pnpm --dir apps/web gen:api`도 한다.
- DENIAL_MESSAGE는 모델이 받는 도구 결과다(명세 문장). 바꾸면 routers/approvals.py 거절 설명과
  생성된 웹 타입 설명도 고친다.
- 시간 창은 plans.DECISION_WINDOW·EXECUTE_WINDOW에 있다. EXPIRED_MESSAGE의 "30분"과 맞춘다.
- 테스트: tests/test_approvals.py, test_approval_revise.py('고쳐서 다시'), test_approval_card_api.py,
  test_action_plans.py, test_cancel_race.py(동시 결정·취소), test_automation_runs.py(자동 작업은 승인을 만들 수 없음).
  테이블 칸을 바꾸면 마이그레이션과 approvals.plan_row.

변경 이력:
- 2026-11-18 '고쳐서 다시'(요구사항 밖, 사용자 결정): decide·apply_decision에 revision_note, build_results가
  denial_message로 메모를 모델에 전달. 새 승인 상태는 만들지 않았다(거절 + 메모).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic_ai.tools import DeferredToolResults, ToolApproved, ToolDenied
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import outbox
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
# '고쳐서 다시'(요구사항 밖, 2026-11-18 사용자 결정)일 때 DENIAL_MESSAGE 뒤에 붙는 말. 프롬프트(prompts.py)가
# 이 말을 보고 같은 도구를 고친 인자로 다시 부르라고 안내한다.
# 바꾸면 prompts.py와 tests/test_approval_revise.py도 고친다.
REVISION_PREFIX = "고쳐 달라는 내용: "


def denial_message(revision_note: str | None) -> str:
    """모델이 받을 거절 결과. 메모가 없으면 명세 문장 그대로, 있으면 "사용자가 거절함. 고쳐 달라는 내용: …".

    메모는 사용자 본인이 카드에 적은 글이다(도구 결과처럼 외부에서 온 글이 아님). 서버는 인자를 고치지 않고
    모델이 다시 계획하게 한다. 그래야 새 계획도 정규화·감시·preview·승인을 모두 다시 거친다(절대 원칙 3·4).
    """
    if revision_note is None:
        return DENIAL_MESSAGE
    return f"{DENIAL_MESSAGE}. {REVISION_PREFIX}{revision_note}"


# 만료로 task를 취소할 때 남기는 assistant 메시지(고정 문구). "30분"은 plans.DECISION_WINDOW와 같아야 한다.
EXPIRED_MESSAGE = "승인을 30분 안에 받지 못해 작업을 취소했습니다. 필요하면 다시 요청해 주세요."


# ---- 결정 결과와 오류 (라우터가 HTTP 응답으로 바꾼다) ----
# code는 화면이 문구가 아니라 code로 분기하게 하려는 값이다(404 not_found, 409 task_not_waiting·
# mismatch_confirmation_required, 410 approval_expired). 값을 바꾸면 파일 머리 "고칠 때 주의"의 곳을 함께 고친다.


class ApprovalError(Exception):
    code = "approval_error"


class ApprovalNotFoundError(ApprovalError):
    """승인이 없거나 다른 사용자의 것. 둘을 구분하지 않아 남의 승인 id가 있는지 드러나지 않는다."""

    code = "not_found"


class TaskNotWaitingError(ApprovalError):
    """pending 승인인데 task가 승인 대기가 아님(취소됨 등). 끝난 task의 승인을 살려 실행하는 것을 막는다."""

    code = "task_not_waiting"


class ApprovalExpiredError(ApprovalError):
    """결정 마감(30분)이 지남. decide가 만료 정리를 커밋한 뒤 던진다."""

    code = "approval_expired"


class MismatchConfirmationRequiredError(ApprovalError):
    """감시 판정이 mismatch인데 confirm_mismatch 없이 승인.

    화면 버그로 "한 번 더 묻기"를 건너뛰지 못하게 서버도 막는다.
    """

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
    preview·감시는 모델 호출이 있어 DB 트랜잭션 밖에서 미리 만든다(트랜잭션을 오래 열어 두지 않기 위해).
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
    """지금 run의 회차(agent_runs.round). 회차 기록이 없으면(직접 만든 테스트 task) 1.

    모델이 만든 tool_call_id는 회차를 넘어 겹칠 수 있어(BUILD_SPEC 3.2) 계획·승인을
    (task, 회차, tool_call_id)로 구분한다.
    """
    return await session.scalar(select(func.max(AgentRun.round)).where(AgentRun.task_id == task_id)) or 1


def plan_row(step: Step, user_id: int, round_: int, request: ApprovalRequest, now: datetime) -> ActionPlan:
    """계획 초안을 action_plans 행으로 옮긴다(아직 add 전). 저장 뒤에는 바꾸지 않는다.

    operation_id는 여기서 서버가 만든다(모델이 고를 수 없음, BUILD_SPEC 3.2). 외부 서비스 멱등성 키로 쓰인다.
    계획 칸을 더하면 이 함수, app/models, 마이그레이션, plans.PlanDraft를 함께 고친다.
    """
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
        target_version=draft.target_version,
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
    불변 조건: task 결과 저장과 같은 트랜잭션이어야 한다(승인 요청만 남고 task가 승인 대기가 아닌 상태를 만들지 않는다).
    step 순서는 이 task의 마지막 step 뒤로 이어 붙인다. step.input도 정규화한 인자다.
    """
    # 자동 작업이 사람 없는 시각에 외부 쓰기 승인 카드를 쌓는 것을 막는다(절대 원칙 7). 코드 버그면 크게 실패시킨다.
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
    """step 하나에 붙는 실행 계획·operation(awaiting_approval)·승인·감시 결과.

    계획 1 : operation 1 : 승인 1(DECISIONS 2단계 보강). operation은 planned를 거치지 않고 곧바로 awaiting_approval이다
    (승인 없이 저장되는 계획이 없다). 승인 마감(expires_at)은 계획의 decision_expires_at과 같은 값이다.
    """
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


async def set_operation_status(
    session: AsyncSession,
    plan_ids: Any,
    status: OperationStatus,
    from_statuses: tuple[OperationStatus, ...] = (OperationStatus.AWAITING_APPROVAL,),
) -> None:
    """from_statuses(기본: 결정 전)인 operation만 바꾼다. plan_ids는 id 목록이나 select 문.

    조건부 UPDATE(WHERE status IN)라 행을 따로 잠그지 않는다. 이미 executing·succeeded 등으로 넘어간 operation을
    취소·만료가 덮어써 "실행했는데 cancelled"로 보이는 사고를 막는다. 커밋은 호출한 쪽.
    """
    await session.execute(
        update(Operation)
        .where(Operation.plan_id.in_(plan_ids), Operation.status.in_(from_statuses))
        .values(status=status)
    )


# ---- 2. 사용자의 결정 ----


async def owned_task_id(session: AsyncSession, approval_id: int, user_id: int) -> int | None:
    """승인이 속한 task id. 다른 사용자의 승인이거나 없으면 None(→ ApprovalNotFoundError, 404). 읽기만 한다."""
    return await session.scalar(
        select(Task.id)
        .join(Step, Step.task_id == Task.id)
        .join(Approval, Approval.step_id == Step.id)
        .where(Approval.id == approval_id, Task.user_id == user_id)
    )


async def pending_count(session: AsyncSession, task_id: int) -> int:
    """이 task에서 아직 결정되지 않은(pending) 승인 수. 0이면 재개할 수 있다."""
    count = await session.scalar(
        select(func.count(Approval.id))
        .join(Step, Step.id == Approval.step_id)
        .where(Step.task_id == task_id, Approval.status == ApprovalStatus.PENDING)
    )
    return count or 0


async def lock_task_and_approval(session: AsyncSession, task_id: int, approval_id: int) -> tuple[Task, Approval]:
    """task 행을 먼저, 승인 행을 다음에 잠근다. 같은 task의 결정이 한 줄로 서서 '마지막 결정'이 하나만 나온다.

    populate_existing: 같은 세션에 먼저 읽은 옛 값이 있어도 잠근 뒤의 최신 값으로 덮어 판단한다.
    잠금 순서(task → 승인)를 바꾸면 다른 경로(취소 등 task를 먼저 잠그는 곳)와 교착될 수 있다.
    """
    task = await session.scalar(
        select(Task).where(Task.id == task_id).with_for_update().execution_options(populate_existing=True)
    )
    approval = await session.scalar(
        select(Approval).where(Approval.id == approval_id).with_for_update().execution_options(populate_existing=True)
    )
    assert task is not None and approval is not None
    return task, approval


# 결과·예외: 바꿨으면 changed=True, 이미 결정됐으면 changed=False(같은 결정 재요청은 기존 상태, BUILD_SPEC 3.4).
# ApprovalNotFoundError·TaskNotWaitingError·MismatchConfirmationRequiredError는 커밋 없이 던진다(라우터가 rollback).
# ApprovalExpiredError는 만료 정리를 커밋한 뒤 던진다. 부수 효과: task·승인 FOR UPDATE, operation·step 상태, 커밋.
async def decide(
    session: AsyncSession,
    approval_id: int,
    user_id: int,
    *,
    approve: bool,
    confirm_mismatch: bool = False,
    revision_note: str | None = None,
    now: datetime | None = None,
) -> Decision:
    """승인 또는 거절. pending일 때만 바꾸고, 이미 결정된 승인은 그대로 둔 채 changed=False를 돌려준다.

    revision_note는 거절('고쳐서 다시')에만 쓴다. 이미 결정된 승인이면 메모도 바꾸지 않는다(첫 결정이 남는다).
    """
    now = now or datetime.now(UTC)
    task_id = await owned_task_id(session, approval_id, user_id)
    if task_id is None:
        raise ApprovalNotFoundError
    task, approval = await lock_task_and_approval(session, task_id, approval_id)
    if approval.status != ApprovalStatus.PENDING:
        # 승인·거절이 경합하면 먼저 확정된 결과를 유지한다. 두 번째 요청은 아무것도 바꾸지 않는다.
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
    # 감시가 "요청과 다를 수 있음"이라 본 행동은 사용자가 한 번 더 확인해야 승인된다. 거절에는 확인이 필요 없다.
    if approve and not confirm_mismatch and await verdict_of(session, approval.step_id) == VerdictKind.MISMATCH:
        raise MismatchConfirmationRequiredError
    await apply_decision(session, approval, approve=approve, now=now, revision_note=revision_note)
    all_decided = await pending_count(session, task_id) == 0
    if all_decided:
        # 3단계 보강: 재개 요청을 결정과 같은 트랜잭션에 남긴다(커밋 직후 서버가 죽어도 재개된다).
        await outbox.add_resume_event(session, task, now)
    await session.commit()
    return Decision(approval_id, task_id, changed=True, all_decided=all_decided)


async def request_revision(
    session: AsyncSession, approval_id: int, user_id: int, note: str, *, now: datetime | None = None
) -> Decision:
    """'고쳐서 다시': 메모를 붙여 거절만 한다(승인으로 바꾸는 길이 없다). 결과·예외·커밋은 decide와 같다.

    API(routers/approvals.py revise_action)와 eval 실행기(evals/harness.py)가 함께 쓴다. eval 디렉터리에는 승인을
    결정하는 코드를 둘 수 없으므로(tests/test_evals.py 소스 검사), 거절만 가능한 이 함수를 따로 둔다.
    note는 호출하는 쪽이 앞뒤 공백을 지우고 1~300자로 검사한 값이어야 한다(API는 ReviseRequest가 검사).
    """
    return await decide(session, approval_id, user_id, approve=False, revision_note=note, now=now)


async def verdict_of(session: AsyncSession, step_id: int) -> VerdictKind | None:
    """그 step의 감시 판정(guard_checks). 없으면 None(mismatch가 아니므로 확인 없이 승인 가능)."""
    return await session.scalar(select(GuardCheck.verdict).where(GuardCheck.step_id == step_id))


async def apply_decision(
    session: AsyncSession, approval: Approval, *, approve: bool, now: datetime, revision_note: str | None = None
) -> None:
    """승인 행·operation·step에 결정을 적는다(잠금·커밋은 decide). 승인이면 실행 유효 시간을 지금부터 정한다.

    승인한 step은 waiting_approval 그대로다(실행기 begin_execution이 running으로 바꾼다). 거절한 step만 rejected.
    plan_id가 없는 승인(2단계 보강 전)은 operation이 없어 승인 행만 바꾼다.
    '고쳐서 다시'는 상태로는 그냥 거절과 같고(새 상태 없음) revision_note만 남는다. 승인에는 메모를 붙이지 않는다.
    """
    approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    approval.decided_at = now
    if not approve:
        approval.revision_note = revision_note
    if approve:
        approval.execute_before = now + EXECUTE_WINDOW
    if approval.plan_id is not None:
        status = OperationStatus.APPROVED if approve else OperationStatus.REJECTED
        await set_operation_status(session, [approval.plan_id], status)
    if not approve:
        step = await session.get_one(Step, approval.step_id)
        step.status = StepStatus.REJECTED


async def expire_task(session: AsyncSession, task: Task) -> None:
    """task의 pending 승인을 모두 expired로, 그 step도 expired로, task는 cancelled로 바꾼다.

    호출한 쪽이 task 행을 잠그고 waiting_approval인지 확인한 뒤 부른다(decide, check.expire_overdue_approvals).
    부수 효과: operation awaiting_approval → expired, assistant 메시지(EXPIRED_MESSAGE) 추가, 대화 updated_at 갱신.
    커밋은 호출한 쪽. step을 rejected가 아닌 expired로 두는 이유: "사용자가 거절함"과 구분하려고(DECISIONS 2026-11-17).
    """
    step_ids = select(Step.id).where(Step.task_id == task.id)
    expired_steps = select(Approval.step_id).where(
        Approval.step_id.in_(step_ids), Approval.status == ApprovalStatus.PENDING
    )
    # 순서가 중요하다: 승인을 expired로 바꾸기 전에 pending 조건으로 step·operation을 먼저 고른다.
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
    """결과를 기다리는 호출(pending_ids)마다 승인=ToolApproved, 거절=ToolDenied(denial_message: '사용자가 거절함',
    '고쳐서 다시'면 고칠 내용을 덧붙인 문장).

    승인은 계획에 저장된 정규화 인자를 override_args로 넘긴다(2단계 보강). 그래서 재개한 run의 도구는 기록 속
    모델 인자가 아니라 승인된 인자로 실행된다. 같은 tool_call_id가 여러 회차에 있으면 가장 최근 회차를 쓴다.
    approval_round 오름차순으로 읽어 뒤 회차가 앞 회차를 덮어쓰게 한 것이 "가장 최근 회차"의 구현이다.
    approved·rejected가 아닌 호출이 하나라도 남으면 ValueError(Pydantic AI는 대기 호출 전부에 결과가 있어야 한다).
    읽기만 한다. 계획이 없는 옛 승인(2단계 보강 전)은 stored_args가 None이라 기록 속 인자를 쓴다.
    """
    rows = await session.execute(
        select(Approval, ActionPlan.canonical_args)
        .join(Step, Step.id == Approval.step_id)
        .outerjoin(ActionPlan, ActionPlan.id == Approval.plan_id)
        .where(Step.task_id == task_id, Approval.tool_call_id.in_(pending_ids))
        .order_by(Approval.approval_round)
    )
    decisions: dict[str, ToolApproved | ToolDenied] = {}
    for approval, stored_args in rows:
        if approval.status == ApprovalStatus.APPROVED:
            decisions[approval.tool_call_id] = ToolApproved(override_args=stored_args)
        elif approval.status == ApprovalStatus.REJECTED:
            decisions[approval.tool_call_id] = ToolDenied(denial_message(approval.revision_note))
    missing = set(pending_ids) - set(decisions)
    if missing:
        raise ValueError(f"결정되지 않은 승인이 있어 재개할 수 없다: {sorted(missing)}")
    return DeferredToolResults(approvals=dict(decisions))
