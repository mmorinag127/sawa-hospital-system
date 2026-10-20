"""Add a configurable output display name to shift templates.

Revision ID: 91b4c8e6f2a1
Revises: 4d18c6f28a92
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "91b4c8e6f2a1"
down_revision: str | Sequence[str] | None = "4d18c6f28a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("shift_templates") as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.String(length=120), nullable=True))
    with op.batch_alter_table("positions") as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.String(length=120), nullable=True))
    shift_templates = sa.table(
        "shift_templates",
        sa.column("name", sa.String(length=120)),
        sa.column("display_name", sa.String(length=120)),
    )
    legacy_names = {
        "PDF日勤": "通常勤務",
        "PDF5時": "5時勤務",
        "PDF6時": "6時勤務",
        "PDF半日": "半日勤務",
    }
    for internal_name, display_name in legacy_names.items():
        op.execute(
            shift_templates.update()
            .where(shift_templates.c.name == internal_name)
            .values(display_name=display_name)
        )


def downgrade() -> None:
    with op.batch_alter_table("positions") as batch_op:
        batch_op.drop_column("display_name")
    with op.batch_alter_table("shift_templates") as batch_op:
        batch_op.drop_column("display_name")
