import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import menu_service
from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuItem, MenuMaster, MenuFacilityOverride


def test_menu_edit_updates_item():
    with session_scope() as session:
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
    menu_service.create_menu("2025-12", b"menu\nMenuA\n", "file.csv")
    item = menu_service.create_item_stub("2025-12", "メニューA")
    changed = menu_service.update_item(
        "2025-12",
        item["id"],
        {
            "unit_type": "g",
            "qty_per_serving": 120,
            "temp_type": "cold",
            "daypart": "昼食",
            "category": "主菜",
            "bag_max_qty": 200,
            "bag_max_unit": "g",
        },
    )
    assert changed
    fetched = menu_service.get_item(item["id"])
    assert fetched["unit_type"] == "g"
    assert fetched["qty_per_serving"] == 120
    assert fetched["temp_type"] == "cold"
    assert fetched["daypart"] == "昼食"
    assert fetched["category"] == "主菜"
    assert fetched["bag_max_qty"] == 200
    assert fetched["bag_max_unit"] == "g"

    with session_scope() as session:
        row = session.get(MonthlyMenuItem, item["id"])
        assert row is not None
        assert row.menu_master_id is not None
        assert row.unit_type is None
        assert row.qty_per_serving is None
        assert row.temp_type is None
        assert row.daypart is None
        assert row.category is None

        master = session.get(MenuMaster, row.menu_master_id)
        assert master is not None
        assert master.unit_type == "g"
        assert master.qty_per_serving == 120
        assert master.temp_type == "cold"
        assert master.daypart == "昼食"
        assert master.category == "主菜"
        assert master.bag_max_qty == 200
        assert master.bag_max_unit == "g"

    cleared = menu_service.update_item(
        "2025-12",
        item["id"],
        {
            "unit_type": None,
            "qty_per_serving": None,
            "temp_type": None,
            "daypart": None,
            "category": None,
            "bag_max_qty": None,
            "bag_max_unit": None,
        },
    )
    assert cleared
    fetched_cleared = menu_service.get_item(item["id"])
    assert fetched_cleared["unit_type"] is None
    assert fetched_cleared["qty_per_serving"] is None
    assert fetched_cleared["temp_type"] is None
    assert fetched_cleared["daypart"] is None
    assert fetched_cleared["category"] is None
    assert fetched_cleared["bag_max_qty"] is None
    assert fetched_cleared["bag_max_unit"] is None


def test_menu_edit_normalizes_japanese_unit_inputs():
    with session_scope() as session:
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
    menu_service.create_menu("2025-12", b"menu\nMenuA\n", "file.csv")
    item = menu_service.create_item_stub("2025-12", "白身魚のフライ")
    changed = menu_service.update_item(
        "2025-12",
        item["id"],
        {
            "unit_type": "切",
            "qty_per_serving": 1,
            "bag_max_qty": 30,
            "bag_max_unit": "個",
        },
    )
    assert changed

    fetched = menu_service.get_item(item["id"])
    assert fetched["unit_type"] == "cut"
    assert fetched["qty_per_serving"] == 1
    assert fetched["bag_max_unit"] == "count"

    with session_scope() as session:
        row = session.get(MonthlyMenuItem, item["id"])
        assert row is not None
        master = session.get(MenuMaster, row.menu_master_id)
        assert master is not None
        assert master.unit_type == "cut"
        assert master.qty_per_serving == 1
        assert master.bag_max_unit == "count"
