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

진입점:
- BudgetCapability: agent.py(메인), guard.py(감시), automation/watch.py(감시 판정)의 capabilities.
  deps가 budget_scope를 가져야 한다. 메인은 AgentDeps.budget_scope(키 "task:{id}", deps.py), 감시는 runner가
  "guard:{task}:{tool_call_id}", 감시 판정은 automation/check.py가 "watch:{job}:{시각}"으로 만든다.
- 예외: DailyBudgetExceededError·UnpricedModelError는 run 밖으로 올라간다. runner.execute_agent가 각각
  budget_exceeded·failed로 끝낸다. 감시는 unsure(guard.check_action), 감시 판정은 judged=False(watch.judge)로 바꾼다.
- daily_cost_exceeded: runner.prepare_run·prepare_resume(작업·재개 시작 전), automation/check.py(감시 실행 전),
  routers/automations.py(목록의 멈춤 표시).
- committed_today·cost_today·seoul_day_start: usage_report.py(비용 화면). cost_today는 evals/run_evals.py도 쓴다.
- calculate_cost: runner·check.py(usage_logs 기록), evals/estimate.py(eval 예상 비용).

근거: BUILD_SPEC 6.2(예산 예약: 호출 전 예약, 부족하면 호출 안 함, 정산, 결과 불명 예약을 무조건 풀지 않음,
사용자 시간대 날짜), 1.4 절대 원칙 14(비용 제한은 서버에서 검사). DECISIONS 2026-11-16(MODEL_PRICES 값,
usage_logs는 응답마다 한 행), 2026-11-17(daily_budgets·budget_reservations 원장, FOR UPDATE, 한도는 원장으로만 판단).

고칠 때 주의:
- 가격표는 app/config.py의 MODEL_PRICES다. 값을 바꾸면 docs/DECISIONS.md(가격 줄), docs/EVALS.md "예상 비용"의 가격과
  표를 `uv run python -m evals.run_evals`로 다시 뽑고, tests/test_budget.py의 calculate_cost 기대값을 고친다.
- MAX_OUTPUT_TOKENS는 model_choice.model_settings_for의 max_tokens로도 쓰인다(예상과 실제 상한이 같아야 한다).
- call_key 모양(접두사:메시지 수)을 바꾸면 복구 때 같은 호출이 두 번 예약된다. budget_reservations.call_key는
  사용자별이 아니라 테이블 전체에서 UNIQUE이고 200자다(app/models/budget.py). 새 접두사는 task·job id처럼 전역에서
  겹치지 않는 값을 넣고, 길게 만들지 않는다. 겹치면 남의 예약을 "이미 잡힘"으로 보고 돌려받는다.
- 하루 경계는 Asia/Seoul 0시, 실제 시각이다(eval의 고정 시각 clock.now_utc를 쓰지 않는다. docs/EVALS.md).
- 테스트: tests/test_budget.py(가격 계산), tests/test_budget_reservations.py(예약·정산·불명·같은 키·동시 예약),
  tests/test_budget_pause.py(감시 멈춤), tests/test_usage_api.py(비용 화면 날짜 경계).
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

# 가격표 단가가 "100만 토큰당 달러"라서 나눈다(MODEL_PRICES의 *_per_mtok).
ONE_MILLION = Decimal(1_000_000)
COST_PLACES = Decimal("0.000001")  # NUMERIC(…, 6). app/models/budget.py의 MONEY = Numeric(12, 6)과 자릿수를 맞춘다
# 모델 응답 한 번의 출력 상한. 세 에이전트의 model_settings.max_tokens로도 건다(예상이 실제 상한과 같도록).
# model_choice.model_settings_for가 이 값을 쓴다. 값을 바꾸면 예상 비용(예약액)도 같이 바뀐다.
MAX_OUTPUT_TOKENS = 4096
# 메시지 기록 밖에서 붙는 입력(시스템 지시문, 날짜·기억 지시문, 도구 설명)의 몫. 도구 20여 개 기준으로 넉넉히 잡았다.
# 너무 작으면 예약이 실제보다 작아 하루 한도를 넘길 수 있고, 너무 크면 한도 근처에서 호출이 일찍 막힌다.
INSTRUCTION_TOKEN_ALLOWANCE = 4000
# runner.execute_agent가 UnpricedModelError를 받았을 때 task의 오류 문구(Outcome.error)로 쓴다.
UNPRICED_MESSAGE = "가격이 등록되지 않은 모델이라 비용을 알 수 없어 부르지 않았습니다."


