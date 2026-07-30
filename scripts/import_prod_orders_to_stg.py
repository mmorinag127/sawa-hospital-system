from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from google.cloud import storage
from google.cloud.sql.connector import Connector


PROJECT_ID = "sawahospitalsystem"
PROD_CONN = "sawahospitalsystem:asia-northeast2:orders-prod"
STG_CONN = "sawahospitalsystem:asia-northeast2:orders-stg"
DB_NAME = "orders"
DB_USER = "orders_app"
PROD_SECRET = "db-password"
STG_SECRET = "db-password-stg"
DEFAULT_STG_BASE_URL = "https://worker-stg-167795504375.asia-northeast2.run.app"


@dataclass(frozen=True)
class ProdOrder:
    order_id: str
    facility_code: str
    week_code: str
    message_id: str
    document_uri: str
    received_at: str


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


def _require_env_or_arg(value: str | None, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise SystemExit(f"{name} is required")
    return normalized


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(str(uri or "").strip())
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path:
        raise SystemExit(f"unsupported gs uri: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _fetch_prod_orders(order_ids: list[str]) -> list[ProdOrder]:
    connector = Connector()
    try:
        conn = _connect(connector, PROD_CONN, _load_secret(PROD_SECRET))
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT id, facility_code, week_code, message_id, document_uri, received_at
                    FROM orders
                    WHERE id = ANY(%s)
                    ORDER BY received_at ASC, id ASC
                    """,
                    (order_ids,),
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            conn.close()
    finally:
        connector.close()
    by_id = {
        str(row[0] or "").strip(): ProdOrder(
            order_id=str(row[0] or "").strip(),
            facility_code=str(row[1] or "").strip(),
            week_code=str(row[2] or "").strip(),
            message_id=str(row[3] or "").strip(),
            document_uri=str(row[4] or "").strip(),
            received_at=row[5].isoformat() if row[5] is not None else "",
        )
        for row in rows
    }
    missing = [order_id for order_id in order_ids if order_id not in by_id]
    if missing:
        raise SystemExit(f"prod orders not found: {', '.join(missing)}")
    return [by_id[order_id] for order_id in order_ids]


def _fetch_existing_stg_orders_by_message_id(message_ids: list[str]) -> dict[str, str]:
    connector = Connector()
    try:
        conn = _connect(connector, STG_CONN, _load_secret(STG_SECRET))
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT message_id, id
                    FROM orders
                    WHERE message_id = ANY(%s) AND archived_at IS NULL
                    ORDER BY received_at DESC, id DESC
                    """,
                    (message_ids,),
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            conn.close()
    finally:
        connector.close()
    return {str(row[0] or "").strip(): str(row[1] or "").strip() for row in rows}


def _download_pdf_bytes(storage_client: storage.Client, document_uri: str) -> tuple[bytes, str]:
    bucket_name, blob_name = _parse_gs_uri(document_uri)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    if not blob.exists():
        raise SystemExit(f"missing prod source object: {document_uri}")
    data = blob.download_as_bytes()
    return data, Path(blob_name).name


def _upload_to_stg(
    *,
    base_url: str,
    auth: dict[str, str],
    prod_order: ProdOrder,
    pdf_bytes: bytes,
    filename: str,
) -> dict:
    response = requests.post(
        f"{base_url.rstrip('/')}/ingest/upload",
        headers=auth,
        data={
            "facility_hint": prod_order.facility_code,
            "week_hint": prod_order.week_code,
            "received_at": prod_order.received_at,
            "force": "0",
            "skip_ocr": "0",
        },
        files={
            "pdf_file": (filename, pdf_bytes, "application/pdf"),
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise SystemExit(f"unexpected upload payload for {prod_order.order_id}")
    return payload


def _trigger_recovery(base_url: str, auth: dict[str, str]) -> None:
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/ingest/recover-ready",
            headers=auth,
            timeout=60,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(
            json.dumps(
                {
                    "warning": "recover_ready_failed",
                    "detail": str(exc),
                },
                ensure_ascii=False,
            )
        )


def _wait_for_stg_orders(message_ids: list[str], *, timeout_seconds: int) -> dict[str, str]:
    deadline = time.time() + max(timeout_seconds, 1)
    while time.time() < deadline:
        existing = _fetch_existing_stg_orders_by_message_id(message_ids)
        if all(message_id in existing for message_id in message_ids):
            return existing
        time.sleep(2)
    return _fetch_existing_stg_orders_by_message_id(message_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import canonical prod orders into stg via the normal ingest upload API.")
    parser.add_argument("--order-id", action="append", required=True, help="Prod order id to import. Repeatable.")
    parser.add_argument("--stg-base-url", default=os.getenv("STG_BASE_URL", DEFAULT_STG_BASE_URL))
    parser.add_argument("--bearer-token", default=os.getenv("GOOGLE_ID_TOKEN"))
    parser.add_argument("--wait-seconds", type=int, default=120)
    args = parser.parse_args()

    bearer_token = _require_env_or_arg(args.bearer_token, "GOOGLE_ID_TOKEN")
    auth = {"Authorization": f"Bearer {bearer_token}"}

    order_ids = [str(order_id or "").strip() for order_id in args.order_id if str(order_id or "").strip()]
    prod_orders = _fetch_prod_orders(order_ids)
    existing_before = _fetch_existing_stg_orders_by_message_id([order.message_id for order in prod_orders])

    storage_client = storage.Client(project=PROJECT_ID)
    uploaded: list[dict] = []
    skipped: list[dict] = []
    for prod_order in prod_orders:
        if prod_order.message_id in existing_before:
            skipped.append(
                {
                    "prod_order_id": prod_order.order_id,
                    "message_id": prod_order.message_id,
                    "existing_stg_order_id": existing_before[prod_order.message_id],
                }
            )
            continue
        pdf_bytes, filename = _download_pdf_bytes(storage_client, prod_order.document_uri)
        payload = _upload_to_stg(
            base_url=args.stg_base_url,
            headers=auth,
            prod_order=prod_order,
            pdf_bytes=pdf_bytes,
            filename=filename,
        )
        uploaded.append(
            {
                "prod_order_id": prod_order.order_id,
                "facility_code": prod_order.facility_code,
                "week_code": prod_order.week_code,
                "message_id": prod_order.message_id,
                "document_uri": prod_order.document_uri,
                "stg_upload": payload,
            }
        )

    if uploaded:
        _trigger_recovery(args.stg_base_url, auth)

    final_orders = _wait_for_stg_orders([order.message_id for order in prod_orders], timeout_seconds=args.wait_seconds)
    missing_after = [
        order.message_id
        for order in prod_orders
        if order.message_id not in final_orders
    ]

    summary = {
        "requested_order_ids": order_ids,
        "uploaded_count": len(uploaded),
        "skipped_count": len(skipped),
        "uploaded": uploaded,
        "skipped": skipped,
        "stg_orders_by_message_id": final_orders,
        "missing_after_wait": missing_after,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if missing_after:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
