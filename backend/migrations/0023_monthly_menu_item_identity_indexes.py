"""Allow monthly menu items to vary by meal and category."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP INDEX IF EXISTS uq_monthly_menu_item_scope"))
        bind.execute(sa.text("DROP INDEX IF EXISTS uq_monthly_menu_items_scope_name"))
        bind.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_monthly_menu_items_scope_identity
                ON monthly_menu_items(
                  monthly_menu_id,
                  name,
                  COALESCE(daypart, ''),
                  COALESCE(category, ''),
                  COALESCE(diet_type, ''),
                  COALESCE(facility_override, '')
                )
                """
            )
        )
    else:
        bind.execute(sa.text("DROP INDEX IF EXISTS uq_monthly_menu_item_scope"))
        bind.execute(sa.text("DROP INDEX IF EXISTS uq_monthly_menu_items_scope_name"))
        bind.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_monthly_menu_items_scope_identity
                ON monthly_menu_items(
                  monthly_menu_id,
                  name,
                  COALESCE(daypart, ''),
                  COALESCE(category, ''),
                  COALESCE(diet_type, ''),
                  COALESCE(facility_override, '')
                )
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DROP INDEX IF EXISTS uq_monthly_menu_items_scope_identity"))
    bind.execute(sa.text("DROP INDEX IF EXISTS uq_monthly_menu_item_scope"))
    bind.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_monthly_menu_items_scope_name
            ON monthly_menu_items(monthly_menu_id, name, COALESCE(facility_override, ''))
            """
        )
    )
