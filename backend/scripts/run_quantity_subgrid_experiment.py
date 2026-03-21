from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
OCR_PIPELINE_ROOT = ROOT / "ocr_pipeline"
for candidate in (str(BACKEND_ROOT), str(OCR_PIPELINE_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from app.page_correction import correct_pdf_for_yomitoku
from app.pdf_render import render_pdf_to_page_images
from app.yomitoku_runner import run_yomitoku
from src.services.quantity_subgrid_experiment import (
    crop_image_by_norm_box,
    infer_quantity_subgrid,
    reread_suspicious_quantity_cells,
    save_debug_image,
)


DEFAULT_ORDER_IDS = [
    "ORDc767d2a1",
    "ORD032433a2",
    "ORD8931bb3e",
    "ORD1c0310d0",
    "ORD5333c097",
    "ORDfcd4bc37",
]


def _load_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        order_id = str(item.get("order_id") or "").strip()
        filename = str(item.get("file") or "").strip()
        if order_id and filename:
            mapping[order_id] = filename
    return mapping


def _find_pdf(pdf_root: Path, filename: str) -> Path | None:
    exact = pdf_root / filename
    if exact.exists():
        return exact
    matches = list(pdf_root.rglob(filename))
    return matches[0] if matches else None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_bytes(path: Path, payload: bytes | None) -> None:
    if isinstance(payload, (bytes, bytearray)) and payload:
        path.write_bytes(bytes(payload))


def _table_preview_rows(table: dict[str, Any] | None, limit: int = 12) -> list[list[str]]:
    if not isinstance(table, dict):
        return []
    rows = table.get("rows") or []
    return [list(row) for row in rows[:limit] if isinstance(row, list)]


def run_experiment(*, order_id: str, pdf_path: Path, output_dir: Path) -> dict[str, Any]:
    pdf_bytes = pdf_path.read_bytes()
    corrected_pdf, correction_summary, corrected_pages = correct_pdf_for_yomitoku(
        pdf_bytes=pdf_bytes,
        dpi=200,
        db=None,
    )
    full_results, full_ocr_pdf, full_layout_pdf = run_yomitoku(
        pdf_bytes=corrected_pdf,
        dpi=200,
        device="cpu",
        visualize=True,
        ignore_line_break=True,
        no_figure=True,
        figure_width=800,
        figure_dir="figures",
        page_images=corrected_pages or None,
    )
    full_table = full_results[0].tables[0] if full_results and full_results[0].tables else None
    spec = infer_quantity_subgrid(full_table or {})
    source_pages = corrected_pages or render_pdf_to_page_images(corrected_pdf, 200)
    result: dict[str, Any] = {
        "order_id": order_id,
        "pdf_path": str(pdf_path),
        "page_correction_applied": bool(correction_summary.get("applied")),
        "full_table_found": bool(full_table),
        "full_markdown_head": full_results[0].markdown_text.splitlines()[:20] if full_results else [],
        "full_table_head_rows": _table_preview_rows(full_table),
    }
    if source_pages:
        save_debug_image(str(output_dir / "corrected_page_1.png"), source_pages[0][1])
    _write_bytes(output_dir / "full_ocr_overlay.pdf", full_ocr_pdf)
    _write_bytes(output_dir / "full_layout_overlay.pdf", full_layout_pdf)
    if not full_table or spec is None or not source_pages:
        result["quantity_subgrid_found"] = False
        return result

    crop = crop_image_by_norm_box(source_pages[0][1], spec.crop_box_norm, padding_px=4)
    save_debug_image(str(output_dir / "quantity_crop.png"), crop)
    sub_results, sub_ocr_pdf, sub_layout_pdf = run_yomitoku(
        pdf_bytes=None,
        dpi=200,
        device="cpu",
        visualize=True,
        ignore_line_break=True,
        no_figure=True,
        figure_width=800,
        figure_dir="figures",
        page_images=[(1, crop)],
    )
    _write_bytes(output_dir / "quantity_ocr_overlay.pdf", sub_ocr_pdf)
    _write_bytes(output_dir / "quantity_layout_overlay.pdf", sub_layout_pdf)
    sub_table = sub_results[0].tables[0] if sub_results and sub_results[0].tables else None
    result["quantity_subgrid_found"] = True
    result["quantity_subgrid_spec"] = {
        "body_start_row": spec.body_start_row,
        "menu_col_index": spec.menu_col_index,
        "quantity_start_col_index": spec.quantity_start_col_index,
        "crop_box_norm": spec.crop_box_norm,
        "row_count": spec.row_count,
        "quantity_col_count": spec.quantity_col_count,
    }
    result["quantity_markdown_head"] = sub_results[0].markdown_text.splitlines()[:20] if sub_results else []
    result["quantity_table_head_rows"] = _table_preview_rows(sub_table)
    improved_rows, digit_patches = reread_suspicious_quantity_cells(
        quantity_crop_bgr=crop,
        quantity_table=sub_table or {},
        dpi=200,
    )
    result["digit_reread_patch_count"] = len(digit_patches)
    result["digit_reread_patches"] = [
        {
            "row_index": patch.row_index,
            "col_index": patch.col_index,
            "original_text": patch.original_text,
            "replacement_text": patch.replacement_text,
            "variant_name": patch.variant_name,
            "score": patch.score,
            "candidates": patch.candidates,
        }
        for patch in digit_patches
    ]
    result["quantity_table_head_rows_after_digit_reread"] = improved_rows[:12]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf-root",
        default="/tmp/fax260322/発注書260322",
        help="Directory containing the extracted 3/22 PDF files",
    )
    parser.add_argument(
        "--mapping-json",
        default="/tmp/sawa_260322_ocr_status_summary.json",
        help="order_id -> filename mapping JSON",
    )
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_ROOT / "tmp" / "quantity_subgrid_experiment"),
        help="Directory to write experiment artifacts",
    )
    parser.add_argument(
        "--order-id",
        action="append",
        dest="order_ids",
        help="Target order_id. Repeat to override defaults.",
    )
    args = parser.parse_args()

    pdf_root = Path(args.pdf_root)
    mapping = _load_mapping(Path(args.mapping_json))
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    order_ids = args.order_ids or list(DEFAULT_ORDER_IDS)
    summary: list[dict[str, Any]] = []

    for order_id in order_ids:
        filename = mapping.get(order_id)
        if not filename:
            summary.append({"order_id": order_id, "error": "mapping_not_found"})
            continue
        pdf_path = _find_pdf(pdf_root, filename)
        if pdf_path is None:
            summary.append({"order_id": order_id, "file": filename, "error": "pdf_not_found"})
            continue
        case_dir = output_root / order_id
        case_dir.mkdir(parents=True, exist_ok=True)
        result = run_experiment(order_id=order_id, pdf_path=pdf_path, output_dir=case_dir)
        result["file"] = filename
        _write_json(case_dir / "result.json", result)
        summary.append(result)
        print(order_id, "quantity_subgrid_found=", result.get("quantity_subgrid_found"))
        print("  full:", result.get("full_table_head_rows", [])[:5])
        print("  qty :", result.get("quantity_table_head_rows", [])[:5])

    _write_json(output_root / "summary.json", summary)
    print("summary:", output_root / "summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
