#!/usr/bin/env python3
"""Import the facility master backup JSON into the canonical DB tables."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from src.db import session_scope  # noqa: E402
from src.models.facility import Facility, FacilityConfig  # noqa: E402
from src.services import facility_service  # noqa: E402


def _load_master(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        master = json.load(handle)
    if not isinstance(master, dict):
        raise SystemExit("facility master must be a JSON object")
    return master


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--master-path",
        default=str(BACKEND / "src" / "data" / "facility_master.template.json"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    master = _load_master(Path(args.master_path))
    facilities = master.get("facilities")
    if not isinstance(facilities, list):
        raise SystemExit("facility master does not contain facilities[]")
    facility_ids = [
        str(item.get("facility_id") or "").strip()
        for item in facilities
        if isinstance(item, dict) and str(item.get("facility_id") or "").strip()
    ]
    if not facility_ids:
        raise SystemExit("facility master does not contain any facility_id")

    with session_scope() as session:
        before_facilities = session.query(Facility).count()
        before_configs = session.query(FacilityConfig).count()
        if not args.dry_run:
            facility_service.upsert_facilities_and_configs_from_master(session, master)
            session.flush()
        after_facilities = session.query(Facility).count()
        after_configs = session.query(FacilityConfig).count()

    print(
        json.dumps(
            {
                "dry_run": args.dry_run,
                "master_facilities": len(facility_ids),
                "first_facility_id": facility_ids[0],
                "last_facility_id": facility_ids[-1],
                "before_facilities": before_facilities,
                "after_facilities": after_facilities,
                "before_configs": before_configs,
                "after_configs": after_configs,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
