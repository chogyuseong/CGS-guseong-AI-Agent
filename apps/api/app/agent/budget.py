"""사용량 상한과 비용.

세 가지 상한이 있다. 어느 것이든 넘으면 task는 budget_exceeded로 끝난다.
1. 요청 수 상한(TASK_MAX_REQUESTS): Pydantic AI UsageLimits(request_limit). 넘기 직전 요청은 보내지 않는다.
2. 토큰 상한(TASK_MAX_TOKENS): UsageLimits(total_tokens_limit). 응답을 받은 뒤 합계가 넘으면 멈춘다.
3. 하루 비용 상한(DAILY_COST_LIMIT_USD): run을 시작하기 전에 오늘(Asia/Seoul) usage_logs 비용 합계로 직접 검사한다.
"""

import logging
from datetime import datetime, time
from decimal import Decimal

from pydantic_ai.usage import UsageLimits
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import SEOUL
from app.config import MODEL_PRICES, ModelPrice, Settings
from app.models import UsageLog

logger = logging.getLogger(__name__)

ONE_MILLION = Decimal(1_000_000)
COST_PLACES = Decimal("0.000001")  # usage_logs.cost_usd는 NUMERIC(10,6)


def build_usage_limits(settings: Settings) -> UsageLimits:
    return UsageLimits(request_limit=settings.task_max_requests, total_tokens_limit=settings.task_max_tokens)


def price_rates(price: ModelPrice, input_tokens: int) -> tuple[Decimal, Decimal]:
    """요청 하나의 (입력, 출력) 단가. 프롬프트가 길면 비싼 구간 가격을 쓴다."""
    threshold = price.long_prompt_threshold
    if threshold is not None and input_tokens > threshold:
        return price.long_input_per_mtok or price.input_per_mtok, price.long_output_per_mtok or price.output_per_mtok
    return price.input_per_mtok, price.output_per_mtok


def calculate_cost(model_name: str, input_tokens: int, output_tokens: int) -> Decimal:
    """모델 호출 한 번의 비용(달러). 가격표에 없는 모델은 0으로 계산하고 경고를 남긴다."""
    price = MODEL_PRICES.get(model_name.removeprefix("anthropic:"))
    if price is None:
        logger.warning("가격표(MODEL_PRICES)에 없는 모델이라 비용을 0으로 기록합니다: %s", model_name)
        return Decimal("0")
    input_rate, output_rate = price_rates(price, input_tokens)
    cost = (Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate) / ONE_MILLION
    return cost.quantize(COST_PLACES)


def seoul_day_start(now: datetime | None = None) -> datetime:
    """Asia/Seoul 기준 오늘 0시(시간대 포함)."""
    today = (now or datetime.now(SEOUL)).astimezone(SEOUL).date()
    return datetime.combine(today, time.min, tzinfo=SEOUL)


async def cost_today(session: AsyncSession, user_id: int) -> Decimal:
    total = await session.scalar(
        select(func.coalesce(func.sum(UsageLog.cost_usd), 0)).where(
            UsageLog.user_id == user_id, UsageLog.created_at >= seoul_day_start()
        )
    )
    return Decimal(total or 0)


async def daily_cost_exceeded(session: AsyncSession, user_id: int, settings: Settings) -> bool:
    return await cost_today(session, user_id) >= settings.daily_cost_limit_usd
