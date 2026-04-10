"""Add durable uploaded pdf pipeline tables."""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uploaded_pdfs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("content_sha256", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False, server_default="manual_upload"),
        sa.Column("original_filename", sa.String(), nullable=False),
        sa.Column("storage_uri", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("facility_hint", sa.String(), nullable=True),
        sa.Column("week_hint", sa.String(), nullable=True),
        sa.Column("facility_name", sa.String(), nullable=True),
        sa.Column("skip_ocr", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("current_stage", sa.String(), nullable=False, server_default="uploaded"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("lease_owner", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(), nullable=True),
        sa.Column("last_error_message", sa.String(), nullable=True),
        sa.Column("alerted_at", sa.DateTime(), nullable=True),
        sa.Column("current_order_id", sa.String(), nullable=True),
        sa.Column("current_document_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_uploaded_pdfs_message_id"),
    )
    op.create_index("ix_uploaded_pdfs_content_sha256", "uploaded_pdfs", ["content_sha256"], unique=False)
    op.create_index("ix_uploaded_pdfs_status", "uploaded_pdfs", ["status"], unique=False)
    op.create_index("ix_uploaded_pdfs_current_stage", "uploaded_pdfs", ["current_stage"], unique=False)
    op.create_index("ix_uploaded_pdfs_lease_expires_at", "uploaded_pdfs", ["lease_expires_at"], unique=False)
    op.create_index("ix_uploaded_pdfs_next_retry_at", "uploaded_pdfs", ["next_retry_at"], unique=False)
    op.create_index("ix_uploaded_pdfs_current_order_id", "uploaded_pdfs", ["current_order_id"], unique=False)
    op.create_index("ix_uploaded_pdfs_current_document_id", "uploaded_pdfs", ["current_document_id"], unique=False)

    op.create_table(
        "uploaded_pdf_attempts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("uploaded_pdf_id", sa.String(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("worker_instance", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uploaded_pdf_id", "attempt_no", name="uq_uploaded_pdf_attempt_scope"),
    )
    op.create_index("ix_uploaded_pdf_attempts_uploaded_pdf_id", "uploaded_pdf_attempts", ["uploaded_pdf_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_uploaded_pdf_attempts_uploaded_pdf_id", table_name="uploaded_pdf_attempts")
    op.drop_table("uploaded_pdf_attempts")

    op.drop_index("ix_uploaded_pdfs_current_document_id", table_name="uploaded_pdfs")
    op.drop_index("ix_uploaded_pdfs_current_order_id", table_name="uploaded_pdfs")
    op.drop_index("ix_uploaded_pdfs_next_retry_at", table_name="uploaded_pdfs")
    op.drop_index("ix_uploaded_pdfs_lease_expires_at", table_name="uploaded_pdfs")
    op.drop_index("ix_uploaded_pdfs_current_stage", table_name="uploaded_pdfs")
    op.drop_index("ix_uploaded_pdfs_status", table_name="uploaded_pdfs")
    op.drop_index("ix_uploaded_pdfs_content_sha256", table_name="uploaded_pdfs")
    op.drop_table("uploaded_pdfs")
