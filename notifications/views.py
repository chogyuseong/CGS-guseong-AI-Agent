import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import PushSubscription

HTTPS_URL = URLValidator(schemes=["https"])  # 브라우저 푸시 서비스 주소는 항상 https다
SERVICE_WORKER_PATH = settings.BASE_DIR / "static" / "sw.js"


@login_required
@require_POST
def subscribe(request):
    """브라우저가 만든 구독 정보를 저장한다. 같은 브라우저가 다시 구독하면 한 줄을 갱신한다."""
    data = _read_json(request)
    endpoint = str(data.get("endpoint", ""))
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    p256dh, auth = str(keys.get("p256dh", "")), str(keys.get("auth", ""))
    if not _valid_subscription(endpoint, p256dh, auth):
        return JsonResponse({"ok": False, "error": "구독 정보가 올바르지 않습니다."}, status=400)
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": request.headers.get("User-Agent", "")[:300],
        },
    )
    return JsonResponse({"ok": True}, status=201)


@login_required
@require_POST
def unsubscribe(request):
    endpoint = str(_read_json(request).get("endpoint", ""))
    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return HttpResponse(status=204)


@require_GET
def service_worker(request):
    """서비스 워커는 사이트 전체(/)를 맡아야 해서 /static/이 아니라 루트 주소로 내보낸다."""
    response = HttpResponse(SERVICE_WORKER_PATH.read_bytes(), content_type="application/javascript")
    response["Cache-Control"] = "no-cache"  # 수정한 sw.js가 바로 반영되게
    return response


def _read_json(request):
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _valid_subscription(endpoint, p256dh, auth):
    if len(endpoint) > 1000 or not (0 < len(p256dh) <= 200 and 0 < len(auth) <= 100):
        return False
    try:
        HTTPS_URL(endpoint)
    except ValidationError:
        return False
    return True
