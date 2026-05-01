#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import urllib.request
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

def _resolve_backend_root() -> Path:
    path = Path(__file__).resolve()
    for candidate in path.parents:
        if (candidate / "src").exists() and (candidate / "requirements.txt").exists():
            return candidate
        if (candidate / "backend" / "src").exists():
            return candidate / "backend"
    return path.parents[2]


BACKEND_ROOT = _resolve_backend_root()
WORKSPACE = BACKEND_ROOT.parent if (BACKEND_ROOT.parent / "tmp").exists() else BACKEND_ROOT
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(BACKEND_ROOT))

import compare_kasuga_digit_preprocess_methods as cmp  # noqa: E402
from src.services.hakodate_cell_ocr_batch_service import _build_preprocess_for_ocr  # noqa: E402
from src.services.hakodate_step_review_pipeline_service import (  # noqa: E402
    _draw_quad_points,
    _make_review_canvas,
    _write_pdf_from_pages,
)
from src.hakodate_best_method_runtime.run_text_recognizer_corner_noise_trial import (  # noqa: E402
    TRIAL_ENGINE as TEXT_RECOGNIZER_ENGINE,
    _load_text_recognizer,
    build_recognizer_contact_sheet,
    run_text_recognizer_direct,
)


OUT_DIR = SCRIPT_DIR / "best_method_overlay_all_facilities"
ROWS_START = cmp.EVAL_WORKSHEET_ROW_START
LOCAL_WORKSPACE_PREFIX = "/Users/mmorinag/Sawa/2025.12/workspace/"
_TEXT_RECOGNIZER = None


def _get_text_recognizer():
    global _TEXT_RECOGNIZER
    if _TEXT_RECOGNIZER is None:
        _TEXT_RECOGNIZER = _load_text_recognizer("cpu")
    return _TEXT_RECOGNIZER


def _resolve_item_paths(item: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(item)
    for key in ("fax_pdf", "template_pdf", "step2_png"):
        value = str(resolved.get(key) or "").strip()
        if not value:
            continue
        if Path(value).exists():
            continue
        if value.startswith(LOCAL_WORKSPACE_PREFIX):
            candidate = WORKSPACE / value[len(LOCAL_WORKSPACE_PREFIX) :]
            if candidate.exists():
                resolved[key] = str(candidate)
    return resolved


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
) -> None:
    try:
        draw.text(xy, text, font=font, fill=fill)
    except Exception:
        fallback = text.encode("ascii", "ignore").decode("ascii") or "?"
        draw.text(xy, fallback, font=font, fill=fill)


