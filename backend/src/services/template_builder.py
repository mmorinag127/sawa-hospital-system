from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TemplateBuildResult:
    template: dict
    template_image: bytes
    debug_images: dict[str, bytes]


def _encode_png(image) -> bytes:
    import cv2  # type: ignore

    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("failed to encode PNG")
    return encoded.tobytes()


def _otsu_bin(gray):
    import cv2  # type: ignore

    den = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
    _, binary = cv2.threshold(den, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def _extract_lines(binary, h_ksize: int, v_ksize: int):
    import cv2  # type: ignore

    inv = 255 - binary
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_ksize, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_ksize))
    h_lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, h_kernel, iterations=1)
    v_lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, v_kernel, iterations=1)
    grid = cv2.bitwise_or(h_lines, v_lines)
    return h_lines, v_lines, grid


def _find_largest_table_bbox(grid_mask, height: int, width: int, margin_ratio: float = 0.02):
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
        return (mx, my, width - 2 * mx, height - 2 * my)
    return best


def _cluster_positions(pos, max_gap: int) -> list[int]:
    import numpy as np  # type: ignore

    if len(pos) == 0:
        return []
    pos = np.sort(pos)
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


def _smooth_1d(x, win: int):
    import numpy as np  # type: ignore

    if win <= 1:
        return x
    k = np.ones(win, dtype=np.float32) / win
    return np.convolve(x.astype(np.float32), k, mode="same")


