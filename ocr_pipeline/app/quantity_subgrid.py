from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

import cv2
import numpy as np

from app.yomitoku_runner import run_yomitoku


_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}")
_DIGITS_RE = re.compile(r"[0-9]+")
_ONLY_DIGITS_AND_PUNCT_RE = re.compile(r"[0-9\.,\-\s]+")
_NON_MENU_TOKENS = {
    "",
    "-",
    "日付",
    "日 付",
    "区分",
    "区 分",
    "献立",
    "備考",
    "備考欄",
}
_DIGIT_CONFUSABLES = {
    "O": "0",
    "o": "0",
    "D": "0",
    "Q": "0",
    "I": "1",
    "l": "1",
    "|": "1",
    "て": "2",
    "で": "2",
    "ニ": "2",
    "へ": "2",
    "N": "2",
    "n": "2",
    "S": "5",
    "s": "5",
    "B": "8",
}


@dataclass
class QuantitySubgridSpec:
    body_start_row: int
    menu_col_index: int
    quantity_start_col_index: int
    crop_box_norm: list[float]
    row_count: int
    quantity_col_count: int


@dataclass
class QuantitySubgridPass:
    page_index: int
    table_index: int
    spec: QuantitySubgridSpec
    crop_png_bytes: bytes
    markdown_text: str
    tables: list[dict[str, Any]]
    normalized_rows: list[list[str]]
    normalization_patches: list[dict[str, Any]]
    ocr_pdf: bytes | None
    layout_pdf: bytes | None


def _clean_cell_text(value: object) -> str:
    text = str(value or "").strip()
    return (
        text.replace("<br>", "\n")
        .replace("<br/>", "\n")
        .replace("<br />", "\n")
        .replace("\\n", "\n")
        .strip()
    )


def _strip_cell_noise(text: str) -> str:
    return text.strip().strip("\"'`.,;:[](){} ")


def _looks_like_menu_text(value: object) -> bool:
    text = _clean_cell_text(value)
    if text in _NON_MENU_TOKENS:
        return False
    if _DATE_RE.search(text):
        return False
    if len(text) <= 1 and not re.search(r"[一-龥ぁ-んァ-ヶ]", text):
        return False
    return bool(re.search(r"[一-龥ぁ-んァ-ヶ]", text))


def _looks_like_numeric_text(value: object) -> bool:
    text = _clean_cell_text(value)
    if not text:
        return False
    normalized = (
        text.replace("O", "0")
        .replace("o", "0")
        .replace("I", "1")
        .replace("l", "1")
        .replace("て", "1")
    )
    return bool(_DIGITS_RE.fullmatch(normalized))


def normalize_digit_candidate(value: object) -> str:
    text = _strip_cell_noise(_clean_cell_text(value))
    if not text:
        return ""
    if _DIGITS_RE.fullmatch(text):
        return text
    if text in _DIGIT_CONFUSABLES:
        return _DIGIT_CONFUSABLES[text]
    if _ONLY_DIGITS_AND_PUNCT_RE.fullmatch(text):
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits if digits else ""
    converted = "".join(_DIGIT_CONFUSABLES.get(ch, ch) for ch in text)
    if _DIGITS_RE.fullmatch(converted):
        return converted
    digits = "".join(ch for ch in converted if ch.isdigit())
    if digits and len(digits) <= 3:
        return digits
    return ""


def _extract_neighbor_digits(
    rows: list[list[str]],
    *,
    row_index: int,
    col_index: int,
) -> tuple[str, str]:
    prev_value = ""
    next_value = ""
    for probe in range(row_index - 1, -1, -1):
        if col_index >= len(rows[probe]):
            continue
        prev_value = normalize_digit_candidate(rows[probe][col_index])
        if prev_value:
            break
    for probe in range(row_index + 1, len(rows)):
        if col_index >= len(rows[probe]):
            continue
        next_value = normalize_digit_candidate(rows[probe][col_index])
        if next_value:
            break
    return prev_value, next_value


