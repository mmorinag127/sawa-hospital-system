"""Add effective date range to facility template versions."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("facility_template_versions", sa.Column("config_json", sa.JSON(), nullable=True))
    op.add_column("facility_template_versions", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("facility_template_versions", sa.Column("valid_to", sa.Date(), nullable=True))
    op.create_index(
        "ix_facility_template_versions_valid_from",
        "facility_template_versions",
        ["valid_from"],
        unique=False,
    )
    op.create_index(
        "ix_facility_template_versions_valid_to",
        "facility_template_versions",
        ["valid_to"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_facility_template_versions_valid_to", table_name="facility_template_versions")
    op.drop_index("ix_facility_template_versions_valid_from", table_name="facility_template_versions")
    op.drop_column("facility_template_versions", "valid_to")
    op.drop_column("facility_template_versions", "valid_from")
    op.drop_column("facility_template_versions", "config_json")
