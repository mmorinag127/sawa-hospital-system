"""Actual menu writes/reads on owned formal SQLite, not live database proof."""
from datetime import date

import pytest

from src import db
from src.models.menu import MenuMaster, MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry, MenuFacilityOverride
from src.services import menu_service


@pytest.fixture
def monthly_store(isolated_formal_schema):
    with db.session_scope() as session:
        session.add(MonthlyMenu(id="month", month_start=date(2026, 10, 1)))
        session.add(MenuMaster(id="master", name="Dish", normalized_name="dish", unit_type="g",
            qty_per_serving=99, daypart="夕食", category="Master-only", bag_max_qty=10))
    return isolated_formal_schema


def seed_item(item_id="item", **values):
    with db.session_scope() as session:
        session.add(MonthlyMenuItem(id=item_id, monthly_menu_id="month", menu_master_id="master",
            name="Dish", **values))


def get_and_list(item_id, facility_id=None):
    direct = menu_service.get_item(item_id)
    items = (menu_service.get_menu_items_for_facility("month", facility_id, allow_unresolved=True)
        if facility_id else menu_service.get_menu_items("month", allow_unresolved=True))
    listed = next(item for item in items
        if item["id"] == item_id)
    return direct, listed


@pytest.mark.parametrize("diet", ["regular", "soft", None])
@pytest.mark.parametrize("identity", [{"daypart": "昼食"}, {"category": "主菜"},
    {"daypart": "昼食", "category": "主菜"}])
@pytest.mark.parametrize("quantity", [0, None])
def test_first_explicit_identity_survives_write_get_and_list(monthly_store, diet, identity, quantity):
    seed_item(diet_type=diet)
    patch = dict(identity, qty_per_serving=quantity, unit_type="g", bag_max_qty=0, temp_type=None)
    assert menu_service.update_item_status("month", "item", patch) == "updated"
    for projection in get_and_list("item"):
        for field in ("daypart", "category"):
            assert projection[field] == identity.get(field), "identity never comes from the master"
        assert projection["diet_type"] == diet
        assert projection["qty_per_serving"] == quantity
        assert projection["unit_type"] == "g"
        assert projection["bag_max_qty"] == 0
        assert projection["temp_type"] is None
        if diet is None:
            assert projection["confirmation_required"] is True
            assert "diet_type" in projection["unresolved_fields"]
    with db.session_scope() as session:
        item = session.get(MonthlyMenuItem, "item")
        assert item.master_resolution_mode is None
        assert item.qty_per_serving is None
        assert item.unit_type is None and item.temp_type is None
        assert item.bag_max_qty is None
        assert item.menu_master_id == "master"
        assert item.diet_type == diet
        for field in ("daypart", "category"):
            assert getattr(item, field) == identity.get(field)
        master = session.get(MenuMaster, "master")
        assert master.qty_per_serving == quantity
        assert master.unit_type == "g" and master.bag_max_qty == 0


def test_initial_identity_keeps_independent_master_quantity_inheritance(monthly_store):
    seed_item(diet_type="regular")
    assert menu_service.update_item_status("month", "item",
        {"daypart": "昼食", "category": "主菜", "qty_per_serving": 0, "unit_type": "g"}) == "updated"
    for projection in get_and_list("item"):
        assert projection["qty_per_serving"] == 0
    with db.session_scope() as session:
        master = session.get(MenuMaster, "master")
        master.qty_per_serving = 125
    for projection in get_and_list("item"):
        assert projection["qty_per_serving"] == 125
        assert projection["unit_type"] == "g"
        assert (projection["daypart"], projection["category"]) == ("昼食", "主菜")
    with db.session_scope() as session:
        item = session.get(MonthlyMenuItem, "item")
        assert item.qty_per_serving is None and item.unit_type is None
        assert item.master_resolution_mode is None and item.menu_master_id == "master"


