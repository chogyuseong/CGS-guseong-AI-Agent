"""외부 실행: action_plans(고정된 실행 계획), operations(실행 상태), execution_receipts(확인된 결과).

2단계 보강(BUILD_SPEC 3.1~3.3, 4.2, 9.1). EXTERNAL 도구 호출 한 건이 승인 대기가 되면
계획 1 : operation 1 : 승인 1이 함께 생기고, 실행이 확인되면 영수증 1이 붙는다.

    action_plans ──1:1── operations ──1:1── execution_receipts
         └──1:1── approvals(plan_id)

- 계획은 만든 뒤 바꾸지 않는다. 실행 인자가 달라지면 새 계획·새 승인이다.
- operation_id(UUID)는 서버가 만든다. 외부 서비스에 보내는 멱등성 키로도 쓴다.
- user_id를 여러 테이블에 두므로, 상위 레코드와 소유자가 다를 수 없게 (id, user_id) 복합 외래 키를 건다
  (BUILD_SPEC 9.1: 모든 조회에 user_id를 붙이는 것만으로 끝내지 않는다).
- connection_id는 외래 키가 아니다. 연결을 지우거나 다시 연결해도 계획에 적힌 값이 남아야
  실행기가 "그 사이 연결이 바뀌었다"를 알아볼 수 있다.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampedTable, enum_type
from app.models.enums import OperationStatus, OutboxKind, OutboxStatus


class ActionPlan(TimestampedTable):
    __tablename__ = "action_plans"
    __table_args__ = (
        # 모델이 만든 tool_call_id는 전체에서 유일하다고 보지 않는다. task·승인 회차 안에서만 고유하다.
        UniqueConstraint("task_id", "approval_round", "tool_call_id"),
        UniqueConstraint("id", "user_id"),
        ForeignKeyConstraint(["task_id", "user_id"], ["tasks.id", "tasks.user_id"], ondelete="CASCADE"),
    )

    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    step_id: Mapped[int] = mapped_column(ForeignKey("steps.id", ondelete="CASCADE"), unique=True)
    approval_round: Mapped[int] = mapped_column(Integer)  # agent_runs.round(이 계획을 요청한 run)
    tool_call_id: Mapped[str] = mapped_column(String(200))
    tool_name: Mapped[str] = mapped_column(String(100))
    tool_version: Mapped[str] = mapped_column(String(20))
    connection_id: Mapped[int | None] = mapped_column(Integer)
    provider_account_reference: Mapped[str | None] = mapped_column(String(200))
    target_resource_id: Mapped[str | None] = mapped_column(String(1024))
    canonical_args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    args_hash: Mapped[str] = mapped_column(String(64))
    target_version: Mapped[str | None] = mapped_column(String(200))  # 4단계: 일정 ETag
    preview: Mapped[str] = mapped_column(Text)
    decision_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    policy_version: Mapped[str] = mapped_column(String(40))


class Operation(TimestampedTable):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        ForeignKeyConstraint(["plan_id", "user_id"], ["action_plans.id", "action_plans.user_id"], ondelete="CASCADE"),
    )

    plan_id: Mapped[int] = mapped_column(Integer, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[OperationStatus] = mapped_column(enum_type(OperationStatus, "operation_status"))
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))


class ExecutionReceipt(TimestampedTable):
    """외부 서비스가 처리했다고 확인한 결과. 화면의 "완료"는 이 행이 있을 때만 보인다(절대 원칙 10)."""

    __tablename__ = "execution_receipts"
    __table_args__ = (
        ForeignKeyConstraint(["operation_id", "user_id"], ["operations.id", "operations.user_id"], ondelete="CASCADE"),
    )

    operation_id: Mapped[int] = mapped_column(Integer, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    external_resource_id: Mapped[str | None] = mapped_column(String(1024))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxEvent(TimestampedTable):
    """워크플로 시작 요청(3단계 보강, BUILD_SPEC 4.4). 그 요청을 만든 DB 변경과 같은 트랜잭션에 저장한다.

    dedupe_key와 workflow_id가 고정이라 dispatcher가 여러 번(동시에도) 처리해도 워크플로는 한 번만 돈다.
    실행 내용(사용자 메시지)은 여기 복사하지 않는다. 시작할 때 task에서 다시 읽는다.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (ForeignKeyConstraint(["task_id", "user_id"], ["tasks.id", "tasks.user_id"], ondelete="CASCADE"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[OutboxKind] = mapped_column(enum_type(OutboxKind, "outbox_kind"))
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    workflow_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[OutboxStatus] = mapped_column(
        enum_type(OutboxStatus, "outbox_status"), server_default=OutboxStatus.PENDING.value, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
