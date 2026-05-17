from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.services.order_pipeline_lineage_repair_service import (  # noqa: E402
    APPLY_CONFIRMATION_TOKEN,
    backfill_step4_output_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill workflow-v2 Step4 payloads into artifact tables.")
    parser.add_argument("--order-id", default=None, help="Optional order id to backfill.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum workflow rows to inspect.")
    parser.add_argument("--summary-only", action="store_true", help="Only print summary counts.")
    parser.add_argument("--apply", action="store_true", help="Write repairable backfill plans.")
    parser.add_argument(
        "--confirm",
        default=None,
        help=f"Required with --apply. Use {APPLY_CONFIRMATION_TOKEN}.",
    )
    parser.add_argument("--actor", default="system", help="Actor recorded in audit_logs when --apply writes.")
    parser.add_argument("--reason", default="step4_artifact_backfill", help="Repair reason recorded in audit_logs.")
    parser.add_argument("--idempotency-key", default=None, help="Optional idempotency key recorded in audit_logs.")
    args = parser.parse_args()

    result = backfill_step4_output_artifacts(
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
