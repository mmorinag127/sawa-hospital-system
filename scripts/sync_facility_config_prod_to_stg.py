from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from uuid import uuid4

import requests
from google.cloud.sql.connector import Connector


PROJECT_ID = "sawahospitalsystem"
DEFAULT_PROD_BASE_URL = "https://worker-prod-avlnzjjrca-dt.a.run.app"
DEFAULT_STG_BASE_URL = "https://worker-stg-167795504375.asia-northeast2.run.app"
STG_CONN = "sawahospitalsystem:asia-northeast2:orders-stg"
DB_NAME = "orders"
DB_USER = "orders_app"
STG_SECRET = "db-password-stg"


def _require_env_or_arg(value: str | None, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise SystemExit(f"{name} is required")
    return normalized


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


def _get_facility_payload(base_url: str, facility_id: str, auth: tuple[str, str]) -> dict:
    response = requests.get(
        f"{base_url.rstrip('/')}/facilities/{facility_id}",
        auth=auth,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise SystemExit("facility payload is not an object")
    return payload


def _put_facility_config(base_url: str, facility_id: str, config: dict, auth: tuple[str, str]) -> dict:
    response = requests.put(
        f"{base_url.rstrip('/')}/facilities/{facility_id}/config",
        auth=auth,
        json={"config": config},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise SystemExit("update response is not an object")
    return payload


def _put_facility_config_via_db(facility_id: str, config: dict) -> dict:
    connector = Connector()
    stg_password = _load_secret(STG_SECRET)
    try:
        conn = _connect(connector, STG_CONN, stg_password)
        try:
            conn.autocommit = False
            cursor = conn.cursor()
            try:
                cursor.execute('SELECT 1 FROM facilities WHERE id = %s', (facility_id,))
                if cursor.fetchone() is None:
                    raise SystemExit(f"facility not found in stg: {facility_id}")
                cursor.execute('DELETE FROM facility_configs WHERE facility_id = %s', (facility_id,))
                cursor.execute(
                    'INSERT INTO facility_configs (facility_id, config_json) VALUES (%s, %s)',
                    (facility_id, json.dumps(config, ensure_ascii=False)),
                )
                cursor.execute(
                    'INSERT INTO audit_logs (id, actor, action, target, fac, wek, metadata, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                    (
                        f"AUD{uuid4().hex[:16]}",
                        "system",
                        "facility_config_sync",
                        facility_id,
                        facility_id,
                        None,
                        json.dumps({"source": "prod_to_stg_sync", "keys": sorted(config.keys())}, ensure_ascii=False),
                        datetime.utcnow(),
                    ),
                )
                conn.commit()
            finally:
                cursor.close()
        finally:
            conn.close()
    finally:
        connector.close()
    return {"updated": True, "mode": "db"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync raw facility config from prod to stg through the normal API.")
    parser.add_argument("--facility-id", required=True)
    parser.add_argument("--prod-base-url", default=os.getenv("PROD_BASE_URL", DEFAULT_PROD_BASE_URL))
    parser.add_argument("--stg-base-url", default=os.getenv("STG_BASE_URL", DEFAULT_STG_BASE_URL))
    parser.add_argument("--operator-user", default=os.getenv("OPERATOR_USER"))
    parser.add_argument("--operator-password", default=os.getenv("OPERATOR_PASSWORD"))
    parser.add_argument("--write-mode", choices=("auto", "api", "db"), default="auto")
    args = parser.parse_args()

    operator_user = _require_env_or_arg(args.operator_user, "OPERATOR_USER")
    operator_password = _require_env_or_arg(args.operator_password, "OPERATOR_PASSWORD")
    auth = (operator_user, operator_password)

    prod_payload = _get_facility_payload(args.prod_base_url, args.facility_id, auth)
    raw_config = prod_payload.get("config")
    if not isinstance(raw_config, dict):
        raise SystemExit("prod raw facility config is missing")

    if args.write_mode == "db":
        update_payload = _put_facility_config_via_db(args.facility_id, raw_config)
    else:
        try:
            update_payload = _put_facility_config(args.stg_base_url, args.facility_id, raw_config, auth)
        except requests.HTTPError:
            if args.write_mode != "auto":
                raise
            update_payload = _put_facility_config_via_db(args.facility_id, raw_config)
    stg_payload = _get_facility_payload(args.stg_base_url, args.facility_id, auth)

    summary = {
        "facility_id": args.facility_id,
        "prod_config_keys": sorted(raw_config.keys()),
        "prod_raw_config": raw_config,
        "stg_update": update_payload,
        "stg_raw_config": stg_payload.get("config"),
        "stg_resolved_config": stg_payload.get("resolved_config"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
