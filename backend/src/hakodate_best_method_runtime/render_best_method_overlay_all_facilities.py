#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import time
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
    snap_regions_x_to_local_fax_rulings,
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


def _sheet_field_from_region(region: dict[str, Any]) -> str:
    target = (region.get("logical_targets") or [{}])[0]
    candidates = (
        target.get("semantic_field"),
        target.get("field"),
        region.get("semantic_field"),
        region.get("field"),
    )
    for candidate in candidates:
        field = str(candidate or "").strip()
        if field == "note":
            return "remarks"
        if field:
            return field
    return ""


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
        region_field = _sheet_field_from_region(region)
        field = region_field if region_field in field_indexes else field_by_col.get(worksheet_col) or region_field
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
            "eval_numeric": field != "remarks" and not field.startswith("post_menu."),
        }
    return truth, field_by_col


def _snap_regions_x_to_fax_lines_all_targets(
    rectified: np.ndarray,
    regions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return snap_regions_x_to_local_fax_rulings(rectified, regions)


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
                **({"polygon": list(display_region["polygon"])} if isinstance(display_region.get("polygon"), list) else {}),
                **(
                    {"display_polygon": list(display_region["display_polygon"])}
                    if isinstance(display_region.get("display_polygon"), list)
                    else {}
                ),
            }
        )
    return restored


def _region_polygon(region: dict[str, Any]) -> list[tuple[int, int]] | None:
    polygon = region.get("display_polygon") if isinstance(region.get("display_polygon"), list) else region.get("polygon")
    if not isinstance(polygon, list) or len(polygon) < 4:
        return None
    points: list[tuple[int, int]] = []
    for point in polygon:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        try:
            points.append((int(round(float(point[0]))), int(round(float(point[1])))))
        except Exception:
            return None
    return points


def _draw_region_outline(
    draw: ImageDraw.ImageDraw,
    region: dict[str, Any],
    *,
    outline: tuple[int, int, int, int],
    width: int,
) -> None:
    polygon = _region_polygon(region)
    if polygon:
        draw.line(polygon + [polygon[0]], fill=outline, width=width, joint="curve")
        return
    box = region.get("bbox")
    if not isinstance(box, list) or len(box) != 4:
        return
    rx0, ry0, rx1, ry1 = [int(round(float(v))) for v in box]
    draw.rectangle((rx0, ry0, rx1, ry1), outline=outline, width=width)


def _region_center(region: dict[str, Any]) -> tuple[int, int] | None:
    polygon = _region_polygon(region)
    if polygon:
        return (
            int(round(sum(point[0] for point in polygon) / len(polygon))),
            int(round(sum(point[1] for point in polygon) / len(polygon))),
        )
    box = region.get("bbox")
    if not isinstance(box, list) or len(box) != 4:
        return None
    rx0, ry0, rx1, ry1 = [float(v) for v in box]
    return int(round((rx0 + rx1) / 2.0)), int(round((ry0 + ry1) / 2.0))


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
    contact_t0 = time.perf_counter()
    contact_sheet, usable_regions, polygons, skipped_regions = build_recognizer_contact_sheet(
        rectified_fax_bgr=raw_rectified_bgr,
        regions=regions,
        line_mask=None,
        mode="corner_cc",
        min_ink_area=18,
        min_ink_height=8,
    )
    contact_seconds = time.perf_counter() - contact_t0
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
        "contact_sheet_seconds": round(float(contact_seconds), 4),
        "contact_sheet_size": [int(contact_sheet.width), int(contact_sheet.height)],
        "contact_sheet_region_count": len(regions),
        "contact_sheet_usable_region_count": len(usable_regions),
        "contact_sheet_skipped_region_count": len(skipped_regions),
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
    header_intersection_points: list[dict[str, Any]] | None,
    facility_code: str,
    order_id: str,
    details: list[str],
) -> Image.Image:
    base = Image.fromarray(cv2.cvtColor(raw_rectified_bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _font(30)
    small_font = _font(13)
    for region in regions:
        _draw_region_outline(draw, region, outline=(0, 190, 0, 220), width=3)

    for point in header_intersection_points or []:
        try:
            px = int(round(float(point.get("x"))))
            py = int(round(float(point.get("y"))))
        except Exception:
            continue
        if not (0 <= px < base.width and 0 <= py < base.height):
            continue
        draw.ellipse((px - 7, py - 7, px + 7, py + 7), outline=(255, 145, 0, 255), width=3)
        draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=(255, 145, 0, 255))

    by_cell = {str(record.get("sheet_cell")): record for record in records}
    for region in regions:
        record = by_cell.get(str(region.get("sheet_cell")))
        has_ink = bool(record.get("ocr_candidate")) if isinstance(record, dict) else True
        inner_fill = (255, 0, 0, 255) if has_ink else (0, 105, 255, 255)
        center = _region_center(region)
        if center is None:
            continue
        cx, cy = center
        _draw_region_outline(draw, region, outline=(0, 115, 255, 125), width=1)
        draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=(255, 255, 255, 235))
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=inner_fill)

    for region in regions:
        record = by_cell.get(str(region.get("sheet_cell")))
        if not record:
            continue
        value = str(record.get("pred_digits") or "").strip()
        if not value:
            continue
        center = _region_center(region)
        if center is None:
            continue
        cx, cy = center
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


