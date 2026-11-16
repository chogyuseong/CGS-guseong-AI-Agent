from django.conf import settings
from django.db import models

from config.base_models import TimeStampedModel


class Memory(TimeStampedModel):
    """사용자가 직접 말한 기억 한 줄. 출처 메시지를 반드시 함께 저장해 근거를 추적한다."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memories")
    content = models.CharField(max_length=300)
    # 출처 메시지가 지워지면 근거가 사라지므로 기억도 함께 지운다.
    source_message = models.ForeignKey("chat.Message", on_delete=models.CASCADE, related_name="memories")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "memories"

    def __str__(self):
        return self.content
