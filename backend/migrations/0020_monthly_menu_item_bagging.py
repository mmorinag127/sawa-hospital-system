"""Add monthly menu item bagging fields."""

from alembic import op
import sqlalchemy as sa


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("monthly_menu_items", sa.Column("bag_max_qty", sa.Float(), nullable=True))
    op.add_column("monthly_menu_items", sa.Column("bag_max_unit", sa.String(), nullable=True))


def downgrade():
    op.drop_column("monthly_menu_items", "bag_max_unit")
    op.drop_column("monthly_menu_items", "bag_max_qty")
