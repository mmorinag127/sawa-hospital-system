"""Record original source and execution ownership; never backfill missing proof."""
from alembic import op
import sqlalchemy as sa

revision = "0028_ocr_source_execution"
down_revision = "0027_ocr_delivery_intent"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("implementation_price_ocr_jobs", sa.Column("source_json", sa.JSON(), nullable=True))
    op.add_column("implementation_price_ocr_jobs", sa.Column("result_owner_json", sa.JSON(), nullable=True))
    for name in ("input_digest", "business_attempt_id", "execution_token"):
        op.add_column("implementation_price_ocr_jobs", sa.Column(name, sa.String(), nullable=True))


def downgrade():
    raise RuntimeError("Cannot erase persisted original OCR execution ownership")
