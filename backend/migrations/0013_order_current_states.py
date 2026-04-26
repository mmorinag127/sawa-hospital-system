"""Add persisted order current-state snapshots."""

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_current_states",
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), primary_key=True),
        sa.Column("draft_id", sa.String(), sa.ForeignKey("order_sheet_drafts.id"), nullable=True),
        sa.Column("evidence_run_id", sa.String(), sa.ForeignKey("order_ocr_evidence_runs.id"), nullable=True),
        sa.Column("snapshot_version", sa.String(), nullable=False, server_default="v1"),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("order_current_states")
