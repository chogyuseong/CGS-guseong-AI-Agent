from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from . import approvals, usage
from .models import Approval, Task

ACTIVE_STATUSES = (Task.Status.QUEUED, Task.Status.RUNNING)
TASK_LIST_LIMIT = 50


@login_required
def task_list(request):
    tasks = (
        Task.objects.filter(user=request.user)
        .select_related("trigger_message", "conversation")
        .order_by("-created_at")[:TASK_LIST_LIMIT]
    )
    return render(request, "agent/task_list.html", {"active_tab": "tasks", "tasks": tasks})


@login_required
@require_GET
def usage_page(request):
    """본인 작업의 LLM 호출 수, 토큰, 비용. 설정 화면에서 들어온다(하단 탭은 설정이 켜진 채로)."""
    context = {"active_tab": "settings", **usage.usage_summary(request.user)}
    return render(request, "agent/usage.html", context)


@login_required
@require_GET
def task_steps(request, pk):
    """진행 중인 Task의 단계 타임라인 조각. 채팅 화면이 1초마다 불러 간다(HTMX 폴링).

    작업이 끝났거나 승인 대기로 바뀌면 화면 전체를 새로 그리게 한다. 최종 답변과 승인 카드는
    채팅 화면 쪽에서 그리므로, 조각을 따로 만들지 않고 한 번 새로고침하는 편이 단순하다.
    SSE로 바꿀 때는 이 뷰만 바꾸면 된다.
    """
    task = get_object_or_404(Task, pk=pk, user=request.user)
    if task.status not in ACTIVE_STATUSES:
        response = HttpResponse("")
        response["HX-Refresh"] = "true"
        return response
    return render(request, "agent/_timeline.html", {"task": task})


@login_required
@require_POST
def cancel_task(request, pk):
    task = get_object_or_404(Task, pk=pk, user=request.user)
    approvals.cancel_task(task.id, request.user)
    return redirect("chat:conversation", pk=task.conversation_id)


@login_required
@require_POST
def approve_approval(request, pk):
    approval = _decide(approvals.approve, pk, request.user)
    # 이번 요청이 승인을 실제로 처리했을 때만 실행한다. 연속 클릭한 두 번째 요청은 아무것도 하지 않는다.
    # 실행 뒤 Task는 queued로 돌아가고, 이어지는 LLM 호출은 워커가 한다(요청 안에서 기다리지 않는다).
    approvals.resume_after_approval(approval.id)
    return redirect("chat:conversation", pk=approval.step.task.conversation_id)


@login_required
@require_POST
def reject_approval(request, pk):
    approval = _decide(approvals.reject, pk, request.user)  # Task가 queued로 돌아가고 워커가 이어 간다
    return redirect("chat:conversation", pk=approval.step.task.conversation_id)


def _decide(action, pk, user):
    try:
        return action(pk, user)
    except Approval.DoesNotExist as error:
        raise Http404 from error  # 다른 사람의 승인 요청은 있는지조차 알려 주지 않는다
