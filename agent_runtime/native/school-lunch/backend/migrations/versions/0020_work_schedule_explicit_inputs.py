"""Preserve unknown zones, per-task ownership and adopted source notes."""
from alembic import op
import sqlalchemy as sa

revision = "0020_schedule_explicit_inputs"
down_revision = "0019_incomplete_schedule_times"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("school_lunch_daily_work_schedules", sa.Column("source_notes", sa.JSON(), nullable=True))
    with op.batch_alter_table("school_lunch_work_schedule_tasks") as batch:
        batch.alter_column("work_zone", existing_type=sa.String(32), nullable=True)
        batch.add_column(sa.Column("assignees", sa.JSON(), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM school_lunch_work_schedule_tasks WHERE work_zone IS NULL OR assignees IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("未確定区分または作業別担当があるためダウングレードできません")
    if op.get_bind().execute(sa.text("SELECT 1 FROM school_lunch_daily_work_schedules WHERE source_notes IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("原本注記があるためダウングレードできません")
    with op.batch_alter_table("school_lunch_work_schedule_tasks") as batch:
        batch.drop_column("assignees")
        batch.alter_column("work_zone", existing_type=sa.String(32), nullable=False)
    op.drop_column("school_lunch_daily_work_schedules", "source_notes")
