from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from config.pages import render_placeholder

from . import approvals
from .loop import run_task
from .models import Approval


@login_required
def task_list(request):
    # Task 목록과 단계 기록이 들어올 자리.
    return render_placeholder(request, active_tab="tasks", title="작업 기록", stage=3)


@login_required
@require_POST
def approve_approval(request, pk):
    approval = _decide(approvals.approve, pk, request.user)
    # 이번 요청이 실제로 실행했을 때만 이어서 돌린다. 연속 클릭한 두 번째 요청은 여기서 아무것도 하지 않는다.
    if approvals.resume_after_approval(approval.id):
        run_task(approval.step.task_id)  # 3단계에서 워커가 이어 받도록 바뀐다
    return redirect("chat:conversation", pk=approval.step.task.conversation_id)


@login_required
@require_POST
def reject_approval(request, pk):
    approval = _decide(approvals.reject, pk, request.user)
    if approval.status == Approval.Status.REJECTED:
        run_task(approval.step.task_id)  # 거절 사실을 LLM에 알려 다른 방법을 찾게 한다
    return redirect("chat:conversation", pk=approval.step.task.conversation_id)


def _decide(action, pk, user):
    try:
        return action(pk, user)
    except Approval.DoesNotExist as error:
        raise Http404 from error  # 다른 사람의 승인 요청은 있는지조차 알려 주지 않는다
