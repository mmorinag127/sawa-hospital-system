"""Add paid-leave eligibility to staff.

Revision ID: c0f4d1b9a8e7
Revises: 91b4c8e6f2a1
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c0f4d1b9a8e7"
down_revision: str | Sequence[str] | None = "91b4c8e6f2a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("staff") as batch_op:
        batch_op.add_column(
            sa.Column(
                "paid_leave_eligible",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("staff") as batch_op:
        batch_op.drop_column("paid_leave_eligible")
