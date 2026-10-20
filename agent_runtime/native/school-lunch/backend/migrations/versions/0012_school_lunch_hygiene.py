"""Explicit work phases, kettle uses, and delivery staff; no inferred backfill."""
from alembic import op
import sqlalchemy as sa

revision = "0012_school_lunch_hygiene"
down_revision = "0011_school_lunch_variants"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("school_lunch_work_schedule_rows", sa.Column("menu_version", sa.Integer(), nullable=True))
    # Copy only the already-saved authoritative revision, never today's menu.
    connection = op.get_bind()
    schedules = sa.table("school_lunch_daily_work_schedules", sa.column("id", sa.Integer()), sa.column("source_menu_versions", sa.JSON()))
    rows = sa.table("school_lunch_work_schedule_rows", sa.column("schedule_id", sa.Integer()), sa.column("menu_execution_id", sa.Integer()), sa.column("menu_version", sa.Integer()))
    for saved in connection.execute(sa.select(schedules.c.id, schedules.c.source_menu_versions)).mappings():
        for menu_id, version in (saved["source_menu_versions"] or {}).items():
            connection.execute(rows.update().where(rows.c.schedule_id == saved["id"], rows.c.menu_execution_id == int(menu_id)).values(menu_version=version))
    op.add_column("school_lunch_daily_work_schedules", sa.Column("delivery_assignees", sa.JSON(), nullable=True))
    for name, kind in [("phase", sa.String(20)), ("kettle", sa.String(1)), ("kettle_use", sa.Integer()), ("uses_raw_meat", sa.Boolean())]:
        op.add_column("school_lunch_work_schedule_tasks", sa.Column(name, kind, nullable=True))


def downgrade():
    op.drop_column("school_lunch_work_schedule_rows", "menu_version")
    for name in ["uses_raw_meat", "kettle_use", "kettle", "phase"]:
        op.drop_column("school_lunch_work_schedule_tasks", name)
    op.drop_column("school_lunch_daily_work_schedules", "delivery_assignees")
