"""모델 호출 예산(1단계 보강, BUILD_SPEC 6.2): daily_budgets(사용자·날짜별 원장), budget_reservations(호출별 예약).

- daily_budgets 행은 사용자·서울 날짜마다 하나다. 예약할 때 이 행을 FOR UPDATE로 잠가, 동시에 들어온 호출들이
  남은 한도를 함께 보지 못하게 한다. 하루 한도 검사는 spent_usd + reserved_usd로 한다.
- budget_reservations는 모델 호출 한 번마다 하나다. call_key가 같으면 같은 호출(복구 때 다시 실행된 경우)이라
  새로 예약하지 않는다.
- usage_logs는 그대로 "무엇에 얼마를 썼나" 기록(비용 화면)이고, 한도 판단은 이 원장으로만 한다.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable, enum_type
from app.models.enums import ReservationStatus, UsagePurpose

MONEY = Numeric(12, 6)


class DailyBudget(TimestampedTable):
    __tablename__ = "daily_budgets"
    __table_args__ = (UniqueConstraint("user_id", "local_date"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    local_date: Mapped[date] = mapped_column(Date)  # Asia/Seoul 날짜
    spent_usd: Mapped[Decimal] = mapped_column(MONEY, server_default="0")  # 정산된 실제 비용 합계
    reserved_usd: Mapped[Decimal] = mapped_column(MONEY, server_default="0")  # 아직 정산 안 된 예약 합계


class BudgetReservation(TimestampedTable):
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
