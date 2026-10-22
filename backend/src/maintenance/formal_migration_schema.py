"""Read-only comparison of observed Alembic revision catalog attributes."""

import json
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from src.monthly_menu_identity import RAW_COLUMNS

from src.services.monthly_menu_identity_schema import (
    IDENTITY_INDEX, LEGACY_INDEX, RAW_CONSTRAINT,
    _sqlite_ddl_tokens, preflight_identity_migration,
    require_canonical_identity_schema,
)

CONTRACT = Path(__file__).resolve().parents[2] / "alembic/catalog_contracts.json"
ALEMBIC_CONFIG = Path(__file__).resolve().parents[2] / "alembic.ini"


def formal_revision_chain():
    """Return the one linear revision chain declared by the actual Alembic source."""
    scripts = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG)))
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise RuntimeError("formal migration blocked: source must have exactly one head")
    head = scripts.get_revision(heads[0])
    if head is None:
        raise RuntimeError("formal migration blocked: source head is unknown")
    chain = tuple(reversed(tuple(scripts.walk_revisions(base="base", head=head.revision))))
    if not chain or chain[-1].revision != head.revision:
        raise RuntimeError("formal migration blocked: source chain is incomplete")
    for previous, current in zip(chain, chain[1:]):
        if current.down_revision != previous.revision:
            raise RuntimeError("formal migration blocked: source must have one linear chain")
    return tuple(revision.revision for revision in chain)


def formal_head_revision():
    return formal_revision_chain()[-1]


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


def _normalize_reflected_index_aliases(shape, dialect):
    if dialect != "postgresql":
        return shape
    normalized = dict(shape)
    indexes = []
    for original in shape["indexes"]:
        index = dict(original)
        if "include_columns" in index:
            options = index.get("dialect_options")
            if (not isinstance(options, dict) or "postgresql_include" not in options
                    or index["include_columns"] != options["postgresql_include"]):
                raise RuntimeError("catalog blocked: PostgreSQL INCLUDE alias missing canonical value or conflicting")
            del index["include_columns"]
        indexes.append(index)
    normalized["indexes"] = _ordered(indexes)
    return normalized


def _normalize_reflected_catalog_aliases(shape, dialect):
    normalized = _normalize_reflected_index_aliases(shape, dialect)
    if dialect != "postgresql" or "checks" not in normalized:
        return normalized
    # pg_dump/restore can distribute an array cast over its elements. These
    # exact spellings have the same three permitted values, including NULL.
    reviewed = "system_key::text = ANY (ARRAY['hospital'::character varying, 'shift'::character varying, 'school-lunch'::character varying]::text[])"
    restored = "system_key::text = ANY (ARRAY['hospital'::character varying::text, 'shift'::character varying::text, 'school-lunch'::character varying::text])"
    reviewed_native = "CHECK (((system_key)::text = ANY ((ARRAY['hospital'::character varying, 'shift'::character varying, 'school-lunch'::character varying])::text[])))"
    restored_native = "CHECK (((system_key)::text = ANY (ARRAY[('hospital'::character varying)::text, ('shift'::character varying)::text, ('school-lunch'::character varying)::text])))"
    name = "ck_user_system_access_system_key"
    normalized = dict(normalized)
    normalized["checks"] = _ordered([
        {**item, "sqltext": reviewed} if item.get("name") == name and item.get("sqltext") == restored else item
        for item in normalized["checks"]])
    normalized["native"] = _ordered([
        {**item, "definition": reviewed_native}
        if item.get("table_name") == "user_system_access" and item.get("constraint_name") == name
        and item.get("definition") == restored_native else item
        for item in normalized["native"]])
    return normalized


def table_shapes(snapshot, dialect):
    """Normalize ordering, SQLite SQL tokens and verified PG INCLUDE aliases."""
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
        shapes[name] = _normalize_reflected_catalog_aliases(shapes[name], dialect)
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
    names = sorted(inspector.get_table_names())
    if connection.dialect.name == "postgresql" and names:
        # Match the PostgreSQL single-table reflection wrappers' scope/kind.
        options = dict(schema=None, filter_names=names,
                       scope=sa.engine.reflection.ObjectScope.ANY,
                       kind=sa.engine.reflection.ObjectKind.ANY)
        reflected = {
            "columns": inspector.get_multi_columns(**options),
            "pk": inspector.get_multi_pk_constraint(**options),
            "unique": inspector.get_multi_unique_constraints(**options),
            "fk": inspector.get_multi_foreign_keys(**options),
            "checks": inspector.get_multi_check_constraints(**options),
            "indexes": inspector.get_multi_indexes(**options),
        }
        for name in names:
            # Missing reflection entries are failures, never empty constraints.
            tables[name] = _clean({key: values[(None, name)] for key, values in reflected.items()})
    else:
        for name in names:
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


def _named_columns(shape):
    """Apply an explicitly declared PostgreSQL column-identity policy."""
    result = dict(shape)
    result["columns"] = sorted(shape["columns"], key=lambda column: column["name"])
    result["native"] = _ordered([
        {key: value for key, value in item.items()
         if not (key == "ordinal" and "column_name" in item)}
        for item in shape["native"]
    ])
    return result


def column_identity_policy(expected, dialect, revision):
    if int(revision) >= 35 and "column_identity" not in expected:
        raise RuntimeError("catalog blocked: explicit column identity policy required")
    policy = expected.get("column_identity", "position")
    if policy not in {"position", "name"} or (policy == "name" and dialect != "postgresql"):
        raise RuntimeError("catalog blocked: unsupported column identity policy")
    if int(revision) < 35 and policy != "position":
        raise RuntimeError("catalog blocked: historical revision requires physical column order")
    return policy


def require_revision_catalog(connection, revision):
    dialect = connection.dialect.name
    contract = json.loads(CONTRACT.read_text())
    expected = contract["revisions"].get(dialect, {}).get(revision)
    if expected is None:
        raise RuntimeError(f"catalog blocked: revision {revision} has no observed {dialect} contract")
    policy = column_identity_policy(expected, dialect, revision)
    catalog = read_catalog(connection)
    actual = table_shapes(catalog, dialect)
    missing_tables = sorted(set(expected["tables"]) - set(actual))
    if missing_tables:
        raise RuntimeError("catalog is partial or incompatible: missing table " + ", ".join(missing_tables))
    if catalog["views"] != expected["views"] or set(actual) != set(expected["tables"]):
        raise RuntimeError(f"catalog is partial or incompatible: revision {revision} table/view set mismatch")
    for name, reference in expected["tables"].items():
        shape = _normalize_reflected_catalog_aliases(contract["shapes"][reference], dialect)
        if policy == "name":
            shape = _named_columns(shape)
            actual[name] = _named_columns(actual[name])
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
    head = formal_head_revision()
    if revisions != [head]:
        raise RuntimeError(f"runtime blocked: formal migration head {head} is required")
    require_revision_catalog(connection, head)
