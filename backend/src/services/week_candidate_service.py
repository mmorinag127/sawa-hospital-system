from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from src.services import menu_service, sheet_week_service

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
    anchor_month = start_date.strftime("%Y-%m")
    normalized_month = _normalize_month_id(month_id) or anchor_month
    if normalized_month != anchor_month:
        normalized_month = anchor_month
    return f"{normalized_month}@{start_date.isoformat()}~{end_date.isoformat()}"


def _format_week_label(month_id: str, start_date: date, end_date: date) -> str:
    anchor_month = start_date.strftime("%Y-%m")
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
    except Exception:
        return None, None
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)
    return month_start, month_end


def _clip_range_to_month(
    month_id: str,
    start_date: date,
    end_date: date,
) -> tuple[date, date] | tuple[None, None]:
    month_start, month_end = _month_bounds(month_id)
    if not isinstance(month_start, date) or not isinstance(month_end, date):
        return None, None
    clipped_start = max(start_date, month_start)
    clipped_end = min(end_date, month_end)
    if clipped_end < clipped_start:
        return None, None
    return clipped_start, clipped_end


def _calendar_week_range_for_anchor(month_id: str, anchor_date: date) -> tuple[date, date] | tuple[None, None]:
    days_from_sunday = (anchor_date.weekday() + 1) % 7
    raw_start = anchor_date - timedelta(days=days_from_sunday)
    raw_end = raw_start + timedelta(days=6)
    return _clip_range_to_month(month_id, raw_start, raw_end)


def _menu_backed_week_ranges_for_month(month_id: str, facility_id: str | None) -> list[tuple[date, date]]:
    normalized_month = _normalize_month_id(month_id)
    if not normalized_month:
        return []
    payload = menu_service.get_menu_for_facility(normalized_month, facility_id) or {}
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    ranges: list[tuple[date, date]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_menu_date = str(entry.get("menu_date") or "").strip()
        if not raw_menu_date:
            continue
        try:
            menu_date = date.fromisoformat(raw_menu_date)
        except Exception:
            continue
        if menu_date.strftime("%Y-%m") != normalized_month:
            continue
        week_start, week_end = _calendar_week_range_for_anchor(normalized_month, menu_date)
        if not isinstance(week_start, date) or not isinstance(week_end, date):
            continue
        key = (week_start.isoformat(), week_end.isoformat())
        if key in seen:
            continue
        seen.add(key)
        ranges.append((week_start, week_end))
    ranges.sort()
    return ranges


def build_week_option_entries(month_id: str, facility_id: str | None) -> list[dict[str, Any]]:
    normalized_month = _normalize_month_id(month_id)
    if not normalized_month:
        return []
    ranges = _menu_backed_week_ranges_for_month(normalized_month, facility_id)
    return [
        {
            "week_id": _format_week_value(normalized_month, start_date, end_date),
            "label": _format_week_label(normalized_month, start_date, end_date),
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
        }
        for start_date, end_date in ranges
    ]


def _normalize_anchor_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


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
    anchor_date = _normalize_anchor_date(received_at)
    if not selected_week_value and month_id and isinstance(anchor_date, date):
        option_entries = list(build_week_option_entries(month_id, facility_id))
        option_entries.sort(key=lambda item: str(item.get("date_from") or "").strip())
        for item in option_entries:
            week_id = str(item.get("week_id") or "").strip()
            raw_start = str(item.get("date_from") or "").strip()
            raw_end = str(item.get("date_to") or "").strip()
            if not week_id or not raw_start or not raw_end:
                continue
            try:
                start_date = date.fromisoformat(raw_start)
                end_date = date.fromisoformat(raw_end)
            except Exception:
                continue
            if start_date <= anchor_date <= end_date:
                selected_week_value = week_id
                break
    resolved_week_value = selected_week_value or resolved_week_value
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
    }
