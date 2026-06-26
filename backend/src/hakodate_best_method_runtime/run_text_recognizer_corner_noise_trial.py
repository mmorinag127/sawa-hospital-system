#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

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
sys.path.insert(0, str(BACKEND_ROOT))

from src.services.hakodate_cell_ocr_batch_service import (  # noqa: E402
    _normalize_digits,
    _expanded_cell_box,
    _erase_known_cell_frame,
    _remove_small_noise_only,
    _safe_int_box,
    _bgr_from_pil,
    _build_preprocess_for_ocr,
    _draw_text_safe,
    _load_overlay_font,
    draw_ocr_results_overlay,
    sheet_assignments_from_ocr_regions,
    sheet_value_grid_from_assignments,
)
from src.services.hakodate_step_review_pipeline_service import (  # noqa: E402
    _make_review_canvas,
    _split_line_masks,
    _write_pdf_from_pages,
)
from src.services.yomitoku_text_recognizer_topk import YomitokuTextRecognizerTopKWrapper  # noqa: E402


DEFAULT_BASE = WORKSPACE / "tmp" / "outer_quad_eval_correct_20260426"
DEFAULT_MANIFEST = DEFAULT_BASE / "step123_no_code_change_20260427" / "manifest.json"
DEFAULT_BASELINE_SUMMARY = DEFAULT_BASE / "hakodate_cell_ocr_batch_20260428_current" / "cell_ocr_summary.json"
DEFAULT_OUTPUT_DIR = DEFAULT_BASE / "hakodate_text_recognizer_corner_noise_trial_20260430"
DEFAULT_TRUTH_ROOT = WORKSPACE / "tmp" / "hakodate_text_recognizer_trial_20260428" / "best_method_overlay_all_facilities"
DEFAULT_TRUTH_RECORDS = (
    DEFAULT_TRUTH_ROOT
    / "01_FAC00001_ORD7a83fd79"
    / "best_method_records.json"
)
FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
TRIAL_ENGINE = "yomitoku_text_recognizer_corner_noise_trial"
DEFAULT_RECOGNIZER_MODES = ("raw", "clean", "corner_cc")


def _load_text_recognizer(device: str):
    from yomitoku import TextRecognizer  # noqa: PLC0415

    return TextRecognizer(device=device, visualize=False)


def _clean_text_for_number(value: object) -> str:
    text = str(value or "").strip().translate(FULLWIDTH_DIGIT_TRANS)
    text = text.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1").replace("|", "1")
    return re.sub(r"[^0-9]", "", text)


def _remove_table_lines(gray: np.ndarray, line_mask: np.ndarray) -> np.ndarray:
    if line_mask.size == 0:
        return gray
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    line_mask = cv2.dilate(line_mask, kernel, iterations=1)
    cleaned = gray.copy()
    cleaned[line_mask > 0] = 255
    return cleaned


def _remove_noise_components(binary: np.ndarray) -> np.ndarray:
    ink = 255 - binary
    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    kept = np.zeros_like(ink)
    height, width = ink.shape[:2]
    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        if area < 5:
            continue
        if w > width * 0.62 and h <= max(3, height * 0.12):
            continue
        if h > height * 0.72 and w <= max(3, width * 0.10):
            continue
        kept[labels == label] = 255
    return 255 - kept


def _foreground_centered(binary: np.ndarray, *, out_width: int, out_height: int) -> tuple[Image.Image, dict[str, Any]]:
    ink = 255 - binary
    points = cv2.findNonZero(ink)
    canvas = np.full((out_height, out_width), 255, dtype=np.uint8)
    if points is None:
        return Image.fromarray(canvas).convert("RGB"), {"ink_area": 0, "bbox": None, "bbox_width": 0, "bbox_height": 0}
    x, y, w, h = cv2.boundingRect(points)
    crop = binary[y : y + h, x : x + w]
    stats = {
        "ink_area": int(np.count_nonzero(ink)),
        "bbox": [int(x), int(y), int(w), int(h)],
        "bbox_width": int(w),
        "bbox_height": int(h),
    }
    if crop.size == 0:
        return Image.fromarray(canvas).convert("RGB"), stats
    max_w = max(1, min(out_width, out_width - 18 if out_width > 18 else out_width))
    max_h = max(1, min(out_height, out_height - 18 if out_height > 18 else out_height))
    fit_scale = min(max_w / max(1, crop.shape[1]), max_h / max(1, crop.shape[0]))
    # Very wide line/noise components must still fit in the contact-sheet slot.
    scale = fit_scale if fit_scale < 0.2 else max(0.2, min(fit_scale, 5.5))
    resized_width = max(1, min(max_w, int(round(crop.shape[1] * scale))))
    resized_height = max(1, min(max_h, int(round(crop.shape[0] * scale))))
    resized = cv2.resize(
        crop,
        (resized_width, resized_height),
        interpolation=cv2.INTER_CUBIC,
    )
    px = (out_width - resized.shape[1]) // 2
    py = (out_height - resized.shape[0]) // 2
    canvas[py : py + resized.shape[0], px : px + resized.shape[1]] = resized
    return Image.fromarray(canvas).convert("RGB"), stats


def _preprocess_crop_for_recognizer(
    crop_bgr: np.ndarray,
    *,
    line_mask: np.ndarray | None,
    slot_width: int,
    slot_height: int,
    mode: str,
) -> tuple[Image.Image, dict[str, Any]]:
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.ndim == 3 else crop_bgr.copy()
    if mode == "clean" and line_mask is not None:
        gray = _remove_table_lines(gray, line_mask)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gray = cv2.equalizeHist(gray)
    _threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if mode == "clean":
        binary = _remove_noise_components(binary)
    return _foreground_centered(binary, out_width=slot_width - 10, out_height=slot_height - 10)


