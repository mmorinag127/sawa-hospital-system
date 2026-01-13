from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Callable

import numpy as np


OcrFn = Callable[[np.ndarray, str, int], str]


def _normalize_box(box: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    if not box or len(box) != 4:
        return 0, 0, 0, 0
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


def _coerce_box(box: Any) -> list[float]:
    if isinstance(box, dict):
        return [box.get("x", 0), box.get("y", 0), box.get("w", 0), box.get("h", 0)]
    if isinstance(box, (list, tuple)):
        return list(box)
    return [0, 0, 0, 0]


def _crop_box(image: np.ndarray, box: list[float]) -> np.ndarray | None:
    height, width = image.shape[:2]
    x, y, w, h = _normalize_box(box, width, height)
    if w <= 0 or h <= 0:
        return None
    x1 = min(x + w, width)
    y1 = min(y + h, height)
    if x1 <= x or y1 <= y:
        return None
    return image[y:y1, x:x1].copy()


def _crop_with_inset(image: np.ndarray, inset: list[int]) -> np.ndarray:
    if len(inset) != 4:
        return image
    top, right, bottom, left = inset
    h, w = image.shape[:2]
    x0 = max(left, 0)
    y0 = max(top, 0)
    x1 = max(w - right, x0)
    y1 = max(h - bottom, y0)
    return image[y0:y1, x0:x1].copy()


def crop_rois(image_bgr: np.ndarray, template: dict) -> dict[str, Any]:
    rois: dict[str, Any] = {}
    config = template.get("rois") if isinstance(template.get("rois"), dict) else {}
    if not config:
        return rois

    if config.get("facility_name_box"):
        rois["facility_name"] = _crop_box(image_bgr, config.get("facility_name_box"))
    if config.get("menu_band"):
        rois["menu_band"] = _crop_box(image_bgr, config.get("menu_band"))
    if config.get("notes_box"):
        rois["notes"] = _crop_box(image_bgr, config.get("notes_box"))

    qty = config.get("qty")
    if isinstance(qty, dict):
        boxes = qty.get("boxes_row_major") or []
        rois["qty_cells"] = [_crop_box(image_bgr, _coerce_box(box)) for box in boxes]
        rois["qty_schema"] = qty.get("schema") or {}
    return rois


def extract_quantities(rois: dict[str, Any], template: dict, ocr_fn: OcrFn) -> tuple[dict, list[dict]]:
    qty_cells = rois.get("qty_cells") or []
    schema = rois.get("qty_schema") or {}
    rows = int(schema.get("rows") or 0)
    cols = int(schema.get("cols") or 0)
    row_names = schema.get("row_names") or []
    col_names = schema.get("col_names") or []

    post = template.get("postprocess") if isinstance(template.get("postprocess"), dict) else {}
    qty_re = re.compile(post.get("qty_regex", r"^\d{0,2}$"))
    retry_cfg = post.get("retry") if isinstance(post.get("retry"), dict) else {}
    max_attempts = int(retry_cfg.get("max_attempts", 2))
    crop_inset_px = retry_cfg.get("crop_inset_px") or []

    quantities: dict[str, dict] = {}
    failed_cells: list[dict] = []

    for idx, cell in enumerate(qty_cells):
        r_idx = idx // max(cols, 1)
        c_idx = idx % max(cols, 1)
        row_key = row_names[r_idx] if r_idx < len(row_names) else str(r_idx)
        col_key = col_names[c_idx] if c_idx < len(col_names) else str(c_idx)
        if cell is None:
            quantities.setdefault(row_key, {})[col_key] = None
            failed_cells.append({"row": row_key, "col": col_key, "reason": "missing_roi"})
            continue
        raw_text = ""
        parsed = None
        for attempt in range(max_attempts):
            target = _crop_with_inset(cell, crop_inset_px) if attempt > 0 else cell
            raw_text = ocr_fn(target, "画像内の数字のみ返してください。なければ空。", 32).strip()
            cleaned = (
                raw_text.replace("０", "0")
                .replace("１", "1")
                .replace("２", "2")
                .replace("３", "3")
                .replace("４", "4")
                .replace("５", "5")
                .replace("６", "6")
                .replace("７", "7")
                .replace("８", "8")
                .replace("９", "9")
            )
            if cleaned == "":
                parsed = None
                break
            if qty_re.match(cleaned):
                try:
                    parsed = int(cleaned)
                except ValueError:
                    parsed = None
                break
        if parsed is None and raw_text:
            failed_cells.append({"row": row_key, "col": col_key, "reason": "unreadable"})
        quantities.setdefault(row_key, {})[col_key] = parsed

    return quantities, failed_cells


def build_ocr_output(
    *,
    job_id: str,
    status: str,
    template_id: str | None,
    facility_id: str | None,
    input_reference: str | None,
    output_reference: str | None,
    quantities: dict,
    notes: str | None,
    failed_cells: list[dict],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": status,
        "template_id": template_id,
        "facility_id": facility_id,
        "input_reference": input_reference,
        "output_reference": output_reference,
        "quantities": quantities,
        "notes": notes or "",
        "failed_cells": failed_cells,
        "warnings": warnings or [],
        "created_at": datetime.utcnow().isoformat(),
    }
