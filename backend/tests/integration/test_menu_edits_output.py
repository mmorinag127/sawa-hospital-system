import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import menu_service
from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuItem


def test_menu_edit_updates_item():
    with session_scope() as session:
        session.query(MonthlyMenuItem).delete()
        session.query(MonthlyMenu).delete()
    menu_service.create_menu("2025-12", b"menu\nMenuA\n", "file.csv")
    item = menu_service.create_item_stub("2025-12", "メニューA")
    changed = menu_service.update_item(
        "2025-12", item["id"], {"unit_type": "g", "qty_per_serving": 120}
    )
    assert changed
    fetched = menu_service.get_item(item["id"])
    assert fetched["unit_type"] == "g"
