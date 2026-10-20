"""Add daily school lunch work schedules.

Revision ID: 0004_school_lunch_work_schedules
Revises: 0003_school_lunch_schedule_input
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_school_lunch_work_schedules"
down_revision: Union[str, None] = "0003_school_lunch_schedule_input"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "school_lunch_daily_work_schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("facility_id", sa.Integer(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("plating_start", sa.Time(), nullable=False),
        sa.Column("plating_end", sa.Time(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("plating_end > plating_start", name="ck_school_lunch_work_schedule_plating_range"),
        sa.CheckConstraint("length(trim(status)) > 0", name="ck_school_lunch_work_schedule_status"),
        sa.CheckConstraint("version >= 1", name="ck_school_lunch_work_schedule_version"),
        sa.ForeignKeyConstraint(["facility_id"], ["school_lunch_facilities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "service_date", name="uq_school_lunch_work_schedule_identity"),
    )
    op.create_index(op.f("ix_school_lunch_daily_work_schedules_facility_id"), "school_lunch_daily_work_schedules", ["facility_id"])
    op.create_index(op.f("ix_school_lunch_daily_work_schedules_service_date"), "school_lunch_daily_work_schedules", ["service_date"])
    op.create_table(
        "school_lunch_work_schedule_rows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("menu_execution_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("menu_name", sa.String(length=200), nullable=False),
        sa.CheckConstraint("position >= 1", name="ck_school_lunch_work_schedule_row_position"),
        sa.CheckConstraint("length(trim(menu_name)) > 0", name="ck_school_lunch_work_schedule_row_menu_name"),
        sa.ForeignKeyConstraint(["menu_execution_id"], ["school_lunch_menu_executions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["schedule_id"], ["school_lunch_daily_work_schedules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_id", "position", name="uq_school_lunch_work_schedule_row_position"),
    )
    op.create_index(op.f("ix_school_lunch_work_schedule_rows_schedule_id"), "school_lunch_work_schedule_rows", ["schedule_id"])
    op.create_index(op.f("ix_school_lunch_work_schedule_rows_menu_execution_id"), "school_lunch_work_schedule_rows", ["menu_execution_id"])
    op.create_table(
        "school_lunch_work_schedule_assignees",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("row_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.CheckConstraint("position >= 1", name="ck_school_lunch_work_schedule_assignee_position"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_work_schedule_assignee_name"),
        sa.ForeignKeyConstraint(["row_id"], ["school_lunch_work_schedule_rows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("row_id", "position", name="uq_school_lunch_work_schedule_assignee_position"),
        sa.UniqueConstraint("row_id", "name", name="uq_school_lunch_work_schedule_assignee_name"),
    )
    op.create_index(op.f("ix_school_lunch_work_schedule_assignees_row_id"), "school_lunch_work_schedule_assignees", ["row_id"])
    op.create_table(
        "school_lunch_work_schedule_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("row_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("operation", sa.String(length=500), nullable=False),
        sa.Column("work_zone", sa.String(length=32), nullable=False),
        sa.Column("ingredient_name", sa.String(length=200), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_school_lunch_work_schedule_task_sequence"),
        sa.CheckConstraint("end_time > start_time", name="ck_school_lunch_work_schedule_task_time_range"),
        sa.CheckConstraint("length(trim(operation)) > 0", name="ck_school_lunch_work_schedule_task_operation"),
        sa.CheckConstraint("work_zone IN ('contaminated', 'non_contaminated')", name="ck_school_lunch_work_schedule_task_work_zone"),
        sa.ForeignKeyConstraint(["row_id"], ["school_lunch_work_schedule_rows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("row_id", "sequence", name="uq_school_lunch_work_schedule_task_sequence"),
    )
    op.create_index(op.f("ix_school_lunch_work_schedule_tasks_row_id"), "school_lunch_work_schedule_tasks", ["row_id"])


def downgrade() -> None:
    op.drop_index("ix_school_lunch_work_schedule_tasks_row_id", table_name="school_lunch_work_schedule_tasks")
    op.drop_table("school_lunch_work_schedule_tasks")
    op.drop_index("ix_school_lunch_work_schedule_assignees_row_id", table_name="school_lunch_work_schedule_assignees")
    op.drop_table("school_lunch_work_schedule_assignees")
    op.drop_index("ix_school_lunch_work_schedule_rows_menu_execution_id", table_name="school_lunch_work_schedule_rows")
    op.drop_index("ix_school_lunch_work_schedule_rows_schedule_id", table_name="school_lunch_work_schedule_rows")
    op.drop_table("school_lunch_work_schedule_rows")
    op.drop_index("ix_school_lunch_daily_work_schedules_service_date", table_name="school_lunch_daily_work_schedules")
    op.drop_index("ix_school_lunch_daily_work_schedules_facility_id", table_name="school_lunch_daily_work_schedules")
    op.drop_table("school_lunch_daily_work_schedules")
