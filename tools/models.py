"""내부 도구(save_note, add_todo 등)가 다루는 데이터. 앱 안에서만 바뀌므로 INTERNAL 등급이다."""

from django.conf import settings
from django.db import models

from config.base_models import TimeStampedModel


class Note(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notes")
    content = models.TextField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.content[:30]


class Todo(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="todos")
    title = models.CharField(max_length=200)
    is_done = models.BooleanField(default=False)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["is_done", "-created_at"]

    def __str__(self):
        return self.title
