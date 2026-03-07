from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Optional, Tuple


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


def _adaptive_binary(image):
    import cv2  # type: ignore
    import numpy as np

    img = np.array(image)
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
    return 255 - binary


def _otsu_binary(image):
    import cv2  # type: ignore
    import numpy as np

    img = np.array(image)
    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    else:
        gray = img
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return 255 - binary


def _extract_grid_lines(
    binary,
    line_scale: int,
    *,
    horizontal_scale: Optional[int] = None,
    vertical_scale: Optional[int] = None,
) -> tuple[object, object]:
    import cv2  # type: ignore

    height_px, width_px = binary.shape[:2]
    scale = max(line_scale, 10)
    h_scale = max(int(horizontal_scale or scale), 10)
    v_scale = max(int(vertical_scale or scale), 10)
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(1, height_px // v_scale))
    )
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(1, width_px // h_scale), 1)
    )
    vertical = cv2.erode(binary, vertical_kernel)
    vertical = cv2.dilate(vertical, vertical_kernel)
    horizontal = cv2.erode(binary, horizontal_kernel)
    horizontal = cv2.dilate(horizontal, horizontal_kernel)
    return vertical, horizontal


def _extract_open_lines(binary, width: int, height: int) -> tuple[object, object, object]:
    import cv2  # type: ignore

    inv = 255 - binary
    h_ksize = max(30, width // 40)
    v_ksize = max(30, height // 60)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_ksize, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_ksize))
    h_lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, h_kernel, iterations=1)
    v_lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, v_kernel, iterations=1)
    grid = cv2.bitwise_or(h_lines, v_lines)
    return h_lines, v_lines, grid


