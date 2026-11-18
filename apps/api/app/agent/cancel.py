"""작업 취소(1단계 보강, BUILD_SPEC 4.6).

취소는 이미 끝난 행동을 되돌리지 않는다. 취소 요청 뒤에 새로 시작하는 일(모델 호출, 도구 실행)만 막는다.

    queued, waiting_approval  → 바로 cancelled. 결정 전 승인은 cancelled, 실행하지 않은 단계는 실패로 닫고
                                답변을 남긴다. 승인했지만 실행 전인 외부 작업(operation approved)도 cancelled.
                                늦게 시작한 워크플로는 claim에서 queued가 아니라 아무것도 하지 않는다.
    running                   → cancel_requested. 실행 쪽(tracking.StepTracking)이 다음 모델 호출·새 도구 실행 직전에
                                확인하고 TaskCancelledError로 멈춘다 → runner가 cancelled로 저장한다.
                                마지막 답을 이미 받았으면 그대로 done(이미 한 일을 숨기지 않는다).
                                이미 나간 외부 요청은 끝까지 결과를 기록한다(영수증). 실행기(executor)가 task 행을
                                FOR SHARE로 잠근 채 시작하므로 취소와 실행 시작은 한 줄로 선다(3단계 보강).
    cancel_requested, cancelled → 그대로(같은 요청을 다시 보내도 같은 결과)
    그 밖의 끝난 상태          → CancelNotAllowedError(409)

진입점:
- POST /api/tasks/{id}/cancel(app/routers/tasks.py) → request_cancel. None이면 404, CancelNotAllowedError면 409.
- tracking.StepTracking → raise_if_cancel_requested(모델 요청 직전, 새 도구 실행 직전).
- runner → TaskCancelledError를 잡아 RUN_CANCELLED_MESSAGE로 cancelled 저장, 끝낼 때 completed_external_note,
  NOT_STARTED(실행 전 operation 상태 목록)도 같이 쓴다.
근거: BUILD_SPEC 4.6, DECISIONS 2026-11-17 "취소(POST /api/tasks/{id}/cancel)".

고칠 때 주의:
- 상태 전이(task·approval·operation·step)를 바꾸면 docs/STATE_MACHINES.md의 표를 같이 고친다. 표에 적힌 함수 이름
  (cancel.request_cancel, cancel.close_for_cancel, cancel.completed_external_note)은 tests/test_state_machines.py가
  이 모듈에 있는지 확인하므로 이름을 바꾸면 문서도 바꾼다.
- 답변 문구(CANCELLED_REPLY 등)는 화면과 대화 기록에 그대로 보인다. tests/test_cancel.py는 "취소"가 들어 있는지만 본다.
- 테스트: tests/test_cancel.py(흐름), tests/test_cancel_race.py(취소와 실행 시작이 겹칠 때).
"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.approvals import set_operation_status
from app.agent.deps import AgentDeps
from app.agent.results import not_executed_output
from app.models import ActionPlan, Approval, Conversation, ExecutionReceipt, Message, Operation, Step, Task
from app.models.enums import ApprovalStatus, MessageRole, OperationStatus, StepStatus, TaskStatus

# 대기 중 취소(close_for_cancel)의 답변. 실행 중 취소의 답변은 RUN_CANCELLED_MESSAGE(runner가 저장)다.
CANCELLED_REPLY = "작업을 취소했습니다. 취소 전에 끝난 단계는 위 기록에 그대로 남아 있습니다."
RUN_CANCELLED_MESSAGE = "취소 요청에 따라 멈췄습니다. 위 단계까지만 처리되었습니다."
# 승인 대기 step의 output 문장. not_executed_output으로 감싸 "실행 안 함이 확실"을 unknown과 구별한다.
NOT_EXECUTED_ON_CANCEL = "작업을 취소해 실행하지 않았습니다."
# 아직 아무 모델 호출·도구 실행이 진행 중이 아니라서 바로 닫아도 되는 상태.
CANCEL_IMMEDIATELY = (TaskStatus.QUEUED, TaskStatus.WAITING_APPROVAL)
# 외부 요청을 아직 보내지 않은 operation. executing·unknown은 이미 나갔을 수 있어 넣지 않는다(결과를 숨기지 않기 위해).
NOT_STARTED = (OperationStatus.AWAITING_APPROVAL, OperationStatus.APPROVED)
# 같은 취소 요청을 다시 보내도 같은 결과(멱등)가 되게 그대로 돌려주는 상태.
ALREADY_CANCELLING = (TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELLED)


class TaskCancelledError(Exception):
    """실행 쪽이 취소 요청을 발견했다. 다음 모델 호출·도구 실행을 시작하지 않는다.

    StepTracking이 흐름 제어 예외로 그대로 올려 보내고(도구 실패로 바꾸지 않음),
    runner.execute_agent가 잡아 cancelled로 끝낸다.
    """


class CancelNotAllowedError(Exception):
    """이미 끝난 작업이라 취소할 것이 없다. 인자는 그때의 TaskStatus. router가 409로 바꾼다."""


async def completed_external_note(session: AsyncSession, task_id: int) -> str:
    """취소 답변 끝에 붙일 문장: 취소 전에 끝난 외부 작업(영수증 있음)은 되돌리지 않았고 숨기지도 않는다(3단계 보강).

    영수증이 있는 succeeded operation만 센다(절대 원칙 10: 성공 표시는 영수증 근거). 없으면 빈 문자열.
    읽기만 하고 커밋하지 않는다. task 소유자 확인은 부른 쪽(request_cancel의 잠금, runner의 task)이 이미 했다.
    """
    count = await session.scalar(
        select(func.count(ExecutionReceipt.id))
        .join(Operation, Operation.id == ExecutionReceipt.operation_id)
        .join(ActionPlan, ActionPlan.id == Operation.plan_id)
        .where(ActionPlan.task_id == task_id, Operation.status == OperationStatus.SUCCEEDED)
    )
    if not count:
        return ""
    return f" 취소 전에 끝난 외부 작업 {count}건은 되돌리지 않았습니다. 승인 카드에서 결과를 확인해 주세요."


async def is_cancel_requested(deps: AgentDeps) -> bool:
    """지금 task가 cancel_requested인지 짧은 세션을 따로 열어 읽는다(잠그지 않음).

    실행 중인 run의 다른 트랜잭션과 섞이지 않게 매번 새로 읽는다. deps.task_id는 서버가 만든 값이라 user_id 조건이 없다.
    """
    async with deps.db() as session:
        current = await session.scalar(select(Task.status).where(Task.id == deps.task_id))
    return current == TaskStatus.CANCEL_REQUESTED


async def raise_if_cancel_requested(deps: AgentDeps) -> None:
    """취소 요청이면 TaskCancelledError를 던진다. StepTracking이 모델 요청 직전과 새 도구 실행 직전에 부른다.

    이미 기록된 도구 호출을 복구 때 재생하는 경로에서는 부르지 않는다(이미 한 일은 숨기지 않는다).
    """
    if await is_cancel_requested(deps):
        raise TaskCancelledError(deps.task_id)


async def lock_owned_task(session: AsyncSession, task_id: int, user_id: int) -> Task | None:
    """내 task를 FOR UPDATE로 잠가 읽는다. 남의 task나 없는 task는 None(router가 404로 바꿔 존재를 숨긴다).

    잠금은 부른 쪽 트랜잭션이 끝날 때까지 간다. 실행기·runner가 같은 행을 잠그므로 취소와 상태 변경이 한 줄로 선다.
    populate_existing: 세션에 이미 올라온 Task 객체가 있어도 잠근 뒤의 최신 값으로 덮어써 판단한다.
    """
    return await session.scalar(
        select(Task)
        .where(Task.id == task_id, Task.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def request_cancel(session: AsyncSession, task_id: int, user_id: int) -> TaskStatus | None:
    """취소를 요청하고 바뀐(또는 지금) 상태를 돌려준다. 내 task가 아니면 None.

    부수 효과: task 행 FOR UPDATE 잠금 → 상태 변경 → 커밋까지 여기서 한다. 끝난 상태면 CancelNotAllowedError(커밋 없음).
    running은 표시만 바꾼다. 실제로 멈추는 일은 실행 쪽(StepTracking)이 하므로, 이미 나간 모델·외부 요청은 끝까지 간다.
    """
    task = await lock_owned_task(session, task_id, user_id)
    if task is None:
        return None
    if task.status in ALREADY_CANCELLING:
        return task.status
    if task.status == TaskStatus.RUNNING:
        task.status = TaskStatus.CANCEL_REQUESTED
    elif task.status in CANCEL_IMMEDIATELY:
        await close_for_cancel(session, task)
    else:
        raise CancelNotAllowedError(task.status)
    status = task.status
    await session.commit()
    return status


async def close_for_cancel(session: AsyncSession, task: Task) -> None:
    """실행하지 않을 승인·단계를 닫고 task를 cancelled로 바꾼 뒤 답변을 남긴다(커밋은 부른 쪽).

    task 행은 부른 쪽이 이미 잠갔어야 한다(request_cancel의 lock_owned_task).
    그래야 승인 결정·실행 시작과 겹치지 않는다.
    부수 효과: operation(NOT_STARTED만)·approval(pending만)·step(waiting_approval만) 갱신, assistant 메시지 추가,
    대화 updated_at 갱신(대화 목록 정렬). 이미 실행된 단계와 영수증은 건드리지 않는다.
    """
    step_ids = select(Step.id).where(Step.task_id == task.id)
    # 결정 전이거나, 승인했지만 아직 실행을 시작하지 않은 외부 작업은 실행하지 않는다(3단계 보강).
    task_plans = select(ActionPlan.id).where(ActionPlan.task_id == task.id)
    await set_operation_status(session, task_plans, OperationStatus.CANCELLED, NOT_STARTED)
    await session.execute(
        update(Approval)
        .where(Approval.step_id.in_(step_ids), Approval.status == ApprovalStatus.PENDING)
        .values(status=ApprovalStatus.CANCELLED, decided_at=func.now())
    )
    await session.execute(
        update(Step)
        .where(Step.task_id == task.id, Step.status == StepStatus.WAITING_APPROVAL)
        .values(status=StepStatus.FAILED, ok=False, output=not_executed_output(NOT_EXECUTED_ON_CANCEL))
    )
    reply = CANCELLED_REPLY + await completed_external_note(session, task.id)
    task.status = TaskStatus.CANCELLED
    task.error = reply
    session.add(
        Message(conversation_id=task.conversation_id, role=MessageRole.ASSISTANT, content=reply, task_id=task.id)
    )
    conversation = await session.get_one(Conversation, task.conversation_id)
    conversation.updated_at = func.now()
