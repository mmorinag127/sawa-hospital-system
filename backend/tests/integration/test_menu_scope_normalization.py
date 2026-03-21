import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.db import session_scope
from src.models.menu import BaseMenuCycleItem, MenuRule
from src.services import base_menu_service, menu_rule_service


def test_base_menu_service_normalizes_diet_type_on_save_and_serialize():
    with session_scope() as session:
        session.query(BaseMenuCycleItem).delete()

    base_menu_service.replace_items(
        [
            {
                "cycle_day": 1,
                "daypart": "朝食",
                "category": "主菜",
                "name": "筑前煮",
                "diet_type": "糖尿",
                "slot_index": 0,
            }
        ]
    )

    items = base_menu_service.list_items(1)
    assert items[0]["diet_type"] == "diabetes"

    item_id = items[0]["id"]
    assert base_menu_service.update_item(item_id, {"diet_type": "禁食(魚禁)"}) is True

    updated = base_menu_service.list_items(1)
    assert updated[0]["diet_type"] == "no_fish"


def test_menu_rule_service_normalizes_diet_type_on_create_and_update():
    with session_scope() as session:
        session.query(MenuRule).delete()

    created = menu_rule_service.create_rule(
        {
            "rule_type": "global",
            "daypart": "昼食",
            "category": "主菜",
            "diet_type": "軟菜",
            "unit_type": "g",
            "qty_per_serving": 80,
            "active": True,
        }
    )

    assert created["diet_type"] == "soft"

    assert menu_rule_service.update_rule(created["id"], {"diet_type": "ゴマアレルギー"}) is True

    rules = menu_rule_service.list_rules("global")
    assert rules[0]["diet_type"] == "sesame_allergy"
