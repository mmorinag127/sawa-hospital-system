"""Fail-closed policy for one run's menu-master verification record, not an app API."""

import hashlib
import json
import math
import re
import time

import sqlalchemy as sa

FIELDS = ("name", "unit_type", "qty_per_serving", "bag_max_qty", "bag_max_unit",
          "temp_type", "daypart", "category", "condiments")
SNAPSHOT = ("id", "revision", "normalized_name", *FIELDS)
NAME_TABLES = ("menu_items", "monthly_menu_items", "monthly_menu_entries", "base_menu_cycle_items")
ID_TABLES = ("menu_facility_overrides", "monthly_menu_items")
TABLES = tuple(sorted({"menu_masters", *NAME_TABLES, *ID_TABLES}))
SA_EMAIL = "sawa-github-deploy-stg@sawahospitalsystem.iam.gserviceaccount.com"
NAME_PATTERN = r"c1-live-[1-9][0-9]*-[1-9][0-9]*-[0-9a-f]{32}"


class Blocked(RuntimeError):
    """Only developer-controlled diagnostics may cross the log boundary."""


def require(ok, code):
    if not ok:
        raise Blocked(code)


def canonical(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fingerprint(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def snapshot(item):
    require(isinstance(item, dict) and set(SNAPSHOT) <= item.keys(), "record-envelope-invalid")
    require(isinstance(item["id"], str) and bool(re.fullmatch(r"MNU[0-9a-f]{8}", item["id"])), "record-id-invalid")
    require(type(item["revision"]) is int and item["revision"] > 0, "record-revision-invalid")
    require(isinstance(item["name"], str) and bool(re.fullmatch(NAME_PATTERN, item["name"])), "record-name-outside-run-format")
    require(item["normalized_name"] == item["name"], "record-normalized-name-mismatch")
    for key in ("qty_per_serving", "bag_max_qty"):
        value = item[key]
        require(value is None or (type(value) in (int, float) and math.isfinite(value)), "record-number-invalid")
    for key in ("unit_type", "bag_max_unit", "temp_type", "daypart", "category"):
        require(item[key] is None or isinstance(item[key], str), "record-text-invalid")
    require(isinstance(item["condiments"], list) and all(isinstance(v, str) for v in item["condiments"]), "record-condiments-invalid")
    return {key: item[key] for key in SNAPSHOT}


def schema_gate(connection):
    require(connection.dialect.name == "postgresql", "postgres-required")
    inspector = sa.inspect(connection)
    schema = connection.execute(sa.text("SELECT current_schema()")).scalar_one()
    require(schema == "public", "public-schema-required")
    relations = connection.execute(sa.text("""
        SELECT c.relname, c.oid, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
               pg_get_userbyid(c.relowner) AS owner,
               has_table_privilege(current_user,c.oid,'SELECT') AS readable,
               has_table_privilege(current_user,c.oid,'DELETE') AS deletable,
               has_table_privilege(current_user,c.oid,'UPDATE,DELETE,TRUNCATE') AS lockable
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname = ANY(:tables)
        ORDER BY c.relname
    """), {"tables": list(TABLES)}).mappings().all()
    require({r["relname"] for r in relations} == set(TABLES), "required-reference-table-missing")
    for relation in relations:
        require(relation["relkind"] == "r", "partition-or-view-not-supported")
        require(not relation["relrowsecurity"] and not relation["relforcerowsecurity"], "row-security-policy-blocks-proof")
        require(relation["readable"] and relation["lockable"], "reference-select-or-lock-privilege-missing")
        if relation["relname"] == "menu_masters":
            require(relation["deletable"], "menu-master-delete-privilege-missing")
    oids = [r["oid"] for r in relations]
    for query, code in (
        ("SELECT 1 FROM pg_trigger WHERE tgrelid=ANY(:oids) AND NOT tgisinternal LIMIT 1", "unknown-trigger"),
        ("SELECT 1 FROM pg_policy WHERE polrelid=ANY(:oids) LIMIT 1", "unknown-policy"),
        ("SELECT 1 FROM pg_rewrite WHERE ev_class=ANY(:oids) LIMIT 1", "unknown-rule"),
        ("SELECT 1 FROM pg_inherits WHERE inhparent=ANY(:oids) OR inhrelid=ANY(:oids) LIMIT 1", "unknown-inheritance"),
    ):
        require(connection.execute(sa.text(query), {"oids": oids}).first() is None, code)
    columns = {c["name"]: c for c in inspector.get_columns("menu_masters", schema="public")}
    require(set(columns) == {*SNAPSHOT, "updated_at"}, "menu-master-column-shape-unknown")
    require(inspector.get_pk_constraint("menu_masters", schema="public")["constrained_columns"] == ["id"], "menu-master-primary-key-invalid")
    require(all(not columns[k]["nullable"] for k in ("id", "name", "normalized_name", "revision")), "menu-master-identity-nullable")
    require(isinstance(columns["revision"]["type"], sa.Integer), "menu-master-revision-type-invalid")
    unique = inspector.get_unique_constraints("menu_masters", schema="public") + inspector.get_indexes("menu_masters", schema="public")
    require(any(u.get("column_names") == ["normalized_name"] and u.get("unique", True)
                and not u.get("dialect_options", {}).get("postgresql_where") for u in unique), "menu-master-name-not-unique")
    for table in NAME_TABLES:
        require("name" in {c["name"] for c in inspector.get_columns(table, schema="public")}, "reference-name-column-missing")
    for table in ID_TABLES:
        require("menu_master_id" in {c["name"] for c in inspector.get_columns(table, schema="public")}, "reference-id-column-missing")
    named_references = connection.execute(sa.text("""
        SELECT n.nspname,c.relname,a.attname FROM pg_attribute a
        JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE a.attnum>0 AND NOT a.attisdropped AND a.attname IN ('menu_master_id','menu_master_name')
          AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
    """)).all()
    require(all(n == 'public' and t in ID_TABLES and col == 'menu_master_id' for n, t, col in named_references),
            "unknown-non-FK-reference-column")
    require(not inspector.get_foreign_keys("menu_masters", schema="public"), "unknown-outgoing-reference")
    references = connection.execute(sa.text("""
        SELECT n.nspname, c.relname, k.conname, k.convalidated, k.confdeltype,
               ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY AS x(num,ord)
                     JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.num ORDER BY x.ord) AS columns,
               ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY AS x(num,ord)
                     JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.num ORDER BY x.ord) AS targets
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE k.contype='f' AND k.confrelid='public.menu_masters'::regclass
        ORDER BY n.nspname,c.relname,k.conname
    """)).mappings().all()
    for ref in references:
        require(ref["nspname"] == "public" and ref["relname"] in ID_TABLES
                and ref["columns"] == ["menu_master_id"] and ref["targets"] == ["id"]
                and ref["convalidated"] and ref["confdeltype"] in ("a", "r"), "unknown-or-cascading-reference")
    return {"postgresVersion": connection.execute(sa.text("SHOW server_version")).scalar_one(),
            "database": connection.execute(sa.text("SELECT current_database()")).scalar_one(),
            "role": connection.execute(sa.text("SELECT current_user")).scalar_one(),
            "relations": [{k: v for k, v in r.items() if k != "oid"} for r in relations],
            "references": [dict(r) for r in references]}


def lock_tables(connection):
    connection.execute(sa.text("SET LOCAL lock_timeout='1s'"))
    connection.execute(sa.text("SET LOCAL statement_timeout='5s'"))
    # Fixed names only. Block non-FK/name-reference writers for the short check/delete transaction.
    connection.execute(sa.text("LOCK TABLE " + ", ".join("public." + t for t in TABLES) + " IN SHARE ROW EXCLUSIVE MODE NOWAIT"))


def preflight(engine, name, *, database="orders", role="orders_app"):
    require(bool(re.fullmatch(NAME_PATTERN, name)), "run-name-invalid")
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(sa.text("SET TRANSACTION READ ONLY"))
            connection.execute(sa.text("SET LOCAL statement_timeout='5s'"))
            proof = schema_gate(connection)
            require(proof["database"] == database and proof["role"] == role, "database-or-role-mismatch")
            users = connection.execute(sa.text("SELECT id,role,status FROM users WHERE lower(account)=:email"), {"email": SA_EMAIL}).mappings().all()
            require(len(users) == 1 and users[0]["role"] in ("operator", "admin") and users[0]["status"] == "active", "deploy-principal-not-active-or-ambiguous")
            grant = connection.execute(sa.text("SELECT enabled FROM user_system_access WHERE user_id=:id AND system_key='hospital'"), {"id": users[0]["id"]}).scalars().all()
            require(grant == [True], "deploy-principal-hospital-grant-missing")
            require(connection.execute(sa.text("SELECT 1 FROM menu_masters WHERE name=:name OR normalized_name=:name LIMIT 1"), {"name": name}).first() is None, "test-name-already-exists")
    # No DML: prove the real role can acquire all locks, then release the transaction.
    with engine.connect() as connection:
        with connection.begin():
            lock_tables(connection)
            require(fingerprint(schema_gate(connection)) == fingerprint(proof), "schema-changed-during-preflight")
    return proof


def last_owned_record(initial, ledger):
    require(all(ledger.get(k) == initial[k] for k in ("version", "name", "source", "runId", "attempt", "nonce", "schemaSHA256", "absentBefore")), "ledger-run-mismatch")
    require(initial["absentBefore"] is True, "ownership-absence-not-proven")
    receipts = ledger.get("receipts", [])
    if not receipts:
        require(not ledger.get("writeAttempted"), "unresolved-post-no-trustworthy-returned-id")
        return None
    require(ledger.get("pending") is None, "unresolved-write-outcome")
    previous = None
    for receipt in receipts:
        require(receipt.get("status") == 200, "write-receipt-not-success")
        current = snapshot(receipt.get("item"))
        require(current["name"] == initial["name"], "ledger-name-outside-run")
        payload = receipt.get("payload", {})
        require({k: payload.get(k) for k in FIELDS} == {k: current[k] for k in FIELDS}, "receipt-payload-mismatch")
        if previous is None:
            require(receipt.get("method") == "POST" and current["revision"] == 1 and set(payload) == set(FIELDS), "creation-receipt-invalid")
        else:
            require(receipt.get("method") == "PUT" and current["id"] == previous["id"]
                    and payload.get("revision") == previous["revision"] and current["revision"] == previous["revision"] + 1
                    and set(payload) == {*FIELDS, "revision"}, "update-receipt-chain-invalid")
        previous = current
    return previous


def cleanup_owned(engine, initial, ledger, proof):
    current = last_owned_record(initial, ledger)
    if current is None:
        return {"status": "no-write", "deleted": []}
    # Reuse the application's canonical name comparison, without calling CRUD/schema helpers.
    from src.services.menu_service import _normalize_menu_name
    with engine.begin() as connection:
        lock_tables(connection)
        deadline = time.monotonic() + 10
        require(fingerprint(schema_gate(connection)) == fingerprint(proof), "cleanup-schema-or-privilege-changed")
        row = connection.execute(sa.text("SELECT * FROM public.menu_masters WHERE id=:id FOR UPDATE"), {"id": current["id"]}).mappings().first()
        require(row is not None and snapshot(dict(row)) == current, "cleanup-row-unknown-or-mutated")
        for table in ID_TABLES:
            require(connection.execute(sa.text(f"SELECT 1 FROM public.{table} WHERE menu_master_id=:id LIMIT 1"), {"id": current["id"]}).first() is None, "cleanup-id-reference-present")
        for table in NAME_TABLES:
            # Do not miss full-width/whitespace equivalents in historical name-only references.
            values = connection.execution_options(stream_results=True).execute(sa.text(f"SELECT name FROM public.{table}"))
            try:
                for name in values.scalars():
                    require(time.monotonic() < deadline, "cleanup-reference-scan-deadline")
                    require(_normalize_menu_name(name or "") != current["normalized_name"], "cleanup-name-reference-present")
            finally:
                values.close()
                connection.execution_options(stream_results=False)
        require(time.monotonic() < deadline, "cleanup-reference-scan-deadline")
        removed = connection.execute(sa.text("DELETE FROM public.menu_masters WHERE id=:id RETURNING id"), {"id": current["id"]}).scalars().all()
        require(removed == [current["id"]], "cleanup-delete-count-mismatch")
    return {"status": "deleted", "deleted": removed}
