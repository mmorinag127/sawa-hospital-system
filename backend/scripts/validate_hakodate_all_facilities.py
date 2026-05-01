#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from urllib.error import HTTPError, URLError
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
RUNTIME_DIR = BACKEND_ROOT / "src" / "hakodate_best_method_runtime"
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(RUNTIME_DIR))

import compare_kasuga_digit_preprocess_methods as cmp  # noqa: E402
from src.services import order_form_service  # noqa: E402
from src.services.hakodate_fixed_quad_registration_service import (  # noqa: E402
    build_fixed_quad_template_registration,
    render_pdf_page_to_bgr,
)
from src.services import hakodate_ocr_evidence_service  # noqa: E402
from src.services.order_service import (  # noqa: E402
    _estimate_hakodate_template_bbox_from_rendered_image,
    _week_sheet_name_from_week_value,
)
from src.services.storage_service import load_bytes_from_uri  # noqa: E402
from src.services.workbook_pdf_renderer import render_workbook_path_to_pdf  # noqa: E402
from src.hakodate_best_method_runtime.render_best_method_overlay_all_facilities import (  # noqa: E402
    build_best_method_for_manifest_item,
)


DEFAULT_OUTPUT_DIR = WORKSPACE_ROOT / "tmp" / "hakodate_all_facilities_validation_20260501"
DEFAULT_DRAFT_CACHE_DIR = RUNTIME_DIR / "best_method_overlay_all_facilities" / "draft_sheets"
TEMPLATE_SNAPSHOT_REL = (
    "tmp/outer_quad_eval_correct_20260426/"
    "preprocess_v10_template_snap_real_orders_20260425_0430/templates"
)


def _json_get(url: str, auth_header: str, *, timeout: int = 120, retries: int = 4) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Authorization": auth_header})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= retries:
                raise
        except (TimeoutError, URLError):
            if attempt >= retries:
                raise
        time.sleep(min(90, 10 * (attempt + 1)))
    raise RuntimeError(f"unreachable retry state: {url}")


