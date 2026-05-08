#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from src.hakodate_best_method_runtime.run_text_recognizer_trial import DEFAULT_MANIFEST
from src.services.hakodate_cell_ocr_batch_service import build_hakodate_best_method_for_manifest_item
from src.services.hakodate_step_review_pipeline_service import (
    _write_pdf_from_pages,
    build_all_facility_hakodate_step_review_pdfs,
)


def _build_best_method_overlay_pdf(
    *,
    manifest_path: Path,
    output_dir: Path,
    render_width: int,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("results") if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        raise ValueError("manifest results are missing")
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    results: list[dict[str, Any]] = []
    empty_draft_sheet = {"fields": [], "rows": []}
    for page, item in enumerate(items, start=1):
        result, review_page = build_hakodate_best_method_for_manifest_item(
            item=item,
            page=page,
            draft_sheet=empty_draft_sheet,
            output_dir=output_dir,
            render_width=render_width,
        )
        pages.append(review_page)
        results.append(asdict(result))
    pdf_path = output_dir / "best_method_overlay_all_facilities.pdf"
    _write_pdf_from_pages(pages, pdf_path)
    summary = {
        "count": len(results),
        "pdf": str(pdf_path),
        "results": results,
    }
    (output_dir / "best_method_overlay_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Hakodate visual verification PDFs from the local manifest.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--render-width", type=int, default=1864)
    parser.add_argument("--skip-ocr", action="store_true")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    step_summary = build_all_facility_hakodate_step_review_pdfs(
        manifest_path=args.manifest,
        output_dir=args.output_dir / "step_review",
        render_width=args.render_width,
    )
    result: dict[str, object] = {"step_review": step_summary}
    if not args.skip_ocr:
        result["best_method_overlay"] = _build_best_method_overlay_pdf(
            manifest_path=args.manifest,
            output_dir=args.output_dir / "best_method_overlay",
            render_width=args.render_width,
        )
    summary_path = args.output_dir / "visual_verification_summary.json"
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": str(summary_path), **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
