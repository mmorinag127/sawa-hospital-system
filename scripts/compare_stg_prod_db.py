#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any

from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials


PROJECT_ID = "sawahospitalsystem"
DB_NAME = "orders"
DB_USER = "orders_app"
STG_CONN = "sawahospitalsystem:asia-northeast2:orders-stg"
PROD_CONN = "sawahospitalsystem:asia-northeast2:orders-prod"
STG_SECRET = "db-password-stg"
PROD_SECRET = "db-password"

DO_NOT_SYNC_PREFIXES = (
    "order_",
    "orders",
    "uploaded_pdf",
    "ocr_",
    "ingest_",
    "audit_logs",
    "notifications",
    "bags",
    "delivery_notes",
    "label_rows",
    "manufacturing_aggregate_rows",
    "shipping_tracking_",
)
MENU_TABLES = {
    "weekly_menus",
    "menu_items",
    "monthly_menus",
    "monthly_menu_items",
    "monthly_menu_entries",
    "menu_masters",
    "menu_facility_overrides",
}
FACILITY_TABLES = {
    "facilities",
    "facility_areas",
    "facility_configs",
    "facility_template_versions",
}


def _load_secret(secret_name: str) -> str:
    env_key = f"{secret_name.upper().replace('-', '_')}_VALUE"
    existing = str(os.getenv(env_key) or "").strip()
    if existing:
        return existing
    completed = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={secret_name}",
            f"--project={PROJECT_ID}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _connect(connector: Connector, instance_connection_name: str, password: str):
    return connector.connect(
        instance_connection_name,
        "pg8000",
        user=DB_USER,
        password=password,
        db=DB_NAME,
    )


def _make_connector() -> Connector:
    auth_mode = str(os.getenv("CLOUDSQL_CONNECTOR_AUTH") or "auto").strip().lower()
    if auth_mode in {"gcloud", "gcloud-token", "token"}:
        token = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return Connector(credentials=Credentials(token=token), quota_project=PROJECT_ID)
    return Connector()


def _fetch_columns(cursor) -> dict[str, list[dict[str, Any]]]:
    cursor.execute(
        """
        SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
        """
    )
    tables: dict[str, list[dict[str, Any]]] = {}
    for row in cursor.fetchall():
        table = str(row[0])
        tables.setdefault(table, []).append(
            {
                "column": row[1],
                "data_type": row[2],
                "udt_name": row[3],
                "is_nullable": row[4],
                "column_default": row[5],
                "ordinal_position": row[6],
            }
        )
    return tables


def _fetch_indexes(cursor) -> dict[str, list[dict[str, Any]]]:
    cursor.execute(
        """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
        ORDER BY tablename, indexname
        """
    )
    tables: dict[str, list[dict[str, Any]]] = {}
    for row in cursor.fetchall():
        tables.setdefault(str(row[0]), []).append({"name": row[1], "definition": row[2]})
    return tables


def _fetch_constraints(cursor) -> dict[str, list[dict[str, Any]]]:
    cursor.execute(
        """
        SELECT tc.table_name, tc.constraint_name, tc.constraint_type
        FROM information_schema.table_constraints tc
        WHERE tc.table_schema = 'public'
        ORDER BY tc.table_name, tc.constraint_name
        """
    )
    tables: dict[str, list[dict[str, Any]]] = {}
    for row in cursor.fetchall():
        tables.setdefault(str(row[0]), []).append({"name": row[1], "type": row[2]})
    return tables


def _count_rows(cursor, table_name: str) -> int:
    cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
    return int(cursor.fetchone()[0])


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _table_content_hash(cursor, table_name: str, columns: list[str]) -> dict[str, Any]:
    quoted = ", ".join(f'"{column}"' for column in columns)
    order_by = "id" if "id" in columns else columns[0]
    cursor.execute(f'SELECT {quoted} FROM "{table_name}" ORDER BY "{order_by}"')
    h = hashlib.sha256()
    rows = 0
    for row in cursor.fetchall():
        rows += 1
        h.update(_stable_json(dict(zip(columns, row))).encode("utf-8"))
        h.update(b"\n")
    return {"rows": rows, "sha256": h.hexdigest()}


def _snapshot(connector: Connector, env: str, conn_name: str, secret_name: str) -> dict[str, Any]:
    password = _load_secret(secret_name)
    with closing(_connect(connector, conn_name, password)) as conn:
        with closing(conn.cursor()) as cursor:
            columns = _fetch_columns(cursor)
            indexes = _fetch_indexes(cursor)
            constraints = _fetch_constraints(cursor)
            counts = {table: _count_rows(cursor, table) for table in sorted(columns)}
            hashes: dict[str, dict[str, Any]] = {}
            for table in sorted(FACILITY_TABLES & set(columns)):
                hashes[table] = _table_content_hash(cursor, table, [c["column"] for c in columns[table]])
    return {
        "env": env,
        "connection": conn_name,
        "schema": {
            "columns": columns,
            "indexes": indexes,
            "constraints": constraints,
        },
        "row_counts": counts,
        "facility_hashes": hashes,
    }


def _column_signature(column: dict[str, Any]) -> tuple[Any, ...]:
    return (
        column["column"],
        column["data_type"],
        column["udt_name"],
        column["is_nullable"],
        column["column_default"],
    )


def _classify_table(table: str) -> str:
    if table in FACILITY_TABLES:
        return "facility_diff_requires_decision"
    if table in MENU_TABLES:
        return "menu_prod_authoritative_do_not_sync"
    if table == "orders" or any(table.startswith(prefix) for prefix in DO_NOT_SYNC_PREFIXES):
        return "order_or_operational_prod_authoritative_do_not_sync"
    return "review_required"