def _draw_sheet_review_base(
    *,
    raw_rectified_bgr: Any,
    regions: list[dict[str, Any]],
    quad_points: list[tuple[float, float]],
) -> Image.Image:
    """Clean review base for sheet values: no OCR dots or OCR digit labels."""
    base = Image.fromarray(cv2.cvtColor(raw_rectified_bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for region in regions:
        _draw_region_outline(draw, region, outline=(0, 190, 0, 220), width=3)
    image = Image.alpha_composite(base, layer).convert("RGB")
    return _draw_quad_points(image, quad_points, prefix="Q")


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


def _select_template_owned_eval_regions(target_regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Geometry comes from the facility template. Blank-menu rows are filtered
    # upstream when target regions are built.
    return list(target_regions)


def _accepted_header_intersection_points(axis_evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return header intersections for visual inspection.

    Prefer the points that actually participated in the structural matcher. If
    the matcher rejects the correction, still show the detected header-internal
    points so the live overlay exposes why the matcher did not engage.
    """
    if not isinstance(axis_evidence, dict):
        return []
    match = axis_evidence.get("header_intersection_x_match")
    if not isinstance(match, dict):
        return []
    points = match.get("header_intersection_points")
    if not isinstance(points, list):
        return []
    if not match.get("used"):
        return [point for point in points if isinstance(point, dict)]
    x_clusters = match.get("fax_x_clusters")
    y_clusters = match.get("fax_y_clusters")
    if not isinstance(x_clusters, list) or not isinstance(y_clusters, list):
        return [point for point in points if isinstance(point, dict)]

    accepted_x_point_indexes: set[int] = set()
    for cluster in x_clusters:
        if not isinstance(cluster, dict):
            continue
        for point_index in cluster.get("point_indexes") or []:
            try:
                accepted_x_point_indexes.add(int(point_index))
            except Exception:
                continue

    accepted_y_point_indexes: set[int] = set()
    for cluster in y_clusters:
        if not isinstance(cluster, dict):
            continue
        for point_index in cluster.get("point_indexes") or []:
            try:
                accepted_y_point_indexes.add(int(point_index))
            except Exception:
                continue

    accepted_indexes = accepted_x_point_indexes & accepted_y_point_indexes
    accepted: list[dict[str, Any]] = []
    for index in sorted(accepted_indexes):
        if 0 <= index < len(points) and isinstance(points[index], dict):
            accepted.append(points[index])
    return accepted


def _extract_vertical_line_peaks_for_target_frame(
    rectified: np.ndarray,
    regions: list[dict[str, Any]],
) -> list[tuple[int, float]]:
    if not regions:
        return []
    gray = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY) if rectified.ndim == 3 else rectified.copy()
    height, width = gray.shape[:2]
    min_x = int(min(float(region["bbox"][0]) for region in regions))
    max_x = int(max(float(region["bbox"][2]) for region in regions))
    min_y = int(min(float(region["bbox"][1]) for region in regions))
    max_y = int(max(float(region["bbox"][3]) for region in regions))

    # Target boxes may already be shifted when the template axis is wrong.
    # Search a wider table band, then let span evidence and profile matching
    # decide which lines are real boundaries.
    x0 = max(0, min_x - max(160, int(width * 0.22)))
    x1 = min(width, max_x + max(80, int(width * 0.08)))
    y0 = max(0, min_y - 80)
    y1 = min(height, max_y + 80)
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return []

    _threshold, binary = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel_h = max(35, int((y1 - y0) * 0.035))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_h))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel, iterations=1)
    projection = vertical.sum(axis=0).astype(np.float32) / 255.0
    if projection.size == 0:
        return []
    smooth = cv2.GaussianBlur(projection.reshape(1, -1), (1, 9), 0).ravel()
    threshold = max(float(np.percentile(smooth, 82)), float(smooth.max()) * 0.16, 6.0)
    candidates = np.where(smooth >= threshold)[0]
    if candidates.size == 0:
        return []

    peaks: list[tuple[int, float]] = []
    start = int(candidates[0])
    prev = int(candidates[0])
    for value in candidates[1:]:
        value = int(value)
        if value > prev + 2:
            segment = smooth[start : prev + 1]
            local = int(np.argmax(segment)) + start
            peaks.append((int(x0 + local), float(smooth[local])))
            start = value
        prev = value
    segment = smooth[start : prev + 1]
    local = int(np.argmax(segment)) + start
    peaks.append((int(x0 + local), float(smooth[local])))
    return peaks


def _vertical_peak_span_evidence(
    rectified: np.ndarray,
    regions: list[dict[str, Any]],
    peaks: list[tuple[int, float]],
) -> list[dict[str, Any]]:
    if not peaks or not regions:
        return []
    gray = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY) if rectified.ndim == 3 else rectified.copy()
    y0 = max(0, int(min(float(region["bbox"][1]) for region in regions)) - 40)
    y1 = min(gray.shape[0], int(max(float(region["bbox"][3]) for region in regions)) + 40)
    roi = gray[y0:y1, :]
    if roi.size == 0:
        return []
    _threshold, binary = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 45))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel, iterations=1)
    vertical = cv2.dilate(vertical, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5)), iterations=1)
    height = max(1, y1 - y0)
    enriched: list[dict[str, Any]] = []
    for peak_x, score in peaks:
        x0 = max(0, int(peak_x) - 2)
        x1 = min(vertical.shape[1], int(peak_x) + 3)
        strip = vertical[:, x0:x1]
        if strip.size == 0:
            row_hits = np.zeros((height,), dtype=bool)
        else:
            row_hits = (strip.sum(axis=1) / 255.0) >= 1.0
        coverage_ratio = float(row_hits.sum()) / float(height)
        bands = np.array_split(row_hits, 14)
        band_hits = [bool(band.size and band.any()) for band in bands]
        hit_band_ratio = float(sum(1 for hit in band_hits if hit)) / float(len(band_hits))
        body_bands = band_hits[2:]
        body_band_ratio = float(sum(1 for hit in body_bands if hit)) / float(max(1, len(body_bands)))
        top_band_hit = any(band_hits[:2])
        bottom_band_hit = any(band_hits[-2:])
        full_height_valid = bool(hit_band_ratio >= 0.72 and top_band_hit and bottom_band_hit)
        body_height_valid = bool(body_band_ratio >= 0.84 and bottom_band_hit)
        enriched.append(
            {
                "x": int(peak_x),
                "score": float(score),
                "coverage_ratio": round(coverage_ratio, 4),
                "hit_band_ratio": round(hit_band_ratio, 4),
                "body_band_ratio": round(body_band_ratio, 4),
                "top_band_hit": top_band_hit,
                "bottom_band_hit": bottom_band_hit,
                "full_height_valid": full_height_valid,
                "body_height_valid": body_height_valid,
                "valid": bool(full_height_valid or body_height_valid),
            }
        )
    return enriched


def _snap_boundaries_by_template_profile(
    original_boundaries: list[int],
    peaks: list[dict[str, Any]],
) -> tuple[list[int], list[dict[str, Any]], dict[str, Any]]:
    if len(original_boundaries) < 2:
        return original_boundaries, [], {"used": False, "reason": "insufficient_boundaries"}
    valid_peaks = [peak for peak in peaks if peak.get("valid")]
    states: list[list[dict[str, Any]]] = []
    for index, boundary in enumerate(original_boundaries):
        left_gap = (
            original_boundaries[index] - original_boundaries[index - 1]
            if index > 0
            else original_boundaries[1] - original_boundaries[0]
        )
        right_gap = (
            original_boundaries[index + 1] - original_boundaries[index]
            if index < len(original_boundaries) - 1
            else original_boundaries[-1] - original_boundaries[-2]
        )
        neighbor_gap = max(1, min(int(left_gap), int(right_gap)))
        tolerance = max(85, min(170, int(neighbor_gap * 0.85)))
        boundary_states: dict[int, dict[str, Any]] = {
            int(boundary): {
                "x": int(boundary),
                "score": 0.0,
                "source": "template",
                "local_cost": 6.0,
                "candidate_count": 0,
            }
        }
        candidate_count = 0
        for peak in valid_peaks:
            peak_x = int(peak["x"])
            delta = abs(peak_x - int(boundary))
            if index == 0 and peak_x < int(boundary) - 25:
                continue
            if delta > tolerance:
                continue
            candidate_count += 1
            score_bonus = min(float(peak.get("score") or 0.0) / 260.0, 1.0) * 0.25
            span_penalty = 0.0 if peak.get("full_height_valid") else 1.2
            local_cost = (delta / max(1.0, float(tolerance))) * 5.0 + span_penalty - score_bonus
            existing = boundary_states.get(peak_x)
            if existing is None or local_cost < float(existing["local_cost"]):
                boundary_states[peak_x] = {
                    "x": peak_x,
                    "score": float(peak.get("score") or 0.0),
                    "source": "fax",
                    "local_cost": float(local_cost),
                    "candidate_count": candidate_count,
                    "coverage_ratio": peak.get("coverage_ratio"),
                    "hit_band_ratio": peak.get("hit_band_ratio"),
                    "body_band_ratio": peak.get("body_band_ratio"),
                    "full_height_valid": peak.get("full_height_valid"),
                    "body_height_valid": peak.get("body_height_valid"),
                }
        for state in boundary_states.values():
            state["candidate_count"] = candidate_count
        states.append(sorted(boundary_states.values(), key=lambda item: (float(item["local_cost"]), int(item["x"]))))

    dp: list[dict[int, tuple[float, int | None]]] = []
    prev: list[dict[int, int | None]] = []
    for index, boundary_states in enumerate(states):
        dp_row: dict[int, tuple[float, int | None]] = {}
        prev_row: dict[int, int | None] = {}
        for state in boundary_states:
            x = int(state["x"])
            local_cost = float(state["local_cost"])
            if index == 0:
                dp_row[x] = (local_cost, None)
                prev_row[x] = None
                continue
            template_gap = max(1.0, float(original_boundaries[index] - original_boundaries[index - 1]))
            best_cost = float("inf")
            best_prev: int | None = None
            for prev_x, (prev_cost, _prev_prev) in dp[index - 1].items():
                gap = float(x - prev_x)
                if gap <= 0:
                    continue
                gap_ratio = gap / template_gap
                if gap < max(28.0, template_gap * 0.45) or gap_ratio > 1.80:
                    transition_cost = 80.0
                else:
                    transition_cost = abs(gap_ratio - 1.0) * 5.0
                total = float(prev_cost) + local_cost + transition_cost
                if total < best_cost:
                    best_cost = total
                    best_prev = prev_x
            if best_prev is not None:
                dp_row[x] = (best_cost, best_prev)
                prev_row[x] = best_prev
        if not dp_row:
            return original_boundaries, [], {"used": False, "reason": "no_monotonic_profile_match"}
        dp.append(dp_row)
        prev.append(prev_row)

    last_x = min(dp[-1].items(), key=lambda item: item[1][0])[0]
    snapped = [last_x]
    for index in range(len(states) - 1, 0, -1):
        previous_x = prev[index].get(snapped[-1])
        if previous_x is None:
            return original_boundaries, [], {"used": False, "reason": "profile_backtrack_failed"}
        snapped.append(previous_x)
    snapped.reverse()
    state_by_index_x = [{int(state["x"]): state for state in state_list} for state_list in states]
    assignments: list[dict[str, Any]] = []
    for index, (template_x, snapped_x) in enumerate(zip(original_boundaries, snapped)):
        state = state_by_index_x[index][int(snapped_x)]
        assignments.append(
            {
                "template_x": int(template_x),
                "snapped_x": int(snapped_x),
                "delta": int(snapped_x - template_x),
                "score": round(float(state.get("score") or 0.0), 2),
                "source": state.get("source"),
                "candidate_count": int(state.get("candidate_count") or 0),
                "coverage_ratio": state.get("coverage_ratio"),
                "hit_band_ratio": state.get("hit_band_ratio"),
                "body_band_ratio": state.get("body_band_ratio"),
                "full_height_valid": state.get("full_height_valid"),
                "body_height_valid": state.get("body_height_valid"),
            }
        )
    return [int(value) for value in snapped], assignments, {"used": True, "valid_peak_count": len(valid_peaks)}


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
    total_t0 = time.perf_counter()
    preprocess_t0 = time.perf_counter()
    pre = _build_preprocess_for_ocr(item=item, page=page_index, render_width=render_width)
    preprocess_seconds = time.perf_counter() - preprocess_t0
    eval_regions = _select_template_owned_eval_regions(pre["target_regions"])
    truth, field_by_col = _build_truth_for_facility(draft_sheet, eval_regions)
    preprocess_axis_evidence = pre.get("axis_evidence") if isinstance(pre.get("axis_evidence"), dict) else {}
    preprocess_snap_debug = (
        preprocess_axis_evidence.get("target_local_grid_snap")
        if isinstance(preprocess_axis_evidence.get("target_local_grid_snap"), dict)
        else {}
    )
    if preprocess_snap_debug.get("applied"):
        snapped_regions = eval_regions
        snap_debug = {**preprocess_snap_debug, "source": "preprocess_target_regions"}
        snap_seconds = 0.0
    else:
        snap_t0 = time.perf_counter()
        snapped_regions, snap_debug = _snap_regions_x_to_fax_lines_all_targets(pre["raw_rectified"], eval_regions)
        snap_seconds = time.perf_counter() - snap_t0
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
            "preprocess_seconds": round(float(preprocess_seconds), 4),
            "snap_seconds": round(float(snap_seconds), 4),
            "contact_sheet_seconds": ocr_metrics.get("contact_sheet_seconds"),
            "contact_sheet_size": ocr_metrics.get("contact_sheet_size"),
            "contact_sheet_region_count": ocr_metrics.get("contact_sheet_region_count"),
            "contact_sheet_usable_region_count": ocr_metrics.get("contact_sheet_usable_region_count"),
            "contact_sheet_skipped_region_count": ocr_metrics.get("contact_sheet_skipped_region_count"),
            "target_grid_snap_applied": bool(snap_debug.get("applied")),
            "target_grid_snap_reason": snap_debug.get("reason"),
            "target_grid_snap_source": snap_debug.get("source") or "runtime_resnap",
            "target_grid_snap_snapped_region_count": snap_debug.get("snapped_region_count"),
            "target_grid_snap_fallback_region_count": snap_debug.get("fallback_region_count"),
            "target_grid_snap_fallback_reason_counts": snap_debug.get("fallback_reason_counts"),
            "target_grid_snap_required_min_snapped_region_count": snap_debug.get("required_min_snapped_region_count"),
            "target_grid_snap_row_boundary_count": snap_debug.get("row_boundary_count"),
            "target_grid_snap_row_boundary_curve_count": snap_debug.get("row_boundary_curve_count"),
            "target_grid_snap_column_boundary_curve_count": snap_debug.get("column_boundary_curve_count"),
            "target_grid_snap_row_curve_reject_samples": snap_debug.get("row_curve_reject_samples"),
            "target_grid_snap_row_curve_repair_debug": snap_debug.get("row_curve_repair_debug"),
        }
    )
    axis_evidence = pre.get("axis_evidence") if isinstance(pre.get("axis_evidence"), dict) else {}
    row_dewarp_evidence = axis_evidence.get("row_dewarp") if isinstance(axis_evidence.get("row_dewarp"), dict) else {}
    row_slant_evidence = (
        axis_evidence.get("row_slant_dewarp") if isinstance(axis_evidence.get("row_slant_dewarp"), dict) else {}
    )
    preprocess_timings = (
        axis_evidence.get("preprocess_timings") if isinstance(axis_evidence.get("preprocess_timings"), dict) else {}
    )
    metrics.update(
        {
            "row_dewarp_applied": bool(row_dewarp_evidence.get("applied")),
            "row_dewarp_reason": row_dewarp_evidence.get("reason"),
            "row_dewarp_method": row_dewarp_evidence.get("method"),
            "row_slant_dewarp_applied": bool(row_slant_evidence.get("applied")),
            "row_slant_dewarp_reason": row_slant_evidence.get("reason"),
            "row_slant_dewarp_method": row_slant_evidence.get("method"),
            "row_slant_dewarp_fitted_row_count": row_slant_evidence.get("fitted_row_count"),
            "row_slant_dewarp_nonzero_slope_count": row_slant_evidence.get("nonzero_slope_count"),
            "row_slant_dewarp_max_abs_shift": row_slant_evidence.get("max_abs_shift"),
            "preprocess_timings": preprocess_timings,
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
        "orange circles: detected header-internal intersections for header-axis alignment inspection",
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
        header_intersection_points=_accepted_header_intersection_points(pre.get("axis_evidence")),
        facility_code=facility_code,
        order_id=order_id,
        details=details,
    )
    sheet_review_base = _draw_sheet_review_base(
        raw_rectified_bgr=pre["raw_rectified"],
        regions=snapped_regions,
        quad_points=quad_points,
    )
    page_png = facility_dir / "best_method_overlay.png"
    page_pdf = facility_dir / "best_method_overlay.pdf"
    sheet_review_base_path = facility_dir / "best_method_sheet_review_base.png"
    records_path = facility_dir / "best_method_records.json"
    regions_path = facility_dir / "best_method_ocr_regions.json"
    summary_path = facility_dir / "best_method_summary.json"
    contact_sheet_path = facility_dir / "best_method_contact_sheet.png"
    sheet_values_path = facility_dir / "best_method_sheet_values.json"
    review_page.save(page_png)
    sheet_review_base.save(sheet_review_base_path)
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
    metrics["total_seconds"] = round(float(time.perf_counter() - total_t0), 4)
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
            "sheet_review_base": str(sheet_review_base_path),
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
        summary_path = facility_dir / "best_method_summary.json"
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
