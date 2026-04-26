from __future__ import annotations

import argparse
import json
import os
import subprocess
from contextlib import closing
from urllib.parse import urlparse

import requests
from google.cloud import storage
from google.cloud.sql.connector import Connector


PROJECT_ID = "sawahospitalsystem"
PROD_CONN = "sawahospitalsystem:asia-northeast2:orders-prod"
DB_NAME = "orders"
DB_USER = "orders_app"
PROD_SECRET = "db-password"
UPLOAD_LOG_ACTION = "menu_upload"
DEFAULT_STG_BASE_URL = "https://worker-stg-167795504375.asia-northeast2.run.app"


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


def _fetch_prod_upload(cursor, *, source_month_id: str, upload_id: str) -> dict | None:
    cursor.execute(
        """
        SELECT id, created_at, actor, metadata
        FROM audit_logs
        WHERE action = %s AND target = %s AND id = %s
        LIMIT 1
        """,
        (UPLOAD_LOG_ACTION, source_month_id, upload_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    metadata = row[3] if isinstance(row[3], dict) else json.loads(row[3] or "{}")
    return {
        "id": row[0],
        "created_at": row[1],
        "actor": row[2],
        "metadata": metadata,
    }


def _read_gcs_bytes(storage_client: storage.Client, file_uri: str) -> tuple[bytes, str]:
    parsed = urlparse(file_uri)
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path:
        raise ValueError(f"unsupported file_uri: {file_uri}")
    bucket = storage_client.bucket(parsed.netloc)
    blob_name = parsed.path.lstrip("/")
    blob = bucket.blob(blob_name)
    if not blob.exists():
        raise FileNotFoundError(f"missing object: {file_uri}")
    payload = blob.download_as_bytes()
    media_type = blob.content_type or "application/octet-stream"
    return payload, media_type


def _upload_to_stg(
    *,
    stg_base_url: str,
    operator_user: str,
    operator_password: str,
    target_month_id: str,
    filename: str,
    sheet_name: str | None,
    file_bytes: bytes,
    media_type: str,
) -> requests.Response:
    params = {"month_id": target_month_id}
    data: dict[str, str] = {}
    if sheet_name:
        data["sheet_name"] = sheet_name
    return requests.post(
        f"{stg_base_url.rstrip('/')}/monthly-menus",
        params=params,
        auth=(operator_user, operator_password),
        files={"file": (filename, file_bytes, media_type)},
        data=data,
        timeout=180,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a prod monthly menu upload archive into stg via the normal upload API.")
    parser.add_argument("--source-month-id", required=True)
    parser.add_argument("--upload-id", required=True)
    parser.add_argument("--target-month-id", required=True)
    parser.add_argument("--stg-base-url", default=os.getenv("STG_BASE_URL", DEFAULT_STG_BASE_URL))
    parser.add_argument("--operator-user", default=os.getenv("OPERATOR_USER", ""))
    parser.add_argument("--operator-password", default=os.getenv("OPERATOR_PASSWORD", ""))
    args = parser.parse_args()

    if not args.operator_user or not args.operator_password:
        raise SystemExit("OPERATOR_USER and OPERATOR_PASSWORD are required")

    prod_password = _load_secret(PROD_SECRET)
    connector = Connector()
    storage_client = storage.Client(project=PROJECT_ID)
    try:
        with closing(_connect(connector, PROD_CONN, prod_password)) as prod_conn:
            with closing(prod_conn.cursor()) as cursor:
                upload = _fetch_prod_upload(
                    cursor,
                    source_month_id=args.source_month_id,
                    upload_id=args.upload_id,
                )
        if not upload:
            raise SystemExit("prod upload log not found")

        metadata = dict(upload["metadata"] or {})
        file_uri = str(metadata.get("file_uri") or "").strip()
        filename = str(metadata.get("filename") or "").strip()
        if not file_uri or not filename:
            raise SystemExit("prod upload log does not contain file_uri/filename")

        file_bytes, media_type = _read_gcs_bytes(storage_client, file_uri)
        response = _upload_to_stg(
            stg_base_url=args.stg_base_url,
            operator_user=args.operator_user,
            operator_password=args.operator_password,
            target_month_id=args.target_month_id,
            filename=filename,
            sheet_name=metadata.get("sheet_name"),
            file_bytes=file_bytes,
            media_type=media_type,
        )
        summary = {
            "source_month_id": args.source_month_id,
            "upload_id": args.upload_id,
            "target_month_id": args.target_month_id,
            "filename": filename,
            "source_file_uri": file_uri,
            "status_code": response.status_code,
            "response": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if response.status_code >= 400:
            raise SystemExit(1)
    finally:
        connector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
