"""Formal Alembic migration and readonly runtime catalog verification."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
from pathlib import Path
import tempfile

from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError


MIGRATION_LOCK_KEY = 1_764_068
ALEMBIC_VERSION_TABLE = "alembic_version"


class SchemaContractError(RuntimeError):
    """The selected database is not a verified formal schema target."""


class ConcurrentMigrationError(SchemaContractError):
    """Another formal migration owns the database migration lock."""


# Comparison hashes derived from preserved, fresh native Alembic observations.
# Provenance: docs/modernization/v1-shift-catalog-20261007.md.
CATALOG_HASHES: dict[str, dict[str, str | None]] = {
    "sqlite": {
        "788d8e3b1f0e": "12fb65e87813658d974df61938efa888d5ab44bb756dc5e02150784181f1f790",
        "4d18c6f28a92": "d0513fce25d8683972ed7d7dd052ea53cb2ac7395a094293b5fd60a35d75e593",
        "91b4c8e6f2a1": "eb9d98a2e68dfd0e533740608914ea763ba1230d9159c4258cbd88727c5c636f",
        "c0f4d1b9a8e7": "eb9b90da89b8e85e86089c6184c5737d5a3bd57b848b063ffd0db60e701abe19",
        "e6a4c8b9d210": "898006cf93a4bc3b9e252d9cfb98e978130601f16737c3daedc3364524280c31",
        "f2c1a9e7b4d6": "fc254a4c378a383b05be45d7439995bc5855ebb7f086dab22841b5987149a297",
        "a3d7e5c9b1f4": "579555c464754c28067702cbe709b2fb8a6b131799886f1fa2a5bfebd843cb2a",
        "b8d42e190c73": "7988b73da3b9c5d30ddb4bafd18c8e2aa1f96059b2134b0607a03a724e9cd326",
        "d5e831a7c902": "ac3a4d6f7a1f3f2cfc1bada77009fd54a783c0df46b1baf2a167b754adffb694",
        "f7c31a9e204b": "16ef38eccac6da6d7736aee5fbf3de2c98b986761c6d9f360f07dc2f9f5af700",
        "c6a9e2f8b104": "16ef38eccac6da6d7736aee5fbf3de2c98b986761c6d9f360f07dc2f9f5af700",
    },
    "postgresql": {
        "788d8e3b1f0e": "ceff3dae1cf7dc1407ceb0c52a36268cc99efe271e70a92050c769013cde8f40",
        "4d18c6f28a92": "bc733bff10fc2b4689006547b5a0a0e8e9fe40a633392a7beb2f01763055ab79",
        "91b4c8e6f2a1": "00092bd085e06a59f5691eb9874de36d6f161c89da124d182c77fffea38dac11",
        "c0f4d1b9a8e7": "49ad90a967d5bfaaa2c79bd7e1981304efb795657784b8e0068227f2a72e7391",
        "e6a4c8b9d210": "8a83312fd3e8013d8a7a42f6dc93dbee00858679328bb5cef089304b168c675b",
        "f2c1a9e7b4d6": "e12b48ffc542a557d306543f15e298addd12ea793377b8da338b1951a0fddecd",
        "a3d7e5c9b1f4": "d5e3825644c74c852e297884ecbd8969a8d5c7d313836a4756f805ce9242b6d0",
        "b8d42e190c73": "9a76a65bdd2d1ef3d19030f8b27e97240b96307f675bbac17798f1682c76f534",
        "d5e831a7c902": "1c5709c2dac3555fca21ee5a9c2d15386ba8cb3ce46a2235e9f076ee4701c42f",
        "f7c31a9e204b": "cfc95df957aa0bfd6922fb5f1e961b5af0e52fd3a5a46283aa6d1b75445ef074",
        "c6a9e2f8b104": "0a6cfba861288cde3389a632bbc0ffd4e864a57ba894c54506f5c4cbe82aec5e",
    },
}


def _normalize(value):
    if isinstance(value, dict):
        return {key: _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _postgres_sequences(connection: Connection) -> tuple[list[dict[str, object]], list[str]]:
    rows = connection.execute(
        text(
            "SELECT sequence.relname AS name, definition.seqtypid::regtype::text AS data_type, "
            "definition.seqstart AS start_value, definition.seqmin AS min_value, "
            "definition.seqmax AS max_value, definition.seqincrement AS increment_by, "
            "definition.seqcache AS cache_size, definition.seqcycle AS cycle, "
            "owner_table.relname AS owner_table, owner_column.attname AS owner_column "
            "FROM pg_class sequence "
            "JOIN pg_namespace namespace ON namespace.oid = sequence.relnamespace "
            "JOIN pg_sequence definition ON definition.seqrelid = sequence.oid "
            "LEFT JOIN pg_depend dependency ON dependency.classid = 'pg_class'::regclass "
            "AND dependency.objid = sequence.oid AND dependency.refclassid = 'pg_class'::regclass "
            "AND dependency.deptype IN ('a', 'i') "
            "LEFT JOIN pg_class owner_table ON owner_table.oid = dependency.refobjid "
            "AND owner_table.relnamespace = namespace.oid "
            "LEFT JOIN pg_attribute owner_column ON owner_column.attrelid = owner_table.oid "
            "AND owner_column.attnum = dependency.refobjsubid AND NOT owner_column.attisdropped "
            "WHERE namespace.nspname = current_schema() AND sequence.relkind = 'S' "
            "ORDER BY sequence.relname"
        )
    ).mappings().all()
    sequences: list[dict[str, object]] = []
    unsupported: list[str] = []
    for row in rows:
        if row["owner_table"] is None or row["owner_column"] is None:
            unsupported.append(f"standalone sequence:{row['name']}")
            continue
        sequences.append(dict(row))
    return sequences, unsupported


def _unsupported_features(connection: Connection) -> list[str]:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        rows = connection.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name")
        ).scalars().all()
        return [f"trigger:{name}" for name in rows]
    if dialect == "postgresql":
        rows = connection.execute(
            text(
                "SELECT 'foreign table:' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relkind = 'f' "
                "UNION ALL SELECT 'unsupported relation:' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND (c.relispartition OR c.relkind IN ('p', 'm') "
                "OR c.relrowsecurity OR c.relforcerowsecurity OR EXISTS "
                "(SELECT 1 FROM pg_inherits i WHERE i.inhparent = c.oid) OR EXISTS "
                "(SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)) "
                "UNION ALL SELECT 'trigger:' || t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND NOT t.tgisinternal "
                "UNION ALL SELECT 'policy:' || p.polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = current_schema() "
                "UNION ALL SELECT 'routine:' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = current_schema() "
                "UNION ALL SELECT 'type:' || t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = current_schema() AND (NOT t.typisdefined OR "
                "(t.typrelid = 0 AND t.typelem = 0 AND t.typtype IN ('b', 'd', 'e', 'r', 'm'))) "
                "UNION ALL SELECT 'type:' || t.typname FROM pg_type t JOIN pg_class c ON c.oid = t.typrelid "
                "JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = current_schema() AND t.typtype = 'c' AND c.relkind = 'c' "
                "UNION ALL SELECT 'rewrite rule:' || r.rulename FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND r.rulename <> '_RETURN'"
            )
        ).scalars().all()
        _, standalone_sequences = _postgres_sequences(connection)
        return sorted([*rows, *standalone_sequences])
    return [f"unsupported dialect:{dialect}"]


def catalog_document(connection: Connection) -> dict:
    """Serialize every public table/view and its declared relational structure."""
    unsupported = _unsupported_features(connection)
    if unsupported:
        raise SchemaContractError(f"unsupported catalog features: {unsupported}")
    inspector = inspect(connection)
    document = {"tables": {}, "views": sorted(inspector.get_view_names())}
    if connection.dialect.name == "postgresql":
        sequences, _ = _postgres_sequences(connection)
        document["sequences"] = sequences
    for table_name in sorted(inspector.get_table_names()):
        columns = []
        for column in inspector.get_columns(table_name):
            type_object = column["type"]
            columns.append(
                {
                    "name": column["name"],
                    "type": type_object.compile(dialect=connection.dialect),
                    "type_class": f"{type_object.__class__.__module__}.{type_object.__class__.__qualname__}",
                    "type_attributes": _normalize(vars(type_object)),
                    "nullable": column.get("nullable"),
                    "default": _normalize(column.get("default")),
                    "autoincrement": _normalize(column.get("autoincrement")),
                    "computed": _normalize(column.get("computed")),
                    "identity": _normalize(column.get("identity")),
                }
            )
        document["tables"][table_name] = {
            "columns": columns,
            "primary_key": inspector.get_pk_constraint(table_name),
            "foreign_keys": sorted(
                inspector.get_foreign_keys(table_name), key=lambda item: item.get("name") or ""
            ),
            "unique": sorted(
                inspector.get_unique_constraints(table_name), key=lambda item: item.get("name") or ""
            ),
            "checks": sorted(
                inspector.get_check_constraints(table_name), key=lambda item: item.get("name") or ""
            ),
            "indexes": sorted(inspector.get_indexes(table_name), key=lambda item: item.get("name") or ""),
        }
    if connection.dialect.name == "sqlite":
        document["sqlite_master"] = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "WHERE type IN ('table', 'view', 'index', 'trigger') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
                )
            ).mappings()
        ]
    return _normalize(document)


def _table_tokens(sql: str) -> list:
    """Validate with SQLite, retaining clause bytes except boundary indentation."""
    if re.match(r"\s*CREATE\s+TABLE\s+", sql, re.IGNORECASE) is None:
        raise SchemaContractError("unrecognized SQLite table definition")
    depth, opening, start, closing = 0, None, None, None
    quote = None
    clauses = []
    position = 0
    while position < len(sql):
        character = sql[position]
        if quote is not None:
            if character == quote:
                if quote != "]" and position + 1 < len(sql) and sql[position + 1] == quote:
                    position += 2
                    continue
                quote = None
        elif character in "'\"`[":
            quote = "]" if character == "[" else character
        elif sql[position:position + 2] in {"--", "/*"}:
            raise SchemaContractError("unsupported SQLite table SQL comment")
        elif character == "(":
            if opening is None:
                opening, start = position, position + 1
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise SchemaContractError("malformed SQLite table parentheses")
            if depth == 0 and closing is None:
                clauses.append(sql[start:position].strip())
                closing = position
        elif character == "," and depth == 1:
            clauses.append(sql[start:position].strip())
            start = position + 1
        position += 1
    if quote is not None or depth != 0 or closing is None or any(not clause for clause in clauses):
        raise SchemaContractError("malformed SQLite table clauses")
    identifier = r'(?:"(?:""|[^"])+"|`(?:``|[^`])+`|\[[^\]]+\]|[A-Za-z_][A-Za-z_0-9]*)'
    if re.fullmatch(r"\s*CREATE\s+TABLE\s+" + identifier + r"\s*", sql[:opening], re.IGNORECASE) is None:
        raise SchemaContractError("unsupported SQLite CREATE TABLE prefix")
    validator = sqlite3.connect(":memory:")
    try:
        validator.execute(sql)
    except sqlite3.Error as error:
        raise SchemaContractError("invalid or unsupported SQLite CREATE TABLE SQL") from error
    finally:
        validator.close()
    columns, constraints = [], []
    for clause in clauses:
        target = constraints if re.match(r"(?:CONSTRAINT|PRIMARY|UNIQUE|FOREIGN|CHECK)\b", clause, re.IGNORECASE) else columns
        target.append(clause)
    return [sql[:opening], columns, sorted(constraints), sql[closing + 1:]]


def comparison_catalog_document(native: dict) -> dict:
    """Derive comparison form without mutating or dropping native content."""
    result = json.loads(json.dumps(native, ensure_ascii=True))
    for shape in result["tables"].values():
        for field in ("foreign_keys", "unique", "checks"):
            shape[field] = sorted(shape[field], key=lambda value: json.dumps(
                value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
            ))
    for entry in result.get("sqlite_master", []):
        if entry["type"] == "table" and entry["sql"] is not None:
            entry["sql"] = _table_tokens(entry["sql"])
    return result


def comparison_catalog_hash(native: dict) -> str:
    encoded = json.dumps(
        comparison_catalog_document(native), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def catalog_hash(connection: Connection) -> str:
    return comparison_catalog_hash(catalog_document(connection))


def _database_revision(connection: Connection) -> str | None:
    table_names = set(inspect(connection).get_table_names())
    if ALEMBIC_VERSION_TABLE not in table_names:
        return None
    rows = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if not rows:
        if table_names == {ALEMBIC_VERSION_TABLE}:
            return None
        raise SchemaContractError("empty alembic_version exists beside a non-empty catalog")
    if len(rows) != 1:
        raise SchemaContractError("alembic_version must contain exactly one revision")
    return rows[0]


def _script_revisions(script: ScriptDirectory) -> set[str]:
    return {revision.revision for revision in script.walk_revisions()}


def _assert_catalog(connection: Connection, revision: str) -> None:
    expected = CATALOG_HASHES.get(connection.dialect.name, {}).get(revision)
    if expected is None:
        raise SchemaContractError(
            f"no formal catalog contract for {connection.dialect.name}:{revision}"
        )
    actual = catalog_hash(connection)
    if actual != expected:
        raise SchemaContractError(
            f"schema catalog mismatch for {connection.dialect.name}:{revision}: {actual}"
        )


def assert_migration_source(connection: Connection, script: ScriptDirectory) -> str | None:
    """Reject every non-empty versionless or unknown/catalog-mismatched target."""
    revision = _database_revision(connection)
    inspector = inspect(connection)
    if revision is None:
        unsupported = _unsupported_features(connection)
        if unsupported:
            raise SchemaContractError(f"unsupported catalog features: {unsupported}")
        if set(inspector.get_table_names()) - {ALEMBIC_VERSION_TABLE} or inspector.get_view_names():
            raise SchemaContractError("versionless non-empty database is not a migration target")
        return None
    if revision not in _script_revisions(script):
        raise SchemaContractError(f"unknown Alembic revision: {revision}")
    _assert_catalog(connection, revision)
    return revision


def assert_runtime_schema(connection: Connection, script: ScriptDirectory) -> None:
    """Readonly runtime gate: formal head and its catalog must both match."""
    expected_head = script.get_current_head()
    revision = _database_revision(connection)
    if revision != expected_head:
        raise SchemaContractError(
            f"schema revision mismatch: expected {expected_head}, found {revision}"
        )
    _assert_catalog(connection, expected_head)


def claim_fresh_owned_sqlite_database(database_url: str) -> None:
    """Claim a never-before-used tempfile database without deleting any file."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise SchemaContractError("fixture setup requires an owned SQLite database URL")
    database_path = Path(database_url.removeprefix(prefix)).resolve()
    if database_path.parent != Path(tempfile.gettempdir()).resolve() or not database_path.name.startswith("shift-scheduler-"):
        raise SchemaContractError("fixture database must be a named tempfile target")
    if database_path.exists():
        raise SchemaContractError("fixture database already exists and is not safe to replace")
    owner_claim = database_path.with_suffix(database_path.suffix + ".owner")
    try:
        with owner_claim.open("x", encoding="utf-8") as claim:
            claim.write("owned formal migration fixture\n")
    except FileExistsError as error:
        raise SchemaContractError("fixture database target already has an ownership claim") from error


@contextmanager
def migration_transaction(connection: Connection) -> Iterator[None]:
    """Acquire dialect-specific migration exclusion for the supplied connection."""
    started_here = False
    try:
        if connection.dialect.name == "sqlite":
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            except OperationalError as error:
                message = str(error).lower()
                if "database is locked" in message or "database is busy" in message:
                    raise ConcurrentMigrationError("SQLite migration lock is already held") from error
                raise
            started_here = True
        else:
            connection.begin()
            started_here = True
            if connection.dialect.name == "postgresql":
                locked = connection.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
                ).scalar_one()
                if not locked:
                    raise ConcurrentMigrationError("PostgreSQL migration lock is already held")
        yield
        if started_here:
            connection.commit()
    except BaseException:
        if started_here and connection.in_transaction():
            connection.rollback()
        raise
