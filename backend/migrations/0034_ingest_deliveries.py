"""Durable original ingest reservations; never backfill inferred ownership."""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if (not inspector.has_table("ingest_jobs") or inspector.has_table("ingest_deliveries")
        or inspector.has_table("ingest_delivery_successors")):
        raise RuntimeError("0034 requires verified original schema and absent delivery table")
    op.create_table("ingest_deliveries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("ingest_jobs.id"), nullable=False),
        sa.Column("business_generation", sa.String(), nullable=False),
        sa.Column("input_digest", sa.String(), nullable=False),
        sa.Column("original_payload", sa.JSON(), nullable=False),
        sa.Column("uploaded_identity", sa.JSON(), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("task_contract", sa.JSON(), nullable=False),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("owner_lease", sa.JSON(), nullable=True),
        sa.Column("handoff_receipt", sa.JSON(), nullable=True),
        sa.Column("terminal_receipt", sa.JSON(), nullable=True),
        sa.Column("visit_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", "business_generation", name="uq_ingest_delivery_original"))
    op.create_index("ix_ingest_deliveries_job_id", "ingest_deliveries", ["job_id"])
    op.create_index("ix_ingest_deliveries_state", "ingest_deliveries", ["state"])
    op.create_table("ingest_delivery_successors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("predecessor_job_id", sa.String(), sa.ForeignKey("ocr_jobs.id"), nullable=False, unique=True),
        sa.Column("parent_delivery_id", sa.String(), sa.ForeignKey("ingest_deliveries.id"), nullable=False),
        sa.Column("job_id", sa.String(), sa.ForeignKey("ocr_jobs.id"), nullable=False, unique=True),
        sa.Column("original", sa.JSON(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("task_contract", sa.JSON(), nullable=False),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("started", sa.Boolean(), nullable=False),
        sa.Column("terminal_receipt", sa.JSON(), nullable=True),
        sa.Column("handoff_receipt", sa.JSON(), nullable=True),
        sa.Column("visit_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_ingest_delivery_successors_state", "ingest_delivery_successors", ["state"])


def downgrade():
    raise RuntimeError("0034 durable original delivery receipts cannot be discarded")
