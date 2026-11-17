"""요청 한 건의 실행 기록: tasks, steps, approvals, guard_checks, usage_logs.

0단계에서는 테이블만 만든다. 이 테이블을 채우는 에이전트·승인 코드는 1~2단계에서 만든다.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable, enum_type
from app.models.enums import (
    ApprovalStatus,
    RiskLevel,
    RunKind,
    StepStatus,
    TaskOrigin,
    TaskStatus,
    UsagePurpose,
    VerdictKind,
)


class Task(TimestampedTable):
    __tablename__ = "tasks"
    # 1단계 보강: 같은 사용자가 같은 client_request_id로 다시 보내면 새 task를 만들지 않는다(BUILD_SPEC 9.1).
    __table_args__ = (UniqueConstraint("user_id", "client_request_id"),)

    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 시작 메시지와 1:1. 예약·감시 작업처럼 메시지 없이 시작할 수 있어 null을 허용한다.
    trigger_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), unique=True)
    origin: Mapped[TaskOrigin] = mapped_column(enum_type(TaskOrigin, "task_origin"))
    # 화면이 메시지마다 만드는 UUID. 채팅 task에만 있고 예약·감시 task는 null(null끼리는 겹쳐도 된다).
    client_request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[TaskStatus] = mapped_column(
        enum_type(TaskStatus, "task_status"), server_default=TaskStatus.QUEUED.value
    )
    workflow_id: Mapped[str | None] = mapped_column(String(200))
    message_history: Mapped[list[Any] | None] = mapped_column(JSONB)
    request_count: Mapped[int] = mapped_column(Integer, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    final_answer: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class AgentRun(TimestampedTable):
    """task의 실행 회차 하나(1단계 보강, BUILD_SPEC 9.1). 처음 실행이 1회차, 승인 뒤 재개마다 1씩 는다.

    claim 단계에서 만들고 finish 단계에서 결과 상태·요청 수·토큰을 채운다. 같은 워크플로가 복구로 claim을 다시 해도
    (task_id, workflow_id)가 같아 행이 하나만 생긴다.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("task_id", "round"), UniqueConstraint("task_id", "workflow_id"))

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    kind: Mapped[RunKind] = mapped_column(enum_type(RunKind, "run_kind"))
    workflow_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[TaskStatus | None] = mapped_column(enum_type(TaskStatus, "task_status"))  # 끝난 뒤 채운다
    request_count: Mapped[int] = mapped_column(Integer, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Step(TimestampedTable):
    """도구 호출 한 번. risk_level은 도구 정의에서 복사해 둔다(나중에 등급이 바뀌어도 기록이 남게)."""

    __tablename__ = "steps"
    __table_args__ = (UniqueConstraint("task_id", "order"),)

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(100))
    tool_call_id: Mapped[str] = mapped_column(String(200))
    risk_level: Mapped[RiskLevel] = mapped_column(enum_type(RiskLevel, "risk_level"))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ok: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[StepStatus] = mapped_column(enum_type(StepStatus, "step_status"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200))


class Approval(TimestampedTable):
    __tablename__ = "approvals"

    step_id: Mapped[int] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"), unique=True)
    tool_call_id: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "approval_status"), server_default=ApprovalStatus.PENDING.value
    )
    summary: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 명세: 만든 시각 + 30분 안에 결정하지 않으면 만료.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now() + interval '30 minutes'")
    )


class GuardCheck(TimestampedTable):
    __tablename__ = "guard_checks"

    step_id: Mapped[int] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"), unique=True)
    verdict: Mapped[VerdictKind] = mapped_column(enum_type(VerdictKind, "verdict_kind"))
    reason: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))


class UsageLog(TimestampedTable):
    __tablename__ = "usage_logs"

    # 하루 비용 상한을 사용자별로 합산한다. task가 없는 사용(감시 판정, eval)도 사용자에게 붙인다.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    purpose: Mapped[UsagePurpose] = mapped_column(enum_type(UsagePurpose, "usage_purpose"))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6))
