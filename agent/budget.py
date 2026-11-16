"""
단계 수, 토큰, 하루 비용 상한.

피클 사례에서 예매 한 건이 하루 한도의 8%를 쓴 일이 있었다.
그래서 LLM을 부르기 전에 매번 세 가지 상한을 검사한다.
"""

from datetime import datetime, time
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from agent.models import UsageLog

MILLION = Decimal(1_000_000)
COST_PLACES = Decimal("0.000001")  # UsageLog.cost_usd의 소수 6자리에 맞춘다


def cost_for(model, input_tokens, output_tokens):
    """토큰 수를 달러로 바꾼다. 가격을 모르면 0으로 계산한다(실제 호출은 llm.py가 막는다)."""
    price = settings.MODEL_PRICES.get(model) or {}
    if price.get("input") is None or price.get("output") is None:
        return Decimal("0")
    total = Decimal(input_tokens) * Decimal(str(price["input"])) + Decimal(output_tokens) * Decimal(
        str(price["output"])
    )
    return (total / MILLION).quantize(COST_PLACES)


def today_cost():
    """서울 기준 오늘 0시부터 쓴 비용 합계. 에이전트, 감시 에이전트, 조건 감시를 모두 포함한다."""
    midnight = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    total = UsageLog.objects.filter(created_at__gte=midnight).aggregate(total=Sum("cost_usd"))["total"]
    return total or Decimal("0")


def exceeded_reason(task):
    """상한을 넘었으면 사용자에게 보여 줄 이유를, 아니면 None을 돌려준다."""
    if task.step_count >= settings.TASK_MAX_STEPS:
        return f"한 작업의 최대 단계 수({settings.TASK_MAX_STEPS})에 도달했습니다."
    if task.total_tokens >= settings.TASK_MAX_TOKENS:
        return f"한 작업의 최대 토큰 수({settings.TASK_MAX_TOKENS:,})에 도달했습니다."
    if today_cost() >= settings.DAILY_COST_LIMIT_USD:
        return f"오늘의 비용 상한(${settings.DAILY_COST_LIMIT_USD})에 도달했습니다."
    return None
