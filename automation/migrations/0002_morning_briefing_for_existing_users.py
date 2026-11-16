"""이미 있는 계정에 아침 브리핑(매일 07:30) 예약 작업을 만든다. 새 계정은 signals.py가 만든다."""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import migrations
from django.utils import timezone

TITLE = "아침 브리핑"
PROMPT = "오늘 일정, 오늘 날씨, 미완료 할 일을 요약해라. 행동이 필요한 항목은 제안으로만 적어라."
AT = time(7, 30)


def add_briefing(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    ScheduledJob = apps.get_model("automation", "ScheduledJob")
    today = timezone.localdate()
    first = timezone.make_aware(datetime.combine(today, AT))
    if first <= timezone.now():
        first += timedelta(days=1)
    for user in User.objects.all():
        if not ScheduledJob.objects.filter(user=user, title=TITLE).exists():
            ScheduledJob.objects.create(
                user=user, title=TITLE, prompt_text=PROMPT, time_of_day=AT, weekdays=[], next_run_at=first
            )


class Migration(migrations.Migration):
    dependencies = [
        ("automation", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(add_briefing, migrations.RunPython.noop)]
