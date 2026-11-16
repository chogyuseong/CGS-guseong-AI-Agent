from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from agent.loop import run_task
from agent.models import Approval, Task

from .models import Conversation, Message

MESSAGES_PER_MINUTE = 20  # 실수나 자동 클릭으로 비용이 폭주하지 않게 막는다
MAX_MESSAGE_LENGTH = 2000


@login_required
def home(request):
    """가장 최근 대화로 보낸다. 대화가 하나도 없으면 첫 대화를 만든다."""
    conversation = Conversation.objects.filter(user=request.user).first()
    if conversation is None:
        conversation = Conversation.objects.create(user=request.user, title="새 대화")
    return redirect("chat:conversation", pk=conversation.pk)


@login_required
def conversation_detail(request, pk):
    # 다른 사람의 대화 id를 넣어도 404가 나도록 항상 user 조건을 붙인다.
    conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
    messages_list = conversation.messages.select_related("task").prefetch_related("task__steps")
    pending_approvals = (
        Approval.objects.filter(
            step__task__conversation=conversation,
            step__task__user=request.user,
            status=Approval.Status.PENDING,
        )
        .select_related("step__task", "step__guard_check")
        .order_by("created_at")
    )
    context = {
        "active_tab": "chat",
        "conversation": conversation,
        "messages_list": messages_list,
        "pending_approvals": pending_approvals,
    }
    return render(request, "chat/conversation.html", context)


@login_required
@require_POST
def send_message(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
    content = request.POST.get("content", "").strip()[:MAX_MESSAGE_LENGTH]
    if not content:
        return redirect("chat:conversation", pk=pk)
    if _sent_too_many(request.user):
        return HttpResponse("메시지를 너무 빠르게 보내고 있습니다. 1분 뒤 다시 시도해 주세요.", status=429)
    task = _create_task(conversation, content)
    # 1단계에서는 요청 안에서 바로 실행한다. 3단계에서 Task를 큐에 넣고 워커가 실행하도록 바뀐다.
    run_task(task.id)
    return redirect("chat:conversation", pk=pk)


def _sent_too_many(user):
    one_minute_ago = timezone.now() - timedelta(minutes=1)
    recent = Message.objects.filter(
        conversation__user=user, role=Message.Role.USER, created_at__gte=one_minute_ago
    ).count()
    return recent >= MESSAGES_PER_MINUTE


@transaction.atomic
def _create_task(conversation, content):
    """사용자 메시지와 Task를 함께 만든다. 둘 중 하나만 생기는 일이 없게 한 트랜잭션으로 묶는다."""
    message = Message.objects.create(conversation=conversation, role=Message.Role.USER, content=content)
    task = Task.objects.create(conversation=conversation, user=conversation.user, trigger_message=message)
    message.task = task
    message.save(update_fields=["task", "updated_at"])
    return task
