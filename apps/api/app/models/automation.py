"""스스로 도는 작업의 설정: scheduled_jobs(예약 작업), watches(조건 감시), automation_occurrences(발생 기록).

진입점: 만들기·끄기·지우기와 서버 제한은 app/automation/store.py(도구 app/tools/automation.py, 화면 API
app/routers/automations.py), 실행은 1분 검사 app/automation/check.py. 제한 값은 app/automation/rules.py.
근거: BUILD_SPEC 8.1, DECISIONS 2026-11-16(DB 기본값 만료·CHECK 간격), 6단계·6단계 보강.

고칠 때 주의:
- 컬럼을 바꾸면 Alembic 마이그레이션(`uv run python -m alembic revision --autogenerate`)을 만들고 생성 파일을 읽는다.
  화면 API 모양이 바뀌면 `pnpm --dir apps/web gen:api`.
- DB 기본값·CHECK(감시 간격 30분, 만료 30일)는 rules.py의 MIN_WATCH_INTERVAL_MINUTES·WATCH_LIFETIME과 같아야 한다.
  rules.py 머리의 "제한 값을 바꾸면" 목록(도구 설명, 화면 안내, 테스트)도 함께 고친다.
- automation_occurrences는 예정 시각이 보관 기간(7일)보다 오래되면 retention.py가 지운다(docs/DATA_POLICY.md).
- kind·condition_type·status는 enum이 아닌 문자열 컬럼이다(CHECK 없음). 허용 값은 코드(rules.py 등)가 지킨다.
"""

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
    """예약 작업 하나. 실행 때마다 origin=schedule task를 '예약 작업' 대화방에 만들고, READ 도구만으로 돈다.

    next_run_at은 서버가 time_of_day·weekdays·timezone으로 계산한다(rules.next_schedule_run). 1분 검사가 이 값이
    지난 행을 잡을 때 행 잠금과 "next_run_at = 예정 시각" 비교로 같은 실행을 한 번만 시작한다(check.claim_schedule_run).
    """

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
    """조건 감시 하나. task 없이 tool_name(READ, 메일 제외)을 tool_input으로 직접 부르고 조건을 판정한다.

    tool_name·tool_input은 app/automation/watchable.py가 만들 때와 실행 때 다시 검사한다. 조건이 맞으면 알린 뒤
    repeat=false면 끄고 true면 12시간 뒤에 다시 본다(check.finish_watch). 만료되면 1분 검사가 끈다.
    """

    __tablename__ = "watches"
    # 간격 30분 이상은 서버 코드(6단계)에서도 검사하지만, DB에서도 한 번 더 막는다.
    __table_args__ = (CheckConstraint("interval_minutes >= 30", name="interval_at_least_30"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(String(100))
    tool_input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    condition_text: Mapped[str] = mapped_column(Text)
    # 6단계 보강: number(서버가 숫자 비교, condition_spec {field, op, value}) | text(판정 모델).
    # app/automation/conditions.py
    condition_type: Mapped[str] = mapped_column(String(20), server_default="text")
    condition_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 마지막으로 확실히 판정한 결과(unknown은 바꾸지 않는다). 거짓→참으로 바뀔 때만 다시 알린다.
    last_known_met: Mapped[bool | None] = mapped_column(Boolean)
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
