"""Add daily output portion override table."""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_output_portion_overrides",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("output_date", sa.Date(), nullable=False),
        sa.Column("facility_id", sa.String(), nullable=False),
        sa.Column("menu_name", sa.String(), nullable=False),
        sa.Column("normalized_menu_name", sa.String(), nullable=False),
        sa.Column("diet_type", sa.String(), nullable=False, server_default=""),
        sa.Column("daypart", sa.String(), nullable=False, server_default=""),
        sa.Column("menu_category", sa.String(), nullable=False, server_default=""),
        sa.Column("unit_type", sa.String(), nullable=False),
        sa.Column("qty_per_serving", sa.Float(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "output_date",
            "facility_id",
            "normalized_menu_name",
            "diet_type",
            "daypart",
            "menu_category",
            name="uq_daily_output_portion_override_scope",
        ),
    )
    op.create_index(
        "ix_daily_output_portion_overrides_output_date",
        "daily_output_portion_overrides",
        ["output_date"],
        unique=False,
    )
    op.create_index(
        "ix_daily_output_portion_overrides_facility_id",
        "daily_output_portion_overrides",
        ["facility_id"],
        unique=False,
    )
    op.create_index(
        "ix_daily_output_portion_overrides_normalized_menu_name",
        "daily_output_portion_overrides",
        ["normalized_menu_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_daily_output_portion_overrides_normalized_menu_name",
        table_name="daily_output_portion_overrides",
    )
    op.drop_index(
        "ix_daily_output_portion_overrides_facility_id",
        table_name="daily_output_portion_overrides",
    )
    op.drop_index(
        "ix_daily_output_portion_overrides_output_date",
        table_name="daily_output_portion_overrides",
    )
    op.drop_table("daily_output_portion_overrides")
