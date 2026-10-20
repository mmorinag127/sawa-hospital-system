"""Keep uploaded originals and their imported menu lineage."""
from alembic import op
import sqlalchemy as sa

revision = "0025_school_lunch_documents"
down_revision = "0024_school_scope"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("school_lunch_documents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("facility_id", sa.Integer(), sa.ForeignKey("school_lunch_facilities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_month", sa.String(7), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("extracted_json", sa.JSON(), nullable=True),
        sa.Column("ocr_job_id", sa.String(), sa.ForeignKey("implementation_price_ocr_jobs.id", ondelete="RESTRICT"), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_school_lunch_documents_facility_id", "school_lunch_documents", ["facility_id"])
    with op.batch_alter_table("school_lunch_menu_executions") as batch:
        batch.add_column(sa.Column("source_document_id", sa.String(32), nullable=True))
        batch.create_foreign_key("fk_menu_source_document", "school_lunch_documents", ["source_document_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_school_lunch_menu_executions_source_document_id", ["source_document_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM school_lunch_documents LIMIT 1")).first():
        raise RuntimeError("保存原本があるため削除を伴うダウングレードはできません")
    with op.batch_alter_table("school_lunch_menu_executions") as batch:
        batch.drop_index("ix_school_lunch_menu_executions_source_document_id")
        batch.drop_constraint("fk_menu_source_document", type_="foreignkey")
        batch.drop_column("source_document_id")
    op.drop_table("school_lunch_documents")