@pytest.mark.parametrize("clear", [None, "", " "])
def test_previous_identity_flag_survives_explicit_clear(monthly_store, clear):
    seed_item(daypart="朝食", category="副菜", diet_type="regular", qty_per_serving=0,
        unit_type="g", master_resolution_mode="month_only")
    assert menu_service.update_item_status("month", "item", {"daypart": "昼食", "category": "主菜"}) == "updated"
    for projection in get_and_list("item"):
        assert (projection["daypart"], projection["category"]) == ("昼食", "主菜")
        assert projection["qty_per_serving"] == 0
    assert menu_service.update_item_status("month", "item", {"daypart": clear, "category": clear}) == "updated"
    with db.session_scope() as session:
        item = session.get(MonthlyMenuItem, "item")
        assert item.master_resolution_mode == "month_only"
        assert item.daypart is None and item.category is None
        assert item.qty_per_serving == 0
    for projection in get_and_list("item"):
        assert projection["daypart"] is None and projection["category"] is None
        assert projection["qty_per_serving"] == 0
        assert projection["master_resolution_mode"] == "month_only"


@pytest.mark.parametrize("quantity", [0, None])
def test_amount_only_keeps_existing_master_inheritance_without_identity_guess(monthly_store, quantity):
    seed_item(diet_type="regular")
    assert menu_service.update_item_status("month", "item", {"qty_per_serving": quantity, "bag_max_qty": 0}) == "updated"
    with db.session_scope() as session:
        item = session.get(MonthlyMenuItem, "item")
        master = session.get(MenuMaster, "master")
        assert item.master_resolution_mode is None
        assert item.qty_per_serving is None and item.bag_max_qty is None
        assert master.qty_per_serving == quantity and master.bag_max_qty == 0
    for projection in get_and_list("item"):
        assert projection["qty_per_serving"] == quantity
        assert projection["bag_max_qty"] == 0
        assert projection["daypart"] is None and projection["category"] is None


@pytest.mark.parametrize("diet", ["regular", "soft"])
@pytest.mark.parametrize("scope", [None, "GI7"])
def test_first_identity_does_not_select_same_name_diet_or_scope_neighbor(monthly_store, diet, scope):
    seed_item(diet_type=diet, facility_override=scope)
    neighbors = [(other_diet, other_scope) for other_diet in ("regular", "soft")
        for other_scope in (None, "GI7") if (other_diet, other_scope) != (diet, scope)]
    for index, (other_diet, other_scope) in enumerate(neighbors):
        seed_item(f"neighbor-{index}", diet_type=other_diet, facility_override=other_scope,
            daypart="昼食", category="主菜", qty_per_serving=777, unit_type="g", master_resolution_mode="month_only")
    assert menu_service.update_item_status("month", "item",
        {"daypart": "昼食", "category": "主菜", "qty_per_serving": 0}) == "updated"
    for projection in get_and_list("item", scope):
        assert projection["diet_type"] == diet and projection["facility_override"] == scope
        assert projection["qty_per_serving"] == 0
        assert (projection["daypart"], projection["category"]) == ("昼食", "主菜")
    if scope:
        unscoped = next(item for item in menu_service.get_menu_items("month", allow_unresolved=True)
            if item["id"] == "item")
        assert unscoped["qty_per_serving"] == 0
    with db.session_scope() as session:
        item = session.get(MonthlyMenuItem, "item")
        assert item.qty_per_serving is None and item.unit_type is None
        assert item.master_resolution_mode is None
        for index, (other_diet, other_scope) in enumerate(neighbors):
            row = session.get(MonthlyMenuItem, f"neighbor-{index}")
            assert (row.diet_type, row.facility_override, row.daypart, row.category, row.qty_per_serving) == (
                other_diet, other_scope, "昼食", "主菜", 777)