def normalize_quantity_subgrid_table_rows(table: dict[str, Any]) -> tuple[list[list[str]], list[dict[str, Any]]]:
    rows = [list(row) for row in (table.get("rows") or []) if isinstance(row, list)]
    if not rows:
        return [], []
    normalized_rows = [list(row) for row in rows]
    patches: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            raw_text = _clean_cell_text(value)
            if not raw_text or _DIGITS_RE.fullmatch(raw_text):
                continue
            candidate = normalize_digit_candidate(raw_text)
            if not candidate:
                continue
            prev_value, next_value = _extract_neighbor_digits(rows, row_index=row_index, col_index=col_index)
            neighbor_match = bool(candidate and (candidate == prev_value or candidate == next_value))
            strong_alignment = bool(prev_value and next_value and prev_value == next_value == candidate)
            punctuation_only = bool(_ONLY_DIGITS_AND_PUNCT_RE.fullmatch(raw_text))
            if not (neighbor_match or strong_alignment or punctuation_only):
                continue
            normalized_rows[row_index][col_index] = candidate
            patches.append(
                {
                    "row_index": row_index,
                    "col_index": col_index,
                    "original_text": raw_text,
                    "normalized_text": candidate,
                    "prev_neighbor": prev_value,
                    "next_neighbor": next_value,
                }
            )
    return normalized_rows, patches


def _infer_body_start_row(rows: list[list[str]]) -> int:
    for row_index, row in enumerate(rows):
        joined = " ".join(_clean_cell_text(cell) for cell in row if _clean_cell_text(cell))
        if _DATE_RE.search(joined):
            return row_index
    return 0


def _column_bounds(table: dict[str, Any]) -> dict[int, list[float]]:
    bounds: dict[int, list[float]] = {}
    for cell in table.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        bbox = cell.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        col_index = int(cell.get("col_index") or 0)
        current = bounds.get(col_index)
        if current is None:
            bounds[col_index] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
            continue
        current[0] = min(current[0], float(bbox[0]))
        current[1] = min(current[1], float(bbox[1]))
        current[2] = max(current[2], float(bbox[2]))
        current[3] = max(current[3], float(bbox[3]))
    return bounds


def _row_bounds(table: dict[str, Any]) -> dict[int, list[float]]:
    bounds: dict[int, list[float]] = {}
    for cell in table.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        bbox = cell.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        row_index = int(cell.get("row_index") or 0)
        current = bounds.get(row_index)
        if current is None:
            bounds[row_index] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
            continue
        current[0] = min(current[0], float(bbox[0]))
        current[1] = min(current[1], float(bbox[1]))
        current[2] = max(current[2], float(bbox[2]))
        current[3] = max(current[3], float(bbox[3]))
    return bounds


def infer_quantity_subgrid(table: dict[str, Any]) -> QuantitySubgridSpec | None:
    rows = [list(row) for row in (table.get("rows") or []) if isinstance(row, list)]
    col_count = int(table.get("col_count") or 0)
    row_count = int(table.get("row_count") or 0)
    if not rows or col_count <= 0 or row_count <= 0:
        return None

    body_start_row = _infer_body_start_row(rows)
    sample_end = min(len(rows), body_start_row + 24)
    header_rows = rows[:body_start_row]
    sampled_rows = rows[body_start_row:sample_end]
    if not sampled_rows:
        return None

    menu_scores: list[tuple[int, int, int]] = []
    for col_index in range(col_count):
        menu_hits = 0
        numeric_hits = 0
        for row in sampled_rows:
            if col_index >= len(row):
                continue
            cell = row[col_index]
            if _looks_like_menu_text(cell):
                menu_hits += 1
            if _looks_like_numeric_text(cell):
                numeric_hits += 1
        menu_scores.append((col_index, menu_hits, numeric_hits))

    menu_col_index = max(menu_scores, key=lambda item: (item[1], -item[2]))[0]
    quantity_start_col_index: int | None = None
    for col_index in range(menu_col_index + 1, col_count):
        header_text = " ".join(
            _clean_cell_text(row[col_index])
            for row in header_rows
            if col_index < len(row) and _clean_cell_text(row[col_index])
        )
        numeric_hits = sum(
            1
            for row in sampled_rows
            if col_index < len(row) and _looks_like_numeric_text(row[col_index])
        )
        if header_text or numeric_hits >= 2:
            quantity_start_col_index = col_index
            break
    if quantity_start_col_index is None:
        return None

    col_bounds = _column_bounds(table)
    row_bounds = _row_bounds(table)
    if quantity_start_col_index not in col_bounds or body_start_row not in row_bounds:
        return None

    x0 = col_bounds[quantity_start_col_index][0]
    y0 = row_bounds[body_start_row][1]
    x1 = max(bounds[2] for bounds in col_bounds.values())
    y1 = max(bounds[3] for bounds in row_bounds.values())
    if x1 <= x0 or y1 <= y0:
        return None

    return QuantitySubgridSpec(
        body_start_row=body_start_row,
        menu_col_index=menu_col_index,
        quantity_start_col_index=quantity_start_col_index,
        crop_box_norm=[float(x0), float(y0), float(x1), float(y1)],
        row_count=max(0, row_count - body_start_row),
        quantity_col_count=max(0, col_count - quantity_start_col_index),
    )