def _estimate_grid_lines(v_lines, h_lines, table_bbox, cols: Optional[int], rows: Optional[int]):
    import numpy as np  # type: ignore

    x0, y0, w, h = table_bbox
    v = v_lines[y0 : y0 + h, x0 : x0 + w]
    hh = h_lines[y0 : y0 + h, x0 : x0 + w]

    vproj = _smooth_1d(v.sum(axis=0), win=max(5, w // 200))
    hproj = _smooth_1d(hh.sum(axis=1), win=max(5, h // 200))

    vx = np.where(vproj > 0.5 * vproj.max())[0]
    hy = np.where(hproj > 0.5 * hproj.max())[0]

    xs = _cluster_positions(vx, max_gap=max(2, w // 300))
    ys = _cluster_positions(hy, max_gap=max(2, h // 300))

    def reconcile(vals: list[int], expected_lines: Optional[int], limit: int) -> list[int]:
        if expected_lines is None:
            return vals
        if expected_lines <= 1:
            return [0, limit - 1]
        if len(vals) == expected_lines:
            return vals
        if len(vals) < expected_lines:
            return [int(round(i * (limit - 1) / (expected_lines - 1))) for i in range(expected_lines)]
        idxs = np.linspace(0, len(vals) - 1, expected_lines).round().astype(int)
        return [vals[i] for i in idxs]

    xs = reconcile(sorted(set(xs)), (cols + 1) if cols is not None else None, w)
    ys = reconcile(sorted(set(ys)), (rows + 1) if rows is not None else None, h)

    if len(xs) < 2:
        xs = [0, w - 1]
    if len(ys) < 2:
        ys = [0, h - 1]

    return [x0 + x for x in xs], [y0 + y for y in ys]


def _find_rect_roi_in_region(grid_mask, region, min_w: int, min_h: int, max_w: int, max_h: int):
    import cv2  # type: ignore

    x0, y0, w, h = region
    sub = grid_mask[y0 : y0 + h, x0 : x0 + w]
    cnts, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for c in cnts:
        x, y, ww, hh = cv2.boundingRect(c)
        if ww < min_w or hh < min_h or ww > max_w or hh > max_h:
            continue
        area = ww * hh
        if area > best_area:
            best_area = area
            best = (x0 + x, y0 + y, ww, hh)
    return best


def _fallback_box(width: int, height: int, rx: float, ry: float, rw: float, rh: float):
    return (int(width * rx), int(height * ry), int(width * rw), int(height * rh))


def build_template_from_pdf(
    *,
    pdf_bytes: bytes,
    facility_id: str,
    template_id: str,
    rows: Optional[int] = None,
    cols: Optional[int] = None,
    dpi: int = 350,
) -> TemplateBuildResult:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    from src.services.pdf_render import render_pdf_to_png_bytes

    png_bytes = render_pdf_to_png_bytes(pdf_bytes, dpi=dpi, page=1)
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("failed to decode template image")
    height, width = img.shape[:2]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    binary = _otsu_bin(gray)

    h_ksize = max(30, width // 40)
    v_ksize = max(30, height // 60)
    h_lines, v_lines, grid = _extract_lines(binary, h_ksize, v_ksize)

    debug_lines = cv2.cvtColor(255 - grid, cv2.COLOR_GRAY2BGR)

    table_bbox = _find_largest_table_bbox(grid, height, width)
    x0, y0, tw, th = table_bbox

    dbg_table = img.copy()
    cv2.rectangle(dbg_table, (x0, y0), (x0 + tw, y0 + th), (0, 0, 255), 3)

    xs, ys = _estimate_grid_lines(v_lines, h_lines, table_bbox, cols=cols, rows=rows)
    xs = sorted(xs)
    ys = sorted(ys)

    inset = 6
    qty_boxes: list[dict[str, int]] = []
    for r in range(len(ys) - 1):
        for c in range(len(xs) - 1):
            x_left, x_right = xs[c], xs[c + 1]
            y_top, y_bottom = ys[r], ys[r + 1]
            x = x_left + inset
            y = y_top + inset
            w = max(1, (x_right - x_left) - 2 * inset)
            h = max(1, (y_bottom - y_top) - 2 * inset)
            qty_boxes.append(
                {
                    "x": int(x),
                    "y": int(y),
                    "w": int(w),
                    "h": int(h),
                }
            )

    top_region = (0, 0, width, int(height * 0.25))
    name_box = _find_rect_roi_in_region(
        grid,
        top_region,
        min_w=int(width * 0.15),
        min_h=int(height * 0.02),
        max_w=int(width * 0.70),
        max_h=int(height * 0.12),
    ) or _fallback_box(width, height, 0.06, 0.06, 0.45, 0.06)

    bottom_region = (0, int(height * 0.70), width, int(height * 0.30))
    notes_box = _find_rect_roi_in_region(
        grid,
        bottom_region,
        min_w=int(width * 0.50),
        min_h=int(height * 0.06),
        max_w=int(width * 0.95),
        max_h=int(height * 0.35),
    ) or _fallback_box(width, height, 0.05, 0.78, 0.90, 0.18)

    menu_band = (int(x0), int(y0), int(tw * 0.45), int(th))

    dynamic_rows = rows is None
    row_count = rows if rows is not None else 0
    col_count = cols if cols is not None else max(0, len(xs) - 1)

    qty_schema = {
        "rows": row_count,
        "cols": col_count,
        "row_names": [f"r{i}" for i in range(row_count)],
        "col_names": [f"c{j}" for j in range(col_count)],
    }

    qty_payload: dict[str, object] = {
        "schema": qty_schema,
        "boxes_row_major": qty_boxes if not dynamic_rows else [],
        "column_edges": [round(x / width, 6) for x in xs],
        "row_edges": [round(y / height, 6) for y in ys],
        "dynamic_rows": dynamic_rows,
    }

    template_payload = {
        "template_id": template_id,
        "facility_id": facility_id,
        "version": 1,
        "template_image_gcs_uri": "",
        "match": {"orb_nfeatures": 2000, "min_matches": 25, "min_inlier_ratio": 0.15},
        "warp": {"output_size": [int(width), int(height)]},
        "rois": {
            "facility_name_box": [int(v) for v in name_box],
            "menu_band": [int(v) for v in menu_band],
            "table_box": [int(x0), int(y0), int(tw), int(th)],
            "qty": qty_payload,
            "notes_box": [int(v) for v in notes_box],
        },
        "postprocess": {
            "qty_regex": r"^\\d{0,2}$",
            "normalize_fullwidth": True,
            "reject_repetition": {"max_repeat_run": 3, "min_unique_line_ratio": 0.3},
            "retry": {"max_attempts": 2, "crop_inset_px": [6, 6, 6, 6], "alt_binarize": True},
        },
    }

    dbg_grid = img.copy()
    for x in xs:
        cv2.line(dbg_grid, (x, y0), (x, y0 + th), (255, 0, 0), 2)
    for y in ys:
        cv2.line(dbg_grid, (x0, y), (x0 + tw, y), (0, 255, 0), 2)
    cv2.rectangle(
        dbg_grid,
        (name_box[0], name_box[1]),
        (name_box[0] + name_box[2], name_box[1] + name_box[3]),
        (0, 0, 255),
        2,
    )
    cv2.rectangle(
        dbg_grid,
        (notes_box[0], notes_box[1]),
        (notes_box[0] + notes_box[2], notes_box[1] + notes_box[3]),
        (0, 0, 255),
        2,
    )

    debug_images = {
        "debug_lines.png": _encode_png(debug_lines),
        "debug_table_bbox.png": _encode_png(dbg_table),
        "debug_grid_overlay.png": _encode_png(dbg_grid),
    }

    return TemplateBuildResult(
        template=template_payload,
        template_image=png_bytes,
        debug_images=debug_images,
    )
