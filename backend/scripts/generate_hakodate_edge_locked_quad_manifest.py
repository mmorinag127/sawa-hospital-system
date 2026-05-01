#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "backend"))

from src.services.hakodate_fixed_quad_registration_service import estimate_edge_locked_quad_from_pdf  # noqa: E402


DEFAULT_BASE = WORKSPACE / "tmp" / "outer_quad_eval_correct_20260426"
DEFAULT_INPUT_MANIFEST = DEFAULT_BASE / "stg_week_2026-04-26_2026-04-30_manifest.local.json"
DEFAULT_OUTPUT_DIR = DEFAULT_BASE / "quad_v4_edge_locked_pipeline_integrated_20260429"


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        rows = payload.get("results") or payload.get("items") or []
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError(f"manifest rows are not a list: {path}")
    return [row for row in rows if isinstance(row, dict)]


def build_quad_results(*, input_manifest: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _load_manifest(input_manifest)
    results: list[dict[str, Any]] = []
    for row in rows:
        pdf_path = Path(str(row.get("local_pdf") or row.get("fax_pdf") or ""))
        if not pdf_path.exists():
            raise ValueError(f"fax pdf not found for {row.get('facility_code')} {row.get('id') or row.get('order_id')}: {pdf_path}")
        estimate = estimate_edge_locked_quad_from_pdf(pdf_path, dpi=220)
        results.append(
            {
                **row,
                **estimate,
                "overlay": str(output_dir / f"{row.get('facility_code')}_{row.get('id') or row.get('order_id')}" / "quad_overlay.png"),
            }
        )
    json_path = output_dir / "quad_results.json"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "count": len(results),
        "ok": sum(result.get("status") == "ok" for result in results),
        "ng": sum(result.get("status") == "ng" for result in results),
        "error": sum(result.get("status") == "error" for result in results),
        "json": str(json_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-manifest", type=Path, default=DEFAULT_INPUT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    print(json.dumps(build_quad_results(input_manifest=args.input_manifest, output_dir=args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
