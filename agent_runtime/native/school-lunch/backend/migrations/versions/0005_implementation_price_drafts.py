"""Persist implementation price drafts.

Revision ID: 0005_price_drafts
Revises: 0004_school_lunch_work_schedules
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_price_drafts"
down_revision: Union[str, None] = "0004_school_lunch_work_schedules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "implementation_price_drafts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_implementation_price_drafts_updated_at",
        "implementation_price_drafts",
        ["updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_implementation_price_drafts_updated_at", table_name="implementation_price_drafts")
    op.drop_table("implementation_price_drafts")
