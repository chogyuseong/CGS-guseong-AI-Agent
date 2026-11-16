"""스스로 도는 작업의 설정: scheduled_jobs(예약 작업), watches(조건 감시)."""

from datetime import datetime, time
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, SmallInteger, String, Text, Time, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable


class ScheduledJob(TimestampedTable):
    __tablename__ = "scheduled_jobs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    prompt_text: Mapped[str] = mapped_column(Text)
    time_of_day: Mapped[time] = mapped_column(Time)
    # 요일 번호 배열. 0=월요일 ... 6=일요일 (Python datetime.weekday()와 같은 규칙).
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    # 6단계 추가: 마지막 실행이 만든 task. 자동화 화면의 "마지막 실행 결과"와, 같은 실행 시각의 task를 두 번 만들지
    # 않는 검사(복구 때 claim 단계가 다시 실행돼도 같은 task를 돌려준다)에 쓴다. task를 지우면 null.
    last_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))


class Watch(TimestampedTable):
    __tablename__ = "watches"
    # 간격 30분 이상은 서버 코드(6단계)에서도 검사하지만, DB에서도 한 번 더 막는다.
    __table_args__ = (CheckConstraint("interval_minutes >= 30", name="interval_at_least_30"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(String(100))
    tool_input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    condition_text: Mapped[str] = mapped_column(Text)
    interval_minutes: Mapped[int] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    repeat: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # 명세: 기본 만료 30일.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now() + interval '30 days'")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
