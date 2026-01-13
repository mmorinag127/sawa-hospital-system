import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import menu_service
from src.db import session_scope
from src.models.menu import WeeklyMenu, MenuItem


def test_menu_override_replacement():
    with session_scope() as session:
        session.query(MenuItem).delete()
        session.query(WeeklyMenu).delete()
    menu_service.create_menu("WEK2025W52", b"menu\nMenuA\n", "file.csv")
    item = menu_service.create_item_stub("WEK2025W52", "メニューA")
    menu_service.update_item("WEK2025W52", item["id"], {"facility_override": "FAC002"})
    fetched = menu_service.get_item(item["id"])
    assert fetched["facility_override"] == "FAC002"
