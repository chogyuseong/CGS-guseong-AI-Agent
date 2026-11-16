"""add expired to step status

2단계: 승인을 30분 안에 결정하지 않아 실행하지 않은 EXTERNAL 호출의 step 상태 'expired'를 더한다.
상태 값은 VARCHAR + CHECK 제약이라(native_enum=False) CHECK 제약만 바꾸면 된다.
Alembic autogenerate는 CHECK 제약의 값 변화를 감지하지 못해 손으로 썼다.
approvals.status에는 처음부터 'expired'가 있다.

downgrade 전에 expired인 step이 있으면 제약을 다시 걸 수 없으므로 failed로 바꾼다.

Revision ID: fdef064b5c87
Revises: 389f1231893e
Create Date: 2026-11-17 01:35:19.215199

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fdef064b5c87"
down_revision: str | Sequence[str] | None = "389f1231893e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_steps_step_status"
OLD_VALUES = ("running", "waiting_approval", "done", "failed", "rejected")
NEW_VALUES = (*OLD_VALUES, "expired")


def status_check(values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({quoted})"


def upgrade() -> None:
    """Upgrade schema."""
    # naming_convention의 ck 규칙이 이름 앞에 ck_steps_를 또 붙이지 않게 op.f로 "이미 완성된 이름"이라고 표시한다.
    op.drop_constraint(op.f(CONSTRAINT), "steps", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "steps", status_check(NEW_VALUES))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE steps SET status = 'failed' WHERE status = 'expired'")
    op.drop_constraint(op.f(CONSTRAINT), "steps", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "steps", status_check(OLD_VALUES))
