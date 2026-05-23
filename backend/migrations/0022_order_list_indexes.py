"""Add indexes for order list pagination."""

from __future__ import annotations

from alembic import op


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_orders_received_at_id",
        "orders",
        ["received_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_orders_archived_at_received_at_id",
        "orders",
        ["archived_at", "received_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_orders_status_archived_at_received_at_id",
        "orders",
        ["status", "archived_at", "received_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_orders_status_archived_at_received_at_id", table_name="orders")
    op.drop_index("ix_orders_archived_at_received_at_id", table_name="orders")
    op.drop_index("ix_orders_received_at_id", table_name="orders")
