from django.db import models


class TimeStampedModel(models.Model):
    """모든 모델이 상속한다. 언제 만들어지고 바뀌었는지는 디버깅과 감사 기록의 기본이다."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
