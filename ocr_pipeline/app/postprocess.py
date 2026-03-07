from __future__ import annotations

import json
import os
import re
import shutil
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
    if x1 <= x0 or y1 <= y0:
        return image
    return image[y0:y1, x0:x1].copy()


def _alt_binarize(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2)


def _ensure_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _ink_ratio(image: np.ndarray) -> float:
    if image is None or image.size == 0:
        return 0.0
    gray = _ensure_gray(image)
    binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)[1]
    return float(np.count_nonzero(binary)) / float(binary.size or 1)


def _text_band_count(image: np.ndarray) -> int:
    if image is None or image.size == 0:
        return 0
    gray = _ensure_gray(image)
    binary = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)[1]
    projection = np.count_nonzero(binary, axis=1)
    threshold = max(2, int(gray.shape[1] * 0.03))
    active = projection >= threshold
    groups = 0
    in_group = False
    group_start = 0
    for idx, flag in enumerate(active):
        if flag and not in_group:
            in_group = True
            group_start = idx
        elif not flag and in_group:
            if idx - group_start >= 3:
                groups += 1
            in_group = False
    if in_group and len(active) - group_start >= 3:
        groups += 1
    return groups


def _ink_quality(ink_ratio: float, *, min_ratio: float, max_ratio: float) -> float:
    if ink_ratio <= 0:
        return 0.0
    if min_ratio > 0 and ink_ratio < min_ratio:
        return max(0.0, min(1.0, ink_ratio / min_ratio))
    if max_ratio > 0 and ink_ratio > max_ratio:
        span = max(1e-6, 1.0 - max_ratio)
        return max(0.0, 1.0 - ((ink_ratio - max_ratio) / span))
    return 1.0


def _tesseract_qty_fallback_enabled() -> bool:
    return os.environ.get("OCR_ENABLE_TESSERACT_QTY_FALLBACK", "false").lower() == "true"


def _tesseract_digits_text(image: np.ndarray) -> str:
    if shutil.which("tesseract") is None:
        return ""
    try:
        import pytesseract
    except Exception:
        return ""

    gray = _ensure_gray(image)
    variants = [
        gray,
        cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1],
        cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            2,
        ),
    ]
    configs = [
        "--psm 7 -c tessedit_char_whitelist=0123456789",
        "--psm 6 -c tessedit_char_whitelist=0123456789",
    ]
    best = ""
    for variant in variants:
        for cfg in configs:
            raw = pytesseract.image_to_string(variant, lang="eng", config=cfg).strip()
            digits = re.sub(r"\D+", "", raw)
            if digits and len(digits) <= 2:
                return digits
            if len(digits) > len(best):
                best = digits
    return best[:2]


def _tight_crop_to_ink(
    image: np.ndarray,
    *,
    padding_px: int,
    min_ink_ratio: float,
) -> np.ndarray:
    gray = _ensure_gray(image)
    if gray.size == 0:
        return gray
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    ink_ratio = float(np.count_nonzero(binary)) / float(binary.size or 1)
    if ink_ratio < max(0.0, min_ink_ratio):
        return gray
    points = cv2.findNonZero(binary)
    if points is None:
        return gray
    x, y, w, h = cv2.boundingRect(points)
    if w <= 0 or h <= 0:
        return gray
    pad = max(0, int(padding_px))
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(gray.shape[1], x + w + pad)
    y1 = min(gray.shape[0], y + h + pad)
    cropped = gray[y0:y1, x0:x1]
    return cropped if cropped.size else gray


def _connect_strokes(image: np.ndarray) -> np.ndarray:
    gray = _ensure_gray(image)
    inv = 255 - gray
    closed = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=1)
    return 255 - closed


def _suppress_lines(image: np.ndarray) -> np.ndarray:
    gray = _ensure_gray(image)
    inv = 255 - gray
    h, w = gray.shape[:2]
    if h <= 0 or w <= 0:
        return gray
    k_h = max(8, int(w * 0.7))
    k_v = max(8, int(h * 0.7))
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (k_h, 1))
    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, k_v))
    line_h = cv2.erode(inv, kernel_h, iterations=1)
    line_h = cv2.dilate(line_h, kernel_h, iterations=1)
    line_v = cv2.erode(inv, kernel_v, iterations=1)
    line_v = cv2.dilate(line_v, kernel_v, iterations=1)
    lines = cv2.bitwise_or(line_h, line_v)
    filtered = inv.copy()
    filtered[lines > 0] = 0
    return 255 - filtered


