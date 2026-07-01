from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from src.services import (
    config_service,
    position_column_mapping_service,
    sheet_week_service,
    week_candidate_service,
)

_DATE_RE = re.compile(r"(?:(20\d{2})[/-])?(\d{1,2})[/-](\d{1,2})")


def _dedupe_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in items:
        value = str(item.get("value") or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(item)
    return normalized


def _normalize_facility_candidates(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw_candidates = payload.get("facility_candidates") if isinstance(payload, dict) else None
    normalized_candidates: list[dict[str, Any]] = []
    if not isinstance(raw_candidates, list):
        return normalized_candidates
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        facility_id = str(item.get("facility_id") or "").strip()
        if not facility_id:
            continue
        normalized_candidates.append(
            {
                "value": facility_id,
                "label": str(item.get("facility_name") or facility_id).strip() or facility_id,
                "score": float(item.get("score") or 0.0),
                "reason": str(item.get("reason") or "").strip() or None,
                "auto": bool(item.get("auto")),
            }
        )
    normalized_candidates.sort(key=lambda item: (item.get("score") or 0.0, item.get("value") or ""), reverse=True)
    return _dedupe_candidates(normalized_candidates)


def _confidence_band(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.9:
        return "high"
    if score >= 0.7:
        return "medium"
    return "low"


def _confidence_value(label: str) -> float:
    normalized = str(label or "").strip().lower()
    if normalized == "high":
        return 0.95
    if normalized == "medium":
        return 0.75
    if normalized == "low":
        return 0.45
    return 0.0


def _resolution_resolved_value(resolution: dict[str, Any] | None) -> str:
    if not isinstance(resolution, dict):
        return ""
    return str(
        resolution.get("resolved_value")
        or resolution.get("resolved_column_mapping_id")
        or resolution.get("resolved_template_id")
        or resolution.get("resolved_quantity_choice_id")
        or ""
    ).strip()


def get_resolution_gate_state(resolution: dict[str, Any] | None) -> dict[str, Any]:
    decision_type = str((resolution or {}).get("decision_type") or "").strip() if isinstance(resolution, dict) else ""
    resolved_value = _resolution_resolved_value(resolution)
    blocked_reasons = [
        str(item).strip()
        for item in ((resolution or {}).get("blocked_reasons") or [])
        if str(item).strip()
    ] if isinstance(resolution, dict) else []
    requires_user_choice = bool((resolution or {}).get("requires_user_choice")) if isinstance(resolution, dict) else False
    blocked_without_resolution = bool((resolution or {}).get("blocked")) and not resolved_value if isinstance(resolution, dict) else False
    blocked = bool(blocked_without_resolution or (blocked_reasons and not resolved_value and not requires_user_choice))
    if requires_user_choice:
        status = "choice_required"
    elif resolved_value:
        status = "resolved"
    elif blocked:
        status = "blocked"
    else:
        status = "missing"
    return {
        "decision_type": decision_type or None,
        "resolved_value": resolved_value or None,
        "blocked_reasons": blocked_reasons,
        "requires_user_choice": requires_user_choice,
        "blocked": blocked,
        "status": status,
    }


def summarize_resolution_gate(
    resolutions: dict[str, Any] | None,
    *,
    suppress_decision_types: set[str] | None = None,
) -> dict[str, Any]:
    suppressed = {str(item).strip() for item in (suppress_decision_types or set()) if str(item).strip()}
    details: list[dict[str, Any]] = []
    choice_required_types: list[str] = []
    blocked_types: list[str] = []
    unresolved_types: list[str] = []
    for decision_type, resolution in (resolutions or {}).items():
        if not isinstance(resolution, dict):
            continue
        state = get_resolution_gate_state(resolution)
        normalized_type = str(decision_type or state.get("decision_type") or "").strip()
        if not normalized_type:
            continue
        is_suppressed = normalized_type in suppressed
        detail = dict(state)
        detail["decision_type"] = normalized_type
        detail["suppressed"] = is_suppressed
        details.append(detail)
        if is_suppressed:
            continue
        if state["status"] == "choice_required":
            choice_required_types.append(normalized_type)
            unresolved_types.append(normalized_type)
        elif state["status"] == "blocked":
            blocked_types.append(normalized_type)
            unresolved_types.append(normalized_type)
    return {
        "details": details,
        "choice_required_types": choice_required_types,
        "blocked_types": blocked_types,
        "unresolved_types": unresolved_types,
    }


def _candidate_score(candidate: dict[str, Any]) -> float | None:
    raw = candidate.get("score")
    if isinstance(raw, (int, float)):
        return float(raw)
    raw_confidence = candidate.get("confidence")
    if isinstance(raw_confidence, (int, float)):
        return float(raw_confidence)
    return None


def _extract_payload_text(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    texts: list[str] = []
    table_raw = payload.get("table_raw")
    if isinstance(table_raw, str) and table_raw.strip():
        texts.append(table_raw)
    for page in payload.get("pages") or []:
        if not isinstance(page, dict):
            continue
        markdown = page.get("markdown_text") or page.get("markdown")
        if isinstance(markdown, str) and markdown.strip():
            texts.append(markdown)
    for row in payload.get("rows") or []:
        if isinstance(row, list):
            texts.append(" ".join(str(cell or "").strip() for cell in row if str(cell or "").strip()))
    for row in payload.get("table_rows") or []:
        if isinstance(row, list):
            texts.append(" ".join(str(cell or "").strip() for cell in row if str(cell or "").strip()))
    for table in payload.get("tables") or []:
        if not isinstance(table, dict):
            continue
        for row in table.get("rows") or []:
            if isinstance(row, list):
                texts.append(" ".join(str(cell or "").strip() for cell in row if str(cell or "").strip()))
    return "\n".join(texts)


def _collect_payload_dates(payload: dict[str, Any] | None, received_at: datetime | None) -> list[date]:
    text = _extract_payload_text(payload)
    if not text:
        return []
    base_year = received_at.year if isinstance(received_at, datetime) else datetime.utcnow().year
    dates: list[date] = []
    for match in _DATE_RE.finditer(text):
        year_token, month_token, day_token = match.groups()
        try:
            year = int(year_token) if year_token else base_year
            month = int(month_token)
            day = int(day_token)
            parsed = date(year, month, day)
        except Exception:
            continue
        if parsed not in dates:
            dates.append(parsed)
    return dates


def build_facility_resolution(
    *,
    current_facility: str | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_candidates = _normalize_facility_candidates(payload)
    current_value = str(current_facility or "").strip() or None
    if current_value and not any(item["value"] == current_value for item in normalized_candidates):
        normalized_candidates.insert(
            0,
            {
                "value": current_value,
                "label": current_value,
                "score": 1.0,
                "reason": "current_order_value",
                "auto": False,
            },
        )
    top_score = normalized_candidates[0]["score"] if normalized_candidates else None
    second_score = normalized_candidates[1]["score"] if len(normalized_candidates) > 1 else None
    requires_user_choice = (
        len(normalized_candidates) >= 2
        and (top_score is None or second_score is None or (top_score - second_score) < 0.15 or top_score < 0.85)
        and not current_value
    )
    resolved_value = current_value or (normalized_candidates[0]["value"] if len(normalized_candidates) == 1 else None)
    blocked_reasons: list[str] = []
    if not resolved_value and not normalized_candidates:
        blocked_reasons.append("facility_candidates_missing")
    if requires_user_choice:
        blocked_reasons.append("facility_choice_required")
    return {
        "decision_type": "facility",
        "resolved_value": resolved_value,
        "resolved_label": next((item["label"] for item in normalized_candidates if item["value"] == resolved_value), resolved_value),
        "confidence": _confidence_band(top_score if resolved_value else None),
        "blocked": bool(blocked_reasons and not resolved_value),
        "blocked_reasons": blocked_reasons,
        "requires_user_choice": requires_user_choice,
        "candidates": normalized_candidates,
    }


def build_week_resolution(
    *,
    current_week: str | None,
    received_at: datetime | None,
    payload: dict[str, Any] | None,
    facility_id: str | None,
) -> dict[str, Any]:
    raw_current_value = str(current_week or "").strip() or None
    current_value = raw_current_value if raw_current_value and "@" in raw_current_value else None
    anchor_month = (raw_current_value.split("@", 1)[0] if raw_current_value else "") or (
        received_at or datetime.utcnow()
    ).strftime("%Y-%m")
    payload_dates = _collect_payload_dates(payload, received_at)
    candidate_months: list[str] = []
    for delta in (-2, -1, 0, 1, 2):
        shifted = sheet_week_service.shift_sheet_month_id(anchor_month, delta)
        if shifted and shifted not in candidate_months:
            candidate_months.append(shifted)
    normalized_candidates: list[dict[str, Any]] = []
    matched_candidates: list[str] = []
    for month_id in candidate_months:
        for week_option in week_candidate_service.build_week_option_entries(month_id, facility_id):
            week_value = str(week_option.get("week_id") or "").strip()
            raw_start_date = str(week_option.get("date_from") or "").strip()
            raw_end_date = str(week_option.get("date_to") or "").strip()
            if not week_value or not raw_start_date or not raw_end_date:
                continue
            try:
                start_date = date.fromisoformat(raw_start_date)
                end_date = date.fromisoformat(raw_end_date)
            except Exception:
                continue
            matched_dates = [
                item
                for item in payload_dates
                if start_date <= item <= end_date
            ]
            matched_ratio = (len(matched_dates) / len(payload_dates)) if payload_dates else 0.0
            score = 0.2
            reason = "menu_week"
            if current_value and current_value == week_value:
                score = 1.0
                reason = "current_order_value"
            elif matched_dates:
                score = 0.9 + min(matched_ratio, 1.0) * 0.09
                reason = "ocr_dates"
                matched_candidates.append(week_value)
            elif isinstance(received_at, datetime) and start_date <= received_at.date() <= end_date:
                score = 0.75
                reason = "received_at"
            elif month_id == anchor_month:
                score = 0.6
                reason = "anchor_month"
            normalized_candidates.append(
                {
                    "value": week_value,
                    "label": str(week_option.get("label") or week_value),
                    "score": score,
                    "reason": reason,
                }
            )
    normalized_candidates.sort(key=lambda item: (item.get("score") or 0.0, item.get("value") or ""), reverse=True)
    normalized_candidates = _dedupe_candidates(normalized_candidates)
    top_score = normalized_candidates[0]["score"] if normalized_candidates else None
    deduped_matched_candidates: list[str] = []
    for value in matched_candidates:
        if value not in deduped_matched_candidates:
            deduped_matched_candidates.append(value)
    requires_user_choice = False
    month_only_current = bool(raw_current_value and not current_value)
    resolved_value = current_value
    if (
        not resolved_value
        and len(deduped_matched_candidates) == 1
        and not (month_only_current and len(normalized_candidates) >= 2)
    ):
        resolved_value = deduped_matched_candidates[0]
    if not resolved_value and len(deduped_matched_candidates) >= 2:
        requires_user_choice = True
    elif not resolved_value and month_only_current:
        # A month-only order week is only ambiguous when OCR payload dates actually
        # point at a specific weekly slice. Pure calendar fallback candidates are not
        # authoritative enough to force a user choice ahead of downstream blockers.
        requires_user_choice = bool(payload_dates)
    elif not resolved_value and not payload_dates and len(normalized_candidates) >= 2:
        requires_user_choice = True
    elif not resolved_value and len(normalized_candidates) >= 2 and not deduped_matched_candidates:
        requires_user_choice = True
    blocked_reasons: list[str] = []
    if not resolved_value and requires_user_choice:
        blocked_reasons.append("week_choice_required")
    elif not resolved_value and not normalized_candidates:
        blocked_reasons.append("week_candidates_missing")
    return {
        "decision_type": "week",
        "resolved_value": resolved_value,
        "resolved_label": next((item["label"] for item in normalized_candidates if item["value"] == resolved_value), resolved_value),
        "confidence": _confidence_band(top_score if resolved_value else None),
        "blocked": bool(blocked_reasons and not resolved_value),
        "blocked_reasons": blocked_reasons,
        "requires_user_choice": requires_user_choice,
        "candidates": normalized_candidates,
    }


def build_template_resolution_snapshot(payload: dict[str, Any] | None) -> dict[str, Any]:
    resolution = payload.get("template_resolution") if isinstance(payload, dict) else None
    if not isinstance(resolution, dict):
        return {
            "decision_type": "template",
            "resolved_value": None,
            "resolved_label": None,
            "confidence": "unknown",
            "blocked": True,
            "blocked_reasons": ["template_resolution_missing"],
            "requires_user_choice": False,
            "candidates": [],
        }
    raw_candidates = resolution.get("candidates") or []
    candidate_ids = resolution.get("candidate_template_ids") or []
    normalized_candidates: list[dict[str, Any]] = []
    if isinstance(raw_candidates, list) and raw_candidates:
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            template_id = str(item.get("id") or item.get("template_id") or "").strip()
            if not template_id:
                continue
            normalized_candidates.append(
                {
                    "value": template_id,
                    "label": template_id,
                    "score": float(item.get("score") or 0.0),
                    "reason": str(item.get("reason") or "").strip() or None,
                }
            )
    elif isinstance(candidate_ids, list):
        for index, item in enumerate(candidate_ids):
            template_id = str(item or "").strip()
            if not template_id:
                continue
            normalized_candidates.append(
                {
                    "value": template_id,
                    "label": template_id,
                    "score": max(0.0, 1.0 - (index * 0.15)),
                    "reason": "template_candidate",
                }
            )
    normalized_candidates = _dedupe_candidates(normalized_candidates)
    resolved_value = str(resolution.get("resolved_template_id") or "").strip() or None
    confidence_value = resolution.get("confidence")
    score = float(confidence_value) if isinstance(confidence_value, (int, float)) else None
    blocked_reasons = [
        str(item).strip()
        for item in (resolution.get("blocked_reasons") or [])
        if str(item).strip()
    ]
    requires_user_choice = bool(
        not resolved_value
        and len(normalized_candidates) >= 2
        and (score is None or score < 0.85)
    )
    if requires_user_choice and "template_choice_required" not in blocked_reasons:
        blocked_reasons.append("template_choice_required")
    return {
        "decision_type": "template",
        "resolved_value": resolved_value,
        "resolved_label": resolved_value,
        "confidence": _confidence_band(score),
        "blocked": bool(resolution.get("blocked")) or bool(blocked_reasons and not resolved_value),
        "blocked_reasons": blocked_reasons,
        "requires_user_choice": requires_user_choice,
        "candidates": normalized_candidates,
    }


def _collapse_equivalent_template_resolution(
    *,
    facility_id: str | None,
    resolution: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(resolution, dict):
        return resolution
    normalized_facility_id = str(facility_id or "").strip() or None
    if not normalized_facility_id:
        return resolution
    facility_config = config_service.get_facility_config(normalized_facility_id)
    if not isinstance(facility_config, dict):
        return resolution
    configured_template_ids = facility_config.get("fax_template_ids") or facility_config.get("fax_template_id")
    collapsed_template_ids = config_service.collapse_equivalent_template_ids(
        normalized_facility_id,
        configured_template_ids,
    )
    if len(collapsed_template_ids) != 1:
        return resolution
    resolved_value = str(resolution.get("resolved_value") or "").strip()
    if resolved_value:
        return resolution
    candidates = resolution.get("candidates") if isinstance(resolution.get("candidates"), list) else []
    candidate_values = [
        str(item.get("value") or "").strip()
        for item in candidates
        if isinstance(item, dict) and str(item.get("value") or "").strip()
    ]
    configured_values = {
        str(item).strip()
        for item in (
            facility_config.get("fax_template_ids")
            if isinstance(facility_config.get("fax_template_ids"), list)
            else [facility_config.get("fax_template_id")]
        )
        if str(item).strip()
    }
    if candidate_values and any(value not in configured_values for value in candidate_values):
        return resolution
    canonical_template_id = collapsed_template_ids[0]
    return {
        **resolution,
        "resolved_value": canonical_template_id,
        "resolved_label": canonical_template_id,
        "confidence": resolution.get("confidence") or "high",
        "blocked": False,
        "blocked_reasons": [],
        "requires_user_choice": False,
        "candidates": [{"value": canonical_template_id, "label": canonical_template_id, "score": 1.0, "reason": "effective_template_equivalent"}],
    }


def _collect_issue_codes(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    codes: list[str] = []
    for issue in payload.get("cell_issues") or []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("issue_code") or "").strip()
        if code and code not in codes:
            codes.append(code)
    return codes


def _normalize_generic_candidates(raw_candidates: object) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(raw_candidates, list):
        return normalized
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or item.get("id") or "").strip()
        if not value:
            continue
        score = _candidate_score(item)
        normalized.append(
            {
                "candidate_id": str(item.get("candidate_id") or value).strip() or value,
                "candidate_type": str(item.get("candidate_type") or "").strip() or None,
                "value": value,
                "label": str(item.get("label") or value).strip() or value,
                "score": score,
                "confidence": _confidence_band(score),
                "reason": str(item.get("reason") or "").strip() or None,
                "evidence_ref": item.get("evidence_ref") if isinstance(item.get("evidence_ref"), dict) else None,
                "decision_source": str(item.get("decision_source") or "").strip() or None,
                "auto_selectable": bool(item.get("auto_selectable")) if item.get("auto_selectable") is not None else None,
                "requires_user_choice": bool(item.get("requires_user_choice")) if item.get("requires_user_choice") is not None else None,
                "critical": bool(item.get("critical") or item.get("high_impact")),
                "partial_quantity_mapping": bool(item.get("partial_quantity_mapping")) if item.get("partial_quantity_mapping") is not None else None,
                "mapped_quantity_fields": [
                    str(field).strip()
                    for field in (item.get("mapped_quantity_fields") or [])
                    if str(field).strip()
                ]
                if isinstance(item.get("mapped_quantity_fields"), list)
                else None,
                "expected_quantity_fields": [
                    str(field).strip()
                    for field in (item.get("expected_quantity_fields") or [])
                    if str(field).strip()
                ]
                if isinstance(item.get("expected_quantity_fields"), list)
                else None,
            }
        )
    return _dedupe_candidates(normalized)


def _top_two_scores(candidates: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    if not candidates:
        return None, None
    top_score = _candidate_score(candidates[0])
    second_score = _candidate_score(candidates[1]) if len(candidates) > 1 else None
    return top_score, second_score


def _should_require_choice(
    *,
    resolved_value: str | None,
    candidates: list[dict[str, Any]],
    score: float | None,
    explicit: bool = False,
) -> bool:
    if resolved_value or len(candidates) < 2:
        return False
    top_score, second_score = _top_two_scores(candidates)
    if explicit:
        return True
    if top_score is None or second_score is None:
        return score is None or score < 0.85
    return (top_score - second_score) < 0.15 or top_score < 0.85


def _build_critical_choice_payload(
    decision_type: str,
    resolution: dict[str, Any],
    *,
    default_title: str,
) -> dict[str, Any]:
    return {
        "decision_type": decision_type,
        "title": default_title,
        "candidates": list(resolution.get("candidates") or []),
        "blocked_reasons": list(resolution.get("blocked_reasons") or []),
        "decision_source": resolution.get("decision_source"),
        "ambiguity_scope": resolution.get("ambiguity_scope"),
        "evidence_ref": resolution.get("evidence_ref"),
    }


def _first_candidate_metadata(candidates: list[dict[str, Any]], key: str) -> Any:
    for item in candidates:
        if not isinstance(item, dict):
            continue
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict) and value:
            return value
    return None


def _first_candidate_bool_metadata(candidates: list[dict[str, Any]], key: str) -> bool | None:
    for item in candidates:
        if not isinstance(item, dict):
            continue
        value = item.get(key)
        if isinstance(value, bool):
            return value
    return None


def _first_candidate_list_metadata(candidates: list[dict[str, Any]], key: str) -> list[str] | None:
    for item in candidates:
        if not isinstance(item, dict):
            continue
        value = item.get(key)
        if not isinstance(value, list):
            continue
        normalized = [str(field).strip() for field in value if str(field).strip()]
        if normalized:
            return normalized
    return None


def build_column_mapping_resolution(payload: dict[str, Any] | None) -> dict[str, Any]:
    issue_codes = _collect_issue_codes(payload)
    attention_reasons = [
        code
        for code in issue_codes
        if code in {"column_swap", "mirrored_sibling_columns"}
    ]
    resolution = payload.get("column_mapping_resolution") if isinstance(payload, dict) else None
    resolution = resolution if isinstance(resolution, dict) else {}
    candidates_raw = resolution.get("candidates") if resolution else None
    if not isinstance(candidates_raw, list) and isinstance(payload, dict):
        candidates_raw = payload.get("column_mapping_candidates")
    candidates = _normalize_generic_candidates(candidates_raw)
    resolved_value = str(
        resolution.get("resolved_value")
        or resolution.get("resolved_column_mapping_id")
        or ""
    ).strip() or None
    confidence_value = resolution.get("confidence")
    score = float(confidence_value) if isinstance(confidence_value, (int, float)) else None
    blocked_reasons = [
        str(item).strip()
        for item in (resolution.get("blocked_reasons") or [])
        if str(item).strip()
    ]
    explicit_requires_user_choice = bool(resolution.get("requires_user_choice")) or (
        "column_mapping_choice_required" in blocked_reasons
    )
    decision_source = str(
        resolution.get("decision_source")
        or _first_candidate_metadata(candidates, "decision_source")
        or "ocr_evidence"
    ).strip() or "ocr_evidence"
    if (
        decision_source == "position_fallback"
        and len(candidates) >= 2
        and _should_require_choice(
            resolved_value=None,
            candidates=candidates,
            score=score,
            explicit=explicit_requires_user_choice,
        )
    ):
        explicit_requires_user_choice = True
        resolved_value = None
    requires_user_choice = _should_require_choice(
        resolved_value=resolved_value,
        candidates=candidates,
        score=score,
        explicit=explicit_requires_user_choice,
    )
    if requires_user_choice and "column_mapping_choice_required" not in blocked_reasons:
        blocked_reasons.append("column_mapping_choice_required")
    resolved_label = next(
        (item.get("label") for item in candidates if item.get("value") == resolved_value),
        resolved_value,
    )
    partial_quantity_mapping = bool(resolution.get("partial_quantity_mapping"))
    if not partial_quantity_mapping:
        partial_quantity_mapping = bool(_first_candidate_bool_metadata(candidates, "partial_quantity_mapping"))
    mapped_quantity_fields = (
        [
            str(field).strip()
            for field in (resolution.get("mapped_quantity_fields") or [])
            if str(field).strip()
        ]
        if isinstance(resolution.get("mapped_quantity_fields"), list)
        else None
    ) or _first_candidate_list_metadata(candidates, "mapped_quantity_fields") or []
    expected_quantity_fields = (
        [
            str(field).strip()
            for field in (resolution.get("expected_quantity_fields") or [])
            if str(field).strip()
        ]
        if isinstance(resolution.get("expected_quantity_fields"), list)
        else None
    ) or _first_candidate_list_metadata(candidates, "expected_quantity_fields") or []
    return {
        "decision_type": "column_mapping",
        "resolved_value": resolved_value,
        "resolved_label": resolved_label,
        "confidence": _confidence_band(score) if score is not None else ("low" if attention_reasons else "unknown"),
        "blocked": bool(resolution.get("blocked")) or bool(blocked_reasons and not resolved_value),
        "blocked_reasons": blocked_reasons,
        "requires_user_choice": requires_user_choice,
        "candidates": candidates,
        "attention_required": bool(attention_reasons),
        "attention_reasons": attention_reasons,
        "decision_source": decision_source,
        "ambiguity_scope": "column_mapping" if requires_user_choice else None,
        "partial_quantity_mapping": partial_quantity_mapping,
        "mapped_quantity_fields": mapped_quantity_fields,
        "expected_quantity_fields": expected_quantity_fields,
        "evidence_ref": (
            resolution.get("evidence_ref")
            if isinstance(resolution.get("evidence_ref"), dict)
            else _first_candidate_metadata(candidates, "evidence_ref")
        ),
    }


def build_quantity_resolution(payload: dict[str, Any] | None) -> dict[str, Any]:
    issue_codes = _collect_issue_codes(payload)
    attention_reasons = [
        code
        for code in issue_codes
        if code
        in {
            "merged_numeric_cell",
            "overextended_span",
            "invalid_numeric_spike",
            "all_quantity_blank",
            "unexpected_dense_fill",
            "missing_blank_anchor_rows",
        }
    ]
    failed_cells = payload.get("failed_cells") if isinstance(payload, dict) else None
    failed_cell_count = len(failed_cells) if isinstance(failed_cells, list) else 0
    if failed_cell_count > 0 and "failed_cells_present" not in attention_reasons:
        attention_reasons.append("failed_cells_present")
    resolution = payload.get("quantity_resolution") if isinstance(payload, dict) else None
    resolution = resolution if isinstance(resolution, dict) else {}
    critical_candidates_raw = (
        resolution.get("critical_candidates")
        if resolution
        else (payload.get("critical_quantity_candidates") if isinstance(payload, dict) else None)
    )
    quantity_candidates_raw = (
        resolution.get("candidates")
        if resolution
        else (payload.get("quantity_candidates") if isinstance(payload, dict) else None)
    )
    candidates = _normalize_generic_candidates(critical_candidates_raw)
    used_critical_candidates = bool(candidates)
    if not candidates:
        candidates = _normalize_generic_candidates(quantity_candidates_raw)
    resolved_value = str(
        resolution.get("resolved_value")
        or resolution.get("resolved_quantity_choice_id")
        or ""
    ).strip() or None
    confidence_value = resolution.get("confidence")
    score = float(confidence_value) if isinstance(confidence_value, (int, float)) else None
    blocked_reasons = [
        str(item).strip()
        for item in (resolution.get("blocked_reasons") or [])
        if str(item).strip()
    ]
    explicit_choice = used_critical_candidates or any(bool(item.get("critical")) for item in candidates)
    requires_user_choice = _should_require_choice(
        resolved_value=resolved_value,
        candidates=candidates,
        score=score,
        explicit=explicit_choice,
    )
    if requires_user_choice and "quantity_choice_required" not in blocked_reasons:
        blocked_reasons.append("quantity_choice_required")
    resolved_label = next(
        (item.get("label") for item in candidates if item.get("value") == resolved_value),
        resolved_value,
    )
    return {
        "decision_type": "quantity",
        "resolved_value": resolved_value,
        "resolved_label": resolved_label,
        "confidence": _confidence_band(score) if score is not None else ("low" if attention_reasons else "unknown"),
        "blocked": bool(resolution.get("blocked")) or bool(blocked_reasons and not resolved_value),
        "blocked_reasons": blocked_reasons,
        "requires_user_choice": requires_user_choice,
        "candidates": candidates,
        "attention_required": bool(attention_reasons),
        "attention_reasons": attention_reasons,
        "failed_cell_count": failed_cell_count,
        "decision_source": str(
            resolution.get("decision_source")
            or _first_candidate_metadata(candidates, "decision_source")
            or ("critical_quantity_candidates" if used_critical_candidates else "ocr_evidence")
        ).strip()
        or ("critical_quantity_candidates" if used_critical_candidates else "ocr_evidence"),
        "ambiguity_scope": "high_impact_quantity" if requires_user_choice else None,
        "evidence_ref": (
            resolution.get("evidence_ref")
            if isinstance(resolution.get("evidence_ref"), dict)
            else _first_candidate_metadata(candidates, "evidence_ref")
        ),
    }


def resolve_order_candidates(
    *,
    order_id: str,
    facility_code: str | None,
    week_code: str | None,
    received_at: datetime | None,
    evidence_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    augmented_payload = evidence_payload
    facility = build_facility_resolution(current_facility=facility_code, payload=evidence_payload)
    week = build_week_resolution(
        current_week=week_code,
        received_at=received_at,
        payload=augmented_payload,
        facility_id=facility.get("resolved_value") or facility_code,
    )
    template = _collapse_equivalent_template_resolution(
        facility_id=facility.get("resolved_value") or facility_code,
        resolution=build_template_resolution_snapshot(augmented_payload),
    )
    column_mapping = build_column_mapping_resolution(augmented_payload)
    quantity = build_quantity_resolution(augmented_payload)
    resolutions = {
        "facility": facility,
        "week": week,
        "template": template,
        "column_mapping": column_mapping,
        "quantity": quantity,
    }
    critical_choices = [
        _build_critical_choice_payload(
            key,
            value,
            default_title={
                "facility": "施設候補を選択",
                "week": "対象週を選択",
                "template": "票面テンプレートを選択",
                "column_mapping": "列の並び候補を選択",
                "quantity": "重要な数量候補を選択",
            }.get(key, key),
        )
        for key, value in resolutions.items()
        if bool(value.get("requires_user_choice"))
    ]
    gate_summary = summarize_resolution_gate(resolutions)
    overall_confidence_candidates = [
        _confidence_value(resolution.get("confidence"))
        for resolution in resolutions.values()
        if (
            resolution.get("resolved_value")
            or resolution.get("requires_user_choice")
            or resolution.get("attention_required")
            or resolution.get("blocked_reasons")
        )
        and _confidence_value(resolution.get("confidence")) > 0
    ]
    overall_confidence = min(overall_confidence_candidates, default=0.0)
    return {
        "order_id": order_id,
        "resolutions": resolutions,
        "gate_summary": gate_summary,
        "requires_user_choice": bool(critical_choices),
        "critical_choices": critical_choices,
        "attention_required": bool(
            column_mapping.get("attention_required") or quantity.get("attention_required")
        ),
        "confidence_band": _confidence_band(overall_confidence if overall_confidence > 0 else None),
    }


def _compact_resolution(resolution: dict[str, Any] | None, *, max_candidates: int = 3) -> dict[str, Any] | None:
    if not isinstance(resolution, dict):
        return None
    candidates = resolution.get("candidates")
    normalized_candidates = (
        [item for item in candidates if isinstance(item, dict)][:max_candidates]
        if isinstance(candidates, list)
        else []
    )
    return {
        "decision_type": resolution.get("decision_type"),
        "resolved_value": resolution.get("resolved_value"),
        "resolved_label": resolution.get("resolved_label"),
        "confidence": resolution.get("confidence"),
        "gate_state": get_resolution_gate_state(resolution),
        "blocked": bool(resolution.get("blocked")),
        "blocked_reasons": [str(item).strip() for item in (resolution.get("blocked_reasons") or []) if str(item).strip()],
        "requires_user_choice": bool(resolution.get("requires_user_choice")),
        "candidates": normalized_candidates,
    }


def resolve_order_list_candidates(
    *,
    facility_code: str | None,
    week_code: str | None,
    received_at: datetime | None,
    evidence_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    current_facility = str(facility_code or "").strip() or None
    current_week = str(week_code or "").strip() or None
    facility = build_facility_resolution(current_facility=current_facility, payload=evidence_payload)
    # Treat bare month codes as unresolved for list grouping so OCR-derived
    # week ranges can surface without forcing a full workflow refresh.
    explicit_current_week = current_week if current_week and "@" in current_week else None
    week = build_week_resolution(
        current_week=explicit_current_week,
        received_at=received_at,
        payload=evidence_payload,
        facility_id=facility.get("resolved_value") or current_facility,
    )
    compact_facility = _compact_resolution(facility, max_candidates=2)
    compact_week = _compact_resolution(week, max_candidates=3)
    gate_summary = summarize_resolution_gate({"facility": facility, "week": week})
    overall_confidence_candidates = [
        _confidence_value((compact_facility or {}).get("confidence")),
        _confidence_value((compact_week or {}).get("confidence")),
    ]
    overall_confidence = min([item for item in overall_confidence_candidates if item > 0], default=0.0)
    critical_choices = [
        _build_critical_choice_payload(
            "facility",
            facility,
            default_title="施設候補を選択",
        )
        if bool((compact_facility or {}).get("requires_user_choice"))
        else None,
        _build_critical_choice_payload(
            "week",
            week,
            default_title="対象週を選択",
        )
        if bool((compact_week or {}).get("requires_user_choice"))
        else None,
    ]
    return {
        "resolutions": {
            "facility": compact_facility,
            "week": compact_week,
        },
        "gate_summary": gate_summary,
        "requires_user_choice": any(item is not None for item in critical_choices),
        "critical_choices": [item for item in critical_choices if isinstance(item, dict)],
        "confidence_band": _confidence_band(overall_confidence if overall_confidence > 0 else None),
    }
