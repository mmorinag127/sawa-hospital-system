#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OCR_PIPELINE_ROOT = ROOT.parent / "ocr_pipeline"
for candidate in (str(ROOT), str(OCR_PIPELINE_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from app.yomitoku_runner import run_yomitoku
from src.services import config_service, order_form_service
from src.services.structure_guided_ocr import select_primary_table
from src.services.workbook_pdf_renderer import render_workbook_path_to_pdf


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_target_facility_ids(explicit_ids: list[str] | None) -> list[str]:
    if explicit_ids:
        return explicit_ids
    master = config_service.load_facility_master()
    facilities = master.get("facilities") if isinstance(master, dict) else None
    if not isinstance(facilities, list):
        return []
    facility_ids: list[str] = []
    for item in facilities:
        if not isinstance(item, dict):
            continue
        facility_id = str(item.get("facility_id") or "").strip()
        if not facility_id or not facility_id.startswith("FAC000"):
            continue
        facility_ids.append(facility_id)
    return facility_ids


def _column_bounds(table: dict[str, Any]) -> dict[int, list[float]]:
    bounds: dict[int, list[float]] = {}
    for cell in table.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        col_index = cell.get("col_index")
        bbox = cell.get("bbox")
        if not isinstance(col_index, int) or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            x0, y0, x1, y1 = [float(item) for item in bbox]
        except Exception:
            continue
        current = bounds.get(col_index)
        if current is None:
            bounds[col_index] = [x0, y0, x1, y1]
            continue
        current[0] = min(current[0], x0)
        current[1] = min(current[1], y0)
        current[2] = max(current[2], x1)
        current[3] = max(current[3], y1)
    return bounds


def _x_overlap(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def _x_center(bbox: list[float]) -> float:
    return (bbox[0] + bbox[2]) / 2.0


def _best_template_col(
    default_bbox: list[float],
    template_bounds: dict[int, list[float]],
) -> tuple[int | None, float, float]:
    best_col: int | None = None
    best_overlap = -1.0
    best_center_delta = float("inf")
    for template_col, template_bbox in template_bounds.items():
        overlap = _x_overlap(default_bbox, template_bbox)
        center_delta = abs(_x_center(default_bbox) - _x_center(template_bbox))
        if overlap > best_overlap or (abs(overlap - best_overlap) <= 1e-9 and center_delta < best_center_delta):
            best_col = template_col
            best_overlap = overlap
            best_center_delta = center_delta
    return best_col, max(0.0, best_overlap), best_center_delta


def _header_texts(table: dict[str, Any], *, limit: int = 2) -> list[list[str]]:
    rows = table.get("rows") or []
    return [list(row) for row in rows[:limit] if isinstance(row, list)]


def _compare_tables(
    *,
    default_table: dict[str, Any],
    template_table: dict[str, Any],
) -> dict[str, Any]:
    default_bounds = _column_bounds(default_table)
    template_bounds = _column_bounds(template_table)
    mappings: list[dict[str, Any]] = []
    identity_match_count = 0
    for default_col, default_bbox in sorted(default_bounds.items()):
        template_col, overlap, center_delta = _best_template_col(default_bbox, template_bounds)
        is_identity = template_col == default_col
        if is_identity:
            identity_match_count += 1
        mappings.append(
            {
                "default_col": default_col,
                "template_col": template_col,
                "is_identity": is_identity,
                "x_overlap": overlap,
                "center_delta": center_delta,
                "default_bbox": default_bbox,
                "template_bbox": template_bounds.get(template_col) if template_col is not None else None,
            }
        )
    shifted = [item for item in mappings if not item["is_identity"]]
    return {
        "default_col_count": len(default_bounds),
        "template_col_count": len(template_bounds),
        "identity_match_count": identity_match_count,
        "shifted_col_count": len(shifted),
        "has_shift": bool(shifted),
        "mappings": mappings,
        "default_headers": _header_texts(default_table),
        "template_headers": _header_texts(template_table),
    }


def _run_table_from_pdf(
    *,
    pdf_bytes: bytes,
    dpi: int,
    device: str,
) -> dict[str, Any]:
    page_results, _ocr_pdf, _layout_pdf = run_yomitoku(
        pdf_bytes=pdf_bytes,
        dpi=dpi,
        device=device,
        visualize=False,
        ignore_line_break=True,
        no_figure=True,
        figure_width=800,
        figure_dir="figures",
    )
    table = select_primary_table(page_results[0].tables if page_results else [])
    if not isinstance(table, dict):
        raise RuntimeError("primary table not found")
    return table


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch-check column drift of default yomitoku table output against structure-only templates.",
    )
    parser.add_argument("--week", required=True, help="Week sheet name, e.g. 4月26日～4月30日")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--facility-id",
        dest="facility_ids",
        action="append",
        default=None,
        help="Repeat to restrict to specific facilities.",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir or (ROOT / "tmp" / f"column_drift_{stamp}"))
    output_dir.mkdir(parents=True, exist_ok=True)

    facility_ids = _load_target_facility_ids(args.facility_ids)
    results: list[dict[str, Any]] = []

    for facility_id in facility_ids:
        facility_output_dir = output_dir / facility_id
        facility_output_dir.mkdir(parents=True, exist_ok=True)
        try:
            facility = config_service.get_facility_config(facility_id)
            if not facility:
                raise ValueError("facility not found")

            order_form_xlsx = order_form_service.build_fax_order_form_excel(
                facility_id=facility_id,
                week_sheet_name=args.week,
                output_dir=facility_output_dir,
            )
            order_form_pdf = facility_output_dir / f"{order_form_xlsx.stem}.pdf"
            render_workbook_path_to_pdf(
                order_form_xlsx,
                output_path=order_form_pdf,
                sheet_name=args.week,
                dpi=args.dpi,
            )

            structure_xlsx = order_form_service.build_fax_structure_only_excel(
                facility_id=facility_id,
                week_sheet_name=args.week,
                output_dir=facility_output_dir,
            )
            structure_pdf = facility_output_dir / f"{structure_xlsx.stem}.pdf"
            render_workbook_path_to_pdf(
                structure_xlsx,
                output_path=structure_pdf,
                sheet_name=args.week,
                dpi=args.dpi,
            )

            default_table = _run_table_from_pdf(
                pdf_bytes=order_form_pdf.read_bytes(),
                dpi=args.dpi,
                device=args.device,
            )
            template_table = _run_table_from_pdf(
                pdf_bytes=structure_pdf.read_bytes(),
                dpi=args.dpi,
                device=args.device,
            )
            comparison = _compare_tables(default_table=default_table, template_table=template_table)
            result = {
                "facility_id": facility_id,
                "facility_name": str(facility.get("facility_name") or facility.get("name") or facility_id),
                "week": args.week,
                "artifacts": {
                    "order_form_xlsx": str(order_form_xlsx),
                    "order_form_pdf": str(order_form_pdf),
                    "structure_xlsx": str(structure_xlsx),
                    "structure_pdf": str(structure_pdf),
                },
                "comparison": comparison,
            }
            _write_json(facility_output_dir / "result.json", result)
            results.append(result)
            print(
                f"{facility_id}: has_shift={comparison['has_shift']} "
                f"identity={comparison['identity_match_count']}/{comparison['default_col_count']}"
            )
        except Exception as exc:  # noqa: BLE001
            error_result = {
                "facility_id": facility_id,
                "week": args.week,
                "error": str(exc),
            }
            _write_json(facility_output_dir / "result.json", error_result)
            results.append(error_result)
            print(f"{facility_id}: error={exc}")

    successful = [item for item in results if not item.get("error")]
    shifted = [
        {
            "facility_id": item["facility_id"],
            "facility_name": item.get("facility_name"),
            "shifted_col_count": item["comparison"]["shifted_col_count"],
            "identity_match_count": item["comparison"]["identity_match_count"],
            "default_col_count": item["comparison"]["default_col_count"],
            "shifted_mappings": [
                {
                    "default_col": mapping["default_col"],
                    "template_col": mapping["template_col"],
                    "x_overlap": mapping["x_overlap"],
                }
                for mapping in item["comparison"]["mappings"]
                if not mapping["is_identity"]
            ],
        }
        for item in successful
        if item["comparison"]["has_shift"]
    ]
    summary = {
        "week": args.week,
        "facility_count": len(facility_ids),
        "success_count": len(successful),
        "error_count": len([item for item in results if item.get("error")]),
        "shift_count": len(shifted),
        "shifted_facilities": shifted,
        "results": results,
    }
    _write_json(output_dir / "summary.json", summary)
    print(f"output_dir={output_dir}")
    print(f"summary={output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
