"""Read-only comparison of observed Alembic revision catalog attributes."""

import json
from pathlib import Path

import sqlalchemy as sa
from src.models.monthly_menu_identity import RAW_COLUMNS

from src.services.monthly_menu_identity_schema import (
    IDENTITY_INDEX, LEGACY_INDEX, RAW_CONSTRAINT,
    _sqlite_ddl_tokens, preflight_identity_migration,
    require_canonical_identity_schema,
)

CONTRACT = Path(__file__).resolve().parents[2] / "alembic/catalog_contracts.json"


def _clean(value):
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _ordered(items):
    return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))


def _sqlite_table_definition(sql):
    tokens = _sqlite_ddl_tokens(sql)
    if tokens is None:
        raise RuntimeError("catalog blocked: unknown SQLite table SQL")
    start = next((index for index, token in enumerate(tokens) if token == ("symbol", "(")), None)
    if start is None:
        raise RuntimeError("catalog blocked: missing SQLite table definition")
    parts, current, depth = [], [], 1
    for index in range(start + 1, len(tokens)):
        token = tokens[index]
        if token == ("symbol", "("):
            depth += 1
        elif token == ("symbol", ")"):
            depth -= 1
            if depth == 0:
                parts.append(current)
                return {"declarations": _ordered(_clean(parts)), "suffix": _clean(tokens[index + 1:])}
        if token == ("symbol", ",") and depth == 1:
            parts.append(current)
            current = []
        else:
            current.append(token)
    raise RuntimeError("catalog blocked: incomplete SQLite table definition")


def table_shapes(snapshot, dialect):
    """Normalize only ordering and SQLite SQL tokens; preserve literal values."""
    shapes = {}
    for name, attributes in snapshot["tables"].items():
        attributes = _clean(attributes)
        shapes[name] = {key: _ordered(value) if isinstance(value, list) else value for key, value in attributes.items()}
        native = []
        for item in snapshot["native"]:
            if item.get("tbl_name", item.get("table_name")) != name:
                continue
            item = dict(item)
            if dialect == "sqlite" and item.get("sql") is not None:
                sql = item.pop("sql")
                if item.get("type") == "table":
                    item["table_definition"] = _sqlite_table_definition(sql)
                    native.append(item)
                    continue
                tokens = _sqlite_ddl_tokens(sql)
                if tokens is None:
                    raise RuntimeError(f"unrecognized SQLite SQL tokens: {name}")
                item["tokens"] = _clean(tokens)
            native.append(item)
        shapes[name]["native"] = _ordered(native)
    return shapes


def read_catalog(connection):
    inspector = sa.inspect(connection)
    if connection.dialect.name == "postgresql" and inspector.get_materialized_view_names():
        raise RuntimeError("catalog blocked: unsupported catalog object: materialized view")
    tables = {}
    for name in sorted(inspector.get_table_names()):
        if name == "alembic_version":
            continue
        tables[name] = _clean({
            "columns": inspector.get_columns(name),
            "pk": inspector.get_pk_constraint(name),
            "unique": inspector.get_unique_constraints(name),
            "fk": inspector.get_foreign_keys(name),
            "checks": inspector.get_check_constraints(name),
            "indexes": inspector.get_indexes(name),
        })
    if connection.dialect.name == "sqlite":
        native = list(connection.exec_driver_sql(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "AND tbl_name != 'alembic_version' ORDER BY type,name"
        ).mappings())
    elif connection.dialect.name == "postgresql":
        native = list(connection.exec_driver_sql("""
            SELECT t.relname AS table_name,x.relname AS index_name,
                   pg_get_indexdef(i.indexrelid) AS definition,
                   i.indisunique,i.indisvalid,i.indisready,
                   i.indoption::smallint[] AS key_options,
                   pg_get_expr(i.indpred,i.indrelid) AS predicate,
                   c.conname AS owner,c.contype AS owner_type
            FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            JOIN pg_class x ON x.oid=i.indexrelid
            LEFT JOIN pg_constraint c ON c.conindid=i.indexrelid
            WHERE n.nspname=current_schema() AND t.relname != 'alembic_version'
            ORDER BY t.relname,x.relname
        """).mappings())
    else:
        raise RuntimeError("catalog blocked: unsupported migration dialect")
    return {"tables": tables, "native": _clean([dict(item) for item in native]),
            "views": {name: inspector.get_view_definition(name) for name in inspector.get_view_names()}}


def _without_identity(shape, dialect):
    shape = dict(shape)
    names = {IDENTITY_INDEX, LEGACY_INDEX, RAW_CONSTRAINT}
    shape["unique"] = [item for item in shape["unique"] if item.get("name") not in names]
    shape["indexes"] = [item for item in shape["indexes"] if item.get("name") not in names]
    native = []
    for item in shape["native"]:
        if item.get("name", item.get("index_name")) in names:
            continue
        if dialect == "sqlite" and item.get("type") == "table":
            item = dict(item)
            definition = dict(item["table_definition"])
            recognized_raw = _clean(_sqlite_ddl_tokens(
                f"CONSTRAINT {RAW_CONSTRAINT} UNIQUE ({', '.join(RAW_COLUMNS)})"
            ))
            # Do not erase COLLATE, conflict policies or other unknown additions
            # merely because a constraint has the recognized name and columns.
            definition["declarations"] = [part for part in definition["declarations"]
                if part != recognized_raw]
            item["table_definition"] = definition
        native.append(item)
    shape["native"] = native
    return shape


def require_revision_catalog(connection, revision):
    dialect = connection.dialect.name
    contract = json.loads(CONTRACT.read_text())
    expected = contract["revisions"].get(dialect, {}).get(revision)
    if expected is None:
        raise RuntimeError(f"catalog blocked: revision {revision} has no observed {dialect} contract")
    catalog = read_catalog(connection)
    actual = table_shapes(catalog, dialect)
    if catalog["views"] != expected["views"] or set(actual) != set(expected["tables"]):
        raise RuntimeError(f"catalog is partial or incompatible: revision {revision} table/view set mismatch")
    for name, reference in expected["tables"].items():
        shape = contract["shapes"][reference]
        if actual[name] == shape:
            continue
        # Only pre-0023 identity object variants already covered by root tests.
        if name == "monthly_menu_items" and 15 <= int(revision) <= 22:
            if _without_identity(actual[name], dialect) == _without_identity(shape, dialect):
                preflight_identity_migration(connection)
                continue
        keys = sorted(key for key in shape if shape.get(key) != actual[name].get(key))
        raise RuntimeError(f"catalog blocked: revision {revision} {name} mismatch: {', '.join(keys)}")
    if int(revision) >= 23:
        require_canonical_identity_schema(connection)
