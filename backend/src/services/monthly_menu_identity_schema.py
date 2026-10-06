"""Canonical schema checks for monthly-menu item identity migration 0023."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from src.monthly_menu_identity import (
    IDENTITY_INDEX,
    LEGACY_INDEX,
    RAW_COLUMNS,
    RAW_CONSTRAINT,
    TABLE,
    identity_index_ddl,
    legacy_index_ddl,
)

class MonthlyMenuIdentitySchemaError(RuntimeError):
    pass


@dataclass(frozen=True)
class IdentityMigrationPlan:
    drop_raw_constraint: bool
    drop_legacy_index: bool
    create_identity_index: bool


def _sqlite_ddl_tokens(sql: str | None) -> tuple[tuple[str, str], ...] | None:
    """Tokenize SQLite DDL without changing quoted literal or identifier content."""
    if not sql:
        return None
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(sql):
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith("--", index) or sql.startswith("/*", index):
            return None
        if char in "'\"":
            quote = char
            index += 1
            value: list[str] = []
            while index < len(sql):
                if sql[index] == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        value.append(quote)
                        index += 2
                        continue
                    index += 1
                    break
                value.append(sql[index])
                index += 1
            else:
                return None
            tokens.append(("literal" if quote == "'" else "identifier", "".join(value)))
            continue
        if char.isalnum() or char == "_":
            end = index + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            tokens.append(("word", sql[index:end].lower()))
            index = end
            continue
        tokens.append(("symbol", char))
        index += 1
    return tuple(tokens)


def _sqlite_ddl_matches(sql: str | None, expected: str) -> bool:
    actual_tokens = _sqlite_ddl_tokens(sql)
    return actual_tokens is not None and actual_tokens == _sqlite_ddl_tokens(expected)


def _duplicate_identity_row(connection) -> object | None:
    return connection.execute(
        sa.text(
            "SELECT monthly_menu_id, name FROM monthly_menu_items "
            "GROUP BY monthly_menu_id, name, COALESCE(daypart, ''), "
            "COALESCE(category, ''), COALESCE(diet_type, ''), "
            "COALESCE(facility_override, '') HAVING count(*) > 1 LIMIT 1"
        )
    ).first()


def _require_no_duplicates(connection) -> None:
    if _duplicate_identity_row(connection) is not None:
        raise MonthlyMenuIdentitySchemaError(
            "0023 blocked: NULL-coalescing monthly-menu identity collision; no DDL was applied"
        )


def _is_empty(connection) -> bool:
    return connection.execute(sa.text("SELECT NOT EXISTS (SELECT 1 FROM monthly_menu_items)")).scalar_one()


def _pg_index(connection, name: str) -> dict | None:
    return connection.execute(
        sa.text(
            "SELECT i.indexrelid AS oid, i.indisunique, i.indisvalid, i.indisready, i.indpred IS NULL AS nonpartial, "
            "i.indnkeyatts, i.indnatts, am.amname, "
            "i.indoption::smallint[] AS key_options, "
            "ARRAY(SELECT pg_get_indexdef(i.indexrelid, n, false) "
            "FROM generate_series(1, i.indnkeyatts) AS n ORDER BY n) AS keys, "
            "ARRAY(SELECT conname FROM pg_constraint con WHERE con.conindid=i.indexrelid ORDER BY conname) AS constraints "
            "FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
            "JOIN pg_am am ON am.oid=c.relam "
            "WHERE i.indrelid=to_regclass('monthly_menu_items') AND c.relname=:name"
        ),
        {"name": name},
    ).mappings().one_or_none()


def _pg_constraint(connection, name: str) -> dict | None:
    return connection.execute(
        sa.text(
            "SELECT c.contype, c.condeferrable, c.condeferred, c.conindid, "
            "ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY u(attnum, ord) "
            "JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=u.attnum ORDER BY u.ord) AS columns "
            "FROM pg_constraint c WHERE c.conrelid=to_regclass('monthly_menu_items') AND c.conname=:name"
        ),
        {"name": name},
    ).mappings().one_or_none()


def _pg_identity_is_exact(index: dict | None) -> bool:
    expected = ["monthly_menu_id", "name"] + [
        f"COALESCE({name}, ''::character varying)"
        for name in ("daypart", "category", "diet_type", "facility_override")
    ]
    return bool(index) and (
        index["indisunique"] and index["indisvalid"] and index["indisready"] and index["nonpartial"]
        and index["indnkeyatts"] == index["indnatts"] == 6 and index["amname"] == "btree"
        and list(index["key_options"]) == [0] * 6 and not list(index["constraints"])
        and list(index["keys"]) == expected
    )


def _pg_legacy_is_exact(index: dict | None) -> bool:
    return bool(index) and (
        index["indisunique"] and index["indisvalid"] and index["indisready"] and index["nonpartial"]
        and index["indnkeyatts"] == index["indnatts"] == 3 and index["amname"] == "btree"
        and list(index["key_options"]) == [0] * 3 and not list(index["constraints"])
        and list(index["keys"]) == ["monthly_menu_id", "name", "COALESCE(facility_override, ''::character varying)"]
    )


def _pg_plan(connection) -> IdentityMigrationPlan:
    raw_constraint = _pg_constraint(connection, RAW_CONSTRAINT)
    raw_index = _pg_index(connection, RAW_CONSTRAINT)
    legacy_index = _pg_index(connection, LEGACY_INDEX)
    identity_index = _pg_index(connection, IDENTITY_INDEX)
    if raw_constraint is not None:
        if not (
            raw_constraint["contype"] == "u"
            and not raw_constraint["condeferrable"]
            and not raw_constraint["condeferred"]
            and list(raw_constraint["columns"]) == list(RAW_COLUMNS)
            and raw_index is not None
            and raw_constraint["conindid"] == raw_index["oid"]
            and list(raw_index["constraints"]) == [RAW_CONSTRAINT]
            and raw_index["indisunique"] and raw_index["indisvalid"] and raw_index["indisready"]
            and raw_index["indnkeyatts"] == raw_index["indnatts"] == 6
            and list(raw_index["key_options"]) == [0] * 6
            and list(raw_index["keys"]) == list(RAW_COLUMNS)
        ):
            raise MonthlyMenuIdentitySchemaError("0023 blocked: unknown uq_monthly_menu_item_scope constraint")
    elif raw_index is not None:
        raise MonthlyMenuIdentitySchemaError("0023 blocked: unknown uq_monthly_menu_item_scope index")
    if legacy_index is not None and not _pg_legacy_is_exact(legacy_index):
        raise MonthlyMenuIdentitySchemaError("0023 blocked: unknown uq_monthly_menu_items_scope_name index")
    if identity_index is not None and not _pg_identity_is_exact(identity_index):
        raise MonthlyMenuIdentitySchemaError("0023 blocked: unknown uq_monthly_menu_items_scope_identity index")
    if identity_index is not None and raw_constraint is None and legacy_index is None:
        return IdentityMigrationPlan(False, False, False)
    if raw_constraint is None and legacy_index is None and identity_index is None and _is_empty(connection):
        return IdentityMigrationPlan(False, False, True)
    if raw_constraint is None and legacy_index is None:
        raise MonthlyMenuIdentitySchemaError("0023 blocked: recognized legacy identity schema is missing")
    _require_no_duplicates(connection)
    return IdentityMigrationPlan(raw_constraint is not None, legacy_index is not None, identity_index is None)


def _sqlite_named_indexes(connection) -> dict[str, str]:
    return {
        row.name: row.sql or ""
        for row in connection.execute(
            sa.text("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='monthly_menu_items'")
        )
    }


def _sqlite_batch_recreate_is_safe(connection) -> None:
    """Refuse SQLite table recreation unless every reflected object is known."""
    extra_indexes = {
        name
        for name, sql in _sqlite_named_indexes(connection).items()
        if sql and name not in {LEGACY_INDEX, IDENTITY_INDEX}
    }
    if extra_indexes:
        raise MonthlyMenuIdentitySchemaError(
            "0023 blocked: SQLite batch recreate would not preserve unknown monthly_menu_items indexes"
        )
    triggers = connection.execute(
        sa.text(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='monthly_menu_items'"
        )
    ).scalars().all()
    if triggers:
        raise MonthlyMenuIdentitySchemaError(
            "0023 blocked: SQLite batch recreate would not preserve monthly_menu_items triggers"
        )
    inspector = sa.inspect(connection)
    unique_constraints = inspector.get_unique_constraints(TABLE)
    if any(
        item.get("name") != RAW_CONSTRAINT or tuple(item.get("column_names") or ()) != RAW_COLUMNS
        for item in unique_constraints
    ):
        raise MonthlyMenuIdentitySchemaError(
            "0023 blocked: SQLite batch recreate would not preserve unknown monthly_menu_items unique constraints"
        )
    if inspector.get_check_constraints(TABLE):
        raise MonthlyMenuIdentitySchemaError(
            "0023 blocked: SQLite batch recreate would not preserve monthly_menu_items check constraints"
        )
    foreign_keys = {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
        )
        for item in inspector.get_foreign_keys(TABLE)
    }
    expected_foreign_keys = {
        (("monthly_menu_id",), "monthly_menus", ("id",)),
        (("menu_master_id",), "menu_masters", ("id",)),
    }
    if foreign_keys != expected_foreign_keys:
        raise MonthlyMenuIdentitySchemaError(
            "0023 blocked: SQLite batch recreate would not preserve unknown monthly_menu_items foreign keys"
        )
    incoming_foreign_keys = [
        (table_name, item.get("name"), tuple(item.get("constrained_columns") or ()))
        for table_name in inspector.get_table_names()
        if table_name != TABLE
        for item in inspector.get_foreign_keys(table_name)
        if item.get("referred_table") == TABLE
    ]
    if incoming_foreign_keys:
        raise MonthlyMenuIdentitySchemaError(
            "0023 blocked: SQLite batch recreate would not preserve incoming monthly_menu_items foreign keys"
        )


def _sqlite_plan(connection) -> IdentityMigrationPlan:
    inspector = sa.inspect(connection)
    constraints = {item.get("name"): item.get("column_names") for item in inspector.get_unique_constraints(TABLE)}
    raw = constraints.get(RAW_CONSTRAINT)
    if raw is not None and tuple(raw) != RAW_COLUMNS:
        raise MonthlyMenuIdentitySchemaError("0023 blocked: unknown uq_monthly_menu_item_scope constraint")
    indexes = _sqlite_named_indexes(connection)
    if RAW_CONSTRAINT in indexes:
        raise MonthlyMenuIdentitySchemaError("0023 blocked: unknown uq_monthly_menu_item_scope index")
    legacy = indexes.get(LEGACY_INDEX)
    identity = indexes.get(IDENTITY_INDEX)
    if legacy is not None and not _sqlite_ddl_matches(legacy, legacy_index_ddl()):
        raise MonthlyMenuIdentitySchemaError("0023 blocked: unknown uq_monthly_menu_items_scope_name index")
    if identity is not None and not _sqlite_ddl_matches(identity, identity_index_ddl()):
        raise MonthlyMenuIdentitySchemaError("0023 blocked: unknown uq_monthly_menu_items_scope_identity index")
    if identity is not None and raw is None and legacy is None:
        return IdentityMigrationPlan(False, False, False)
    if raw is None and legacy is None and identity is None and _is_empty(connection):
        return IdentityMigrationPlan(False, False, True)
    if raw is None and legacy is None:
        raise MonthlyMenuIdentitySchemaError("0023 blocked: recognized legacy identity schema is missing")
    _require_no_duplicates(connection)
    if raw is not None:
        _sqlite_batch_recreate_is_safe(connection)
    # Alembic's SQLite batch reflection does not retain expression indexes.
    # A raw constraint removal recreates the table, so recreate the canonical
    # identity index even when it existed before the batch operation.
    return IdentityMigrationPlan(raw is not None, legacy is not None, identity is None or raw is not None)


def preflight_identity_migration(connection) -> IdentityMigrationPlan:
    if connection.dialect.name == "postgresql":
        return _pg_plan(connection)
    if connection.dialect.name == "sqlite":
        return _sqlite_plan(connection)
    raise MonthlyMenuIdentitySchemaError("0023 blocked: unsupported database dialect")


def preflight_identity_downgrade(connection) -> None:
    collision = connection.execute(
        sa.text(
            "SELECT monthly_menu_id, name FROM monthly_menu_items "
            "GROUP BY monthly_menu_id, name, COALESCE(facility_override, '') HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if collision is not None:
        raise MonthlyMenuIdentitySchemaError(
            "0023 downgrade blocked: three-key legacy identity collision; no DDL was applied"
        )


def require_canonical_identity_schema(connection) -> None:
    if connection.dialect.name == "postgresql":
        if not _pg_identity_is_exact(_pg_index(connection, IDENTITY_INDEX)):
            raise MonthlyMenuIdentitySchemaError(
                "menu schema is not migrated; run migration 0023 before boot: prior 0023 unique nonpartial "
                "six-key NULL-coalescing identity index required; "
                "C2 remediation needed"
            )
        if _pg_constraint(connection, RAW_CONSTRAINT) is not None or _pg_index(connection, LEGACY_INDEX) is not None:
            raise MonthlyMenuIdentitySchemaError("menu schema is not migrated: noncanonical 0023 identity objects remain")
        return
    if connection.dialect.name == "sqlite":
        indexes = _sqlite_named_indexes(connection)
        if not _sqlite_ddl_matches(indexes.get(IDENTITY_INDEX), identity_index_ddl()):
            raise MonthlyMenuIdentitySchemaError(
                "menu schema is not migrated; run migration 0023 before boot: prior 0023 unique nonpartial "
                "six-key NULL-coalescing identity index required; "
                "C2 remediation needed"
            )
        constraints = {item.get("name") for item in sa.inspect(connection).get_unique_constraints(TABLE)}
        if RAW_CONSTRAINT in constraints or LEGACY_INDEX in indexes:
            raise MonthlyMenuIdentitySchemaError("menu schema is not migrated: noncanonical 0023 identity objects remain")
        return
    raise MonthlyMenuIdentitySchemaError("menu schema is not migrated: unsupported database dialect")


def require_legacy_identity_schema(connection) -> None:
    if connection.dialect.name == "postgresql":
        if not _pg_legacy_is_exact(_pg_index(connection, LEGACY_INDEX)):
            raise MonthlyMenuIdentitySchemaError("0023 downgrade failed: exact three-key legacy identity index required")
        if _pg_index(connection, IDENTITY_INDEX) is not None or _pg_constraint(connection, RAW_CONSTRAINT) is not None:
            raise MonthlyMenuIdentitySchemaError("0023 downgrade failed: nonlegacy 0023 identity objects remain")
        return
    if connection.dialect.name == "sqlite":
        indexes = _sqlite_named_indexes(connection)
        if not _sqlite_ddl_matches(indexes.get(LEGACY_INDEX), legacy_index_ddl()):
            raise MonthlyMenuIdentitySchemaError("0023 downgrade failed: exact three-key legacy identity index required")
        constraints = {item.get("name") for item in sa.inspect(connection).get_unique_constraints(TABLE)}
        if IDENTITY_INDEX in indexes or RAW_CONSTRAINT in constraints:
            raise MonthlyMenuIdentitySchemaError("0023 downgrade failed: nonlegacy 0023 identity objects remain")
        return
    raise MonthlyMenuIdentitySchemaError("0023 downgrade failed: unsupported database dialect")
