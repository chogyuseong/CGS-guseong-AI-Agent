from django.contrib.auth.decorators import login_required

from config.pages import render_placeholder


@login_required
def memory_list(request):
    # 기억 목록, 원래 메시지 보기, 삭제가 들어올 자리.
    return render_placeholder(request, active_tab="memory", title="기억", stage=5)
