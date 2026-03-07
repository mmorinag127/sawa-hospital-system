import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import menu_service
from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuItem, MenuMaster, MenuFacilityOverride


def test_menu_override_replacement():
    with session_scope() as session:
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
    menu_service.create_menu("2025-12", b"menu\nMenuA\n", "file.csv")
    item = menu_service.create_item_stub("2025-12", "メニューA")
    menu_service.update_item(
        "2025-12",
        item["id"],
        {
            "facility_override": "FAC002",
            "unit_type": "cut",
            "qty_per_serving": 2,
            "bag_max_qty": 30,
            "bag_max_unit": "cut",
        },
    )
    fetched = menu_service.get_item(item["id"])
    assert fetched["facility_override"] == "FAC002"
    assert fetched["unit_type"] == "cut"
    assert fetched["qty_per_serving"] == 2
    assert fetched["bag_max_qty"] == 30
    assert fetched["bag_max_unit"] == "cut"

    with session_scope() as session:
        row = session.get(MonthlyMenuItem, item["id"])
        assert row is not None
        assert row.menu_master_id is not None
        assert row.unit_type is None
        assert row.qty_per_serving is None

        override = (
            session.query(MenuFacilityOverride)
            .filter(MenuFacilityOverride.menu_master_id == row.menu_master_id)
            .filter(MenuFacilityOverride.facility_id == "FAC002")
            .first()
        )
        assert override is not None
        assert override.unit_type == "cut"
        assert override.qty_per_serving == 2
        assert override.bag_max_qty == 30
        assert override.bag_max_unit == "cut"


def test_menu_update_rejects_month_mismatch():
    with session_scope() as session:
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
    menu_service.create_menu("2025-12", b"menu\nMenuA\n", "file.csv")
    menu_service.create_menu("2026-01", b"menu\nMenuB\n", "file.csv")
    item = menu_service.create_item_stub("2026-01", "メニューB")
    updated = menu_service.update_item("2025-12", item["id"], {"diet_type": "soft"})
    assert updated is False
    fetched = menu_service.get_item(item["id"])
    assert fetched["diet_type"] is None


def test_create_item_stub_reuses_existing_name():
    with session_scope() as session:
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
    menu_service.create_menu("2025-12", b"menu\nMenuA\n", "file.csv")
    first = menu_service.create_item_stub("2025-12", "メニューA")
    second = menu_service.create_item_stub("2025-12", "メニューA")
    assert first["id"] == second["id"]
