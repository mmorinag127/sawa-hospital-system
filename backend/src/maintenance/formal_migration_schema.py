"""Read-only comparison of observed Alembic revision catalog attributes."""

import json
from pathlib import Path

import sqlalchemy as sa
from src.monthly_menu_identity import RAW_COLUMNS

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
                # Column order is meaningful; only independent table constraints commute.
                constraints, columns = [], []
                for part in parts:
                    first = part[0][1] if part else None
                    (constraints if first in {"constraint", "primary", "unique", "foreign", "check"} else columns).append(part)
                return {"declarations": _clean(columns) + _ordered(_clean(constraints)), "suffix": _clean(tokens[index + 1:])}
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
        shapes[name] = {key: _ordered(value) if key in {"unique", "fk", "checks", "indexes"} else value for key, value in attributes.items()}
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
    if connection.dialect.name == "postgresql":
        unsupported = connection.exec_driver_sql("""
            SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND (
                c.relkind IN ('p','f','S') OR c.relispartition OR c.relrowsecurity OR c.relforcerowsecurity
                OR EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
                OR EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid)
                OR EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)
                OR EXISTS (SELECT 1 FROM pg_rewrite r WHERE r.ev_class=c.oid AND r.rulename != '_RETURN')
            ) ORDER BY c.relname
        """).scalars().all()
        if unsupported:
            raise RuntimeError("catalog blocked: unsupported trigger/RLS/partition/inheritance: " + ", ".join(unsupported))
        types = connection.exec_driver_sql("""
            SELECT t.typname, CASE
                WHEN NOT t.typisdefined THEN 'shell'
                WHEN t.typtype='r' THEN 'range'
                WHEN t.typtype='m' THEN 'multirange'
                WHEN t.typtype='b' THEN 'base'
                WHEN t.typtype='d' THEN 'domain'
                WHEN t.typtype='e' THEN 'enum'
                ELSE 'composite' END AS kind
            FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
            LEFT JOIN pg_class c ON c.oid=t.typrelid
            WHERE n.nspname=current_schema() AND (
                NOT t.typisdefined OR t.typtype IN ('d','e','r','m')
                OR (t.typtype='b' AND t.typelem=0)
                OR (t.typtype='c' AND c.relkind='c'))
            ORDER BY t.typname
        """).all()
        if types:
            raise RuntimeError("catalog blocked: unsupported standalone type: " +
                               ", ".join(f"{name} ({kind})" for name, kind in types))
        routines = connection.exec_driver_sql("""
            SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=current_schema() ORDER BY p.proname
        """).scalars().all()
        if routines:
            raise RuntimeError("catalog blocked: unsupported routine or standalone type")
    tables = {}
    for name in sorted(inspector.get_table_names()):
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
            "ORDER BY type,name"
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
            WHERE n.nspname=current_schema()
            ORDER BY t.relname,x.relname
        """).mappings())
        native += list(connection.exec_driver_sql("""
            SELECT c.relname AS table_name, a.attname AS column_name,a.attnum AS ordinal,
                   format_type(a.atttypid,a.atttypmod) AS native_type,
                   a.attnotnull,a.attidentity,a.attgenerated,
                   cn.nspname AS collation_schema,co.collname AS collation_name
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_collation co ON co.oid=a.attcollation
            LEFT JOIN pg_namespace cn ON cn.oid=co.collnamespace
            WHERE n.nspname=current_schema() AND c.relkind='r'
              AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attnum
        """).mappings())
        native += list(connection.exec_driver_sql("""
            SELECT t.relname AS table_name,c.conname AS constraint_name,
                   c.contype,c.condeferrable,c.condeferred,c.convalidated,
                   c.connoinherit,pg_get_constraintdef(c.oid) AS definition
            FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=current_schema()
            ORDER BY t.relname,c.conname
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
        if item.get("name", item.get("index_name", item.get("constraint_name"))) in names:
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
    missing_tables = sorted(set(expected["tables"]) - set(actual))
    if missing_tables:
        raise RuntimeError("catalog is partial or incompatible: missing table " + ", ".join(missing_tables))
    if catalog["views"] != expected["views"] or set(actual) != set(expected["tables"]):
        raise RuntimeError(f"catalog is partial or incompatible: revision {revision} table/view set mismatch")
    for name, reference in expected["tables"].items():
        shape = contract["shapes"][reference]
        if actual[name] == shape:
            continue
        missing_columns = sorted({item["name"] for item in shape["columns"]} - {item["name"] for item in actual[name]["columns"]})
        if missing_columns:
            raise RuntimeError(f"catalog blocked: missing columns in {name}: {', '.join(missing_columns)}")
        # Only pre-0023 identity object variants already covered by root tests.
        if name == "monthly_menu_items" and 15 <= int(revision) <= 22:
            if _without_identity(actual[name], dialect) == _without_identity(shape, dialect):
                preflight_identity_migration(connection)
                continue
        missing_indexes = sorted({item["name"] for item in shape["indexes"]} - {item["name"] for item in actual[name]["indexes"]})
        if missing_indexes:
            raise RuntimeError(f"catalog blocked: missing indexes in {name}: {', '.join(missing_indexes)}")
        keys = sorted(key for key in shape if shape.get(key) != actual[name].get(key))
        raise RuntimeError(f"catalog blocked: revision {revision} {name} mismatch: {', '.join(keys)}")
    if int(revision) >= 23:
        require_canonical_identity_schema(connection)


def require_formal_head(connection):
    inspector = sa.inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        raise RuntimeError("runtime blocked: formal migration head is required; automatic adoption is disabled")
    revisions = list(connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars())
    if revisions != ["0028"]:
        raise RuntimeError("runtime blocked: formal migration head 0028 is required")
    require_revision_catalog(connection, "0028")
