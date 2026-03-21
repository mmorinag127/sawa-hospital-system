#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
from datetime import datetime
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from src.services.ocr_week_rerun_service import (  # noqa: E402
    ApiJsonResult,
    compare_backup_bundle_to_live,
    compose_api_root,
    fetch_order_backup_bundle,
    load_order_backup_bundle,
    write_json,
)


class HttpApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_prefix: str,
        operator_user: str,
        operator_password: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_root = compose_api_root(base_url, api_prefix=api_prefix)
        token = base64.b64encode(f"{operator_user}:{operator_password}".encode("utf-8")).decode("ascii")
        self.authorization = f"Basic {token}"
        self.timeout_seconds = max(float(timeout_seconds), 1.0)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> ApiJsonResult:
        query = f"?{urlencode(params, doseq=True)}" if params else ""
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.api_root}{normalized_path}{query}"
        payload_bytes = None
        if body is not None:
            payload_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=payload_bytes,
            method=method.upper(),
            headers={
                "Authorization": self.authorization,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                parsed = json.loads(raw.decode("utf-8")) if raw else None
                return ApiJsonResult(status_code=int(getattr(response, "status", 200) or 200), ok=True, data=parsed)
        except HTTPError as exc:
            raw = exc.read()
            parsed = None
            detail = None
            if raw:
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except Exception:
                    parsed = None
            if isinstance(parsed, dict):
                detail = parsed.get("detail") or parsed.get("error")
            return ApiJsonResult(status_code=int(exc.code), ok=False, data=parsed, error=str(detail or exc.reason or f"http_{exc.code}"))
        except (TimeoutError, URLError) as exc:
            detail = getattr(exc, "reason", None)
            return ApiJsonResult(status_code=0, ok=False, data=None, error=str(detail or exc))

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> ApiJsonResult:
        return self._request_json("GET", path, params=params)

    def post_json(self, path: str, body: dict[str, Any] | None = None) -> ApiJsonResult:
        return self._request_json("POST", path, body=body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare a saved week OCR backup against current live OCR endpoints.")
    parser.add_argument("--backup-dir", required=True, help="Backup directory created by backup_and_rerun_week_ocr.py")
    parser.add_argument("--base-url", default="https://web-prod-avlnzjjrca-dt.a.run.app", help="Base web or worker URL.")
    parser.add_argument("--api-prefix", default="/api", help="API prefix. Use empty string for worker direct URL.")
    parser.add_argument("--operator-user", default="", help="Operator basic-auth username. Can also be provided by OPERATOR_USER.")
    parser.add_argument("--operator-password", default="", help="Operator basic-auth password. Can also be provided by OPERATOR_PASSWORD.")
    parser.add_argument("--history-limit", type=int, default=200, help="History length to compare. Default: 200")
    parser.add_argument("--only-changed", action="store_true", help="Print only changed orders.")
    parser.add_argument("--summary-json", default="", help="Optional summary output path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    import os

    operator_user = args.operator_user or os.environ.get("OPERATOR_USER", "")
    operator_password = args.operator_password or os.environ.get("OPERATOR_PASSWORD", "")
    if not operator_user or not operator_password:
        print("OPERATOR_USER / OPERATOR_PASSWORD are required", file=sys.stderr)
        return 2

    backup_dir = Path(args.backup_dir)
    if not backup_dir.exists():
        print(f"backup dir not found: {backup_dir}", file=sys.stderr)
        return 2

    client = HttpApiClient(
        base_url=args.base_url,
        api_prefix=args.api_prefix,
        operator_user=operator_user,
        operator_password=operator_password,
    )

    order_dirs = sorted([path for path in backup_dir.iterdir() if path.is_dir() and path.name.startswith("ORD")])
    comparisons: list[dict[str, Any]] = []
    changed_orders: list[str] = []
    for order_dir in order_dirs:
        order_id = order_dir.name
        backup_bundle = load_order_backup_bundle(output_root=backup_dir, order_id=order_id)
        live_bundle = fetch_order_backup_bundle(client, order_id=order_id, history_limit=max(1, int(args.history_limit)))
        comparison = compare_backup_bundle_to_live(backup_bundle=backup_bundle, live_bundle=live_bundle)
        comparison["compared_at"] = datetime.utcnow().isoformat() + "Z"
        comparisons.append(comparison)
        write_json(order_dir / "live_compare.json", comparison)
        if comparison.get("changed"):
            changed_orders.append(order_id)

    summary = {
        "backup_dir": str(backup_dir),
        "compared_at": datetime.utcnow().isoformat() + "Z",
        "order_count": len(comparisons),
        "changed_count": len(changed_orders),
        "changed_orders": changed_orders,
        "orders": comparisons if not args.only_changed else [item for item in comparisons if item.get("changed")],
    }
    if args.summary_json:
        write_json(args.summary_json, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
