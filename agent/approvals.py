"""
승인 게이트. 앱 밖에 흔적이 남는 행동(EXTERNAL)은 이 파일을 거쳐야만 실행된다.

  request_approval  루프가 EXTERNAL 호출을 만나면 부른다. preview -> 감시 에이전트 -> Approval(pending).
  approve / reject  사용자가 승인 카드에서 누른다. pending일 때만 상태가 바뀐다(두 번째 클릭은 무시).
  execute_approved  승인된 Step을 실행하는 유일한 경로. 승인이 없으면 PermissionError.
  expire_old        30분이 지난 승인 요청을 만료시키고 Task를 취소한다(3단계 워커가 주기적으로 부른다).

"정확히 한 번 실행"을 지키는 방법
  1) Approval 행을 select_for_update로 잠그고 상태를 확인한다.
  2) Step을 waiting_approval -> running으로 바꾸는 UPDATE 한 번으로 실행권을 가져온다.
     이 UPDATE는 동시에 여러 요청이 와도 한 요청만 성공한다(SQLite, PostgreSQL 모두).
  3) 외부 호출은 잠금을 푼 뒤에 한다. 외부 API가 10초 걸려도 DB를 오래 붙잡지 않기 위해서다.
     실행권은 이미 가져왔으므로 그 사이 두 번째 요청이 와도 2)에서 막힌다.
"""

import logging
import time

from django.db import transaction
from django.utils import timezone

from agent import guard, registry
from agent.models import Approval, Step, Task
from chat.models import Message

logger = logging.getLogger(__name__)

REJECTED_BY_USER = "사용자가 거절함"
EXPIRED_MESSAGE = "승인 시간(30분)이 지나 작업을 취소했습니다. 필요하면 다시 요청해 주세요."


def request_approval(step, guard_llm):
    """승인 요청을 만들고 Task를 멈춘다. 자동으로 도는 작업(예약, 감시)은 승인을 만들 수 없다."""
    task = step.task
    if task.origin != Task.Origin.CHAT:
        # 스스로 도는 작업은 조회만 한다(절대 원칙 4). 루프가 먼저 막지만 여기서 한 번 더 막는다.
        raise PermissionError("자동으로 도는 작업에서는 외부 행동 승인을 요청할 수 없습니다.")
    tool = registry.get_tool(step.tool_name)
    preview = registry.preview_text(tool, task.user, step.input_json)
    guard.check(step, preview, guard_llm)  # 경고를 보탤 뿐, 결과와 상관없이 승인 요청은 만든다
    with transaction.atomic():
        approval = Approval.objects.create(step=step, summary=preview)
        Step.objects.filter(id=step.id).update(status=Step.Status.WAITING_APPROVAL)
        Task.objects.filter(id=task.id).update(status=Task.Status.WAITING_APPROVAL)
    # 3단계: 여기서 notify(user, "approval_needed", url)로 푸시를 보낸다.
    return approval


def approve(approval_id, user):
    """승인한다. pending이 아니면 아무것도 바꾸지 않고 현재 상태를 돌려준다."""
    with transaction.atomic():
        approval = _lock(approval_id, user)
        if approval.status != Approval.Status.PENDING:
            return approval
        if approval.expires_at <= timezone.now():
            _expire(approval)
            return approval
        approval.status = Approval.Status.APPROVED
        approval.decided_at = timezone.now()
        approval.save(update_fields=["status", "decided_at", "updated_at"])
    return approval


def reject(approval_id, user):
    """거절한다. 도구는 실행하지 않고, LLM이 '사용자가 거절함'을 보고 다른 방법을 찾도록 Task를 이어 간다."""
    with transaction.atomic():
        approval = _lock(approval_id, user)
        if approval.status != Approval.Status.PENDING:
            return approval
        if approval.expires_at <= timezone.now():
            _expire(approval)
            return approval
        approval.status = Approval.Status.REJECTED
        approval.decided_at = timezone.now()
        approval.save(update_fields=["status", "decided_at", "updated_at"])
        _close_step(approval.step, Step.Status.REJECTED, registry.error_result(REJECTED_BY_USER))
        _requeue(approval.step.task_id)
    return approval


