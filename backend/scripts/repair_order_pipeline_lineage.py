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
    repair_confirmed_snapshot_payloads,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair workflow-v2 lineage gaps from confirmed snapshots.")
    parser.add_argument("--order-id", required=True, help="Order id to inspect or repair.")
    parser.add_argument("--apply", action="store_true", help="Write the repair when the plan is repairable.")
    parser.add_argument("--actor", default="system", help="Actor recorded in audit_logs when --apply writes.")
    parser.add_argument("--reason", default=None, help="Repair reason recorded in audit_logs when --apply writes.")
    parser.add_argument(
        "--idempotency-key",
        default=None,
        help="Optional idempotency key recorded in audit_logs when --apply writes.",
    )
    parser.add_argument(
        "--confirm",
        default=None,
        help=f"Required with --apply. Use {APPLY_CONFIRMATION_TOKEN}.",
    )
    args = parser.parse_args()

    result = repair_confirmed_snapshot_payloads(
        order_id=args.order_id,
        apply=args.apply,
        confirm=args.confirm,
        actor=args.actor,
        reason=args.reason,
        idempotency_key=args.idempotency_key,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["plan"]["status"] == "blocked":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
