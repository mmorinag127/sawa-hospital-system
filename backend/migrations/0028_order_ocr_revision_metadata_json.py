"""Align OCR revision metadata with the ORM without synthesizing values."""

from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


class RevisionSchemaError(RuntimeError):
    pass


def require_metadata_column(bind):
    inspector = sa.inspect(bind)
    if not inspector.has_table("order_ocr_revisions"):
        raise RevisionSchemaError("0028 blocked: order_ocr_revisions is missing")
    columns = {column["name"]: column for column in inspector.get_columns("order_ocr_revisions")}
    column = columns.get("metadata_json")
    if column is None:
        return False
    if (column["type"].compile(dialect=bind.dialect).upper() != "JSON"
            or not column["nullable"] or column.get("default") is not None
            or column.get("computed") or column.get("identity")):
        raise RevisionSchemaError("0028 blocked: metadata_json must be JSON NULL without default or generation")
    return True


def upgrade():
    bind = op.get_bind()
    if not require_metadata_column(bind):
        op.add_column("order_ocr_revisions", sa.Column("metadata_json", sa.JSON(), nullable=True))
    require_metadata_column(bind)


def downgrade():
    bind = op.get_bind()
    if not require_metadata_column(bind):
        raise RevisionSchemaError("0028 blocked: metadata_json is missing")
    if bind.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM order_ocr_revisions WHERE metadata_json IS NOT NULL)"
    )).scalar_one():
        raise RevisionSchemaError("0028 downgrade blocked: metadata_json contains non-NULL values")
    op.drop_column("order_ocr_revisions", "metadata_json")
