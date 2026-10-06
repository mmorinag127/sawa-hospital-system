from __future__ import annotations

import argparse
import json
import os

import requests


DEFAULT_PROD_BASE_URL = "https://worker-prod-avlnzjjrca-dt.a.run.app"
DEFAULT_STG_BASE_URL = "https://worker-stg-167795504375.asia-northeast2.run.app"


def _require_env_or_arg(value: str | None, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise SystemExit(f"{name} is required")
    return normalized


def _require_snapshot(payload: dict, facility_id: str) -> str:
    revision = payload.get("revision")
    if payload.get("source") != "canonical_json" or not isinstance(revision, str) or not revision:
        raise SystemExit("canonical JSON revision is missing; outcome unconfirmed; no automatic write or retry")
    if not isinstance(payload.get("facility"), dict) or payload["facility"].get("id") != facility_id:
        raise SystemExit("facility snapshot target mismatch; outcome unconfirmed; no automatic write or retry")
    if not isinstance(payload.get("config"), dict) or not isinstance(payload.get("resolved_config"), dict):
        raise SystemExit("paired canonical facility config is missing; outcome unconfirmed; no automatic write or retry")
    return revision


def _get_facility_payload(base_url: str, facility_id: str, auth: dict[str, str]) -> dict:
    response = requests.get(
        f"{base_url.rstrip('/')}/facilities/{facility_id}",
        headers=auth,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise SystemExit("facility payload is not an object")
    _require_snapshot(payload, facility_id)
    return payload


def _put_facility_config(base_url: str, facility_id: str, config: dict, auth: dict[str, str], *, expected_revision: str) -> dict:
    response = requests.put(
        f"{base_url.rstrip('/')}/facilities/{facility_id}/config",
        headers=auth,
        json={"config": config, "expected_revision": expected_revision},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise SystemExit("update response is not an object")
    revision = _require_snapshot(payload, facility_id)
    projection = payload.get("projection")
    if payload.get("updated") is not True or not isinstance(projection, dict) or \
            projection.get("revision") != revision or projection.get("status") not in {"synced", "reconcile_required"}:
        raise SystemExit("save receipt unconfirmed; JSON may be saved; do not retry automatically")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync raw facility config from prod to stg through the normal API.")
    parser.add_argument("--facility-id", required=True)
    parser.add_argument("--prod-base-url", default=os.getenv("PROD_BASE_URL", DEFAULT_PROD_BASE_URL))
    parser.add_argument("--stg-base-url", default=os.getenv("STG_BASE_URL", DEFAULT_STG_BASE_URL))
    parser.add_argument("--bearer-token", default=os.getenv("GOOGLE_ID_TOKEN"))
    parser.add_argument("--write-mode", choices=("api",), default="api")
    args = parser.parse_args()

    bearer_token = _require_env_or_arg(args.bearer_token, "GOOGLE_ID_TOKEN")
    auth = {"Authorization": f"Bearer {bearer_token}"}

    destination = _get_facility_payload(args.stg_base_url, args.facility_id, auth)
    expected_revision = _require_snapshot(destination, args.facility_id)
    prod_payload = _get_facility_payload(args.prod_base_url, args.facility_id, auth)
    raw_config = prod_payload.get("config")
    if not isinstance(raw_config, dict):
        raise SystemExit("prod raw facility config is missing")

    update_payload = _put_facility_config(args.stg_base_url, args.facility_id, raw_config, auth,
                                          expected_revision=expected_revision)

    summary = {
        "facility_id": args.facility_id,
        "prod_config_keys": sorted(raw_config.keys()),
        "prod_raw_config": raw_config,
        "stg_update": update_payload,
        "stg_expected_revision": expected_revision,
        "stg_raw_config": update_payload["config"],
        "stg_resolved_config": update_payload["resolved_config"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
