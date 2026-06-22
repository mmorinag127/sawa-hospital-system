"""Add indexes for order line and OCR queue reads."""

from __future__ import annotations

from alembic import op


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_order_lines_order_id", "order_lines", ["order_id"], unique=False)
    op.create_index("ix_ocr_jobs_status", "ocr_jobs", ["status"], unique=False)
    op.create_index("ix_ocr_jobs_updated_at", "ocr_jobs", ["updated_at"], unique=False)
    op.create_index(
        "ix_ocr_jobs_status_updated_at",
        "ocr_jobs",
        ["status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ocr_jobs_status_updated_at", table_name="ocr_jobs")
    op.drop_index("ix_ocr_jobs_updated_at", table_name="ocr_jobs")
    op.drop_index("ix_ocr_jobs_status", table_name="ocr_jobs")
    op.drop_index("ix_order_lines_order_id", table_name="order_lines")