def resume_after_approval(approval_id):
    """승인된 Step을 실행하고 Task를 이어 갈 수 있게 한다. 이번 요청이 실제로 실행했으면 True."""
    approval = Approval.objects.select_related("step").get(id=approval_id)
    if approval.status != Approval.Status.APPROVED:
        return False
    try:
        execute_approved(approval.step)
    except PermissionError:
        return False  # 이미 다른 요청이 실행했다(버튼 연속 클릭)
    return True


def execute_approved(step):
    """EXTERNAL 도구를 실행하는 유일한 경로. 위 모듈 설명의 1) 2) 3) 순서를 따른다."""
    with transaction.atomic():
        approval = Approval.objects.select_for_update().filter(step_id=step.id).first()  # 1)
        if approval is None or approval.status != Approval.Status.APPROVED:
            raise PermissionError("승인되지 않은 외부 행동은 실행할 수 없습니다.")
        claimed = Step.objects.filter(id=step.id, status=Step.Status.WAITING_APPROVAL).update(
            status=Step.Status.RUNNING
        )  # 2)
        if not claimed:
            raise PermissionError("이미 실행했거나 처리 중인 단계입니다.")
    approval = Approval.objects.select_related("step__task__user").get(id=approval.id)
    started = time.monotonic()
    tool = registry.get_tool(approval.step.tool_name)
    if tool is None:
        result = registry.error_result("도구가 더 이상 등록되어 있지 않습니다.")
    else:
        result = registry.run_external(tool, approval)  # 3)
    status = Step.Status.DONE if result["ok"] else Step.Status.FAILED
    _close_step(approval.step, status, result, started)
    _requeue(approval.step.task_id)


def expire_old():
    """만료 시각이 지난 pending 승인을 정리한다. 처리한 개수를 돌려준다."""
    count = 0
    expired_ids = Approval.objects.filter(
        status=Approval.Status.PENDING, expires_at__lte=timezone.now()
    ).values_list("id", flat=True)
    for approval_id in list(expired_ids):
        with transaction.atomic():
            approval = Approval.objects.select_for_update().select_related("step__task").get(id=approval_id)
            if approval.status == Approval.Status.PENDING:  # 잠그는 사이 결정됐을 수 있다
                _expire(approval)
                count += 1
    return count


# ── 내부 도우미 ─────────────────────────────────────────


def _lock(approval_id, user):
    """승인 행을 잠근다. 다른 사람의 승인이면 DoesNotExist(뷰에서는 404)."""
    return (
        Approval.objects.select_for_update()
        .select_related("step__task")
        .get(id=approval_id, step__task__user=user)
    )


def _expire(approval):
    approval.status = Approval.Status.EXPIRED
    approval.save(update_fields=["status", "updated_at"])
    _close_step(approval.step, Step.Status.FAILED, registry.error_result("승인 시간이 지났습니다."))
    task = approval.step.task
    Task.objects.filter(id=task.id).update(status=Task.Status.CANCELLED, error=EXPIRED_MESSAGE)
    Message.objects.create(
        conversation_id=task.conversation_id, role=Message.Role.ASSISTANT, content=EXPIRED_MESSAGE, task=task
    )


def _close_step(step, status, result, started=None):
    step.status = status
    step.output_json = result
    step.ok = result["ok"]
    if started is not None:
        step.duration_ms = int((time.monotonic() - started) * 1000)
    step.save(update_fields=["status", "output_json", "ok", "duration_ms", "updated_at"])


def _requeue(task_id):
    """승인 대기에서 풀린 Task를 다시 큐에 넣는다. 3단계부터는 워커가 이어서 실행한다."""
    Task.objects.filter(id=task_id, status=Task.Status.WAITING_APPROVAL).update(status=Task.Status.QUEUED)