def _prepare_qty_variant(
    image: np.ndarray,
    *,
    crop_inset_px: list[int],
    apply_binarize: bool,
    connect_strokes: bool,
    suppress_lines: bool,
    tight_crop: bool,
    tight_crop_padding_px: int,
    tight_crop_min_ink_ratio: float,
    upscale: bool,
    target_min_dim_px: int,
) -> np.ndarray:
    target = _crop_with_inset(image, crop_inset_px) if crop_inset_px else image
    gray = _ensure_gray(target)
    if suppress_lines:
        gray = _suppress_lines(gray)
    if connect_strokes:
        gray = _connect_strokes(gray)
    if apply_binarize:
        gray = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            2,
        )
    if tight_crop:
        gray = _tight_crop_to_ink(
            gray,
            padding_px=tight_crop_padding_px,
            min_ink_ratio=tight_crop_min_ink_ratio,
        )
    min_dim = max(1, min(gray.shape[:2]))
    if upscale and min_dim < max(1, int(target_min_dim_px)):
        scale = min(4.0, max(1.0, float(target_min_dim_px) / float(min_dim)))
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return gray


def _build_qty_variants(
    cell: np.ndarray,
    *,
    alt_cell: np.ndarray | None,
    crop_inset_px: list[int],
    alt_binarize: bool,
    tight_crop: bool,
    tight_crop_padding_px: int,
    tight_crop_min_ink_ratio: float,
    target_min_dim_px: int,
    max_variants: int,
) -> list[tuple[str, np.ndarray]]:
    variants: list[tuple[str, np.ndarray | None]] = [("raw", cell)]
    variants.append(
        (
            "fallback",
            _prepare_qty_variant(
                alt_cell if alt_cell is not None else cell,
                crop_inset_px=crop_inset_px,
                apply_binarize=alt_binarize,
                connect_strokes=True,
                suppress_lines=True,
                tight_crop=tight_crop,
                tight_crop_padding_px=tight_crop_padding_px,
                tight_crop_min_ink_ratio=tight_crop_min_ink_ratio,
                upscale=True,
                target_min_dim_px=target_min_dim_px,
            ),
        )
    )
    if alt_cell is not None:
        variants.append(
            (
                "primary_prep",
                _prepare_qty_variant(
                    cell,
                    crop_inset_px=crop_inset_px,
                    apply_binarize=alt_binarize,
                    connect_strokes=True,
                    suppress_lines=True,
                    tight_crop=tight_crop,
                    tight_crop_padding_px=tight_crop_padding_px,
                    tight_crop_min_ink_ratio=tight_crop_min_ink_ratio,
                    upscale=True,
                    target_min_dim_px=target_min_dim_px,
                ),
            )
        )
        variants.append(("alt_raw", alt_cell))

    unique: list[tuple[str, np.ndarray]] = []
    seen: set[tuple[tuple[int, ...], int]] = set()
    for label, image in variants:
        if image is None or image.size == 0:
            continue
        gray = _ensure_gray(image)
        signature = (gray.shape, hash(gray.tobytes()))
        if signature in seen:
            continue
        seen.add(signature)
        unique.append((label, image))
        if len(unique) >= max_variants:
            break
    return unique


def _parse_qty_candidate(
    raw_text: str,
    *,
    qty_re: re.Pattern[str],
    normalize_fullwidth: bool,
    min_digit_purity: float,
) -> dict[str, Any] | None:
    cleaned = _normalize_digits(raw_text) if normalize_fullwidth else raw_text
    visible = re.sub(r"\s+", "", cleaned)
    if not visible:
        return None
    digits = "".join(ch for ch in visible if ch.isdigit())
    if not digits or not qty_re.match(digits):
        return None
    purity = len(digits) / max(len(visible), 1)
    if purity < min_digit_purity:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    return {
        "value": value,
        "cleaned": cleaned,
        "digits": digits,
        "purity": purity,
        "exact": 1.0 if visible == digits else 0.0,
    }


def _resolve_qty_max_value(post: dict[str, Any], col_key: str) -> int | None:
    by_col = post.get("qty_max_value_by_col")
    if isinstance(by_col, dict):
        raw = by_col.get(col_key)
        try:
            max_value = int(raw) if raw is not None else None
        except Exception:
            max_value = None
        if max_value is not None and max_value >= 0:
            return max_value
    raw = post.get("qty_max_value")
    try:
        max_value = int(raw) if raw is not None else None
    except Exception:
        max_value = None
    if max_value is None or max_value < 0:
        return None
    return max_value