def _load_manifest_items() -> list[tuple[int, dict[str, Any]]]:
    manifest = json.loads(cmp.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    items = manifest.get("results") if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        raise ValueError("manifest results are missing")
    return [(page, item) for page, item in enumerate(items, start=1)]


def _fetch_draft_sheet(order_id: str, out_dir: Path, auth_header: str) -> dict[str, Any]:
    path = out_dir / "draft_sheets" / f"{order_id}_draft_sheet.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    request = urllib.request.Request(
        f"{cmp.STG_API_BASE}/orders/{order_id}/draft-sheet",
        headers={"Authorization": auth_header},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = response.read()
    path.write_bytes(payload)
    return json.loads(payload)


def _target_field_mapping(fields: list[str], target_cols: list[int]) -> dict[int, str]:
    if not fields or not target_cols:
        return {}
    try:
        menu_index = fields.index("menu")
    except ValueError:
        menu_index = 2 if len(fields) > 2 else -1
    after_menu = fields[menu_index + 1 :]
    if len(after_menu) == len(target_cols):
        target_fields = after_menu
    elif len(after_menu) > len(target_cols) and after_menu[-1] == "remarks":
        target_fields = after_menu[: len(target_cols) - 1] + [after_menu[-1]]
    else:
        target_fields = after_menu[: len(target_cols)]
    return {col: str(field) for col, field in zip(target_cols, target_fields)}


def _build_truth_for_facility(
    draft_sheet: dict[str, Any],
    regions: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[int, str]]:
    fields = [str(field) for field in (draft_sheet.get("fields") or [])]
    rows = list(draft_sheet.get("rows") or [])
    field_indexes = {field: index for index, field in enumerate(fields)}
    target_cols = sorted({int(region.get("worksheet_col") or 0) for region in regions})
    field_by_col = _target_field_mapping(fields, target_cols)
    truth: dict[str, dict[str, Any]] = {}
    for region in regions:
        sheet_cell = str(region.get("sheet_cell") or region.get("region_id") or "")
        try:
            worksheet_row = int(region.get("worksheet_row") or 0)
            worksheet_col = int(region.get("worksheet_col") or 0)
        except Exception:
            continue
        row_index = worksheet_row - ROWS_START
        field = field_by_col.get(worksheet_col)
        if field is None or row_index < 0:
            continue
        expected = ""
        field_index = field_indexes.get(field)
        if row_index < len(rows) and field_index is not None:
            row = rows[row_index]
            if isinstance(row, list) and field_index < len(row):
                expected = str(row[field_index] or "")
        target = (region.get("logical_targets") or [{}])[0]
        truth[sheet_cell] = {
            "sheet_cell": sheet_cell,
            "worksheet_row": worksheet_row,
            "worksheet_col": worksheet_col,
            "row_index": row_index,
            "field": field,
            "expected_text": expected,
            "expected_digits": cmp._normalize_expected(expected),
            "date": target.get("date"),
            "daypart": target.get("daypart"),
            "menu_name": target.get("menu_name"),
            "field_label": region.get("field_label"),
            "eval_numeric": field != "remarks",
        }
    return truth, field_by_col


def _snap_regions_x_to_fax_lines_all_targets(
    rectified: np.ndarray,
    regions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target_regions = [
        region
        for region in regions
        if isinstance(region.get("bbox"), list) and len(region["bbox"]) == 4
    ]
    if not target_regions:
        return regions, {"applied": False, "reason": "no_target_regions"}
    original_boundaries = sorted(
        {int(round(float(region["bbox"][0]))) for region in target_regions}
        | {int(round(float(region["bbox"][2]))) for region in target_regions}
    )
    if len(original_boundaries) < 2:
        return regions, {"applied": False, "reason": "insufficient_boundaries"}
    peaks = cmp._extract_vertical_line_peaks(rectified, target_regions)
    if not peaks:
        return regions, {
            "applied": False,
            "reason": "no_fax_line_peaks",
            "original_boundaries": original_boundaries,
        }
    snapped_boundaries: list[int] = []
    assignments: list[dict[str, Any]] = []
    for index, boundary in enumerate(original_boundaries):
        tolerance = 55 if 0 < index < len(original_boundaries) - 1 else 35
        candidates = [(x, score) for x, score in peaks if abs(x - boundary) <= tolerance]
        if candidates:
            chosen_x, chosen_score = max(candidates, key=lambda item: item[1])
        else:
            chosen_x, chosen_score = boundary, 0.0
        snapped_boundaries.append(int(chosen_x))
        assignments.append(
            {
                "template_x": int(boundary),
                "snapped_x": int(chosen_x),
                "delta": int(chosen_x - boundary),
                "score": round(float(chosen_score), 2),
                "candidate_count": len(candidates),
            }
        )
    for index in range(1, len(snapped_boundaries)):
        min_gap = 35
        if snapped_boundaries[index] <= snapped_boundaries[index - 1] + min_gap:
            snapped_boundaries[index] = original_boundaries[index]
            assignments[index]["snapped_x"] = int(original_boundaries[index])
            assignments[index]["delta"] = 0
            assignments[index]["score"] = 0.0
            assignments[index]["fallback_reason"] = "non_monotonic_after_snap"
    boundary_by_original = dict(zip(original_boundaries, snapped_boundaries))
    snapped_regions: list[dict[str, Any]] = []
    for region in regions:
        copied = dict(region)
        box = copied.get("bbox")
        if isinstance(box, list) and len(box) == 4:
            left = int(round(float(box[0])))
            right = int(round(float(box[2])))
            new_box = list(box)
            new_box[0] = float(boundary_by_original.get(left, left))
            new_box[2] = float(boundary_by_original.get(right, right))
            if new_box[2] > new_box[0]:
                copied["bbox"] = new_box
                copied["x_snap"] = {
                    "original_left": left,
                    "original_right": right,
                    "snapped_left": int(new_box[0]),
                    "snapped_right": int(new_box[2]),
                }
        snapped_regions.append(copied)
    return snapped_regions, {
        "applied": True,
        "original_boundaries": original_boundaries,
        "snapped_boundaries": snapped_boundaries,
        "assignments": assignments,
        "peaks": [{"x": int(x), "score": round(float(score), 2)} for x, score in peaks],
    }


def _apply_soft_pair_when_present(
    records: list[dict[str, Any]],
    field_by_col: dict[int, str],
) -> list[dict[str, Any]]:
    if "soft" in field_by_col.get(7, "") and "soft" in field_by_col.get(8, ""):
        return cmp._apply_soft_pair_spillover_postprocess(records)
    return records


def _restore_display_bboxes(
    records: list[dict[str, Any]],
    display_regions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep OCR crop boxes snapped, but publish/display canonical step6 cell boxes."""
    display_by_cell = {str(region.get("sheet_cell") or region.get("region_id") or ""): region for region in display_regions}
    restored: list[dict[str, Any]] = []
    for record in records:
        cell = str(record.get("sheet_cell") or record.get("region_id") or "")
        display_region = display_by_cell.get(cell)
        if not display_region or not isinstance(display_region.get("bbox"), list):
            restored.append(record)
            continue
        restored.append(
            {
                **record,
                "ocr_crop_bbox": record.get("bbox"),
                "bbox": list(display_region["bbox"]),
            }
        )
    return restored


def _confidence_score_for_region(region: dict[str, Any]) -> float:
    accepted_candidate = region.get("recognizer_accepted_candidate")
    if (
        str(region.get("recognizer_decision_source") or "").strip() == "topk_digits"
        and isinstance(accepted_candidate, dict)
    ):
        try:
            return float(accepted_candidate.get("score") or 0.0)
        except Exception:
            return 0.0
    try:
        return float(region.get("recognizer_score") or 0.0)
    except Exception:
        return 0.0


def _run_text_recognizer_records(
    *,
    raw_rectified_bgr: Any,
    regions: list[dict[str, Any]],
    truth: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], Image.Image]:
    contact_sheet, usable_regions, polygons, skipped_regions = build_recognizer_contact_sheet(
        rectified_fax_bgr=raw_rectified_bgr,
        regions=regions,
        line_mask=None,
        mode="corner_cc",
        min_ink_area=18,
        min_ink_height=8,
    )
    recognized_regions, metrics = run_text_recognizer_direct(
        recognizer=_get_text_recognizer(),
        contact_sheet=contact_sheet,
        regions=usable_regions,
        polygons=polygons,
        score_threshold=0.45,
        digit_score_threshold=0.05,
        candidate_digit_score_threshold=0.05,
        enable_context_repair=False,
        sequence_top_k=5,
    )
    merged_regions = sorted(
        recognized_regions + skipped_regions,
        key=lambda item: int(item.get("ocr_contact_slot_index") or 0),
    )
    records: list[dict[str, Any]] = []
    for region in merged_regions:
        sheet_cell = str(region.get("sheet_cell") or region.get("region_id") or "")
        record = {
            **region,
            "truth": truth.get(sheet_cell) or {},
            "expected_digits": str((truth.get(sheet_cell) or {}).get("expected_digits") or ""),
            "ocr_engine": TEXT_RECOGNIZER_ENGINE,
            "raw_text": str(region.get("recognizer_raw_text") or region.get("ocr_text") or "").strip(),
            "pred_digits": str(region.get("ocr_normalized") or "").strip(),
            "score": _confidence_score_for_region(region),
            "ocr_candidate": bool(region.get("recognizer_candidate")),
            "supervised_label_source": "",
        }
        records.append(record)
    metrics = {
        **metrics,
        "numeric_eval_cell_count": len(records),
        "pred_nonempty_count": sum(1 for record in records if str(record.get("pred_digits") or "").strip()),
    }
    return records, metrics, contact_sheet


def _draw_overlay(
    *,
    raw_rectified_bgr: Any,
    regions: list[dict[str, Any]],
    records: list[dict[str, Any]],
    quad_points: list[tuple[float, float]],
    facility_code: str,
    order_id: str,
    details: list[str],
) -> Image.Image:
    base = Image.fromarray(cv2.cvtColor(raw_rectified_bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _font(30)
    small_font = _font(13)
    x_edges = sorted({int(round(float(v))) for region in regions for v in (region["bbox"][0], region["bbox"][2])})
    y_edges = sorted({int(round(float(v))) for region in regions for v in (region["bbox"][1], region["bbox"][3])})
    x0 = min(x_edges)
    x1 = max(x_edges)
    y0 = min(y_edges)
    y1 = max(y_edges)

    for x in x_edges:
        draw.line((x, y0, x, y1), fill=(0, 190, 0, 230), width=3)
    for y in y_edges:
        draw.line((x0, y, x1, y), fill=(0, 190, 0, 210), width=2)

    by_cell = {str(record.get("sheet_cell")): record for record in records}
    for region in regions:
        box = region.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            continue
        record = by_cell.get(str(region.get("sheet_cell")))
        has_ink = bool(record.get("ocr_candidate")) if isinstance(record, dict) else True
        inner_fill = (255, 0, 0, 255) if has_ink else (0, 105, 255, 255)
        rx0, ry0, rx1, ry1 = [int(round(float(v))) for v in box]
        cx = int(round((rx0 + rx1) / 2.0))
        cy = int(round((ry0 + ry1) / 2.0))
        draw.rectangle((rx0, ry0, rx1, ry1), outline=(0, 115, 255, 125), width=1)
        draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=(255, 255, 255, 235))
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=inner_fill)

    for region in regions:
        record = by_cell.get(str(region.get("sheet_cell")))
        if not record:
            continue
        value = str(record.get("pred_digits") or "").strip()
        if not value:
            continue
        box = region.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            continue
        rx0, ry0, rx1, ry1 = [int(round(float(v))) for v in box]
        cx = int(round((rx0 + rx1) / 2.0))
        cy = int(round((ry0 + ry1) / 2.0))
        label = value[:8]
        try:
            tb = draw.textbbox((0, 0), label, font=font)
            tw = max(1, tb[2] - tb[0])
            th = max(1, tb[3] - tb[1])
        except Exception:
            tw, th = 28, 20
        lx = min(max(cx + 12, 0), max(0, base.width - tw - 1))
        ly = min(max(cy - th - 8, 0), max(0, base.height - th - 1))
        _draw_text(draw, (lx, ly), label, font=font, fill=(220, 0, 0, 255))
        if str(record.get("postprocess") or ""):
            _draw_text(draw, (lx + 6, ly + th - 12), "pp", font=small_font, fill=(0, 80, 180, 230))

    image = Image.alpha_composite(base, layer).convert("RGB")
    image = _draw_quad_points(image, quad_points, prefix="Q")
    return _make_review_canvas(
        title="Best method overlay all facilities: FAX-line snap + supervised KNN",
        facility_code=facility_code,
        order_id=order_id,
        image=image,
        details=details,
    )


def _thumbnail(page: Image.Image, caption: str) -> Image.Image:
    thumb = page.copy()
    thumb.thumbnail((760, 1180), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (820, 1260), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font(22)
    draw.text((20, 12), caption, fill=(0, 0, 0), font=font)
    canvas.paste(thumb, ((canvas.width - thumb.width) // 2, 58))
    return canvas


def _write_preview_contact_sheet(thumbnails: list[Image.Image], path: Path) -> None:
    if not thumbnails:
        return
    columns = 2
    rows = math.ceil(len(thumbnails) / columns)
    w = columns * thumbnails[0].width
    h = rows * thumbnails[0].height
    sheet = Image.new("RGB", (w, h), "white")
    for index, thumb in enumerate(thumbnails):
        x = (index % columns) * thumb.width
        y = (index // columns) * thumb.height
        sheet.paste(thumb, (x, y))
    sheet.save(path)


def build_best_method_for_manifest_item(
    *,
    item: dict[str, Any],
    page_index: int,
    draft_sheet: dict[str, Any],
    output_dir: Path,
    render_width: int = 1864,
) -> tuple[dict[str, Any], Image.Image]:
    item = _resolve_item_paths(item)
    facility_code = str(item.get("facility_code") or "")
    order_id = str(item.get("order_id") or "")
    pre = _build_preprocess_for_ocr(item=item, page=page_index, render_width=render_width)
    draft_rows = draft_sheet.get("rows") if isinstance(draft_sheet.get("rows"), list) else []
    max_worksheet_row = ROWS_START + len(draft_rows)
    eval_regions = [
        region
        for region in pre["target_regions"]
        if ROWS_START <= int(region.get("worksheet_row") or 0) < max_worksheet_row
    ]
    truth, field_by_col = _build_truth_for_facility(draft_sheet, eval_regions)
    snapped_regions, snap_debug = _snap_regions_x_to_fax_lines_all_targets(pre["raw_rectified"], eval_regions)
    facility_dir = output_dir / f"{page_index:02d}_{facility_code}_{order_id}"
    facility_dir.mkdir(parents=True, exist_ok=True)
    records, ocr_metrics, contact_sheet = _run_text_recognizer_records(
        raw_rectified_bgr=pre["raw_rectified"],
        regions=snapped_regions,
        truth=truth,
    )
    records = _restore_display_bboxes(records, snapped_regions)
    metrics = cmp._evaluate(records)
    metrics.update(
        {
            "ocr_seconds": ocr_metrics.get("ocr_seconds"),
            "raw_prediction_count": ocr_metrics.get("raw_prediction_count"),
            "digit_prediction_count": ocr_metrics.get("digit_prediction_count"),
            "accepted_digit_count": ocr_metrics.get("accepted_digit_count"),
            "candidate_digit_prediction_count": ocr_metrics.get("candidate_digit_prediction_count"),
            "candidate_digit_accept_count": ocr_metrics.get("candidate_digit_accept_count"),
            "digit_score_threshold": ocr_metrics.get("digit_score_threshold"),
            "candidate_digit_score_threshold": ocr_metrics.get("candidate_digit_score_threshold"),
        }
    )
    quad_points = [
        (float(point[0]), float(point[1]))
        for point in (pre.get("rectified_quad_points") or [])
        if isinstance(point, (list, tuple)) and len(point) >= 2
    ]
    if len(quad_points) != 4:
        x0, y0, x1, y1 = [float(v) for v in item["template_bbox"]]
        quad_points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    details = [
        f"page={page_index} fields={field_by_col}",
        "green lines: snapped target-cell grid after matching template X to actual FAX vertical lines",
        "red points: target cells with ink; blue points: target cells estimated blank; blue boxes: target cell boxes; Q markers: accepted outer 4 points",
        "OCR: yomitoku TextRecognizer top-k, strict>=0.45 / assisted>=0.15 / suggestion>=0.05",
        (
            f"metrics numeric={metrics['nonempty_exact_count']}/{metrics['expected_nonempty_count']} "
            f"({metrics['nonempty_exact_rate']:.1%}) all={metrics['exact_all_count']}/{metrics['numeric_eval_cell_count']} "
            f"({metrics['exact_all_rate']:.1%}) FN={metrics['false_negative_count']} "
            f"wrong={metrics['wrong_digit_count']} FP_blank={metrics['false_positive_blank_count']}"
        ),
    ]
    review_page = _draw_overlay(
        raw_rectified_bgr=pre["raw_rectified"],
        regions=snapped_regions,
        records=records,
        quad_points=quad_points,
        facility_code=facility_code,
        order_id=order_id,
        details=details,
    )
    page_png = facility_dir / "best_method_overlay.png"
    page_pdf = facility_dir / "best_method_overlay.pdf"
    records_path = facility_dir / "best_method_records.json"
    regions_path = facility_dir / "best_method_ocr_regions.json"
    summary_path = facility_dir / "best_method_summary.json"
    contact_sheet_path = facility_dir / "best_method_contact_sheet.png"
    sheet_values_path = facility_dir / "best_method_sheet_values.json"
    review_page.save(page_png)
    _write_pdf_from_pages([review_page], page_pdf)
    contact_sheet.save(contact_sheet_path)
    records_json = [cmp._strip_record_for_json(record) for record in records]
    records_path.write_text(json.dumps(records_json, ensure_ascii=False, indent=2), encoding="utf-8")
    ocr_regions: list[dict[str, Any]] = []
    for record in records_json:
        region = dict(record)
        region["ocr_text"] = str(record.get("raw_text") or record.get("pred_digits") or "").strip()
        region["ocr_normalized"] = str(record.get("pred_digits") or "").strip()
        region["ocr_engine"] = "opencv_knn_leave_one_out_k5"
        region["source"] = "render_best_method_overlay_all_facilities.py"
        ocr_regions.append(region)
    regions_path.write_text(json.dumps(ocr_regions, ensure_ascii=False, indent=2), encoding="utf-8")
    sheet_values_path.write_text(json.dumps({"cells": {}, "rows": [], "columns": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "page": page_index,
        "facility_code": facility_code,
        "order_id": order_id,
        "method": "fax_line_snap_current_frame_noise_text_recognizer_topk",
        "engine": TEXT_RECOGNIZER_ENGINE,
        "postprocess": "none",
        "field_by_col": field_by_col,
        "metrics": metrics,
        "snap_debug": snap_debug,
        "outputs": {
            "pdf": str(page_pdf),
            "overlay": str(page_png),
            "overlay_png": str(page_png),
            "contact_sheet": str(contact_sheet_path),
            "records": str(records_path),
            "ocr_regions": str(regions_path),
            "sheet_values": str(sheet_values_path),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary, review_page


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    method = cmp.MethodSpec(
        name="fax_line_snap_current_frame_noise",
        description="FAX-line snapped cells, current frame erasure, OpenCV KNN leave-one-out.",
        crop_mode="expanded",
        pad_x=1,
        pad_y=8,
        frame_mode="current",
        threshold_mode="gray",
        component_filter="small_only",
    )
    auth_header = cmp._operator_auth_header_from_gcloud()
    pages: list[Image.Image] = []
    thumbnails: list[Image.Image] = []
    summaries: list[dict[str, Any]] = []
    for page_index, item in _load_manifest_items():
        facility_code = str(item.get("facility_code") or "")
        order_id = str(item.get("order_id") or "")
        draft_sheet = _fetch_draft_sheet(order_id, OUT_DIR, auth_header)
        facility_dir = OUT_DIR / f"{page_index:02d}_{facility_code}_{order_id}"
        facility_dir.mkdir(parents=True, exist_ok=True)
        summary, review_page = build_best_method_for_manifest_item(
            item=item,
            page_index=page_index,
            draft_sheet=draft_sheet,
            output_dir=OUT_DIR,
            render_width=1864,
        )
        page_png = facility_dir / "best_method_overlay.png"
        page_pdf = facility_dir / "best_method_overlay.pdf"
        records_path = facility_dir / "best_method_records.json"
        summary_path = facility_dir / "best_method_summary.json"
        contact_sheet_path = facility_dir / "best_method_contact_sheet.png"
        review_page.save(page_png)
        _write_pdf_from_pages([review_page], page_pdf)
        metrics = dict(summary.get("metrics") or {})
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append(summary)
        pages.append(review_page)
        thumbnails.append(_thumbnail(review_page, f"p{page_index} {facility_code} {order_id}"))
        print(
            json.dumps(
                {
                    "page": page_index,
                    "facility_code": facility_code,
                    "order_id": order_id,
                    "metrics": metrics,
                    "png": str(page_png),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    combined_pdf = OUT_DIR / "best_method_overlay_all_facilities.pdf"
    combined_preview = OUT_DIR / "best_method_overlay_all_facilities_preview.png"
    combined_summary = OUT_DIR / "best_method_overlay_all_facilities_summary.json"
    _write_pdf_from_pages(pages, combined_pdf)
    _write_preview_contact_sheet(thumbnails, combined_preview)
    combined_summary.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "outputs": {
                    "pdf": str(combined_pdf),
                    "preview": str(combined_preview),
                    "summary": str(combined_summary),
                },
                "facility_count": len(summaries),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
