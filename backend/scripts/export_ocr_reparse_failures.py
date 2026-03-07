#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from src.db import session_scope
from src.models.ocr_job import OcrJob


def _normalize_provider(metrics: dict[str, Any]) -> str:
    for key in ("provider", "requested_provider"):
        value = metrics.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _normalize_error(*, status: str, error_message: str | None, metrics: dict[str, Any]) -> str:
    value = metrics.get("error")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    if isinstance(error_message, str) and error_message.strip():
        return error_message.strip().lower()
    normalized_status = str(status or "").strip().lower()
    if normalized_status:
        return f"status:{normalized_status}"
    return "unknown"


def _order_id_from_job_id(job_id: str) -> str | None:
    raw = str(job_id or "").strip()
    if raw.startswith("OCR-ORD"):
        return raw[len("OCR-") :]
    return None


def collect_failures(*, hours: int, limit: int) -> dict[str, Any]:
    since = datetime.utcnow() - timedelta(hours=max(1, int(hours)))
    with session_scope() as session:
        rows = (
            session.execute(
                select(
                    OcrJob.id,
                    OcrJob.status,
                    OcrJob.metrics,
                    OcrJob.error_message,
                    OcrJob.updated_at,
                )
                .where(OcrJob.updated_at >= since)
                .order_by(OcrJob.updated_at.desc())
                .limit(max(1, int(limit)))
            )
            .all()
        )

    patterns: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "latest_at": None, "sample_orders": []}
    )
    total_failed = 0
    scanned = 0

    for job_id, status_value, metrics_value, error_message_value, updated_at_value in rows:
        scanned += 1
        metrics = metrics_value if isinstance(metrics_value, dict) else {}
        provider = _normalize_provider(metrics)
        if not provider:
            continue
        status = str(status_value or "").strip().lower()
        if status not in {"failed", "empty"}:
            continue
        total_failed += 1
        error_key = _normalize_error(status=status, error_message=error_message_value, metrics=metrics)
        key = (provider, error_key)
        item = patterns[key]
        item["count"] += 1
        if updated_at_value:
            latest_at = item.get("latest_at")
            if latest_at is None or updated_at_value > latest_at:
                item["latest_at"] = updated_at_value
        order_id = _order_id_from_job_id(str(job_id or ""))
        if order_id and order_id not in item["sample_orders"] and len(item["sample_orders"]) < 10:
            item["sample_orders"].append(order_id)

    entries: list[dict[str, Any]] = []
    for (provider, error_key), item in patterns.items():
        entries.append(
            {
                "provider": provider,
                "error": error_key,
                "count": int(item["count"]),
                "latest_at": item["latest_at"].isoformat() if item.get("latest_at") else None,
                "sample_orders": item.get("sample_orders") or [],
            }
        )

    entries.sort(key=lambda row: (-int(row.get("count") or 0), row.get("provider") or "", row.get("error") or ""))
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "since": since.isoformat(),
        "scanned_jobs": scanned,
        "failed_jobs": total_failed,
        "patterns": entries,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# OCR Reparse Failure Patterns",
        "",
        f"- generated_at: {payload.get('generated_at')}",
        f"- since: {payload.get('since')}",
        f"- scanned_jobs: {payload.get('scanned_jobs')}",
        f"- failed_jobs: {payload.get('failed_jobs')}",
        "",
        "| provider | error | count | latest_at | sample_orders |",
        "|---|---|---:|---|---|",
    ]
    patterns = payload.get("patterns") or []
    for row in patterns:
        provider = row.get("provider") or "-"
        error = row.get("error") or "-"
        count = int(row.get("count") or 0)
        latest_at = row.get("latest_at") or "-"
        sample_orders = ", ".join(row.get("sample_orders") or []) or "-"
        lines.append(f"| {provider} | {error} | {count} | {latest_at} | {sample_orders} |")
    if not patterns:
        lines.append("| - | - | 0 | - | - |")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export OCR reparse failure patterns.")
    parser.add_argument("--hours", type=int, default=168, help="Lookback window in hours (default: 168)")
    parser.add_argument("--limit", type=int, default=200, help="Max OCR jobs to scan (default: 200)")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument("--output", type=str, default="", help="Optional file path to write output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = collect_failures(hours=args.hours, limit=args.limit)
    if args.format == "markdown":
        text = render_markdown(payload)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
