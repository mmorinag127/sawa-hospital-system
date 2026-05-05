#!/usr/bin/env python3
"""Backfill facility-template-version lineage for existing orders.

The schema migration only adds columns and constraints. This command stamps
existing order artifacts with the active facility template version when the
order already has an explicit facility and that facility has a resolvable
template configuration.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.db import session_scope  # noqa: E402
from src.services import facility_template_version_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--actor", default="facility-template-version-backfill")
    args = parser.parse_args()

    with session_scope() as session:
        summary = facility_template_version_service.backfill_facility_template_version_lineage(
            session,
            dry_run=bool(args.dry_run),
            actor=str(args.actor or "").strip() or "facility-template-version-backfill",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