def crop_image_by_norm_box(
    image_bgr: np.ndarray,
    box_norm: list[float],
    *,
    padding_px: int = 0,
) -> np.ndarray:
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("image_bgr is empty")
    if not isinstance(box_norm, list) or len(box_norm) < 4:
        raise ValueError("box_norm must contain four values")

    height, width = image_bgr.shape[:2]
    x0 = max(0, int(float(box_norm[0]) * width) - padding_px)
    y0 = max(0, int(float(box_norm[1]) * height) - padding_px)
    x1 = min(width, int(float(box_norm[2]) * width) + padding_px)
    y1 = min(height, int(float(box_norm[3]) * height) + padding_px)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("normalized crop box resolves to an empty image")
    return image_bgr[y0:y1, x0:x1].copy()


def _encode_png(image_bgr: np.ndarray) -> bytes:
    ok, png = cv2.imencode(".png", image_bgr)
    if not ok:
        raise RuntimeError("cv2.imencode failed for quantity subgrid crop")
    return png.tobytes()


def build_quantity_subgrid_second_passes(
    *,
    page_results: Iterable[Any],
    page_images: Iterable[tuple[int, np.ndarray]],
    dpi: int,
    device: str,
    visualize: bool,
    ignore_line_break: bool,
    no_figure: bool,
    figure_width: int,
    figure_dir: str,
    max_passes: int = 1,
) -> list[QuantitySubgridPass]:
    page_image_map = {int(page_index): image for page_index, image in page_images}
    if not page_image_map:
        return []

    passes: list[QuantitySubgridPass] = []
    for page_result in page_results:
        page_index = int(getattr(page_result, "page_index", 0) or 0)
        if page_index <= 0:
            continue
        page_image = page_image_map.get(page_index)
        if page_image is None:
            continue
        tables = list(getattr(page_result, "tables", []) or [])
        for table_index, table in enumerate(tables):
            if not isinstance(table, dict):
                continue
            spec = infer_quantity_subgrid(table)
            if spec is None:
                continue
            crop = crop_image_by_norm_box(page_image, spec.crop_box_norm, padding_px=4)
            sub_results, ocr_pdf, layout_pdf = run_yomitoku(
                pdf_bytes=None,
                dpi=dpi,
                device=device,
                visualize=visualize,
                ignore_line_break=ignore_line_break,
                no_figure=no_figure,
                figure_width=figure_width,
                figure_dir=figure_dir,
                page_images=[(page_index, crop)],
            )
            if not sub_results:
                continue
            sub_page = sub_results[0]
            normalized_rows: list[list[str]] = []
            normalization_patches: list[dict[str, Any]] = []
            first_table = next(
                (dict(item) for item in (getattr(sub_page, "tables", []) or []) if isinstance(item, dict)),
                None,
            )
            if first_table is not None:
                normalized_rows, normalization_patches = normalize_quantity_subgrid_table_rows(first_table)
            passes.append(
                QuantitySubgridPass(
                    page_index=page_index,
                    table_index=table_index,
                    spec=spec,
                    crop_png_bytes=_encode_png(crop),
                    markdown_text=str(getattr(sub_page, "markdown_text", "") or ""),
                    tables=[dict(item) for item in (getattr(sub_page, "tables", []) or []) if isinstance(item, dict)],
                    normalized_rows=normalized_rows,
                    normalization_patches=normalization_patches,
                    ocr_pdf=ocr_pdf,
                    layout_pdf=layout_pdf,
                )
            )
            if len(passes) >= max(1, int(max_passes)):
                return passes
    return passes
