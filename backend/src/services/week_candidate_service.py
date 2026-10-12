from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any

from src.services import sheet_week_service

def _normalize_month_id(value: object) -> str | None:
    text = str(value or "").strip()
    if len(text) >= 7 and text[4:5] == "-":
        candidate = text[:7]
        year_token, month_token = candidate.split("-", 1)
        if year_token.isdigit() and month_token.isdigit():
            month = int(month_token)
            if 1 <= month <= 12:
                return candidate
    return None


def _format_week_value(month_id: str, start_date: date, end_date: date) -> str:
    anchor_month = start_date.isoformat()[:7]
    normalized_month = _normalize_month_id(month_id) or anchor_month
    if normalized_month != anchor_month:
        normalized_month = anchor_month
    return f"{normalized_month}@{start_date.isoformat()}~{end_date.isoformat()}"


def _format_week_label(month_id: str, start_date: date, end_date: date) -> str:
    anchor_month = start_date.isoformat()[:7]
    normalized_month = _normalize_month_id(month_id) or anchor_month
    if normalized_month != anchor_month:
        normalized_month = anchor_month
    return f"{normalized_month} ({start_date.strftime('%m/%d')}-{end_date.strftime('%m/%d')})"


def _month_bounds(month_id: str) -> tuple[date, date] | tuple[None, None]:
    normalized_month = _normalize_month_id(month_id)
    if not normalized_month:
        return None, None
    try:
        year = int(normalized_month[:4])
        month = int(normalized_month[5:7])
        month_start = date(year, month, 1)
        month_end = date(year, month, monthrange(year, month)[1])
    except Exception:
        return None, None
    return month_start, month_end


def build_week_option_entries(month_id: str, facility_id: str | None) -> list[dict[str, Any]]:
    normalized_month = _normalize_month_id(month_id)
    if not normalized_month or normalized_month != str(month_id).strip():
        return []
    start_date, month_end = _month_bounds(normalized_month)
    if start_date is None or month_end is None:
        return []
    # Calendar choices do not depend on menu availability and never select a week.
    options: list[dict[str, Any]] = []
    while start_date <= month_end:
        days_to_end = min((5 - start_date.weekday()) % 7, (month_end - start_date).days)
        end_date = start_date + timedelta(days=days_to_end)
        options.append({
            "week_id": _format_week_value(normalized_month, start_date, end_date),
            "label": _format_week_label(normalized_month, start_date, end_date),
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
        })
        if end_date == month_end:
            break
        start_date = end_date + timedelta(days=1)
    return options


def resolve_current_week_selection(
    week_value: object,
    *,
    received_at: date | datetime | None = None,
    facility_id: str | None = None,
) -> dict[str, Any]:
    stored_week_value = sheet_week_service.normalize_sheet_week_value(week_value) or _normalize_month_id(week_value)
    month_id = _normalize_month_id(week_value) or _normalize_month_id(stored_week_value)
    resolved_week_value = stored_week_value
    selected_week_value = (
        resolved_week_value
        if isinstance(resolved_week_value, str) and "@" in resolved_week_value
        else None
    )
    resolved_week_value = selected_week_value
    resolved_week_label = (
        sheet_week_service.format_sheet_week_label(resolved_week_value)
        if resolved_week_value
        else month_id
        or ""
    )
    return {
        "stored_week_value": stored_week_value,
        "month_id": month_id,
        "resolved_week_value": resolved_week_value,
        "resolved_week_label": resolved_week_label,
        "blocker_code": None if resolved_week_value else "week_choice_required",
    }
