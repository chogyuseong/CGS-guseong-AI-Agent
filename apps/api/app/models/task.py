"""요청 한 건의 실행 기록: tasks, steps, approvals, guard_checks, usage_logs.

0단계에서는 테이블만 만든다. 이 테이블을 채우는 에이전트·승인 코드는 1~2단계에서 만든다.
이력: 1단계 보강 agent_runs·client_request_id·TaskStatus 12가지, 2단계 보강 approvals.plan_id(계획별 승인)와
tasks (id, user_id) 고유(복합 외래 키용).

누가 쓰는가: tasks는 app/routers/conversations.py(메시지 보내기)와 자동화(예약 실행)가 만들고 runner가 상태를 바꾼다.
steps는 tracking.StepTracking(승인 대기 step은 approvals.save_requests), approvals·guard_checks는 approvals,
usage_logs는 runner와 app/automation/check.py(감시 판정).
상태 전이는 docs/STATE_MACHINES.md(누가 바꾸는지까지).

고칠 때 주의:
- 컬럼을 바꾸면 Alembic 마이그레이션(`uv run python -m alembic revision --autogenerate`)을 만들고 생성 파일을 읽는다.
  상태 enum을 바꾸면 docs/STATE_MACHINES.md·tests/test_state_machines.py(models/enums.py 머리 참고).
- 보관: tasks.message_history와 steps.input·output은 끝난 7일 뒤 비운다(app/automation/retention.py). 새 내용 컬럼을
  더하면 retention.py와 docs/DATA_POLICY.md 표에 넣는다. 승인 카드 문장(approvals.summary)은 정리하지 않는다.
- tasks (id, user_id) 고유 제약은 action_plans·outbox_events의 복합 외래 키가 기대므로 지우지 않는다.
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
    """사용자 요청 한 건(채팅 메시지 하나, 또는 예약 실행 한 번)의 실행 단위. 감시 확인은 task를 만들지 않는다.

    message_history: Pydantic AI 메시지 JSON(app/agent/history.py 저장 규칙. 메일 결과는 암호문).
    다음 task가 이어받는다.
    workflow_id: 마지막으로 이 task를 잡은 DBOS 워크플로 ID(task-{id}, task-{id}-resume-N). 재개 번호 계산에 쓴다.
    request_count·total_tokens: 모든 회차 합계(회차별 값은 agent_runs).
    """

    __tablename__ = "tasks"
    # 1단계 보강: 같은 사용자가 같은 client_request_id로 다시 보내면 새 task를 만들지 않는다(BUILD_SPEC 9.1).
    # (id, user_id) 고유: action_plans가 (task_id, user_id) 복합 외래 키로 소유자 일치를 강제한다(2단계 보강).
    __table_args__ = (UniqueConstraint("user_id", "client_request_id"), UniqueConstraint("id", "user_id"))

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
    """도구 호출 한 번. risk_level은 도구 정의에서 복사해 둔다(나중에 등급이 바뀌어도 기록이 남게).

    tracking.StepTracking이 실행 직전에 만들고(승인 대기 EXTERNAL 호출은 approvals.save_requests) 끝나면
    output·status를 채운다. 복구 때 같은 tool_call_id의 행을 찾아 기록된 결과를 재생하거나,
    도중에 끊긴 INTERNAL·EXTERNAL 호출을 unknown으로 닫는다(다시 실행하지 않음).
    order는 task 안의 순서(UNIQUE). 메일 도구의 output에는 내용 없이 통 수만 남긴다(results.output_for_storage).
    """

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
    """실행 계획 하나에 대한 사용자 결정(2단계 보강: 계획·승인 회차별 하나).

    expires_at은 결정 마감(만든 시각 + 30분)이고 계획의 decision_expires_at과 같은 값으로 만든다.
    execute_before는 승인한 시각 + 5분이다. 그 뒤에는 실행하지 않고 다시 승인받는다(BUILD_SPEC 3.4).
    plan_id가 없는 행은 2단계 보강 전에 만든 기록이다.
    """

    __tablename__ = "approvals"

    step_id: Mapped[int] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"), unique=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("action_plans.id", ondelete="CASCADE"), unique=True)
    approval_round: Mapped[int] = mapped_column(Integer, server_default="1")
    # 모델이 만든 값이라 전체에서 유일하다고 보지 않는다(BUILD_SPEC 3.2). 고유성은 plan_id·step_id로 건다.
    tool_call_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "approval_status"), server_default=ApprovalStatus.PENDING.value
    )
    summary: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 명세: 만든 시각 + 30분 안에 결정하지 않으면 만료.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now() + interval '30 minutes'")
    )
    execute_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # '고쳐서 다시'(요구사항 밖, 2026-11-18 사용자 결정): 거절과 함께 사용자가 적은 고칠 내용. 그냥 거절·승인이면 None.
    # 300자는 schemas/tasks.py ReviseRequest와 같다.
    # 모델에게는 거절 결과 문장에 붙어 간다(approvals.denial_message).
    revision_note: Mapped[str | None] = mapped_column(String(300))


class GuardCheck(TimestampedTable):
    """승인 대기 step 하나에 대한 감시 에이전트 판정(app/agent/guard.py). 승인 카드의 배지·이유로만 쓰인다.

    model은 String(100)이라 guard.model_label이 100자로 자른다.
    """

    __tablename__ = "guard_checks"

    step_id: Mapped[int] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"), unique=True)
    verdict: Mapped[VerdictKind] = mapped_column(enum_type(VerdictKind, "verdict_kind"))
    reason: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))


class UsageLog(TimestampedTable):
    """모델 호출 비용 기록(비용 화면, app/usage_report.py). 메시지 내용 없이 계속 보관한다(DATA_POLICY.md "비용 기록").

    하루 한도 판단은 이 표가 아니라 daily_budgets 원장으로 한다(models/budget.py).
    """

    __tablename__ = "usage_logs"

    # 하루 비용 상한을 사용자별로 합산한다. task가 없는 사용(감시 판정, eval)도 사용자에게 붙인다.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    purpose: Mapped[UsagePurpose] = mapped_column(enum_type(UsagePurpose, "usage_purpose"))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6))
