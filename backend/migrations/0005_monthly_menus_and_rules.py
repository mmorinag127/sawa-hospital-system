"""Add monthly menus and menu rules tables."""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monthly_menus",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("month_start", sa.Date(), nullable=True),
        sa.Column("filename", sa.String(), nullable=True),
    )
    op.create_table(
        "monthly_menu_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("monthly_menu_id", sa.String(), sa.ForeignKey("monthly_menus.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("unit_type", sa.String(), nullable=True),
        sa.Column("qty_per_serving", sa.Float(), nullable=True),
        sa.Column("temp_type", sa.String(), nullable=True),
        sa.Column("daypart", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("diet_type", sa.String(), nullable=True),
        sa.Column("facility_override", sa.String(), nullable=True),
    )
    op.create_table(
        "monthly_menu_entries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("monthly_menu_id", sa.String(), sa.ForeignKey("monthly_menus.id"), nullable=False),
        sa.Column("menu_date", sa.Date(), nullable=False),
        sa.Column("daypart", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("diet_type", sa.String(), nullable=True),
        sa.Column("slot_index", sa.Integer(), nullable=True),
    )
    op.create_table(
        "menu_rules",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("rule_type", sa.String(), nullable=False),
        sa.Column("match_type", sa.String(), nullable=True),
        sa.Column("menu_pattern", sa.String(), nullable=True),
        sa.Column("facility_id", sa.String(), nullable=True),
        sa.Column("daypart", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("diet_type", sa.String(), nullable=True),
        sa.Column("unit_type", sa.String(), nullable=True),
        sa.Column("qty_per_serving", sa.Float(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("menu_rules")
    op.drop_table("monthly_menu_entries")
    op.drop_table("monthly_menu_items")
    op.drop_table("monthly_menus")
