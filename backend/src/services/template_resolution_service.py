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


def _normalize_grid_table_box(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 4:
        return None
    try:
        return [float(item) for item in value[:4]]
    except Exception:
        return None


def _normalize_grid_edges(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) < 2:
        return None
    try:
        return [float(item) for item in value]
    except Exception:
        return None


def _expected_grid_columns(template: dict[str, Any]) -> int:
    try:
        expected = int(template.get("grid_expected_columns") or 0)
    except Exception:
        expected = 0
    if expected >= 2:
        return expected
    grid_columns = template.get("grid_columns")
    if isinstance(grid_columns, list) and len(grid_columns) >= 2:
        return len(grid_columns)
    return 0


def _synthesize_grid_column_edges(
    table_box: list[float] | None,
    template: dict[str, Any],
) -> list[float] | None:
    if not isinstance(table_box, list) or len(table_box) < 4:
        return None
    expected = _expected_grid_columns(template)
    if expected < 2:
        return None
    left = float(table_box[0])
    right = float(table_box[2])
    span = right - left
    if span <= 0:
        return None
    return [left + span * idx / expected for idx in range(expected + 1)]


def resolve_effective_grid_metadata(
    *,
    template_resolution: dict[str, Any] | None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        table_box = _normalize_grid_table_box(payload.get("table_box"))
        column_edges = _normalize_grid_edges(payload.get("grid_column_edges"))
        row_edges = _normalize_grid_edges(payload.get("grid_row_edges"))
        if table_box and column_edges:
            return {
                "source": "payload",
                "template_id": str(
                    (
                        (template_resolution or {}).get("resolved_template_id")
                        if isinstance(template_resolution, dict)
                        else payload.get("template_id")
                    )
                    or ""
                ).strip()
                or None,
                "table_box": table_box,
                "grid_column_edges": column_edges,
                "grid_row_edges": row_edges,
            }

    candidate_ids: list[str] = []
    if isinstance(template_resolution, dict):
        for key in ("resolved_template_id", "requested_template_id", "template_id"):
            token = str(template_resolution.get(key) or "").strip()
            if token and token not in candidate_ids:
                candidate_ids.append(token)
        for raw_value in template_resolution.get("requested_template_ids") or []:
            token = str(raw_value or "").strip()
            if token and token not in candidate_ids:
                candidate_ids.append(token)
    if isinstance(payload, dict):
        token = str(payload.get("template_id") or "").strip()
        if token and token not in candidate_ids:
            candidate_ids.append(token)

    if not candidate_ids:
        return None

    return None


def normalize_template_resolution_state(
    resolution: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(resolution, dict):
        return None
    normalized = dict(resolution)
    requested = str(normalized.get("requested_template_id") or "").strip() or None
    requested_ids = [
        token
        for token in (str(item or "").strip() for item in (normalized.get("requested_template_ids") or []))
        if token
    ]
    if requested and requested not in requested_ids:
        requested_ids.insert(0, requested)
    resolved = str(normalized.get("resolved_template_id") or normalized.get("template_id") or "").strip() or None
    matched = str(normalized.get("matched_template_id") or "").strip() or None
    requested_scope = requested_ids if requested_ids else ([requested] if requested else [])
    requested_scope_mismatch = bool(requested_scope and resolved and resolved not in requested_scope)
    preferred_requested_mismatch = bool(requested and resolved and requested != resolved)
    classifier_mismatch = bool(
        matched
        and (
            (resolved and matched != resolved)
            or (not resolved and requested and matched != requested)
        )
    )
    warp_mismatch = bool(normalized.get("warp_mismatch"))
    mismatch = bool(requested_scope_mismatch or preferred_requested_mismatch or classifier_mismatch or warp_mismatch)

    raw_confidence = normalized.get("confidence")
    try:
        confidence = float(raw_confidence) if raw_confidence is not None else None
    except Exception:
        confidence = None
    min_confidence = _read_float_env("OCR_TEMPLATE_MIN_CONFIDENCE", 0.6)
    confidence_low = confidence is not None and confidence < min_confidence

    raw_blockers = [
        str(item or "").strip()
        for item in (normalized.get("blocked_reasons") or [])
        if str(item or "").strip()
    ]
    blocked_reasons: list[str] = []
    if requested_scope_mismatch or warp_mismatch:
        blocked_reasons.append("template_mismatch")
    if warp_mismatch:
        blocked_reasons.append("page_correction_template_mismatch")
    if confidence_low:
        blocked_reasons.append("template_confidence_low")
    for token in raw_blockers:
        if token not in {
            "template_mismatch",
            "page_correction_template_mismatch",
            "template_confidence_low",
        } and token not in blocked_reasons:
            blocked_reasons.append(token)

    normalized["requested_template_id"] = requested
    normalized["requested_template_ids"] = requested_ids
    normalized["resolved_template_id"] = resolved
    normalized["matched_template_id"] = matched
    normalized["classifier_mismatch"] = classifier_mismatch
    normalized["warp_mismatch"] = warp_mismatch
    normalized["mismatch"] = mismatch
    normalized["blocked_reasons"] = blocked_reasons
    normalized["blocked"] = bool(blocked_reasons)
    return normalized


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
    classifier_mismatch = False
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

    requested_scope = requested_ids if requested_ids else ([requested] if requested else [])
    requested_scope_mismatch = bool(requested_scope and resolved and resolved not in requested_scope)
    preferred_requested_mismatch = bool(requested and resolved and requested != resolved)
    classifier_mismatch = bool(
        matched
        and (
            (resolved and matched != resolved)
            or (not resolved and requested and matched != requested)
        )
    )
    if requested_scope_mismatch or preferred_requested_mismatch:
        mismatch = True
    if classifier_mismatch:
        mismatch = True
    if warp_mismatch:
        mismatch = True

    min_confidence = _read_float_env("OCR_TEMPLATE_MIN_CONFIDENCE", 0.6)
    confidence_low = confidence is not None and confidence < min_confidence

    if requested_scope_mismatch or warp_mismatch:
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
        "classifier_mismatch": classifier_mismatch,
        "warp_mismatch": warp_mismatch,
        "blocked_reasons": blocked_reasons,
        "blocked": bool(blocked_reasons),
    }
