#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

if sys.version_info < (3, 11):
    sys.stderr.write("cleanup_order_operational_data.py requires Python 3.11+. Use `uv run python ...`.\n")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.services.order_operational_cleanup_service import (  # noqa: E402
    CleanupScope,
    apply_order_cleanup,
    build_order_cleanup_plan,
    export_order_pdfs_for_cleanup,
    read_order_cleanup_plan,
)


def _parse_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    token = raw.strip()
    if not token:
        return None
    return date.fromisoformat(token)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run by default. Freeze reviewed order rows before cleanup. "
            "Saved histories, originals and facility/menu/template configuration are retained."
        )
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all-orders", action="store_true", help="Target every order row.")
    scope.add_argument("--received-from", help="Target orders received on or after YYYY-MM-DD.")
    parser.add_argument("--received-to", help="Target orders received on or before YYYY-MM-DD.")
    parser.add_argument("--export-pdfs-dir", help="Copy target order PDFs before cleanup and write manifest.json.")
    parser.add_argument("--apply", action="store_true", help="Actually delete targeted order data.")
    parser.add_argument('--plan-file', help='Previously reviewed plan.sqlite from a dry run.')
    parser.add_argument('--plan-sha256', help='SHA256 printed by that dry run.')
    parser.add_argument(
        "--confirm",
        default="",
        help="Required with --apply. Must be CLEAN_ORDER_DATA.",
    )
    args = parser.parse_args()
    if bool(args.plan_file) != bool(args.plan_sha256) or (args.apply and not args.plan_file):
        parser.error('--apply requires the reviewed --plan-file and --plan-sha256; run a dry run first')
    return args


def main() -> int:
    args = parse_args()
    cleanup_scope = CleanupScope(
        all_orders=bool(args.all_orders),
        received_from=_parse_date(args.received_from),
        received_to=_parse_date(args.received_to),
    )

    plan = read_order_cleanup_plan(args.plan_file, args.plan_sha256) if args.plan_file else build_order_cleanup_plan(cleanup_scope)
    result: dict[str, object] = {
        "mode": "apply" if args.apply else "dry_run",
        "cleanup_plan": plan,
    }

    if args.export_pdfs_dir:
        result["pdf_export"] = export_order_pdfs_for_cleanup(
            scope=cleanup_scope,
            output_dir=args.export_pdfs_dir,
            plan_path=plan['manifest']['path'], expected_plan_sha256=plan['manifest']['sha256'],
        )

    if args.apply:
        result["cleanup_result"] = apply_order_cleanup(
            cleanup_scope,
            confirm_token=str(args.confirm or ""),
            plan_path=plan['manifest']['path'], expected_plan_sha256=plan['manifest']['sha256'],
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
