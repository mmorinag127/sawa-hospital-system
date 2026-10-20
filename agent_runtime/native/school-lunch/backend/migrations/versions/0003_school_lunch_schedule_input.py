"""Add school lunch schedule input fields.

Revision ID: 0003_school_lunch_schedule_input
Revises: 0002_school_lunch_price_core
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_school_lunch_schedule_input"
down_revision: Union[str, None] = "0002_school_lunch_price_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "school_lunch_menu_assignees",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("menu_execution_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.CheckConstraint("position >= 1", name="ck_school_lunch_menu_assignee_position"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_menu_assignee_name"),
        sa.ForeignKeyConstraint(["menu_execution_id"], ["school_lunch_menu_executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("menu_execution_id", "position", name="uq_school_lunch_menu_assignee_position"),
        sa.UniqueConstraint("menu_execution_id", "name", name="uq_school_lunch_menu_assignee_name"),
    )
    op.create_index(
        op.f("ix_school_lunch_menu_assignees_menu_execution_id"),
        "school_lunch_menu_assignees",
        ["menu_execution_id"],
    )
    with op.batch_alter_table("school_lunch_process_steps") as batch_op:
        batch_op.add_column(sa.Column("scheduled_time", sa.Time(), nullable=True))
        batch_op.add_column(sa.Column("work_zone", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("route_line_style", sa.String(length=16), nullable=True))
        batch_op.create_check_constraint(
            "ck_school_lunch_process_step_work_zone",
            "work_zone IS NULL OR work_zone IN ('contaminated', 'non_contaminated')",
        )
        batch_op.create_check_constraint(
            "ck_school_lunch_process_step_line_style",
            "route_line_style IS NULL OR route_line_style IN ('solid', 'dashed')",
        )


def downgrade() -> None:
    with op.batch_alter_table("school_lunch_process_steps") as batch_op:
        batch_op.drop_constraint("ck_school_lunch_process_step_line_style", type_="check")
        batch_op.drop_constraint("ck_school_lunch_process_step_work_zone", type_="check")
        batch_op.drop_column("route_line_style")
        batch_op.drop_column("work_zone")
        batch_op.drop_column("scheduled_time")
    op.drop_index("ix_school_lunch_menu_assignees_menu_execution_id", table_name="school_lunch_menu_assignees")
    op.drop_table("school_lunch_menu_assignees")
