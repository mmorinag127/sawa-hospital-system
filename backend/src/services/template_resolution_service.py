from __future__ import annotations

import os
from typing import Any


def _read_float_env(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def build_template_resolution(
    *,
    requested_template_id: str | None,
    requested_template_ids: list[str] | None,
    resolved_template_id: str | None,
    classification: dict[str, Any] | None,
    page_correction_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    requested = str(requested_template_id or "").strip() or None
    requested_ids = [
        token
        for token in (str(item or "").strip() for item in (requested_template_ids or []))
        if token
    ]
    if requested and requested not in requested_ids:
        requested_ids.insert(0, requested)
    resolved = str(resolved_template_id or "").strip() or None
    matched = None
    confidence = None
    candidate_ids: list[str] = []
    mismatch = False
    warp_mismatch = False
    blocked_reasons: list[str] = []

    if isinstance(classification, dict):
        matched = str(classification.get("matched_template_id") or "").strip() or None
        raw_confidence = classification.get("confidence")
        try:
            confidence = float(raw_confidence) if raw_confidence is not None else None
        except Exception:
            confidence = None
        candidates = classification.get("candidates")
        if isinstance(candidates, list):
            candidate_ids = [
                token
                for token in (
                    str((item or {}).get("id") or "").strip()
                    for item in candidates
                    if isinstance(item, dict)
                )
                if token
            ]

    correction_pages = []
    if isinstance(page_correction_summary, dict):
        correction_pages = page_correction_summary.get("pages") if isinstance(page_correction_summary.get("pages"), list) else []
    for page in correction_pages:
        if not isinstance(page, dict):
            continue
        if str(page.get("mode") or "").strip() != "template_warp":
            continue
        page_template_id = str(page.get("template_id") or "").strip() or None
        if resolved and page_template_id and page_template_id != resolved:
            warp_mismatch = True
            break

    if requested and resolved and requested != resolved:
        mismatch = True
    if requested and matched and requested != matched:
        mismatch = True
    if warp_mismatch:
        mismatch = True

    min_confidence = _read_float_env("OCR_TEMPLATE_MIN_CONFIDENCE", 0.6)
    confidence_low = confidence is not None and confidence < min_confidence

    if mismatch:
        blocked_reasons.append("template_mismatch")
    if warp_mismatch:
        blocked_reasons.append("page_correction_template_mismatch")
    if confidence_low:
        blocked_reasons.append("template_confidence_low")

    return {
        "requested_template_id": requested,
        "requested_template_ids": requested_ids,
        "matched_template_id": matched,
        "resolved_template_id": resolved,
        "confidence": confidence,
        "candidate_template_ids": candidate_ids,
        "mismatch": mismatch,
        "warp_mismatch": warp_mismatch,
        "blocked_reasons": blocked_reasons,
        "blocked": bool(blocked_reasons),
    }
