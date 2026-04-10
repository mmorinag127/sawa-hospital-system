"""Add current/event tables for shipping tracking."""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shipping_tracking_current",
        sa.Column("tracking_key", sa.String(), nullable=False),
        sa.Column("tracking_number", sa.String(), nullable=False),
        sa.Column("ship_date", sa.Date(), nullable=True),
        sa.Column("facility_name", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("arrival_text", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("looked_up_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("tracking_key"),
    )
    op.create_index(
        "ix_shipping_tracking_current_ship_date",
        "shipping_tracking_current",
        ["ship_date"],
        unique=False,
    )
    op.create_index(
        "ix_shipping_tracking_current_looked_up_at",
        "shipping_tracking_current",
        ["looked_up_at"],
        unique=False,
    )

    op.create_table(
        "shipping_tracking_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tracking_key", sa.String(), nullable=False),
        sa.Column("tracking_number", sa.String(), nullable=False),
        sa.Column("event_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_status", sa.String(), nullable=False),
        sa.Column("event_at_text", sa.String(), nullable=True),
        sa.Column("event_at", sa.DateTime(), nullable=True),
        sa.Column("office_name", sa.String(), nullable=True),
        sa.Column("looked_up_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_shipping_tracking_events_tracking_key",
        "shipping_tracking_events",
        ["tracking_key"],
        unique=False,
    )
    op.create_index(
        "ix_shipping_tracking_events_event_at",
        "shipping_tracking_events",
        ["event_at"],
        unique=False,
    )
    op.create_index(
        "ix_shipping_tracking_events_looked_up_at",
        "shipping_tracking_events",
        ["looked_up_at"],
        unique=False,
    )
    op.create_index(
        "ix_shipping_tracking_events_tracking_key_order",
        "shipping_tracking_events",
        ["tracking_key", "event_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_shipping_tracking_events_tracking_key_order", table_name="shipping_tracking_events")
    op.drop_index("ix_shipping_tracking_events_looked_up_at", table_name="shipping_tracking_events")
    op.drop_index("ix_shipping_tracking_events_event_at", table_name="shipping_tracking_events")
    op.drop_index("ix_shipping_tracking_events_tracking_key", table_name="shipping_tracking_events")
    op.drop_table("shipping_tracking_events")

    op.drop_index("ix_shipping_tracking_current_looked_up_at", table_name="shipping_tracking_current")
    op.drop_index("ix_shipping_tracking_current_ship_date", table_name="shipping_tracking_current")
    op.drop_table("shipping_tracking_current")
