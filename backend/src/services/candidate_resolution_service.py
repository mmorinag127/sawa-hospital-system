from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from src.db import Base, engine
from src.services import config_service, menu_service, position_column_mapping_service


Base.metadata.create_all(bind=engine)


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


def _calendar_week_ranges_for_month(month_id: str) -> list[tuple[date, date]]:
    try:
        month_start = date.fromisoformat(f"{month_id}-01")
    except Exception:
        return []
    ranges: list[tuple[date, date]] = []
    current = month_start
    while current.month == month_start.month:
        week_start = current
        week_end = min(week_start + timedelta(days=6), _month_end(month_start))
        ranges.append((week_start, week_end))
        current = week_end + timedelta(days=1)
    return ranges


def _month_end(month_start: date) -> date:
    if month_start.month == 12:
        next_month = date(month_start.year + 1, 1, 1)
    else:
        next_month = date(month_start.year, month_start.month + 1, 1)
    return next_month - timedelta(days=1)


def _format_week_value(month_id: str, start_date: date, end_date: date) -> str:
    return f"{month_id}@{start_date.isoformat()}~{end_date.isoformat()}"


def _week_label(month_id: str, start_date: date, end_date: date) -> str:
    return f"{month_id} ({start_date.strftime('%m/%d')}-{end_date.strftime('%m/%d')})"


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


def position_fallback_allowed_for_facility(
    *,
    current_facility: str | None,
    payload: dict[str, Any] | None,
) -> bool:
    current_value = str(current_facility or "").strip()
    if not current_value:
        return False
    normalized_candidates = _normalize_facility_candidates(payload)
    if not normalized_candidates:
        return True
    top_candidate = normalized_candidates[0]
    top_value = str(top_candidate.get("value") or "").strip()
    if not top_value or top_value == current_value:
        return True
    top_score = float(top_candidate.get("score") or 0.0)
    second_score = float(normalized_candidates[1].get("score") or 0.0) if len(normalized_candidates) > 1 else None
    if top_score < 0.85:
        return True
    if second_score is not None and (top_score - second_score) < 0.15:
        return True
    return False


def build_week_resolution(
    *,
    current_week: str | None,
    received_at: datetime | None,
    payload: dict[str, Any] | None,
    facility_id: str | None,
) -> dict[str, Any]:
    current_value = str(current_week or "").strip() or None
    dates = _collect_payload_dates(payload, received_at)
    candidate_months: list[str] = []
    base_month = (received_at or datetime.utcnow()).strftime("%Y-%m")
    for value in [current_value, base_month]:
        normalized = str(value or "").strip()
        if normalized and normalized not in candidate_months:
            candidate_months.append(normalized.split("@", 1)[0])
    for parsed in dates:
        month_id = parsed.strftime("%Y-%m")
        if month_id not in candidate_months:
            candidate_months.append(month_id)
    if base_month not in candidate_months:
        candidate_months.append(base_month)
    normalized_candidates: list[dict[str, Any]] = []
    for month_id in candidate_months[:6]:
        menu = menu_service.get_menu_for_facility(month_id, facility_id)
        entries = menu.get("entries") if isinstance(menu, dict) else None
        ranges: list[tuple[date, date]] = []
        if isinstance(entries, list) and entries:
            parsed_dates: set[date] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                raw_menu_date = str(entry.get("menu_date") or "").strip()
                if not raw_menu_date:
                    continue
                try:
                    parsed_dates.add(date.fromisoformat(raw_menu_date))
                except Exception:
                    continue
            unique_dates = sorted(parsed_dates)
            if unique_dates:
                current_group: list[date] = []
                previous: date | None = None
                for item in unique_dates:
                    if previous is None or (item - previous).days > 1 or len(current_group) >= 7:
                        if current_group:
                            ranges.append((min(current_group), max(current_group)))
                        current_group = [item]
                    else:
                        current_group.append(item)
                    previous = item
                if current_group:
                    ranges.append((min(current_group), max(current_group)))
        if not ranges:
            ranges = _calendar_week_ranges_for_month(month_id)
        for start_date, end_date in ranges:
            week_value = _format_week_value(month_id, start_date, end_date)
            score = 0.2
            if current_value and current_value == week_value:
                score = 1.0
            elif any(start_date <= item <= end_date for item in dates):
                score = 0.9
            elif month_id == base_month:
                score = 0.6
            normalized_candidates.append(
                {
                    "value": week_value,
                    "label": _week_label(month_id, start_date, end_date),
                    "score": score,
                    "reason": "ocr_dates" if score >= 0.9 else ("current_order_value" if score >= 1.0 else "calendar"),
                }
            )
    normalized_candidates.sort(key=lambda item: (item.get("score") or 0.0, item.get("value") or ""), reverse=True)
    normalized_candidates = _dedupe_candidates(normalized_candidates)
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
        blocked_reasons.append("week_candidates_missing")
    if requires_user_choice:
        blocked_reasons.append("week_choice_required")
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
    effective_facility_code = str(facility_code or "").strip() or None
    if (
        isinstance(evidence_payload, dict)
        and effective_facility_code
        and position_fallback_allowed_for_facility(
            current_facility=effective_facility_code,
            payload=evidence_payload,
        )
    ):
        facility_config = config_service.get_facility_config(effective_facility_code) or {}
        fax_template = facility_config.get("fax_template") if isinstance(facility_config, dict) else None
        if isinstance(fax_template, dict):
            augmented_payload = position_column_mapping_service.augment_payload_with_position_fallback(
                evidence_payload,
                fax_template,
                template_id=str(facility_config.get("fax_template_id") or "").strip() or None,
            )
    facility = build_facility_resolution(current_facility=facility_code, payload=evidence_payload)
    week = build_week_resolution(
        current_week=week_code,
        received_at=received_at,
        payload=augmented_payload,
        facility_id=facility.get("resolved_value") or facility_code,
    )
    template = build_template_resolution_snapshot(augmented_payload)
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
        "requires_user_choice": bool(critical_choices),
        "critical_choices": critical_choices,
        "attention_required": bool(
            column_mapping.get("attention_required") or quantity.get("attention_required")
        ),
        "confidence_band": _confidence_band(overall_confidence if overall_confidence > 0 else None),
    }
