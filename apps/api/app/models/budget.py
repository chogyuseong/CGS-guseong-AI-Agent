"""모델 호출 예산(1단계 보강, BUILD_SPEC 6.2): daily_budgets(사용자·날짜별 원장), budget_reservations(호출별 예약).

- daily_budgets 행은 사용자·서울 날짜마다 하나다. 예약할 때 이 행을 FOR UPDATE로 잠가, 동시에 들어온 호출들이
  남은 한도를 함께 보지 못하게 한다. 하루 한도 검사는 spent_usd + reserved_usd로 한다.
- budget_reservations는 모델 호출 한 번마다 하나다. call_key가 같으면 같은 호출(복구 때 다시 실행된 경우)이라
  새로 예약하지 않는다.
- usage_logs는 그대로 "무엇에 얼마를 썼나" 기록(비용 화면)이고, 한도 판단은 이 원장으로만 한다.

진입점: app/agent/budget.py(BudgetCapability가 모델 호출 전 예약·후 정산).
메인·감시·감시 판정 에이전트가 같은 원장을 쓴다.
근거: BUILD_SPEC 6.2, DECISIONS 2026-11-17("하루 비용 상한을 모델 호출마다 예약·정산한다").

고칠 때 주의:
- 컬럼을 바꾸면 Alembic 마이그레이션(`uv run python -m alembic revision --autogenerate`)을 만들고 생성 파일을 읽는다.
- call_key(UNIQUE) 모양은 app/agent/budget.py가 정한다. 바꾸면 복구 때 같은 호출이 두 번 예약된다(budget.py 머리).
- 금액 정밀도(MONEY, 소수 6자리)를 바꾸면 usage_logs.cost_usd(Numeric(10, 6))와 비용 화면 합계도 함께 본다.
- 두 표는 내용 없이 금액만 담아 docs/DATA_POLICY.md 표에 따로 줄이 없다. 보관 정리 대상도 아니다.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable, enum_type
from app.models.enums import ReservationStatus, UsagePurpose

# 달러 금액 칸. 소수 6자리는 usage_logs.cost_usd(Numeric(10, 6))와 맞춘 것이다.
MONEY = Numeric(12, 6)


class DailyBudget(TimestampedTable):
    """사용자·서울 날짜별 원장 한 줄. 예약 때 FOR UPDATE로 잠근다(동시 호출이 남은 한도를 함께 쓰지 못하게)."""

    __tablename__ = "daily_budgets"
    __table_args__ = (UniqueConstraint("user_id", "local_date"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    local_date: Mapped[date] = mapped_column(Date)  # Asia/Seoul 날짜
    spent_usd: Mapped[Decimal] = mapped_column(MONEY, server_default="0")  # 정산된 실제 비용 합계
    reserved_usd: Mapped[Decimal] = mapped_column(MONEY, server_default="0")  # 아직 정산 안 된 예약 합계


class BudgetReservation(TimestampedTable):
    """모델 호출 한 번의 예약. reserved → settled(실제 비용) 또는 unknown(결과 모름, 예약액을 풀지 않음).

    task_id는 task 없는 호출(감시 판정)이면 None이다. task를 지워도 예약 기록은 남는다(SET NULL).
    """

    __tablename__ = "budget_reservations"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    call_key: Mapped[str] = mapped_column(String(200), unique=True)
    local_date: Mapped[date] = mapped_column(Date)
    purpose: Mapped[UsagePurpose] = mapped_column(enum_type(UsagePurpose, "usage_purpose"))
    model: Mapped[str] = mapped_column(String(100))
    estimated_usd: Mapped[Decimal] = mapped_column(MONEY)
    actual_usd: Mapped[Decimal | None] = mapped_column(MONEY)
    status: Mapped[ReservationStatus] = mapped_column(
        enum_type(ReservationStatus, "reservation_status"), server_default=ReservationStatus.RESERVED.value
    )
