from __future__ import annotations

from typing import Any


def load_template_config(db, template_id: str, collection: str = "templates") -> dict:
    doc = db.collection(collection).document(template_id).get()
    if not doc.exists:
        raise RuntimeError(f"Template not found: {template_id}")
    cfg = doc.to_dict() or {}
    cfg["id"] = template_id
    return cfg


def _coerce_box(box: Any):
    if isinstance(box, dict):
        return [box.get("x", 0), box.get("y", 0), box.get("w", 0), box.get("h", 0)]
    if isinstance(box, (list, tuple)):
        return list(box)
    return []


def _normalize_box(box, width: int, height: int):
    box = _coerce_box(box)
    if not box or len(box) != 4:
        return None
    x, y, w, h = box
    if max(box) <= 1.5:
        x = int(x * width)
        y = int(y * height)
        w = int(w * width)
        h = int(h * height)
    else:
        x = int(x)
        y = int(y)
        w = int(w)
        h = int(h)
    return x, y, w, h


def _group_indices(indices, max_gap: int = 2):
    groups = []
    for idx in sorted(indices):
        if not groups or idx - groups[-1][-1] > max_gap:
            groups.append([idx])
        else:
            groups[-1].append(idx)
    return groups


def _line_centers_from_mask(mask, axis: int, threshold: float, max_gap: int):
    import numpy as np

    projection = mask.sum(axis=axis) / 255.0
    indices = [i for i, value in enumerate(projection) if value >= threshold]
    groups = _group_indices(indices, max_gap=max_gap)
    centers = []
    for group in groups:
        if not group:
            continue
        centers.append(int(sum(group) / len(group)))
    return centers


