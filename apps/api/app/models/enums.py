"""테이블에서 쓰는 상태 값. 값의 목록은 마스터 프롬프트의 [데이터 모델]과 같다."""

from enum import StrEnum


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class TaskOrigin(StrEnum):
    CHAT = "chat"
    SCHEDULE = "schedule"
    WATCH = "watch"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"


class RiskLevel(StrEnum):
    READ = "READ"
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class StepStatus(StrEnum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    FAILED = "failed"
    REJECTED = "rejected"
    # 2단계 추가: 승인을 30분 안에 결정하지 않아 실행하지 않은 EXTERNAL 호출.
    EXPIRED = "expired"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class VerdictKind(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNSURE = "unsure"


class UsagePurpose(StrEnum):
    AGENT = "agent"
    GUARD = "guard"
    WATCH = "watch"
    EVAL = "eval"
