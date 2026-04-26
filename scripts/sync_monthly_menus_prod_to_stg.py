from __future__ import annotations

import json
import os
import subprocess
from contextlib import closing
from urllib.parse import urlparse

from google.cloud.sql.connector import Connector
from google.cloud import storage


PROD_CONN = "sawahospitalsystem:asia-northeast2:orders-prod"
STG_CONN = "sawahospitalsystem:asia-northeast2:orders-stg"
DB_NAME = "orders"
DB_USER = "orders_app"
PROD_SECRET = "db-password"
STG_SECRET = "db-password-stg"
PROD_RAW_BUCKET = os.getenv("PROD_RAW_BUCKET", "sawahospitalsystem-prod-raw")
STG_RAW_BUCKET = os.getenv("STG_RAW_BUCKET", "sawahospitalsystem-stg-raw")
UPLOAD_LOG_ACTION = "menu_upload"

COPY_TABLES = [
    "monthly_menus",
    "menu_masters",
    "menu_facility_overrides",
    "monthly_menu_items",
    "monthly_menu_entries",
]

DELETE_ORDER = list(reversed(COPY_TABLES))

UNIQUE_ROW_KEYS = {
    "menu_facility_overrides": ("menu_master_id", "facility_id"),
    "monthly_menu_items": ("monthly_menu_id", "name", "facility_override"),
}


def _load_secret(secret_name: str) -> str:
    env_key = f"{secret_name.upper().replace('-', '_')}_VALUE"
    existing = os.getenv(env_key, "").strip()
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
            "--project=sawahospitalsystem",
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


def _table_columns(cursor, table_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table_name,),
    )
    return [row[0] for row in cursor.fetchall()]


def _fetch_all_rows(cursor, table_name: str) -> tuple[list[str], list[tuple]]:
    columns = _table_columns(cursor, table_name)
    quoted = ", ".join(f'"{column}"' for column in columns)
    cursor.execute(f'SELECT {quoted} FROM "{table_name}"')
    return columns, _dedupe_rows(table_name, columns, cursor.fetchall())


def _dedupe_rows(table_name: str, columns: list[str], rows: list[tuple]) -> list[tuple]:
    unique_key_columns = UNIQUE_ROW_KEYS.get(table_name)
    if not unique_key_columns:
        return rows
    index = {column: idx for idx, column in enumerate(columns)}
    key_indexes = [index[column] for column in unique_key_columns if column in index]
    if len(key_indexes) != len(unique_key_columns):
        return rows
    deduped: dict[tuple, tuple] = {}
    for row in rows:
        key = tuple(row[idx] for idx in key_indexes)
        deduped[key] = row
    return list(deduped.values())


def _delete_table_rows(cursor, table_name: str) -> None:
    cursor.execute(f'DELETE FROM "{table_name}"')


def _insert_rows(cursor, table_name: str, columns: list[str], rows: list[tuple]) -> None:
    if not rows:
        return
    quoted = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f'INSERT INTO "{table_name}" ({quoted}) VALUES ({placeholders})'
    for row in rows:
        cursor.execute(sql, row)


def _count_rows(cursor, table_name: str) -> int:
    cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
    return int(cursor.fetchone()[0])


def _fetch_menu_upload_logs(cursor, month_ids: list[str]) -> tuple[list[str], list[tuple]]:
    columns = _table_columns(cursor, "audit_logs")
    quoted = ", ".join(f'"{column}"' for column in columns)
    if month_ids:
        cursor.execute(
            f'SELECT {quoted} FROM "audit_logs" WHERE "action" = %s AND "target" = ANY(%s) ORDER BY "created_at" ASC, "id" ASC',
            (UPLOAD_LOG_ACTION, month_ids),
        )
    else:
        cursor.execute(
            f'SELECT {quoted} FROM "audit_logs" WHERE "action" = %s ORDER BY "created_at" ASC, "id" ASC',
            (UPLOAD_LOG_ACTION,),
        )
    return columns, cursor.fetchall()


def _count_menu_upload_logs(cursor, month_ids: list[str]) -> int:
    if month_ids:
        cursor.execute(
            'SELECT COUNT(*) FROM "audit_logs" WHERE "action" = %s AND "target" = ANY(%s)',
            (UPLOAD_LOG_ACTION, month_ids),
        )
    else:
        cursor.execute(
            'SELECT COUNT(*) FROM "audit_logs" WHERE "action" = %s',
            (UPLOAD_LOG_ACTION,),
        )
    return int(cursor.fetchone()[0])


def _delete_menu_upload_logs(cursor, month_ids: list[str]) -> None:
    if month_ids:
        cursor.execute(
            'DELETE FROM "audit_logs" WHERE "action" = %s AND "target" = ANY(%s)',
            (UPLOAD_LOG_ACTION, month_ids),
        )
    else:
        cursor.execute(
            'DELETE FROM "audit_logs" WHERE "action" = %s',
            (UPLOAD_LOG_ACTION,),
        )


def _normalize_json_value(raw):
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        return json.loads(raw)
    return {}


