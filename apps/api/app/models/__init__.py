"""SQLAlchemy 모델. 여기서 모두 불러와야 Alembic이 모든 테이블을 전부 찾는다.

파일 나눔: base(공통 컬럼·제약 이름 규칙), enums(상태 값), user, chat, task(실행 기록), execution(외부 실행),
budget(예산 원장), automation(예약·감시), integration(외부 연결·푸시), personal(기억·메모·할 일).

고칠 때 주의(어느 모델 파일이든):
- 새 테이블은 여기 import와 __all__에 넣는다. 빠지면 autogenerate가 테이블을 못 보고
  "지우는" 마이그레이션을 만들 수 있다.
- 컬럼·제약을 바꾸면 apps/api에서 `uv run python -m alembic revision --autogenerate -m "설명"` → 생성 파일을 읽고 확인
  → `uv run python -m alembic upgrade head`. API 응답 모양이 바뀌면 `pnpm --dir apps/web gen:api`.
- 새로 저장하는 데이터는 docs/DATA_POLICY.md 표에 넣고, 보관 기간이 있으면 app/automation/retention.py에서 정리한다.
- 상태 enum을 바꾸면 docs/STATE_MACHINES.md와 tests/test_state_machines.py를 같이 본다(models/enums.py 머리 참고).
"""

from app.models.automation import AutomationOccurrence, ScheduledJob, Watch
from app.models.base import Base
from app.models.budget import BudgetReservation, DailyBudget
from app.models.chat import Conversation, Message
from app.models.execution import ActionPlan, ExecutionReceipt, Operation, OutboxEvent
from app.models.integration import Connection, Notification, OAuthState, PushSubscription
from app.models.personal import Experience, Memory, Note, Todo
from app.models.task import AgentRun, Approval, GuardCheck, Step, Task, UsageLog
from app.models.user import User, UserSession

__all__ = [
    "ActionPlan",
    "AgentRun",
    "Approval",
    "AutomationOccurrence",
    "Base",
    "BudgetReservation",
    "Connection",
    "Conversation",
    "DailyBudget",
    "ExecutionReceipt",
    "Experience",
    "GuardCheck",
    "Memory",
    "Message",
    "Note",
    "Notification",
    "OAuthState",
    "Operation",
    "OutboxEvent",
    "PushSubscription",
    "ScheduledJob",
    "Step",
    "Task",
    "Todo",
    "UsageLog",
    "User",
    "UserSession",
    "Watch",
]
