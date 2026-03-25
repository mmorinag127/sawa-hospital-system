"""Add ship_date to shipping tracking logs."""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shipping_tracking_logs", sa.Column("ship_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("shipping_tracking_logs", "ship_date")
