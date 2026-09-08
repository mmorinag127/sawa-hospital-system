from copy import deepcopy
from datetime import date

import pytest

from src.services import output_builder


@pytest.mark.parametrize("daypart", ["朝", "朝食"])
@pytest.mark.parametrize("diet", ["normal", "soft", "mixer"])
def test_breakfast_label_changes_only_category_display(daypart, diet):
    bag = {
        "date": date(2026, 9, 13),
        "daypart": daypart,
        "menu_category": "主菜",
        "menu_name": "試験献立",
        "diet_type": diet,
        "quantity": 2,
        "menu_qty_per_serving": 100,
        "menu_unit_type": "g",
    }
    original = deepcopy(bag)
    breakfast_rows, _, _ = output_builder._build_label_rows([bag], {}, "試験施設")
    lunch_rows, _, _ = output_builder._build_label_rows([{**bag, "daypart": "昼"}], {}, "試験施設")
    breakfast = breakfast_rows[0]
    lunch = lunch_rows[0]
    assert breakfast["メニュー"] == lunch["メニュー"].replace("主菜", "副菜①", 1)
    for key in breakfast:
        if key not in {"メニュー", "時間"}:
            assert breakfast[key] == lunch[key]
    assert bag == original
    assert output_builder._label_payload_legacy(bag, {}, "試験施設")["menu_category"] == "副菜①"


@pytest.mark.parametrize("daypart", ["昼", "昼食", "夕", "夕食"])
def test_lunch_and_dinner_categories_are_unchanged(daypart):
    assert output_builder._label_display_category(daypart, "主菜") == "主菜"


def test_breakfast_other_categories_and_suffixes():
    assert output_builder._label_display_category("朝", "副菜②") == "副菜②"
    assert output_builder._label_display_category("朝", "主菜（軟菜）") == "副菜①（軟菜）"
    assert output_builder._label_display_category("朝", "主菜 添え") == "副菜① 添え"


def test_breakfast_distinct_categories_do_not_merge():
    bags = [
        {
            "date": date(2026, 9, 13), "daypart": "朝",
            "menu_category": category, "menu_name": "試験献立",
            "quantity": 2, "quantity_original": 2,
            "menu_qty_per_serving": 100, "menu_unit_type": "g",
        }
        for category in ("主菜", "副菜①")
    ]
    rows, _, _ = output_builder._build_label_rows(bags, {}, "試験施設")
    assert len(rows) == 2
    assert [row["メニュー"] for row in rows] == ["副菜①", "副菜①"]
    assert [row["発行枚数"] for row in rows] == [1, 1]
    totals, _, _ = output_builder._build_total_rows(bags, {}, "試験施設", {})
    assert len(totals) == 2
    assert [row["内容量"] for row in totals] == [row["内容量"] for row in rows]
