"""Keep operator source corrections separate from immutable OCR evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0014_school_lunch_source_reviews"
down_revision = "0013_menu_cooking_instructions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "school_lunch_source_reviews",
        sa.Column("job_id", sa.String(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_source_review_version"),
    )


def downgrade():
    op.drop_table("school_lunch_source_reviews")
