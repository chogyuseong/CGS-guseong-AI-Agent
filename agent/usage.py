"""
사용량 화면(/usage/)의 집계.

새 테이블을 만들지 않고 비용 상한이 쓰는 UsageLog를 그대로 모은다.
UsageLog에는 user가 없어서(DECISIONS 0단계) `task__user`로 본인 것만 거른다.
그래서 Task 없이 기록되는 조건 감시 판단 비용(purpose=watch, task=None)은 누구의 것인지 알 수 없어 빠진다.
하루 비용 상한은 앱 전체 합계(budget.today_cost)로 검사하므로, 상한 도달 여부는 따로 알려 준다.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from agent import budget
from agent.models import UsageLog

USAGE_DAYS = 14

_TOKENS = F("input_tokens") + F("output_tokens") + F("cache_write_tokens") + F("cache_read_tokens")


def _start_of(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def usage_summary(user, days=USAGE_DAYS):
    """본인 Task에 묶인 호출만 모아 오늘, 최근 며칠의 일별, 모델별 합계를 돌려준다."""
    today = timezone.localdate()
    first_day = today - timedelta(days=days - 1)
    logs = UsageLog.objects.filter(task__user=user, created_at__gte=_start_of(first_day))

    by_day = {
        row["day"]: row
        for row in logs.annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(calls=Count("id"), tokens=Sum(_TOKENS), cost=Sum("cost_usd"))
    }
    daily = []
    for offset in range(days):
        day = today - timedelta(days=offset)  # 최근 날짜가 위로 온다
        row = by_day.get(day) or {}
        daily.append(
            {
                "day": day,
                "calls": row.get("calls", 0),
                "tokens": row.get("tokens") or 0,
                "cost": row.get("cost") or Decimal("0"),
            }
        )
    max_cost = max((d["cost"] for d in daily), default=Decimal("0"))
    for d in daily:
        d["bar_percent"] = _percent(d["cost"], max_cost) if max_cost > 0 else 0

    by_model = list(
        logs.values("model")
        .annotate(calls=Count("id"), tokens=Sum(_TOKENS), cost=Sum("cost_usd"))
        .order_by("-cost", "model")
    )

    today_row = daily[0]
    limit = settings.DAILY_COST_LIMIT_USD
    return {
        "days": days,
        "today": today_row,
        "limit": limit,
        "today_percent": _percent(today_row["cost"], limit) if limit > 0 else 0,
        # 상한은 앱 전체 합계로 검사한다. 금액은 보여 주지 않고 '멈췄는가'만 알린다.
        "limit_reached": budget.today_cost() >= limit,
        "daily": daily,
        "by_model": by_model,
        "has_usage": any(d["calls"] for d in daily),
        "period_cost": sum((d["cost"] for d in daily), Decimal("0")),
        "period_calls": sum(d["calls"] for d in daily),
        "period_tokens": sum(d["tokens"] for d in daily),
    }


def _percent(value, whole):
    """0~100 사이 비율. 막대 너비와 상한 대비 사용률에 쓴다(보여 줄 때 반올림은 템플릿 필터가 한다)."""
    return float(min(Decimal("100"), value / whole * 100).quantize(Decimal("0.0001")))
