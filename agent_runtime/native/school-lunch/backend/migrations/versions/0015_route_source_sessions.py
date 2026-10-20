"""Unconfirmed hand-drawn route candidates and immutable source evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0015_route_source_sessions"
down_revision = "0014_school_lunch_source_reviews"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("school_lunch_route_source_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("diagram_id", sa.Integer(), sa.ForeignKey("school_lunch_route_diagrams.id"), nullable=False),
        sa.Column("diagram_version", sa.Integer(), nullable=False),
        sa.Column("layout_id", sa.Integer(), sa.ForeignKey("school_lunch_kitchen_layouts.id"), nullable=False),
        sa.Column("layout_version", sa.Integer(), nullable=False),
        sa.Column("source_versions", sa.JSON(), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("source_pdf", sa.LargeBinary(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("confirmation", sa.JSON(), nullable=True))


def downgrade():
    op.drop_table("school_lunch_route_source_sessions")
