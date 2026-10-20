"""Add transactional revisions for editable planning masters.

Revision ID: a3d7e5c9b1f4
Revises: f2c1a9e7b4d6
"""

from alembic import op
import sqlalchemy as sa

revision = "a3d7e5c9b1f4"
down_revision = "f2c1a9e7b4d6"
branch_labels = None
depends_on = None

_RESOURCES = (
    "staff", "shift_templates", "positions", "constraints", "workdays",
    "demands", "pair_rules", "rotations", "exceptions",
)

def upgrade() -> None:
    for table in (
        "staff", "shift_templates", "positions", "availabilities", "workday_rules",
        "demands", "staff_pair_rules", "rotation_policies", "schedule_exceptions",
    ):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        with op.batch_alter_table(table) as batch:
            batch.alter_column("revision", existing_type=sa.Integer(), server_default=None)
    op.create_table("editor_revisions", sa.Column("resource", sa.String(length=80), primary_key=True), sa.Column("revision", sa.Integer(), nullable=False))
    op.bulk_insert(sa.table("editor_revisions", sa.column("resource", sa.String), sa.column("revision", sa.Integer)), [{"resource": value, "revision": 1} for value in _RESOURCES])
    bind = op.get_bind()
    for table in ("editor_revisions", "staff", "shift_templates", "positions", "availabilities", "workday_rules", "demands", "staff_pair_rules", "rotation_policies", "schedule_exceptions"):
        invalid = bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE revision IS NULL OR revision < 1 LIMIT 1")).first()
        if invalid is not None:
            raise RuntimeError(f"{table} contains an invalid editor revision; repair is forbidden")

def downgrade() -> None:
    op.drop_table("editor_revisions")
    for table in (
        "schedule_exceptions", "rotation_policies", "staff_pair_rules", "demands",
        "workday_rules", "availabilities", "positions", "shift_templates", "staff",
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("revision")
