"""Canonical contact-sheet processing without native recognition dependencies."""
from __future__ import annotations

from typing import Any
import cv2
import numpy as np
from PIL import Image
from src.services.hakodate_recognizer_input_evidence import contact_crop_evidence
from src.services.hakodate_cell_ocr_batch_service import (
    _expanded_cell_box, _erase_known_cell_frame, _remove_small_noise_only, _safe_int_box,
)


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


def _component_has_hole(component_mask: np.ndarray) -> bool:
    if component_mask.size == 0:
        return False
    contours, hierarchy = cv2.findContours(component_mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None or not contours:
        return False
    return any(int(item[3]) >= 0 for item in hierarchy[0])


def _printed_zero_shape_candidate(ink: np.ndarray, component: dict[str, Any]) -> bool:
    height, width = ink.shape[:2]
    x, y, w, h = [int(value) for value in component.get("bbox") or [0, 0, 0, 0]]
    area = int(component.get("area") or 0)
    if w <= 0 or h <= 0 or area <= 0:
        return False
    aspect = float(w) / max(1.0, float(h))
    fill_ratio = float(area) / max(1.0, float(w * h))
    cx, cy = [float(value) for value in component.get("centroid") or [0.0, 0.0]]
    centered = width * 0.20 <= cx <= width * 0.80 and height * 0.15 <= cy <= height * 0.86
    digit_sized = h >= max(8, int(round(height * 0.24))) and w >= max(4, int(round(width * 0.055)))
    zero_shaped = 0.18 <= aspect <= 1.05 and 0.10 <= fill_ratio <= 0.72
    if not (centered and digit_sized and zero_shaped):
        return False
    component_mask = ink[y : y + h, x : x + w]
    return _component_has_hole(component_mask)


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
    zero_shape_candidate_count = sum(1 for item in components if _printed_zero_shape_candidate(kept, item))
    stats_out.update(
        {
            "component_count": len(components),
            "kept_component_count": sum(1 for item in components if item["kept"]),
            "digit_like_component_count": sum(1 for item in components if item["kept"] and item.get("digit_like")),
            "zero_shape_candidate_count": zero_shape_candidate_count,
            "removed_component_count": sum(1 for item in components if not item["kept"]),
            "components": components[:20],
            "crop_box": list(crop_box),
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


def _warp_polygon_cell_to_axis_aligned_crop(
    rectified_fax_bgr: np.ndarray,
    polygon: list[list[float]] | None,
) -> tuple[np.ndarray, list[float], tuple[int, int, int, int], tuple[int, int, int, int]] | None:
    if polygon is None:
        return None
    if len(polygon) != 4:
        raise ValueError("recognizer_crop_polygon_invalid")
    pts = np.array(polygon, dtype=np.float32)
    height, width = rectified_fax_bgr.shape[:2]
    if (
        pts.shape != (4, 2)
        or not np.isfinite(pts).all()
        or (pts[:, 0] < 0).any()
        or (pts[:, 0] > width).any()
        or (pts[:, 1] < 0).any()
        or (pts[:, 1] > height).any()
        or not cv2.isContourConvex(pts)
        or cv2.contourArea(pts, oriented=True) <= 0
    ):
        raise ValueError("recognizer_crop_polygon_invalid")
    top_w = float(np.linalg.norm(pts[1] - pts[0]))
    bottom_w = float(np.linalg.norm(pts[2] - pts[3]))
    left_h = float(np.linalg.norm(pts[3] - pts[0]))
    right_h = float(np.linalg.norm(pts[2] - pts[1]))
    out_w = int(round(max(top_w, bottom_w)))
    out_h = int(round(max(left_h, right_h)))
    if out_w < 2 or out_h < 2:
        raise ValueError("recognizer_crop_dimensions_invalid")
    # Rectify at source resolution; fit the foreground uniformly only afterwards.
    dst = np.array(
        [
            [0.0, 0.0],
            [float(out_w - 1), 0.0],
            [float(out_w - 1), float(out_h - 1)],
            [0.0, float(out_h - 1)],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(pts, dst)
    warped = cv2.warpPerspective(
        rectified_fax_bgr,
        matrix,
        (out_w, out_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    xs = [float(point[0]) for point in polygon[:4]]
    ys = [float(point[1]) for point in polygon[:4]]
    source_box = (
        int(np.floor(min(xs))),
        int(np.floor(min(ys))),
        int(np.ceil(max(xs))),
        int(np.ceil(max(ys))),
    )
    return warped, [0.0, 0.0, float(out_w), float(out_h)], (0, 0, out_w, out_h), source_box


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
    # Keep legacy ink arguments for callers, but never use them to skip recognition.
    height, width = rectified_fax_bgr.shape[:2]
    from src.services.hakodate_preprocess_observation import observe, pixels
    observation_source_hash = pixels(rectified_fax_bgr)['sha256']
    candidate_items: list[tuple[int, dict[str, Any], Image.Image, tuple[int, int, int, int], dict[str, Any]]] = []
    skipped_regions: list[dict[str, Any]] = []
    for slot_index, region in enumerate(regions):
        box = region.get("bbox")
        if (
            not isinstance(box, list)
            or len(box) != 4
            or not all(isinstance(value, (int, float)) and np.isfinite(value) for value in box)
            or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)
        ):
            raise ValueError(f"recognizer_crop_bbox_invalid: target_index={slot_index}")
        polygon = _region_polygon(region)
        if region.get("polygon") is not None and polygon is None:
            raise ValueError(f"recognizer_crop_polygon_invalid: target_index={slot_index}")
        warped_cell = _warp_polygon_cell_to_axis_aligned_crop(rectified_fax_bgr, polygon) if mode.startswith("corner_cc") else None
        if warped_cell is not None:
            crop, preprocess_cell_box, preprocess_px_box, source_px_box = warped_cell
            px_box = source_px_box
        elif mode == "dynamic":
            px_box = _expanded_cell_box(box, width=width, height=height, pad_x_px=4, pad_y_px=12)
        elif mode == "corner_cc":
            px_box = _expanded_cell_box(box, width=width, height=height, pad_x_px=2, pad_y_px=10)
        else:
            px_box = _safe_int_box(box, width=width, height=height, margin_ratio=margin_ratio)
        if not px_box:
            raise ValueError(f"recognizer_crop_bbox_empty: target_index={slot_index}")
        if warped_cell is None:
            x0, y0, x1, y1 = px_box
            crop = rectified_fax_bgr[y0:y1, x0:x1]
            if crop.size == 0:
                raise ValueError(f"recognizer_crop_empty: target_index={slot_index}")
            crop = _mask_crop_to_region_polygon(crop, polygon=polygon, crop_box=px_box)
            preprocess_cell_box = box
            preprocess_px_box = px_box
            source_px_box = px_box
        if crop.size == 0:
            raise ValueError(f"recognizer_crop_empty: target_index={slot_index}")
        crop_line_mask = None if warped_cell is not None or line_mask is None else line_mask[y0:y1, x0:x1]
        if mode == "dynamic":
            crop_image, ink_stats = _preprocess_dynamic_crop_for_recognizer(
                crop,
                cell_box=preprocess_cell_box,
                crop_box=preprocess_px_box,
                slot_width=slot_width,
                slot_height=slot_height,
            )
        elif mode.startswith("corner_cc"):
            crop_image, ink_stats = _preprocess_corner_component_crop_for_recognizer(
                crop,
                cell_box=preprocess_cell_box,
                crop_box=preprocess_px_box,
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
        prepared_region = {
            **region,
            "ocr_contact_slot_index": slot_index,
            "ocr_cell_crop_bbox_px": [int(value) for value in source_px_box],
            "recognizer_crop_mode": mode,
            "recognizer_ink_stats": ink_stats,
            "recognizer_candidate": True,
        }
        sx0, sy0, sx1, sy1 = source_px_box
        prepared_region["recognizer_preprocess_ink_removed"] = bool(
            crop_image.convert("RGB").getextrema() == ((255, 255), (255, 255), (255, 255))
            and np.any(rectified_fax_bgr[sy0:sy1, sx0:sx1] < 255))
        if mode == 'corner_cc':
            prepared_region['recognizer_preprocess_observation'] = observe(
                rectified_fax_bgr,region,crop,source_px_box,observation_source_hash)
        candidate_items.append((slot_index, prepared_region, crop_image, source_px_box, ink_stats))
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
        if 'recognizer_preprocess_observation' in prepared_region:
            prepared_region['recognizer_preprocess_observation']['contact'] = contact_crop_evidence(sheet,polygon)
        usable_regions.append(
            {
                **prepared_region,
                "ocr_contact_compact_slot_index": compact_index,
                "ocr_contact_slot": [slot_x, slot_y, slot_x + slot_width, slot_y + slot_height],
                "ocr_contact_crop_box": [paste_x, paste_y, paste_x + crop_image.width, paste_y + crop_image.height],
            }
        )
    return sheet, usable_regions, polygons, skipped_regions
