from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from contextlib import closing
from urllib.parse import urlparse

import requests
from google.cloud import storage
from google.cloud.sql.connector import Connector
from verify_stg_monthly_menus import apply_saved_import, authored_selection, review_material, validate_receipt


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
    bearer_token: str,
    target_month_id: str,
    filename: str,
    sheet_name: str | None,
    file_bytes: bytes,
    media_type: str,
    scope_override: str | None,
    diet_types: list[str] | None,
) -> requests.Response:
    authored_selection({"scope_override": scope_override, "diet_types": diet_types})
    params = {"month_id": target_month_id}
    if diet_types is not None:
        params["diet_types"] = json.dumps(diet_types, ensure_ascii=False)
    if sheet_name:
        params["sheet_name"] = sheet_name
    if scope_override is not None:
        if not isinstance(scope_override, str) or not scope_override:
            raise ValueError("recorded source scope is unresolved")
        params.update({"scope_type": "tag" if scope_override.startswith("TAG:") else "facility",
                       "scope_value": scope_override[4:] if scope_override.startswith("TAG:") else scope_override})
    return requests.post(
        f"{stg_base_url.rstrip('/')}/monthly-menus",
        params=params,
        headers={"Authorization": f"Bearer {bearer_token}"},
        files={"file": (filename, file_bytes, media_type)},
        timeout=180,
    )


def _verified_original_digest(file_bytes, metadata, explicit_digest):
    digest = hashlib.sha256(file_bytes).hexdigest()
    recorded_digest = metadata.get("content_sha256")
    if recorded_digest is not None:
        if (not isinstance(recorded_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", recorded_digest)
            or digest != recorded_digest):
            raise ValueError("prod original differs from recorded archive digest")
    elif explicit_digest != digest:
        raise ValueError("unrecorded original digest requires explicit verified --source-sha256")
    if explicit_digest is not None and explicit_digest != digest:
        raise ValueError("original differs from explicit verified --source-sha256")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a prod monthly menu upload archive into stg via the normal upload API.")
    parser.add_argument("--source-month-id", required=True)
    parser.add_argument("--upload-id", required=True)
    parser.add_argument("--target-month-id", required=True)
    parser.add_argument("--stg-base-url", default=os.getenv("STG_BASE_URL", DEFAULT_STG_BASE_URL))
    parser.add_argument("--bearer-token", default=os.getenv("GOOGLE_ID_TOKEN", ""))
    parser.add_argument("--saved-import-id", help="Exact previously saved staging receipt to explicitly review")
    parser.add_argument("--review-file", type=Path, help="Operator reviewed exact receipt/input plus explicit resolutions JSON")
    parser.add_argument("--source-sha256", help="Explicit verified original digest if the historical log has no digest")
    args = parser.parse_args()

    if not args.bearer_token:
        raise SystemExit("GOOGLE_ID_TOKEN is required")

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
        scope, diets = authored_selection(metadata)

        file_bytes, media_type = _read_gcs_bytes(storage_client, file_uri)
        digest = _verified_original_digest(file_bytes, metadata, args.source_sha256)
        auth = {"Authorization": f"Bearer {args.bearer_token}"}
        if args.saved_import_id:
            response = requests.get(f"{args.stg_base_url.rstrip('/')}/monthly-menus/{args.target_month_id}/imports/{args.saved_import_id}",
                                    headers=auth, timeout=180)
        else:
            response = _upload_to_stg(
                stg_base_url=args.stg_base_url, bearer_token=args.bearer_token,
                target_month_id=args.target_month_id, filename=filename,
                sheet_name=metadata.get("sheet_name"), file_bytes=file_bytes,
                media_type=media_type, scope_override=scope, diet_types=diets,
            )
        summary = {
            "source_month_id": args.source_month_id,
            "upload_id": args.upload_id,
            "target_month_id": args.target_month_id,
            "filename": filename,
            "source_file_uri": file_uri,
            "applied": False,
            "status_code": response.status_code,
            "response": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if response.status_code >= 400:
            raise SystemExit(1)
        receipt = response.json()
        validate_receipt(receipt, month=args.target_month_id, filename=filename, digest=digest,
                         scope=scope, diet_types=diets, sheet_name=metadata.get("sheet_name"), import_id=args.saved_import_id)
        print(json.dumps({"saved_import_id": receipt["id"], "applied": False,
                          "review_material": review_material(receipt)}, ensure_ascii=False, indent=2))
        if args.review_file is None:
            raise SystemExit("Original saved, not applied: explicit --review-file and exact --saved-import-id are required to continue")
        if not args.saved_import_id:
            raise SystemExit("Review/apply requires explicitly resuming the saved receipt; no automatic approval of a new upload")
        approval = json.loads(args.review_file.read_text())
        def command(path, body=None, content_type=None, *, revision=None):
            headers = {**auth, "If-Match": revision}
            if content_type:
                headers["Content-Type"] = content_type
            own_response = requests.post(args.stg_base_url.rstrip('/') + path, data=body, headers=headers, timeout=180)
            own_response.raise_for_status()
            own = own_response.json()
            print(json.dumps({"stage": "review_response" if path.endswith("/review") else "apply_response",
                              "response": own}, ensure_ascii=False, indent=2))
            return own
        applied = apply_saved_import(receipt, approval, command)
        print(json.dumps({**summary, "status_code": 200, "applied": True, "response": applied}, ensure_ascii=False, indent=2))
    finally:
        connector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
