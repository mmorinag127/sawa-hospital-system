#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from src.services.ocr_week_rerun_service import (  # noqa: E402
    ApiJsonResult,
    collect_week_orders,
    compose_api_root,
    fetch_order_backup_bundle,
    trigger_reparse,
    wait_for_ocr_terminal,
    write_json,
    write_order_backup_bundle,
)


class HttpApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_prefix: str,
        bearer_token: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_root = compose_api_root(base_url, api_prefix=api_prefix)
        self.authorization = f"Bearer {bearer_token}"
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
                return ApiJsonResult(
                    status_code=int(getattr(response, "status", 200) or 200),
                    ok=True,
                    data=parsed,
                    error=None,
                )
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
            return ApiJsonResult(
                status_code=int(exc.code),
                ok=False,
                data=parsed,
                error=str(detail or exc.reason or f"http_{exc.code}"),
            )
        except (TimeoutError, URLError) as exc:
            detail = getattr(exc, "reason", None)
            return ApiJsonResult(status_code=0, ok=False, data=None, error=str(detail or exc))

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> ApiJsonResult:
        return self._request_json("GET", path, params=params)

    def post_json(self, path: str, body: dict[str, Any] | None = None) -> ApiJsonResult:
        return self._request_json("POST", path, body=body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up current OCR artifacts for all orders in a week, then rerun OCR reparse.",
    )
    parser.add_argument(
        "--week-start",
        default="2026-02-15",
        help="Week start date in YYYY-MM-DD format. Default: 2026-02-15",
    )
    parser.add_argument(
        "--week-end",
        default="",
        help="Week end date in YYYY-MM-DD format. Default: week_start + 6 days",
    )
    parser.add_argument(
        "--base-url",
        default="https://web-prod-avlnzjjrca-dt.a.run.app",
        help="Base web or worker URL. Default: production web URL.",
    )
    parser.add_argument(
        "--api-prefix",
        default="/api",
        help="API prefix to append to --base-url. Use empty string for worker direct URLs.",
    )
    parser.add_argument(
        "--bearer-token",
        default="",
        help="Google ID token. Can also be provided by GOOGLE_ID_TOKEN.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Directory for backup output. Default: backend/tmp/week_ocr_backups/<week>_<timestamp>",
    )
    parser.add_argument(
        "--facility",
        default="",
        help="Optional facility filter passed to /orders/by-line-date.",
    )
    parser.add_argument(
        "--status",
        default="",
        help="Optional order status filter passed to /orders/by-line-date.",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=200,
        help="History length to store per order. Default: 200",
    )
    parser.add_argument(
        "--ocr-provider",
        default="",
        help="Optional provider override: pipeline|openai|gemini",
    )
    parser.add_argument(
        "--ocr-prompt",
        default="",
        help="Optional extra OCR prompt text for explicit rerun.",
    )
    parser.add_argument(
        "--no-llm-assist",
        action="store_true",
        help="Disable LLM assist in reparse request. Default is enabled.",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll each order until OCR reaches a terminal status.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=900.0,
        help="Timeout when --wait is enabled. Default: 900",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="Poll interval when --wait is enabled. Default: 5",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Back up artifacts and emit manifest, but do not call reparse.",
    )
    return parser.parse_args()


def _resolve_output_dir(raw: str, start_date: date, end_date: date) -> Path:
    if raw.strip():
        return Path(raw.strip())
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "tmp" / "week_ocr_backups" / f"{start_date.isoformat()}_{end_date.isoformat()}_{stamp}"


def main() -> int:
    args = parse_args()
    bearer_token = args.bearer_token or __import__("os").environ.get("GOOGLE_ID_TOKEN", "")
    if not bearer_token:
        print("GOOGLE_ID_TOKEN is required", file=sys.stderr)
        return 2

    start_date = date.fromisoformat(args.week_start)
    end_date = date.fromisoformat(args.week_end) if args.week_end else start_date + timedelta(days=6)
    output_dir = _resolve_output_dir(args.output_dir, start_date, end_date)
    client = HttpApiClient(
        base_url=args.base_url,
        api_prefix=args.api_prefix,
        bearer_token=bearer_token,
    )

    orders, date_summaries = collect_week_orders(
        client,
        start_date=start_date,
        end_date=end_date,
        facility=args.facility or None,
        status=args.status or None,
    )

    manifest: dict[str, Any] = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "week_start": start_date.isoformat(),
        "week_end": end_date.isoformat(),
        "base_url": args.base_url,
        "api_prefix": args.api_prefix,
        "facility": args.facility or None,
        "status": args.status or None,
        "dry_run": bool(args.dry_run),
        "wait": bool(args.wait),
        "order_count": len(orders),
        "date_summaries": date_summaries,
        "orders": [],
    }

    write_json(output_dir / "week_manifest.initial.json", manifest)

    for order in orders:
        order_id = str(order.get("id") or "").strip()
        if not order_id:
            continue
        backup_bundle = fetch_order_backup_bundle(
            client,
            order_id=order_id,
            history_limit=max(1, int(args.history_limit)),
        )
        backup_files = write_order_backup_bundle(output_root=output_dir, bundle=backup_bundle)
        order_entry: dict[str, Any] = {
            "order_id": order_id,
            "received_at": order.get("received_at"),
            "facility": order.get("facility"),
            "backup_files": backup_files,
        }
        if not args.dry_run:
            reparse_result = trigger_reparse(
                client,
                order_id=order_id,
                llm_assist=not args.no_llm_assist,
                ocr_provider=args.ocr_provider or None,
                ocr_prompt=args.ocr_prompt or None,
            )
            order_entry["reparse_request"] = {
                "status_code": reparse_result.status_code,
                "ok": reparse_result.ok,
                "error": reparse_result.error,
                "data": reparse_result.data,
            }
            write_json(output_dir / order_id / "reparse_request.json", order_entry["reparse_request"])
            if args.wait and reparse_result.ok:
                wait_payload = wait_for_ocr_terminal(
                    client,
                    order_id=order_id,
                    timeout_seconds=args.timeout_seconds,
                    poll_seconds=args.poll_seconds,
                )
                order_entry["reparse_wait"] = wait_payload
                write_json(output_dir / order_id / "reparse_wait.json", wait_payload)
        manifest["orders"].append(order_entry)
        write_json(output_dir / "week_manifest.latest.json", manifest)

    final_manifest = output_dir / "week_manifest.final.json"
    write_json(final_manifest, manifest)
    print(json.dumps({"output_dir": str(output_dir), "manifest": str(final_manifest), "order_count": len(manifest["orders"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
