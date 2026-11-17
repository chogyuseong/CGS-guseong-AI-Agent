"""모델 호출별 예산 예약·정산(1단계 보강, BUILD_SPEC 6.2, 15절 19~23번).

모델을 부르기 전에 예상 비용을 사용자·날짜 원장(daily_budgets)에 잡아 두고, 응답을 받으면 실제 비용으로 정산한다.
가짜 모델 가격은 conftest의 test_model_prices가 넣는다(scripted: 입력 $1, 출력 $5 / 100만 토큰).
"""

import asyncio
from decimal import Decimal

import httpx
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent import budget
from app.agent.agent import MyAgent
from app.agent.guard import GuardAgent
from app.config import MODEL_PRICES, Settings
from app.models import BudgetReservation, DailyBudget, UsageLog, User
from app.models.enums import ReservationStatus, UsagePurpose
from tests.conftest import UserFactory
from tests.fake_tools import FakeExternalCounter
from tests.model_scripts import GuardScript, call, registered, scripted, text
from tests.test_agent_run import send


async def reservations(session: AsyncSession) -> list[BudgetReservation]:
    return list((await session.scalars(select(BudgetReservation).order_by(BudgetReservation.id))).all())


async def ledger(session: AsyncSession, user_id: int) -> DailyBudget:
    row = await session.scalar(select(DailyBudget).where(DailyBudget.user_id == user_id))
    assert row is not None
    await session.refresh(row)
    return row


async def test_each_model_call_is_reserved_then_settled(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession, logged_in_user: User
) -> None:
    model = scripted(call("add_todo", title="우유 사기"), text("추가했습니다."))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "우유 사기 추가해줘")

    assert task["status"] == "done"
    rows = await reservations(db_session)
    assert len(rows) == model.calls == 2
    assert {row.status for row in rows} == {ReservationStatus.SETTLED}
    assert all(row.purpose == UsagePurpose.AGENT and row.task_id == task["id"] for row in rows)
    day = await ledger(db_session, logged_in_user.id)
    assert day.reserved_usd == 0
    logged = sum((log.cost_usd for log in (await db_session.scalars(select(UsageLog))).all()), Decimal(0))
    assert day.spent_usd == sum((row.actual_usd or Decimal(0) for row in rows), Decimal(0)) == logged
    assert day.spent_usd > 0


async def test_call_is_not_sent_when_reservation_would_exceed_daily_limit(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession, test_settings: Settings
) -> None:
    """예상 비용(입력 추정 + 출력 상한)이 남은 한도를 넘으면 모델을 부르지 않는다."""
    test_settings.daily_cost_limit_usd = Decimal("0.001")
    model = scripted(text("나오면 안 된다"))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "안녕")

    assert task["status"] == "budget_exceeded"
    assert model.calls == 0
    assert await reservations(db_session) == []


async def test_unpriced_model_is_blocked(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """가격표에 없는 모델은 비용을 0으로 치지 않고 부르지 않는다(15절 22번)."""
    monkeypatch.delitem(MODEL_PRICES, "scripted")
    model = scripted(text("나오면 안 된다"))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "안녕")

    assert task["status"] == "failed"
    assert "가격" in task["error"]
    assert model.calls == 0
    assert await reservations(db_session) == []


async def test_unknown_result_keeps_reservation(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, db_session: AsyncSession, logged_in_user: User
) -> None:
    """호출이 오류로 끝나면 청구 여부를 모르므로 예약을 풀지 않는다(15절 23번)."""

    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("연결이 끊겼다")

    with agent.override(model=registered(broken)):
        task = await send(client, conversation, "안녕")

    assert task["status"] == "failed"
    [row] = await reservations(db_session)
    assert row.status == ReservationStatus.UNKNOWN
    day = await ledger(db_session, logged_in_user.id)
    assert day.reserved_usd == row.estimated_usd > 0


async def test_same_call_key_reserves_once(
    session_factory: async_sessionmaker[AsyncSession],
    logged_in_user: User,
    test_settings: Settings,
    db_session: AsyncSession,
) -> None:
    """복구 때 같은 호출(같은 call_key)이 다시 와도 한 번만 잡는다."""
    scope = budget.BudgetScope(
        user_id=logged_in_user.id,
        task_id=None,
        key_prefix="replay",
        purpose=UsagePurpose.AGENT,
        db=session_factory,
        settings=test_settings,
    )
    first = await budget.reserve(scope, call_key="replay:3", model="scripted", input_tokens=100)
    again = await budget.reserve(scope, call_key="replay:3", model="scripted", input_tokens=100)

    assert again == first
    assert len(await reservations(db_session)) == 1
    assert (await ledger(db_session, logged_in_user.id)).reserved_usd == first.estimated_usd

    await budget.settle(scope, call_key="replay:3", model="scripted", input_tokens=100, output_tokens=10)
    await budget.settle(scope, call_key="replay:3", model="scripted", input_tokens=100, output_tokens=10)
    day = await ledger(db_session, logged_in_user.id)
    assert day.reserved_usd == 0
    assert day.spent_usd == budget.calculate_cost("scripted", 100, 10)


async def test_guard_call_is_reserved_too(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    fake_external_counter: FakeExternalCounter,
    guard: GuardAgent,
) -> None:
    """감시 에이전트 호출도 같은 원장에 예약·정산한다(15절 21번)."""
    with (
        agent.override(model=scripted(call("fake_external", value=1)).model()),
        guard.override(model=GuardScript().model()),
    ):
        task = await send(client, conversation, "일정 넣어줘")

    assert task["status"] == "waiting_approval"
    rows = await reservations(db_session)
    guard_rows = [row for row in rows if row.purpose == UsagePurpose.GUARD]
    assert len(guard_rows) == 1
    assert guard_rows[0].status == ReservationStatus.SETTLED
    assert guard_rows[0].task_id == task["id"]


async def test_concurrent_reservations_do_not_overspend(
    migrated_database_url: str, test_settings: Settings, make_committed_user: UserFactory
) -> None:
    """동시에 들어온 두 호출이 남은 한도를 함께 보지 못한다. 한도가 한 번 분량이면 하나만 통과한다(15절 19번)."""
    user = await make_committed_user("budget@example.com", "password-budget")
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    scope = budget.BudgetScope(
        user_id=user.id,
        task_id=None,
        key_prefix="race",
        purpose=UsagePurpose.AGENT,
        db=factory,
        settings=test_settings,
    )
    one_call = budget.estimate_cost("scripted", input_tokens=1000)
    test_settings.daily_cost_limit_usd = one_call * Decimal("1.5")

    results = await asyncio.gather(
        budget.reserve(scope, call_key="race:a", model="scripted", input_tokens=1000),
        budget.reserve(scope, call_key="race:b", model="scripted", input_tokens=1000),
        return_exceptions=True,
    )
    await engine.dispose()

    assert sum(isinstance(r, budget.Reservation) for r in results) == 1
    assert sum(isinstance(r, budget.DailyBudgetExceededError) for r in results) == 1
