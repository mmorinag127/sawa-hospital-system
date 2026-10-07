"""Durable hospital workflow OCR delivery intents."""
from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("orders") or not inspector.has_table("ocr_jobs"):
        raise RuntimeError("0033 requires orders and ocr_jobs")
    if inspector.has_table("workflow_ocr_deliveries"):
        raise RuntimeError("0033 delivery table already exists; unknown ownership")
    op.create_table(
        "workflow_ocr_deliveries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("job_id", sa.String(), sa.ForeignKey("ocr_jobs.id"), nullable=False),
        sa.Column("workflow_attempt_id", sa.String(), nullable=False),
        sa.Column("workflow_revision", sa.Integer(), nullable=False),
        sa.Column("delivery_generation", sa.Integer(), nullable=False),
        sa.Column("task_name", sa.String(), nullable=False, unique=True),
        sa.Column("task_contract", sa.JSON(), nullable=False),
        sa.Column("identity_sha256", sa.String(), nullable=False),
        sa.Column("visit_count", sa.Integer(), nullable=False),
        sa.Column("dispatch_error", sa.String(), nullable=True),
        sa.Column("handoff_receipt", sa.JSON(), nullable=True),
        sa.Column("result_receipt", sa.JSON(), nullable=True),
        sa.Column("request_mode", sa.String(), nullable=False),
        sa.Column("selected_pdf_receipt", sa.JSON(), nullable=False),
        sa.Column("request_identity", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("transport_receipt", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workflow_attempt_id", name="uq_workflow_ocr_delivery_original_attempt"),
        sa.UniqueConstraint("workflow_attempt_id", "delivery_generation", name="uq_workflow_ocr_delivery_attempt_generation"),
    )
    op.create_index("ix_workflow_ocr_deliveries_order_id", "workflow_ocr_deliveries", ["order_id"])
    op.create_index("ix_workflow_ocr_deliveries_job_id", "workflow_ocr_deliveries", ["job_id"])
    op.create_index("ix_workflow_ocr_deliveries_workflow_attempt_id", "workflow_ocr_deliveries", ["workflow_attempt_id"])
    op.create_index("ix_workflow_ocr_deliveries_state", "workflow_ocr_deliveries", ["state"])
    op.create_index("ix_workflow_ocr_deliveries_claim_token", "workflow_ocr_deliveries", ["claim_token"])
    op.create_index("ix_workflow_ocr_deliveries_lease_expires_at", "workflow_ocr_deliveries", ["lease_expires_at"])
    op.create_index("ix_workflow_ocr_deliveries_updated_at", "workflow_ocr_deliveries", ["updated_at"])


def downgrade():
    raise RuntimeError("0033 delivery intents are durable operational records and cannot be discarded")
