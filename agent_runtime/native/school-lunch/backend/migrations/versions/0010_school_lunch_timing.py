"""Operator-defined timing profiles; existing schedules retain null metadata."""
from alembic import op
import sqlalchemy as sa

revision = "0010_school_lunch_timing"
down_revision = "0009_school_lunch_staff"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "school_lunch_timing_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("facility_id", sa.Integer(), sa.ForeignKey("school_lunch_facilities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("plating_start", sa.Time(), nullable=False),
        sa.Column("plating_end", sa.Time(), nullable=False),
        sa.Column("timeline_ticks", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("facility_id", "name", name="uq_lunch_timing_profile_name"),
        sa.CheckConstraint("version >= 1", name="ck_lunch_timing_version"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_lunch_timing_name"),
        sa.CheckConstraint("plating_end > plating_start", name="ck_lunch_timing_plating"),
    )
    for name in ("timing_profile_id", "timing_profile_version"):
        op.add_column("school_lunch_daily_work_schedules", sa.Column(name, sa.Integer(), nullable=True))
    op.add_column("school_lunch_daily_work_schedules", sa.Column("timeline_ticks", sa.JSON(), nullable=True))
    for name in ("duration_minutes", "offset_before_plating_minutes"):
        op.add_column("school_lunch_work_schedule_tasks", sa.Column(name, sa.Integer(), nullable=True))


def downgrade():
    for name in ("duration_minutes", "offset_before_plating_minutes"):
        op.drop_column("school_lunch_work_schedule_tasks", name)
    for name in ("timing_profile_id", "timing_profile_version", "timeline_ticks"):
        op.drop_column("school_lunch_daily_work_schedules", name)
    op.drop_table("school_lunch_timing_profiles")
