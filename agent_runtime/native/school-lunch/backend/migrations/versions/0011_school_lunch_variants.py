"""Record support/allergy instructions and independent source snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0011_school_lunch_variants"
down_revision = "0010_school_lunch_timing"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "school_lunch_menu_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_menu_id", sa.Integer(), sa.ForeignKey("school_lunch_menu_executions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_menu_id", sa.Integer(), sa.ForeignKey("school_lunch_menu_executions.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("instruction", sa.String(2000), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.CheckConstraint("kind IN ('reuse', 'support', 'allergy')", name="ck_menu_variant_kind"),
    )


def downgrade():
    op.drop_table("school_lunch_menu_variants")
