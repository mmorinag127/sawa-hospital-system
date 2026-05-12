from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from src.services import hakodate_assignment_service
from src.services.hakodate_fixed_quad_registration_service import (
    build_fixed_quad_template_registration,
    rectify_fax_to_template_grid,
    render_pdf_page_to_bgr,
    render_template_pdf_to_canvas,
    resolve_fixed_quad_px_for_manifest_item,
    resolve_template_axes_from_manifest_or_image,
)
from src.services.hakodate_step_review_pipeline_service import (
    WEEK_SHEET_NAME,
    _align_axes,
    _bbox_quad_points,
    _draw_quad_points,
    _draw_merge_aware_grid,
    _make_review_canvas,
    _post_menu_target_regions,
    _source_template_name,
    _split_line_masks,
    _write_pdf_from_pages,
)


@dataclass(frozen=True)
class HakodatePreprocessingResult:
    page: int
    facility_code: str
    order_id: str
    fax_pdf: str
    template_pdf: str
    source_template: str
    target_cell_count: int
    quality_gate: dict[str, Any]
    alignment_evidence: dict[str, Any]
    outputs: dict[str, str]


def _extract_vertical_line_peaks(rectified: np.ndarray, regions: list[dict[str, Any]]) -> list[tuple[int, float]]:
    if not regions:
        return []
    gray = cv2.cvtColor(rectified, cv2.COLOR_BGR2GRAY) if rectified.ndim == 3 else rectified
    x0 = max(0, int(min(float(region["bbox"][0]) for region in regions)) - 50)
    x1 = min(gray.shape[1], int(max(float(region["bbox"][2]) for region in regions)) + 50)
    y0 = max(0, int(min(float(region["bbox"][1]) for region in regions)) - 40)
    y1 = min(gray.shape[0], int(max(float(region["bbox"][3]) for region in regions)) + 40)
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return []
    _threshold, binary = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 45))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel, iterations=1)
    vertical = cv2.dilate(vertical, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5)), iterations=1)
    projection = vertical.sum(axis=0).astype(np.float32) / 255.0
    if projection.size == 0:
        return []
    smooth = np.convolve(projection, np.ones(7, dtype=np.float32) / 7.0, mode="same")
    threshold = max(20.0, float(np.percentile(smooth, 95)) * 0.35)
    segments: list[tuple[int, int]] = []
    in_segment = False
    start = 0
    for index, value in enumerate(smooth):
        if value >= threshold and not in_segment:
            start = index
            in_segment = True
        elif (value < threshold or index == len(smooth) - 1) and in_segment:
            end = index if value < threshold else index + 1
            if end - start >= 2:
                segments.append((start, end))
            in_segment = False
    peaks: list[tuple[int, float]] = []
    for start, end in segments:
        segment = smooth[start:end]
        if segment.size == 0:
            continue
        positions = np.arange(start, end, dtype=np.float32)
        peak_x = int(round(float(np.average(positions, weights=segment))))
        peaks.append((x0 + peak_x, float(np.max(segment))))
    merged: list[tuple[int, float]] = []
    for peak_x, score in peaks:
        if not merged or peak_x - merged[-1][0] > 10:
            merged.append((peak_x, score))
        elif score > merged[-1][1]:
            merged[-1] = (peak_x, score)
    return merged


