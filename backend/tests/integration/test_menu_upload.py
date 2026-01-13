import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import menu_service
from src.db import session_scope
from src.models.menu import WeeklyMenu, MenuItem


def test_menu_upload_records_menu():
    with session_scope() as session:
        session.query(MenuItem).delete()
        session.query(WeeklyMenu).delete()
    content = b"menu\nMenuA\nMenuB\n"
    menu_service.create_menu("WEK2025W52", content, "file.csv")
    items = menu_service.get_menu_items("WEK2025W52")
    assert len(items) == 2
