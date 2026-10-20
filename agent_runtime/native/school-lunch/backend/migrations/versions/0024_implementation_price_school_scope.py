"""Add explicit school scope without assigning or splitting existing sources."""
from alembic import op
import sqlalchemy as sa

revision = "0024_school_scope"
down_revision = "0023_portable_process_masters"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("implementation_price_drafts", "implementation_price_ocr_jobs"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("facility_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(f"fk_{table}_facility", "school_lunch_facilities", ["facility_id"], ["id"], ondelete="RESTRICT")
            batch.create_index(f"ix_{table}_facility_id", ["facility_id"])
    with op.batch_alter_table("implementation_price_drafts") as batch:
        batch.add_column(sa.Column("implementation_date", sa.Date(), nullable=True))
        batch.create_index("ix_implementation_price_drafts_implementation_date", ["implementation_date"])


def downgrade():
    for table in ("implementation_price_drafts", "implementation_price_ocr_jobs"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} WHERE facility_id IS NOT NULL LIMIT 1")).first():
            raise RuntimeError("学校IDが保存されているためダウングレードできません")
    with op.batch_alter_table("implementation_price_drafts") as batch:
        batch.drop_index("ix_implementation_price_drafts_implementation_date")
        batch.drop_column("implementation_date")
    for table in ("implementation_price_drafts", "implementation_price_ocr_jobs"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_facility_id")
            batch.drop_constraint(f"fk_{table}_facility", type_="foreignkey")
            batch.drop_column("facility_id")
