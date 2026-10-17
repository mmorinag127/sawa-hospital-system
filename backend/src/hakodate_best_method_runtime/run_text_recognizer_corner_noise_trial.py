#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
from copy import deepcopy
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

def _resolve_backend_root() -> Path:
    path = Path(__file__).resolve()
    for candidate in path.parents:
        if (candidate / "src").exists() and (candidate / "requirements.txt").exists():
            return candidate
        if (candidate / "backend" / "src").exists():
            return candidate / "backend"
    return path.parents[2]


BACKEND_ROOT = _resolve_backend_root()
WORKSPACE = BACKEND_ROOT.parent if (BACKEND_ROOT.parent / "tmp").exists() else BACKEND_ROOT
sys.path.insert(0, str(BACKEND_ROOT))

from src.services.hakodate_recognizer_input_evidence import contact_crop_evidence  # noqa: E402
from src.services.hakodate_ocr_evidence_service import normalize_ocr_value, recognition_resolution  # noqa: E402

from src.services.hakodate_cell_ocr_batch_service import (  # noqa: E402
    _normalize_digits,
    _expanded_cell_box,
    _erase_known_cell_frame,
    _remove_small_noise_only,
    _safe_int_box,
    _bgr_from_pil,
    _build_preprocess_for_ocr,
    _draw_text_safe,
    _load_overlay_font,
    draw_ocr_results_overlay,
    sheet_assignments_from_ocr_regions,
    sheet_value_grid_from_assignments,
)
from src.services.hakodate_step_review_pipeline_service import (  # noqa: E402
    _make_review_canvas,
    _split_line_masks,
    _write_pdf_from_pages,
)
from src.services.yomitoku_text_recognizer_topk import (  # noqa: E402
    YomitokuTextRecognizerTopKWrapper,
    validate_recognizer_result,
)
from src.services.recognizer_diagnostics import diagnosed  # noqa: E402
from src.services.hakodate_recognizer_contact_sheet import (
    _remove_table_lines,
    _remove_noise_components,
    _foreground_centered,
    _preprocess_crop_for_recognizer,
    _preprocess_dynamic_crop_for_recognizer,
    _component_has_hole,
    _printed_zero_shape_candidate,
    _preprocess_corner_component_crop_for_recognizer,
    _region_polygon,
    _safe_int_box_for_polygon,
    _mask_crop_to_region_polygon,
    _warp_polygon_cell_to_axis_aligned_crop,
    build_recognizer_contact_sheet,
)


DEFAULT_BASE = WORKSPACE / "tmp" / "outer_quad_eval_correct_20260426"
DEFAULT_MANIFEST = DEFAULT_BASE / "step123_no_code_change_20260427" / "manifest.json"
DEFAULT_BASELINE_SUMMARY = DEFAULT_BASE / "hakodate_cell_ocr_batch_20260428_current" / "cell_ocr_summary.json"
DEFAULT_OUTPUT_DIR = DEFAULT_BASE / "hakodate_text_recognizer_corner_noise_trial_20260430"
DEFAULT_TRUTH_ROOT = WORKSPACE / "tmp" / "hakodate_text_recognizer_trial_20260428" / "best_method_overlay_all_facilities"
DEFAULT_TRUTH_RECORDS = (
    DEFAULT_TRUTH_ROOT
    / "01_FAC00001_ORD7a83fd79"
    / "best_method_records.json"
)
FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
TRIAL_ENGINE = "yomitoku_text_recognizer_corner_noise_trial"
DEFAULT_RECOGNIZER_MODES = ("raw", "clean", "corner_cc")


@diagnosed("initialize")
def _load_text_recognizer(device: str):
    from src.services.hakodate_empty_sequence_model import load_empty_sequence_recognizer

    return load_empty_sequence_recognizer(device)


def _clean_text_for_number(value: object) -> str:
    text = str(value or "").strip().translate(FULLWIDTH_DIGIT_TRANS)
    text = text.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1").replace("|", "1")
    return re.sub(r"[^0-9]", "", text)


