from django.conf import settings
from django.db import models

from config.base_models import TimeStampedModel


class Conversation(TimeStampedModel):
    """대화방. 사용자 한 명이 여러 개를 가진다."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or f"대화 {self.pk}"


class Message(TimeStampedModel):
    """사용자와 에이전트가 주고받은 말 한 개."""

    class Role(models.TextChoices):
        USER = "user", "사용자"
        ASSISTANT = "assistant", "에이전트"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    # 어떤 Task에서 나온 메시지인지. Task가 지워져도 대화 기록은 남긴다.
    task = models.ForeignKey(
        "agent.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="messages"
    )

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.get_role_display()}: {self.content[:30]}"
