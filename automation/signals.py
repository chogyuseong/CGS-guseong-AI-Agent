from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_morning_briefing(sender, instance, created, **kwargs):
    """계정이 생길 때 아침 브리핑(매일 07:30)을 하나 만든다. 이미 있는 계정은 마이그레이션이 채운다."""
    if created:
        from automation.services import ensure_briefing

        ensure_briefing(instance)
