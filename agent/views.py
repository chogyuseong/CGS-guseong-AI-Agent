from django.contrib.auth.decorators import login_required

from config.pages import render_placeholder


@login_required
def task_list(request):
    # Task 목록과 단계 기록이 들어올 자리.
    return render_placeholder(request, active_tab="tasks", title="작업 기록", stage=1)
