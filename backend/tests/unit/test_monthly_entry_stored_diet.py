from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuEntry
from src.services import menu_service


@pytest.mark.parametrize("stored", [None, "", "regular"])
def test_stored_regular_entry_uses_same_override_identity(stored):
    entry = SimpleNamespace(menu_date=date(2026, 7, 6), daypart="昼食", slot_index=1, diet_type=stored)
    assert menu_service._monthly_entry_override_key(entry) == (date(2026, 7, 6), "昼食", 1, "regular")


@pytest.mark.parametrize("requested, expected", [
    (None, {"null", "blank", "regular"}),
    ("", {"null", "blank", "regular"}),
    ("regular", {"null", "blank", "regular"}),
    ("soft", {"soft"}),
    ("mixer", {"mixer"}),
    ("soft_mixer", {"soft_mixer"}),
    ("unknown", set()),
])
def test_entry_filter_preserves_stored_meaning_without_cross_diet_matches(isolated_formal_schema, requested, expected):
    with session_scope() as session:
        session.add(MonthlyMenu(id="stored-diets", month_start=date(2026, 7, 1)))
        session.flush()
        for identity, diet in [("null", None), ("blank", ""), ("regular", "regular"),
                               ("soft", "soft"), ("mixer", "mixer"), ("soft_mixer", "soft_mixer")]:
            session.add(MonthlyMenuEntry(id=identity, monthly_menu_id="stored-diets",
                                        menu_date=date(2026, 7, 6), daypart="昼食", slot_index=1,
                                        name=identity, diet_type=diet))
        session.flush()
        matches = session.execute(select(MonthlyMenuEntry.id).where(
            menu_service._monthly_entry_diet_filter(MonthlyMenuEntry.diet_type, requested))).scalars().all()
        assert set(matches) == expected
        if requested == "regular":
            with pytest.raises(ValueError, match="monthly_menu_entry_identity_ambiguous"):
                menu_service._find_existing_monthly_entry(session, month_id="stored-diets",
                    menu_date=date(2026, 7, 6), daypart="昼食", slot_index=1,
                    facility_override=None, diet_type=requested)


def test_unresolved_master_item_diet_is_not_inferred():
    assert menu_service._monthly_item_identity_key(name="Dish", daypart="昼食", category="主菜",
                                                  diet_type=None, facility_override=None)[3] == ""
