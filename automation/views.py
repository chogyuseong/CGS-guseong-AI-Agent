from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import ScheduledJob, Watch
from .services import MAX_ACTIVE, WEEKDAYS, describe_repeat, next_run_after


def _counts(user):
    return {
        "schedule_count": ScheduledJob.objects.filter(user=user).count(),
        "watch_count": Watch.objects.filter(user=user).count(),
    }


@login_required
@require_GET
def schedule_list(request):
    jobs = list(ScheduledJob.objects.filter(user=request.user).order_by("-is_active", "time_of_day"))
    for job in jobs:
        job.repeat_text = describe_repeat(job.time_of_day, job.weekdays)
        # 시각표 왼쪽, 큰 시각 아래에 붙는 짧은 요일 표시: '매일' 또는 '일' '월, 수'
        job.days_text = ", ".join(WEEKDAYS[d] for d in sorted(job.weekdays)) if job.weekdays else "매일"
    context = {"active_tab": "automation", "section": "schedules", "jobs": jobs, "limit": MAX_ACTIVE}
    return render(request, "automation/schedules.html", {**context, **_counts(request.user)})


@login_required
@require_GET
def watch_list(request):
    watches = Watch.objects.filter(user=request.user).order_by("-is_active", "-created_at")
    context = {"active_tab": "automation", "section": "watches", "watches": watches, "limit": MAX_ACTIVE}
    return render(request, "automation/watches.html", {**context, **_counts(request.user)})


@login_required
@require_POST
def schedule_toggle(request, pk):
    job = get_object_or_404(ScheduledJob, pk=pk, user=request.user)
    job.is_active = not job.is_active
    if job.is_active:  # 다시 켜면 지난 회차를 몰아서 돌리지 않고 다음 시각부터 시작한다
        job.next_run_at = next_run_after(timezone.now(), job.time_of_day, job.weekdays)
    job.save(update_fields=["is_active", "next_run_at", "updated_at"])
    return redirect("automation:schedule_list")


@login_required
@require_POST
def schedule_delete(request, pk):
    get_object_or_404(ScheduledJob, pk=pk, user=request.user).delete()
    messages.success(request, "예약 작업을 지웠습니다.")
    return redirect("automation:schedule_list")


@login_required
@require_POST
def watch_toggle(request, pk):
    watch = get_object_or_404(Watch, pk=pk, user=request.user)
    watch.is_active = not watch.is_active
    if watch.is_active:
        now = timezone.now()
        watch.next_run_at = now
        if watch.expires_at <= now:  # 만료된 감시를 다시 켜면 기본 기간(30일)을 새로 준다
            watch.expires_at = now + timedelta(days=30)
    watch.save(update_fields=["is_active", "next_run_at", "expires_at", "updated_at"])
    return redirect("automation:watch_list")


@login_required
@require_POST
def watch_delete(request, pk):
    get_object_or_404(Watch, pk=pk, user=request.user).delete()
    messages.success(request, "조건 감시를 지웠습니다.")
    return redirect("automation:watch_list")
