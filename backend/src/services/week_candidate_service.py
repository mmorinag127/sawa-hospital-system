from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from src.services import menu_service


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
    return f"{month_id}@{start_date.isoformat()}~{end_date.isoformat()}"


def _format_week_label(month_id: str, start_date: date, end_date: date) -> str:
    return f"{month_id} ({start_date.strftime('%m/%d')}-{end_date.strftime('%m/%d')})"


def calendar_week_ranges_for_month(month_id: str) -> list[tuple[date, date]]:
    normalized_month = _normalize_month_id(month_id)
    if not normalized_month:
        return []
    try:
        year = int(normalized_month[:4])
        month = int(normalized_month[5:7])
        month_start = date(year, month, 1)
    except Exception:
        return []
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)

    # Order detail Step1 already uses Sunday-Saturday week buckets.
    first_week_start = month_start - timedelta(days=(month_start.weekday() + 1) % 7)
    ranges: list[tuple[date, date]] = []
    cursor = first_week_start
    while cursor <= month_end:
        week_start = max(cursor, month_start)
        week_end = min(cursor + timedelta(days=6), month_end)
        if week_end >= week_start:
            ranges.append((week_start, week_end))
        cursor += timedelta(days=7)
    return ranges


def _group_menu_dates(entries: list[dict[str, Any]]) -> list[tuple[date, date]]:
    menu_dates: list[date] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_date = entry.get("menu_date")
        if not isinstance(raw_date, str) or not raw_date.strip():
            continue
        try:
            menu_dates.append(date.fromisoformat(raw_date.strip()))
        except Exception:
            continue
    unique_dates = sorted(set(menu_dates))
    if not unique_dates:
        return []

    grouped_dates: list[list[date]] = []
    current_group: list[date] = []
    previous_date: date | None = None
    for menu_date in unique_dates:
        if (
            previous_date is None
            or (menu_date - previous_date).days > 1
            or len(current_group) >= 7
        ):
            if current_group:
                grouped_dates.append(current_group)
            current_group = [menu_date]
        else:
            current_group.append(menu_date)
        previous_date = menu_date
    if current_group:
        grouped_dates.append(current_group)
    return [
        (min(date_group), max(date_group))
        for date_group in grouped_dates
        if date_group
    ]


def build_week_option_entries(month_id: str, facility_id: str | None) -> list[dict[str, Any]]:
    normalized_month = _normalize_month_id(month_id)
    if not normalized_month:
        return []
    menu = menu_service.get_menu_for_facility(normalized_month, facility_id)
    entries = menu.get("entries") if isinstance(menu, dict) else None
    ranges = _group_menu_dates(entries) if isinstance(entries, list) else []
    if not ranges:
        ranges = calendar_week_ranges_for_month(normalized_month)
    return [
        {
            "week_id": _format_week_value(normalized_month, start_date, end_date),
            "label": _format_week_label(normalized_month, start_date, end_date),
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
        }
        for start_date, end_date in ranges
    ]