class DailyBudgetExceededError(Exception):
    """이번 호출의 예상 비용을 잡으면 오늘 한도를 넘는다. 모델을 부르지 않았다."""


class UnpricedModelError(Exception):
    """가격표(MODEL_PRICES)에 없는 모델. 비용을 0으로 치지 않고 부르지 않는다."""


def build_usage_limits(settings: Settings) -> UsageLimits:
    """task 하나의 요청 수·토큰 상한(.env TASK_MAX_REQUESTS·TASK_MAX_TOKENS). runner가 agent.run에 넘긴다.

    넘으면 Pydantic AI가 UsageLimitExceeded를 내고 runner가 budget_exceeded로 끝낸다. 하루 비용 한도와는 별개다.
    """
    return UsageLimits(request_limit=settings.task_max_requests, total_tokens_limit=settings.task_max_tokens)


# ---- 가격 ----


def price_for(model_name: str) -> ModelPrice | None:
    """가격표 항목. 모델 문자열에 "anthropic:" 접두사가 붙어 와도 찾도록 떼고 본다. 없으면 None."""
    return MODEL_PRICES.get(model_name.removeprefix("anthropic:"))


def price_rates(price: ModelPrice, input_tokens: int) -> tuple[Decimal, Decimal]:
    """요청 하나의 (입력, 출력) 단가. 프롬프트가 길면 비싼 구간 가격을 쓴다.

    구간은 입력 토큰 수로만 고른다(Haiku 5.5: 10만 토큰 초과 구간, config.MODEL_PRICES 주석). 긴 구간 값이 비어 있으면
    기본 단가를 쓴다.
    """
    threshold = price.long_prompt_threshold
    if threshold is not None and input_tokens > threshold:
        return price.long_input_per_mtok or price.input_per_mtok, price.long_output_per_mtok or price.output_per_mtok
    return price.input_per_mtok, price.output_per_mtok


def cost_with(price: ModelPrice, input_tokens: int, output_tokens: int) -> Decimal:
    """토큰 수와 단가로 달러 비용. float 오차가 원장 합계에 쌓이지 않게 Decimal로 계산하고 DB 자릿수로 반올림한다."""
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
    """호출 전 예상 비용: 입력 추정 + 출력 상한. 가격이 없으면 UnpricedModelError.

    calculate_cost와 달리 0으로 넘어가지 않는다: 가격을 모르는 모델을 공짜로 치면 하루 한도가 무력해진다.
    """
    price = price_for(model_name)
    if price is None:
        raise UnpricedModelError(model_name)
    return cost_with(price, input_tokens, MAX_OUTPUT_TOKENS)


def estimate_input_tokens(messages: list[ModelMessage]) -> int:
    """입력 토큰을 넉넉하게 추정한다.

    한국어는 글자 하나가 토큰 하나 안팎이라 직렬화한 글자 수를 그대로 토큰으로 본다.
    호출 전에 토큰을 세는 API를 부르지 않으려는 근사다(추가 요청·비용 없음). JSON 키·따옴표까지 세므로 대개
    실제보다 크다. 정산은 응답의 실제 usage로 하므로 이 값은 예약액에만 쓰인다.
    """
    serialized = ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")
    return len(serialized) + INSTRUCTION_TOKEN_ALLOWANCE


# ---- 날짜 ----


def seoul_today(now: datetime | None = None) -> date:
    """하루 원장의 날짜(Asia/Seoul, BUILD_SPEC 6.2 "사용자 시간대 기준"). now가 없으면 실제 지금 시각."""
    return (now or datetime.now(SEOUL)).astimezone(SEOUL).date()


def seoul_day_start(now: datetime | None = None) -> datetime:
    """Asia/Seoul 기준 오늘 0시(시간대 포함). usage_logs 합계(cost_today, 비용 화면)의 시작 경계다."""
    return datetime.combine(seoul_today(now), time.min, tzinfo=SEOUL)


# ---- 원장 ----


