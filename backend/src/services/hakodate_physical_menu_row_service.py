from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from src.services import sheet_week_service


def resolve_fax_menu_row_indexes(sheet: dict[str, Any], row_match: dict[str, Any]) -> list[int]:
    """Map canonical menu rows into the observed Sunday-to-Saturday FAX grid."""
    row_ids = sheet.get("physical_menu_row_ids")
    count, _ = physical_row_count_from_sheet(sheet)
    if (
        not isinstance(row_ids, list)
        or any(not isinstance(value, str) for value in row_ids)
        or len(row_ids) != count
        or len(set(row_ids)) != count
    ):
        raise ValueError("physical_menu_row_ids_unresolved")
    try:
        dates = [date.fromisoformat(value.split("__", 1)[0]) for value in row_ids]
    except (AttributeError, TypeError, ValueError):
        raise ValueError("physical_menu_row_dates_unresolved") from None
    if not dates or dates != sorted(dates):
        raise ValueError("physical_menu_row_dates_unresolved")
    _, start, end = sheet_week_service.parse_sheet_week_value(sheet.get("week_id"))
    if start is None or end is None:
        raise ValueError("physical_menu_week_unresolved")
    expected_dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if list(dict.fromkeys(dates)) != expected_dates:
        raise ValueError("physical_menu_days_disagree_with_order_period")
    _, calendar_start, calendar_end = sheet_week_service.parse_sheet_week_value(
        sheet_week_service.build_calendar_week_value(start)
    )
    if calendar_start is None or calendar_end is None or end > calendar_end:
        raise ValueError("fax_calendar_week_unresolved")
    structural = row_match.get("structural_match")
    if not isinstance(structural, dict):
        raise ValueError("fax_day_boundaries_disagree_with_monthly_menu")
    boundaries = structural.get("body_day_boundary_indexes")
    day_count = (calendar_end - calendar_start).days + 1
    if (
        not isinstance(boundaries, list)
        or len(boundaries) != day_count + 1
        or any(type(value) is not int for value in boundaries)
        or boundaries[0] != 0
        or any(b <= a for a, b in zip(boundaries, boundaries[1:]))
        or boundaries[-1] != structural.get("detected_body_band_count")
    ):
        raise ValueError("fax_day_boundaries_disagree_with_monthly_menu")
    indexes: list[int] = []
    for menu_date in expected_dates:
        day_index = (menu_date - calendar_start).days
        first, last = boundaries[day_index:day_index + 2]
        if last - first != dates.count(menu_date):
            raise ValueError("fax_day_boundaries_disagree_with_monthly_menu")
        indexes.extend(range(first, last))
    return indexes


def physical_row_key_from_entry(entry: dict[str, Any], fallback_index: int) -> tuple[str, str, str] | None:
    menu_date = entry.get("menu_date")
    if isinstance(menu_date, date):
        date_value = menu_date.isoformat()
    else:
        date_value = str(menu_date or "").strip()
    daypart_value = str(entry.get("daypart_key") or entry.get("daypart") or "").strip()
    slot_raw = entry.get("slot_index")
    try:
        slot_value = str(int(slot_raw)) if slot_raw is not None else str(fallback_index)
    except Exception:
        slot_value = str(slot_raw or fallback_index).strip()
    if not date_value or not daypart_value or not slot_value:
        return None
    return date_value, daypart_value, slot_value


def physical_row_id_from_entry(entry: dict[str, Any], fallback_index: int) -> str:
    key = physical_row_key_from_entry(entry, fallback_index)
    if key is None:
        return f"physical-row-{fallback_index + 1}"
    return "__".join([key[0], key[1], key[2]])


def physical_row_keys_from_entries(entries: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    keys: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        key = physical_row_key_from_entry(entry, idx)
        if key is None or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def physical_row_ids_from_entries(entries: list[dict[str, Any]]) -> list[str]:
    row_ids: list[str] = []
    seen: set[str] = set()
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        row_id = physical_row_id_from_entry(entry, idx)
        if not row_id or row_id in seen:
            continue
        seen.add(row_id)
        row_ids.append(row_id)
    return row_ids


def physical_row_count_from_entries(entries: list[dict[str, Any]]) -> int:
    return len(physical_row_keys_from_entries(entries))


def physical_row_count_from_sheet(sheet: dict[str, Any] | None) -> tuple[int, str]:
    if not isinstance(sheet, dict):
        return 0, "sheet_missing"
    explicit_count = sheet.get("physical_menu_row_count")
    try:
        normalized_count = int(explicit_count)
    except Exception:
        normalized_count = 0
    if normalized_count > 0:
        return normalized_count, "physical_menu_row_count"
    physical_row_ids = [
        str(item or "").strip()
        for item in (sheet.get("physical_menu_row_ids") or [])
        if str(item or "").strip()
    ]
    if physical_row_ids:
        return len(set(physical_row_ids)), "physical_menu_row_ids"
    return 0, "physical_menu_rows_unresolved"
