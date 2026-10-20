#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
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
WORKSPACE = BACKEND_ROOT if (BACKEND_ROOT / "tmp").exists() else BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

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
from src.services.yomitoku_text_recognizer_topk import YomitokuTextRecognizerTopKWrapper  # noqa: E402
from src.services.hakodate_recognizer_contact_sheet import (
    _remove_table_lines,
    _remove_noise_components,
    _foreground_centered,
    _preprocess_crop_for_recognizer,
    _preprocess_dynamic_crop_for_recognizer,
)
from src.services.recognizer_diagnostics import diagnosed  # noqa: E402


from src.hakodate_best_method_runtime.text_recognizer_common import (
    FULLWIDTH_DIGIT_TRANS,
    _clean_text_for_number,
    summarize_regions,
    _candidate_summary,
    build_unaccepted_topk_review,
    load_baseline_counts,
)


DEFAULT_BASE = WORKSPACE / "tmp" / "outer_quad_eval_correct_20260426"
DEFAULT_MANIFEST = DEFAULT_BASE / "step123_no_code_change_20260427" / "manifest.json"
DEFAULT_BASELINE_SUMMARY = DEFAULT_BASE / "hakodate_cell_ocr_batch_20260428_current" / "cell_ocr_summary.json"
DEFAULT_OUTPUT_DIR = DEFAULT_BASE / "hakodate_text_recognizer_trial_20260428"
TRIAL_ENGINE = "yomitoku_text_recognizer_direct"
RECOGNIZER_MODES = ("raw", "clean", "dynamic")


@diagnosed("initialize")
def _load_text_recognizer(device: str):
    from yomitoku import TextRecognizer  # noqa: PLC0415

    return TextRecognizer(device=device, visualize=False)


