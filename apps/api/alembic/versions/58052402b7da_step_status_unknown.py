"""step status unknown

Revision ID: 58052402b7da
Revises: e99a45393f2d
Create Date: 2026-11-17 11:20:11.815175

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "58052402b7da"
down_revision: str | Sequence[str] | None = "e99a45393f2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_steps_step_status"
OLD_VALUES = ("running", "waiting_approval", "done", "failed", "rejected", "expired")
NEW_VALUES = (*OLD_VALUES, "unknown")


def status_check(values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"status IN ({quoted})"


def upgrade() -> None:
    """Upgrade schema."""
    # 1단계 보강: 실행했는지 모르는 단계(unknown). autogenerate는 CHECK 변경을 찾지 못해 직접 쓴다.
    # naming_convention이 이름 앞에 ck_steps_를 또 붙이지 않게 op.f로 완성된 이름이라고 표시한다.
    op.drop_constraint(op.f(CONSTRAINT), "steps", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "steps", status_check(NEW_VALUES))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE steps SET status = 'failed' WHERE status = 'unknown'")
    op.drop_constraint(op.f(CONSTRAINT), "steps", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "steps", status_check(OLD_VALUES))
