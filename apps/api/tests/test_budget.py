"""테스트 6: 요청 수 상한, 토큰 상한, 하루 비용 상한을 넘으면 task가 budget_exceeded로 끝난다."""

from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import MyAgent
from app.agent.budget import calculate_cost
from app.config import Settings
from app.models import UsageLog, User
from app.models.enums import UsagePurpose
from tests.conftest import spent_today
from tests.model_scripts import call, scripted, text
from tests.test_agent_run import send


async def test_request_limit_exceeded(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, test_settings: Settings
) -> None:
    test_settings.task_max_requests = 2
    model = scripted(call("list_todos"))  # 끝없이 도구만 부른다

    with agent.override(model=model.model()):
        task = await send(client, conversation, "계속 확인해")

    assert task["status"] == "budget_exceeded"
    assert model.calls == 2  # 상한을 넘는 세 번째 요청은 보내지 않는다
    assert len(task["steps"]) == 2  # 어디까지 했는지 남는다
    assert task["error"]


async def test_token_limit_exceeded(
    client: httpx.AsyncClient, agent: MyAgent, conversation: int, test_settings: Settings
) -> None:
    test_settings.task_max_tokens = 10
    model = scripted(text("아주 긴 답변 " * 50))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "길게 말해줘")

    assert task["status"] == "budget_exceeded"
    assert task["final_answer"] is None


async def test_daily_cost_limit_exceeded_before_calling_model(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    logged_in_user: User,
    test_settings: Settings,
) -> None:
    test_settings.daily_cost_limit_usd = Decimal("1.0")
    # 하루 한도는 원장(daily_budgets)으로만 판단한다(1단계 보강).
    db_session.add(spent_today(logged_in_user.id, Decimal("1.0")))
    await db_session.commit()
    model = scripted(text("이 답변은 나오면 안 된다"))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "안녕")

    assert task["status"] == "budget_exceeded"
    assert model.calls == 0
    messages = (await client.get(f"/api/conversations/{conversation}/messages")).json()
    assert messages[-1]["role"] == "assistant"


async def test_other_users_cost_does_not_count(
    client: httpx.AsyncClient,
    agent: MyAgent,
    conversation: int,
    db_session: AsyncSession,
    make_user,
    test_settings: Settings,
) -> None:
    other = await make_user("other@example.com", "password-other")
    db_session.add(
        UsageLog(
            user_id=other.id,
            purpose=UsagePurpose.AGENT,
            model="claude-sonnet-5-5",
            input_tokens=1,
            output_tokens=1,
            cost_usd=Decimal("5.0"),
        )
    )
    await db_session.commit()
    model = scripted(text("안녕하세요"))

    with agent.override(model=model.model()):
        task = await send(client, conversation, "안녕")

    assert task["status"] == "done"


def test_calculate_cost_uses_model_prices() -> None:
    # Sonnet 5.5: 입력 $2, 출력 $10 (100만 토큰당)
    assert calculate_cost("claude-sonnet-5-5", 1_000_000, 100_000) == Decimal("3.000000")
    # Haiku 5.5: 프롬프트 10만 토큰 이하는 $0.10/$0.50, 넘으면 $0.50/$2.50
    assert calculate_cost("claude-haiku-5-5", 100_000, 10_000) == Decimal("0.015000")
    assert calculate_cost("claude-haiku-5-5", 200_000, 10_000) == Decimal("0.125000")
    # 가격표에 없는 모델은 기록용 계산에서 0이다. 그런 모델은 예약 단계에서 막혀 실제로 불리지 않는다.
    assert calculate_cost("unregistered-model", 1000, 1000) == Decimal("0")
