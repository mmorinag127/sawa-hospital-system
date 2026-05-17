#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.services.order_pipeline_lineage_audit_service import audit_order_pipeline_lineage  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only audit for workflow-v2 order pipeline lineage mismatches."
    )
    parser.add_argument("--order-id", default="", help="Audit one order id. Defaults to all orders.")
    parser.add_argument("--limit", type=int, default=0, help="Limit audited orders when --order-id is not set.")
    parser.add_argument("--summary-only", action="store_true", help="Omit per-issue rows from stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = audit_order_pipeline_lineage(
        order_id=str(args.order_id or "").strip() or None,
        limit=args.limit if args.limit > 0 else None,
    )
    if args.summary_only:
        result = {key: value for key, value in result.items() if key != "issues"}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
