from django.conf import settings
from django.db import models

from config.base_models import TimeStampedModel


class PushSubscription(TimeStampedModel):
    """브라우저 하나의 웹 푸시 구독 정보. 기기마다 하나씩 생긴다."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions"
    )
    # 같은 브라우저가 두 번 구독해도 한 줄만 남도록 endpoint를 고유값으로 둔다.
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} - {self.endpoint[:40]}"
