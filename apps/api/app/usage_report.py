"""비용 화면(9단계 2번)의 합계 계산. 합계는 usage_logs, 오늘 남은 한도는 하루 원장(daily_budgets)을 읽는다.

- 날짜는 Asia/Seoul 기준이다(app/agent/budget.py와 같은 0시 경계).
- 1단계 보강: 서버는 원장(정산된 비용 + 아직 정산 안 된 예약)으로 한도를 막으므로,
  "남은 한도"·"초과"도 원장으로 보여 준다.
  결과를 모르는 호출의 예약은 풀리지 않아 "오늘 비용"(기록된 실제 비용)보다 많이 잡힐 수 있다.
- 모든 조회에 user_id 조건을 건다.

진입점: app/routers/usage.py의 `GET /api/usage/daily`(DECISIONS 2026-11-18 이름 변경)가 usage_summary를 부른다.
읽기만 하고 커밋하지 않는다.

고칠 때 주의
- 응답 모양(app/schemas/usage.py)을 바꾸면 `pnpm --dir apps/web gen:api`로 웹 타입을 다시 만든다.
- 비용 자릿수(COST_PLACES)·하루 경계(seoul_day_start)·오늘 합계(cost_today, committed_today)는 agent/budget.py의
  것을 그대로 쓴다. 여기서 따로 계산하면 화면 숫자와 서버의 한도 판단이 어긋난다.
- 관련 테스트: tests/test_usage_api.py.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute

from app.agent.budget import COST_PLACES, committed_today, cost_today, seoul_day_start
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

# 비용 화면이 한 번에 볼 수 있는 날 수(routers/usage.py days 쿼리의 상한·기본값, OpenAPI에도 실린다).
# 상한은 한 요청의 집계 범위를 묶어 두려는 것이다. 바꾸면 gen:api로 웹 타입을 다시 만든다.
MAX_DAYS = 90
DEFAULT_DAYS = 14


def money(value: Decimal | int | None) -> float:
    """달러 값을 COST_PLACES 자리로 맞춘 float(JSON 응답용). None은 0."""
    return float(Decimal(value or 0).quantize(COST_PLACES))


def total_columns() -> list[ColumnElement[Any]]:
    """합계 열 4개(입력 토큰, 출력 토큰, 비용, 호출 수). 순서는 totals·sum_rows가 위치로 읽으므로 같이 바꾼다."""
    return [
        func.coalesce(func.sum(UsageLog.input_tokens), 0),
        func.coalesce(func.sum(UsageLog.output_tokens), 0),
        func.coalesce(func.sum(UsageLog.cost_usd), 0),
        func.count(UsageLog.id),
    ]


def totals(row: Sequence[Any] | None) -> dict[str, Any]:
    """합계 행을 응답 필드로. 기록이 없는 날·용도(None)는 0으로 채운다(화면이 빈 칸 없이 그린다)."""
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
    """since 이후 내 사용량을 key별로 합친다. {key 값: (입력, 출력, 비용, 행 수)}

    user_id 조건이 빠지면 다른 사용자의 사용량이 섞인다(이 조건이 사용자 간 경계다).
    """
    rows = await session.execute(
        select(key, *total_columns()).where(UsageLog.user_id == user_id, UsageLog.created_at >= since).group_by(key)
    )
    return {row[0]: row[1:] for row in rows}


def seoul_date_of_row() -> ColumnElement[Any]:
    """행의 서울 날짜(DB에서 계산). UTC 날짜로 묶으면 서울 오전 9시 전 사용량이 전날로 들어간다."""
    # timestamptz → 서울 현지 시각 → 날짜
    return func.date(func.timezone(USAGE_TIMEZONE, UsageLog.created_at))


async def today_out(session: AsyncSession, user_id: int, settings: Settings, today: date) -> UsageTodayOut:
    """오늘 칸. cost_usd는 기록된 실제 비용, 남은 한도·초과는 원장(정산 + 미정산 예약)으로 계산한다.

    서버가 실제로 막는 기준(원장)과 화면 표시를 맞추려는 것이다. 그래서 cost_usd + remaining_usd가 한도와
    다를 수 있다(결과를 모르는 호출의 예약이 남아 있을 때).
    """
    spent = await cost_today(session, user_id)
    committed = await committed_today(session, user_id)
    limit = settings.daily_cost_limit_usd
    return UsageTodayOut(
        date=today,
        cost_usd=money(spent),
        limit_usd=money(limit),
        remaining_usd=money(max(limit - committed, Decimal(0))),
        exceeded=committed >= limit,
    )


async def usage_summary(
    session: AsyncSession, user_id: int, settings: Settings, *, days: int, now: datetime | None = None
) -> UsageOut:
    """오늘을 포함한 최근 days일(서울 날짜)의 날별·용도별·모델별 합계와 오늘 한도. 읽기만 한다.

    날별 목록은 기록이 없는 날도 0으로 넣어 늘 days개다. 용도는 UsagePurpose 전부를 늘 보여 준다.
    now: 기준 시각. 라우터는 넘기지 않는다(실제 시각, budget.seoul_day_start).
    """
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
    """날별 합계를 다시 더해 기간 전체 합계를 만든다(쿼리를 한 번 더 하지 않으려고). 비용은 Decimal로 더한다."""
    input_tokens, output_tokens, cost, calls = 0, 0, Decimal(0), 0
    for row in rows:
        input_tokens += int(row[0])
        output_tokens += int(row[1])
        cost += Decimal(row[2] or 0)
        calls += int(row[3])
    return input_tokens, output_tokens, cost, calls
