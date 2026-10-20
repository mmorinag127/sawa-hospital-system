"""Add explicit revisions for skills and staff-skill collections.

Revision ID: f2c1a9e7b4d6
Revises: e6a4c8b9d210
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f2c1a9e7b4d6"
down_revision: str | Sequence[str] | None = "e6a4c8b9d210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("skills") as batch_op:
        batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("staff") as batch_op:
        batch_op.add_column(sa.Column("skills_revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("skills") as batch_op:
        batch_op.alter_column("revision", existing_type=sa.Integer(), server_default=None)
    with op.batch_alter_table("staff") as batch_op:
        batch_op.alter_column("skills_revision", existing_type=sa.Integer(), server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("staff") as batch_op:
        batch_op.drop_column("skills_revision")
    with op.batch_alter_table("skills") as batch_op:
        batch_op.drop_column("revision")
