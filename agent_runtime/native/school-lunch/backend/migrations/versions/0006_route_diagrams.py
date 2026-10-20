"""Kitchen layouts and versioned route diagrams."""
from alembic import op
import sqlalchemy as sa
revision = "0006_route_diagrams"
down_revision = "0005_price_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("school_lunch_kitchen_layouts", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("facility_id", sa.Integer(), sa.ForeignKey("school_lunch_facilities.id"), nullable=False), sa.Column("name", sa.String(200), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("image", sa.LargeBinary(), nullable=False), sa.Column("locations", sa.JSON(), nullable=False))
    op.create_table("school_lunch_route_diagrams", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("facility_id", sa.Integer(), sa.ForeignKey("school_lunch_facilities.id"), nullable=False), sa.Column("service_date", sa.Date(), nullable=False), sa.Column("layout_id", sa.Integer(), sa.ForeignKey("school_lunch_kitchen_layouts.id"), nullable=False), sa.Column("layout_version", sa.Integer(), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("source_versions", sa.JSON(), nullable=False), sa.Column("routes", sa.JSON(), nullable=False), sa.UniqueConstraint("facility_id", "service_date"))


def downgrade():
    op.drop_table("school_lunch_route_diagrams")
    op.drop_table("school_lunch_kitchen_layouts")
