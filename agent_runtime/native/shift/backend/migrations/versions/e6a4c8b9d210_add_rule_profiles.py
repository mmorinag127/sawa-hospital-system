"""Add reusable rule profiles.

Revision ID: e6a4c8b9d210
Revises: c0f4d1b9a8e7
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e6a4c8b9d210"
down_revision: str | Sequence[str] | None = "c0f4d1b9a8e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("profile_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_rule_profiles_name", "rule_profiles", ["name"], unique=True)
    op.create_index("ix_rule_profiles_enabled", "rule_profiles", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_rule_profiles_enabled", table_name="rule_profiles")
    op.drop_index("ix_rule_profiles_name", table_name="rule_profiles")
    op.drop_table("rule_profiles")