def _copy_gcs_object(
    storage_client: storage.Client,
    *,
    source_bucket: str,
    source_blob_name: str,
    dest_bucket: str,
    dest_blob_name: str,
) -> bool:
    src_bucket = storage_client.bucket(source_bucket)
    dst_bucket = storage_client.bucket(dest_bucket)
    src_blob = src_bucket.blob(source_blob_name)
    if not src_blob.exists():
        raise FileNotFoundError(f"missing source object gs://{source_bucket}/{source_blob_name}")
    dst_blob = dst_bucket.blob(dest_blob_name)
    rewritten = False
    token = None
    while True:
        token, _bytes_rewritten, _bytes_total = dst_blob.rewrite(src_blob, token=token)
        rewritten = True
        if token is None:
            break
    return rewritten


def _rewrite_menu_upload_logs_to_stg(
    *,
    storage_client: storage.Client,
    columns: list[str],
    rows: list[tuple],
) -> tuple[list[tuple], int]:
    if not rows:
        return rows, 0
    column_index = {column: idx for idx, column in enumerate(columns)}
    metadata_idx = column_index.get("metadata")
    if metadata_idx is None:
        return rows, 0
    copied_objects = 0
    rewritten_rows: list[tuple] = []
    for row in rows:
        current = list(row)
        metadata = _normalize_json_value(current[metadata_idx])
        file_uri = str(metadata.get("file_uri") or "").strip()
        if file_uri:
            parsed = urlparse(file_uri)
            if parsed.scheme == "gs":
                source_bucket = parsed.netloc
                source_blob_name = parsed.path.lstrip("/")
                if source_bucket == PROD_RAW_BUCKET and source_blob_name:
                    _copy_gcs_object(
                        storage_client,
                        source_bucket=source_bucket,
                        source_blob_name=source_blob_name,
                        dest_bucket=STG_RAW_BUCKET,
                        dest_blob_name=source_blob_name,
                    )
                    copied_objects += 1
                    metadata.setdefault("source_file_uri", file_uri)
                    metadata["file_uri"] = f"gs://{STG_RAW_BUCKET}/{source_blob_name}"
        current[metadata_idx] = metadata
        rewritten_rows.append(tuple(current))
    return rewritten_rows, copied_objects


def main() -> int:
    prod_password = _load_secret(PROD_SECRET)
    stg_password = _load_secret(STG_SECRET)
    connector = Connector()
    storage_client = storage.Client(project="sawahospitalsystem")
    try:
        with closing(_connect(connector, PROD_CONN, prod_password)) as prod_conn, closing(
            _connect(connector, STG_CONN, stg_password)
        ) as stg_conn:
            prod_conn.autocommit = False
            stg_conn.autocommit = False
            with closing(prod_conn.cursor()) as prod_cursor, closing(stg_conn.cursor()) as stg_cursor:
                export_payload: dict[str, dict[str, object]] = {}
                for table_name in COPY_TABLES:
                    columns, rows = _fetch_all_rows(prod_cursor, table_name)
                    export_payload[table_name] = {
                        "columns": columns,
                        "rows": rows,
                        "source_count": len(rows),
                    }
                month_columns = list(export_payload["monthly_menus"]["columns"])
                month_rows = list(export_payload["monthly_menus"]["rows"])
                month_id_idx = month_columns.index("id")
                month_ids = [str(row[month_id_idx]) for row in month_rows if row[month_id_idx]]

                audit_columns, audit_rows = _fetch_menu_upload_logs(prod_cursor, month_ids)
                rewritten_audit_rows, copied_object_count = _rewrite_menu_upload_logs_to_stg(
                    storage_client=storage_client,
                    columns=audit_columns,
                    rows=audit_rows,
                )

                before_counts = {table_name: _count_rows(stg_cursor, table_name) for table_name in COPY_TABLES}
                before_menu_upload_logs = _count_menu_upload_logs(stg_cursor, month_ids)

                for table_name in DELETE_ORDER:
                    _delete_table_rows(stg_cursor, table_name)
                _delete_menu_upload_logs(stg_cursor, month_ids)

                for table_name in COPY_TABLES:
                    payload = export_payload[table_name]
                    _insert_rows(
                        stg_cursor,
                        table_name,
                        list(payload["columns"]),
                        list(payload["rows"]),
                    )
                _insert_rows(
                    stg_cursor,
                    "audit_logs",
                    audit_columns,
                    rewritten_audit_rows,
                )

                stg_conn.commit()
                after_counts = {table_name: _count_rows(stg_cursor, table_name) for table_name in COPY_TABLES}
                after_menu_upload_logs = _count_menu_upload_logs(stg_cursor, month_ids)

            summary = {
                "tables": {
                    table_name: {
                        "prod_rows": int(export_payload[table_name]["source_count"]),
                        "stg_before": before_counts[table_name],
                        "stg_after": after_counts[table_name],
                    }
                    for table_name in COPY_TABLES
                },
                "menu_upload_logs": {
                    "prod_rows": len(rewritten_audit_rows),
                    "stg_before": before_menu_upload_logs,
                    "stg_after": after_menu_upload_logs,
                    "copied_objects": copied_object_count,
                    "source_bucket": PROD_RAW_BUCKET,
                    "dest_bucket": STG_RAW_BUCKET,
                },
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        connector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