def _detect_grid_edges(img_bgr, table_box, expected_cols: int | None = None):
    import cv2
    import numpy as np

    h, w = img_bgr.shape[:2]
    normalized = _normalize_box(table_box, w, h)
    if not normalized:
        return None, None
    x0, y0, bw, bh = normalized
    x1 = min(x0 + bw, w)
    y1 = min(y0 + bh, h)
    if x1 <= x0 or y1 <= y0:
        return None, None

    crop = img_bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
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
    scale = max(10, min(height_px, width_px) // 30)
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(1, height_px // scale)))
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(1, width_px // scale), 1)
    )
    vertical = cv2.dilate(cv2.erode(binary, vertical_kernel), vertical_kernel)
    horizontal = cv2.dilate(cv2.erode(binary, horizontal_kernel), horizontal_kernel)

    min_ratio = 0.6
    merge_gap = 2
    col_centers = _line_centers_from_mask(
        vertical, axis=0, threshold=height_px * min_ratio, max_gap=merge_gap
    )
    row_centers = _line_centers_from_mask(
        horizontal, axis=1, threshold=width_px * min_ratio, max_gap=merge_gap
    )

    xs = [x0 + edge for edge in col_centers]
    ys = [y0 + edge for edge in row_centers]

    edges_x = [x0] + sorted(set(xs)) + [x1]
    edges_y = [y0] + sorted(set(ys)) + [y1]

    if expected_cols and len(edges_x) - 1 != expected_cols:
        if expected_cols > 0:
            edges_x = [int(x0 + i * (x1 - x0) / expected_cols) for i in range(expected_cols + 1)]

    return edges_x, edges_y


def _detect_text_row_edges(img_bgr, table_box):
    import cv2
    from statistics import median

    h, w = img_bgr.shape[:2]
    normalized = _normalize_box(table_box, w, h)
    if not normalized:
        return None
    x0, y0, bw, bh = normalized
    x1 = min(x0 + bw, w)
    y1 = min(y0 + bh, h)
    if x1 <= x0 or y1 <= y0:
        return None

    crop = img_bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        21,
        -3,
    )
    binary = 255 - binary

    kernel_width = max(10, bw // 40)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1))
    dilated = cv2.dilate(binary, kernel, iterations=1)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    rows = []
    for contour in contours:
        x, y, w0, h0 = cv2.boundingRect(contour)
        if w0 < bw * 0.05 or h0 < 5:
            continue
        rows.append((y, y + h0))

    if not rows:
        return None

    rows.sort()
    merged = []
    for y1, y2 in rows:
        if not merged or y1 > merged[-1][1] + 4:
            merged.append([y1, y2])
        else:
            merged[-1][1] = max(merged[-1][1], y2)

    centers = [(row[0] + row[1]) / 2 for row in merged]
    heights = [row[1] - row[0] for row in merged]
    if not heights:
        return None
    row_height = max(6, int(median(heights)))

    edges = [max(0, int(centers[0] - row_height / 2))]
    for idx in range(len(centers) - 1):
        edges.append(int((centers[idx] + centers[idx + 1]) / 2))
    edges.append(min(bh, int(centers[-1] + row_height / 2)))

    edges = sorted({int(edge) for edge in edges})
    if len(edges) < 2:
        return None
    return [y0 + edge for edge in edges]


def _crop(img_bgr, box):
    h, w = img_bgr.shape[:2]
    normalized = _normalize_box(box, w, h)
    if not normalized:
        return None
    x, y, bw, bh = normalized
    x1 = min(x + bw, w)
    y1 = min(y + bh, h)
    if x1 <= x or y1 <= y:
        return None
    return img_bgr[y:y1, x:x1].copy()


def crop_rois(warped_ocr_bgr, tpl_cfg: dict, warped_alt_bgr=None) -> dict:
    rois = {}
    rois_cfg = tpl_cfg.get("rois") or {}

    if rois_cfg.get("facility_name_box"):
        rois["facility_name"] = _crop(warped_ocr_bgr, rois_cfg["facility_name_box"])
        if warped_alt_bgr is not None:
            rois["facility_name_alt"] = _crop(
                warped_alt_bgr, rois_cfg["facility_name_box"]
            )

    if rois_cfg.get("menu_band"):
        rois["menu_band"] = _crop(warped_ocr_bgr, rois_cfg["menu_band"])
        if warped_alt_bgr is not None:
            rois["menu_band_alt"] = _crop(warped_alt_bgr, rois_cfg["menu_band"])

    qty = rois_cfg.get("qty") or {}
    if qty:
        boxes = qty.get("boxes_row_major") or []
        schema = qty.get("schema") or {}
        dynamic_rows = bool(qty.get("dynamic_rows") or qty.get("rows_dynamic"))
        table_box = rois_cfg.get("table_box") or rois_cfg.get("table_roi") or [0, 0, 1, 1]
        col_edges = qty.get("column_edges") or []
        row_edges = qty.get("row_edges") or []
        h, w = warped_ocr_bgr.shape[:2]
        edges_x = []
        edges_y = []
        if col_edges:
            edges_x = [int(edge * w) if edge <= 1.5 else int(edge) for edge in col_edges]
        if row_edges:
            edges_y = [int(edge * h) if edge <= 1.5 else int(edge) for edge in row_edges]

        if not edges_x or (dynamic_rows and not edges_y):
            detected_x, detected_y = _detect_grid_edges(
                warped_ocr_bgr, table_box, expected_cols=int(schema.get("cols") or 0)
            )
            if not edges_x and detected_x:
                edges_x = detected_x
            if not edges_y and detected_y:
                edges_y = detected_y

        if dynamic_rows:
            min_rows = int(qty.get("row_min_rows") or 6)
            if not edges_y or (len(edges_y) - 1) < min_rows:
                text_edges = _detect_text_row_edges(warped_ocr_bgr, table_box)
                if text_edges:
                    edges_y = text_edges

        if not boxes or dynamic_rows:
            if edges_x and edges_y:
                boxes = []
                inset = int(qty.get("cell_inset_px") or 6)
                for r in range(len(edges_y) - 1):
                    for c in range(len(edges_x) - 1):
                        x_left, x_right = edges_x[c], edges_x[c + 1]
                        y_top, y_bottom = edges_y[r], edges_y[r + 1]
                        x = x_left + inset
                        y = y_top + inset
                        bw = max(1, (x_right - x_left) - 2 * inset)
                        bh = max(1, (y_bottom - y_top) - 2 * inset)
                        boxes.append([x, y, bw, bh])
                schema = {
                    "rows": max(0, len(edges_y) - 1),
                    "cols": max(0, len(edges_x) - 1),
                    "row_names": [f"r{i}" for i in range(max(0, len(edges_y) - 1))],
                    "col_names": [f"c{j}" for j in range(max(0, len(edges_x) - 1))],
                }
        rois["qty_cells"] = [_crop(warped_ocr_bgr, b) for b in boxes]
        if warped_alt_bgr is not None:
            rois["qty_cells_alt"] = [_crop(warped_alt_bgr, b) for b in boxes]
        rois["qty_schema"] = schema

    if rois_cfg.get("notes_box"):
        rois["notes"] = _crop(warped_ocr_bgr, rois_cfg["notes_box"])
        if warped_alt_bgr is not None:
            rois["notes_alt"] = _crop(warped_alt_bgr, rois_cfg["notes_box"])

    table_box = rois_cfg.get("table_box") or rois_cfg.get("table_roi")
    if table_box:
        rois["table"] = _crop(warped_ocr_bgr, table_box)
        if warped_alt_bgr is not None:
            rois["table_alt"] = _crop(warped_alt_bgr, table_box)

    return rois