def _preprocess_dynamic_crop_for_recognizer(
    crop_bgr: np.ndarray,
    *,
    cell_box: list[float],
    crop_box: tuple[int, int, int, int],
    slot_width: int,
    slot_height: int,
) -> tuple[Image.Image, dict[str, Any]]:
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.ndim == 3 else crop_bgr.copy()
    frame_removed = _erase_known_cell_frame(gray, cell_box=cell_box, crop_box=crop_box)
    cleaned = _remove_small_noise_only(frame_removed)
    _threshold, binary = cv2.threshold(cleaned, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _foreground_centered(binary, out_width=slot_width - 10, out_height=slot_height - 10)


def _clear_printed_zero_digits(ink: np.ndarray, components: list[dict[str, Any]]) -> str:
    kept_components = [item for item in components if item.get("kept")]
    if len(kept_components) != 1:
        return ""
    height, width = ink.shape[:2]
    component = kept_components[0]
    x, y, w, h = [int(value) for value in component.get("bbox") or [0, 0, 0, 0]]
    area = int(component.get("area") or 0)
    if w <= 0 or h <= 0 or area <= 0:
        return ""
    aspect = float(w) / max(1.0, float(h))
    fill_ratio = float(area) / max(1.0, float(w * h))
    cx, cy = [float(value) for value in component.get("centroid") or [0.0, 0.0]]
    centered = width * 0.28 <= cx <= width * 0.72 and height * 0.20 <= cy <= height * 0.82
    digit_sized = h >= max(10, int(round(height * 0.30))) and w >= max(5, int(round(width * 0.08)))
    zero_shaped = 0.30 <= aspect <= 0.85 and 0.18 <= fill_ratio <= 0.58
    if not (centered and digit_sized and zero_shaped):
        return ""
    component_mask = ink[y : y + h, x : x + w]
    if component_mask.size == 0:
        return ""
    contours, hierarchy = cv2.findContours(component_mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None or not contours:
        return ""
    hole_count = sum(1 for item in hierarchy[0] if int(item[3]) >= 0)
    if hole_count != 1:
        return ""
    return "0"


def _preprocess_corner_component_crop_for_recognizer(
    crop_bgr: np.ndarray,
    *,
    cell_box: list[float],
    crop_box: tuple[int, int, int, int],
    slot_width: int,
    slot_height: int,
    mode: str = "corner_cc",
) -> tuple[Image.Image, dict[str, Any]]:
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.ndim == 3 else crop_bgr.copy()
    frame_removed = _erase_known_cell_frame(gray, cell_box=cell_box, crop_box=crop_box)
    denoised = _remove_small_noise_only(frame_removed)
    if mode == "corner_cc_sharp":
        blur = cv2.GaussianBlur(denoised, (0, 0), 1.2)
        denoised = cv2.addWeighted(denoised, 1.7, blur, -0.7, 0)
    elif mode != "corner_cc_noblur":
        denoised = cv2.GaussianBlur(denoised, (3, 3), 0)
    if mode == "corner_cc_adaptive":
        binary = cv2.adaptiveThreshold(
            denoised,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            21,
            9,
        )
    else:
        _threshold, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if mode == "corner_cc_close":
        ink_for_close = 255 - binary
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        ink_for_close = cv2.morphologyEx(ink_for_close, cv2.MORPH_CLOSE, kernel, iterations=1)
        binary = 255 - ink_for_close
    ink = 255 - binary
    height, width = ink.shape[:2]
    border_x = max(3, int(round(width * 0.08)))
    border_y = max(3, int(round(height * 0.12)))
    corner_x = max(6, int(round(width * 0.20)))
    corner_y = max(6, int(round(height * 0.24)))
    min_digit_h = max(8, int(round(height * 0.22)))
    min_digit_area = max(16, int(round(width * height * 0.010)))
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(ink, connectivity=8)
    kept = np.zeros_like(ink)
    components: list[dict[str, Any]] = []
    for label in range(1, num_labels):
        x, y, w, h, area = [int(value) for value in stats[label]]
        cx, cy = [float(value) for value in centroids[label]]
        touches_edge = x <= border_x or y <= border_y or x + w >= width - border_x or y + h >= height - border_y
        touches_side_edge = x <= border_x or x + w >= width - border_x
        in_corner = (cx <= corner_x or cx >= width - corner_x) and (cy <= corner_y or cy >= height - corner_y)
        line_like = (w >= width * 0.45 and h <= max(3, height * 0.10)) or (
            h >= height * 0.55 and w <= max(3, width * 0.08)
        )
        edge_sliver = touches_side_edge and w <= max(3, int(round(width * 0.025))) and h >= height * 0.18
        tiny = area < min_digit_area and h < min_digit_h
        aspect = float(w) / max(1.0, float(h))
        digit_like = (
            h >= min_digit_h
            and area >= min_digit_area
            and 0.08 <= aspect <= 1.65
            and not line_like
        )
        reject = bool(edge_sliver or (line_like and touches_edge) or (touches_edge and tiny) or (in_corner and area < min_digit_area * 2))
        components.append(
            {
                "bbox": [x, y, w, h],
                "area": area,
                "centroid": [round(cx, 2), round(cy, 2)],
                "touches_edge": touches_edge,
                "edge_sliver": edge_sliver,
                "in_corner": in_corner,
                "line_like": line_like,
                "tiny": tiny,
                "digit_like": digit_like,
                "kept": not reject,
            }
        )
        if not reject:
            kept[labels == label] = 255
    cleaned_binary = 255 - kept
    image, stats_out = _foreground_centered(cleaned_binary, out_width=slot_width - 10, out_height=slot_height - 10)
    fast_digits = _clear_printed_zero_digits(kept, components)
    stats_out.update(
        {
            "component_count": len(components),
            "kept_component_count": sum(1 for item in components if item["kept"]),
            "digit_like_component_count": sum(1 for item in components if item["kept"] and item.get("digit_like")),
            "removed_component_count": sum(1 for item in components if not item["kept"]),
            "components": components[:20],
            "crop_box": list(crop_box),
            "fast_digits": fast_digits,
            "fast_digit_source": "clear_printed_zero_shape" if fast_digits else "",
        }
    )
    return image, stats_out


def _region_polygon(region: dict[str, Any]) -> list[list[float]] | None:
    polygon = region.get("polygon")
    if not isinstance(polygon, list) or len(polygon) < 4:
        return None
    points: list[list[float]] = []
    for point in polygon:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        try:
            points.append([float(point[0]), float(point[1])])
        except Exception:
            return None
    return points


def _safe_int_box_for_polygon(
    polygon: list[list[float]],
    *,
    width: int,
    height: int,
    pad_x_px: int,
    pad_y_px: int,
) -> tuple[int, int, int, int] | None:
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    if not xs or not ys:
        return None
    x0 = max(0, int(np.floor(min(xs))) - pad_x_px)
    y0 = max(0, int(np.floor(min(ys))) - pad_y_px)
    x1 = min(width, int(np.ceil(max(xs))) + pad_x_px)
    y1 = min(height, int(np.ceil(max(ys))) + pad_y_px)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _mask_crop_to_region_polygon(
    crop_bgr: np.ndarray,
    *,
    polygon: list[list[float]] | None,
    crop_box: tuple[int, int, int, int],
) -> np.ndarray:
    if not polygon:
        return crop_bgr
    x0, y0, _x1, _y1 = crop_box
    points = np.array(
        [[[int(round(float(px) - x0)), int(round(float(py) - y0))] for px, py in polygon]],
        dtype=np.int32,
    )
    mask = np.zeros(crop_bgr.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, points, 255)
    masked = crop_bgr.copy()
    masked[mask == 0] = 255
    return masked


def build_recognizer_contact_sheet(
    *,
    rectified_fax_bgr: np.ndarray,
    regions: list[dict[str, Any]],
    line_mask: np.ndarray | None,
    mode: str,
    slot_width: int = 132,
    slot_height: int = 84,
    columns: int = 18,
    margin_ratio: float = 0.18,
    min_ink_area: int = 18,
    min_ink_height: int = 8,
) -> tuple[Image.Image, list[dict[str, Any]], list[list[list[int]]], list[dict[str, Any]]]:
    height, width = rectified_fax_bgr.shape[:2]
    candidate_items: list[tuple[int, dict[str, Any], Image.Image, tuple[int, int, int, int], dict[str, Any]]] = []
    skipped_regions: list[dict[str, Any]] = []
    for slot_index, region in enumerate(regions):
        box = region.get("bbox")
        if not isinstance(box, list):
            continue
        polygon = _region_polygon(region)
        if polygon and mode.startswith("corner_cc"):
            px_box = _safe_int_box_for_polygon(
                polygon,
                width=width,
                height=height,
                pad_x_px=2,
                pad_y_px=10,
            )
        elif mode == "dynamic":
            px_box = _expanded_cell_box(box, width=width, height=height, pad_x_px=4, pad_y_px=12)
        elif mode == "corner_cc":
            px_box = _expanded_cell_box(box, width=width, height=height, pad_x_px=2, pad_y_px=10)
        else:
            px_box = _safe_int_box(box, width=width, height=height, margin_ratio=margin_ratio)
        if not px_box:
            continue
        x0, y0, x1, y1 = px_box
        crop = rectified_fax_bgr[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        crop = _mask_crop_to_region_polygon(crop, polygon=polygon, crop_box=px_box)
        crop_line_mask = line_mask[y0:y1, x0:x1] if line_mask is not None else None
        if mode == "dynamic":
            crop_image, ink_stats = _preprocess_dynamic_crop_for_recognizer(
                crop,
                cell_box=box,
                crop_box=px_box,
                slot_width=slot_width,
                slot_height=slot_height,
            )
        elif mode.startswith("corner_cc"):
            crop_image, ink_stats = _preprocess_corner_component_crop_for_recognizer(
                crop,
                cell_box=box,
                crop_box=px_box,
                slot_width=slot_width,
                slot_height=slot_height,
                mode=mode,
            )
        else:
            crop_image, ink_stats = _preprocess_crop_for_recognizer(
                crop,
                line_mask=crop_line_mask,
                slot_width=slot_width,
                slot_height=slot_height,
                mode=mode,
            )
        is_candidate = bool(
            int(ink_stats["ink_area"]) >= min_ink_area and int(ink_stats["bbox_height"]) >= min_ink_height
        )
        if mode.startswith("corner_cc") and int(ink_stats.get("digit_like_component_count") or 0) <= 0:
            is_candidate = False
        prepared_region = {
            **region,
            "ocr_contact_slot_index": slot_index,
            "ocr_cell_crop_bbox_px": [x0, y0, x1, y1],
            "recognizer_crop_mode": mode,
            "recognizer_ink_stats": ink_stats,
            "recognizer_candidate": is_candidate,
        }
        fast_digits = str(ink_stats.get("fast_digits") or "").strip()
        if is_candidate and fast_digits:
            skipped_regions.append(
                {
                    **prepared_region,
                    "ocr_contact_slot": [],
                    "ocr_contact_crop_box": [],
                    "ocr_text": fast_digits,
                    "ocr_normalized": fast_digits,
                    "ocr_words": [],
                    "ocr_word_count": 0,
                    "recognizer_raw_text": fast_digits,
                    "recognizer_score": 1.0,
                    "recognizer_direction": "",
                    "recognizer_accepted": True,
                    "recognizer_skipped": False,
                    "recognizer_fast_path": True,
                    "recognizer_decision_source": str(ink_stats.get("fast_digit_source") or "fast_digits"),
                }
            )
            continue
        if is_candidate:
            candidate_items.append((slot_index, prepared_region, crop_image, px_box, ink_stats))
        else:
            skipped_regions.append(
                {
                    **prepared_region,
                    "ocr_contact_slot": [],
                    "ocr_contact_crop_box": [],
                    "ocr_text": "",
                    "ocr_normalized": "",
                    "ocr_words": [],
                    "ocr_word_count": 0,
                    "recognizer_raw_text": "",
                    "recognizer_score": 0.0,
                    "recognizer_direction": "",
                    "recognizer_accepted": False,
                    "recognizer_skipped": True,
                }
            )
    row_count = max(1, (len(candidate_items) + columns - 1) // columns)
    sheet = Image.new("RGB", (columns * slot_width, row_count * slot_height), "white")
    usable_regions: list[dict[str, Any]] = []
    polygons: list[list[list[int]]] = []
    for compact_index, (_slot_index, prepared_region, crop_image, _px_box, _ink_stats) in enumerate(candidate_items):
        slot_col = compact_index % columns
        slot_row = compact_index // columns
        slot_x = slot_col * slot_width
        slot_y = slot_row * slot_height
        paste_x = slot_x + (slot_width - crop_image.width) // 2
        paste_y = slot_y + (slot_height - crop_image.height) // 2
        sheet.paste(crop_image, (paste_x, paste_y))
        polygon = [
            [paste_x, paste_y],
            [paste_x + crop_image.width, paste_y],
            [paste_x + crop_image.width, paste_y + crop_image.height],
            [paste_x, paste_y + crop_image.height],
        ]
        polygons.append(polygon)
        usable_regions.append(
            {
                **prepared_region,
                "ocr_contact_compact_slot_index": compact_index,
                "ocr_contact_slot": [slot_x, slot_y, slot_x + slot_width, slot_y + slot_height],
                "ocr_contact_crop_box": [paste_x, paste_y, paste_x + crop_image.width, paste_y + crop_image.height],
            }
        )
    return sheet, usable_regions, polygons, skipped_regions


def run_text_recognizer_direct(
    *,
    recognizer: Any,
    contact_sheet: Image.Image,
    regions: list[dict[str, Any]],
    polygons: list[list[list[int]]],
    score_threshold: float,
    digit_score_threshold: float,
    candidate_digit_score_threshold: float,
    enable_context_repair: bool,
    sequence_top_k: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    t0 = time.perf_counter()
    if polygons:
        wrapper = YomitokuTextRecognizerTopKWrapper(
            recognizer,
            token_top_k=5,
            sequence_top_k=sequence_top_k,
            max_decode_steps=8,
        )
        results = wrapper.recognize(_bgr_from_pil(contact_sheet), points=polygons)
        contents = list(results.get("contents") or [])
        scores = [float(value) for value in results.get("scores") or []]
        directions = list(results.get("directions") or [])
        candidates_by_region = list(results.get("candidates") or [])
    else:
        contents = []
        scores = []
        directions = []
        candidates_by_region = []
    elapsed = time.perf_counter() - t0
    assigned: list[dict[str, Any]] = []
    accepted_count = 0
    digit_prediction_count = 0
    candidate_digit_prediction_count = 0
    candidate_digit_accept_count = 0
    for index, region in enumerate(regions):
        raw_text = str(contents[index] if index < len(contents) else "").strip()
        score = float(scores[index] if index < len(scores) else 0.0)
        direction = str(directions[index] if index < len(directions) else "")
        candidates = list(candidates_by_region[index] if index < len(candidates_by_region) else [])
        digits = _clean_text_for_number(raw_text)
        if digits:
            digit_prediction_count += 1
        accepted_candidate = next(
            (
                {
                    **candidate,
                    "normalized_digits": _clean_text_for_number(candidate.get("text")),
                }
                for candidate in candidates
                if _clean_text_for_number(candidate.get("text"))
                and float(candidate.get("score") or 0.0) >= candidate_digit_score_threshold
            ),
            None,
        )
        if any(_clean_text_for_number(candidate.get("text")) for candidate in candidates):
            candidate_digit_prediction_count += 1
        accepted = bool(digits and score >= digit_score_threshold)
        candidate_accepted = accepted_candidate is not None
        if accepted:
            accepted_count += 1
        if candidate_accepted and not accepted:
            candidate_digit_accept_count += 1
        text = digits if accepted else str((accepted_candidate or {}).get("normalized_digits") or "")
        decision_source = ""
        if accepted:
            decision_source = "raw_digits"
        elif candidate_accepted:
            decision_source = "topk_digits"
        assigned.append(
            {
                **region,
                "ocr_text": text,
                "ocr_normalized": text,
                "ocr_words": [
                    {
                        "text": raw_text,
                        "normalized_digits": digits,
                        "score": score,
                        "direction": direction,
                        "candidates": candidates,
                    }
                ],
                "ocr_word_count": 1 if raw_text else 0,
                "recognizer_raw_text": raw_text,
                "recognizer_score": score,
                "recognizer_direction": direction,
                "recognizer_accepted": accepted,
                "recognizer_candidates": candidates,
                "recognizer_candidate_accepted": candidate_accepted,
                "recognizer_accepted_candidate": accepted_candidate,
                "recognizer_decision_source": decision_source,
            }
        )
    repaired_assigned = apply_column_context_digit_repair(assigned) if enable_context_repair else assigned
    repair_count = sum(1 for region in repaired_assigned if region.get("recognizer_context_repair"))
    return repaired_assigned, {
        "engine": TRIAL_ENGINE,
        "ocr_seconds": round(float(elapsed), 3),
        "candidate_count": len(regions),
        "raw_prediction_count": len(contents),
        "digit_prediction_count": digit_prediction_count,
        "accepted_digit_count": accepted_count,
        "candidate_digit_prediction_count": candidate_digit_prediction_count,
        "candidate_digit_accept_count": candidate_digit_accept_count,
        "context_repair_count": repair_count,
        "score_threshold": score_threshold,
        "digit_score_threshold": digit_score_threshold,
        "candidate_digit_score_threshold": candidate_digit_score_threshold,
        "sequence_top_k": sequence_top_k,
    }


def summarize_regions(regions: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [r for r in regions if str(r.get("ocr_normalized") or "").strip()]
    raw_digit = [r for r in regions if _clean_text_for_number(r.get("recognizer_raw_text"))]
    raw_nonempty = [r for r in regions if str(r.get("recognizer_raw_text") or "").strip()]
    candidates = [r for r in regions if r.get("recognizer_candidate")]
    skipped = [r for r in regions if r.get("recognizer_skipped")]
    false_like = [
        {
            "region_id": r.get("region_id"),
            "raw": r.get("recognizer_raw_text"),
            "digits": _clean_text_for_number(r.get("recognizer_raw_text")),
            "score": round(float(r.get("recognizer_score") or 0.0), 4),
            "candidates": [
                {
                    "text": candidate.get("text"),
                    "score": round(float(candidate.get("score") or 0.0), 4),
                    "digits": _clean_text_for_number(candidate.get("text")),
                }
                for candidate in list(r.get("recognizer_candidates") or [])[:5]
            ],
        }
        for r in raw_digit[:25]
    ]
    return {
        "region_count": len(regions),
        "candidate_count": len(candidates),
        "skipped_count": len(skipped),
        "raw_nonempty_count": len(raw_nonempty),
        "raw_digit_count": len(raw_digit),
        "accepted_digit_count": len(accepted),
        "examples": false_like,
    }


def _candidate_summary(candidate: dict[str, Any]) -> str:
    text = str(candidate.get("text") or "")
    score = float(candidate.get("score") or 0.0)
    digits = _clean_text_for_number(text)
    suffix = f" -> {digits}" if digits and digits != text else ""
    return f"{text or '<blank>'} ({score:.3f}){suffix}"


def load_truth_records(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        return {}
    truth: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        sheet_cell = str(record.get("sheet_cell") or "").strip()
        if not sheet_cell:
            continue
        truth[sheet_cell] = {
            "expected_digits": str(record.get("expected_digits") or ""),
            "field": record.get("field"),
            "field_label": record.get("field_label"),
            "source": record.get("supervised_label_source"),
        }
    return truth


def resolve_truth_records_path(path: Path, *, page: int, facility_code: str, order_id: str) -> Path | None:
    if path.is_file():
        return path
    if not path.exists() or not path.is_dir():
        return None
    direct = path / f"{page:02d}_{facility_code}_{order_id}" / "best_method_records.json"
    if direct.exists():
        return direct
    matches = sorted(path.glob(f"*_{facility_code}_{order_id}/best_method_records.json"))
    return matches[0] if matches else None


def _first_candidate_digits(region: dict[str, Any]) -> str:
    raw_digits = _clean_text_for_number(region.get("recognizer_raw_text"))
    if raw_digits:
        return raw_digits
    for candidate in region.get("recognizer_candidates") or []:
        digits = _clean_text_for_number(candidate.get("text"))
        if digits:
            return digits
    return ""


def _candidate_digit_options(region: dict[str, Any]) -> list[str]:
    options: list[str] = []
    for value in [region.get("recognizer_raw_text")] + [
        candidate.get("text") for candidate in region.get("recognizer_candidates") or []
    ]:
        digits = _clean_text_for_number(value)
        if digits and digits not in options:
            options.append(digits)
    return options


def _nearest_column_values(
    *,
    regions: list[dict[str, Any]],
    target: dict[str, Any],
    limit: int = 8,
) -> list[str]:
    target_row = int(target.get("worksheet_row") or 0)
    target_field = str(target.get("field") or "")
    target_col = int(target.get("worksheet_col") or 0)
    ranked: list[tuple[int, str]] = []
    for region in regions:
        if region is target or str(region.get("field") or "") != target_field:
            continue
        if int(region.get("worksheet_col") or 0) != target_col:
            continue
        value = str(region.get("ocr_normalized") or "").strip()
        if not value.isdigit():
            continue
        row = int(region.get("worksheet_row") or 0)
        if row <= 0 or target_row <= 0:
            continue
        distance = abs(row - target_row)
        if distance == 0 or distance > limit:
            continue
        ranked.append((distance, value))
    ranked.sort(key=lambda item: item[0])
    return [value for _distance, value in ranked]


def _has_substantial_digit_ink(region: dict[str, Any]) -> bool:
    stats = region.get("recognizer_ink_stats")
    if not isinstance(stats, dict):
        return False
    return (
        int(stats.get("kept_component_count") or 0) >= 1
        and int(stats.get("ink_area") or 0) >= 120
        and int(stats.get("bbox_width") or 0) >= 12
        and int(stats.get("bbox_height") or 0) >= 24
    )


def _choose_column_context_value(
    *,
    region: dict[str, Any],
    neighbor_values: list[str],
) -> str | None:
    if len(neighbor_values) < 2:
        return None
    counts: dict[str, int] = {}
    for value in neighbor_values:
        counts[value] = counts.get(value, 0) + 1
    ranked_values = sorted(counts.items(), key=lambda item: (-item[1], neighbor_values.index(item[0])))
    candidate_options = _candidate_digit_options(region)
    current = str(region.get("ocr_normalized") or "").strip()

    if not current:
        if not _has_substantial_digit_ink(region):
            return None
        value, count = ranked_values[0]
        if count < 4 or count / len(neighbor_values) < 0.75:
            return None
        return value

    if len(current) == 1:
        raw_text = str(region.get("recognizer_raw_text") or "").strip().translate(FULLWIDTH_DIGIT_TRANS)
        if raw_text == current:
            return None
        suffix_matches = [value for value, count in ranked_values if count >= 2 and value.endswith(current)]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        return None

    if len(current) == 2 and current.isdigit():
        numeric_neighbors = sorted(int(value) for value in neighbor_values if len(value) == 2 and value.isdigit())
        if not numeric_neighbors:
            return None
        median_neighbor = numeric_neighbors[len(numeric_neighbors) // 2]
        current_value = int(current)
        if abs(current_value - median_neighbor) <= 10:
            return None
        for value, count in ranked_values:
            if count < 2 or value == current or value not in candidate_options:
                continue
            if abs(int(value) - median_neighbor) <= 5:
                return value
    return None


def _apply_column_context_digit_repair_once(regions: list[dict[str, Any]], *, pass_index: int) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for region in regions:
        current = str(region.get("ocr_normalized") or "").strip()
        if str(region.get("role") or "") != "quantity":
            repaired.append(region)
            continue
        if current and not current.isdigit():
            repaired.append(region)
            continue
        neighbor_values = _nearest_column_values(regions=regions, target=region)
        chosen = _choose_column_context_value(region=region, neighbor_values=neighbor_values)
        if not chosen or chosen == current:
            repaired.append(region)
            continue
        repaired_region = {
            **region,
            "ocr_text": chosen,
            "ocr_normalized": chosen,
            "recognizer_context_repair": {
                "from": current,
                "to": chosen,
                "pass": pass_index,
                "neighbor_values": neighbor_values[:8],
                "candidate_digit_options": _candidate_digit_options(region),
            },
        }
        repaired.append(repaired_region)
    return repaired


def apply_column_context_digit_repair(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair OCR-only outliers using same-column local consistency, without fax-specific thresholds."""
    repaired = regions
    for pass_index in range(1, 4):
        next_repaired = _apply_column_context_digit_repair_once(repaired, pass_index=pass_index)
        if json.dumps(next_repaired, sort_keys=True, ensure_ascii=False) == json.dumps(
            repaired,
            sort_keys=True,
            ensure_ascii=False,
        ):
            break
        repaired = next_repaired
    return repaired


def attach_truth_and_eval(
    *,
    regions: list[dict[str, Any]],
    truth_by_cell: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    regular_rows = []
    all_rows = []
    for region in regions:
        sheet_cell = str(region.get("sheet_cell") or region.get("region_id") or "")
        truth = truth_by_cell.get(sheet_cell)
        if truth:
            region["truth"] = truth
            expected_any = str(truth.get("expected_digits") or "")
            pred_any = str(region.get("ocr_normalized") or "")
            all_rows.append(
                {
                    "sheet_cell": sheet_cell,
                    "field": region.get("field"),
                    "field_label": region.get("field_label"),
                    "expected": expected_any,
                    "pred_accepted": pred_any,
                    "accepted_ok": pred_any == expected_any,
                    "raw": region.get("recognizer_raw_text"),
                    "score": region.get("recognizer_score"),
                }
            )
        if str(region.get("field") or "") != "qty.regular_x":
            continue
        expected = str((truth or {}).get("expected_digits") or "")
        pred_accepted = str(region.get("ocr_normalized") or "")
        pred_first_digit = _first_candidate_digits(region)
        regular_rows.append(
            {
                "sheet_cell": sheet_cell,
                "expected": expected,
                "pred_accepted": pred_accepted,
                "pred_first_digit": pred_first_digit,
                "accepted_ok": pred_accepted == expected,
                "first_digit_ok": pred_first_digit == expected,
                "raw": region.get("recognizer_raw_text"),
                "score": region.get("recognizer_score"),
                "component_stats": region.get("recognizer_ink_stats"),
            }
        )
    nonempty = [row for row in regular_rows if row["expected"]]
    all_nonempty = [row for row in all_rows if row["expected"]]
    return {
        "all_count": len(all_rows),
        "all_nonempty_count": len(all_nonempty),
        "all_accepted_exact_count": sum(1 for row in all_rows if row["accepted_ok"]),
        "all_accepted_exact_rate": round(
            sum(1 for row in all_rows if row["accepted_ok"]) / max(1, len(all_rows)),
            4,
        ),
        "all_nonempty_accepted_exact_count": sum(1 for row in all_nonempty if row["accepted_ok"]),
        "all_nonempty_accepted_exact_rate": round(
            sum(1 for row in all_nonempty if row["accepted_ok"]) / max(1, len(all_nonempty)),
            4,
        ),
        "all_accepted_mismatches": [row for row in all_rows if not row["accepted_ok"]],
        "regular_count": len(regular_rows),
        "regular_nonempty_count": len(nonempty),
        "regular_accepted_exact_count": sum(1 for row in regular_rows if row["accepted_ok"]),
        "regular_accepted_exact_rate": round(
            sum(1 for row in regular_rows if row["accepted_ok"]) / max(1, len(regular_rows)),
            4,
        ),
        "regular_first_digit_exact_count": sum(1 for row in regular_rows if row["first_digit_ok"]),
        "regular_first_digit_exact_rate": round(
            sum(1 for row in regular_rows if row["first_digit_ok"]) / max(1, len(regular_rows)),
            4,
        ),
        "regular_nonempty_first_digit_exact_count": sum(1 for row in nonempty if row["first_digit_ok"]),
        "regular_nonempty_first_digit_exact_rate": round(
            sum(1 for row in nonempty if row["first_digit_ok"]) / max(1, len(nonempty)),
            4,
        ),
        "regular_mismatches": [
            row
            for row in regular_rows
            if row["pred_first_digit"] != row["expected"]
        ],
        "regular_accepted_mismatches": [row for row in regular_rows if not row["accepted_ok"]],
    }


def build_unaccepted_topk_review(
    *,
    contact_sheet: Image.Image,
    regions: list[dict[str, Any]],
    max_items: int = 36,
) -> Image.Image:
    review_targets = [
        region
        for region in regions
        if region.get("recognizer_candidate")
        and not str(region.get("ocr_normalized") or "").strip()
        and (
            str(region.get("recognizer_raw_text") or "").strip()
            or any(str(candidate.get("text") or "").strip() for candidate in region.get("recognizer_candidates") or [])
        )
    ]
    review_targets.sort(
        key=lambda region: (
            not any(_clean_text_for_number(candidate.get("text")) for candidate in region.get("recognizer_candidates") or []),
            int(region.get("ocr_contact_slot_index") or 0),
        )
    )
    review_targets = review_targets[:max_items]
    row_h = 124
    crop_w = 220
    canvas_w = 1500
    title_h = 52
    canvas_h = max(title_h + row_h, title_h + row_h * len(review_targets))
    canvas = Image.new("RGB", (canvas_w, canvas_h), "white")
    draw = ImageDraw.Draw(canvas)
    font = _load_overlay_font(20)
    small = _load_overlay_font(16)
    _draw_text_safe(
        draw,
        (18, 14),
        f"accepted なしセルの top-k 候補 ({len(review_targets)} shown)",
        fill=(0, 0, 0, 255),
        font=font,
    )
    for idx, region in enumerate(review_targets):
        y = title_h + idx * row_h
        slot = region.get("ocr_contact_crop_box") or region.get("ocr_contact_slot")
        if isinstance(slot, list) and len(slot) == 4:
            x0, y0, x1, y1 = [int(round(float(value))) for value in slot]
            crop = contact_sheet.crop((x0, y0, x1, y1)).convert("RGB")
            crop.thumbnail((crop_w - 22, row_h - 18), Image.Resampling.LANCZOS)
            canvas.paste(crop, (14, y + (row_h - crop.height) // 2))
            draw.rectangle((12, y + 8, crop_w - 4, y + row_h - 8), outline=(210, 210, 210), width=1)
        raw_text = str(region.get("recognizer_raw_text") or "")
        raw_score = float(region.get("recognizer_score") or 0.0)
        candidates = list(region.get("recognizer_candidates") or [])[:5]
        topk = " / ".join(_candidate_summary(candidate) for candidate in candidates)
        line1 = (
            f"{region.get('region_id')} slot={region.get('ocr_contact_slot_index')} "
            f"raw={raw_text or '<blank>'} ({raw_score:.3f})"
        )
        line2 = f"top-k: {topk or '<none>'}"
        _draw_text_safe(draw, (crop_w + 18, y + 18), line1, fill=(0, 0, 0, 255), font=font)
        _draw_text_safe(draw, (crop_w + 18, y + 52), line2, fill=(170, 0, 0, 255), font=small)
    if not review_targets:
        _draw_text_safe(draw, (18, 76), "対象なし", fill=(0, 0, 0, 255), font=font)
    return canvas


def build_page_result(
    *,
    item: dict[str, Any],
    page: int,
    output_dir: Path,
    recognizer: Any,
    render_width: int,
    score_threshold: float,
    digit_score_threshold: float,
    candidate_digit_score_threshold: float,
    min_ink_area: int,
    min_ink_height: int,
    sequence_top_k: int,
    modes: tuple[str, ...],
    enable_context_repair: bool,
    truth_by_cell: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], Image.Image]:
    pre = _build_preprocess_for_ocr(item=item, page=page, render_width=render_width)
    facility_code = str(pre["facility_code"])
    order_id = str(pre["order_id"])
    case_dir = output_dir / f"{page:02d}_{facility_code}_{order_id}"
    case_dir.mkdir(parents=True, exist_ok=True)
    horizontal_line_mask, vertical_line_mask = _split_line_masks(pre["raw_rectified"])
    line_mask = cv2.bitwise_or(horizontal_line_mask, vertical_line_mask)
    variants: dict[str, dict[str, Any]] = {}
    review_images: list[Image.Image] = []
    for mode in modes:
        contact_sheet, regions, polygons, skipped_regions = build_recognizer_contact_sheet(
            rectified_fax_bgr=pre["raw_rectified"],
            regions=pre["target_regions"],
            line_mask=line_mask if mode == "clean" else None,
            mode=mode,
            min_ink_area=min_ink_area,
            min_ink_height=min_ink_height,
        )
        recognized_regions, metrics = run_text_recognizer_direct(
            recognizer=recognizer,
            contact_sheet=contact_sheet,
            regions=regions,
            polygons=polygons,
            score_threshold=score_threshold,
            digit_score_threshold=digit_score_threshold,
            candidate_digit_score_threshold=candidate_digit_score_threshold,
            enable_context_repair=enable_context_repair,
            sequence_top_k=sequence_top_k,
        )
        ocr_regions = sorted(
            recognized_regions + skipped_regions,
            key=lambda item: int(item.get("ocr_contact_slot_index") or 0),
        )
        truth_eval = attach_truth_and_eval(regions=ocr_regions, truth_by_cell=truth_by_cell)
        assignments = sheet_assignments_from_ocr_regions(ocr_regions)
        sheet_values = sheet_value_grid_from_assignments(assignments)
        overlay = draw_ocr_results_overlay(target_overlay=pre["target_overlay"], ocr_regions=ocr_regions)
        topk_review = build_unaccepted_topk_review(contact_sheet=contact_sheet, regions=ocr_regions)
        contact_sheet_path = case_dir / f"{mode}_text_recognizer_contact_sheet.png"
        regions_path = case_dir / f"{mode}_text_recognizer_regions.json"
        assignments_path = case_dir / f"{mode}_text_recognizer_sheet_assignments.json"
        sheet_values_path = case_dir / f"{mode}_text_recognizer_sheet_values.json"
        overlay_path = case_dir / f"{mode}_text_recognizer_overlay.png"
        topk_review_path = case_dir / f"{mode}_unaccepted_topk_review.png"
        contact_sheet.save(contact_sheet_path)
        overlay.save(overlay_path)
        topk_review.save(topk_review_path)
        regions_path.write_text(json.dumps(ocr_regions, ensure_ascii=False, indent=2), encoding="utf-8")
        assignments_path.write_text(json.dumps(assignments, ensure_ascii=False, indent=2), encoding="utf-8")
        sheet_values_path.write_text(json.dumps(sheet_values, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = summarize_regions(ocr_regions)
        variants[mode] = {
            **metrics,
            **summary,
            "truth_eval": truth_eval,
            "outputs": {
                "contact_sheet": str(contact_sheet_path),
                "regions": str(regions_path),
                "sheet_assignments": str(assignments_path),
                "sheet_values": str(sheet_values_path),
                "overlay": str(overlay_path),
                "unaccepted_topk_review": str(topk_review_path),
            },
        }
        details = [
            f"mode={mode} engine={TRIAL_ENGINE}",
            (
                f"regions={summary['region_count']} candidates={summary['candidate_count']} "
                f"skipped={summary['skipped_count']} raw_digits={summary['raw_digit_count']} "
                f"accepted_digits={summary['accepted_digit_count']}"
            ),
            (
                f"score_threshold={score_threshold} digit_threshold={digit_score_threshold} "
                f"candidate_digit_threshold={candidate_digit_score_threshold} ocr_sec={metrics['ocr_seconds']}"
            ),
            (
                f"topk={metrics['sequence_top_k']} candidate_digits={metrics['candidate_digit_prediction_count']} "
                f"candidate_accept_plus={metrics['candidate_digit_accept_count']}"
            ),
            (
                f"regular accepted exact={truth_eval['regular_accepted_exact_count']}/"
                f"{truth_eval['regular_count']} all accepted exact={truth_eval['all_accepted_exact_count']}/"
                f"{truth_eval['all_count']}"
            ),
            "green grid/red points: fixed preprocessing output; red digit labels: accepted TextRecognizer digits",
        ]
        review_images.append(
            _make_review_canvas(
                title=f"Hakodate TextRecognizer direct OCR trial ({mode})",
                facility_code=facility_code,
                order_id=order_id,
                image=overlay,
                details=details,
            )
        )
        review_images.append(topk_review)
    width = max(img.width for img in review_images)
    height = sum(img.height for img in review_images)
    page_canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for img in review_images:
        page_canvas.paste(img, (0, y))
        y += img.height
    review_page_path = case_dir / "text_recognizer_trial_review_page.png"
    page_canvas.save(review_page_path)
    return {
        "page": page,
        "facility_code": facility_code,
        "order_id": order_id,
        "variants": variants,
        "review_page": str(review_page_path),
    }, page_canvas


def load_baseline_counts(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "engine": data.get("ocr_engine"),
        "count": data.get("count"),
        "total_physical_region_count": data.get("total_physical_region_count"),
        "total_logical_assignment_count": data.get("total_logical_assignment_count"),
        "total_recognized_region_count": data.get("total_recognized_region_count"),
        "total_recognized_assignment_count": data.get("total_recognized_assignment_count"),
        "total_elapsed_seconds": data.get("total_elapsed_seconds"),
        "summary_path": str(path),
    }


def write_contact_sheet_preview(results: list[dict[str, Any]], output_dir: Path, *, modes: tuple[str, ...]) -> str:
    canvases: list[Image.Image] = []
    for result in results:
        for mode in modes:
            path = Path(result["variants"][mode]["outputs"]["contact_sheet"])
            image = Image.open(path).convert("RGB")
            crop = image.crop((0, 0, min(image.width, 18 * 132), min(image.height, 6 * 84)))
            crop.thumbnail((1500, 460), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (1540, 520), "white")
            canvas.paste(crop, (20, 20))
            ImageDraw.Draw(canvas).text(
                (20, 490),
                f"p{result['page']} {result['facility_code']} {mode} top contact rows",
                fill=(0, 0, 0),
            )
            canvases.append(canvas)
        if len(canvases) >= 6:
            break
    out = Image.new("RGB", (1540, 520 * len(canvases)), "white")
    for idx, canvas in enumerate(canvases):
        out.paste(canvas, (0, idx * 520))
    path = output_dir / "text_recognizer_contact_sheet_preview.png"
    out.save(path)
    return str(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--baseline-summary", type=Path, default=DEFAULT_BASELINE_SUMMARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--render-width", type=int, default=1864)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--score-threshold", type=float, default=0.45)
    parser.add_argument("--digit-score-threshold", type=float, default=0.05)
    parser.add_argument("--candidate-digit-score-threshold", type=float, default=0.05)
    parser.add_argument("--min-ink-area", type=int, default=18)
    parser.add_argument("--min-ink-height", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=14)
    parser.add_argument("--sequence-top-k", type=int, default=5)
    parser.add_argument("--truth-records", type=Path, default=DEFAULT_TRUTH_RECORDS)
    parser.add_argument("--facility-code", default="")
    parser.add_argument("--order-id", default="")
    parser.add_argument("--modes", default=",".join(DEFAULT_RECOGNIZER_MODES))
    parser.add_argument("--no-context-repair", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    modes = tuple(mode.strip() for mode in str(args.modes).split(",") if mode.strip())
    if not modes:
        raise ValueError("at least one recognizer mode is required")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    items = manifest.get("results") if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        raise ValueError("manifest results are missing")
    if args.facility_code:
        items = [item for item in items if str(item.get("facility_code") or "") == args.facility_code]
    if args.order_id:
        items = [item for item in items if str(item.get("order_id") or "") == args.order_id]
    if not items:
        raise ValueError("no manifest items matched the requested filters")
    items = items[: max(1, args.max_pages)]
    recognizer = _load_text_recognizer(args.device)
    pages: list[Image.Image] = []
    results: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for page, item in enumerate(items, start=1):
        facility_code = str(item.get("facility_code") or "")
        order_id = str(item.get("order_id") or "")
        truth_path = resolve_truth_records_path(
            args.truth_records,
            page=page,
            facility_code=facility_code,
            order_id=order_id,
        )
        truth_by_cell = load_truth_records(truth_path) if truth_path is not None else {}
        result, review_page = build_page_result(
            item=item,
            page=page,
            output_dir=output_dir,
            recognizer=recognizer,
            render_width=args.render_width,
            score_threshold=args.score_threshold,
            digit_score_threshold=args.digit_score_threshold,
            candidate_digit_score_threshold=args.candidate_digit_score_threshold,
            min_ink_area=args.min_ink_area,
            min_ink_height=args.min_ink_height,
            sequence_top_k=args.sequence_top_k,
            modes=modes,
            enable_context_repair=not args.no_context_repair,
            truth_by_cell=truth_by_cell,
        )
        result["truth_records"] = str(truth_path) if truth_path is not None else None
        results.append(result)
        pages.append(review_page)
    pdf_path = output_dir / "text_recognizer_trial_all14.pdf"
    _write_pdf_from_pages(pages, pdf_path)
    totals = {
        mode: {
            "raw_nonempty_count": sum(int(item["variants"][mode]["raw_nonempty_count"]) for item in results),
            "raw_digit_count": sum(int(item["variants"][mode]["raw_digit_count"]) for item in results),
            "accepted_digit_count": sum(int(item["variants"][mode]["accepted_digit_count"]) for item in results),
            "candidate_digit_prediction_count": sum(
                int(item["variants"][mode]["candidate_digit_prediction_count"]) for item in results
            ),
            "candidate_digit_accept_count": sum(
                int(item["variants"][mode]["candidate_digit_accept_count"]) for item in results
            ),
            "ocr_seconds": round(sum(float(item["variants"][mode]["ocr_seconds"]) for item in results), 3),
        }
        for mode in modes
    }
    summary = {
        "engine": TRIAL_ENGINE,
        "count": len(results),
        "elapsed_seconds": round(float(time.perf_counter() - t0), 3),
        "score_threshold": args.score_threshold,
        "digit_score_threshold": args.digit_score_threshold,
        "candidate_digit_score_threshold": args.candidate_digit_score_threshold,
        "min_ink_area": args.min_ink_area,
        "min_ink_height": args.min_ink_height,
        "sequence_top_k": args.sequence_top_k,
        "modes": list(modes),
        "context_repair": not args.no_context_repair,
        "truth_records": str(args.truth_records),
        "baseline_document_analyzer": load_baseline_counts(args.baseline_summary),
        "totals": totals,
        "pdf": str(pdf_path),
        "contact_sheet_preview": write_contact_sheet_preview(results, output_dir, modes=modes),
        "results": results,
    }
    summary_path = output_dir / "text_recognizer_trial_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
