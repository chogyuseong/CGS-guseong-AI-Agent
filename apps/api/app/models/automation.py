"""스스로 도는 작업의 설정: scheduled_jobs(예약 작업), watches(조건 감시)."""

from datetime import datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
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
    # 6단계 보강: 'custom'(채팅으로 만든 예약) | 'briefing'(아침 브리핑, 사용자당 하나. 사용자가 켠 뒤에만 생긴다)
    kind: Mapped[str] = mapped_column(String(20), server_default="custom")
    # 6단계 보강: time_of_day·weekdays를 해석하는 시간대(IANA 이름). 지금은 화면·도구 모두 서울만 만든다.
    timezone: Mapped[str] = mapped_column(String(64), server_default="Asia/Seoul")
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


class AutomationOccurrence(TimestampedTable):
    """6단계 보강: 예약·감시의 발생 한 건(BUILD_SPEC 8.1).

    (kind, source_id, scheduled_for)가 유일해 같은 예정 시각은 한 번만 실행된다.

    status: started(실행함. 예약이면 task_id) | skipped(건너뜀, skip_reason: missed·expired·budget).
    source_id는 scheduled_jobs.id 또는 watches.id다(두 표를 가리켜 FK를 걸지 않는다).
    예약·감시를 지워도 기록은 보관 기간(7일)까지 남는다.
    """

    __tablename__ = "automation_occurrences"
    __table_args__ = (UniqueConstraint("kind", "source_id", "scheduled_for", name="uq_occurrence_source_time"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # schedule | watch
    source_id: Mapped[int] = mapped_column(Integer)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))
    skip_reason: Mapped[str | None] = mapped_column(String(40))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