def _compare(stg: dict[str, Any], prod: dict[str, Any]) -> dict[str, Any]:
    stg_tables = set(stg["schema"]["columns"])
    prod_tables = set(prod["schema"]["columns"])
    schema = {
        "tables_missing_in_prod": sorted(stg_tables - prod_tables),
        "tables_extra_in_prod": sorted(prod_tables - stg_tables),
        "column_differences": [],
        "index_differences": [],
        "constraint_differences": [],
    }
    for table in sorted(stg_tables & prod_tables):
        stg_cols = {_column_signature(c) for c in stg["schema"]["columns"][table]}
        prod_cols = {_column_signature(c) for c in prod["schema"]["columns"][table]}
        if stg_cols != prod_cols:
            schema["column_differences"].append(
                {
                    "table": table,
                    "classification": _classify_table(table),
                    "missing_in_prod": [dict(zip(("column", "data_type", "udt_name", "is_nullable", "column_default"), item)) for item in sorted(stg_cols - prod_cols)],
                    "extra_in_prod": [dict(zip(("column", "data_type", "udt_name", "is_nullable", "column_default"), item)) for item in sorted(prod_cols - stg_cols)],
                }
            )
        stg_indexes = {item["definition"] for item in stg["schema"]["indexes"].get(table, [])}
        prod_indexes = {item["definition"] for item in prod["schema"]["indexes"].get(table, [])}
        if stg_indexes != prod_indexes:
            schema["index_differences"].append(
                {
                    "table": table,
                    "classification": _classify_table(table),
                    "missing_in_prod": sorted(stg_indexes - prod_indexes),
                    "extra_in_prod": sorted(prod_indexes - stg_indexes),
                }
            )
        stg_constraints = {tuple(sorted(item.items())) for item in stg["schema"]["constraints"].get(table, [])}
        prod_constraints = {tuple(sorted(item.items())) for item in prod["schema"]["constraints"].get(table, [])}
        if stg_constraints != prod_constraints:
            schema["constraint_differences"].append(
                {
                    "table": table,
                    "classification": _classify_table(table),
                    "missing_in_prod": [dict(item) for item in sorted(stg_constraints - prod_constraints)],
                    "extra_in_prod": [dict(item) for item in sorted(prod_constraints - stg_constraints)],
                }
            )

    table_counts = []
    for table in sorted(stg_tables | prod_tables):
        table_counts.append(
            {
                "table": table,
                "classification": _classify_table(table),
                "stg_rows": stg["row_counts"].get(table),
                "prod_rows": prod["row_counts"].get(table),
            }
        )
    facility_diffs = []
    for table in sorted(FACILITY_TABLES):
        facility_diffs.append(
            {
                "table": table,
                "stg": stg["facility_hashes"].get(table),
                "prod": prod["facility_hashes"].get(table),
                "differs": stg["facility_hashes"].get(table) != prod["facility_hashes"].get(table),
                "resolution": "decision_required" if stg["facility_hashes"].get(table) != prod["facility_hashes"].get(table) else "no_action",
            }
        )
    return {
        "schema": schema,
        "table_counts": table_counts,
        "facility_diffs": facility_diffs,
        "policy": {
            "code_source": "stg_is_authoritative_for_release_branch",
            "orders": "prod_only_do_not_sync_from_stg",
            "menus": "prod_only_do_not_sync_from_stg",
            "facilities": "compare_and_resolve_per_diff",
        },
    }


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# stg/prod DB diff",
        "",
        f"- generated_at: `{report['generated_at']}`",
        "- policy: code follows stg; orders and menus stay prod-only; facility data requires per-diff decisions",
        "",
        "## Schema",
        "",
        f"- tables_missing_in_prod: {len(report['diff']['schema']['tables_missing_in_prod'])}",
        f"- tables_extra_in_prod: {len(report['diff']['schema']['tables_extra_in_prod'])}",
        f"- column_differences: {len(report['diff']['schema']['column_differences'])}",
        f"- index_differences: {len(report['diff']['schema']['index_differences'])}",
        f"- constraint_differences: {len(report['diff']['schema']['constraint_differences'])}",
        "",
        "## Facility Diff",
        "",
        "| table | stg_rows | prod_rows | differs | resolution |",
        "|---|---:|---:|---|---|",
    ]
    for item in report["diff"]["facility_diffs"]:
        stg_rows = (item.get("stg") or {}).get("rows")
        prod_rows = (item.get("prod") or {}).get("rows")
        lines.append(f"| {item['table']} | {stg_rows} | {prod_rows} | {item['differs']} | {item['resolution']} |")
    lines.extend(["", "## Table Counts", "", "| table | class | stg_rows | prod_rows |", "|---|---|---:|---:|"])
    for item in report["diff"]["table_counts"]:
        lines.append(f"| {item['table']} | {item['classification']} | {item['stg_rows']} | {item['prod_rows']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only stg/prod DB diff for prod release planning.")
    parser.add_argument("--out-dir", default="tmp/prod_release_db_diff")
    parser.add_argument("--json-name", default="stg_prod_db_diff.json")
    parser.add_argument("--markdown-name", default="stg_prod_db_diff.md")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    connector = _make_connector()
    try:
        stg = _snapshot(connector, "stg", STG_CONN, STG_SECRET)
        prod = _snapshot(connector, "prod", PROD_CONN, PROD_SECRET)
    finally:
        connector.close()

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sources": {"stg": STG_CONN, "prod": PROD_CONN},
        "stg": stg,
        "prod": prod,
        "diff": _compare(stg, prod),
    }
    json_path = out_dir / args.json_name
    md_path = out_dir / args.markdown_name
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    _write_markdown(report, md_path)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
