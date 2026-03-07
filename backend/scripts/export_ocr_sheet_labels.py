#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from src.services import order_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export current OCR sheet labels for one or more order IDs.",
    )
    parser.add_argument(
        "order_ids",
        nargs="+",
        help="Order IDs to export, for example ORD123 ORD456",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "tests" / "fixtures" / "ocr_sheet_corpus" / "manual_labels"),
        help="Directory to write <order_id>.expected_sheet.json files into.",
    )
    parser.add_argument(
        "--summary-json",
        default="",
        help="Optional path to write an export summary JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    results: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    for order_id in args.order_ids:
        exported, error = order_service.export_ocr_sheet_label(
            order_id,
            output_dir=output_dir,
        )
        if error:
            errors.append({"order_id": order_id, "error": error})
            continue
        if not isinstance(exported, dict):
            errors.append({"order_id": order_id, "error": "export_failed"})
            continue
        results.append(
            {
                "order_id": str(exported.get("order_id") or order_id),
                "output_path": str(exported.get("output_path") or ""),
            }
        )

    summary = {
        "output_dir": str(output_dir),
        "exported": results,
        "errors": errors,
    }

    if args.summary_json:
        Path(args.summary_json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
