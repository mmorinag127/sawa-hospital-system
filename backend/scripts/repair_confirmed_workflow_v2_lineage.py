#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.order_pipeline_lineage_repair_service import (  # noqa: E402
    APPLY_CONFIRMATION_TOKEN,
    repair_confirmed_workflow_v2_lineage,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair confirmed workflow-v2 lineage rows projected through legacy workflow state.")
    parser.add_argument("--order-id", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default=None, help=f"Required with --apply. Use {APPLY_CONFIRMATION_TOKEN}.")
    parser.add_argument("--actor", default="system")
    parser.add_argument("--reason", default="confirmed_workflow_v2_lineage_repair")
    parser.add_argument("--idempotency-key", default=None)
    args = parser.parse_args()
    result = repair_confirmed_workflow_v2_lineage(
        order_id=args.order_id,
        limit=args.limit,
        apply=args.apply,
        confirm=args.confirm,
        actor=args.actor,
        reason=args.reason,
        idempotency_key=args.idempotency_key,
    )
    if args.summary_only:
        result = {
            "mode": result["mode"],
            "applied": result["applied"],
            "status": result["status"],
            "reason": result["reason"],
            "summary": result["summary"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if result.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
