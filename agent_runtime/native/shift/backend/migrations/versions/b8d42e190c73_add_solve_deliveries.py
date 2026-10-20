"""Add durable internal solve delivery records."""

from alembic import op
import sqlalchemy as sa

revision = "b8d42e190c73"
down_revision = "a3d7e5c9b1f4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "solve_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("solve_run_id", sa.Integer(), sa.ForeignKey("solve_runs.id"), nullable=False, unique=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("owner_attempt", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("transport_contract", sa.JSON(), nullable=False),
        sa.Column("captured_config", sa.JSON(), nullable=False),
        sa.Column("input_version", sa.String(64), nullable=False),
        sa.Column("read_set", sa.JSON(), nullable=False),
        sa.Column("receipt", sa.JSON(none_as_null=True), nullable=True),
        sa.CheckConstraint("generation >= 1", name="ck_solve_delivery_generation"),
        sa.CheckConstraint("state IN ('pending', 'claimed', 'stopping', 'terminal')", name="ck_solve_delivery_state"),
    )


def downgrade():
    op.drop_table("solve_deliveries")
