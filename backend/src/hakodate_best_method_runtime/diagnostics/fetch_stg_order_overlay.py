#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[4]
OUT_DIR = WORKSPACE / "tmp" / "stg_order_overlay_fetch"
STG_API_BASE = os.getenv("STG_API_BASE", "https://web-stg-avlnzjjrca-dt.a.run.app/api")
PROJECT_ID = "sawahospitalsystem"
REGION = "asia-northeast2"
SERVICE = "worker-stg"


def _operator_auth_header_from_gcloud() -> str:
    raw = subprocess.check_output(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            SERVICE,
            f"--project={PROJECT_ID}",
            f"--region={REGION}",
            "--format=json",
        ],
        text=True,
    )
    service = json.loads(raw)
    env: dict[str, str] = {}
    containers = service.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if not containers:
        containers = service.get("spec", {}).get("template", {}).get("containers", [])
    for container in containers:
        for item in container.get("env", []):
            if "value" in item:
                env[str(item.get("name"))] = str(item.get("value"))
    user = env.get("OPERATOR_USER")
    password = env.get("OPERATOR_PASSWORD")
    if not user or not password:
        raise RuntimeError("operator credentials are unavailable from Cloud Run env")
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _fetch_json(url: str, auth_header: str, timeout: int = 90) -> tuple[dict[str, Any], float, int]:
    request = urllib.request.Request(url, headers={"Authorization": auth_header})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = int(response.status)
        payload = response.read()
    elapsed = time.monotonic() - started
    return json.loads(payload), elapsed, status


def _download(url: str, out_path: Path, timeout: int = 90) -> float:
    started = time.monotonic()
    with urllib.request.urlopen(url, timeout=timeout) as response:
        out_path.write_bytes(response.read())
    return time.monotonic() - started


def main() -> None:
    order_id = sys.argv[1] if len(sys.argv) > 1 else "ORD4cfa1982"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    auth_header = _operator_auth_header_from_gcloud()

    order_url = f"{STG_API_BASE}/orders/{order_id}"
    order_payload, order_elapsed, order_status = _fetch_json(order_url, auth_header, timeout=120)
    (OUT_DIR / f"{order_id}_order.json").write_text(
        json.dumps(order_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pages_url = f"{STG_API_BASE}/orders/{order_id}/ocr-pages?preview_only=1&quantity_assignment_strategy=hakodate"
    pages_payload, pages_elapsed, pages_status = _fetch_json(pages_url, auth_header, timeout=180)
    (OUT_DIR / f"{order_id}_ocr_pages.json").write_text(
        json.dumps(pages_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pages = pages_payload.get("pages") if isinstance(pages_payload.get("pages"), list) else []
    overlay_url = ""
    if pages and isinstance(pages[0], dict):
        overlay_url = str(pages[0].get("hakodate_overlay_url") or "").strip()
    overlay_path = None
    overlay_elapsed = None
    if overlay_url:
        overlay_path = OUT_DIR / f"{order_id}_hakodate_overlay.png"
        overlay_elapsed = _download(overlay_url, overlay_path)

    summary = {
        "order_id": order_id,
        "order_status_code": order_status,
        "order_elapsed_seconds": round(order_elapsed, 3),
        "ocr_pages_status_code": pages_status,
        "ocr_pages_elapsed_seconds": round(pages_elapsed, 3),
        "hakodate_overlay_status": pages_payload.get("hakodate_overlay_status"),
        "hakodate_overlay_blockers": pages_payload.get("hakodate_overlay_blockers"),
        "hakodate_overlay_message": pages_payload.get("hakodate_overlay_message"),
        "page_count": pages_payload.get("page_count"),
        "overlay_downloaded": bool(overlay_path),
        "overlay_path": str(overlay_path) if overlay_path else None,
        "overlay_download_elapsed_seconds": round(overlay_elapsed, 3) if overlay_elapsed is not None else None,
    }
    summary_path = OUT_DIR / f"{order_id}_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
