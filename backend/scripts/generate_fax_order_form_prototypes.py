#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from src.services import order_form_service  # noqa: E402


DEFAULT_WEEK = "3月22日～3月28日"
DEFAULT_FACILITY_IDS = [
    "FAC00002",
    "FAC00003",
    "FAC00006",
    "FAC00014",
    "FAC00016",
]


def _build_output_dir(output_dir: str | None) -> Path:
    if output_dir:
        path = Path(output_dir)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path("/tmp/fax-order-form-prototypes") / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate fax-ready order-form base templates and facility samples.",
    )
    parser.add_argument("--week", default=DEFAULT_WEEK, help="Week sheet name to extract.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for generated workbooks. Defaults to /tmp/fax-order-form-prototypes/<timestamp>.",
    )
    parser.add_argument(
        "--facility-id",
        dest="facility_ids",
        action="append",
        default=None,
        help="Facility ID to generate. Repeat to override the default sample set.",
    )
    args = parser.parse_args()

    facility_ids = args.facility_ids or list(DEFAULT_FACILITY_IDS)
    output_dir = _build_output_dir(args.output_dir)

    generated_base_paths: list[Path] = []
    seen_template_ids: set[str] = set()
    for facility_id in facility_ids:
        facility = order_form_service.config_service.get_facility_config(facility_id)
        if not facility:
            raise ValueError(f"facility not found: {facility_id}")
        fax_template_id = str(facility.get("fax_template_id") or "").strip()
        if not fax_template_id:
            raise ValueError(f"facility fax_template_id not found: {facility_id}")
        if fax_template_id in seen_template_ids:
            continue
        seen_template_ids.add(fax_template_id)
        generated_base_paths.append(
            order_form_service.build_fax_base_template_excel(
                fax_template_id=fax_template_id,
                week_sheet_name=args.week,
                output_dir=output_dir,
            )
        )

    generated_facility_paths = [
        order_form_service.build_fax_order_form_excel(
            facility_id=facility_id,
            week_sheet_name=args.week,
            output_dir=output_dir,
        )
        for facility_id in facility_ids
    ]

    print(f"output_dir={output_dir}")
    for path in generated_base_paths:
        print(f"base={path}")
    for path in generated_facility_paths:
        print(f"facility={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
