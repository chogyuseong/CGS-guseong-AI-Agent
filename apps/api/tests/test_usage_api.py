"""9단계 2번(API): 비용 화면용 GET /api/usage.

일별 토큰과 비용, 용도별(agent, guard, watch, eval) 합계, 모델별 합계, 오늘 남은 한도.
날짜 경계는 하루 비용 상한 검사(budget.seoul_day_start)와 같은 Asia/Seoul 0시다.
"""

from datetime import timedelta
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.budget import seoul_day_start
from app.config import Settings
from app.models import UsageLog, User
from app.models.enums import UsagePurpose
from tests.conftest import UserFactory


def log(user: User, purpose: UsagePurpose, cost: str, *, minutes_from_today: int, model: str = "claude-haiku-5-5"):
    return UsageLog(
        user_id=user.id,
        purpose=purpose,
        model=model,
        input_tokens=1000,
        output_tokens=100,
        cost_usd=Decimal(cost),
        created_at=seoul_day_start() + timedelta(minutes=minutes_from_today),
    )


async def add_sample_logs(db_session: AsyncSession, me: User, other: User) -> None:
    db_session.add_all(
        [
            # 서울 기준 오늘 0시 30분과 어제 23시 30분: UTC로는 같은 날일 수 있지만 서울 날짜가 다르다.
            log(me, UsagePurpose.AGENT, "0.010000", minutes_from_today=30),
            log(me, UsagePurpose.GUARD, "0.001000", minutes_from_today=40),
            log(me, UsagePurpose.AGENT, "0.200000", minutes_from_today=-30, model="claude-sonnet-5-5"),
            log(me, UsagePurpose.EVAL, "0.050000", minutes_from_today=-60 * 24 * 2),
            log(other, UsagePurpose.AGENT, "0.900000", minutes_from_today=10),  # 남의 사용량은 섞이지 않는다
        ]
    )
    await db_session.commit()


async def test_usage_requires_login(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/usage")

    assert response.status_code == 401


async def test_usage_groups_by_seoul_day_purpose_and_model(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    logged_in_user: User,
    make_user: UserFactory,
    test_settings: Settings,
) -> None:
    other = await make_user("other@example.com", "password-other")
    await add_sample_logs(db_session, logged_in_user, other)

    response = await client.get("/api/usage", params={"days": 3})

    assert response.status_code == 200
    body = response.json()
    today = seoul_day_start().date()
    assert body["timezone"] == "Asia/Seoul"
    assert [d["date"] for d in body["days"]] == [(today - timedelta(days=n)).isoformat() for n in (2, 1, 0)]
    assert [d["cost_usd"] for d in body["days"]] == [0.05, 0.2, 0.011]
    assert [d["calls"] for d in body["days"]] == [1, 1, 2]
    assert body["days"][2]["input_tokens"] == 2000 and body["days"][2]["total_tokens"] == 2200
    by_purpose = {p["purpose"]: (p["cost_usd"], p["calls"]) for p in body["by_purpose"]}
    assert by_purpose == {"agent": (0.21, 2), "guard": (0.001, 1), "watch": (0.0, 0), "eval": (0.05, 1)}
    by_model = {m["model"]: m["cost_usd"] for m in body["by_model"]}
    assert by_model == {"claude-haiku-5-5": 0.061, "claude-sonnet-5-5": 0.2}
    assert body["total"]["cost_usd"] == 0.261
    assert body["today"] == {
        "date": today.isoformat(),
        "cost_usd": 0.011,
        "limit_usd": float(test_settings.daily_cost_limit_usd),
        "remaining_usd": float(test_settings.daily_cost_limit_usd - Decimal("0.011")),
        "exceeded": False,
    }


async def test_today_remaining_is_zero_when_limit_exceeded(
    client: httpx.AsyncClient, db_session: AsyncSession, logged_in_user: User, test_settings: Settings
) -> None:
    test_settings.daily_cost_limit_usd = Decimal("0.05")
    db_session.add(log(logged_in_user, UsagePurpose.WATCH, "0.070000", minutes_from_today=5))
    await db_session.commit()

    today = (await client.get("/api/usage", params={"days": 1})).json()["today"]

    assert today["remaining_usd"] == 0.0
    assert today["exceeded"] is True


async def test_usage_days_must_be_in_range(client: httpx.AsyncClient, logged_in_user: User) -> None:
    assert (await client.get("/api/usage", params={"days": 0})).status_code == 422
    assert (await client.get("/api/usage", params={"days": 91})).status_code == 422
    default = (await client.get("/api/usage")).json()
    assert len(default["days"]) == 14