@dataclass(frozen=True)
class BudgetScope:
    """예약을 누구 앞으로, 어떤 키로 잡을지. 메인 에이전트는 AgentDeps.budget_scope, 감시·판정은 이 값을 deps로 쓴다.

    task_id는 task 없는 호출(감시 판정)이면 None이다. key_prefix는 같은 사용자 안에서 호출 묶음을 구별하고, 같은
    호출이 복구로 다시 와도 같은 값이어야 한다(파일 머리 "복구"). purpose는 budget_reservations에 그대로 남는다.
    """

    user_id: int
    task_id: int | None
    key_prefix: str
    purpose: UsagePurpose
    db: async_sessionmaker[AsyncSession]
    settings: Settings

    @property
    def budget_scope(self) -> "BudgetScope":
        # BudgetCapability는 ctx.deps.budget_scope를 읽는다. 감시·판정은 BudgetScope 자체가 deps라 자기를 돌려준다.
        return self


@dataclass(frozen=True)
class Reservation:
    """reserve의 결과(잡힌 키와 예약액). DB 행이 아니라 값만 담는다."""

    call_key: str
    estimated_usd: Decimal


async def lock_day(session: AsyncSession, user_id: int, local_date: date) -> DailyBudget:
    """사용자·날짜 행을 (없으면 만들고) FOR UPDATE로 잠근다. 같은 사용자의 예약·정산은 이 행에서 한 줄로 선다.

    잠금이 없으면 동시에 온 두 호출이 같은 잔액을 보고 둘 다 예약해 한도를 넘는다(test_concurrent_reservations_*).
    ON CONFLICT DO NOTHING은 (user_id, local_date) UNIQUE에 기대어 첫 행 만들기 경쟁을 없앤다. populate_existing은
    세션에 남은 옛 값 대신 잠근 뒤의 값을 읽게 한다. 잠금은 호출한 쪽의 커밋·롤백까지 이어진다(커밋은 하지 않는다).
    """
    await session.execute(insert(DailyBudget).values(user_id=user_id, local_date=local_date).on_conflict_do_nothing())
    query = (
        select(DailyBudget)
        .where(DailyBudget.user_id == user_id, DailyBudget.local_date == local_date)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.scalars(query)).one()


async def find_reservation(session: AsyncSession, call_key: str, *, lock: bool = False) -> BudgetReservation | None:
    """call_key(UNIQUE)로 예약 행을 찾는다. lock=True면 FOR UPDATE로 잠가 정산·불명 처리가 겹치지 않게 한다."""
    query = select(BudgetReservation).where(BudgetReservation.call_key == call_key)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


async def reserve(scope: BudgetScope, *, call_key: str, model: str, input_tokens: int) -> Reservation:
    """호출 전 예상 비용을 잡는다. 같은 call_key가 이미 있으면 그대로 돌려준다(복구 때 다시 불린 경우).

    예외: 가격이 없으면 UnpricedModelError(DB를 열기 전), 한도를 넘으면 롤백 뒤 DailyBudgetExceededError.
    부수 효과: daily_budgets.reserved_usd 증가와 budget_reservations 행 추가를 한 트랜잭션으로 커밋한다.
    이미 있는 키는 상태(settled·unknown 포함)를 보지 않고 돌려준다. 정산 여부는 settle이 다시 확인한다.
    날짜는 예약 때 정해 행에 남긴다(자정을 넘겨 정산해도 같은 날 원장에 정산된다).
    """
    estimated = estimate_cost(model, input_tokens=input_tokens)
    async with scope.db() as session:
        existing = await find_reservation(session, call_key)
        if existing is not None:
            return Reservation(call_key, existing.estimated_usd)
        local_date = seoul_today()
        day = await lock_day(session, scope.user_id, local_date)
        # 정산된 비용 + 아직 정산 안 된 예약 + 이번 예상으로 판단한다. 진행 중 호출을 빼면 동시 호출이 한도를 넘긴다.
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
                model=model[:100],  # 컬럼 길이에 맞춰 자른다(긴 모델 문자열로 INSERT가 실패하지 않게)
                estimated_usd=estimated,
            )
        )
        await session.commit()
    return Reservation(call_key, estimated)


