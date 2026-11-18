"""테이블에서 쓰는 상태 값. 값의 목록은 마스터 프롬프트의 [데이터 모델]과 같다.

BUILD_SPEC 전환 뒤 보강: TaskStatus 12가지(9.2), OperationStatus 12가지(4.2), Outbox(4.4), Notification(8.4),
Reservation(6.2). DB에는 문자열 + CHECK 제약으로 들어간다(base.enum_type, DECISIONS 2026-11-16).

고칠 때 주의(값을 더하거나 빼거나 이름을 바꿀 때):
- CHECK 제약을 바꾸는 Alembic 마이그레이션을 손으로 쓴다(autogenerate가 못 잡는다. base.py 머리 참고).
  이미 저장된 행의 값은 그대로 남으므로 지우는 값은 먼저 다른 값으로 옮긴다.
- TaskStatus·OperationStatus·ApprovalStatus·OutboxStatus·StepStatus는 docs/STATE_MACHINES.md의 "상태 목록"·전이 표와
  tests/test_state_machines.py가 맞춰 본다(개수 12도 확인). 전이를 만드는 함수와 문서를 함께 고친다.
- API 응답으로 나가는 값(task·step·approval 상태 등)을 바꾸면 `pnpm --dir apps/web gen:api` 뒤
  화면의 상태 문구·배지를 고친다.
- 끝나지 않은 상태 목록이 여러 곳에 있다: app/automation/retention.py IN_PROGRESS_STATUSES(대화 삭제 409에도 씀),
  app/agent/history.py UNFINISHED_STATUSES, app/agent/cancel.py. TaskStatus를 더하면 이 목록들을 확인한다.
"""

from enum import StrEnum


# messages.role. 모델 기록(tasks.message_history)과 별개로 화면에 보이는 말의 주인.
class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


# task를 만든 곳. 도구 묶음(toolsets.TOOLSETS_BY_ORIGIN)과 이전 기록 이어받기(chat만)가 이 값으로 갈린다.
#
# 값을 더하면 TOOLSETS_BY_ORIGIN에 항목이 없어 KeyError가 난다. schedule·watch에는 READ 도구만 준다(절대 원칙 7).
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


# 도구 위험 등급(AGENTS.md 도구 규약 1). toolset metadata에서 steps.risk_level로 복사된다(toolsets.risk_level_of).
#
# 복구 때 실행 도중 끊긴 호출은 READ만 다시 실행한다(tracking).
class RiskLevel(StrEnum):
    READ = "READ"
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


# steps.status(도구 호출 한 번). 전이는 docs/STATE_MACHINES.md 6절.
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


# approvals.status. 결정은 approvals.decide가 FOR UPDATE로 한 번만 한다. 전이는 docs/STATE_MACHINES.md 3절.
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


# 감시 에이전트 판정(guard_checks.verdict, guard.GuardVerdict 출력). 승인을 대신하지 않고 카드 배지로만 보인다.
class VerdictKind(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNSURE = "unsure"


# usage_logs·budget_reservations의 용도. 비용 화면(usage_report)이 용도별로 나눠 보여 준다.
#
# 값을 더하면 화면의 용도 이름 표(DECISIONS 2026-11-17 "용도 이름")도 고친다.
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