def _choose_qty_candidate(
    candidates: list[dict[str, Any]],
    *,
    agree_votes: int,
    min_confidence: float,
    high_confidence: float,
) -> tuple[dict[str, Any] | None, str]:
    if not candidates:
        return None, "reject_no_candidate"

    grouped: dict[int, list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped.setdefault(int(candidate["value"]), []).append(candidate)

    total_candidates = len(candidates)
    best_summary: dict[str, Any] | None = None
    for value, items in grouped.items():
        votes = len(items)
        vote_score = min(1.0, votes / max(agree_votes, 1))
        purity = float(np.mean([float(item.get("purity") or 0.0) for item in items]))
        exact = float(np.mean([float(item.get("exact") or 0.0) for item in items]))
        ink = float(np.mean([float(item.get("ink_quality") or 0.0) for item in items]))
        conflict_penalty = 0.2 * ((total_candidates - votes) / max(total_candidates, 1))
        confidence = max(
            0.0,
            min(
                1.0,
                (0.35 * vote_score) + (0.35 * purity) + (0.15 * exact) + (0.15 * ink) - conflict_penalty,
            ),
        )
        summary = {
            "value": value,
            "votes": votes,
            "confidence": round(confidence, 4),
            "purity": round(purity, 4),
            "ink_quality": round(ink, 4),
            "sources": [str(item.get("source") or "") for item in items],
            "raw_texts": [str(item.get("raw_text") or "") for item in items],
        }
        if best_summary is None:
            best_summary = summary
            continue
        current_key = (
            float(summary["confidence"]),
            int(summary["votes"]),
            float(summary["purity"]),
            float(summary["ink_quality"]),
        )
        best_key = (
            float(best_summary["confidence"]),
            int(best_summary["votes"]),
            float(best_summary["purity"]),
            float(best_summary["ink_quality"]),
        )
        if current_key > best_key:
            best_summary = summary

    if best_summary is None:
        return None, "reject_no_candidate"
    if int(best_summary["votes"]) >= agree_votes and float(best_summary["confidence"]) >= min_confidence:
        return best_summary, f"agree_votes_{int(best_summary['votes'])}"
    if len(grouped) == 1 and float(best_summary["confidence"]) >= high_confidence:
        return best_summary, "high_conf_single"
    return best_summary, "reject_low_confidence"


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
    qty_strategy = str(post.get("qty_strategy") or post.get("qty_mode") or "cell_ocr").strip().lower()
    qty_disabled = qty_strategy in {"disabled", "off", "none", "skip"}
    qty_re = re.compile(post.get("qty_regex", r"^\d{0,2}$"))
    normalize_fullwidth = bool(post.get("normalize_fullwidth", True))
    repetition_cfg = post.get("reject_repetition") if isinstance(post.get("reject_repetition"), dict) else {}
    max_repeat_run = int(repetition_cfg.get("max_repeat_run", 3))
    min_unique_line_ratio = repetition_cfg.get("min_unique_line_ratio")
    retry_cfg = post.get("retry") if isinstance(post.get("retry"), dict) else {}
    max_attempts = int(retry_cfg.get("max_attempts", 2))
    crop_inset_px = retry_cfg.get("crop_inset_px") or []
    alt_binarize = bool(retry_cfg.get("alt_binarize", False))
    agree_votes = max(1, int(post.get("qty_agree_votes", 2)))
    min_confidence = float(post.get("qty_min_confidence", 0.58) or 0.58)
    high_confidence = float(post.get("qty_high_confidence", 0.67) or 0.67)
    min_digit_purity = float(post.get("qty_min_digit_purity", 0.5) or 0.5)
    min_ink_ratio = float(post.get("qty_min_ink_ratio", 0.003) or 0.003)
    max_ink_ratio = float(post.get("qty_max_ink_ratio", 0.35) or 0.35)
    tesseract_min_ink_ratio = float(
        post.get("qty_tesseract_min_ink_ratio", max(min_ink_ratio, 0.008)) or max(min_ink_ratio, 0.008)
    )
    reject_multiline_bands = int(post.get("qty_reject_multiline_bands") or 0)
    tight_crop = bool(post.get("qty_tight_crop", True))
    tight_crop_padding_px = int(post.get("qty_tight_crop_padding_px", 3) or 3)
    tight_crop_min_ink_ratio = float(post.get("qty_tight_crop_min_ink_ratio", 0.002) or 0.002)
    target_min_dim_px = int(post.get("qty_target_min_dim_px", 72) or 72)
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
    min_menu_line_ratio_for_overlay = float(post.get("qty_min_menu_line_ratio_for_overlay", 0.0) or 0.0)
    min_menu_line_rows_for_overlay = int(post.get("qty_min_menu_line_rows_for_overlay", 10) or 10)

    quantities: dict[str, dict] = {}
    failed_cells: list[dict] = []
    qty_cell_diagnostics: list[dict[str, Any]] = []
    disable_overlay_rows = qty_disabled
    metrics = {
        "ocr_calls": 0,
        "retries": 0,
        "tesseract_qty_calls": 0,
        "accepted_qty_cells": 0,
        "rejected_qty_cells": 0,
        "low_confidence_qty_cells": 0,
        "sanity_rejected_qty_cells": 0,
        "qty_strategy": qty_strategy,
        "overlay_disabled_reason": "",
    }

    if not qty_disabled:
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
            alt_cell = qty_cells_alt[idx] if idx < len(qty_cells_alt) else None
            if reject_multiline_bands > 0:
                band_candidates = [_text_band_count(cell)]
                if alt_cell is not None:
                    band_candidates.append(_text_band_count(alt_cell))
                min_bands = min(count for count in band_candidates if count > 0) if any(band_candidates) else 0
                if min_bands > reject_multiline_bands:
                    quantities.setdefault(row_key, {})[col_key] = None
                    failed_cells.append({"row": row_key, "col": col_key, "reason": "multi_line_cell"})
                    qty_cell_diagnostics.append(
                        {
                            "row": row_key,
                            "col": col_key,
                            "route": "reject_multiline",
                            "band_count": min_bands,
                        }
                    )
                    metrics["rejected_qty_cells"] += 1
                    continue
            variants = _build_qty_variants(
                cell,
                alt_cell=alt_cell,
                crop_inset_px=crop_inset_px,
                alt_binarize=alt_binarize,
                tight_crop=tight_crop,
                tight_crop_padding_px=tight_crop_padding_px,
                tight_crop_min_ink_ratio=tight_crop_min_ink_ratio,
                target_min_dim_px=target_min_dim_px,
                max_variants=max(1, max_attempts),
            )

            raw_texts: list[str] = []
            candidates: list[dict[str, Any]] = []
            max_seen_ink_ratio = 0.0

            for attempt, (variant_label, target) in enumerate(variants):
                if attempt > 0:
                    metrics["retries"] += 1
                metrics["ocr_calls"] += 1
                prompt = _merge_prompt(base_prompt, qty_prompt)
                raw_text = ocr_fn(target, prompt, 32).strip()
                raw_text = _dedup_consecutive_lines(raw_text)
                raw_texts.append(raw_text)
                ink_ratio = _ink_ratio(target)
                max_seen_ink_ratio = max(max_seen_ink_ratio, ink_ratio)
                if _repeat_run_max(raw_text) > max_repeat_run:
                    continue
                if min_unique_line_ratio is not None:
                    try:
                        if _unique_line_ratio(raw_text) < float(min_unique_line_ratio):
                            continue
                    except (TypeError, ValueError):
                        pass
                candidate = _parse_qty_candidate(
                    raw_text,
                    qty_re=qty_re,
                    normalize_fullwidth=normalize_fullwidth,
                    min_digit_purity=min_digit_purity,
                )
                if candidate is None:
                    continue
                candidate["source"] = variant_label
                candidate["raw_text"] = raw_text
                candidate["ink_ratio"] = ink_ratio
                candidate["ink_quality"] = _ink_quality(
                    ink_ratio,
                    min_ratio=min_ink_ratio,
                    max_ratio=max_ink_ratio,
                )
                candidates.append(candidate)

            best_candidate, route = _choose_qty_candidate(
                candidates,
                agree_votes=agree_votes,
                min_confidence=min_confidence,
                high_confidence=high_confidence,
            )
            if (
                route.startswith("reject_")
                and _tesseract_qty_fallback_enabled()
                and (any(text.strip() for text in raw_texts) or max_seen_ink_ratio >= tesseract_min_ink_ratio)
            ):
                tesseract_targets: list[tuple[str, np.ndarray]] = []
                if variants:
                    # Prefer the prepared fallback variant before the raw crop.
                    for variant_label, target in variants:
                        if target is None or target.size == 0:
                            continue
                        if variant_label == "fallback":
                            tesseract_targets.insert(0, (f"tesseract_{variant_label}", target))
                        else:
                            tesseract_targets.append((f"tesseract_{variant_label}", target))
                seen_tesseract_signatures: set[tuple[tuple[int, ...], int]] = set()
                for variant_label, target in tesseract_targets:
                    gray = _ensure_gray(target)
                    signature = (gray.shape, hash(gray.tobytes()))
                    if signature in seen_tesseract_signatures:
                        continue
                    seen_tesseract_signatures.add(signature)
                    metrics["tesseract_qty_calls"] += 1
                    raw_text = _tesseract_digits_text(target).strip()
                    if raw_text:
                        raw_texts.append(raw_text)
                    candidate = _parse_qty_candidate(
                        raw_text,
                        qty_re=qty_re,
                        normalize_fullwidth=normalize_fullwidth,
                        min_digit_purity=min_digit_purity,
                    )
                    if candidate is None:
                        continue
                    ink_ratio = _ink_ratio(target)
                    max_seen_ink_ratio = max(max_seen_ink_ratio, ink_ratio)
                    candidate["source"] = variant_label
                    candidate["raw_text"] = raw_text
                    candidate["ink_ratio"] = ink_ratio
                    candidate["ink_quality"] = _ink_quality(
                        ink_ratio,
                        min_ratio=min_ink_ratio,
                        max_ratio=max_ink_ratio,
                    )
                    candidates.append(candidate)
            best_candidate, route = _choose_qty_candidate(
                candidates,
                agree_votes=agree_votes,
                min_confidence=min_confidence,
                high_confidence=high_confidence,
            )
            sanity_max_value = _resolve_qty_max_value(post, col_key)
            if (
                isinstance(best_candidate, dict)
                and not route.startswith("reject_")
                and sanity_max_value is not None
                and int(best_candidate.get("value") or 0) > sanity_max_value
            ):
                route = "reject_sanity_fail"
            confidence = float(best_candidate.get("confidence") or 0.0) if isinstance(best_candidate, dict) else 0.0
            votes = int(best_candidate.get("votes") or 0) if isinstance(best_candidate, dict) else 0
            if isinstance(best_candidate, dict) and not route.startswith("reject_"):
                parsed = int(best_candidate["value"])
                metrics["accepted_qty_cells"] += 1
            else:
                metrics["rejected_qty_cells"] += 1
                if route == "reject_low_confidence":
                    metrics["low_confidence_qty_cells"] += 1
                if route == "reject_sanity_fail":
                    metrics["sanity_rejected_qty_cells"] += 1

            if parsed is None and (any(text.strip() for text in raw_texts) or max_seen_ink_ratio >= min_ink_ratio):
                failure: dict[str, Any] = {"row": row_key, "col": col_key, "reason": "unreadable"}
                if route == "reject_low_confidence":
                    failure["reason"] = "low_confidence"
                elif route == "reject_sanity_fail":
                    failure["reason"] = "sanity_fail"
                    if sanity_max_value is not None:
                        failure["max_allowed"] = int(sanity_max_value)
                if raw_texts:
                    failure["raw"] = raw_texts[-1]
                if confidence:
                    failure["confidence"] = round(confidence, 4)
                if votes:
                    failure["votes"] = votes
                if isinstance(best_candidate, dict):
                    failure["value"] = int(best_candidate.get("value") or 0)
                failed_cells.append(failure)

            qty_cell_diagnostics.append(
                {
                    "row_index": r_idx,
                    "row": row_key,
                    "field": f"qty.{col_key}",
                    "col": col_key,
                    "value": parsed,
                    "confidence": round(confidence, 4),
                    "votes": votes,
                    "route": route,
                    "max_ink_ratio": round(max_seen_ink_ratio, 4),
                    "max_allowed": sanity_max_value,
                    "raw_texts": [text for text in raw_texts if text],
                }
            )
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
    if (
        not disable_overlay_rows
        and min_menu_line_ratio_for_overlay > 0
        and len(row_names) >= max(1, min_menu_line_rows_for_overlay)
    ):
        menu_lines = [line.strip() for line in menu_band.splitlines() if line.strip()]
        minimum_lines = max(1, int(np.ceil(len(row_names) * min_menu_line_ratio_for_overlay)))
        if len(menu_lines) < minimum_lines:
            disable_overlay_rows = True
            metrics["overlay_disabled_reason"] = "menu_band_row_mismatch"

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
        "qty_cell_diagnostics": qty_cell_diagnostics,
        "failed_cells": failed_cells,
        "notes": notes or "",
        "metrics": metrics,
        "disable_overlay_rows": disable_overlay_rows,
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
