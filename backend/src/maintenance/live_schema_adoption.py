"""Explicit transaction for reviewed untracked PostgreSQL schemas, never runtime fallback."""
import hashlib
import json

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from src.maintenance.formal_migration_schema import (
    ALEMBIC_CONFIG, CONTRACT, _normalize_reflected_catalog_aliases,
    read_catalog, require_revision_catalog, table_shapes,
    column_identity_policy,
)

MIGRATION_LOCK = (0x53415741, 0x4D494752)
PRESERVED_NULLS = {("audit_logs", "created_at"), ("users", "created_at")}


def _quote(connection, name):
    return connection.dialect.identifier_preparer.quote(name)


def _shape(contract, revision, table):
    return contract["shapes"][contract["revisions"]["postgresql"][revision]["tables"][table]]


def _fingerprint(connection, columns):
    result = {}
    for table, names in sorted(columns.items()):
        query = "SELECT " + ",".join(_quote(connection, name) for name in names)
        query += " FROM " + _quote(connection, table)
        rows = sorted(json.dumps(list(row), sort_keys=True, default=str, ensure_ascii=False)
                      for row in connection.exec_driver_sql(query))
        result[table] = {"rows": len(rows), "sha256": hashlib.sha256(
            json.dumps(rows, ensure_ascii=False).encode()).hexdigest()}
    return result


def _require_baseline(connection, contract):
    catalog = read_catalog(connection)
    actual = table_shapes(catalog, "postgresql")
    matched = []
    for name, baseline in contract["adoption_baselines"].items():
        expected = {table: _normalize_reflected_catalog_aliases(contract["shapes"][ref], "postgresql")
                    for table, ref in baseline["tables"].items()}
        if actual == expected and catalog["views"] == baseline["views"]:
            matched.append(name)
    if len(matched) != 1:
        raise RuntimeError("adoption blocked: complete catalog does not match exactly one reviewed baseline")
    return matched[0], actual


def _normalize(connection, contract, actual):
    # DDL is derived only from the reviewed formal contract after exact live-baseline validation.
    for table, observed in actual.items():
        target = _shape(contract, "0028", table)
        columns = {column["name"]: column for column in observed["columns"]}
        for column in target["columns"]:
            name = column["name"]
            if name not in columns:
                if (table, name) == ("order_ocr_revisions", "metadata"):
                    connection.exec_driver_sql('ALTER TABLE order_ocr_revisions ADD COLUMN metadata JSON NULL')
                elif (table, name) != ("menu_masters", "revision"):
                    raise RuntimeError("adoption blocked: unexpected missing column")
                continue
            prefix = f"ALTER TABLE {_quote(connection, table)} ALTER COLUMN {_quote(connection, name)} "
            if columns[name]["default"] != column["default"]:
                if column["default"] is None:
                    connection.exec_driver_sql(prefix + "DROP DEFAULT")
                else:
                    connection.exec_driver_sql(prefix + "SET DEFAULT " + column["default"])
            if columns[name]["nullable"] != column["nullable"] and (table, name) not in PRESERVED_NULLS:
                if column["nullable"]:
                    raise RuntimeError("adoption blocked: unreviewed nullable relaxation")
                connection.exec_driver_sql(prefix + "SET NOT NULL")
        actual_indexes = {item["index_name"] for item in observed["native"] if "index_name" in item}
        for item in target["native"]:
            if ("index_name" in item and item["index_name"] not in actual_indexes
                    and item["owner"] is None):
                connection.exec_driver_sql(item["definition"])
                actual_indexes.add(item["index_name"])
    connection.exec_driver_sql("ALTER TABLE facility_areas DROP CONSTRAINT facility_areas_pkey")
    connection.exec_driver_sql("ALTER TABLE facility_areas ADD CONSTRAINT facility_areas_pkey PRIMARY KEY (facility_id,id)")
    connection.exec_driver_sql("ALTER TABLE menu_masters ADD CONSTRAINT uq_menu_masters_normalized_name UNIQUE USING INDEX ix_menu_masters_normalized_name")
    scripts = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG)))
    statuses = scripts.get_revision("0016").module._STATUS_VALUES
    connection.exec_driver_sql("ALTER TABLE facility_template_versions DROP CONSTRAINT ck_facility_template_versions_status")
    connection.exec_driver_sql("ALTER TABLE facility_template_versions ADD CONSTRAINT ck_facility_template_versions_status "
                               + f"CHECK (status IN {tuple(statuses)!r})")


def _adoption_plan(contract):
    scripts = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG)))
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise RuntimeError("adoption blocked: exactly one reviewed target is required")
    target = heads[0]
    final = contract["revisions"].get("postgresql", {}).get(target)
    if final is None or column_identity_policy(final, "postgresql", target) != "name":
        raise RuntimeError("adoption blocked: observed target PostgreSQL contract is required")
    revisions = list(reversed(list(scripts.iterate_revisions(target, "0026"))))
    if "0035" not in [revision.revision for revision in revisions]:
        raise RuntimeError("adoption blocked: reviewed preservation migration is required")
    return target, revisions


def adopt(connection):
    if connection.dialect.name != "postgresql" or connection.in_transaction():
        raise RuntimeError("adoption requires PostgreSQL and its own transaction")
    contract = json.loads(CONTRACT.read_text())
    target, revisions = _adoption_plan(contract)
    with connection.begin():
        acquired = connection.execute(sa.text("SELECT pg_try_advisory_xact_lock(:n,:k)"),
                                      {"n": MIGRATION_LOCK[0], "k": MIGRATION_LOCK[1]}).scalar_one()
        if not acquired:
            raise RuntimeError("adoption blocked: migration lock is already held")
        tables = sa.inspect(connection).get_table_names()
        if "alembic_version" in tables:
            raise RuntimeError("adoption blocked: database is already tracked")
        if not tables:
            raise RuntimeError("adoption blocked: empty schema must use formal migrations")
        connection.exec_driver_sql("LOCK TABLE " + ",".join(_quote(connection, table) for table in sorted(tables))
                                   + " IN ACCESS EXCLUSIVE MODE NOWAIT")
        baseline, actual = _require_baseline(connection, contract)
        columns = {table: [column["name"] for column in shape["columns"]] for table, shape in actual.items()}
        before = _fingerprint(connection, columns)
        _normalize(connection, contract, actual)
        with Operations.context(MigrationContext.configure(connection)):
            for revision in revisions:
                revision.module.upgrade()
        after = _fingerprint(connection, columns)
        if before != after:
            raise RuntimeError("adoption blocked: existing values changed")
        # Version table shape participates in the final guard; success row is written only afterwards.
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))")
        require_revision_catalog(connection, target)
        connection.execute(sa.text("INSERT INTO alembic_version(version_num) VALUES (:target)"), {"target": target})
    return {"baseline": baseline, "revision": target, "preserved": before, "source_unchanged": before == after}
