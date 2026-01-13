"""Expand schema for ingest/output pipeline."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("current_document_id", sa.String(), nullable=True))
    op.add_column("orders", sa.Column("superseded_document_ids", sa.JSON(), nullable=True))

    op.add_column("order_lines", sa.Column("date", sa.Date(), nullable=True))
    op.add_column("order_lines", sa.Column("daypart", sa.String(), nullable=True))
    op.add_column("order_lines", sa.Column("menu_name", sa.String(), nullable=True))
    op.add_column("order_lines", sa.Column("diet_type", sa.String(), nullable=True))
    op.add_column("order_lines", sa.Column("area_id", sa.String(), nullable=True))
    op.add_column("order_lines", sa.Column("bag_type", sa.String(), nullable=True))
    op.add_column("order_lines", sa.Column("quantity_original", sa.Float(), nullable=True))

    op.create_table(
        "order_documents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("facility_code", sa.String(), nullable=True),
        sa.Column("week_code", sa.String(), nullable=True),
        sa.Column("storage_uri", sa.String(), nullable=False),
        sa.Column("source_email_id", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("ocr_attempts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
    )
    op.create_table(
        "bags",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("daypart", sa.String(), nullable=True),
        sa.Column("menu_name", sa.String(), nullable=True),
        sa.Column("diet_type", sa.String(), nullable=True),
        sa.Column("area_id", sa.String(), nullable=True),
        sa.Column("bag_type", sa.String(), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=False),
    )
    op.create_table(
        "label_rows",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("bag_id", sa.String(), sa.ForeignKey("bags.id"), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "delivery_notes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("facility_code", sa.String(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("file_uri", sa.String(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "manufacturing_aggregate_rows",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("week_code", sa.String(), nullable=False),
        sa.Column("facility_code", sa.String(), nullable=False),
        sa.Column("menu_name", sa.String(), nullable=True),
        sa.Column("diet_type", sa.String(), nullable=True),
        sa.Column("area_id", sa.String(), nullable=True),
        sa.Column("bag_type", sa.String(), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("account", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("fac", sa.String(), nullable=True),
        sa.Column("wek", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("target_role", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("related_entity", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("audit_logs")
    op.drop_table("users")
    op.drop_table("manufacturing_aggregate_rows")
    op.drop_table("delivery_notes")
    op.drop_table("label_rows")
    op.drop_table("bags")
    op.drop_table("order_documents")

    op.drop_column("order_lines", "quantity_original")
    op.drop_column("order_lines", "bag_type")
    op.drop_column("order_lines", "area_id")
    op.drop_column("order_lines", "diet_type")
    op.drop_column("order_lines", "menu_name")
    op.drop_column("order_lines", "daypart")
    op.drop_column("order_lines", "date")

    op.drop_column("orders", "superseded_document_ids")
    op.drop_column("orders", "current_document_id")
