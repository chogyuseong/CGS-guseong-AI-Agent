from django.contrib.auth.decorators import login_required

from config.pages import render_placeholder


@login_required
def schedule_list(request):
    # 예약 작업과 조건 감시 목록이 들어올 자리.
    return render_placeholder(request, active_tab="automation", title="자동화", stage=6)
