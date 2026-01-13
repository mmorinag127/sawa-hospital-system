"""Initial schema."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "facilities",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
    )
    op.create_table(
        "facility_areas",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("facility_id", sa.String(), sa.ForeignKey("facilities.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
    )
    op.create_table(
        "facility_configs",
        sa.Column("facility_id", sa.String(), sa.ForeignKey("facilities.id"), primary_key=True),
        sa.Column("config_json", sa.JSON(), nullable=True),
    )
    op.create_table(
        "weekly_menus",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("week_start", sa.Date(), nullable=True),
        sa.Column("filename", sa.String(), nullable=True),
    )
    op.create_table(
        "menu_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("weekly_menu_id", sa.String(), sa.ForeignKey("weekly_menus.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("unit_type", sa.String(), nullable=True),
        sa.Column("qty_per_serving", sa.Float(), nullable=True),
        sa.Column("temp_type", sa.String(), nullable=True),
        sa.Column("daypart", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("facility_override", sa.String(), nullable=True),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("facility_code", sa.String(), nullable=True),
        sa.Column("week_code", sa.String(), nullable=True),
        sa.Column("status", sa.String(), default="要確認"),
        sa.Column("document_uri", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "order_lines",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("line_id", sa.String(), nullable=True),
        sa.Column("quantity_corrected", sa.Float(), nullable=True),
        sa.Column("change_note", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("order_lines")
    op.drop_table("orders")
    op.drop_table("menu_items")
    op.drop_table("weekly_menus")
    op.drop_table("facility_configs")
    op.drop_table("facility_areas")
    op.drop_table("facilities")
