"""작업(task) 조회 응답. 웹은 이 모양을 OpenAPI에서 타입으로 생성한다."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import ApprovalStatus, RiskLevel, StepStatus, TaskOrigin, TaskStatus


class StepOut(BaseModel):
    """도구 호출 한 번. 타임라인 한 줄."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order: int
    tool_name: str
    label: str
    risk_level: RiskLevel
    status: StepStatus
    ok: bool | None
    input: dict[str, Any]
    output: dict[str, Any] | None
    duration_ms: int | None


class ApprovalOut(BaseModel):
    """승인 대기 카드(2단계에서 채운다). 1단계에서는 항상 빈 배열이다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    step_id: int
    status: ApprovalStatus
    summary: str
    expires_at: datetime


class TaskDetail(BaseModel):
    id: int
    conversation_id: int
    origin: TaskOrigin
    status: TaskStatus
    final_answer: str | None
    error: str | None
    request_count: int
    total_tokens: int
    created_at: datetime
    updated_at: datetime
    steps: list[StepOut]
    approvals: list[ApprovalOut]


class TaskSummary(BaseModel):
    """작업 기록 탭의 한 줄."""

    id: int
    conversation_id: int
    origin: TaskOrigin
    status: TaskStatus
    # 작업을 시작한 사용자 메시지. 예약·감시 작업처럼 메시지 없이 시작하면 null.
    request_text: str | None
    final_answer: str | None
    error: str | None
    step_count: int
    created_at: datetime
    updated_at: datetime
