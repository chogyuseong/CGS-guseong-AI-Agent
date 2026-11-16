from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from tools.http import ExternalAPIError

from . import google
from .models import Connection


@login_required
def settings_page(request):
    """알림 켜기(3단계)와 외부 서비스 연결 링크. 브리핑 시각 설정은 6단계에서 붙는다."""
    context = {
        "active_tab": "settings",
        # 공개키는 브라우저가 구독을 만들 때 쓰는 값이라 화면에 넣어도 된다. 개인키는 절대 넣지 않는다.
        "vapid_public_key": settings.VAPID_PUBLIC_KEY,
    }
    return render(request, "accounts/settings.html", context)


@login_required
@require_GET
def connections_page(request):
    connection = Connection.objects.filter(user=request.user, provider=Connection.Provider.GOOGLE).first()
    context = {
        "active_tab": "settings",
        "google_configured": google.is_configured(),
        "google_connection": connection,
        "has_calendar": bool(connection and google.SCOPE_CALENDAR in connection.scopes),
    }
    return render(request, "accounts/connections.html", context)


@login_required
@require_GET
def google_start(request):
    if not google.is_configured():
        messages.error(request, "Google 연결 설정(.env의 GOOGLE_CLIENT_ID 등)이 없습니다.")
        return redirect("settings_page:connections")
    return redirect(google.start(request.session, [google.SCOPE_CALENDAR]))


@login_required
@require_GET
def google_callback(request):
    saved = request.session.pop("google_oauth", None)
    # 우리가 보낸 state와 다르면 다른 사이트가 꾸민 요청일 수 있다(CSRF). 아무것도 저장하지 않는다.
    if not saved or request.GET.get("state") != saved["state"]:
        return HttpResponseBadRequest("연결 요청을 확인하지 못했습니다. 연결 화면에서 다시 시도해 주세요.")
    if "error" in request.GET or "code" not in request.GET:
        messages.warning(request, "Google 연결을 취소했습니다.")
        return redirect("settings_page:connections")
    try:
        tokens = google.exchange_code(
            request.GET["code"],
            state=saved["state"],
            code_verifier=saved["code_verifier"],
            scopes=saved["scopes"],
        )
        google.save_tokens(request.user, tokens)
    except ExternalAPIError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Google 캘린더를 연결했습니다.")
    return redirect("settings_page:connections")


@login_required
@require_POST
def google_disconnect(request):
    google.disconnect(request.user)
    messages.success(request, "Google 연결을 해제했습니다.")
    return redirect("settings_page:connections")
