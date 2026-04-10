"""Add order archive columns."""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("archived_by", sa.String(), nullable=True))
    op.create_index("ix_orders_archived_at", "orders", ["archived_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_orders_archived_at", table_name="orders")
    op.drop_column("orders", "archived_by")
    op.drop_column("orders", "archived_at")
