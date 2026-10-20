"""Persist operational delivery review separately from captured business input."""

from alembic import op
import sqlalchemy as sa

revision = "d5e831a7c902"
down_revision = "b8d42e190c73"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("solve_delivery_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.Integer(), sa.ForeignKey("solve_deliveries.id"), nullable=False, unique=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table("solve_delivery_reviews")
