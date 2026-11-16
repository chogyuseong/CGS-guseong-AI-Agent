"""작업(task) 조회 응답. 웹은 이 모양을 OpenAPI에서 타입으로 생성한다."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.models.enums import ApprovalStatus, RiskLevel, StepStatus, TaskOrigin, TaskStatus, VerdictKind


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


class GuardOut(BaseModel):
    """감시 에이전트의 판정. 승인 카드의 배지(match=초록, mismatch=빨강+reason, unsure=회색)."""

    verdict: VerdictKind
    reason: str


class ApprovalOut(BaseModel):
    """승인 카드 하나. EXTERNAL 도구 호출 한 번에 하나."""

    id: int
    step_id: int
    tool_name: str
    # 도구 입력값(카드에서 접어 둔다)
    tool_input: dict[str, Any]
    status: ApprovalStatus
    # 실행하면 무엇을 하는지 설명하는 문장(preview)
    summary: str
    expires_at: datetime
    decided_at: datetime | None
    # 감시 결과가 없으면 null(감시 에이전트는 승인 요청과 함께 항상 기록하지만, 옛 데이터를 위해 null 허용)
    guard: GuardOut | None
    # true면 승인 전에 "요청과 다를 수 있습니다. 그래도 진행할까요?"를 한 번 더 묻고,
    # 승인 요청 본문에 confirm_mismatch=true를 넣어야 한다(없으면 409 mismatch_confirmation_required).
    needs_mismatch_confirmation: bool


class ApproveRequest(BaseModel):
    """승인 요청 본문(생략 가능)."""

    # 감시 판정이 mismatch인 승인을 사용자가 한 번 더 확인했다는 표시.
    confirm_mismatch: bool = False


class ApprovalDecisionOut(BaseModel):
    """승인·거절 결과. 이미 결정된 승인에 다시 요청하면 changed=false와 현재 상태를 돌려준다."""

    approval: ApprovalOut
    changed: bool
    # 응답 시점의 작업 상태. 마지막 결정이면 재개 워크플로를 시작만 하고 바로 응답하므로 보통 waiting_approval이나
    # running이다(재개가 끝난 뒤의 상태가 아니다). 화면은 GET /api/tasks/{id} 폴링으로 끝을 확인한다.
    task_status: TaskStatus


type ApprovalErrorCode = Literal["not_found", "task_not_waiting", "approval_expired", "mismatch_confirmation_required"]


class ApprovalErrorDetail(BaseModel):
    # 화면은 문구가 아니라 code로 분기한다.
    code: ApprovalErrorCode
    # 사용자에게 보여 줄 한국어 문장
    message: str


class ApprovalErrorOut(BaseModel):
    """승인·거절 API의 404·409·410 응답 본문."""

    detail: ApprovalErrorDetail


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
    # 이 작업의 모든 승인(대기·승인·거절·만료, step 순서). 승인 카드는 status가 pending인 것만 그린다.
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