def _load_manifest_items(manifest_path: Path) -> list[tuple[int, dict[str, Any]]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("results") if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        raise ValueError(f"manifest results are missing: {manifest_path}")
    return [(index, item) for index, item in enumerate(items, start=1) if isinstance(item, dict)]


def _load_or_fetch_draft_sheet(order_id: str, auth_header: str, output_dir: Path) -> dict[str, Any]:
    out = output_dir / "draft_sheets" / f"{order_id}_draft_sheet.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    payload = _json_get(f"{cmp.STG_API_BASE}/orders/{order_id}/draft-sheet", auth_header)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _load_uri_bytes_for_validation(uri: str) -> bytes:
    try:
        return load_bytes_from_uri(uri)
    except Exception:
        if uri.startswith("gs://"):
            return subprocess.check_output(["gcloud", "storage", "cat", uri])
        raise


def _find_packaged_template_pdf(facility_id: str, week_sheet_name: str) -> Path | None:
    filename = f"{facility_id}_{week_sheet_name}.pdf"
    candidates = [
        BACKEND_ROOT / TEMPLATE_SNAPSHOT_REL / filename,
        *sorted(
            (WORKSPACE_ROOT.parent / "integration").glob(
                f"backend-deploy-*/backend/{TEMPLATE_SNAPSHOT_REL}/{filename}"
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _build_live_manifest_item_from_stg_order(
    *,
    order_id: str,
    auth_header: str,
    output_dir: Path,
    render_width: int,
    http_timeout: int,
) -> dict[str, Any]:
    order = _json_get(f"{cmp.STG_API_BASE}/orders/{order_id}", auth_header, timeout=http_timeout)
    facility_id = str(order.get("facility") or order.get("facility_code") or "").strip()
    document_uri = str(order.get("document") or order.get("document_uri") or "").strip()
    week_value = str(order.get("week_value") or order.get("week") or "").strip()
    if not facility_id:
        raise ValueError(f"facility missing for {order_id}")
    if not document_uri:
        raise ValueError(f"document missing for {order_id}")
    week_sheet_name = _week_sheet_name_from_week_value(week_value) or week_value
    if not week_sheet_name:
        raise ValueError(f"week unresolved for {order_id}")

    live_dir = output_dir / "_live_inputs" / order_id
    live_dir.mkdir(parents=True, exist_ok=True)
    fax_pdf = live_dir / f"{order_id}_fax.pdf"
    if not fax_pdf.exists():
        fax_pdf.write_bytes(_load_uri_bytes_for_validation(document_uri))

    packaged_template = _find_packaged_template_pdf(facility_id, week_sheet_name)
    if packaged_template is not None:
        structure_pdf = packaged_template
    else:
        structure_xlsx = order_form_service.build_fax_structure_only_excel(
            facility_id=facility_id,
            week_sheet_name=week_sheet_name,
            output_dir=live_dir,
        )
        structure_pdf = live_dir / f"{structure_xlsx.stem}.pdf"
        if not structure_pdf.exists():
            render_workbook_path_to_pdf(
                structure_xlsx,
                output_path=structure_pdf,
                sheet_name=week_sheet_name,
            )

    accepted_canvas_width = 2362
    accepted_canvas_height = 4273
    template_image = render_pdf_page_to_bgr(str(structure_pdf), width=accepted_canvas_width)
    template_bbox = _estimate_hakodate_template_bbox_from_rendered_image(template_image)
    registration_dir = live_dir / "fixed_quad"
    registration, _images = build_fixed_quad_template_registration(
        facility_code=facility_id,
        order_id=order_id,
        fax_pdf=str(fax_pdf),
        template_pdf=str(structure_pdf),
        quad_px=None,
        manifest_template_bbox=template_bbox,
        canvas_width=accepted_canvas_width,
        canvas_height=accepted_canvas_height,
        render_width=render_width,
        quad_source=None,
        output_dir=registration_dir,
    )
    return {
        "order_id": order_id,
        "facility_code": facility_id,
        "facility_id": facility_id,
        "fax_pdf": str(fax_pdf),
        "template_pdf": str(structure_pdf),
        "step2_png": str(Path(registration.outputs["step2"])),
        "template_bbox": template_bbox,
        "quad_px": registration.quad_px,
        "quad_source": registration.quad_source,
        "week_sheet_name": week_sheet_name,
        "source": "live_order_facility_source_workbook",
    }


def _cell_value_map_from_local_records(records_path: Path) -> dict[str, str]:
    records = json.loads(records_path.read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    if not isinstance(records, list):
        return result
    for record in records:
        if not isinstance(record, dict):
            continue
        sheet_cell = str(record.get("sheet_cell") or "").strip()
        if not sheet_cell:
            continue
        value = str(
            record.get("pred_digits")
            or record.get("ocr_normalized")
            or record.get("ocr_text")
            or ""
        ).strip()
        if value:
            result[sheet_cell] = value
    return result


def _cell_value_map_from_local_artifacts(
    *,
    order_id: str,
    records_path: Path,
    regions_path: Path,
) -> dict[str, str]:
    if not records_path.exists() or not regions_path.exists():
        return _cell_value_map_from_local_records(records_path)
    records = json.loads(records_path.read_text(encoding="utf-8"))
    regions = json.loads(regions_path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not isinstance(regions, list):
        return _cell_value_map_from_local_records(records_path)
    canonical_regions = [dict(item) for item in regions if isinstance(item, dict)]
    raw_evidence_records: list[dict[str, Any]] = []
    for index, region in enumerate(item for item in records if isinstance(item, dict)):
        accepted_candidate = region.get("recognizer_accepted_candidate")
        accepted_candidate = accepted_candidate if isinstance(accepted_candidate, dict) else {}
        accepted_candidate_digits = str(accepted_candidate.get("normalized_digits") or "").strip()
        raw_text = str(
            accepted_candidate_digits
            or region.get("ocr_normalized")
            or region.get("ocr_text")
            or region.get("raw_text")
            or region.get("pred_digits")
            or ""
        ).strip()
        if not raw_text:
            continue
        confidence_source = (
            accepted_candidate.get("score")
            if str(region.get("recognizer_decision_source") or "").strip() == "topk_digits"
            else region.get("recognizer_score")
        )
        if confidence_source is None:
            confidence_source = region.get("score")
        try:
            confidence = float(confidence_source) if confidence_source is not None else None
        except Exception:
            confidence = None
        box = region.get("bbox")
        center = None
        if isinstance(box, list) and len(box) == 4:
            try:
                center = [(float(box[0]) + float(box[2])) / 2.0, (float(box[1]) + float(box[3])) / 2.0]
            except Exception:
                center = None
        raw_evidence_records.append(
            {
                "text": raw_text,
                "normalized_value": hakodate_ocr_evidence_service.normalize_ocr_value(raw_text),
                "bbox": box,
                "center": center,
                "confidence": confidence,
                "evidence_id": f"hakodate-cell-{index}",
                "engine_metadata": {
                    "source_region_id": region.get("region_id"),
                    "sheet_cell": region.get("sheet_cell"),
                    "ocr_contact_slot_index": region.get("ocr_contact_slot_index"),
                    "source_artifact": "best_method_records",
                    "recognizer_score": region.get("recognizer_score"),
                    "recognizer_decision_source": region.get("recognizer_decision_source"),
                    "recognizer_accepted_candidate": accepted_candidate or None,
                },
            }
        )
    target_cells = hakodate_ocr_evidence_service.target_cells_from_regions(canonical_regions)
    evidence_records = hakodate_ocr_evidence_service.evidence_from_records(
        raw_evidence_records,
        run_id=f"{order_id}:hakodate-best-method",
        engine="opencv_knn_leave_one_out_k5",
        source_scope="hakodate_cell_crop_batch",
        raw_payload_ref=str(regions_path),
    )
    assignment = hakodate_ocr_evidence_service.assign_evidence_to_target_cells(
        evidence_records=evidence_records,
        target_cells=target_cells,
    )
    return _cell_value_map_from_assignment(assignment)


def _cell_value_map_from_assignment(assignment: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    items = assignment.get("assignments") if isinstance(assignment, dict) else None
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        sheet_cell = str(item.get("sheet_cell") or "").strip()
        value = str(item.get("assigned_value") or item.get("value_normalized") or "").strip()
        if sheet_cell and value:
            result[sheet_cell] = value
    return result


def _confidence_is_strictly_accepted(confidence: object) -> bool:
    if confidence is None:
        return True
    try:
        return float(confidence) >= 0.45
    except Exception:
        return True


def _accepted_cell_value_map_from_assignment(assignment: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    sheet_output = assignment.get("sheet_output") if isinstance(assignment, dict) else {}
    cells = sheet_output.get("cells") if isinstance(sheet_output, dict) else {}
    if not isinstance(cells, dict):
        return result
    for sheet_cell, cell in cells.items():
        if not isinstance(cell, dict):
            continue
        key = str(sheet_cell or cell.get("sheet_cell") or "").strip()
        value = str(cell.get("value_normalized") or cell.get("value_text") or "").strip()
        if key and value and _confidence_is_strictly_accepted(cell.get("assignment_confidence")):
            result[key] = value
    return result


def _target_truth_by_sheet_cell(assignment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    targets = assignment.get("target_cells") if isinstance(assignment, dict) else None
    if not isinstance(targets, list):
        return result
    for target in targets:
        if not isinstance(target, dict):
            continue
        sheet_cell = str(target.get("sheet_cell") or "").strip()
        metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
        truth = metadata.get("truth") if isinstance(metadata.get("truth"), dict) else {}
        if sheet_cell:
            result[sheet_cell] = {
                "row_index": truth.get("row_index"),
                "field": truth.get("field") or target.get("semantic_field"),
                "worksheet_row": target.get("worksheet_row"),
                "worksheet_col": target.get("worksheet_col"),
            }
    return result


def _sheet_values_by_sheet_cell(
    *,
    sheet: dict[str, Any],
    truth_by_cell: dict[str, dict[str, Any]],
) -> dict[str, str]:
    fields = [str(field) for field in (sheet.get("fields") or [])]
    rows = sheet.get("rows") if isinstance(sheet.get("rows"), list) else []
    field_index = {field: index for index, field in enumerate(fields)}
    result: dict[str, str] = {}
    for sheet_cell, truth in truth_by_cell.items():
        try:
            row_index = int(truth.get("row_index"))
        except Exception:
            continue
        field = str(truth.get("field") or "").strip()
        col_index = field_index.get(field)
        if col_index is None or row_index < 0 or row_index >= len(rows):
            continue
        row = rows[row_index]
        if isinstance(row, list) and col_index < len(row):
            result[sheet_cell] = str(row[col_index] or "").strip()
    return result


def _diff_maps(left: dict[str, str], right: dict[str, str]) -> list[dict[str, str]]:
    keys = sorted(set(left) | set(right))
    diffs: list[dict[str, str]] = []
    for key in keys:
        left_value = str(left.get(key) or "").strip()
        right_value = str(right.get(key) or "").strip()
        if left_value != right_value:
            diffs.append({"sheet_cell": key, "left": left_value, "right": right_value})
    return diffs


def _summarize_numeric_sheet(sheet: dict[str, Any]) -> dict[str, Any]:
    fields = [str(field) for field in (sheet.get("fields") or [])]
    rows = sheet.get("rows") if isinstance(sheet.get("rows"), list) else []
    qty_indexes = [
        index
        for index, field in enumerate(fields)
        if field.startswith("qty.") or field in {"regular", "daycare", "staff", "no_meat", "no_fish", "no_fried", "remarks"}
    ]
    nonempty = 0
    for row in rows:
        if not isinstance(row, list):
            continue
        for index in qty_indexes:
            if index < len(row) and str(row[index] or "").strip():
                nonempty += 1
    return {"fields": len(fields), "rows": len(rows), "quantity_field_count": len(qty_indexes), "nonempty_quantity_cells": nonempty}


def validate_case(
    *,
    page_index: int,
    item: dict[str, Any],
    auth_header: str,
    output_dir: Path,
    render_width: int,
    http_timeout: int,
) -> dict[str, Any]:
    order_id = str(item.get("order_id") or "").strip()
    facility_code = str(item.get("facility_code") or "").strip()
    case_dir = output_dir / f"{page_index:02d}_{facility_code}_{order_id}"
    case_dir.mkdir(parents=True, exist_ok=True)
    preview = _json_get(f"{cmp.STG_API_BASE}/orders/{order_id}/hakodate-overlay-preview", auth_header, timeout=http_timeout)
    ocr_sheet = _json_get(f"{cmp.STG_API_BASE}/orders/{order_id}/ocr-sheet", auth_header, timeout=http_timeout)
    draft_sheet_live = _json_get(f"{cmp.STG_API_BASE}/orders/{order_id}/draft-sheet", auth_header, timeout=http_timeout)
    draft_sheet = draft_sheet_live
    local_item = _build_live_manifest_item_from_stg_order(
        order_id=order_id,
        auth_header=auth_header,
        output_dir=output_dir,
        render_width=render_width,
        http_timeout=http_timeout,
    )

    started = time.perf_counter()
    local_summary, _review_page = build_best_method_for_manifest_item(
        item=local_item,
        page_index=page_index,
        draft_sheet=draft_sheet,
        output_dir=output_dir,
        render_width=render_width,
    )
    local_seconds = time.perf_counter() - started
    records_path = Path(str((local_summary.get("outputs") or {}).get("records") or ""))
    regions_path = Path(str((local_summary.get("outputs") or {}).get("ocr_regions") or ""))
    local_values = (
        _cell_value_map_from_local_artifacts(
            order_id=order_id,
            records_path=records_path,
            regions_path=regions_path,
        )
        if records_path.exists()
        else {}
    )

    assignment = preview.get("assignment") if isinstance(preview.get("assignment"), dict) else {}
    stg_assignment_values = _cell_value_map_from_assignment(assignment)
    stg_accepted_assignment_values_raw = _accepted_cell_value_map_from_assignment(assignment)
    truth_by_cell = _target_truth_by_sheet_cell(assignment)
    ocr_sheet_values = _sheet_values_by_sheet_cell(sheet=ocr_sheet, truth_by_cell=truth_by_cell)
    draft_sheet_values = _sheet_values_by_sheet_cell(sheet=draft_sheet_live, truth_by_cell=truth_by_cell)
    active_sheet_cells = set(ocr_sheet_values) | set(draft_sheet_values)
    stg_accepted_assignment_values = {
        sheet_cell: value
        for sheet_cell, value in stg_accepted_assignment_values_raw.items()
        if sheet_cell in active_sheet_cells
    }

    local_vs_stg = _diff_maps(local_values, stg_assignment_values)
    assignment_vs_ocr_sheet = _diff_maps(stg_assignment_values, ocr_sheet_values)
    assignment_vs_draft_sheet = _diff_maps(stg_assignment_values, draft_sheet_values)
    accepted_assignment_vs_ocr_sheet = _diff_maps(stg_accepted_assignment_values, ocr_sheet_values)
    accepted_assignment_vs_draft_sheet = _diff_maps(stg_accepted_assignment_values, draft_sheet_values)
    ocr_sheet_vs_draft_sheet = _diff_maps(ocr_sheet_values, draft_sheet_values)
    blocking_diffs = (
        local_vs_stg
        or accepted_assignment_vs_ocr_sheet
        or accepted_assignment_vs_draft_sheet
        or ocr_sheet_vs_draft_sheet
    )

    result = {
        "page": page_index,
        "facility_code": facility_code,
        "order_id": order_id,
        "status": "ok" if not blocking_diffs else "ng",
        "local_seconds": round(local_seconds, 3),
        "local_metrics": local_summary.get("metrics"),
        "stg_preview_status": preview.get("status"),
        "stg_source_evidence_run_id": preview.get("source_evidence_run_id"),
        "counts": {
            "local_nonempty_cells": len(local_values),
            "stg_assignment_nonempty_cells": len(stg_assignment_values),
            "stg_accepted_assignment_nonempty_cells": len(stg_accepted_assignment_values),
            "truth_cells": len(truth_by_cell),
            "ocr_sheet": _summarize_numeric_sheet(ocr_sheet),
            "draft_sheet": _summarize_numeric_sheet(draft_sheet_live),
        },
        "diff_counts": {
            "local_vs_stg_assignment": len(local_vs_stg),
            "stg_accepted_assignment_vs_ocr_sheet": len(accepted_assignment_vs_ocr_sheet),
            "stg_accepted_assignment_vs_draft_sheet": len(accepted_assignment_vs_draft_sheet),
            "ocr_sheet_vs_draft_sheet": len(ocr_sheet_vs_draft_sheet),
            "stg_all_assignment_vs_ocr_sheet_diagnostic": len(assignment_vs_ocr_sheet),
            "stg_all_assignment_vs_draft_sheet_diagnostic": len(assignment_vs_draft_sheet),
        },
        "diff_samples": {
            "local_vs_stg_assignment": local_vs_stg[:30],
            "stg_accepted_assignment_vs_ocr_sheet": accepted_assignment_vs_ocr_sheet[:30],
            "stg_accepted_assignment_vs_draft_sheet": accepted_assignment_vs_draft_sheet[:30],
            "ocr_sheet_vs_draft_sheet": ocr_sheet_vs_draft_sheet[:30],
            "stg_all_assignment_vs_ocr_sheet_diagnostic": assignment_vs_ocr_sheet[:30],
            "stg_all_assignment_vs_draft_sheet_diagnostic": assignment_vs_draft_sheet[:30],
        },
        "outputs": local_summary.get("outputs"),
    }
    (case_dir / "validation_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=cmp.DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--render-width", type=int, default=1864)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--http-timeout", type=int, default=420)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    auth_header = cmp._operator_auth_header_from_gcloud()
    results: list[dict[str, Any]] = []
    for page_index, item in _load_manifest_items(args.manifest):
        if args.limit and len(results) >= args.limit:
            break
        result = validate_case(
            page_index=page_index,
            item=item,
            auth_header=auth_header,
            output_dir=args.output_dir,
            render_width=args.render_width,
            http_timeout=args.http_timeout,
        )
        results.append(result)
        print(
            json.dumps(
                {
                    "page": result["page"],
                    "facility_code": result["facility_code"],
                    "order_id": result["order_id"],
                    "status": result["status"],
                    "diff_counts": result["diff_counts"],
                    "local_seconds": result["local_seconds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    summary = {
        "status": "ok" if all(item.get("status") == "ok" for item in results) else "ng",
        "case_count": len(results),
        "ng_cases": [
            {
                "page": item.get("page"),
                "facility_code": item.get("facility_code"),
                "order_id": item.get("order_id"),
                "diff_counts": item.get("diff_counts"),
            }
            for item in results
            if item.get("status") != "ok"
        ],
        "results": results,
    }
    summary_path = args.output_dir / "validation_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": str(summary_path), "status": summary["status"], "case_count": len(results)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
