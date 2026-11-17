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
    """BUILD_SPEC 9.2의 12가지. 1단계 보강에서 늘렸다. 표시·전이 규칙은 docs/STATE_MACHINES.md(9단계)에 정리한다."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"  # 모델이 사용자에게 되물었다(승인 요청과 다름)
    WAITING_APPROVAL = "waiting_approval"
    CANCEL_REQUESTED = "cancel_requested"  # 실행 중 취소 요청. 다음 모델 호출·도구 실행 직전에 멈춘다
    RECONCILING = "reconciling"  # 결과를 모르는 외부 행동을 다시 조회하는 중(3단계)
    DONE = "done"
    PARTIALLY_DONE = "partially_done"  # 일부 외부 행동만 확인됨(2~3단계)
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"
    NEEDS_REVIEW = "needs_review"  # 결과를 확인할 수 없어 사용자가 봐야 함(3단계)


class RunKind(StrEnum):
    """agent_runs 한 회차가 처음 실행인지, 승인 뒤 재개인지."""

    RUN = "run"
    RESUME = "resume"


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
    # 1단계 보강: 실행했는지 모른다(실행 도중 서버 중단, 쓰기 요청 시간 초과). 실패와 구분한다.
    UNKNOWN = "unknown"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"  # 결정 전에 작업을 취소했다(1단계 보강)


class OperationStatus(StrEnum):
    """외부 작업 하나(operation)의 상태(2단계 보강, BUILD_SPEC 4.2). 전이 표는 docs/STATE_MACHINES.md(9단계).

    succeeded는 영수증(execution_receipts)이 있어야 한다. unknown은 실패가 아니다(실행됐는지 모름).
    """

    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILING = "reconciling"
    REJECTED = "rejected"
    EXPIRED = "expired"
    STALE = "stale"
    CANCELLED = "cancelled"


class OutboxKind(StrEnum):
    """outbox 요청 종류(3단계 보강, BUILD_SPEC 4.4)."""

    RUN = "run_task"  # task 저장 → 실행 워크플로 시작
    RESUME = "resume_task"  # 마지막 승인 결정 → 재개 워크플로 시작


class OutboxStatus(StrEnum):
    PENDING = "pending"  # 아직 시작하지 못했다(처음이거나 실패 뒤 다음 시도 대기)
    DISPATCHED = "dispatched"  # 워크플로를 시작했다(같은 ID라 다시 시작해도 한 번만 돈다)
    FAILED = "failed"  # 여러 번 실패해 더 시도하지 않는다(로그·화면에서 확인)


class NotificationStatus(StrEnum):
    """푸시 한 건(notification_key)의 결과(3단계 보강, BUILD_SPEC 8.4). 전달을 정확히 한 번 보장하지는 않는다."""

    PENDING = "pending"  # 보내는 중(이 키는 이미 잡혔다)
    SENT = "sent"  # 한 기기 이상에 보냄
    FAILED = "failed"  # 구독은 있었지만 모두 실패
    NO_SUBSCRIPTION = "no_subscription"  # 구독한 기기가 없음
    DISABLED = "disabled"  # 서버에 VAPID 설정이 없어 보내지 않음


class VerdictKind(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNSURE = "unsure"


class UsagePurpose(StrEnum):
    AGENT = "agent"
    GUARD = "guard"
    WATCH = "watch"
    EVAL = "eval"


class ReservationStatus(StrEnum):
    """모델 호출 한 번의 예산 예약 상태(1단계 보강, BUILD_SPEC 6.2)."""

    RESERVED = "reserved"  # 호출 전 예상 비용을 잡아 둠
    SETTLED = "settled"  # 응답을 받아 실제 비용으로 정산함
    UNKNOWN = "unknown"  # 호출 결과를 모름(오류·시간 초과). 청구됐을 수 있어 예약액을 풀지 않는다
