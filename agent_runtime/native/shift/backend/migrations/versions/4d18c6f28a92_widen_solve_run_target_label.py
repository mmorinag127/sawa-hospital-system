"""Widen solve run target label for arbitrary date ranges.

Revision ID: 4d18c6f28a92
Revises: 788d8e3b1f0e
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "4d18c6f28a92"
down_revision: str | Sequence[str] | None = "788d8e3b1f0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("solve_runs") as batch_op:
        batch_op.alter_column(
            "target_month",
            existing_type=sa.String(length=7),
            type_=sa.String(length=128),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("solve_runs") as batch_op:
        batch_op.alter_column(
            "target_month",
            existing_type=sa.String(length=128),
            type_=sa.String(length=7),
            existing_nullable=False,
        )
