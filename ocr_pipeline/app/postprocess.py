from __future__ import annotations

import json
import re
from typing import Any, Callable

import cv2
import numpy as np


OcrFn = Callable[[np.ndarray, str, int], str]


def _dedup_consecutive_lines(text: str) -> str:
    out = []
    prev = None
    for line in text.splitlines():
        if line != prev:
            out.append(line)
        prev = line
    return "\n".join(out).strip()


def _repeat_run_max(text: str) -> int:
    max_run = 1
    run = 1
    prev = None
    for line in text.splitlines():
        if line and line == prev:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 1
        prev = line
    return max_run


def _unique_line_ratio(text: str) -> float:
    lines = [line for line in text.splitlines() if line]
    if not lines:
        return 1.0
    return len(set(lines)) / len(lines)


def _normalize_digits(text: str) -> str:
    return (
        text.replace("０", "0")
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


def _merge_prompt(base_prompt: str, prompt: str) -> str:
    base = (base_prompt or "").strip()
    if not base:
        return prompt
    return f"{base}\n\n{prompt}"


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


def _alt_binarize(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2)


def _parse_json_payload(raw: str) -> dict | None:
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def postprocess_and_retry(
    *,
    rois: dict,
    tpl_cfg: dict,
    ocr_fn: OcrFn,
    base_prompt: str = "",
) -> dict[str, Any]:
    qty_cells = rois.get("qty_cells") or []
    qty_cells_alt = rois.get("qty_cells_alt") or []
    schema = rois.get("qty_schema") or {}
    rows = int(schema.get("rows") or 0)
    cols = int(schema.get("cols") or 0)
    row_names = schema.get("row_names") or []
    col_names = schema.get("col_names") or []

    post = tpl_cfg.get("postprocess") if isinstance(tpl_cfg.get("postprocess"), dict) else {}
    qty_re = re.compile(post.get("qty_regex", r"^\d{0,2}$"))
    normalize_fullwidth = bool(post.get("normalize_fullwidth", True))
    repetition_cfg = post.get("reject_repetition") if isinstance(post.get("reject_repetition"), dict) else {}
    max_repeat_run = int(repetition_cfg.get("max_repeat_run", 3))
    min_unique_line_ratio = repetition_cfg.get("min_unique_line_ratio")
    retry_cfg = post.get("retry") if isinstance(post.get("retry"), dict) else {}
    max_attempts = int(retry_cfg.get("max_attempts", 2))
    crop_inset_px = retry_cfg.get("crop_inset_px") or []
    alt_binarize = bool(retry_cfg.get("alt_binarize", False))
    qty_prompt = post.get(
        "qty_prompt",
        "Return digits only. If none, return empty. Output numbers only.",
    )
    notes_prompt = post.get(
        "notes_prompt",
        "Return the text in the image as-is. Do not add text.",
    )
    menu_prompt = post.get(
        "menu_prompt",
        "Return the text in the image as-is. Keep line breaks.",
    )
    facility_prompt = post.get(
        "facility_name_prompt",
        "Return the facility name text exactly as shown.",
    )
    table_prompt = post.get("table_prompt") or post.get("full_table_prompt") or ""

    quantities: dict[str, dict] = {}
    failed_cells: list[dict] = []
    metrics = {"ocr_calls": 0, "retries": 0}

    for idx, cell in enumerate(qty_cells):
        r_idx = idx // max(cols, 1)
        c_idx = idx % max(cols, 1)
        row_key = row_names[r_idx] if r_idx < len(row_names) else str(r_idx)
        col_key = col_names[c_idx] if c_idx < len(col_names) else str(c_idx)

        if cell is None:
            quantities.setdefault(row_key, {})[col_key] = None
            failed_cells.append({"row": row_key, "col": col_key, "reason": "missing_roi"})
            continue

        parsed = None
        raw_text = ""
        alt_cell = qty_cells_alt[idx] if idx < len(qty_cells_alt) else None
        for attempt in range(max_attempts):
            if attempt > 0:
                metrics["retries"] += 1
            target = cell
            if attempt > 0 and alt_cell is not None:
                target = alt_cell
            if attempt > 0:
                target = _crop_with_inset(target, crop_inset_px)
            if alt_binarize and attempt > 0:
                target = _alt_binarize(target)
            metrics["ocr_calls"] += 1
            prompt = _merge_prompt(base_prompt, qty_prompt)
            raw_text = ocr_fn(target, prompt, 32).strip()
            raw_text = _dedup_consecutive_lines(raw_text)
            if _repeat_run_max(raw_text) > max_repeat_run:
                continue
            if min_unique_line_ratio is not None:
                try:
                    if _unique_line_ratio(raw_text) < float(min_unique_line_ratio):
                        continue
                except (TypeError, ValueError):
                    pass
            cleaned = _normalize_digits(raw_text) if normalize_fullwidth else raw_text
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
            failed_cells.append({"row": row_key, "col": col_key, "raw": raw_text})
        quantities.setdefault(row_key, {})[col_key] = parsed

    facility_name = ""
    facility_name_crop = rois.get("facility_name")
    facility_name_alt = rois.get("facility_name_alt")
    if facility_name_crop is not None:
        metrics["ocr_calls"] += 1
        prompt = _merge_prompt(base_prompt, facility_prompt)
        primary = ocr_fn(facility_name_crop, prompt, 128)
        facility_name = _dedup_consecutive_lines(primary)
        if not facility_name and facility_name_alt is not None:
            metrics["ocr_calls"] += 1
            fallback = ocr_fn(facility_name_alt, prompt, 128)
            facility_name = _dedup_consecutive_lines(fallback)

    menu_band = ""
    menu_band_crop = rois.get("menu_band")
    menu_band_alt = rois.get("menu_band_alt")
    if menu_band_crop is not None:
        metrics["ocr_calls"] += 1
        prompt = _merge_prompt(base_prompt, menu_prompt)
        primary = ocr_fn(menu_band_crop, prompt, 512)
        menu_band = _dedup_consecutive_lines(primary)
        if not menu_band and menu_band_alt is not None:
            metrics["ocr_calls"] += 1
            fallback = ocr_fn(menu_band_alt, prompt, 512)
            menu_band = _dedup_consecutive_lines(fallback)

    notes = ""
    notes_crop = rois.get("notes")
    notes_alt = rois.get("notes_alt")
    if notes_crop is not None:
        metrics["ocr_calls"] += 1
        prompt = _merge_prompt(base_prompt, notes_prompt)
        primary = ocr_fn(notes_crop, prompt, 256)
        notes = _dedup_consecutive_lines(primary)
        if not notes and notes_alt is not None:
            metrics["ocr_calls"] += 1
            fallback = ocr_fn(notes_alt, prompt, 256)
            notes = _dedup_consecutive_lines(fallback)

    return {
        "template_id": tpl_cfg.get("id"),
        "facility_name": facility_name,
        "menu_band": menu_band,
        "qty": quantities,
        "qty_row_order": row_names,
        "qty_col_order": col_names,
        "failed_cells": failed_cells,
        "notes": notes or "",
        "metrics": metrics,
        **_extract_table_rows(
            rois=rois,
            base_prompt=base_prompt,
            table_prompt=table_prompt,
            ocr_fn=ocr_fn,
            metrics=metrics,
        ),
    }


def _extract_table_rows(
    *,
    rois: dict,
    base_prompt: str,
    table_prompt: str,
    ocr_fn: OcrFn,
    metrics: dict,
) -> dict[str, Any]:
    table_crop = rois.get("table")
    table_alt = rois.get("table_alt")
    if table_crop is None:
        return {}
    prompt = _merge_prompt(base_prompt, table_prompt) if table_prompt else base_prompt
    if not prompt:
        return {}
    metrics["ocr_calls"] += 1
    raw = ocr_fn(table_crop, prompt, 2048)
    parsed = _parse_json_payload(raw)
    if parsed is None and table_alt is not None:
        metrics["ocr_calls"] += 1
        raw = ocr_fn(table_alt, prompt, 2048)
        parsed = _parse_json_payload(raw)
    if not parsed:
        return {"table_raw": raw}
    output: dict[str, Any] = {}
    rows = parsed.get("rows")
    if isinstance(rows, list):
        output["rows"] = rows
    errors = parsed.get("errors")
    if isinstance(errors, list):
        output["errors"] = errors
    fax_datetime = parsed.get("fax_datetime")
    if isinstance(fax_datetime, str):
        output["fax_datetime"] = fax_datetime
    return output
