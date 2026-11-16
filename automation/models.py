"""
스스로 도는 작업. 절대 원칙 4에 따라 둘 다 READ 등급 도구만 쓸 수 있다(6단계에서 서버로 강제).
"""

from datetime import timedelta

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from config.base_models import TimeStampedModel

WATCH_MIN_INTERVAL_MINUTES = 30
WATCH_DEFAULT_LIFETIME_DAYS = 30


class ScheduledJob(TimeStampedModel):
    """정한 시각에 조회 전용 Task를 실행하는 예약 작업. 아침 브리핑도 이것 하나다."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scheduled_jobs"
    )
    title = models.CharField(max_length=100)
    prompt_text = models.TextField()
    time_of_day = models.TimeField()
    # 실행할 요일 목록. 0=월요일 ... 6=일요일 (파이썬 weekday()와 같다)
    weekdays = models.JSONField(default=list)
    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["time_of_day"]

    def __str__(self):
        return self.title


def default_watch_expiry():
    return timezone.now() + timedelta(days=WATCH_DEFAULT_LIFETIME_DAYS)


class Watch(TimeStampedModel):
    """조회 도구를 주기적으로 돌려 조건이 맞으면 알려 주는 조건 감시."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watches")
    description = models.CharField(max_length=200)
    tool_name = models.CharField(max_length=100)
    tool_input_json = models.JSONField(default=dict)
    condition_text = models.TextField()
    interval_minutes = models.PositiveIntegerField(
        default=WATCH_MIN_INTERVAL_MINUTES, validators=[MinValueValidator(WATCH_MIN_INTERVAL_MINUTES)]
    )
    next_run_at = models.DateTimeField()
    last_result_json = models.JSONField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    repeat = models.BooleanField(default=False)
    expires_at = models.DateTimeField(default=default_watch_expiry)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        # 폼 검증을 거치지 않는 경로(도구, 관리자)에서도 30분 미만을 막기 위해 DB 제약으로도 건다.
        constraints = [
            models.CheckConstraint(
                condition=models.Q(interval_minutes__gte=WATCH_MIN_INTERVAL_MINUTES),
                name="watch_interval_at_least_30_minutes",
            )
        ]

    def __str__(self):
        return self.description
