"""Bound JSON parser memory without changing archived OCR inputs or quantities."""
from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    from src.models.ocr_display_projection_v2 import function_ddl, FUNCTION
    op.execute(function_ddl())
    op.drop_column("ocr_jobs", "display_read_json")
    op.add_column("ocr_jobs", sa.Column("display_read_json", sa.Text(), sa.Computed(
        f"{FUNCTION}(metrics, progress_metrics)", persisted=True)))


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    from src.models.ocr_display_projection_v2 import FUNCTION
    from src.models.ocr_display_projection_v1 import expression
    from src.models.ocr_metrics_v1 import PROGRESS_KEYS
    op.drop_column("ocr_jobs", "display_read_json")
    op.add_column("ocr_jobs", sa.Column("display_read_json", sa.Text(), sa.Computed(
        expression("postgresql", progress_keys=PROGRESS_KEYS), persisted=True)))
    op.execute(f"DROP FUNCTION {FUNCTION}(json, json)")
    op.execute("DROP FUNCTION sawa_json_keys_v1(json, text[])")
