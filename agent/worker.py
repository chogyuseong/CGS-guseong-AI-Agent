"""
백그라운드 워커의 한 바퀴. 관리 명령(run_worker)은 이 함수들을 정해진 간격으로 부르기만 한다.

  run_once         queued Task 하나를 가장 오래된 것부터 잡아 실행한다(2초마다).
  run_maintenance  만료된 승인 정리, 멈춘 Task 회수(60초마다). 6단계에서 예약 작업·조건 감시가 붙는다.

앱을 닫아도 작업이 이어지는 이유: 작업 상태가 전부 DB에 있고, 웹 서버가 아니라 이 워커가 실행하기 때문이다.
"""

import logging
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from agent import approvals, registry
from agent.loop import fail_task, run_task
from agent.models import Step, Task

logger = logging.getLogger(__name__)

STUCK_AFTER = timedelta(minutes=5)
STUCK_TASK_MESSAGE = "작업이 5분 넘게 진행되지 않아 중단했습니다. 다시 요청해 주세요."
STUCK_EXTERNAL_MESSAGE = (
    "외부 실행 도중 응답이 끊겼습니다. "
    "실제로 반영됐는지 해당 서비스에서 확인한 뒤 필요하면 다시 요청해 주세요."
)
CRASH_MESSAGE = "작업 중 예상하지 못한 오류가 났습니다."


def run_once():
    """queued Task 하나를 실행한다. 실행한 Task id, 없으면 None."""
    task_id = claim_next_task()
    if task_id is None:
        return None
    try:
        run_task(task_id, claimed=True)
    except Exception:
        # 버그 하나로 워커 전체가 죽지 않게 한다. 해당 Task만 실패로 닫는다.
        logger.exception("Task %s 실행 중 예외", task_id)
        fail_task(task_id, CRASH_MESSAGE)
    return task_id


def claim_next_task():
    """가장 오래된 queued Task를 running으로 바꾸고 id를 돌려준다.

    PostgreSQL은 select_for_update(skip_locked=True)로 다른 워커가 잡고 있는 행을 건너뛴다.
    SQLite는 이 기능이 없어 워커를 하나만 띄운다. 어느 쪽이든 마지막 조건부 UPDATE가
    "한 Task는 한 워커만"을 보장한다.
    """
    with transaction.atomic():
        queued = Task.objects.filter(status=Task.Status.QUEUED).order_by("created_at", "id")
        if connection.features.has_select_for_update_skip_locked:
            queued = queued.select_for_update(skip_locked=True)
        task = queued.only("id").first()
        if task is None:
            return None
        claimed = Task.objects.filter(id=task.id, status=Task.Status.QUEUED).update(
            status=Task.Status.RUNNING, locked_at=timezone.now()
        )
    return task.id if claimed else None


def run_maintenance():
    """주기적인 정리 작업. 처리한 개수를 돌려준다(로그용)."""
    expired = approvals.expire_old()
    reclaimed = reclaim_stuck_tasks()
    return {"expired_approvals": expired, "reclaimed_tasks": reclaimed}


def reclaim_stuck_tasks():
    """5분 넘게 진행이 없는 Task를 실패로 닫는다. 닫은 개수를 돌려준다."""
    cutoff = timezone.now() - STUCK_AFTER
    count = 0
    for task_id in Task.objects.filter(status=Task.Status.RUNNING, locked_at__lt=cutoff).values_list(
        "id", flat=True
    ):
        count += fail_task(task_id, STUCK_TASK_MESSAGE)
    count += _reclaim_stuck_external_steps(cutoff)
    return count


def _reclaim_stuck_external_steps(cutoff):
    """승인 후 외부 호출 도중 서버가 죽어 running으로 남은 Step을 정리한다."""
    count = 0
    stuck = Step.objects.filter(
        status=Step.Status.RUNNING, updated_at__lt=cutoff, task__status=Task.Status.WAITING_APPROVAL
    )
    for step in stuck:
        claimed = Step.objects.filter(id=step.id, status=Step.Status.RUNNING).update(
            status=Step.Status.FAILED, ok=False, output_json=registry.error_result(STUCK_EXTERNAL_MESSAGE)
        )
        if claimed:
            count += fail_task(step.task_id, STUCK_EXTERNAL_MESSAGE, from_status=Task.Status.WAITING_APPROVAL)
    return count