def snap_target_region_x_boundaries(
    rectified: np.ndarray,
    regions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target_regions = [
        region for region in regions if isinstance(region.get("bbox"), list) and len(region["bbox"]) == 4
    ]
    original_boundaries = sorted(
        {int(round(float(region["bbox"][0]))) for region in target_regions}
        | {int(round(float(region["bbox"][2]))) for region in target_regions}
    )
    return regions, {
        "applied": False,
        "reason": "disabled_after_header_intersection_axis_alignment",
        "original_boundaries": original_boundaries,
    }


def _first_logical_target(region: dict[str, Any]) -> dict[str, Any]:
    logical_targets = region.get("logical_targets")
    if isinstance(logical_targets, list):
        for target in logical_targets:
            if isinstance(target, dict):
                return target
    return {}


def target_cell_map_from_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for region in regions:
        box = region.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            continue
        first_target = _first_logical_target(region)
        x0, y0, x1, y1 = [float(value) for value in box]
        sheet_cell = str(region.get("sheet_cell") or region.get("region_id") or "")
        cells.append(
            {
                "target_cell_id": str(region.get("target_cell_id") or sheet_cell or region.get("region_id") or ""),
                "region_id": region.get("region_id"),
                "sheet_cell": sheet_cell,
                "worksheet_row": region.get("worksheet_row"),
                "worksheet_col": region.get("worksheet_col"),
                "semantic_field": region.get("field") or region.get("semantic_field"),
                "field_label": region.get("field_label"),
                "date": region.get("date") or first_target.get("date"),
                "daypart": region.get("daypart") or first_target.get("daypart"),
                "menu_name": region.get("menu_name") or first_target.get("menu_name"),
                "bbox": [x0, y0, x1, y1],
                "center": [(x0 + x1) / 2.0, (y0 + y1) / 2.0],
                "merged_cell": region.get("merged_cell"),
                "logical_targets": region.get("logical_targets") or [],
                "covered_sheet_cells": region.get("covered_sheet_cells") or [],
                "x_snap": region.get("x_snap"),
            }
        )
    return cells


def draw_target_cell_map_overlay(
    *,
    rectified_bgr: np.ndarray,
    regions: list[dict[str, Any]],
    quad_points: list[tuple[float, float]],
) -> Image.Image:
    base = Image.fromarray(cv2.cvtColor(rectified_bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    valid_boxes = [region["bbox"] for region in regions if isinstance(region.get("bbox"), list) and len(region["bbox"]) == 4]
    if valid_boxes:
        x_edges = sorted({int(round(float(value))) for box in valid_boxes for value in (box[0], box[2])})
        y_edges = sorted({int(round(float(value))) for box in valid_boxes for value in (box[1], box[3])})
        x0 = min(x_edges)
        x1 = max(x_edges)
        y0 = min(y_edges)
        y1 = max(y_edges)
        for x in x_edges:
            draw.line((x, y0, x, y1), fill=(0, 190, 0, 230), width=3)
        for y in y_edges:
            draw.line((x0, y, x1, y), fill=(0, 190, 0, 210), width=2)
    for box in valid_boxes:
        rx0, ry0, rx1, ry1 = [int(round(float(value))) for value in box]
        cx = int(round((rx0 + rx1) / 2.0))
        cy = int(round((ry0 + ry1) / 2.0))
        draw.rectangle((rx0, ry0, rx1, ry1), outline=(0, 115, 255, 125), width=1)
        draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=(255, 255, 255, 235))
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(255, 0, 0, 255))
    return _draw_quad_points(Image.alpha_composite(base, layer).convert("RGB"), quad_points, prefix="Q")


def build_hakodate_preprocessing_for_manifest_item(
    *,
    item: dict[str, Any],
    page: int,
    output_dir: Path | None = None,
    render_width: int = 1864,
) -> tuple[HakodatePreprocessingResult, Image.Image]:
    facility_code = str(item["facility_code"])
    order_id = str(item["order_id"])
    existing_step2 = cv2.imread(item["step2_png"])
    if existing_step2 is None:
        raise ValueError(f"step2 canvas not found: {item['step2_png']}")
    canvas_height, canvas_width = existing_step2.shape[:2]
    template = render_template_pdf_to_canvas(item["template_pdf"], width=canvas_width, height=canvas_height)
    template_xs, template_ys, _all_xs, _all_ys = resolve_template_axes_from_manifest_or_image(
        item=item,
        template_image=template,
        manifest_template_bbox=item["template_bbox"],
    )
    week_sheet_name = str(item.get("week_sheet_name") or WEEK_SHEET_NAME).strip() or WEEK_SHEET_NAME
    worksheet = hakodate_assignment_service._worksheet_for_manifest_structure_template(  # noqa: SLF001
        item=item,
        facility_id=facility_code,
        week_sheet_name=week_sheet_name,
    )
    quad_px, quad_source, quad_estimate = resolve_fixed_quad_px_for_manifest_item(item)
    registration, _step_images_np = build_fixed_quad_template_registration(
        facility_code=facility_code,
        order_id=order_id,
        fax_pdf=item["fax_pdf"],
        template_pdf=item["template_pdf"],
        quad_px=quad_px,
        manifest_template_bbox=item["template_bbox"],
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        render_width=render_width,
        quad_source=quad_source,
        output_dir=None,
        template_axes_x=template_xs,
        template_axes_y=template_ys,
    )
    original = render_pdf_page_to_bgr(item["fax_pdf"], width=render_width)
    table_bbox = registration.template_outer_grid_bbox_used
    raw_rectified = rectify_fax_to_template_grid(
        original,
        quad_px=quad_px,
        table_bbox=table_bbox,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    horizontal_line_mask, _vertical_line_mask = _split_line_masks(raw_rectified)
    aligned_xs, aligned_ys, axis_evidence, _axis_match_image = _align_axes(
        rectified_fax=raw_rectified,
        template_xs=template_xs,
        template_ys=template_ys,
        worksheet=worksheet,
    )
    grid_overlay, merge_evidence = _draw_merge_aware_grid(
        worksheet=worksheet,
        rectified_fax=raw_rectified,
        xs=aligned_xs,
        ys=aligned_ys,
        horizontal_line_mask=horizontal_line_mask,
    )
    target_regions, target_evidence = _post_menu_target_regions(
        worksheet=worksheet,
        column_edges=[float(value) for value in aligned_xs],
        row_edges=[float(value) for value in aligned_ys],
        horizontal_line_mask=horizontal_line_mask,
    )
    snapped_regions, snap_evidence = snap_target_region_x_boundaries(raw_rectified, target_regions)
    target_cells = target_cell_map_from_regions(snapped_regions)
    rectified_quad_points = _bbox_quad_points(table_bbox)
    overlay = draw_target_cell_map_overlay(
        rectified_bgr=raw_rectified,
        regions=snapped_regions,
        quad_points=rectified_quad_points,
    )
    review_page = _make_review_canvas(
        title="Hakodate preprocessing target-cell map",
        facility_code=facility_code,
        order_id=order_id,
        image=overlay,
        details=[
            f"source_template={_source_template_name(facility_code)}",
            f"target_cells={len(target_cells)}",
            f"x_snap_applied={snap_evidence.get('applied')}",
        ],
    )
    outputs: dict[str, str] = {}
    if output_dir is not None:
        case_dir = output_dir / f"{page:02d}_{facility_code}_{order_id}"
        case_dir.mkdir(parents=True, exist_ok=True)
        overlay_path = case_dir / "preprocessing_target_cell_overlay.png"
        review_path = case_dir / "preprocessing_target_cell_review_page.png"
        cells_path = case_dir / "target_cell_map.json"
        evidence_path = case_dir / "alignment_evidence.json"
        overlay.save(overlay_path)
        review_page.save(review_path)
        cells_path.write_text(json.dumps(target_cells, ensure_ascii=False, indent=2), encoding="utf-8")
        evidence = {
            **axis_evidence,
            "merge": merge_evidence,
            "target": target_evidence,
            "x_snap": snap_evidence,
            "quad_estimate": quad_estimate,
        }
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs = {
            "overlay": str(overlay_path),
            "review_page": str(review_path),
            "target_cell_map": str(cells_path),
            "alignment_evidence": str(evidence_path),
        }
    else:
        evidence = {
            **axis_evidence,
            "merge": merge_evidence,
            "target": target_evidence,
            "x_snap": snap_evidence,
            "quad_estimate": quad_estimate,
        }
    quality_gate = {
        "ok": bool(target_cells) and bool(snap_evidence.get("applied")),
        "target_cell_count": len(target_cells),
        "x_snap_applied": bool(snap_evidence.get("applied")),
        "blockers": [] if bool(target_cells) and bool(snap_evidence.get("applied")) else ["target_cell_map_incomplete"],
    }
    result = HakodatePreprocessingResult(
        page=page,
        facility_code=facility_code,
        order_id=order_id,
        fax_pdf=str(item["fax_pdf"]),
        template_pdf=str(item["template_pdf"]),
        source_template=str(_source_template_name(facility_code)),
        target_cell_count=len(target_cells),
        quality_gate=quality_gate,
        alignment_evidence=evidence,
        outputs=outputs,
    )
    return result, review_page


def build_all_facility_hakodate_preprocessing_pdf(
    *,
    manifest_path: Path,
    output_dir: Path,
    render_width: int = 1864,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("results") if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        raise ValueError("manifest results are missing")
    output_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Image.Image] = []
    results: list[dict[str, Any]] = []
    for page, item in enumerate(items, start=1):
        result, review_page = build_hakodate_preprocessing_for_manifest_item(
            item=item,
            page=page,
            output_dir=output_dir,
            render_width=render_width,
        )
        pages.append(review_page)
        results.append(asdict(result))
    pdf_path = output_dir / "hakodate_preprocessing_target_cell_map_all_facilities.pdf"
    summary_path = output_dir / "hakodate_preprocessing_summary.json"
    _write_pdf_from_pages(pages, pdf_path)
    summary = {
        "count": len(results),
        "pdf": str(pdf_path),
        "total_target_cell_count": sum(int(item["target_cell_count"]) for item in results),
        "blocker_count": sum(len(item["quality_gate"].get("blockers") or []) for item in results),
        "results": results,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
