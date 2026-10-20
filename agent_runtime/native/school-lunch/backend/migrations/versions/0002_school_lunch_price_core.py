"""Create school lunch data used by implementation price calculation.

Revision ID: 0002_school_lunch_price_core
Revises: 0001_price_ocr_jobs
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_school_lunch_price_core"
down_revision: Union[str, None] = "0001_price_ocr_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "school_lunch_facilities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_facility_name"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "school_lunch_ingredients",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("stock_unit", sa.String(length=32), nullable=False),
        sa.Column("order_unit", sa.String(length=32), nullable=False),
        sa.Column("conversion_rate", sa.Numeric(18, 6), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_ingredient_name"),
        sa.CheckConstraint("length(trim(stock_unit)) > 0", name="ck_school_lunch_ingredient_stock_unit"),
        sa.CheckConstraint("length(trim(order_unit)) > 0", name="ck_school_lunch_ingredient_order_unit"),
        sa.CheckConstraint("conversion_rate > 0", name="ck_school_lunch_ingredient_conversion_rate"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "school_lunch_menu_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("facility_id", sa.Integer(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("servings", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_menu_name"),
        sa.CheckConstraint("servings > 0", name="ck_school_lunch_menu_servings"),
        sa.CheckConstraint("version >= 1", name="ck_school_lunch_menu_version"),
        sa.ForeignKeyConstraint(["facility_id"], ["school_lunch_facilities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "service_date", "name", name="uq_school_lunch_menu_identity"),
    )
    op.create_index(op.f("ix_school_lunch_menu_executions_facility_id"), "school_lunch_menu_executions", ["facility_id"])
    op.create_index(op.f("ix_school_lunch_menu_executions_service_date"), "school_lunch_menu_executions", ["service_date"])
    op.create_table(
        "school_lunch_menu_ingredients",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("menu_execution_id", sa.Integer(), nullable=False),
        sa.Column("ingredient_id", sa.Integer(), nullable=False),
        sa.Column("required_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("process_text", sa.String(length=1000), nullable=False),
        sa.Column("route_color", sa.String(length=7), nullable=False),
        sa.CheckConstraint("required_quantity >= 0", name="ck_school_lunch_menu_ingredient_quantity"),
        sa.CheckConstraint("length(trim(unit)) > 0", name="ck_school_lunch_menu_ingredient_unit"),
        sa.ForeignKeyConstraint(["ingredient_id"], ["school_lunch_ingredients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["menu_execution_id"], ["school_lunch_menu_executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("menu_execution_id", "ingredient_id", name="uq_school_lunch_menu_ingredient"),
    )
    op.create_index(op.f("ix_school_lunch_menu_ingredients_ingredient_id"), "school_lunch_menu_ingredients", ["ingredient_id"])
    op.create_index(op.f("ix_school_lunch_menu_ingredients_menu_execution_id"), "school_lunch_menu_ingredients", ["menu_execution_id"])
    op.create_table(
        "school_lunch_process_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("menu_ingredient_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=False),
        sa.Column("operation", sa.String(length=300), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_school_lunch_process_step_sequence"),
        sa.CheckConstraint("length(trim(location)) > 0", name="ck_school_lunch_process_step_location"),
        sa.CheckConstraint("length(trim(operation)) > 0", name="ck_school_lunch_process_step_operation"),
        sa.ForeignKeyConstraint(["menu_ingredient_id"], ["school_lunch_menu_ingredients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("menu_ingredient_id", "sequence", name="uq_school_lunch_process_step_sequence"),
    )
    op.create_index(op.f("ix_school_lunch_process_steps_menu_ingredient_id"), "school_lunch_process_steps", ["menu_ingredient_id"])


def downgrade() -> None:
    op.drop_index("ix_school_lunch_process_steps_menu_ingredient_id", table_name="school_lunch_process_steps")
    op.drop_table("school_lunch_process_steps")
    op.drop_index("ix_school_lunch_menu_ingredients_menu_execution_id", table_name="school_lunch_menu_ingredients")
    op.drop_index("ix_school_lunch_menu_ingredients_ingredient_id", table_name="school_lunch_menu_ingredients")
    op.drop_table("school_lunch_menu_ingredients")
    op.drop_index("ix_school_lunch_menu_executions_service_date", table_name="school_lunch_menu_executions")
    op.drop_index("ix_school_lunch_menu_executions_facility_id", table_name="school_lunch_menu_executions")
    op.drop_table("school_lunch_menu_executions")
    op.drop_table("school_lunch_ingredients")
    op.drop_table("school_lunch_facilities")