async def settle(scope: BudgetScope, *, call_key: str, model: str, input_tokens: int, output_tokens: int) -> None:
    """응답을 받은 호출을 실제 비용으로 정산한다. 이미 정산했거나 예약이 없으면 아무것도 하지 않는다.

    복구 재생에서 같은 키로 다시 와도 두 번 더하지 않는다(SETTLED 확인). unknown 행도 응답을 받았다면 정산한다.
    잠금 순서는 예약 행 → 날짜 행이다. 부수 효과: reserved에서 예약액을 빼고 spent에 실제 비용을 더해 커밋한다.
    usage_logs 기록은 여기서 하지 않는다(runner·check.py가 따로 쓴다).
    """
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
    """결과를 모르는 호출. 예약액은 그대로 둔다(청구됐을 수 있다).

    예약을 풀면 실제로 청구된 호출이 한도 계산에서 빠져 하루 한도를 넘길 수 있다(BUILD_SPEC 6.2 "결과 불명 예약을
    무조건 해제하지 않는다"). reserved 상태일 때만 바꾸고, 정산된 행은 건드리지 않는다. 잡힌 금액은 그날 계속 남는다.
    """
    async with scope.db() as session:
        row = await find_reservation(session, call_key, lock=True)
        if row is not None and row.status == ReservationStatus.RESERVED:
            row.status = ReservationStatus.UNKNOWN
            await session.commit()


async def committed_today(session: AsyncSession, user_id: int, now: datetime | None = None) -> Decimal:
    """오늘 한도에 잡힌 금액(정산된 비용 + 아직 정산 안 된 예약). 한도 판단은 이 값으로만 한다.

    잠그지 않고 읽는다(표시·빠른 확인용). 원장 행이 없으면 0. 비용 화면의 "남은 한도"도 이 값이다(usage_report.py).
    """
    row = await session.scalar(
        select(DailyBudget).where(DailyBudget.user_id == user_id, DailyBudget.local_date == seoul_today(now))
    )
    return Decimal(0) if row is None else row.spent_usd + row.reserved_usd


async def daily_cost_exceeded(session: AsyncSession, user_id: int, settings: Settings) -> bool:
    """작업을 시작하기 전 빠른 확인. 실제 막기는 호출마다의 reserve가 한다.

    한도와 같아도 True(>=)다: 남은 돈이 0이면 어떤 호출도 예약을 통과할 수 없어 시작하지 않는다.
    """
    return await committed_today(session, user_id) >= settings.daily_cost_limit_usd


async def cost_today(session: AsyncSession, user_id: int) -> Decimal:
    """오늘 usage_logs에 기록된 실제 비용 합계(비용 화면·eval 안내용).

    한도 판단에는 쓰지 않는다(원장과 다르다: 진행 중 예약·결과 불명 호출이 빠진다. DECISIONS 2026-11-17).
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(UsageLog.cost_usd), 0)).where(
            UsageLog.user_id == user_id, UsageLog.created_at >= seoul_day_start()
        )
    )
    return Decimal(total or 0)


# ---- capability ----

type ModelRequestHandler = Callable[[ModelRequestContext], Awaitable[ModelResponse]]


def model_name_of(request_context: ModelRequestContext) -> str:
    """이번 요청의 모델 이름. 예약·정산 모두 이 값으로 가격표를 찾는다(price_for)."""
    return request_context.model.model_name


class BudgetCapability(AbstractCapability[Any]):
    """모델 호출 한 번마다 예약 → 호출 → 정산. deps는 budget_scope(BudgetScope)를 가져야 한다.

    상태가 없어 에이전트마다 하나 만들어 모든 run이 같이 쓴다. deps 타입이 에이전트마다 달라 Any로 받는다.
    """

    async def wrap_model_request(
        self, ctx: RunContext[Any], *, request_context: ModelRequestContext, handler: ModelRequestHandler
    ) -> ModelResponse:
        """예약이 실패하면(한도·가격 없음) handler를 부르지 않고 예외를 올린다. 모델을 부르지 않았으니 청구도 없다.

        handler가 어떤 예외로든(시간 초과, 취소 포함) 끝나면 예약을 unknown으로 두고 예외를 그대로 올린다.
        """
        scope: BudgetScope = ctx.deps.budget_scope
        # 복구 때 DBOS가 같은 메시지를 재생하므로 메시지 수가 같다 → 같은 키 → 예약·정산이 한 번만 일어난다.
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
