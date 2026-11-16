"""비용 화면(9단계 2번)의 합계 계산. usage_logs만 읽는다.

- 날짜는 Asia/Seoul 기준이다. 하루 비용 상한 검사(app/agent/budget.py의 seoul_day_start, cost_today)와 같은 0시 경계를
  쓰므로 화면의 "오늘 비용"과 서버가 상한을 검사할 때의 금액이 같다.
- 모든 조회에 user_id 조건을 건다.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute

from app.agent.budget import COST_PLACES, cost_today, seoul_day_start
from app.config import Settings
from app.models import UsageLog
from app.models.enums import UsagePurpose
from app.schemas.usage import (
    USAGE_TIMEZONE,
    UsageDayOut,
    UsageModelOut,
    UsageOut,
    UsagePurposeOut,
    UsageTodayOut,
    UsageTotals,
)

MAX_DAYS = 90
DEFAULT_DAYS = 14


def money(value: Decimal | int | None) -> float:
    return float(Decimal(value or 0).quantize(COST_PLACES))


def total_columns() -> list[ColumnElement[Any]]:
    return [
        func.coalesce(func.sum(UsageLog.input_tokens), 0),
        func.coalesce(func.sum(UsageLog.output_tokens), 0),
        func.coalesce(func.sum(UsageLog.cost_usd), 0),
        func.count(UsageLog.id),
    ]


def totals(row: Sequence[Any] | None) -> dict[str, Any]:
    input_tokens, output_tokens, cost, calls = row if row is not None else (0, 0, 0, 0)
    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "total_tokens": int(input_tokens) + int(output_tokens),
        "cost_usd": money(cost),
        "calls": int(calls),
    }


async def grouped(
    session: AsyncSession, user_id: int, since: datetime, key: ColumnElement[Any] | QueryableAttribute[Any]
) -> dict[Any, Any]:
    """since 이후 내 사용량을 key별로 합친다. {key 값: (입력, 출력, 비용, 행 수)}"""
    rows = await session.execute(
        select(key, *total_columns()).where(UsageLog.user_id == user_id, UsageLog.created_at >= since).group_by(key)
    )
    return {row[0]: row[1:] for row in rows}


def seoul_date_of_row() -> ColumnElement[Any]:
    # timestamptz → 서울 현지 시각 → 날짜
    return func.date(func.timezone(USAGE_TIMEZONE, UsageLog.created_at))


async def today_out(session: AsyncSession, user_id: int, settings: Settings, today: date) -> UsageTodayOut:
    spent = await cost_today(session, user_id)
    limit = settings.daily_cost_limit_usd
    return UsageTodayOut(
        date=today,
        cost_usd=money(spent),
        limit_usd=money(limit),
        remaining_usd=money(max(limit - spent, Decimal(0))),
        exceeded=spent >= limit,
    )


async def usage_summary(
    session: AsyncSession, user_id: int, settings: Settings, *, days: int, now: datetime | None = None
) -> UsageOut:
    today_start = seoul_day_start(now)
    today = today_start.date()
    since = today_start - timedelta(days=days - 1)
    by_day = await grouped(session, user_id, since, seoul_date_of_row())
    by_purpose = await grouped(session, user_id, since, UsageLog.purpose)
    by_model = await grouped(session, user_id, since, UsageLog.model)
    day_list = [today - timedelta(days=n) for n in range(days - 1, -1, -1)]
    models = [UsageModelOut(model=name, **totals(row)) for name, row in by_model.items()]
    return UsageOut(
        timezone=USAGE_TIMEZONE,
        days=[UsageDayOut(date=day, **totals(by_day.get(day))) for day in day_list],
        by_purpose=[UsagePurposeOut(purpose=p, **totals(by_purpose.get(p))) for p in UsagePurpose],
        by_model=sorted(models, key=lambda m: (-m.cost_usd, m.model)),
        total=UsageTotals(**totals(sum_rows(by_day.values()))),
        today=await today_out(session, user_id, settings, today),
    )


def sum_rows(rows: Any) -> tuple[int, int, Decimal, int]:
    input_tokens, output_tokens, cost, calls = 0, 0, Decimal(0), 0
    for row in rows:
        input_tokens += int(row[0])
        output_tokens += int(row[1])
        cost += Decimal(row[2] or 0)
        calls += int(row[3])
    return input_tokens, output_tokens, cost, calls
