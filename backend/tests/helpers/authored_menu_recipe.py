"""Explicit synthetic recipe declarations, separate from OCR/order quantities."""
from uuid import uuid4
from datetime import date

from src.models.menu import MonthlyMenuEntry, MonthlyMenuItem
from src.services import menu_service


def declare_recipes(session, entries, *, qty_per_serving, unit_type):
    assert qty_per_serving > 0 and unit_type in {"g", "個"}
    for entry in entries:
        identity = menu_service._monthly_item_identity_key_from_entry(entry)
        assert identity[0] and identity[1] and identity[3], "recipe requires authored name, daypart and diet"
        existing = session.query(MonthlyMenuItem).filter_by(monthly_menu_id=entry.monthly_menu_id).all()
        matching = [item for item in existing
                    if menu_service._monthly_item_identity_key_from_item(item) == identity]
        assert len(matching) <= 1, "duplicate authored recipe identity"
        if matching:
            assert (matching[0].qty_per_serving, matching[0].unit_type) == (qty_per_serving, unit_type)
            continue
        session.add(MonthlyMenuItem(id=str(uuid4()), monthly_menu_id=entry.monthly_menu_id,
            name=entry.name, daypart=entry.daypart, category=entry.category,
            diet_type=identity[3], facility_override=entry.facility_override,
            qty_per_serving=qty_per_serving, unit_type=unit_type, master_resolution_mode="manual"))
        session.flush()


def declare_period_recipes(session, *, facility_id, period, qty_per_serving, unit_type):
    from src.services.sheet_week_service import parse_sheet_week_value, sheet_week_month_ids

    _, start, end = parse_sheet_week_value(period)
    assert start is not None and end is not None
    assert isinstance(facility_id, str) and facility_id.strip() == facility_id and facility_id
    entries = []
    for month in sheet_week_month_ids(period):
        selected = menu_service.get_menu_entries_for_facility(month, facility_id, session=session)
        for entry in selected:
            day = date.fromisoformat(entry["menu_date"])
            if start <= day <= end:
                stored = session.get(MonthlyMenuEntry, entry["id"])
                assert stored is not None
                entries.append(stored)
    declare_recipes(session, entries, qty_per_serving=qty_per_serving, unit_type=unit_type)
