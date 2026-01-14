import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import menu_service
from src.db import session_scope
from src.models.menu import MonthlyMenu, MonthlyMenuItem


def test_menu_override_replacement():
    with session_scope() as session:
        session.query(MonthlyMenuItem).delete()
        session.query(MonthlyMenu).delete()
    menu_service.create_menu("2025-12", b"menu\nMenuA\n", "file.csv")
    item = menu_service.create_item_stub("2025-12", "メニューA")
    menu_service.update_item("2025-12", item["id"], {"facility_override": "FAC002"})
    fetched = menu_service.get_item(item["id"])
    assert fetched["facility_override"] == "FAC002"
