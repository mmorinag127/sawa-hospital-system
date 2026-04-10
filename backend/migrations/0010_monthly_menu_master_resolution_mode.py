"""Add monthly menu item master resolution mode."""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "monthly_menu_items",
        sa.Column("master_resolution_mode", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("monthly_menu_items", "master_resolution_mode")
