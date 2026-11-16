from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import Memory


@login_required
@require_GET
def memory_list(request):
    """기억 목록. 각 기억의 원래 메시지를 함께 보여 줘, 무엇을 근거로 기억했는지 사용자가 확인할 수 있다."""
    memories = (
        Memory.objects.filter(user=request.user)
        .select_related("source_message__conversation")
        .order_by("-created_at", "-id")
    )
    return render(request, "memory/list.html", {"active_tab": "memory", "memories": memories})


@login_required
@require_POST
def memory_delete(request, pk):
    # is_active=False로 숨기지 않고 행을 지운다. 지운 기억은 다음 대화의 프롬프트에서도 빠진다.
    get_object_or_404(Memory, pk=pk, user=request.user).delete()
    messages.success(request, "기억을 지웠습니다.")
    return redirect("memory:list")


@login_required
@require_POST
def memory_delete_all(request):
    count, _ = Memory.objects.filter(user=request.user).delete()
    messages.success(request, f"기억 {count}개를 모두 지웠습니다." if count else "지울 기억이 없습니다.")
    return redirect("memory:list")