def _find_largest_table_bbox(grid_mask, height: int, width: int, margin_ratio: float = 0.02) -> tuple[int, int, int, int]:
    import cv2  # type: ignore

    cnts, _ = cv2.findContours(grid_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    mx = int(width * margin_ratio)
    my = int(height * margin_ratio)
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < best_area:
            continue
        if h < height * 0.2 or w < width * 0.3:
            continue
        best = (x, y, w, h)
        best_area = area
    if best is None:
        return (mx, my, max(width - 2 * mx, 1), max(height - 2 * my, 1))
    return best


def _cluster_positions(pos, max_gap: int) -> list[int]:
    if not pos:
        return []
    pos = sorted(pos)
    clusters = []
    start = pos[0]
    prev = pos[0]
    for p in pos[1:]:
        if p - prev > max_gap:
            clusters.append((start, prev))
            start = p
        prev = p
    clusters.append((start, prev))
    return [int((a + b) // 2) for a, b in clusters]


def _smooth_1d(values, win: int):
    import numpy as np

    if win <= 1:
        return values
    kernel = np.ones(win, dtype=np.float32) / win
    return np.convolve(values.astype(np.float32), kernel, mode="same")


def _estimate_grid_lines(
    v_lines,
    h_lines,
    table_bbox,
    column_band: Optional[tuple[float, float]] = None,
    row_band: Optional[tuple[float, float]] = None,
) -> tuple[list[int], list[int]]:
    import numpy as np

    x0, y0, w, h = table_bbox
    col_band = column_band if column_band else (0.0, 1.0)
    row_band = row_band if row_band else (0.0, 1.0)
    cy0 = y0 + int(h * col_band[0])
    cy1 = y0 + int(h * col_band[1])
    rx0 = x0 + int(w * row_band[0])
    rx1 = x0 + int(w * row_band[1])
    cy0 = max(y0, min(cy0, y0 + h))
    cy1 = max(y0, min(cy1, y0 + h))
    rx0 = max(x0, min(rx0, x0 + w))
    rx1 = max(x0, min(rx1, x0 + w))
    if cy1 <= cy0:
        cy0, cy1 = y0, y0 + h
    if rx1 <= rx0:
        rx0, rx1 = x0, x0 + w
    v = v_lines[cy0:cy1, x0 : x0 + w]
    hh = h_lines[y0 : y0 + h, rx0:rx1]
    vproj = _smooth_1d(v.sum(axis=0), win=max(5, w // 200))
    hproj = _smooth_1d(hh.sum(axis=1), win=max(5, h // 200))
    vx = np.where(vproj > 0.5 * vproj.max())[0]
    hy = np.where(hproj > 0.5 * hproj.max())[0]
    xs = _cluster_positions(vx.tolist(), max_gap=max(2, w // 300))
    ys = _cluster_positions(hy.tolist(), max_gap=max(2, h // 300))
    if len(xs) < 2:
        xs = [0, max(w - 1, 0)]
    if len(ys) < 2:
        ys = [0, max(h - 1, 0)]
    return [x0 + x for x in xs], [y0 + y for y in ys]


def _normalize_region(region: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = region[:4]
    if max(region) <= 1.5:
        x0 = int(x0 * width)
        y0 = int(y0 * height)
        x1 = int(x1 * width)
        y1 = int(y1 * height)
    x0 = max(0, min(width, int(x0)))
    x1 = max(0, min(width, int(x1)))
    y0 = max(0, min(height, int(y0)))
    y1 = max(0, min(height, int(y1)))
    if x1 <= x0:
        x0, x1 = 0, width
    if y1 <= y0:
        y0, y1 = 0, height
    return x0, y0, x1, y1


def _auto_detect_grid(image, template: dict) -> Optional[Tuple[list[float], list[float], list[float]]]:
    try:
        import cv2  # type: ignore
        import numpy as np
    except Exception:
        return None

    img = np.array(image)
    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    else:
        gray = img
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    height, width = binary.shape[:2]

    region = template.get("grid_table_search_region")
    if isinstance(region, list) and len(region) >= 4:
        rx0, ry0, rx1, ry1 = _normalize_region(region, width, height)
        binary_region = binary[ry0:ry1, rx0:rx1]
        h_lines, v_lines, grid = _extract_open_lines(binary_region, rx1 - rx0, ry1 - ry0)
        table_bbox = _find_largest_table_bbox(grid, ry1 - ry0, rx1 - rx0)
        x0, y0, w, h = table_bbox
        x0 += rx0
        y0 += ry0
    else:
        h_lines, v_lines, grid = _extract_open_lines(binary, width, height)
        x0, y0, w, h = _find_largest_table_bbox(grid, height, width)

    if w <= 0 or h <= 0:
        return None
    column_band = template.get("grid_column_band") or template.get("grid_column_sample_band")
    row_band = template.get("grid_row_band") or template.get("grid_row_sample_band")
    col_band_tuple = None
    row_band_tuple = None
    if isinstance(column_band, (list, tuple)) and len(column_band) >= 2:
        col_band_tuple = (float(column_band[0]), float(column_band[1]))
    if isinstance(row_band, (list, tuple)) and len(row_band) >= 2:
        row_band_tuple = (float(row_band[0]), float(row_band[1]))
    xs, ys = _estimate_grid_lines(v_lines, h_lines, (x0, y0, w, h), col_band_tuple, row_band_tuple)
    if len(xs) < 3 or len(ys) < 3:
        xs, ys = _estimate_grid_lines(v_lines, h_lines, (x0, y0, w, h), None, None)
    x1 = x0 + w
    y1 = y0 + h
    margin_x = max(2, int(w * 0.01))
    margin_y = max(2, int(h * 0.01))
    if not xs or xs[0] > x0 + margin_x:
        xs.insert(0, x0)
    if xs[-1] < x1 - margin_x:
        xs.append(x1)
    if not ys or ys[0] > y0 + margin_y:
        ys.insert(0, y0)
    if ys[-1] < y1 - margin_y:
        ys.append(y1)
    xs = sorted({x for x in xs if x0 <= x <= x1})
    ys = sorted({y for y in ys if y0 <= y <= y1})
    merge_tol = float(template.get("grid_line_merge_tolerance", 0.02) or 0.02)
    tol_x = max(1, int(merge_tol * max(w, 1)))
    tol_y = max(1, int(merge_tol * max(h, 1)))

    def _merge_edges(edges: list[int], tol: int) -> list[int]:
        merged: list[int] = []
        for edge in edges:
            if not merged or edge - merged[-1] > tol:
                merged.append(edge)
            else:
                merged[-1] = int((merged[-1] + edge) / 2)
        return merged

    xs = _merge_edges(xs, tol_x)
    ys = _merge_edges(ys, tol_y)
    if xs and xs[0] != x0:
        xs.insert(0, x0)
    if xs and xs[-1] != x1:
        xs.append(x1)
    if ys and ys[0] != y0:
        ys.insert(0, y0)
    if ys and ys[-1] != y1:
        ys.append(y1)
    xs = sorted({x for x in xs if x0 <= x <= x1})
    ys = sorted({y for y in ys if y0 <= y <= y1})
    if len(xs) < 2 or len(ys) < 2:
        return None
    table_box = [x0 / width, y0 / height, x1 / width, y1 / height]
    col_edges = [x / width for x in xs]
    row_edges = [y / height for y in ys]
    return table_box, col_edges, row_edges


def _auto_table_box_from_image(image, template: dict) -> Optional[list[float]]:
    auto = _auto_detect_grid(image, template)
    if auto is None:
        return None
    return auto[0]


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


def _detect_grid_from_image(image, template: dict) -> Optional[GridDetectionResult]:
    try:
        import cv2  # type: ignore
        import numpy as np
    except Exception:
        return None

    table_box = template.get("grid_table_box") or template.get("table_box") or [0.0, 0.0, 1.0, 1.0]
    table_box = [_clamp(float(v)) for v in table_box]
    auto_edges = None
    if template.get("grid_auto_table_box"):
        auto = _auto_detect_grid(image, template)
        if auto:
            auto_box, auto_cols, auto_rows = auto
            table_box = [_clamp(float(v)) for v in auto_box]
            auto_edges = (auto_cols, auto_rows)

    width, height = image.size
    x0 = int(table_box[0] * width)
    y0 = int(table_box[1] * height)
    x1 = int(table_box[2] * width)
    y1 = int(table_box[3] * height)
    if x1 <= x0 or y1 <= y0:
        return None

    crop = image.crop((x0, y0, x1, y1))
    if auto_edges and template.get("grid_auto_use_raw_edges"):
        auto_cols, auto_rows = auto_edges
        if len(auto_cols) >= 2 and len(auto_rows) >= 2:
            expected = int(template.get("grid_expected_columns", 0))
            confidence = 1.0 if expected and (len(auto_cols) - 1) == expected else 0.8
            return GridDetectionResult(
                table_box=table_box,
                column_edges=auto_cols,
                row_edges=auto_rows,
                confidence=confidence,
            )

    binary = _adaptive_binary(crop)

    height_px, width_px = binary.shape[:2]
    scale = int(template.get("grid_line_scale", 30))
    scale = max(scale, 10)
    h_scale = int(template.get("grid_line_scale_horizontal", scale))
    v_scale = int(template.get("grid_line_scale_vertical", scale))
    vertical, horizontal = _extract_grid_lines(
        binary,
        scale,
        horizontal_scale=h_scale,
        vertical_scale=v_scale,
    )

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


def detect_table_grid_image(image_bytes: bytes, template: dict) -> Optional[GridDetectionResult]:
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return None
    return _detect_grid_from_image(image, template)


def detect_table_grid(pdf_bytes: bytes, template: dict) -> Optional[GridDetectionResult]:
    try:
        import pdfplumber
    except Exception:
        return None

    page_index = max(int(template.get("page", 1)) - 1, 0)
    dpi = int(template.get("grid_dpi", 300))

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        if not pdf.pages:
            return None
        page = pdf.pages[page_index] if page_index < len(pdf.pages) else pdf.pages[0]
        image = page.to_image(resolution=dpi).original

    return _detect_grid_from_image(image, template)
