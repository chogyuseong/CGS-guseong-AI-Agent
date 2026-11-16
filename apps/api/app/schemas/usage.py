"""비용 화면(9단계 2번) 응답 모양. 금액은 달러이고 소수 6자리까지(usage_logs.cost_usd NUMERIC(10,6))."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.models.enums import UsagePurpose

USAGE_TIMEZONE = "Asia/Seoul"


class UsageTotals(BaseModel):
    """모델 호출 묶음의 합계. calls는 usage_logs 행 수(모델 응답 또는 감시·판정 호출 한 번이 한 행)."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    calls: int


class UsageDayOut(UsageTotals):
    date: date  # 서울 날짜(하루 비용 상한과 같은 0시 경계)


class UsagePurposeOut(UsageTotals):
    purpose: UsagePurpose  # agent | guard | watch | eval. 기간 안에 없으면 0으로 채워서 항상 4개


class UsageModelOut(UsageTotals):
    model: str  # 모델 응답에 찍힌 이름(예: claude-haiku-5-5)


class UsageTodayOut(BaseModel):
    date: date
    cost_usd: float
    limit_usd: float  # DAILY_COST_LIMIT_USD
    remaining_usd: float  # 한도 - 오늘 비용, 0 아래로 내려가지 않음
    exceeded: bool  # True면 새 작업·예약 실행·감시가 모델을 부르지 않는다


class UsageOut(BaseModel):
    timezone: Literal["Asia/Seoul"]
    days: list[UsageDayOut]  # 오래된 날부터, 사용이 없는 날도 0으로 채움(길이 = 요청한 days)
    by_purpose: list[UsagePurposeOut]
    by_model: list[UsageModelOut]  # 비용이 큰 모델부터
    total: UsageTotals  # 기간 전체
    today: UsageTodayOut
