"""Monthly shared-menu procurement inputs."""
from alembic import op
import sqlalchemy as sa

revision = "0007_monthly_procurement"
down_revision = "0006_route_diagrams"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "school_lunch_monthly_procurement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("facility_id", sa.Integer(), sa.ForeignKey("school_lunch_facilities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("menu_versions", sa.JSON(), nullable=False),
        sa.UniqueConstraint("facility_id", "month", name="uq_monthly_procurement_period"),
        sa.CheckConstraint("version >= 1", name="ck_monthly_procurement_version"),
    )


def downgrade():
    op.drop_table("school_lunch_monthly_procurement")
