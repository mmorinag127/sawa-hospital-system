#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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
from src.services import hakodate_ocr_evidence_service  # noqa: E402
from src.models.order import Order  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.services.order_service import (  # noqa: E402
    _build_live_hakodate_manifest_item,
    geometry_source_digest,
)
from src.services.order_workflow_v2_service import _stable_json_hash  # noqa: E402
from src.services.storage_service import verify_selected_pdf_source  # noqa: E402
from src.services.workflow_attempt_guard_service import (  # noqa: E402
    _order_execution_input,
    capture_selected_pdf_receipt,
    snapshots_equal,
)
from src.hakodate_best_method_runtime.render_best_method_overlay_all_facilities import (  # noqa: E402
    build_best_method_for_manifest_item,
)


DEFAULT_OUTPUT_DIR = WORKSPACE_ROOT / "tmp" / "hakodate_all_facilities_validation_20260501"


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


def _capture_geometry_input(order_id: str) -> dict[str, Any]:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if order is None:
            raise ValueError("validation_geometry_order_not_found")
        captured = _order_execution_input(session, order, lock=False)
        captured["selected_pdf_receipt"] = capture_selected_pdf_receipt(session, order)
        verify_selected_pdf_source(captured["selected_pdf_receipt"])
        return captured


def _validate_remote_geometry_source(captured: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    order = captured["order"]
    receipt = review.get("source_receipt")
    keys = (
        "geometry_source_digest", "configured_input_digest", "physical_menu_entries",
        "selected_pdf_receipt", "order_input_identity", "input_digest",
        "selected_version_input", "workflow_revision",
    )
    if review.get("order_id") != order["id"] or not isinstance(receipt, dict) or any(key not in receipt for key in keys):
        raise ValueError("validation_remote_geometry_source_required")
    identity = {key: order[key] for key in ("facility_code", "week_code")}
    expected_digest = geometry_source_digest(
        captured["selected_pdf_receipt"], captured["configured_input"], identity,
        physical_entries=captured["physical_menu_entries"],
    )
    if (receipt["geometry_source_digest"] != expected_digest
            or receipt["configured_input_digest"] != _stable_json_hash(captured["configured_input"])
            or not snapshots_equal(receipt["physical_menu_entries"], captured["physical_menu_entries"])
            or not snapshots_equal(receipt["selected_pdf_receipt"], captured["selected_pdf_receipt"])
            or not snapshots_equal(receipt["order_input_identity"], identity)):
        raise ValueError("validation_remote_geometry_source_mismatch")
    return {key: receipt[key] for key in keys}


def _revalidate_comparison_geometry(
    *, order_id: str, context: dict[str, Any], review: dict[str, Any],
) -> None:
    captured = context.get("captured_input")
    remote_source = context.get("remote_geometry_source")
    if not isinstance(captured, dict) or not isinstance(remote_source, dict):
        raise ValueError("validation_comparison_geometry_context_required")
    current = _capture_geometry_input(order_id)
    if not snapshots_equal(captured, current):
        raise ValueError("validation_geometry_source_changed")
    current_source = _validate_remote_geometry_source(current, review)
    if not snapshots_equal(remote_source, current_source):
        raise ValueError("validation_remote_geometry_source_changed")


def _build_live_manifest_item_from_stg_order(
    *,
    order_id: str,
    auth_header: str,
    output_dir: Path,
    render_width: int,
    http_timeout: int,
    expected_facility_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    order = _json_get(f"{cmp.STG_API_BASE}/orders/{order_id}", auth_header, timeout=http_timeout)
    if order.get("id") != order_id or order.get("facility") != expected_facility_id:
        raise ValueError("validation_order_identity_mismatch")
    captured = _capture_geometry_input(order_id)
    selected_order = captured["order"]
    receipt = captured["selected_pdf_receipt"]
    # A local connection is usable only for the same current order/document as the API target.
    if (selected_order["id"] != order_id
            or selected_order["facility_code"] != expected_facility_id
            or selected_order["week_code"] != order.get("week_value")
            or receipt["order_id"] != order_id
            or receipt["document_id"] != order.get("document_id")
            or receipt["storage_uri"] != order.get("document")):
        raise ValueError("validation_geometry_capture_api_mismatch")
    review_url = f"{cmp.STG_API_BASE}/orders/{order_id}/workflow-v2/quad-review"
    remote_source = _validate_remote_geometry_source(
        captured, _json_get(review_url, auth_header, timeout=http_timeout),
    )
    local_item = _build_live_hakodate_manifest_item(
        order_id=order_id,
        facility_id=expected_facility_id,
        output_dir=output_dir / "_live_inputs" / order_id,
        render_width=render_width,
        document_uri_override=receipt["storage_uri"],
        review_start_input=captured,
    )
    if not snapshots_equal(captured, _capture_geometry_input(order_id)):
        raise ValueError("validation_geometry_source_changed")
    current_source = _validate_remote_geometry_source(
        captured, _json_get(review_url, auth_header, timeout=http_timeout),
    )
    if not snapshots_equal(remote_source, current_source):
        raise ValueError("validation_remote_geometry_source_changed")
    current = _json_get(f"{cmp.STG_API_BASE}/orders/{order_id}", auth_header, timeout=http_timeout)
    if any(current.get(key) != order.get(key) for key in ("id", "facility", "week_value", "document", "document_id", "current_version")):
        raise ValueError("validation_api_selected_document_changed")
    return local_item, {"captured_input": captured, "remote_geometry_source": remote_source}


def _require_local_artifact(outputs: Any, key: str) -> list[dict[str, Any]]:
    raw_path = outputs.get(key) if isinstance(outputs, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"validation_local_artifact_path_required:{key}")
    path = Path(raw_path)
    if not path.is_file():
        raise ValueError(f"validation_local_artifact_missing:{key}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"validation_local_artifact_invalid:{key}") from exc
    if not isinstance(payload, list) or not payload or not all(isinstance(item, dict) and item for item in payload):
        raise ValueError(f"validation_local_artifact_invalid:{key}")
    return payload


def _cell_value_map_from_local_artifacts(
    *,
    order_id: str,
    records_path: Path,
    regions_path: Path,
) -> dict[str, str]:
    outputs = {"records": str(records_path), "ocr_regions": str(regions_path)}
    records = _require_local_artifact(outputs, "records")
    canonical_regions = _require_local_artifact(outputs, "ocr_regions")
    raw_evidence_records: list[dict[str, Any]] = []
    for index, region in enumerate(records):
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


def _require_comparison_sheet(payload: dict[str, Any], *, order_id: str, surface: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("order_id") != order_id:
        raise ValueError(f"validation_sheet_order_mismatch:{surface}")
    sheet = payload.get("sheet")
    if not isinstance(sheet, dict):
        raise ValueError(f"validation_sheet_missing:{surface}")
    fields, rows = sheet.get("fields"), sheet.get("rows")
    if (not isinstance(fields, list) or not fields or not all(isinstance(field, str) for field in fields)
            or not isinstance(rows, list) or not rows or not all(isinstance(row, list) for row in rows)):
        raise ValueError(f"validation_sheet_fields_rows_invalid:{surface}")
    return sheet


def _require_comparison_lineage(
    *, order_id: str, workflow: Any, preview: Any,
    projection: Any = None, saved: Any = None,
) -> dict[str, Any]:
    if not isinstance(workflow, dict) or workflow.get("order_id") != order_id:
        raise ValueError("validation_workflow_order_mismatch")
    revision = workflow.get("revision")
    selected = workflow.get("selected_ocr_result_id")
    if type(revision) is not int or revision < 1:
        raise ValueError("validation_workflow_revision_invalid")
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError("validation_workflow_selected_evidence_required")
    if (not isinstance(preview, dict) or preview.get("status") != "ready"
            or preview.get("blockers") or preview.get("source_evidence_run_id") != selected):
        raise ValueError("validation_preview_workflow_lineage_mismatch")
    if projection is not None or saved is not None:
        _require_comparison_sheet(projection, order_id=order_id, surface="selected_ocr_projection")
        _require_comparison_sheet(saved, order_id=order_id, surface="saved_sheet")
        if projection.get("selected_ocr_result_id") != selected or saved.get("source_ocr_result_id") != selected:
            raise ValueError("validation_sheet_ocr_lineage_mismatch")
    return {"order_id": order_id, "revision": revision, "selected_ocr_result_id": selected}


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
    workflow_path = f"/orders/{order_id}/workflow-v2"
    workflow = _json_get(f"{cmp.STG_API_BASE}{workflow_path}", auth_header, timeout=http_timeout)
    preview = _json_get(f"{cmp.STG_API_BASE}/orders/{order_id}/hakodate-overlay-preview", auth_header, timeout=http_timeout)
    projection_path = f"/orders/{order_id}/workflow-v2/sheet-source"
    saved_path = f"/orders/{order_id}/workflow-v2/sheet"
    projection = _json_get(f"{cmp.STG_API_BASE}{projection_path}", auth_header, timeout=http_timeout)
    saved = _json_get(f"{cmp.STG_API_BASE}{saved_path}", auth_header, timeout=http_timeout)
    lineage = _require_comparison_lineage(
        order_id=order_id, workflow=workflow, preview=preview, projection=projection, saved=saved,
    )
    ocr_sheet = _require_comparison_sheet(projection, order_id=order_id, surface="selected_ocr_projection")
    draft_sheet_live = _require_comparison_sheet(saved, order_id=order_id, surface="saved_sheet")
    selected_result_id = projection.get("selected_ocr_result_id")
    if (not isinstance(selected_result_id, str) or not selected_result_id.strip()
            or saved.get("source_ocr_result_id") != selected_result_id
            or not isinstance(preview, dict)
            or preview.get("source_evidence_run_id") != selected_result_id):
        raise ValueError("validation_sheet_ocr_lineage_mismatch")
    assignment = preview.get("assignment")
    if (preview.get("status") != "ready" or preview.get("blockers")
            or not isinstance(assignment, dict)
            or not isinstance(assignment.get("assignments"), list)
            or not assignment["assignments"]
            or not all(isinstance(entry, dict) and entry for entry in assignment["assignments"])):
        raise ValueError("validation_preview_assignment_unavailable")
    draft_sheet = draft_sheet_live
    local_item, geometry_context = _build_live_manifest_item_from_stg_order(
        order_id=order_id,
        auth_header=auth_header,
        output_dir=output_dir,
        render_width=render_width,
        http_timeout=http_timeout,
        expected_facility_id=facility_code,
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
    outputs = local_summary.get("outputs")
    _require_local_artifact(outputs, "records")
    _require_local_artifact(outputs, "ocr_regions")
    local_values = _cell_value_map_from_local_artifacts(
        order_id=order_id,
        records_path=Path(outputs["records"]),
        regions_path=Path(outputs["ocr_regions"]),
    )

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

    _revalidate_comparison_geometry(
        order_id=order_id, context=geometry_context,
        review=_json_get(f"{cmp.STG_API_BASE}/orders/{order_id}/workflow-v2/quad-review", auth_header, timeout=http_timeout),
    )
    current_projection = _json_get(f"{cmp.STG_API_BASE}{projection_path}", auth_header, timeout=http_timeout)
    current_saved = _json_get(f"{cmp.STG_API_BASE}{saved_path}", auth_header, timeout=http_timeout)
    current_preview = _json_get(f"{cmp.STG_API_BASE}/orders/{order_id}/hakodate-overlay-preview", auth_header, timeout=http_timeout)
    current_workflow = _json_get(f"{cmp.STG_API_BASE}{workflow_path}", auth_header, timeout=http_timeout)
    current_lineage = _require_comparison_lineage(
        order_id=order_id, workflow=current_workflow, preview=current_preview,
        projection=current_projection, saved=current_saved,
    )
    if (not snapshots_equal(lineage, current_lineage)
            or not snapshots_equal(projection, current_projection)
            or not snapshots_equal(saved, current_saved)):
        raise ValueError("validation_comparison_source_changed")

    result = {
        "page": page_index,
        "facility_code": facility_code,
        "order_id": order_id,
        "status": "ok" if not blocking_diffs else "ng",
        "local_seconds": round(local_seconds, 3),
        "local_metrics": local_summary.get("metrics"),
        "stg_preview_status": preview.get("status"),
        "stg_source_evidence_run_id": preview.get("source_evidence_run_id"),
        "sheet_sources": {
            "ocr_sheet": {"source": "workflow_v2_selected_ocr_projection", "endpoint": projection_path, "selected_ocr_result_id": selected_result_id},
            "draft_sheet": {"source": "saved_sheet", "endpoint": saved_path, "source_ocr_result_id": saved["source_ocr_result_id"]},
        },
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