@pytest.mark.parametrize("shared_quantity", [12, None])
def test_unfiltered_list_uses_each_explicit_owner_and_preserves_raw_rows(monthly_store, shared_quantity):
    expected = {"global": (None, shared_quantity), "gi7": ("GI7", 0), "gi8": ("GI8", 42)}
    with db.session_scope() as session:
        master = session.get(MenuMaster, "master")
        master.qty_per_serving = shared_quantity
        master.bag_max_qty = None
        session.add_all([
            MenuFacilityOverride(id="override7", menu_master_id="master", facility_id="GI7",
                qty_per_serving=0, unit_type="g", bag_max_qty=0),
            MenuFacilityOverride(id="override8", menu_master_id="master", facility_id="GI8",
                qty_per_serving=42, unit_type="g", bag_max_qty=None),
        ])
    for item_id, (owner, _) in expected.items():
        seed_item(item_id, diet_type="regular", facility_override=owner,
            daypart="昼食", category="主菜")
    fields = ("menu_master_id", "facility_override", "diet_type", "daypart", "category",
        "qty_per_serving", "unit_type", "bag_max_qty", "master_resolution_mode")
    with db.session_scope() as session:
        before = {item_id: tuple(getattr(session.get(MonthlyMenuItem, item_id), field)
            for field in fields) for item_id in expected}
    listed = {item["id"]: item for item in menu_service.get_menu_items("month", allow_unresolved=True)}
    assert set(listed) == set(expected)
    for item_id, (owner, quantity) in expected.items():
        for projection in (menu_service.get_item(item_id), listed[item_id]):
            assert projection["facility_override"] == owner
            assert projection["menu_master_id"] == "master"
            assert projection["qty_per_serving"] == quantity
            assert projection["unit_type"] == "g"
            assert projection["bag_max_qty"] == (0 if owner == "GI7" else None)
            assert (projection["daypart"], projection["category"]) == ("昼食", "主菜")
    for owner, item_id in (("GI7", "gi7"), ("GI8", "gi8")):
        selected = menu_service.get_menu_items_for_facility("month", owner, allow_unresolved=True)
        assert [item["id"] for item in selected] == [item_id]
        assert selected[0]["qty_per_serving"] == expected[item_id][1]
    with db.session_scope() as session:
        after = {item_id: tuple(getattr(session.get(MonthlyMenuItem, item_id), field)
            for field in fields) for item_id in expected}
        assert after == before
        for item_id in expected:
            row = session.get(MonthlyMenuItem, item_id)
            assert row.qty_per_serving is None and row.unit_type is None
        assert session.get(MenuMaster, "master").qty_per_serving == shared_quantity
        assert session.get(MenuFacilityOverride, "override7").qty_per_serving == 0
        assert session.get(MenuFacilityOverride, "override8").qty_per_serving == 42


def test_exact_identity_conflict_is_rejected_before_master_update(monthly_store):
    seed_item(daypart="朝食", category="主菜", diet_type="regular", qty_per_serving=0)
    seed_item("duplicate", daypart="昼食", category="主菜", diet_type="regular", qty_per_serving=123)
    # The caller owns the transaction and rolls back the reported conflict.
    with db.session_scope() as session:
        assert menu_service.update_item_status("month", "item",
            {"daypart": "昼食", "qty_per_serving": 999}, _session=session) == "conflict"
        assert session.get(MenuMaster, "master").qty_per_serving == 99
        session.rollback()
    with db.session_scope() as session:
        assert session.get(MonthlyMenuItem, "item").daypart == "朝食"
        assert session.get(MonthlyMenuItem, "item").qty_per_serving == 0
        assert session.get(MonthlyMenuItem, "duplicate").qty_per_serving == 123


def test_repaired_id_keeps_month_only_even_without_explicit_identity(monthly_store):
    with db.session_scope() as session:
        session.add(MonthlyMenuEntry(id="entry", monthly_menu_id="month", name="Dish",
            menu_date=date(2026, 10, 2), daypart="", slot_index=0, diet_type="soft"))
    item_id = "MMI_REPAIR_entry"
    assert menu_service.update_item_status("month", item_id, {"qty_per_serving": 0}) == "updated"
    with db.session_scope() as session:
        item = session.get(MonthlyMenuItem, item_id)
        assert item.master_resolution_mode == "month_only"
        assert item.diet_type == "soft" and item.qty_per_serving == 0
        assert item.daypart is None and item.category is None
    for projection in get_and_list(item_id):
        assert projection["qty_per_serving"] == 0 and projection["diet_type"] == "soft"
