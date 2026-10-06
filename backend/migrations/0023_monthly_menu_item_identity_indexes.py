"""Allow monthly menu items to vary by meal and category."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from src.services.monthly_menu_identity_schema import (
    IDENTITY_INDEX,
    LEGACY_INDEX,
    RAW_CONSTRAINT,
    MonthlyMenuIdentitySchemaError,
    identity_index_ddl,
    legacy_index_ddl,
    preflight_identity_downgrade,
    preflight_identity_migration,
    require_canonical_identity_schema,
    require_legacy_identity_schema,
)


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _ensure_sqlite_physical_transaction(bind) -> None:
    """Make SQLite DDL rollback with Alembic's legacy transaction mode."""
    if bind.dialect.name != "sqlite":
        return
    raw_connection = bind.connection.driver_connection
    if not raw_connection.in_transaction:
        bind.exec_driver_sql("BEGIN")


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_sqlite_physical_transaction(bind)
    try:
        plan = preflight_identity_migration(bind)
    except MonthlyMenuIdentitySchemaError as exc:
        raise RuntimeError(str(exc)) from None
    if bind.dialect.name == "postgresql":
        if plan.drop_raw_constraint:
            bind.execute(sa.text(f"ALTER TABLE monthly_menu_items DROP CONSTRAINT {RAW_CONSTRAINT}"))
        if plan.drop_legacy_index:
            bind.execute(sa.text(f"DROP INDEX {LEGACY_INDEX}"))
    else:
        if plan.drop_legacy_index:
            # Batch recreation does not retain SQLite expression indexes.
            bind.execute(sa.text(f"DROP INDEX {LEGACY_INDEX}"))
        if plan.drop_raw_constraint:
            # SQLite batch recreation may silently omit expression indexes. Remove
            # the recognized canonical index first, then recreate it below.
            bind.execute(sa.text(f"DROP INDEX IF EXISTS {IDENTITY_INDEX}"))
            with op.batch_alter_table("monthly_menu_items", recreate="always") as batch:
                batch.drop_constraint(RAW_CONSTRAINT, type_="unique")
    if plan.create_identity_index:
        bind.execute(sa.text(identity_index_ddl()))
    try:
        require_canonical_identity_schema(bind)
    except MonthlyMenuIdentitySchemaError as exc:
        raise RuntimeError(str(exc)) from None


def _create_legacy_index(bind) -> None:
    bind.execute(sa.text(legacy_index_ddl()))


def downgrade() -> None:
    bind = op.get_bind()
    _ensure_sqlite_physical_transaction(bind)
    try:
        require_canonical_identity_schema(bind)
        preflight_identity_downgrade(bind)
    except MonthlyMenuIdentitySchemaError as exc:
        raise RuntimeError(str(exc)) from None
    bind.execute(sa.text(f"DROP INDEX {IDENTITY_INDEX}"))
    _create_legacy_index(bind)
    try:
        require_legacy_identity_schema(bind)
    except MonthlyMenuIdentitySchemaError as exc:
        raise RuntimeError(str(exc)) from None
