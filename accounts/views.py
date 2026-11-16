from datetime import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from automation import services as automation
from tools.http import ExternalAPIError

from . import google
from .models import Connection


@login_required
def settings_page(request):
    """알림 켜기, 아침 브리핑 시각, 외부 서비스 연결."""
    context = {
        "active_tab": "settings",
        # 공개키는 브라우저가 구독을 만들 때 쓰는 값이라 화면에 넣어도 된다. 개인키는 절대 넣지 않는다.
        "vapid_public_key": settings.VAPID_PUBLIC_KEY,
        "briefing": automation.ensure_briefing(request.user),
    }
    return render(request, "accounts/settings.html", context)


@login_required
@require_POST
def briefing_update(request):
    """아침 브리핑의 시각과 켜기/끄기를 바꾼다. 다음 실행 시각도 새 시각에 맞춰 다시 계산한다."""
    briefing = automation.ensure_briefing(request.user)
    try:
        briefing.time_of_day = time.fromisoformat(request.POST.get("time_of_day", ""))
    except ValueError:
        messages.error(request, "시각을 HH:MM 형식으로 입력해 주세요.")
        return redirect("settings_page:settings")
    briefing.is_active = request.POST.get("is_active") == "on"
    briefing.next_run_at = automation.next_run_after(timezone.now(), briefing.time_of_day, briefing.weekdays)
    briefing.save(update_fields=["time_of_day", "is_active", "next_run_at", "updated_at"])
    state = (
        f"매일 {briefing.time_of_day:%H:%M}에 받습니다" if briefing.is_active else "아침 브리핑을 껐습니다"
    )
    messages.success(request, f"저장했습니다. {state}.")
    return redirect("settings_page:settings")


@login_required
@require_GET
def connections_page(request):
    connection = Connection.objects.filter(user=request.user, provider=Connection.Provider.GOOGLE).first()
    context = {
        "active_tab": "settings",
        "google_configured": google.is_configured(),
        "google_connection": connection,
        "has_calendar": bool(connection and google.SCOPE_CALENDAR in connection.scopes),
        "has_mail": bool(connection and google.SCOPE_GMAIL_READ in connection.scopes),
    }
    return render(request, "accounts/connections.html", context)


@login_required
@require_GET
def google_start(request):
    if not google.is_configured():
        messages.error(request, "Google 연결 설정(.env의 GOOGLE_CLIENT_ID 등)이 없습니다.")
        return redirect("settings_page:connections")
    scopes = [google.SCOPE_CALENDAR]
    if request.GET.get("mail") == "1":  # '메일 읽기 권한 추가' 버튼. 이미 받은 캘린더 권한과 함께 묻는다
        scopes.append(google.SCOPE_GMAIL_READ)
    return redirect(google.start(request.session, scopes))


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
        _report_granted(request, saved["scopes"], tokens.get("scopes") or [])
    return redirect("settings_page:connections")


def _report_granted(request, asked, granted):
    """구글 동의 화면에서는 항목별로 체크를 뺄 수 있다. 요청한 권한을 실제로 받았는지 알려 준다."""
    if google.SCOPE_GMAIL_READ in asked and google.SCOPE_GMAIL_READ not in granted:
        messages.warning(
            request,
            "메일 읽기 권한을 받지 못했습니다. 동의 화면에서 Gmail 항목을 체크하고 다시 시도해 주세요.",
        )
    elif google.SCOPE_GMAIL_READ in asked:
        messages.success(
            request, "메일 읽기 권한을 추가했습니다. 채팅에서 '안 읽은 메일 요약해줘'처럼 말해 보세요."
        )
    else:
        messages.success(request, "Google 캘린더를 연결했습니다.")


@login_required
@require_POST
def google_disconnect(request):
    google.disconnect(request.user)
    messages.success(request, "Google 연결을 해제했습니다.")
    return redirect("settings_page:connections")
