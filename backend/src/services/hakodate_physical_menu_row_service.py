from __future__ import annotations

from datetime import date
from typing import Any


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