def run_text_recognizer_direct(
    *,
    recognizer: Any,
    contact_sheet: Image.Image,
    regions: list[dict[str, Any]],
    polygons: list[list[list[int]]],
    score_threshold: float,
    digit_score_threshold: float,
    candidate_digit_score_threshold: float,
    enable_context_repair: bool,
    sequence_top_k: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    t0 = time.perf_counter()
    if not isinstance(regions, list) or not isinstance(polygons, list) or len(regions) != len(polygons):
        raise ValueError("recognizer_input_cardinality_invalid")
    requested_polygons = deepcopy(polygons)
    if polygons:
        wrapper = YomitokuTextRecognizerTopKWrapper(
            recognizer,
            token_top_k=5,
            sequence_top_k=sequence_top_k,
            max_decode_steps=8,
        )
        results = wrapper.recognize(_bgr_from_pil(contact_sheet), points=polygons)
        validate_recognizer_result(results, expected_points=requested_polygons)
        contents = results["contents"]
        scores = results["scores"]
        directions = results["directions"]
        candidates_by_region = results["candidates"]
    else:
        contents = []
        scores = []
        directions = []
        candidates_by_region = []
    elapsed = time.perf_counter() - t0
    assigned: list[dict[str, Any]] = []
    accepted_count = 0
    digit_prediction_count = 0
    candidate_digit_prediction_count = 0
    candidate_digit_accept_count = 0
    for index, region in enumerate(regions):
        raw_text = contents[index].strip()
        score = float(scores[index])
        direction = directions[index]
        candidates = list(candidates_by_region[index])
        normalized_raw = normalize_ocr_value(raw_text)
        digits = normalized_raw if re.fullmatch(r"[0-9]+", normalized_raw) else ""
        if digits:
            digit_prediction_count += 1
        if any(_clean_text_for_number(candidate.get("text")) for candidate in candidates):
            candidate_digit_prediction_count += 1
        accepted = bool(digits and score >= digit_score_threshold)
        if accepted:
            accepted_count += 1
        text = normalized_raw
        decision_source = ""
        if accepted:
            decision_source = "raw_digits"
        assigned.append(
            {
                **region,
                "ocr_text": text,
                "ocr_normalized": text,
                "ocr_words": [
                    {
                        "text": raw_text,
                        "normalized_digits": digits,
                        "score": score,
                        "direction": direction,
                        "candidates": candidates,
                    }
                ],
                "ocr_word_count": 1 if raw_text else 0,
                "recognizer_raw_text": raw_text,
                "recognizer_score": score,
                "recognizer_digit_score_threshold": digit_score_threshold,
                "recognizer_direction": direction,
                "recognizer_accepted": accepted,
                "recognizer_candidates": candidates,
                "recognizer_candidate_accepted": False,
                "recognizer_accepted_candidate": None,
                "recognizer_decision_source": decision_source,
            }
        )
        input_evidence = contact_crop_evidence(contact_sheet, requested_polygons[index])
        assigned[-1]["recognizer_input_evidence"] = input_evidence
        resolution = recognition_resolution(assigned[-1])
        assigned[-1].update(resolution)
        if resolution["resolution_state"] == "review_required":
            assigned[-1].update(ocr_text=None, ocr_normalized=None,
                                recognizer_accepted=False, recognizer_candidate_accepted=False,
                                recognizer_decision_source="review_required")
            accepted_count -= int(accepted)
        elif region.get("role") == "note":
            assigned[-1].update(ocr_text=raw_text, ocr_normalized=raw_text)
    # Alternative candidates and column context never replace a model's raw value.
    return assigned, {
        "engine": TRIAL_ENGINE,
        "ocr_seconds": round(float(elapsed), 3),
        "candidate_count": len(regions),
        "raw_prediction_count": len(contents),
        "digit_prediction_count": digit_prediction_count,
        "accepted_digit_count": accepted_count,
        "candidate_digit_prediction_count": candidate_digit_prediction_count,
        "candidate_digit_accept_count": candidate_digit_accept_count,
        "context_repair_count": 0,
        "score_threshold": score_threshold,
        "digit_score_threshold": digit_score_threshold,
        "candidate_digit_score_threshold": candidate_digit_score_threshold,
        "sequence_top_k": sequence_top_k,
        **({"recognition_model_metadata": deepcopy(recognizer.recognition_model_metadata)}
           if hasattr(recognizer, "recognition_model_metadata") else {}),
    }


def summarize_regions(regions: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [r for r in regions if str(r.get("ocr_normalized") or "").strip()]
    raw_digit = [r for r in regions if _clean_text_for_number(r.get("recognizer_raw_text"))]
    raw_nonempty = [r for r in regions if str(r.get("recognizer_raw_text") or "").strip()]
    candidates = [r for r in regions if r.get("recognizer_candidate")]
    skipped = [r for r in regions if r.get("recognizer_skipped")]
    false_like = [
        {
            "region_id": r.get("region_id"),
            "raw": r.get("recognizer_raw_text"),
            "digits": _clean_text_for_number(r.get("recognizer_raw_text")),
            "score": round(float(r.get("recognizer_score") or 0.0), 4),
            "candidates": [
                {
                    "text": candidate.get("text"),
                    "score": round(float(candidate.get("score") or 0.0), 4),
                    "digits": _clean_text_for_number(candidate.get("text")),
                }
                for candidate in list(r.get("recognizer_candidates") or [])[:5]
            ],
        }
        for r in raw_digit[:25]
    ]
    return {
        "region_count": len(regions),
        "candidate_count": len(candidates),
        "skipped_count": len(skipped),
        "raw_nonempty_count": len(raw_nonempty),
        "raw_digit_count": len(raw_digit),
        "accepted_digit_count": len(accepted),
        "examples": false_like,
    }


def _candidate_summary(candidate: dict[str, Any]) -> str:
    text = str(candidate.get("text") or "")
    score = float(candidate.get("score") or 0.0)
    digits = _clean_text_for_number(text)
    suffix = f" -> {digits}" if digits and digits != text else ""
    return f"{text or '<blank>'} ({score:.3f}){suffix}"


def load_truth_records(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        return {}
    truth: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        sheet_cell = str(record.get("sheet_cell") or "").strip()
        if not sheet_cell:
            continue
        truth[sheet_cell] = {
            "expected_digits": str(record.get("expected_digits") or ""),
            "field": record.get("field"),
            "field_label": record.get("field_label"),
            "source": record.get("supervised_label_source"),
        }
    return truth


def resolve_truth_records_path(path: Path, *, page: int, facility_code: str, order_id: str) -> Path | None:
    if path.is_file():
        return path
    if not path.exists() or not path.is_dir():
        return None
    direct = path / f"{page:02d}_{facility_code}_{order_id}" / "best_method_records.json"
    if direct.exists():
        return direct
    matches = sorted(path.glob(f"*_{facility_code}_{order_id}/best_method_records.json"))
    return matches[0] if matches else None


def _first_candidate_digits(region: dict[str, Any]) -> str:
    raw_digits = _clean_text_for_number(region.get("recognizer_raw_text"))
    if raw_digits:
        return raw_digits
    for candidate in region.get("recognizer_candidates") or []:
        digits = _clean_text_for_number(candidate.get("text"))
        if digits:
            return digits
    return ""


def _candidate_digit_options(region: dict[str, Any]) -> list[str]:
    options: list[str] = []
    for value in [region.get("recognizer_raw_text")] + [
        candidate.get("text") for candidate in region.get("recognizer_candidates") or []
    ]:
        digits = _clean_text_for_number(value)
        if digits and digits not in options:
            options.append(digits)
    return options


def _nearest_column_values(
    *,
    regions: list[dict[str, Any]],
    target: dict[str, Any],
    limit: int = 8,
) -> list[str]:
    target_row = int(target.get("worksheet_row") or 0)
    target_field = str(target.get("field") or "")
    target_col = int(target.get("worksheet_col") or 0)
    ranked: list[tuple[int, str]] = []
    for region in regions:
        if region is target or str(region.get("field") or "") != target_field:
            continue
        if int(region.get("worksheet_col") or 0) != target_col:
            continue
        value = str(region.get("ocr_normalized") or "").strip()
        if not value.isdigit():
            continue
        row = int(region.get("worksheet_row") or 0)
        if row <= 0 or target_row <= 0:
            continue
        distance = abs(row - target_row)
        if distance == 0 or distance > limit:
            continue
        ranked.append((distance, value))
    ranked.sort(key=lambda item: item[0])
    return [value for _distance, value in ranked]


def _has_substantial_digit_ink(region: dict[str, Any]) -> bool:
    stats = region.get("recognizer_ink_stats")
    if not isinstance(stats, dict):
        return False
    return (
        int(stats.get("kept_component_count") or 0) >= 1
        and int(stats.get("ink_area") or 0) >= 120
        and int(stats.get("bbox_width") or 0) >= 12
        and int(stats.get("bbox_height") or 0) >= 24
    )


def _choose_column_context_value(
    *,
    region: dict[str, Any],
    neighbor_values: list[str],
) -> str | None:
    if len(neighbor_values) < 2:
        return None
    counts: dict[str, int] = {}
    for value in neighbor_values:
        counts[value] = counts.get(value, 0) + 1
    ranked_values = sorted(counts.items(), key=lambda item: (-item[1], neighbor_values.index(item[0])))
    candidate_options = _candidate_digit_options(region)
    current = str(region.get("ocr_normalized") or "").strip()

    if not current:
        if not _has_substantial_digit_ink(region):
            return None
        value, count = ranked_values[0]
        if count < 4 or count / len(neighbor_values) < 0.75:
            return None
        return value

    if len(current) == 1:
        raw_text = str(region.get("recognizer_raw_text") or "").strip().translate(FULLWIDTH_DIGIT_TRANS)
        if raw_text == current:
            return None
        suffix_matches = [value for value, count in ranked_values if count >= 2 and value.endswith(current)]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        return None

    if len(current) == 2 and current.isdigit():
        numeric_neighbors = sorted(int(value) for value in neighbor_values if len(value) == 2 and value.isdigit())
        if not numeric_neighbors:
            return None
        median_neighbor = numeric_neighbors[len(numeric_neighbors) // 2]
        current_value = int(current)
        if abs(current_value - median_neighbor) <= 10:
            return None
        for value, count in ranked_values:
            if count < 2 or value == current or value not in candidate_options:
                continue
            if abs(int(value) - median_neighbor) <= 5:
                return value
    return None


def _apply_column_context_digit_repair_once(regions: list[dict[str, Any]], *, pass_index: int) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for region in regions:
        current = str(region.get("ocr_normalized") or "").strip()
        if str(region.get("role") or "") != "quantity":
            repaired.append(region)
            continue
        if current and not current.isdigit():
            repaired.append(region)
            continue
        neighbor_values = _nearest_column_values(regions=regions, target=region)
        chosen = _choose_column_context_value(region=region, neighbor_values=neighbor_values)
        if not chosen or chosen == current:
            repaired.append(region)
            continue
        repaired_region = {
            **region,
            "ocr_text": chosen,
            "ocr_normalized": chosen,
            "recognizer_context_repair": {
                "from": current,
                "to": chosen,
                "pass": pass_index,
                "neighbor_values": neighbor_values[:8],
                "candidate_digit_options": _candidate_digit_options(region),
            },
        }
        repaired.append(repaired_region)
    return repaired


def apply_column_context_digit_repair(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair OCR-only outliers using same-column local consistency, without fax-specific thresholds."""
    repaired = regions
    for pass_index in range(1, 4):
        next_repaired = _apply_column_context_digit_repair_once(repaired, pass_index=pass_index)
        if json.dumps(next_repaired, sort_keys=True, ensure_ascii=False) == json.dumps(
            repaired,
            sort_keys=True,
            ensure_ascii=False,
        ):
            break
        repaired = next_repaired
    return repaired


def attach_truth_and_eval(
    *,
    regions: list[dict[str, Any]],
    truth_by_cell: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    regular_rows = []
    all_rows = []
    for region in regions:
        sheet_cell = str(region.get("sheet_cell") or region.get("region_id") or "")
        truth = truth_by_cell.get(sheet_cell)
        if truth:
            region["truth"] = truth
            expected_any = str(truth.get("expected_digits") or "")
            pred_any = str(region.get("ocr_normalized") or "")
            all_rows.append(
                {
                    "sheet_cell": sheet_cell,
                    "field": region.get("field"),
                    "field_label": region.get("field_label"),
                    "expected": expected_any,
                    "pred_accepted": pred_any,
                    "accepted_ok": pred_any == expected_any,
                    "raw": region.get("recognizer_raw_text"),
                    "score": region.get("recognizer_score"),
                }
            )
        if str(region.get("field") or "") != "qty.regular_x":
            continue
        expected = str((truth or {}).get("expected_digits") or "")
        pred_accepted = str(region.get("ocr_normalized") or "")
        pred_first_digit = _first_candidate_digits(region)
        regular_rows.append(
            {
                "sheet_cell": sheet_cell,
                "expected": expected,
                "pred_accepted": pred_accepted,
                "pred_first_digit": pred_first_digit,
                "accepted_ok": pred_accepted == expected,
                "first_digit_ok": pred_first_digit == expected,
                "raw": region.get("recognizer_raw_text"),
                "score": region.get("recognizer_score"),
                "component_stats": region.get("recognizer_ink_stats"),
            }
        )
    nonempty = [row for row in regular_rows if row["expected"]]
    all_nonempty = [row for row in all_rows if row["expected"]]
    return {
        "all_count": len(all_rows),
        "all_nonempty_count": len(all_nonempty),
        "all_accepted_exact_count": sum(1 for row in all_rows if row["accepted_ok"]),
        "all_accepted_exact_rate": round(
            sum(1 for row in all_rows if row["accepted_ok"]) / max(1, len(all_rows)),
            4,
        ),
        "all_nonempty_accepted_exact_count": sum(1 for row in all_nonempty if row["accepted_ok"]),
        "all_nonempty_accepted_exact_rate": round(
            sum(1 for row in all_nonempty if row["accepted_ok"]) / max(1, len(all_nonempty)),
            4,
        ),
        "all_accepted_mismatches": [row for row in all_rows if not row["accepted_ok"]],
        "regular_count": len(regular_rows),
        "regular_nonempty_count": len(nonempty),
        "regular_accepted_exact_count": sum(1 for row in regular_rows if row["accepted_ok"]),
        "regular_accepted_exact_rate": round(
            sum(1 for row in regular_rows if row["accepted_ok"]) / max(1, len(regular_rows)),
            4,
        ),
        "regular_first_digit_exact_count": sum(1 for row in regular_rows if row["first_digit_ok"]),
        "regular_first_digit_exact_rate": round(
            sum(1 for row in regular_rows if row["first_digit_ok"]) / max(1, len(regular_rows)),
            4,
        ),
        "regular_nonempty_first_digit_exact_count": sum(1 for row in nonempty if row["first_digit_ok"]),
        "regular_nonempty_first_digit_exact_rate": round(
            sum(1 for row in nonempty if row["first_digit_ok"]) / max(1, len(nonempty)),
            4,
        ),
        "regular_mismatches": [
            row
            for row in regular_rows
            if row["pred_first_digit"] != row["expected"]
        ],
        "regular_accepted_mismatches": [row for row in regular_rows if not row["accepted_ok"]],
    }


def build_unaccepted_topk_review(
    *,
    contact_sheet: Image.Image,
    regions: list[dict[str, Any]],
    max_items: int = 36,
) -> Image.Image:
    review_targets = [
        region
        for region in regions
        if region.get("recognizer_candidate")
        and not str(region.get("ocr_normalized") or "").strip()
        and (
            str(region.get("recognizer_raw_text") or "").strip()
            or any(str(candidate.get("text") or "").strip() for candidate in region.get("recognizer_candidates") or [])
        )
    ]
    review_targets.sort(
        key=lambda region: (
            not any(_clean_text_for_number(candidate.get("text")) for candidate in region.get("recognizer_candidates") or []),
            int(region.get("ocr_contact_slot_index") or 0),
        )
    )
    review_targets = review_targets[:max_items]
    row_h = 124
    crop_w = 220
    canvas_w = 1500
    title_h = 52
    canvas_h = max(title_h + row_h, title_h + row_h * len(review_targets))
    canvas = Image.new("RGB", (canvas_w, canvas_h), "white")
    draw = ImageDraw.Draw(canvas)
    font = _load_overlay_font(20)
    small = _load_overlay_font(16)
    _draw_text_safe(
        draw,
        (18, 14),
        f"accepted なしセルの top-k 候補 ({len(review_targets)} shown)",
        fill=(0, 0, 0, 255),
        font=font,
    )
    for idx, region in enumerate(review_targets):
        y = title_h + idx * row_h
        slot = region.get("ocr_contact_crop_box") or region.get("ocr_contact_slot")
        if isinstance(slot, list) and len(slot) == 4:
            x0, y0, x1, y1 = [int(round(float(value))) for value in slot]
            crop = contact_sheet.crop((x0, y0, x1, y1)).convert("RGB")
            crop.thumbnail((crop_w - 22, row_h - 18), Image.Resampling.LANCZOS)
            canvas.paste(crop, (14, y + (row_h - crop.height) // 2))
            draw.rectangle((12, y + 8, crop_w - 4, y + row_h - 8), outline=(210, 210, 210), width=1)
        raw_text = str(region.get("recognizer_raw_text") or "")
        raw_score = float(region.get("recognizer_score") or 0.0)
        candidates = list(region.get("recognizer_candidates") or [])[:5]
        topk = " / ".join(_candidate_summary(candidate) for candidate in candidates)
        line1 = (
            f"{region.get('region_id')} slot={region.get('ocr_contact_slot_index')} "
            f"raw={raw_text or '<blank>'} ({raw_score:.3f})"
        )
        line2 = f"top-k: {topk or '<none>'}"
        _draw_text_safe(draw, (crop_w + 18, y + 18), line1, fill=(0, 0, 0, 255), font=font)
        _draw_text_safe(draw, (crop_w + 18, y + 52), line2, fill=(170, 0, 0, 255), font=small)
    if not review_targets:
        _draw_text_safe(draw, (18, 76), "対象なし", fill=(0, 0, 0, 255), font=font)
    return canvas


def build_page_result(
    *,
    item: dict[str, Any],
    page: int,
    output_dir: Path,
    recognizer: Any,
    render_width: int,
    score_threshold: float,
    digit_score_threshold: float,
    candidate_digit_score_threshold: float,
    min_ink_area: int,
    min_ink_height: int,
    sequence_top_k: int,
    modes: tuple[str, ...],
    enable_context_repair: bool,
    truth_by_cell: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], Image.Image]:
    pre = _build_preprocess_for_ocr(item=item, page=page, render_width=render_width)
    facility_code = str(pre["facility_code"])
    order_id = str(pre["order_id"])
    case_dir = output_dir / f"{page:02d}_{facility_code}_{order_id}"
    case_dir.mkdir(parents=True, exist_ok=True)
    horizontal_line_mask, vertical_line_mask = _split_line_masks(pre["raw_rectified"])
    line_mask = cv2.bitwise_or(horizontal_line_mask, vertical_line_mask)
    variants: dict[str, dict[str, Any]] = {}
    review_images: list[Image.Image] = []
    for mode in modes:
        contact_sheet, regions, polygons, skipped_regions = build_recognizer_contact_sheet(
            rectified_fax_bgr=pre["raw_rectified"],
            regions=pre["target_regions"],
            line_mask=line_mask if mode == "clean" else None,
            mode=mode,
            min_ink_area=min_ink_area,
            min_ink_height=min_ink_height,
        )
        recognized_regions, metrics = run_text_recognizer_direct(
            recognizer=recognizer,
            contact_sheet=contact_sheet,
            regions=regions,
            polygons=polygons,
            score_threshold=score_threshold,
            digit_score_threshold=digit_score_threshold,
            candidate_digit_score_threshold=candidate_digit_score_threshold,
            enable_context_repair=enable_context_repair,
            sequence_top_k=sequence_top_k,
        )
        ocr_regions = sorted(
            recognized_regions + skipped_regions,
            key=lambda item: int(item.get("ocr_contact_slot_index") or 0),
        )
        truth_eval = attach_truth_and_eval(regions=ocr_regions, truth_by_cell=truth_by_cell)
        assignments = sheet_assignments_from_ocr_regions(ocr_regions)
        sheet_values = sheet_value_grid_from_assignments(assignments)
        overlay = draw_ocr_results_overlay(target_overlay=pre["target_overlay"], ocr_regions=ocr_regions)
        topk_review = build_unaccepted_topk_review(contact_sheet=contact_sheet, regions=ocr_regions)
        contact_sheet_path = case_dir / f"{mode}_text_recognizer_contact_sheet.png"
        regions_path = case_dir / f"{mode}_text_recognizer_regions.json"
        assignments_path = case_dir / f"{mode}_text_recognizer_sheet_assignments.json"
        sheet_values_path = case_dir / f"{mode}_text_recognizer_sheet_values.json"
        overlay_path = case_dir / f"{mode}_text_recognizer_overlay.png"
        topk_review_path = case_dir / f"{mode}_unaccepted_topk_review.png"
        contact_sheet.save(contact_sheet_path)
        overlay.save(overlay_path)
        topk_review.save(topk_review_path)
        regions_path.write_text(json.dumps(ocr_regions, ensure_ascii=False, indent=2), encoding="utf-8")
        assignments_path.write_text(json.dumps(assignments, ensure_ascii=False, indent=2), encoding="utf-8")
        sheet_values_path.write_text(json.dumps(sheet_values, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = summarize_regions(ocr_regions)
        variants[mode] = {
            **metrics,
            **summary,
            "truth_eval": truth_eval,
            "outputs": {
                "contact_sheet": str(contact_sheet_path),
                "regions": str(regions_path),
                "sheet_assignments": str(assignments_path),
                "sheet_values": str(sheet_values_path),
                "overlay": str(overlay_path),
                "unaccepted_topk_review": str(topk_review_path),
            },
        }
        details = [
            f"mode={mode} engine={TRIAL_ENGINE}",
            (
                f"regions={summary['region_count']} candidates={summary['candidate_count']} "
                f"skipped={summary['skipped_count']} raw_digits={summary['raw_digit_count']} "
                f"accepted_digits={summary['accepted_digit_count']}"
            ),
            (
                f"score_threshold={score_threshold} digit_threshold={digit_score_threshold} "
                f"candidate_digit_threshold={candidate_digit_score_threshold} ocr_sec={metrics['ocr_seconds']}"
            ),
            (
                f"topk={metrics['sequence_top_k']} candidate_digits={metrics['candidate_digit_prediction_count']} "
                f"candidate_accept_plus={metrics['candidate_digit_accept_count']}"
            ),
            (
                f"regular accepted exact={truth_eval['regular_accepted_exact_count']}/"
                f"{truth_eval['regular_count']} all accepted exact={truth_eval['all_accepted_exact_count']}/"
                f"{truth_eval['all_count']}"
            ),
            "green grid/red points: fixed preprocessing output; red digit labels: accepted TextRecognizer digits",
        ]
        review_images.append(
            _make_review_canvas(
                title=f"Hakodate TextRecognizer direct OCR trial ({mode})",
                facility_code=facility_code,
                order_id=order_id,
                image=overlay,
                details=details,
            )
        )
        review_images.append(topk_review)
    width = max(img.width for img in review_images)
    height = sum(img.height for img in review_images)
    page_canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for img in review_images:
        page_canvas.paste(img, (0, y))
        y += img.height
    review_page_path = case_dir / "text_recognizer_trial_review_page.png"
    page_canvas.save(review_page_path)
    return {
        "page": page,
        "facility_code": facility_code,
        "order_id": order_id,
        "variants": variants,
        "review_page": str(review_page_path),
    }, page_canvas


def load_baseline_counts(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "engine": data.get("ocr_engine"),
        "count": data.get("count"),
        "total_physical_region_count": data.get("total_physical_region_count"),
        "total_logical_assignment_count": data.get("total_logical_assignment_count"),
        "total_recognized_region_count": data.get("total_recognized_region_count"),
        "total_recognized_assignment_count": data.get("total_recognized_assignment_count"),
        "total_elapsed_seconds": data.get("total_elapsed_seconds"),
        "summary_path": str(path),
    }


def write_contact_sheet_preview(results: list[dict[str, Any]], output_dir: Path, *, modes: tuple[str, ...]) -> str:
    canvases: list[Image.Image] = []
    for result in results:
        for mode in modes:
            path = Path(result["variants"][mode]["outputs"]["contact_sheet"])
            image = Image.open(path).convert("RGB")
            crop = image.crop((0, 0, min(image.width, 18 * 132), min(image.height, 6 * 84)))
            crop.thumbnail((1500, 460), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (1540, 520), "white")
            canvas.paste(crop, (20, 20))
            ImageDraw.Draw(canvas).text(
                (20, 490),
                f"p{result['page']} {result['facility_code']} {mode} top contact rows",
                fill=(0, 0, 0),
            )
            canvases.append(canvas)
        if len(canvases) >= 6:
            break
    out = Image.new("RGB", (1540, 520 * len(canvases)), "white")
    for idx, canvas in enumerate(canvases):
        out.paste(canvas, (0, idx * 520))
    path = output_dir / "text_recognizer_contact_sheet_preview.png"
    out.save(path)
    return str(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--baseline-summary", type=Path, default=DEFAULT_BASELINE_SUMMARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--render-width", type=int, default=1864)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--score-threshold", type=float, default=0.45)
    parser.add_argument("--digit-score-threshold", type=float, default=0.05)
    parser.add_argument("--candidate-digit-score-threshold", type=float, default=0.05)
    parser.add_argument("--min-ink-area", type=int, default=18)
    parser.add_argument("--min-ink-height", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=14)
    parser.add_argument("--sequence-top-k", type=int, default=5)
    parser.add_argument("--truth-records", type=Path, default=DEFAULT_TRUTH_RECORDS)
    parser.add_argument("--facility-code", default="")
    parser.add_argument("--order-id", default="")
    parser.add_argument("--modes", default=",".join(DEFAULT_RECOGNIZER_MODES))
    parser.add_argument("--no-context-repair", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    modes = tuple(mode.strip() for mode in str(args.modes).split(",") if mode.strip())
    if not modes:
        raise ValueError("at least one recognizer mode is required")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    items = manifest.get("results") if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        raise ValueError("manifest results are missing")
    if args.facility_code:
        items = [item for item in items if str(item.get("facility_code") or "") == args.facility_code]
    if args.order_id:
        items = [item for item in items if str(item.get("order_id") or "") == args.order_id]
    if not items:
        raise ValueError("no manifest items matched the requested filters")
    items = items[: max(1, args.max_pages)]
    recognizer = _load_text_recognizer(args.device)
    pages: list[Image.Image] = []
    results: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for page, item in enumerate(items, start=1):
        facility_code = str(item.get("facility_code") or "")
        order_id = str(item.get("order_id") or "")
        truth_path = resolve_truth_records_path(
            args.truth_records,
            page=page,
            facility_code=facility_code,
            order_id=order_id,
        )
        truth_by_cell = load_truth_records(truth_path) if truth_path is not None else {}
        result, review_page = build_page_result(
            item=item,
            page=page,
            output_dir=output_dir,
            recognizer=recognizer,
            render_width=args.render_width,
            score_threshold=args.score_threshold,
            digit_score_threshold=args.digit_score_threshold,
            candidate_digit_score_threshold=args.candidate_digit_score_threshold,
            min_ink_area=args.min_ink_area,
            min_ink_height=args.min_ink_height,
            sequence_top_k=args.sequence_top_k,
            modes=modes,
            enable_context_repair=not args.no_context_repair,
            truth_by_cell=truth_by_cell,
        )
        result["truth_records"] = str(truth_path) if truth_path is not None else None
        results.append(result)
        pages.append(review_page)
    pdf_path = output_dir / "text_recognizer_trial_all14.pdf"
    _write_pdf_from_pages(pages, pdf_path)
    totals = {
        mode: {
            "raw_nonempty_count": sum(int(item["variants"][mode]["raw_nonempty_count"]) for item in results),
            "raw_digit_count": sum(int(item["variants"][mode]["raw_digit_count"]) for item in results),
            "accepted_digit_count": sum(int(item["variants"][mode]["accepted_digit_count"]) for item in results),
            "candidate_digit_prediction_count": sum(
                int(item["variants"][mode]["candidate_digit_prediction_count"]) for item in results
            ),
            "candidate_digit_accept_count": sum(
                int(item["variants"][mode]["candidate_digit_accept_count"]) for item in results
            ),
            "ocr_seconds": round(sum(float(item["variants"][mode]["ocr_seconds"]) for item in results), 3),
        }
        for mode in modes
    }
    summary = {
        "engine": TRIAL_ENGINE,
        "count": len(results),
        "elapsed_seconds": round(float(time.perf_counter() - t0), 3),
        "score_threshold": args.score_threshold,
        "digit_score_threshold": args.digit_score_threshold,
        "candidate_digit_score_threshold": args.candidate_digit_score_threshold,
        "min_ink_area": args.min_ink_area,
        "min_ink_height": args.min_ink_height,
        "sequence_top_k": args.sequence_top_k,
        "modes": list(modes),
        "context_repair": not args.no_context_repair,
        "truth_records": str(args.truth_records),
        "baseline_document_analyzer": load_baseline_counts(args.baseline_summary),
        "totals": totals,
        "pdf": str(pdf_path),
        "contact_sheet_preview": write_contact_sheet_preview(results, output_dir, modes=modes),
        "results": results,
    }
    summary_path = output_dir / "text_recognizer_trial_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))



if __name__ == "__main__":
    main()
