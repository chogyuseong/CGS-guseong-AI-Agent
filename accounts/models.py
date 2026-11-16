from django.conf import settings
from django.db import models

from config.base_models import TimeStampedModel


class Connection(TimeStampedModel):
    """외부 서비스 OAuth 연결. 토큰은 Fernet으로 암호화한 문자열만 저장한다(4단계)."""

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="connections")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    encrypted_token = models.TextField()
    # 실제로 허락받은 범위. 메일 읽기 권한이 있는지 여기서 확인한다.
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "provider"], name="unique_connection_per_provider")
        ]

    def __str__(self):
        return f"{self.user} - {self.provider}"