def build_recognizer_contact_sheet(
    *,
    rectified_fax_bgr: np.ndarray,
    regions: list[dict[str, Any]],
    line_mask: np.ndarray | None,
    mode: str,
    slot_width: int = 132,
    slot_height: int = 84,
    columns: int = 18,
    margin_ratio: float = 0.18,
    min_ink_area: int = 18,
    min_ink_height: int = 8,
) -> tuple[Image.Image, list[dict[str, Any]], list[list[list[int]]], list[dict[str, Any]]]:
    height, width = rectified_fax_bgr.shape[:2]
    row_count = max(1, (len(regions) + columns - 1) // columns)
    sheet = Image.new("RGB", (columns * slot_width, row_count * slot_height), "white")
    usable_regions: list[dict[str, Any]] = []
    skipped_regions: list[dict[str, Any]] = []
    polygons: list[list[list[int]]] = []
    for slot_index, region in enumerate(regions):
        box = region.get("bbox")
        if not isinstance(box, list):
            continue
        if mode == "dynamic":
            px_box = _expanded_cell_box(box, width=width, height=height, pad_x_px=4, pad_y_px=12)
        else:
            px_box = _safe_int_box(box, width=width, height=height, margin_ratio=margin_ratio)
        if not px_box:
            continue
        x0, y0, x1, y1 = px_box
        crop = rectified_fax_bgr[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        crop_line_mask = line_mask[y0:y1, x0:x1] if line_mask is not None else None
        if mode == "dynamic":
            crop_image, ink_stats = _preprocess_dynamic_crop_for_recognizer(
                crop,
                cell_box=box,
                crop_box=px_box,
                slot_width=slot_width,
                slot_height=slot_height,
            )
        else:
            crop_image, ink_stats = _preprocess_crop_for_recognizer(
                crop,
                line_mask=crop_line_mask,
                slot_width=slot_width,
                slot_height=slot_height,
                mode=mode,
            )
        slot_col = slot_index % columns
        slot_row = slot_index // columns
        slot_x = slot_col * slot_width
        slot_y = slot_row * slot_height
        paste_x = slot_x + (slot_width - crop_image.width) // 2
        paste_y = slot_y + (slot_height - crop_image.height) // 2
        is_candidate = bool(
            int(ink_stats["ink_area"]) >= min_ink_area and int(ink_stats["bbox_height"]) >= min_ink_height
        )
        if is_candidate:
            sheet.paste(crop_image, (paste_x, paste_y))
        polygon = [
            [paste_x, paste_y],
            [paste_x + crop_image.width, paste_y],
            [paste_x + crop_image.width, paste_y + crop_image.height],
            [paste_x, paste_y + crop_image.height],
        ]
        prepared_region = {
            **region,
            "ocr_contact_slot_index": slot_index,
            "ocr_contact_slot": [slot_x, slot_y, slot_x + slot_width, slot_y + slot_height],
            "ocr_contact_crop_box": [paste_x, paste_y, paste_x + crop_image.width, paste_y + crop_image.height],
            "ocr_cell_crop_bbox_px": [x0, y0, x1, y1],
            "recognizer_crop_mode": mode,
            "recognizer_ink_stats": ink_stats,
            "recognizer_candidate": is_candidate,
        }
        if is_candidate:
            polygons.append(polygon)
            usable_regions.append(prepared_region)
        else:
            skipped_regions.append(
                {
                    **prepared_region,
                    "ocr_text": "",
                    "ocr_normalized": "",
                    "ocr_words": [],
                    "ocr_word_count": 0,
                    "recognizer_raw_text": "",
                    "recognizer_score": 0.0,
                    "recognizer_direction": "",
                    "recognizer_accepted": False,
                    "recognizer_skipped": True,
                }
            )
    return sheet, usable_regions, polygons, skipped_regions


def run_text_recognizer_direct(
    *,
    recognizer: Any,
    contact_sheet: Image.Image,
    regions: list[dict[str, Any]],
    polygons: list[list[list[int]]],
    score_threshold: float,
    sequence_top_k: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    t0 = time.perf_counter()
    if polygons:
        wrapper = YomitokuTextRecognizerTopKWrapper(
            recognizer,
            token_top_k=5,
            sequence_top_k=sequence_top_k,
            max_decode_steps=8,
        )
        results = wrapper.recognize(_bgr_from_pil(contact_sheet), points=polygons)
        contents = list(results.get("contents") or [])
        scores = [float(value) for value in results.get("scores") or []]
        directions = list(results.get("directions") or [])
        candidates_by_region = list(results.get("candidates") or [])
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
        raw_text = str(contents[index] if index < len(contents) else "").strip()
        score = float(scores[index] if index < len(scores) else 0.0)
        direction = str(directions[index] if index < len(directions) else "")
        candidates = list(candidates_by_region[index] if index < len(candidates_by_region) else [])
        digits = _clean_text_for_number(raw_text)
        if digits:
            digit_prediction_count += 1
        accepted_candidate = next(
            (
                {
                    **candidate,
                    "normalized_digits": _clean_text_for_number(candidate.get("text")),
                }
                for candidate in candidates
                if _clean_text_for_number(candidate.get("text"))
                and float(candidate.get("score") or 0.0) >= score_threshold
            ),
            None,
        )
        if any(_clean_text_for_number(candidate.get("text")) for candidate in candidates):
            candidate_digit_prediction_count += 1
        accepted = bool(digits and score >= score_threshold)
        candidate_accepted = accepted_candidate is not None
        if accepted:
            accepted_count += 1
        if candidate_accepted and not accepted:
            candidate_digit_accept_count += 1
        text = digits if accepted else str((accepted_candidate or {}).get("normalized_digits") or "")
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
                "recognizer_direction": direction,
                "recognizer_accepted": accepted,
                "recognizer_candidates": candidates,
                "recognizer_candidate_accepted": candidate_accepted,
                "recognizer_accepted_candidate": accepted_candidate,
            }
        )
    return assigned, {
        "engine": TRIAL_ENGINE,
        "ocr_seconds": round(float(elapsed), 3),
        "candidate_count": len(regions),
        "raw_prediction_count": len(contents),
        "digit_prediction_count": digit_prediction_count,
        "accepted_digit_count": accepted_count,
        "candidate_digit_prediction_count": candidate_digit_prediction_count,
        "candidate_digit_accept_count": candidate_digit_accept_count,
        "score_threshold": score_threshold,
        "sequence_top_k": sequence_top_k,
    }


def build_page_result(
    *,
    item: dict[str, Any],
    page: int,
    output_dir: Path,
    recognizer: Any,
    render_width: int,
    score_threshold: float,
    min_ink_area: int,
    min_ink_height: int,
    sequence_top_k: int,
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
    for mode in RECOGNIZER_MODES:
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
            sequence_top_k=sequence_top_k,
        )
        ocr_regions = sorted(
            recognized_regions + skipped_regions,
            key=lambda item: int(item.get("ocr_contact_slot_index") or 0),
        )
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
            f"score_threshold={score_threshold} ocr_sec={metrics['ocr_seconds']}",
            (
                f"topk={metrics['sequence_top_k']} candidate_digits={metrics['candidate_digit_prediction_count']} "
                f"candidate_accept_plus={metrics['candidate_digit_accept_count']}"
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


def write_contact_sheet_preview(results: list[dict[str, Any]], output_dir: Path) -> str:
    canvases: list[Image.Image] = []
    for result in results:
        for mode in RECOGNIZER_MODES:
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
    parser.add_argument("--min-ink-area", type=int, default=18)
    parser.add_argument("--min-ink-height", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=14)
    parser.add_argument("--sequence-top-k", type=int, default=5)
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    items = manifest.get("results") if isinstance(manifest, dict) else manifest
    if not isinstance(items, list):
        raise ValueError("manifest results are missing")
    items = items[: max(1, args.max_pages)]
    recognizer = _load_text_recognizer(args.device)
    pages: list[Image.Image] = []
    results: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for page, item in enumerate(items, start=1):
        result, review_page = build_page_result(
            item=item,
            page=page,
            output_dir=output_dir,
            recognizer=recognizer,
            render_width=args.render_width,
            score_threshold=args.score_threshold,
            min_ink_area=args.min_ink_area,
            min_ink_height=args.min_ink_height,
            sequence_top_k=args.sequence_top_k,
        )
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
        for mode in RECOGNIZER_MODES
    }
    summary = {
        "engine": TRIAL_ENGINE,
        "count": len(results),
        "elapsed_seconds": round(float(time.perf_counter() - t0), 3),
        "score_threshold": args.score_threshold,
        "min_ink_area": args.min_ink_area,
        "min_ink_height": args.min_ink_height,
        "sequence_top_k": args.sequence_top_k,
        "baseline_document_analyzer": load_baseline_counts(args.baseline_summary),
        "totals": totals,
        "pdf": str(pdf_path),
        "contact_sheet_preview": write_contact_sheet_preview(results, output_dir),
        "results": results,
    }
    summary_path = output_dir / "text_recognizer_trial_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
