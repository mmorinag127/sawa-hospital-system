from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Optional


@dataclass
class GridDetectionResult:
    table_box: list[float]
    column_edges: list[float]
    row_edges: list[float]
    confidence: float


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _group_indices(indices: list[int], max_gap: int = 2) -> list[list[int]]:
    groups: list[list[int]] = []
    for idx in sorted(indices):
        if not groups or idx - groups[-1][-1] > max_gap:
            groups.append([idx])
        else:
            groups[-1].append(idx)
    return groups


def _line_centers_from_mask(mask, axis: int, threshold: float, max_gap: int) -> list[int]:
    import numpy as np

    projection = mask.sum(axis=axis) / 255.0
    indices = [i for i, value in enumerate(projection) if value >= threshold]
    groups = _group_indices(indices, max_gap=max_gap)
    centers: list[int] = []
    for group in groups:
        if not group:
            continue
        centers.append(int(sum(group) / len(group)))
    return centers


def _find_best_sequence(values: list[float], length: int, tolerance: float) -> list[float]:
    if length <= 1 or len(values) < length:
        return []
    best: list[float] = []
    best_score: float | None = None
    for start in range(len(values) - length + 1):
        seq = values[start : start + length]
        gaps = [seq[i + 1] - seq[i] for i in range(len(seq) - 1)]
        avg = sum(gaps) / len(gaps)
        if avg <= 0:
            continue
        max_delta = max(abs(gap - avg) for gap in gaps)
        if max_delta > tolerance:
            continue
        score = sum(abs(gap - avg) for gap in gaps)
        if best_score is None or score < best_score:
            best = seq
            best_score = score
    return best


def _select_column_edges(
    centers_norm: list[float],
    table_left: float,
    table_right: float,
    template: dict,
) -> list[float]:
    expected = int(template.get("grid_expected_columns", 0) or 0)
    if not centers_norm:
        return [table_left, table_right]
    if expected <= 0:
        return [table_left] + centers_norm + [table_right]

    grid_columns = template.get("grid_columns") or []
    qty_count = len([col for col in grid_columns if col.get("role") == "quantity"])
    tolerance = float(template.get("grid_qty_gap_tolerance", 0.02))
    if qty_count:
        seq = _find_best_sequence(centers_norm, qty_count + 1, tolerance)
        if seq:
            left_centers = [c for c in centers_norm if c < seq[0]]
            if left_centers:
                date_boundary = max(left_centers)
            else:
                ratio = float(template.get("grid_left_date_ratio", 0.2))
                date_boundary = table_left + (seq[0] - table_left) * ratio
            edges = [table_left, date_boundary] + list(seq) + [table_right]
            if len(edges) - 1 == expected:
                return edges

    if len(centers_norm) == expected - 1:
        return [table_left] + centers_norm + [table_right]

    merged: list[float] = []
    merge_tol = float(template.get("grid_line_merge_tolerance", 0.02))
    for value in centers_norm:
        if not merged or value - merged[-1] > merge_tol:
            merged.append(value)
        else:
            merged[-1] = (merged[-1] + value) / 2
    if len(merged) >= expected - 1:
        merged = merged[: expected - 1]
    return [table_left] + merged + [table_right]


def detect_table_grid(pdf_bytes: bytes, template: dict) -> Optional[GridDetectionResult]:
    try:
        import cv2  # type: ignore
        import numpy as np
        import pdfplumber
    except Exception:
        return None

    page_index = max(int(template.get("page", 1)) - 1, 0)
    dpi = int(template.get("grid_dpi", 300))
    table_box = template.get("grid_table_box") or template.get("table_box") or [0.0, 0.0, 1.0, 1.0]
    table_box = [_clamp(float(v)) for v in table_box]

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        if not pdf.pages:
            return None
        page = pdf.pages[page_index] if page_index < len(pdf.pages) else pdf.pages[0]
        image = page.to_image(resolution=dpi).original

    width, height = image.size
    x0 = int(table_box[0] * width)
    y0 = int(table_box[1] * height)
    x1 = int(table_box[2] * width)
    y1 = int(table_box[3] * height)
    if x1 <= x0 or y1 <= y0:
        return None

    crop = image.crop((x0, y0, x1, y1))
    img = np.array(crop)
    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    else:
        gray = img

    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        15,
        -2,
    )
    binary = 255 - binary

    height_px, width_px = binary.shape[:2]
    scale = int(template.get("grid_line_scale", 30))
    scale = max(scale, 10)
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(1, height_px // scale))
    )
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(1, width_px // scale), 1)
    )

    vertical = cv2.erode(binary, vertical_kernel)
    vertical = cv2.dilate(vertical, vertical_kernel)
    horizontal = cv2.erode(binary, horizontal_kernel)
    horizontal = cv2.dilate(horizontal, horizontal_kernel)

    min_ratio = float(template.get("grid_line_min_ratio", 0.6))
    min_ratio = max(0.2, min_ratio)
    merge_gap = int(template.get("grid_line_merge_gap", 2))

    col_centers = _line_centers_from_mask(vertical, axis=0, threshold=height_px * min_ratio, max_gap=merge_gap)
    row_centers = _line_centers_from_mask(horizontal, axis=1, threshold=width_px * min_ratio, max_gap=merge_gap)

    if not col_centers:
        return None

    centers_norm = sorted({(x0 + edge) / width for edge in col_centers})
    column_edges = _select_column_edges(centers_norm, table_box[0], table_box[2], template)
    row_edge_values = [0] + sorted(row_centers) + [height_px]
    row_edge_values = sorted({max(0, min(height_px, int(edge))) for edge in row_edge_values})
    row_edges = [((y0 + edge) / height) for edge in row_edge_values]

    expected = int(template.get("grid_expected_columns", 0))
    confidence = 1.0 if expected and (len(column_edges) - 1) == expected else 0.6

    return GridDetectionResult(
        table_box=table_box,
        column_edges=column_edges,
        row_edges=row_edges,
        confidence=confidence,
    )
