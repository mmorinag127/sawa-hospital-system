"""Add upload-time order version history."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("storage_uri", sa.String(), nullable=False),
        sa.Column("facility_code", sa.String(), nullable=True),
        sa.Column("week_code", sa.String(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("line_snapshot", sa.JSON(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("order_id", "version_no", name="uq_order_versions_order_version"),
        sa.UniqueConstraint("document_id", name="uq_order_versions_document"),
    )
    op.create_index("ix_order_versions_order_id", "order_versions", ["order_id"])
    op.create_index("ix_order_versions_document_id", "order_versions", ["document_id"])
    op.create_index("ix_order_versions_is_current", "order_versions", ["is_current"])


def downgrade() -> None:
    op.drop_index("ix_order_versions_is_current", table_name="order_versions")
    op.drop_index("ix_order_versions_document_id", table_name="order_versions")
    op.drop_index("ix_order_versions_order_id", table_name="order_versions")
    op.drop_table("order_versions")
