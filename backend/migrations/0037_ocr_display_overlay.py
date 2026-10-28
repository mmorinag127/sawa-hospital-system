"""Store the small image reference without reparsing the OCR archive on reads."""
from alembic import op
import sqlalchemy as sa

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    column = sa.Column("display_overlay_json", sa.Text(),
                       sa.Computed("payload_json -> 'hakodate_overlay'", persisted=True))
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("order_ocr_evidence_runs", recreate="always") as batch:
            batch.add_column(column)
    else:
        op.add_column("order_ocr_evidence_runs", column)
    columns = (
        sa.Column("display_current_document_json", sa.Text(), sa.Computed(
            "CASE WHEN metrics ->> 'workflow_attempt_id' IS NULL "
            "THEN metrics -> 'nonworkflow_execution' -> 'orders' -> order_id -> 'order' -> 'current_document_id' "
            "ELSE metrics -> 'reparse_read_input' -> 'order' -> 'current_document_id' END", persisted=True)),
        sa.Column("display_native_template_json", sa.Text(), sa.Computed(
            "metrics -> 'nonworkflow_execution' -> 'orders' -> order_id -> 'order' -> 'template_version_id'", persisted=True)),
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("ocr_jobs", recreate="always") as batch:
            for column in columns:
                batch.add_column(column)
    else:
        for column in columns:
            op.add_column("ocr_jobs", column)


def downgrade():
    op.drop_column("ocr_jobs", "display_native_template_json")
    op.drop_column("ocr_jobs", "display_current_document_json")
    op.drop_column("order_ocr_evidence_runs", "display_overlay_json")
