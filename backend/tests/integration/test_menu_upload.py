import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import menu_service
from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuItem, MenuMaster, MenuFacilityOverride


def test_menu_upload_records_menu():
    with session_scope() as session:
        session.query(MonthlyMenuItem).delete()
        session.query(MenuFacilityOverride).delete()
        session.query(MenuMaster).delete()
        session.query(MonthlyMenu).delete()
    content = b"menu\nMenuA\nMenuB\n"
    menu_service.create_menu("2025-12", content, "file.csv")
    items = menu_service.get_menu_items("2025-12")
    assert len(items) == 2
    assert all(item.get("menu_master_id") for item in items)

    first_name = items[0]["name"]
    with session_scope() as session:
        rows = session.query(MonthlyMenuItem).all()
        assert len(rows) == 2
        assert all(row.menu_master_id for row in rows)
        assert all(row.unit_type is None for row in rows)
        assert all(row.qty_per_serving is None for row in rows)
        master = session.get(MenuMaster, rows[0].menu_master_id)
        assert master is not None
        master.unit_type = "g"
        master.qty_per_serving = 80

    merged = menu_service.get_menu_items("2025-12")
    index = {item["name"]: item for item in merged}
    assert index[first_name]["unit_type"] == "g"
    assert index[first_name]["qty_per_serving"] == 80
