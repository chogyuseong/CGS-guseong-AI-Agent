from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .models import Conversation


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
    context = {
        "active_tab": "chat",
        "conversation": conversation,
        "messages_list": conversation.messages.all(),
    }
    return render(request, "chat/conversation.html", context)
