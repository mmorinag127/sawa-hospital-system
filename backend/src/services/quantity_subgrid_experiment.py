from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

import cv2
import numpy as np

try:
    from app.yomitoku_runner import run_yomitoku
except ModuleNotFoundError:  # pragma: no cover - optional OCR experiment dependency
    def run_yomitoku(*args, **kwargs):  # type: ignore[no-redef]
        raise ModuleNotFoundError("app.yomitoku_runner is unavailable in this environment")


_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}")
_DIGITS_RE = re.compile(r"[0-9]+")
_ONLY_DIGITS_AND_PUNCT_RE = re.compile(r"[0-9\\.,\\-\\s]+")
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
class DigitRereadPatch:
    row_index: int
    col_index: int
    original_text: str
    replacement_text: str
    variant_name: str
    score: int
    candidates: list[dict[str, Any]]


def _clean_cell_text(value: object) -> str:
    text = str(value or "").strip()
    return (
        text.replace("<br>", "\n")
        .replace("<br/>", "\n")
        .replace("<br />", "\n")
        .replace("\\n", "\n")
        .strip()
    )


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
        .replace("て", "1")
        .replace("I", "1")
        .replace("l", "1")
    )
    return bool(_DIGITS_RE.fullmatch(normalized))


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


def crop_image_by_norm_box(image_bgr: np.ndarray, box_norm: list[float], *, padding_px: int = 0) -> np.ndarray:
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


def save_debug_image(path: str, image_bgr: np.ndarray) -> None:
    ok = cv2.imwrite(path, image_bgr)
    if not ok:
        raise RuntimeError(f"failed to write image: {path}")


def _strip_cell_noise(text: str) -> str:
    return text.strip().strip("\"'`.,;:[](){} ")


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


def is_suspicious_quantity_text(value: object) -> bool:
    text = _strip_cell_noise(_clean_cell_text(value))
    if not text:
        return False
    return not bool(_DIGITS_RE.fullmatch(text))


def extract_quantity_digit_context(rows: list[list[str]], row_index: int, col_index: int) -> tuple[str, str]:
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


def crop_cell_image_by_norm_box(image_bgr: np.ndarray, box_norm: list[float], *, padding_px: int = 2) -> np.ndarray:
    return crop_image_by_norm_box(image_bgr, box_norm, padding_px=padding_px)


def build_digit_reread_variants(cell_image_bgr: np.ndarray) -> list[tuple[str, np.ndarray]]:
    gray = cv2.cvtColor(cell_image_bgr, cv2.COLOR_BGR2GRAY)
    variants: list[tuple[str, np.ndarray]] = []

    def _to_bgr(image: np.ndarray) -> np.ndarray:
        if len(image.shape) == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        return image

    raw_x3 = cv2.resize(cell_image_bgr, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    variants.append(("raw_x3", raw_x3))

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    clahe_x3 = cv2.resize(clahe, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    variants.append(("clahe_x3", _to_bgr(clahe_x3)))

    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        21,
        7,
    )
    adaptive_x3 = cv2.resize(adaptive, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_NEAREST)
    variants.append(("adaptive_x3", _to_bgr(adaptive_x3)))

    otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    otsu_x4 = cv2.resize(otsu, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_NEAREST)
    variants.append(("otsu_x4", _to_bgr(otsu_x4)))

    kernel = np.ones((2, 2), np.uint8)
    dilated = cv2.dilate(adaptive, kernel, iterations=1)
    dilated_x4 = cv2.resize(dilated, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_NEAREST)
    variants.append(("adaptive_dilate_x4", _to_bgr(dilated_x4)))

    return variants


def _extract_text_candidates_from_tables(tables: Iterable[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for table in tables:
        rows = table.get("rows") or []
        for row in rows:
            if not isinstance(row, list):
                continue
            for cell in row:
                text = _clean_cell_text(cell)
                if text:
                    values.append(text)
    return values


def _extract_text_candidates(markdown_text: str, tables: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "|" in stripped:
            parts = [part.strip() for part in stripped.split("|")]
            values.extend(part for part in parts if part)
        else:
            values.append(stripped)
    values.extend(_extract_text_candidates_from_tables(tables))
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _score_digit_candidate(candidate: str, *, prev_value: str, next_value: str) -> int:
    if not candidate:
        return -10
    score = 60
    if _DIGITS_RE.fullmatch(candidate):
        score += 25
    if len(candidate) == 1:
        score += 10
    elif len(candidate) == 2:
        score += 4
    else:
        score -= 10
    if prev_value and candidate == prev_value:
        score += 8
    if next_value and candidate == next_value:
        score += 8
    if prev_value and next_value and prev_value == next_value == candidate:
        score += 12
    return score


def reread_suspicious_quantity_cells(
    *,
    quantity_crop_bgr: np.ndarray,
    quantity_table: dict[str, Any],
    dpi: int = 200,
) -> tuple[list[list[str]], list[DigitRereadPatch]]:
    rows = [list(row) for row in (quantity_table.get("rows") or []) if isinstance(row, list)]
    cells = [cell for cell in (quantity_table.get("cells") or []) if isinstance(cell, dict)]
    if not rows or not cells:
        return rows, []

    cell_lookup: dict[tuple[int, int], dict[str, Any]] = {}
    for cell in cells:
        bbox = cell.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        row_index = int(cell.get("row_index") or 0)
        col_index = int(cell.get("col_index") or 0)
        cell_lookup[(row_index, col_index)] = cell

    patches: list[DigitRereadPatch] = []
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            if not is_suspicious_quantity_text(value):
                continue
            cell = cell_lookup.get((row_index, col_index))
            if not cell:
                continue
            prev_value, next_value = extract_quantity_digit_context(rows, row_index, col_index)
            cell_image = crop_cell_image_by_norm_box(quantity_crop_bgr, cell["bbox"], padding_px=2)
            candidates: list[dict[str, Any]] = []
            best: tuple[int, str, str] | None = None
            for variant_name, variant_image in build_digit_reread_variants(cell_image):
                variant_results, _, _ = run_yomitoku(
                    pdf_bytes=None,
                    dpi=dpi,
                    device="cpu",
                    visualize=False,
                    ignore_line_break=True,
                    no_figure=True,
                    figure_width=800,
                    figure_dir="figures",
                    page_images=[(1, variant_image)],
                )
                if not variant_results:
                    continue
                text_candidates = _extract_text_candidates(
                    variant_results[0].markdown_text,
                    list(getattr(variant_results[0], "tables", []) or []),
                )
                normalized_candidates: list[str] = []
                for raw in text_candidates:
                    normalized = normalize_digit_candidate(raw)
                    if normalized and normalized not in normalized_candidates:
                        normalized_candidates.append(normalized)
                if not normalized_candidates:
                    continue
                candidate = normalized_candidates[0]
                score = _score_digit_candidate(candidate, prev_value=prev_value, next_value=next_value)
                candidates.append(
                    {
                        "variant": variant_name,
                        "raw_candidates": text_candidates[:5],
                        "normalized_candidate": candidate,
                        "score": score,
                    }
                )
                if best is None or score > best[0]:
                    best = (score, candidate, variant_name)
            if best is None or best[0] < 70:
                continue
            replacement = best[1]
            rows[row_index][col_index] = replacement
            patches.append(
                DigitRereadPatch(
                    row_index=row_index,
                    col_index=col_index,
                    original_text=_clean_cell_text(value),
                    replacement_text=replacement,
                    variant_name=best[2],
                    score=best[0],
                    candidates=candidates,
                )
            )
    return rows, patches
