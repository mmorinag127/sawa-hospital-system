"""Synthetic recipe preparation uses formal facility/period selection, not OCR values."""
from datetime import date

import pytest

from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuEntry, MonthlyMenuItem
from tests.helpers.authored_menu_recipe import declare_period_recipes


def test_recipe_preparation_never_borrows_other_facility_or_overridden_global(isolated_formal_schema):
    with session_scope() as session:
        session.add(MonthlyMenu(id="2026-03", month_start=date(2026, 3, 1)))
        session.flush()
        for identity, facility, name, day in (
            ("global", None, "Global menu", date(2026, 3, 22)),
            ("facility-a", "RECIPE-A", "A menu", date(2026, 3, 22)),
            ("facility-b", "RECIPE-B", "B menu", date(2026, 3, 22)),
            ("outside", "RECIPE-A", "Outside menu", date(2026, 3, 29)),
        ):
            session.add(MonthlyMenuEntry(id=identity, monthly_menu_id="2026-03",
                menu_date=day, daypart="朝", slot_index=0, name=name, category="主菜",
                diet_type="regular", facility_override=facility))
        session.flush()
        original = [(entry.id, entry.name, entry.facility_override, entry.menu_date)
                    for entry in session.query(MonthlyMenuEntry).order_by(MonthlyMenuEntry.id)]
        declare_period_recipes(session, facility_id="RECIPE-A",
            period="2026-03@2026-03-22~2026-03-28", qty_per_serving=40, unit_type="g")
        recipes = session.query(MonthlyMenuItem).all()
        assert [(item.name, item.facility_override, item.qty_per_serving, item.unit_type)
                for item in recipes] == [("A menu", "RECIPE-A", 40, "g")]
        assert [(entry.id, entry.name, entry.facility_override, entry.menu_date)
                for entry in session.query(MonthlyMenuEntry).order_by(MonthlyMenuEntry.id)] == original
        with pytest.raises(AssertionError):
            declare_period_recipes(session, facility_id="RECIPE-A",
                period="2026-03@2026-03-22~2026-03-28", qty_per_serving=50, unit_type="g")
        assert recipes[0].qty_per_serving == 40
