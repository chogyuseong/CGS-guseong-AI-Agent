"""
웹 푸시 알림.

공개 함수는 notify(user, kind, url) 하나뿐이다. 제목과 본문은 kind별 고정 문구라서
호출하는 쪽이 메시지 내용, 일정 제목, 메일 내용 같은 개인 정보를 넘길 방법 자체가 없다.
푸시는 구글·애플의 푸시 서버를 거치므로, 개인 정보를 싣지 않는 것을 함수 모양으로 강제한다.

발송 실패는 작업을 실패시키지 않는다. 404/410(구독이 사라짐)이면 구독을 지운다.
"""

import json
import logging

from django.conf import settings
from django.utils import timezone
from pywebpush import WebPushException, webpush

from notifications.models import PushSubscription

logger = logging.getLogger(__name__)

MESSAGES = {
    "approval_needed": (
        "승인이 필요한 작업이 있습니다",
        "앱을 열어 내용을 확인하고 승인하거나 거절해 주세요.",
    ),
    "task_done": ("맡기신 작업이 끝났습니다", "앱을 열어 결과를 확인해 주세요."),
    "task_failed": ("작업을 끝내지 못했습니다", "앱을 열어 이유를 확인해 주세요."),
    "watch_met": ("지켜보던 조건이 충족되었습니다", "앱을 열어 내용을 확인해 주세요."),
    "briefing": ("오늘의 브리핑이 준비되었습니다", "앱을 열어 브리핑을 확인해 주세요."),
}
PUSH_TTL_SECONDS = 60 * 60  # 폰이 꺼져 있어도 1시간 안에 켜지면 받는다
PUSH_TIMEOUT_SECONDS = 10
GONE_STATUS_CODES = {404, 410}


def notify(user, kind, url):
    """사용자의 모든 기기에 고정 문구 알림을 보낸다. 보낸 기기 수를 돌려준다."""
    if kind not in MESSAGES:
        raise ValueError(f"알 수 없는 알림 종류입니다: {kind}")
    if not (settings.VAPID_PRIVATE_KEY and settings.VAPID_SUBJECT):
        return 0  # 키를 아직 만들지 않았으면 조용히 건너뛴다
    title, body = MESSAGES[kind]
    payload = json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False)
    return sum(_send(subscription, payload) for subscription in PushSubscription.objects.filter(user=user))


def _send(subscription, payload):
    subscription_info = {
        "endpoint": subscription.endpoint,
        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
    }
    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=PUSH_TTL_SECONDS,
            timeout=PUSH_TIMEOUT_SECONDS,
        )
    except WebPushException as error:
        status = getattr(error.response, "status_code", None)
        if status in GONE_STATUS_CODES:
            subscription.delete()  # 사용자가 알림을 끄거나 앱을 지운 기기
        else:
            logger.warning("푸시 발송 실패(구독 %s, 상태 %s)", subscription.id, status)
        return False
    except Exception:
        # 로그에는 구독 id만 남긴다. 알림 내용과 키는 남기지 않는다.
        logger.exception("푸시 발송 중 예외(구독 %s)", subscription.id)
        return False
    subscription.last_used_at = timezone.now()
    subscription.save(update_fields=["last_used_at", "updated_at"])
    return True
