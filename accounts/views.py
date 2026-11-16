from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def settings_page(request):
    """알림 켜기(3단계). 브리핑 시각 설정은 6단계에서 붙는다."""
    context = {
        "active_tab": "settings",
        # 공개키는 브라우저가 구독을 만들 때 쓰는 값이라 화면에 넣어도 된다. 개인키는 절대 넣지 않는다.
        "vapid_public_key": settings.VAPID_PUBLIC_KEY,
    }
    return render(request, "accounts/settings.html", context)
