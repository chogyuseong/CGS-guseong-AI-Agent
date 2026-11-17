"""사용량 상한과 비용.

상한은 세 가지다.
1. 요청 수 상한(TASK_MAX_REQUESTS): Pydantic AI UsageLimits(request_limit). 넘기 직전 요청은 보내지 않는다.
   재개한 run도 앞선 run의 사용량을 이어받아 누적으로 센다(runner.previous_usage).
2. 토큰 상한(TASK_MAX_TOKENS): UsageLimits(total_tokens_limit). 응답을 받은 뒤 합계가 넘으면 멈춘다.
3. 하루 비용 상한(DAILY_COST_LIMIT_USD): 1단계 보강(BUILD_SPEC 6.2)부터 모델 호출 한 번마다 예약·정산한다.

호출별 예약(BudgetCapability.wrap_model_request, 메인·감시·감시 판정 에이전트 공통):

    1. 예상 비용 = 입력 토큰 추정(요청 글자 수 + 지시문·도구 설명 몫) + 출력 상한(MAX_OUTPUT_TOKENS)
       가격표에 없는 모델이면 UnpricedModelError(부르지 않는다. 0원으로 치지 않는다)
    2. daily_budgets(사용자·서울 날짜) 행을 FOR UPDATE로 잠그고 spent + reserved + 예상 > 한도면
       DailyBudgetExceededError(부르지 않는다). 아니면 reserved에 더하고 budget_reservations 행을 만든다.
    3. 모델 호출
    4. 응답을 받으면 실제 비용으로 정산(reserved에서 빼고 spent에 더함, status=settled)
       오류·시간 초과면 status=unknown. 청구됐을 수 있으므로 예약액을 풀지 않는다(그날 한도에 계속 잡힌다).

복구: 이 훅은 DBOS 단계(모델 호출) 밖에서 돈다. 서버가 죽었다 살아나 워크플로가 다시 실행되면 훅도 다시 불린다.
그래서 call_key를 "범위:지금까지의 메시지 수"로 만들어 같은 호출이면 같은 키가 되게 했다. 같은 키의 예약이 있으면
새로 잡지 않고, 이미 정산된 키는 다시 정산하지 않는다.

한계(docs/DECISIONS.md): 예상은 보수적인 추정이다. 이미 보낸 호출의 실제 청구가 예상을 넘으면
그날 한도를 조금 넘을 수 있다.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.usage import UsageLimits
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.prompts import SEOUL
from app.config import MODEL_PRICES, ModelPrice, Settings
from app.models import BudgetReservation, DailyBudget, UsageLog
from app.models.enums import ReservationStatus, UsagePurpose

logger = logging.getLogger(__name__)

ONE_MILLION = Decimal(1_000_000)
COST_PLACES = Decimal("0.000001")  # NUMERIC(…, 6)
# 모델 응답 한 번의 출력 상한. 세 에이전트의 model_settings.max_tokens로도 건다(예상이 실제 상한과 같도록).
MAX_OUTPUT_TOKENS = 4096
# 메시지 기록 밖에서 붙는 입력(시스템 지시문, 날짜·기억 지시문, 도구 설명)의 몫. 도구 20여 개 기준으로 넉넉히 잡았다.
INSTRUCTION_TOKEN_ALLOWANCE = 4000
UNPRICED_MESSAGE = "가격이 등록되지 않은 모델이라 비용을 알 수 없어 부르지 않았습니다."


class DailyBudgetExceededError(Exception):
    """이번 호출의 예상 비용을 잡으면 오늘 한도를 넘는다. 모델을 부르지 않았다."""


class UnpricedModelError(Exception):
    """가격표(MODEL_PRICES)에 없는 모델. 비용을 0으로 치지 않고 부르지 않는다."""


def build_usage_limits(settings: Settings) -> UsageLimits:
    return UsageLimits(request_limit=settings.task_max_requests, total_tokens_limit=settings.task_max_tokens)


# ---- 가격 ----


def price_for(model_name: str) -> ModelPrice | None:
    return MODEL_PRICES.get(model_name.removeprefix("anthropic:"))


def price_rates(price: ModelPrice, input_tokens: int) -> tuple[Decimal, Decimal]:
    """요청 하나의 (입력, 출력) 단가. 프롬프트가 길면 비싼 구간 가격을 쓴다."""
    threshold = price.long_prompt_threshold
    if threshold is not None and input_tokens > threshold:
        return price.long_input_per_mtok or price.input_per_mtok, price.long_output_per_mtok or price.output_per_mtok
    return price.input_per_mtok, price.output_per_mtok


def cost_with(price: ModelPrice, input_tokens: int, output_tokens: int) -> Decimal:
    input_rate, output_rate = price_rates(price, input_tokens)
    cost = (Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate) / ONE_MILLION
    return cost.quantize(COST_PLACES)


def calculate_cost(model_name: str, input_tokens: int, output_tokens: int) -> Decimal:
    """이미 끝난 호출의 비용(usage_logs 기록용). 가격표에 없으면 0과 경고(그런 모델은 예약 단계에서 막힌다)."""
    price = price_for(model_name)
    if price is None:
        logger.warning("가격표(MODEL_PRICES)에 없는 모델이라 비용을 0으로 기록합니다: %s", model_name)
        return Decimal("0")
    return cost_with(price, input_tokens, output_tokens)


def estimate_cost(model_name: str, *, input_tokens: int) -> Decimal:
    """호출 전 예상 비용: 입력 추정 + 출력 상한. 가격이 없으면 UnpricedModelError."""
    price = price_for(model_name)
    if price is None:
        raise UnpricedModelError(model_name)
    return cost_with(price, input_tokens, MAX_OUTPUT_TOKENS)


def estimate_input_tokens(messages: list[ModelMessage]) -> int:
    """입력 토큰을 넉넉하게 추정한다.

    한국어는 글자 하나가 토큰 하나 안팎이라 직렬화한 글자 수를 그대로 토큰으로 본다.
    """
    serialized = ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")
    return len(serialized) + INSTRUCTION_TOKEN_ALLOWANCE


# ---- 날짜 ----


def seoul_today(now: datetime | None = None) -> date:
    return (now or datetime.now(SEOUL)).astimezone(SEOUL).date()


def seoul_day_start(now: datetime | None = None) -> datetime:
    """Asia/Seoul 기준 오늘 0시(시간대 포함)."""
    return datetime.combine(seoul_today(now), time.min, tzinfo=SEOUL)


# ---- 원장 ----


@dataclass(frozen=True)
class BudgetScope:
    """예약을 누구 앞으로, 어떤 키로 잡을지. 메인 에이전트는 AgentDeps.budget_scope, 감시·판정은 이 값을 deps로 쓴다."""

    user_id: int
    task_id: int | None
    key_prefix: str
    purpose: UsagePurpose
    db: async_sessionmaker[AsyncSession]
    settings: Settings

    @property
    def budget_scope(self) -> "BudgetScope":
        return self


@dataclass(frozen=True)
class Reservation:
    call_key: str
    estimated_usd: Decimal


async def lock_day(session: AsyncSession, user_id: int, local_date: date) -> DailyBudget:
    """사용자·날짜 행을 (없으면 만들고) FOR UPDATE로 잠근다. 같은 사용자의 예약·정산은 이 행에서 한 줄로 선다."""
    await session.execute(insert(DailyBudget).values(user_id=user_id, local_date=local_date).on_conflict_do_nothing())
    query = (
        select(DailyBudget)
        .where(DailyBudget.user_id == user_id, DailyBudget.local_date == local_date)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.scalars(query)).one()


async def find_reservation(session: AsyncSession, call_key: str, *, lock: bool = False) -> BudgetReservation | None:
    query = select(BudgetReservation).where(BudgetReservation.call_key == call_key)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


async def reserve(scope: BudgetScope, *, call_key: str, model: str, input_tokens: int) -> Reservation:
    """호출 전 예상 비용을 잡는다. 같은 call_key가 이미 있으면 그대로 돌려준다(복구 때 다시 불린 경우)."""
    estimated = estimate_cost(model, input_tokens=input_tokens)
    async with scope.db() as session:
        existing = await find_reservation(session, call_key)
        if existing is not None:
            return Reservation(call_key, existing.estimated_usd)
        local_date = seoul_today()
        day = await lock_day(session, scope.user_id, local_date)
        if day.spent_usd + day.reserved_usd + estimated > scope.settings.daily_cost_limit_usd:
            await session.rollback()
            raise DailyBudgetExceededError(call_key)
        day.reserved_usd += estimated
        session.add(
            BudgetReservation(
                user_id=scope.user_id,
                task_id=scope.task_id,
                call_key=call_key,
                local_date=local_date,
                purpose=scope.purpose,
                model=model[:100],
                estimated_usd=estimated,
            )
        )
        await session.commit()
    return Reservation(call_key, estimated)


async def settle(scope: BudgetScope, *, call_key: str, model: str, input_tokens: int, output_tokens: int) -> None:
    """응답을 받은 호출을 실제 비용으로 정산한다. 이미 정산했거나 예약이 없으면 아무것도 하지 않는다."""
    async with scope.db() as session:
        row = await find_reservation(session, call_key, lock=True)
        if row is None or row.status == ReservationStatus.SETTLED:
            return
        actual = calculate_cost(model, input_tokens, output_tokens)
        day = await lock_day(session, row.user_id, row.local_date)
        day.reserved_usd -= row.estimated_usd
        day.spent_usd += actual
        row.actual_usd, row.status = actual, ReservationStatus.SETTLED
        await session.commit()


async def mark_unknown(scope: BudgetScope, *, call_key: str) -> None:
    """결과를 모르는 호출. 예약액은 그대로 둔다(청구됐을 수 있다)."""
    async with scope.db() as session:
        row = await find_reservation(session, call_key, lock=True)
        if row is not None and row.status == ReservationStatus.RESERVED:
            row.status = ReservationStatus.UNKNOWN
            await session.commit()


async def committed_today(session: AsyncSession, user_id: int, now: datetime | None = None) -> Decimal:
    """오늘 한도에 잡힌 금액(정산된 비용 + 아직 정산 안 된 예약). 한도 판단은 이 값으로만 한다."""
    row = await session.scalar(
        select(DailyBudget).where(DailyBudget.user_id == user_id, DailyBudget.local_date == seoul_today(now))
    )
    return Decimal(0) if row is None else row.spent_usd + row.reserved_usd


async def daily_cost_exceeded(session: AsyncSession, user_id: int, settings: Settings) -> bool:
    """작업을 시작하기 전 빠른 확인. 실제 막기는 호출마다의 reserve가 한다."""
    return await committed_today(session, user_id) >= settings.daily_cost_limit_usd


async def cost_today(session: AsyncSession, user_id: int) -> Decimal:
    """오늘 usage_logs에 기록된 실제 비용 합계(비용 화면·eval 안내용)."""
    total = await session.scalar(
        select(func.coalesce(func.sum(UsageLog.cost_usd), 0)).where(
            UsageLog.user_id == user_id, UsageLog.created_at >= seoul_day_start()
        )
    )
    return Decimal(total or 0)


# ---- capability ----

type ModelRequestHandler = Callable[[ModelRequestContext], Awaitable[ModelResponse]]


def model_name_of(request_context: ModelRequestContext) -> str:
    return request_context.model.model_name


class BudgetCapability(AbstractCapability[Any]):
    """모델 호출 한 번마다 예약 → 호출 → 정산. deps는 budget_scope(BudgetScope)를 가져야 한다."""

    async def wrap_model_request(
        self, ctx: RunContext[Any], *, request_context: ModelRequestContext, handler: ModelRequestHandler
    ) -> ModelResponse:
        scope: BudgetScope = ctx.deps.budget_scope
        call_key = f"{scope.key_prefix}:{len(ctx.messages)}"
        model = model_name_of(request_context)
        await reserve(
            scope, call_key=call_key, model=model, input_tokens=estimate_input_tokens(request_context.messages)
        )
        try:
            response = await handler(request_context)
        except BaseException:
            await mark_unknown(scope, call_key=call_key)
            raise
        usage = response.usage
        await settle(
            scope, call_key=call_key, model=model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
        )
        return response
