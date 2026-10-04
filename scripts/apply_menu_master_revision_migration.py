#!/usr/bin/env python3
"""Explicitly apply migration 0027; runtime code never invokes this helper."""

import argparse
import importlib.util
from pathlib import Path
import sys

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
import sqlalchemy as sa


def upgrade_staging(connection) -> None:
    """Read-only prerequisite checks, followed by the canonical 0027 upgrade."""
    if connection.dialect.name != "postgresql":
        raise MigrationBlocked("0027 blocked: staging requires PostgreSQL")
    inspector = sa.inspect(connection)
    required = {
        "menu_masters": {
            "id", "name", "normalized_name", "unit_type", "qty_per_serving", "bag_max_qty",
            "bag_max_unit", "temp_type", "daypart", "category", "condiments", "updated_at",
        },
        "menu_facility_overrides": {"id", "menu_master_id", "facility_id"},
        "monthly_menu_items": {"id", "monthly_menu_id", "name", "daypart", "category", "diet_type",
                               "facility_override", "menu_master_id", "master_resolution_mode",
                               "bag_max_qty", "bag_max_unit"},
        "monthly_menu_entries": {"id", "monthly_menu_id", "facility_override"},
        "monthly_menus": {"id"},
        "users": {"id", "account", "role", "status"},
        "audit_logs": {"id", "actor", "action", "target", "created_at"},
        "user_system_access": {"user_id", "system_key", "enabled"},
    }
    columns = {}
    for table, names in required.items():
        if not inspector.has_table(table):
            raise MigrationBlocked(f"0027 blocked: prior table required: {table}")
        columns[table] = {column["name"]: column for column in inspector.get_columns(table)}
        if not names <= columns[table].keys():
            raise MigrationBlocked(f"0027 blocked: prior columns missing in {table}")
    for table, names in required.items():
        for name in names - {"qty_per_serving", "bag_max_qty", "condiments", "updated_at", "created_at", "enabled"}:
            if not isinstance(columns[table][name]["type"], sa.String):
                raise MigrationBlocked(f"0027 blocked: prior string column required: {table}.{name}")
    for name in ("id", "name", "normalized_name"):
        if columns["menu_masters"][name]["nullable"]:
            raise MigrationBlocked("0027 blocked: menu master identity columns must be NOT NULL")
    if inspector.get_pk_constraint("menu_masters")["constrained_columns"] != ["id"]:
        raise MigrationBlocked("0027 blocked: menu master primary key must be id")
    name_unique = connection.execute(sa.text("""
        SELECT 1 FROM pg_index i
        WHERE i.indrelid = to_regclass('menu_masters')
          AND i.indisunique AND i.indisvalid AND i.indisready AND i.indpred IS NULL
          AND i.indnkeyatts = 1 AND i.indnatts = 1
          AND pg_get_indexdef(i.indexrelid, 1, false) = 'normalized_name'
    """)).first()
    if name_unique is None:
        raise MigrationBlocked("0027 blocked: menu master normalized_name unique index required")
    for name in ("qty_per_serving", "bag_max_qty"):
        if not isinstance(columns["menu_masters"][name]["type"], (sa.Float, sa.Numeric)):
            raise MigrationBlocked("0027 blocked: menu master quantity columns must be numeric")
    if not isinstance(columns["menu_masters"]["condiments"]["type"], sa.JSON):
        raise MigrationBlocked("0027 blocked: menu master condiments must be JSON")
    if not isinstance(columns["menu_masters"]["updated_at"]["type"], sa.DateTime):
        raise MigrationBlocked("0027 blocked: menu master updated_at must be timestamp")
    _require_identity_index(connection)
    _require_access_schema(inspector, columns["user_system_access"])
    upgrade(connection)


def _require_identity_index(connection) -> None:
    index = connection.execute(sa.text("""
        SELECT i.indisunique, i.indisvalid, i.indisready,
               i.indpred IS NULL AS nonpartial, i.indnkeyatts, i.indnatts, am.amname,
               i.indoption::smallint[] AS key_options,
               ARRAY(SELECT pg_get_indexdef(i.indexrelid, n, false)
                     FROM generate_series(1, i.indnkeyatts) AS n ORDER BY n) AS keys
        FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_am am ON am.oid = c.relam
        WHERE i.indrelid = to_regclass('monthly_menu_items')
          AND c.relname = 'uq_monthly_menu_items_scope_identity'
    """)).mappings().one_or_none()
    # PostgreSQL's deparser, not a substring/name check, supplies each complete key.
    expected = ["monthly_menu_id", "name"] + [
        f"COALESCE({name}, ''::character varying)"
        for name in ("daypart", "category", "diet_type", "facility_override")
    ]
    if not index or not (
        index["indisunique"] and index["indisvalid"] and index["indisready"] and index["nonpartial"]
        and index["indnkeyatts"] == index["indnatts"] == 6 and index["amname"] == "btree"
        and list(index["key_options"]) == [0] * 6
        and list(index["keys"]) == expected
    ):
        raise MigrationBlocked(
            "0027 blocked: prior 0023 unique nonpartial six-key NULL-coalescing identity index required; C2 remediation needed"
        )


def _require_access_schema(inspector, columns) -> None:
    required = {"user_id", "system_key", "enabled"}
    if not required <= columns.keys() or any(columns[name]["nullable"] for name in required):
        raise MigrationBlocked("0027 blocked: required access columns must exist and be NOT NULL")
    if not isinstance(columns["enabled"]["type"], sa.Boolean):
        raise MigrationBlocked("0027 blocked: prior 0026 enabled must be boolean")
    if set(inspector.get_pk_constraint("user_system_access")["constrained_columns"]) != {"user_id", "system_key"}:
        raise MigrationBlocked("0027 blocked: prior 0026 access primary key required")


def upgrade(connection) -> None:
    source = Path(__file__).resolve().parents[1] / "backend/migrations/0027_menu_master_revision.py"
    spec = importlib.util.spec_from_file_location("menu_master_revision_migration", source)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    except migration.RevisionSchemaError as exc:
        raise MigrationBlocked(str(exc)) from None


class MigrationBlocked(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-uri-stdin", action="store_true", required=True,
                        help="Read the URI from stdin; never put credentials in argv")
    parser.parse_args()
    engine = None
    try:
        uri = sys.stdin.read().strip()
        if not uri:
            raise MigrationBlocked("0027 blocked: stdin DB URI is empty")
        engine = create_engine(uri, hide_parameters=True)
        with engine.begin() as connection:
            upgrade(connection)
    except MigrationBlocked as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Driver/parser errors can contain credentials; do not print their text or traceback.
        print("0027 failed: connection or migration error; URI and driver details suppressed", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print("0027 complete: revision schema applied or already valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
