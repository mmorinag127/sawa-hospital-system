"""Canonical monthly-menu item identity definition shared by ORM and migration checks."""

from __future__ import annotations

import sqlalchemy as sa


TABLE = "monthly_menu_items"
RAW_CONSTRAINT = "uq_monthly_menu_item_scope"
LEGACY_INDEX = "uq_monthly_menu_items_scope_name"
IDENTITY_INDEX = "uq_monthly_menu_items_scope_identity"
RAW_COLUMNS = ("monthly_menu_id", "name", "daypart", "category", "diet_type", "facility_override")
IDENTITY_EXPRESSIONS = (
    "monthly_menu_id",
    "name",
    "COALESCE(daypart, '')",
    "COALESCE(category, '')",
    "COALESCE(diet_type, '')",
    "COALESCE(facility_override, '')",
)
LEGACY_EXPRESSIONS = (
    "monthly_menu_id",
    "name",
    "COALESCE(facility_override, '')",
)


def _create_unique_index_ddl(name: str, expressions: tuple[str, ...]) -> str:
    return f"CREATE UNIQUE INDEX {name} ON {TABLE}({','.join(expressions)})"


def identity_index_ddl() -> str:
    return _create_unique_index_ddl(IDENTITY_INDEX, IDENTITY_EXPRESSIONS)


def legacy_index_ddl() -> str:
    return _create_unique_index_ddl(LEGACY_INDEX, LEGACY_EXPRESSIONS)


def canonical_identity_index() -> sa.Index:
    """Return the ORM schema object for the canonical six-key identity index."""
    return sa.Index(
        IDENTITY_INDEX,
        *(
            expression if position < 2 else sa.text(expression)
            for position, expression in enumerate(IDENTITY_EXPRESSIONS)
        ),
        unique=True,
    )
