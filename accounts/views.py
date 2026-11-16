from django.contrib.auth.decorators import login_required

from config.pages import render_placeholder


@login_required
def settings_page(request):
    # 알림 허용(3단계)과 브리핑 시각(6단계)이 들어올 자리.
    return render_placeholder(request, active_tab="settings", title="설정", stage=3)
