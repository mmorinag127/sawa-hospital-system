"""Add explicit OCR job and evidence lineage constraints."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {str(column.get("name") or "") for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {str(index.get("name") or "") for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _fk_names(table_name: str) -> set[str]:
    return {str(fk.get("name") or "") for fk in sa.inspect(op.get_bind()).get_foreign_keys(table_name)}


def _demote_legacy_evidence() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE order_ocr_evidence_runs
            SET status = 'repair_blocked'
            WHERE source = 'legacy-cache-backfill'
              AND COALESCE(status, '') <> 'repair_blocked'
            """
        )
    )


def _demote_duplicate_current_evidence() -> None:
    op.get_bind().execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY order_id, template_version_id, artifact_digest
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM order_ocr_evidence_runs
                WHERE template_version_id IS NOT NULL
                  AND COALESCE(status, '') <> 'repair_blocked'
            )
            UPDATE order_ocr_evidence_runs
            SET status = 'repair_blocked'
            WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
            """
        )
    )


def upgrade() -> None:
    columns = _column_names("ocr_jobs")
    if "order_id" not in columns:
        op.add_column("ocr_jobs", sa.Column("order_id", sa.String(), nullable=True))
    if "uploaded_pdf_id" not in columns:
        op.add_column("ocr_jobs", sa.Column("uploaded_pdf_id", sa.String(), nullable=True))
    if "order_document_id" not in columns:
        op.add_column("ocr_jobs", sa.Column("order_document_id", sa.String(), nullable=True))
    if "input_artifact_digest" not in columns:
        op.add_column("ocr_jobs", sa.Column("input_artifact_digest", sa.String(), nullable=True))

    indexes = _index_names("ocr_jobs")
    for index_name, column_name in (
        ("ix_ocr_jobs_order_id", "order_id"),
        ("ix_ocr_jobs_uploaded_pdf_id", "uploaded_pdf_id"),
        ("ix_ocr_jobs_order_document_id", "order_document_id"),
        ("ix_ocr_jobs_input_artifact_digest", "input_artifact_digest"),
    ):
        if index_name not in indexes:
            op.create_index(index_name, "ocr_jobs", [column_name], unique=False)

    if op.get_bind().dialect.name != "sqlite":
        fks = _fk_names("ocr_jobs")
        if "fk_ocr_jobs_order_id" not in fks:
            op.create_foreign_key("fk_ocr_jobs_order_id", "ocr_jobs", "orders", ["order_id"], ["id"])
        if "fk_ocr_jobs_uploaded_pdf_id" not in fks:
            op.create_foreign_key("fk_ocr_jobs_uploaded_pdf_id", "ocr_jobs", "uploaded_pdfs", ["uploaded_pdf_id"], ["id"])
        if "fk_ocr_jobs_order_document_id" not in fks:
            op.create_foreign_key(
                "fk_ocr_jobs_order_document_id",
                "ocr_jobs",
                "order_documents",
                ["order_document_id"],
                ["id"],
            )
        uploaded_pdf_fks = _fk_names("uploaded_pdfs")
        if "fk_uploaded_pdfs_current_order_id" not in uploaded_pdf_fks:
            op.create_foreign_key(
                "fk_uploaded_pdfs_current_order_id",
                "uploaded_pdfs",
                "orders",
                ["current_order_id"],
                ["id"],
            )
        if "fk_uploaded_pdfs_current_document_id" not in uploaded_pdf_fks:
            op.create_foreign_key(
                "fk_uploaded_pdfs_current_document_id",
                "uploaded_pdfs",
                "order_documents",
                ["current_document_id"],
                ["id"],
            )

    _demote_legacy_evidence()
    _demote_duplicate_current_evidence()

    evidence_indexes = _index_names("order_ocr_evidence_runs")
    if "uq_order_ocr_evidence_runs_current_identity" not in evidence_indexes:
        op.get_bind().execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_order_ocr_evidence_runs_current_identity
                ON order_ocr_evidence_runs(order_id, template_version_id, artifact_digest)
                WHERE template_version_id IS NOT NULL
                  AND COALESCE(status, '') <> 'repair_blocked'
                """
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        uploaded_pdf_fks = _fk_names("uploaded_pdfs")
        if "fk_uploaded_pdfs_current_document_id" in uploaded_pdf_fks:
            op.drop_constraint("fk_uploaded_pdfs_current_document_id", "uploaded_pdfs", type_="foreignkey")
        if "fk_uploaded_pdfs_current_order_id" in uploaded_pdf_fks:
            op.drop_constraint("fk_uploaded_pdfs_current_order_id", "uploaded_pdfs", type_="foreignkey")
        ocr_job_fks = _fk_names("ocr_jobs")
        if "fk_ocr_jobs_order_document_id" in ocr_job_fks:
            op.drop_constraint("fk_ocr_jobs_order_document_id", "ocr_jobs", type_="foreignkey")
        if "fk_ocr_jobs_uploaded_pdf_id" in ocr_job_fks:
            op.drop_constraint("fk_ocr_jobs_uploaded_pdf_id", "ocr_jobs", type_="foreignkey")
        if "fk_ocr_jobs_order_id" in ocr_job_fks:
            op.drop_constraint("fk_ocr_jobs_order_id", "ocr_jobs", type_="foreignkey")

    indexes = _index_names("order_ocr_evidence_runs")
    if "uq_order_ocr_evidence_runs_current_identity" in indexes:
        op.drop_index("uq_order_ocr_evidence_runs_current_identity", table_name="order_ocr_evidence_runs")

    indexes = _index_names("ocr_jobs")
    for index_name in (
        "ix_ocr_jobs_input_artifact_digest",
        "ix_ocr_jobs_order_document_id",
        "ix_ocr_jobs_uploaded_pdf_id",
        "ix_ocr_jobs_order_id",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="ocr_jobs")

    columns = _column_names("ocr_jobs")
    for column_name in ("input_artifact_digest", "order_document_id", "uploaded_pdf_id", "order_id"):
        if column_name in columns:
            op.drop_column("ocr_jobs", column_name)
