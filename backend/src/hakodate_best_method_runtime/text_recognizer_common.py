from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from PIL import Image, ImageDraw

from src.services.hakodate_cell_ocr_batch_service import _draw_text_safe, _load_overlay_font

FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")


def _clean_text_for_number(value: object) -> str:
    text = str(value or "").strip().translate(FULLWIDTH_DIGIT_TRANS)
    text = text.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1").replace("|", "1")
    return re.sub(r"[^0-9]", "", text)


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
