import pytest

from src.services import order_service, output_builder
from tests.helpers.monthly_override_fixture import (
    authored_entries, captured_overrides, publish_configuration, publish_sources, produce,
)


def _label_menu_values_for_bags(bags):
    labels, _fields, label_format = output_builder._build_label_rows(bags, {}, None)
    assert label_format == "jp"
    return [row.get("メニュー") for row in labels]


def test_daily_labels_preserve_facility_specific_daycare_and_staff_categories():
    menus = _label_menu_values_for_bags(
        [
            {
                "facility": "FAC00004",
                "date": "2026-05-12",
                "daypart": "昼",
                "menu_name": "麻婆茄子",
                "menu_category": "主菜",
                "diet_type": "daycare",
                "quantity": 36,
                "menu_qty_per_serving": 120,
                "menu_unit_type": "g",
            },
            {
                "facility": "FAC00004",
                "date": "2026-05-12",
                "daypart": "夕",
                "menu_name": "豚肉のきのこ炒め",
                "menu_category": "主菜",
                "diet_type": "staff",
                "quantity": 5,
                "menu_qty_per_serving": 130,
                "menu_unit_type": "g",
            },
        ]
    )

    assert "主菜（通所）" in menus
    assert "主菜（職員）" in menus


def test_daily_labels_use_source_diet_for_diabetes_after_regular_output_mapping():
    menus = _label_menu_values_for_bags(
        [
            {
                "facility": "FAC00016",
                "date": "2026-05-12",
                "daypart": "朝",
                "menu_name": "大豆のトマト煮",
                "menu_category": "副菜①",
                "diet_type": "regular",
                "source_diet_type": "diabetes",
                "quantity": 4,
                "menu_qty_per_serving": 70,
                "menu_unit_type": "g",
            }
        ]
    )

    assert menus == ["副菜①（糖尿）"]


def test_daily_labels_derive_comparable_soft_from_facility_quantity_columns():
    bags = output_builder._apply_daily_label_facility_rules_to_bags(
        [
            {
                "facility": "FAC00005",
                "date": "2026-06-21",
                "daypart": "昼",
                "menu_name": "カレイの照焼き",
                "menu_category": "主菜",
                "diet_type": "soft",
                "quantity": 3,
                "menu_qty_per_serving": 100,
                "menu_unit_type": "g",
            }
        ],
        {
            "fax_template": {
                "columns": [
                    {"role": "quantity", "diet_type": "soft", "area_id": "X"},
                    {"role": "quantity", "diet_type": "regular_bag", "area_id": "X"},
                ]
            }
        },
        "FAC00005",
    )

    assert _label_menu_values_for_bags(bags) == ["主菜（軟菜）"]


def test_bag_rows_preserve_source_diet_type_for_label_display_after_output_mapping():
    bags = output_builder._build_bags(
        {
            "id": "ORD-ikebukuro",
            "facility": "FAC00005",
            "lines": [
                {
                    "date": "2026-06-21",
                    "daypart": "昼",
                    "menu_name": "カレイの照焼き",
                    "menu_category": "主菜",
                    "diet_type": "regular",
                    "source_diet_type": "soft",
                    "area_id": "X",
                    "quantity": 3,
                    "menu_qty_per_serving": 100,
                    "menu_unit_type": "g",
                },
                {
                    "date": "2026-06-21",
                    "daypart": "昼",
                    "menu_name": "カレイの照焼き",
                    "menu_category": "主菜",
                    "diet_type": "regular_bag",
                    "area_id": "X",
                    "quantity": 2,
                    "menu_qty_per_serving": 100,
                    "menu_unit_type": "g",
                },
            ],
        },
        {},
        {"zero_as_empty": True},
    )

    assert [
        (bag["diet_type"], bag.get("source_diet_type"), bag["quantity"])
        for bag in sorted(bags, key=lambda item: str(item.get("diet_type")))
    ] == [
        ("regular", "soft", 3.0),
        ("regular_bag", None, 2.0),
    ]


def test_daily_labels_separate_soft_and_mixer_categories():
    menus = _label_menu_values_for_bags(
        [
            {
                "facility": "FAC00010",
                "date": "2026-05-12",
                "daypart": "昼",
                "menu_name": "もやしナムル",
                "menu_category": "副菜②",
                "diet_type": "soft",
                "quantity": 6,
                "menu_qty_per_serving": 40,
                "menu_unit_type": "g",
            },
            {
                "facility": "FAC00010",
                "date": "2026-05-12",
                "daypart": "昼",
                "menu_name": "もやしナムル",
                "menu_category": "副菜②",
                "diet_type": "mixer",
                "quantity": 7,
                "menu_qty_per_serving": 40,
                "menu_unit_type": "g",
            },
        ]
    )

    assert "副菜②（軟菜）" in menus
    assert "副菜②（ミキサー）" in menus


def test_daily_labels_do_not_mark_facility_unsupported_staff_category():
    menus = _label_menu_values_for_bags(
        [
            {
                "facility": "FAC00010",
                "date": "2026-05-12",
                "daypart": "昼",
                "menu_name": "麻婆茄子",
                "menu_category": "主菜",
                "diet_type": "staff",
                "quantity": 1,
                "menu_qty_per_serving": 100,
                "menu_unit_type": "g",
            }
        ]
    )

    assert menus == ["主菜"]


def test_daily_label_facility_config_overrides_default_comparable_diets():
    bags = output_builder._apply_daily_label_facility_rules_to_bags(
        [
            {
                "facility": "FAC00010",
                "date": "2026-05-12",
                "daypart": "昼",
                "menu_name": "麻婆茄子",
                "menu_category": "主菜",
                "diet_type": "staff",
                "quantity": 1,
                "menu_qty_per_serving": 100,
                "menu_unit_type": "g",
            }
        ],
        {"daily_label_comparable_diet_types": ["staff"]},
        "FAC00010",
    )

    assert _label_menu_values_for_bags(bags) == ["主菜（職員）"]


def test_daily_labels_format_expiry_date_in_japanese_style():
    labels, fields, label_format = output_builder._build_label_rows(
        [
            {
                "facility": "FAC00009",
                "date": "2026-05-25",
                "daypart": "夕",
                "menu_name": "豆腐ハンバーグ和風あん",
                "menu_category": "主菜",
                "diet_type": "regular",
                "area_name": "Synthetic area", "area_id": "2F",
                "quantity": 3,
                "menu_qty_per_serving": 100,
                "menu_unit_type": "g",
            }
        ],
        {},
        None,
    )

    assert label_format == "jp"
    assert "賞味期限" in fields
    assert labels[0]["賞味期限"] == "2026年5月25日"


def test_daily_labels_show_master_condiment_as_main_side_label(monkeypatch):
    defaults = {
        "白身魚フライ": {
            "unit_type": "count",
            "qty_per_serving": 1,
            "temp_type": "hot",
            "category": "主菜",
            "condiments": ["ソース"],
        },
        "ソース": {
            "unit_type": "g",
            "qty_per_serving": 5,
            "temp_type": "cold",
            "category": "添え",
        },
    }
    monkeypatch.setattr(
        output_builder,
        "_resolve_current_menu_defaults",
        lambda names, facility_id: {name: defaults[name] for name in names if name in defaults},
    )

    lines = output_builder._apply_menu_master_defaults(
        [
            {
                "date": "2026-06-07",
                "daypart": "昼",
                "menu_name": "白身魚フライ",
                "menu_category": "主菜",
                "diet_type": "regular",
                "area_id": "2F",
                "quantity_original": 3,
            }
        ],
        "FAC00009",
    )
    lines = output_builder._apply_condiment_lines(lines)
    lines = output_builder._apply_menu_master_defaults(lines, "FAC00009")
    bags = output_builder._build_bags(
        {"id": "ORD-condiment-label", "facility": "FAC00009", "lines": lines},
        {},
        {"zero_as_empty": True},
    )
    bags = output_builder._apply_daily_label_facility_rules_to_bags(
        bags, {"areas": [{"id": "2F", "name": "Synthetic area"}]}, "FAC00009")
    labels, _fields, _label_format = output_builder._build_label_rows(bags, {}, None)

    sauce_label = next(row for row in labels if row["商品名１"] == "ソース")
    assert sauce_label["メニュー"] == "主菜 添え"
    assert sauce_label["温・冷"] == "冷菜"
    assert sauce_label["内容量"] == "15g"
    assert sauce_label["内容詳細"] == "5g"


def test_morning_monthly_entry_main_and_side_slots_become_side_one_and_two(isolated_formal_schema):
    lines = [
        {
            "date": "2026-06-08",
            "daypart": "朝",
            "menu_name": "玉子焼き",
            "menu_category": "主菜",
            "diet_type": "regular",
            "quantity_original": 2,
        },
        {
            "date": "2026-06-08",
            "daypart": "朝",
            "menu_name": "ほうれん草和え",
            "menu_category": "副菜",
            "diet_type": "regular",
            "quantity_original": 2,
        },
    ]
    entries = [
        {
            "menu_date": "2026-06-08",
            "daypart": "朝食",
            "name": "玉子焼き",
            "category": "主菜",
            "slot_index": 1,
        },
        {
            "menu_date": "2026-06-08",
            "daypart": "朝食",
            "name": "ほうれん草和え",
            "category": "副菜",
            "slot_index": 2,
        },
    ]

    entries = [{**entry, "diet_type": "regular"} for entry in entries]
    enriched = captured_overrides(lines, entries,
        week="2026-06@2026-06-08~2026-06-08", source_indexes=[0, 1], physical_amount=(1, "g"))

    assert [line["menu_category"] for line in enriched] == ["副菜1", "副菜2"]


def test_garnish_label_uses_parent_category_and_diet_suffix(isolated_formal_schema):
    # Explicit synthetic accessory amount, not a customer/master default.
    publish_sources([], accessories=[{"name": "おろしｿｰｽ", "qty_per_serving": 1, "unit_type": "g"}])
    lines = output_builder._apply_garnish_lines(
        [
            {
                "date": "2026-06-09",
                "daypart": "夕",
                "menu_name": "豆腐ﾊﾝﾊﾞｰｸﾞ 添)おろしｿｰｽ",
                "menu_category": "主菜",
                "diet_type": "soft",
                "area_id": "2F",
                "quantity_original": 4,
            }
        ]
    )
    garnish = next(line for line in lines if line["menu_name"] == "おろしｿｰｽ")
    sources = output_builder._capture_output_sources("2026-06@2026-06-09~2026-06-09", "FAC00009")
    garnish = output_builder._apply_menu_master_defaults([garnish], "FAC00009", sources=sources)[0]
    garnish = output_builder._apply_garnish_defaults([garnish])[0]
    bags = output_builder._build_bags(
        {"id": "ORD-garnish-label", "facility": "FAC00009", "lines": [garnish]},
        {},
        {"zero_as_empty": True},
    )
    bags = output_builder._apply_daily_label_facility_rules_to_bags(
        bags, {"areas": [{"id": "2F", "name": "Synthetic area"}]}, "FAC00009")
    labels, _fields, _label_format = output_builder._build_label_rows(bags, {}, None)

    assert labels[0]["商品名１"] == "おろしｿｰｽ"
    assert labels[0]["メニュー"] == "主菜 添え（軟菜）"


def test_existing_garnish_row_inherits_previous_menu_category():
    lines = [
        {
            "date": "2026-04-26",
            "daypart": "昼",
            "menu_name": "サワラの揚げ浸し",
            "menu_category": "主菜",
            "diet_type": "regular",
            "source_row_index": 1,
            "quantity_original": 4,
        },
        {
            "date": "2026-04-26",
            "daypart": "昼",
            "menu_name": "ﾎｰﾚﾝ草",
            "menu_category": "添え",
            "diet_type": "soft",
            "source_row_index": 2,
            "quantity_original": 4,
        },
    ]

    lines = output_builder._apply_garnish_parent_categories(lines)
    garnish = output_builder._apply_garnish_defaults([lines[1]])[0]
    bags = output_builder._build_bags(
        {"id": "ORD-existing-garnish-label", "facility": "FAC00009", "lines": [garnish]},
        {},
        {"zero_as_empty": True},
    )
    bags = output_builder._apply_daily_label_facility_rules_to_bags(bags, {}, "FAC00009")
    labels, _fields, _label_format = output_builder._build_label_rows(bags, {}, None)

    assert labels[0]["商品名１"] == "ﾎｰﾚﾝ草"
    assert labels[0]["メニュー"] == "主菜 添え（軟菜）"


def test_label_meal_slots_are_assigned_by_daypart_menu_order(isolated_formal_schema):
    lines = [
        {
            "date": "2026-06-14",
            "daypart": "朝",
            "menu_name": "ジャーマンポテト",
            "menu_category": "主菜",
            "diet_type": "regular",
            "source_row_index": 1,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-14",
            "daypart": "朝",
            "menu_name": "カリフラワーとツナのサラダ",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 2,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-14",
            "daypart": "昼",
            "menu_name": "鶏肉の塩麹焼き",
            "menu_category": "主菜",
            "diet_type": "regular",
            "source_row_index": 1,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-14",
            "daypart": "昼",
            "menu_name": "茄子の味噌炒め",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 2,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-14",
            "daypart": "昼",
            "menu_name": "茄子の味噌炒め",
            "menu_category": "副菜",
            "diet_type": "mixer",
            "source_row_index": 2,
            "quantity_original": 1,
        },
        {
            "date": "2026-06-14",
            "daypart": "昼",
            "menu_name": "白菜のお浸し",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 3,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-14",
            "daypart": "夕",
            "menu_name": "鮭の照焼き",
            "menu_category": "主菜",
            "diet_type": "regular",
            "source_row_index": 1,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-14",
            "daypart": "夕",
            "menu_name": "玉子焼き",
            "menu_category": "主菜",
            "diet_type": "regular",
            "source_row_index": 2,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-14",
            "daypart": "夕",
            "menu_name": "小松菜の和え物",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 3,
            "quantity_original": 3,
        },
    ]

    entries = authored_entries([
        ("2026-06-14", "朝", 1, "ジャーマンポテト", "副菜", "regular"),
        ("2026-06-14", "朝", 2, "カリフラワーとツナのサラダ", "副菜", "regular"),
        ("2026-06-14", "昼", 0, "鶏肉の塩麹焼き", "主菜", "regular"),
        ("2026-06-14", "昼", 1, "茄子の味噌炒め", "副菜", "regular"),
        ("2026-06-14", "昼", 2, "白菜のお浸し", "副菜", "regular"),
        ("2026-06-14", "夕", 0, "鮭の照焼き", "主菜", "regular"),
        ("2026-06-14", "夕", 1, "玉子焼き", "副菜", "regular"),
        ("2026-06-14", "夕", 2, "小松菜の和え物", "副菜", "regular"),
    ])
    bound = captured_overrides(lines, entries,
        week="2026-06@2026-06-14~2026-06-14", source_indexes=[0, 1, 2, 3, 3, 4, 5, 6, 7], physical_amount=(1, "g"))
    enriched = output_builder._apply_label_meal_slot_categories(bound)
    labels, _fields, _label_format = output_builder._build_label_rows(
        [
            {**line, "facility": "FAC00009", "quantity": line["quantity_original"], "area_name": "Synthetic area", "area_id": "2F"}
            for line in enriched
        ],
        {},
        None,
    )

    german = next(row for row in labels if row["商品名１"] == "ジャーマンポテト")
    cauliflower = next(row for row in labels if row["商品名１"] == "カリフラワーとツナのサラダ")
    eggplant_regular = next(row for row in labels if row["商品名１"] == "茄子の味噌炒め" and row["メニュー"] == "副菜①")
    eggplant_mixer = next(row for row in labels if row["商品名１"] == "茄子の味噌炒め" and row["メニュー"] == "副菜①（ミキサー）")
    hakusai = next(row for row in labels if row["商品名１"] == "白菜のお浸し")
    tamago = next(row for row in labels if row["商品名１"] == "玉子焼き")
    komatsuna = next(row for row in labels if row["商品名１"] == "小松菜の和え物")
    assert german["メニュー"] == "副菜①"
    assert cauliflower["メニュー"] == "副菜②"
    assert eggplant_regular["メニュー"] == "副菜①"
    assert eggplant_mixer["メニュー"] == "副菜①（ミキサー）"
    assert hakusai["メニュー"] == "副菜②"
    assert tamago["メニュー"] == "副菜①"
    assert komatsuna["メニュー"] == "副菜②"


def test_label_meal_slots_assign_fourth_lunch_item_as_side_three(isolated_formal_schema):
    lines = [
        {
            "date": "2026-09-21",
            "daypart": "昼",
            "menu_name": "行事食主菜",
            "menu_category": "主菜",
            "diet_type": "regular",
            "source_row_index": 18,
            "quantity_original": 3,
        },
        {
            "date": "2026-09-21",
            "daypart": "昼",
            "menu_name": "行事食副菜一",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 19,
            "quantity_original": 3,
        },
        {
            "date": "2026-09-21",
            "daypart": "昼",
            "menu_name": "行事食副菜二",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 20,
            "quantity_original": 3,
        },
        {
            "date": "2026-09-21",
            "daypart": "昼",
            "menu_name": "行事食副菜三",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 21,
            "quantity_original": 3,
        },
    ]

    entries = authored_entries([
        ("2026-09-21", "昼", 0, "行事食主菜", "主菜", "regular"),
        ("2026-09-21", "昼", 1, "行事食副菜一", "副菜", "regular"),
        ("2026-09-21", "昼", 2, "行事食副菜二", "副菜", "regular"),
        ("2026-09-21", "昼", 3, "行事食副菜三", "副菜", "regular"),
    ])
    bound = captured_overrides(lines, entries,
        week="2026-09@2026-09-21~2026-09-21", source_indexes=[0, 1, 2, 3], physical_amount=(1, "g"))
    enriched = output_builder._apply_label_meal_slot_categories(bound)
    labels, _fields, _label_format = output_builder._build_label_rows(
        [
            {**line, "facility": "FAC00009", "quantity": line["quantity_original"], "area_name": "Synthetic area", "area_id": "2F"}
            for line in enriched
        ],
        {},
        None,
    )

    categories_by_name = {row["商品名１"]: row["メニュー"] for row in labels}
    assert categories_by_name == {
        "行事食主菜": "主菜",
        "行事食副菜一": "副菜①",
        "行事食副菜二": "副菜②",
        "行事食副菜三": "副菜③",
    }


def test_monthly_entry_override_assigns_20260921_lunch_fourth_slot_as_side_three():
    entries = [
        {
            "menu_date": "2026-09-21",
            "daypart": "昼食",
            "slot_index": 0,
            "name": "行事食主菜",
            "category": "主菜",
            "diet_type": "regular",
        },
        {
            "menu_date": "2026-09-21",
            "daypart": "昼食",
            "slot_index": 1,
            "name": "行事食副菜一",
            "category": "副菜",
            "diet_type": "regular",
        },
        {
            "menu_date": "2026-09-21",
            "daypart": "昼食",
            "slot_index": 2,
            "name": "行事食副菜二",
            "category": "副菜",
            "diet_type": "regular",
        },
        {
            "menu_date": "2026-09-21",
            "daypart": "昼食",
            "slot_index": 3,
            "name": "行事食副菜三",
            "category": "副菜",
            "diet_type": "regular",
        },
    ]
    lines = [
        {
            "date": "2026-09-21",
            "daypart": "昼",
            "menu_name": entry["name"],
            "menu_category": "副菜" if entry["slot_index"] else "主菜",
            "diet_type": "regular",
            "source_row_index": entry["slot_index"],
            "quantity_original": 3,
        }
        for entry in entries
    ]

    enriched = output_builder._apply_menu_entry_overrides(lines, entries)
    labels, _fields, _label_format = output_builder._build_label_rows(
        [
            {**line, "facility": "FAC00009", "quantity": line["quantity_original"], "area_name": "Synthetic area", "area_id": "2F"}
            for line in enriched
        ],
        {},
        None,
    )

    categories_by_name = {row["商品名１"]: row["メニュー"] for row in labels}
    assert categories_by_name == {
        "行事食主菜": "主菜",
        "行事食副菜一": "副菜①",
        "行事食副菜二": "副菜②",
        "行事食副菜三": "副菜③",
    }


def test_label_meal_slots_skip_parent_prefixed_garnish_category(isolated_formal_schema):
    lines = [
        {
            "date": "2026-06-21",
            "daypart": "昼",
            "menu_name": "主菜確認メニュー",
            "menu_category": "主菜",
            "diet_type": "regular",
            "source_row_index": 1,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-21",
            "daypart": "昼",
            "menu_name": "ｻﾂﾏ芋",
            "menu_category": "主菜　添え",
            "diet_type": "regular",
            "source_row_index": 2,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-21",
            "daypart": "昼",
            "menu_name": "茄子と揚げの田舎煮",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 3,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-21",
            "daypart": "昼",
            "menu_name": "ピーマンと人参のお浸し",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 4,
            "quantity_original": 3,
        },
    ]

    entries = authored_entries([
        ("2026-06-21", "昼", 0, "主菜確認メニュー", "主菜", "regular"),
        ("2026-06-21", "昼", 1, "茄子と揚げの田舎煮", "副菜", "regular"),
        ("2026-06-21", "昼", 2, "ピーマンと人参のお浸し", "副菜", "regular"),
    ])
    # The standalone accessory is intentionally not a physical monthly row.
    bound = captured_overrides([lines[0], *lines[2:]], entries,
        week="2026-06@2026-06-21~2026-06-21", source_indexes=[0, 1, 2], physical_amount=(1, "g"))
    enriched = output_builder._apply_label_meal_slot_categories([bound[0], lines[1], *bound[1:]])
    labels, _fields, _label_format = output_builder._build_label_rows(
        [
            {**line, "facility": "FAC00009", "quantity": line["quantity_original"], "area_name": "Synthetic area", "area_id": "2F"}
            for line in enriched
        ],
        {},
        None,
    )

    garnish = next(row for row in labels if row["商品名１"] == "ｻﾂﾏ芋")
    eggplant = next(row for row in labels if row["商品名１"] == "茄子と揚げの田舎煮")
    pepper = next(row for row in labels if row["商品名１"] == "ピーマンと人参のお浸し")
    assert garnish["メニュー"] == "主菜 添え"
    assert eggplant["メニュー"] == "副菜①"
    assert pepper["メニュー"] == "副菜②"


def test_label_meal_slots_are_applied_after_menu_master_defaults(monkeypatch):
    lines = [
        {
            "date": "2026-06-15",
            "daypart": "朝",
            "menu_name": "竹輪の卵炒め",
            "menu_category": "副菜1",
            "diet_type": "regular",
            "source_row_index": 1,
            "quantity_original": 3,
        },
        {
            "date": "2026-06-15",
            "daypart": "朝",
            "menu_name": "ブロッコリーのおかか和え",
            "menu_category": "副菜2",
            "diet_type": "regular",
            "source_row_index": 2,
            "quantity_original": 3,
        },
    ]

    monkeypatch.setattr(
        output_builder,
        "_resolve_current_menu_defaults",
        lambda _names, _facility_id: {
            "竹輪の卵炒め": {"category": "主菜"},
            "ブロッコリーのおかか和え": {"category": "副菜2"},
        },
    )

    after_defaults = output_builder._apply_menu_master_defaults(lines, "FAC00009")
    assert after_defaults[0]["menu_category"] == "主菜"

    enriched = output_builder._apply_label_meal_slot_categories(after_defaults)
    labels, _fields, _label_format = output_builder._build_label_rows(
        [
            {**line, "facility": "FAC00009", "quantity": line["quantity_original"], "area_name": "Synthetic area", "area_id": "2F"}
            for line in enriched
        ],
        {},
        None,
    )

    chikuwa = next(row for row in labels if row["商品名１"] == "竹輪の卵炒め")
    broccoli = next(row for row in labels if row["商品名１"] == "ブロッコリーのおかか和え")
    assert chikuwa["メニュー"] == "副菜①"
    assert broccoli["メニュー"] == "副菜②"


def test_monthly_menu_item_can_override_mixer_unit_without_changing_regular_unit():
    lines = [
        {
            "date": "2026-06-10",
            "daypart": "夕",
            "menu_name": "玉子焼き",
            "menu_category": "主菜",
            "diet_type": "regular",
            "quantity_original": 4,
        },
        {
            "date": "2026-06-10",
            "daypart": "夕",
            "menu_name": "玉子焼き",
            "menu_category": "主菜",
            "diet_type": "mixer",
            "quantity_original": 4,
        },
    ]
    items = [
        {
            "id": "MMI-tamago-regular",
            "name": "玉子焼き",
            "daypart": "夕食",
            "category": "主菜",
            "diet_type": "regular",
            "unit_type": "count",
            "qty_per_serving": 1,
            "temp_type": "hot",
        },
        {
            "id": "MMI-tamago-mixer",
            "name": "玉子焼き",
            "daypart": "夕食",
            "category": "主菜",
            "diet_type": "mixer",
            "unit_type": "g",
            "qty_per_serving": 80,
            "temp_type": "hot",
        },
    ]

    enriched = output_builder._apply_menu_overrides(lines, items)
    labels, _fields, _label_format = output_builder._build_label_rows(
        [
            {**enriched[0], "facility": "FAC00009", "quantity": 4, "area_name": "Synthetic area", "area_id": "2F"},
            {**enriched[1], "facility": "FAC00009", "quantity": 4, "area_name": "Synthetic area", "area_id": "2F"},
        ],
        {},
        None,
    )

    regular_label = next(row for row in labels if row["商品名１"] == "玉子焼き" and row["メニュー"] == "主菜")
    mixer_label = next(row for row in labels if row["商品名１"] == "玉子焼き" and row["メニュー"] == "主菜（ミキサー）")
    assert regular_label["内容量"] == "4個"
    assert regular_label["内容詳細"] == "1個"
    assert mixer_label["内容量"] == "320g"
    assert mixer_label["内容詳細"] == "80g"


def test_monthly_menu_metadata_falls_back_to_same_daypart_for_variant_diets_and_categories():
    lines = [
        {
            "date": "2026-06-07",
            "daypart": "夕",
            "menu_name": "カレイの照焼き",
            "menu_category": "主菜",
            "diet_type": "regular",
            "quantity_original": 3,
        },
        {
            "date": "2026-06-07",
            "daypart": "夕",
            "menu_name": "カレイの照焼き",
            "menu_category": "主菜",
            "diet_type": "soft",
            "quantity_original": 5,
        },
        {
            "date": "2026-06-07",
            "daypart": "夕",
            "menu_name": "玉子焼き",
            "menu_category": "主菜",
            "diet_type": "mixer",
            "quantity_original": 4,
        },
    ]

    enriched = output_builder._apply_menu_overrides(
        lines,
        [
            {
                "id": "MMI-karei",
                "name": "カレイの照焼き 添）小松菜",
                "daypart": "夕食",
                "category": "主菜",
                "diet_type": "regular",
                "unit_type": "g",
                "qty_per_serving": 100,
                "temp_type": "hot",
            },
            {
                "id": "MMI-tamago",
                "name": "玉子焼き",
                "daypart": "夕食",
                "category": "副菜",
                "diet_type": "regular",
                "unit_type": "count",
                "qty_per_serving": 2,
                "temp_type": "hot",
            },
        ],
    )

    soft_karei = next(line for line in enriched if line["menu_name"] == "カレイの照焼き" and line["diet_type"] == "soft")
    assert soft_karei["menu_temp_type"] == "hot"
    assert soft_karei["menu_unit_type"] == "g"

    mixer_tamago = next(line for line in enriched if line["menu_name"] == "玉子焼き")
    assert mixer_tamago["menu_temp_type"] == "hot"
    assert mixer_tamago["menu_unit_type"] == "count"
    assert mixer_tamago["menu_qty_per_serving"] == 2

    labels, _fields, _label_format = output_builder._build_label_rows(
        [
            {
                **soft_karei,
                "facility": "FAC00009",
                "quantity": 5,
                "area_name": "Synthetic area", "area_id": "2F",
            },
            {
                **mixer_tamago,
                "facility": "FAC00009",
                "quantity": 4,
                "area_name": "Synthetic area", "area_id": "2F",
            },
        ],
        {},
        None,
    )
    karei_label = next(row for row in labels if row["商品名１"] == "カレイの照焼き")
    assert karei_label["温・冷"] == "温菜"
    tamago_label = next(row for row in labels if row["商品名１"] == "玉子焼き")
    assert tamago_label["内容量"] == "8個"
    assert tamago_label["内容詳細"] == "2個"


def test_monthly_menu_item_qty_survives_entry_category_mismatch_and_garnish_alias():
    lines = [
        {
            "date": "2026-06-10",
            "daypart": "夕",
            "menu_name": "玉子焼き",
            "menu_category": "主菜",
            "diet_type": "mixer",
            "quantity_original": 4,
        },
        {
            "date": "2026-06-12",
            "daypart": "昼",
            "menu_name": "豆腐ﾊﾝﾊﾞｰｸﾞ",
            "menu_category": "主菜",
            "diet_type": "soft",
            "quantity_original": 5,
        },
    ]
    entries = [
        {
            "menu_date": "2026-06-10",
            "daypart": "夕食",
            "name": "玉子焼き",
            "category": "副菜",
            "slot_index": 2,
            "diet_type": "mixer",
        },
        {
            "menu_date": "2026-06-12",
            "daypart": "昼食",
            "name": "豆腐ﾊﾝﾊﾞｰｸﾞ　添)おろしｿｰｽ",
            "category": "主菜",
            "slot_index": 1,
            "diet_type": "soft",
        },
    ]
    items = [
        {
            "id": "MMI-tamago",
            "name": "玉子焼き",
            "daypart": "夕食",
            "category": "副菜",
            "diet_type": "regular",
            "unit_type": "count",
            "qty_per_serving": 2,
            "temp_type": "hot",
        },
        {
            "id": "MMI-tofu",
            "name": "豆腐ﾊﾝﾊﾞｰｸﾞ　添)おろしｿｰｽ",
            "daypart": "昼食",
            "category": "主菜",
            "diet_type": "regular",
            "unit_type": "count",
            "qty_per_serving": 2,
            "temp_type": "hot",
        },
    ]

    enriched = output_builder._apply_menu_entry_overrides(lines, entries)
    enriched = output_builder._apply_menu_overrides(enriched, items)
    enriched = output_builder._clear_stale_menu_qty_from_monthly_entry(enriched)

    labels, _fields, _label_format = output_builder._build_label_rows(
        [
            {**enriched[0], "facility": "FAC00009", "quantity": 4, "area_name": "Synthetic area", "area_id": "2F"},
            {**enriched[1], "facility": "FAC00009", "quantity": 5, "area_name": "Synthetic area", "area_id": "2F"},
        ],
        {},
        None,
    )

    tamago_label = next(row for row in labels if row["商品名１"] == "玉子焼き")
    assert tamago_label["内容量"] == "8個"
    assert tamago_label["内容詳細"] == "2個"
    tofu_label = next(row for row in labels if row["商品名１"] == "豆腐ﾊﾝﾊﾞｰｸﾞ")
    assert tofu_label["内容量"] == "10個"
    assert tofu_label["内容詳細"] == "2個"


def test_bag_level_monthly_menu_item_qty_overrides_stale_materialized_amounts(monkeypatch, tmp_path, isolated_formal_schema):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src import db
    from sqlalchemy import select
    from src.db import session_scope
    from src.models.menu import MonthlyMenuItem
    from src.models.order import Order
    from src.services import order_workflow_v2_service as workflow

    bags = []
    for index, (day, daypart, name, diet, area, quantity) in enumerate([
        ("2026-06-12", "昼", "豆腐ﾊﾝﾊﾞｰｸﾞ　添)おろしｿｰｽ", "soft", "2F", 5),
        ("2026-06-10", "夕", "玉子焼き", "mixer", "3F", 4),
    ]):
        owned_path = tmp_path / str(index)
        owned_path.mkdir()
        engine = create_engine(f"sqlite:///{owned_path / 'formal.sqlite3'}")
        try:
            monkeypatch.setenv("DB_URI", str(engine.url))
            monkeypatch.setenv("ALEMBIC_DB_URI", str(engine.url))
            config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            with engine.connect() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            with monkeypatch.context() as owned_patch:
                owned_patch.setattr(db, "SessionLocal", sessionmaker(engine, autoflush=False))
                registered, old_candidate = _actual_bagging_candidate(owned_patch, owned_path, [{
                    "date": day, "daypart": daypart, "menu_name": name, "diet_type": diet,
                    "area_id": area, "quantity_original": quantity, "bag_type": "normal",
                }], menu_qty_per_serving=100, menu_unit_type="g", accessory_masters={"おろしｿｰｽ": {"qty_per_serving": 12, "unit_type": "g"}})
                with session_scope() as session:
                    facility = session.get(Order, registered["id"]).facility_code
                    item = session.execute(select(MonthlyMenuItem).where(
                        MonthlyMenuItem.facility_override == facility,
                        MonthlyMenuItem.diet_type == diet)).scalar_one()
                    item.qty_per_serving = 2
                    item.unit_type = "count"
                state, error = workflow.get_workflow_readonly(registered["id"])
                assert error is None, error
                result, error = workflow.run_bagging(registered["id"], expected_revision=state["revision"])
                assert error is None, error
                candidate = result["bagging_result"]["materialization_candidate"]
                assert old_candidate["output_lines"][0]["menu_qty_per_serving"] == 100
                assert old_candidate["output_lines"][0]["menu_unit_type"] == "g"
                bags.extend(output_builder.build_bag_rows_for_outputs(
                    {"id": registered["id"], "facility": facility}, accepted_candidate=candidate))
        finally:
            engine.dispose()
    labels, _fields, _label_format = output_builder._build_label_rows(bags, {}, None)

    tofu_label = next(row for row in labels if row["商品名１"] == "豆腐ﾊﾝﾊﾞｰｸﾞ")
    assert tofu_label["内容量"] == "10個"
    assert tofu_label["内容詳細"] == "2個"
    sauce_label = next(row for row in labels if row["商品名１"] == "おろしｿｰｽ")
    assert sauce_label["内容量"] == "60g"
    assert sauce_label["内容詳細"] == "12g"
    tamago_label = next(row for row in labels if row["商品名１"] == "玉子焼き")
    assert tamago_label["内容量"] == "8個"
    assert tamago_label["内容詳細"] == "2個"


def test_daily_labels_sort_by_menu_before_floor_and_keep_diet_order():
    labels, _fields, _label_format = output_builder._build_label_rows(
        [
            {
                "facility": "FAC00009",
                "date": "2026-05-24",
                "daypart": "昼",
                "menu_name": "豚肉の生姜炒め",
                "menu_category": "主菜",
                "diet_type": "mixer",
                "area_id": "2F",
                "quantity": 2,
                "menu_qty_per_serving": 100,
                "menu_unit_type": "g",
                "_source_refs": [{"source_row_index": 12}],
                "area_name": "２階",
            },
            {
                "facility": "FAC00009",
                "date": "2026-05-24",
                "daypart": "昼",
                "menu_name": "オクラのおろし和え",
                "menu_category": "副菜①",
                "diet_type": "regular",
                "area_id": "2F",
                "quantity": 3,
                "menu_qty_per_serving": 40,
                "menu_unit_type": "g",
                "_source_refs": [{"source_row_index": 13}],
                "area_name": "２階",
            },
            {
                "facility": "FAC00009",
                "date": "2026-05-24",
                "daypart": "昼",
                "menu_name": "豚肉の生姜炒め",
                "menu_category": "主菜",
                "diet_type": "regular",
                "area_id": "3F",
                "quantity": 5,
                "menu_qty_per_serving": 100,
                "menu_unit_type": "g",
                "_source_refs": [{"source_row_index": 12}],
                "area_name": "３階",
            },
            {
                "facility": "FAC00009",
                "date": "2026-05-24",
                "daypart": "昼",
                "menu_name": "豚肉の生姜炒め",
                "menu_category": "主菜",
                "diet_type": "soft",
                "area_id": "2F",
                "quantity": 3,
                "menu_qty_per_serving": 100,
                "menu_unit_type": "g",
                "_source_refs": [{"source_row_index": 12}],
                "area_name": "２階",
            },
            {
                "facility": "FAC00009",
                "date": "2026-05-24",
                "daypart": "昼",
                "menu_name": "豚肉の生姜炒め",
                "menu_category": "主菜",
                "diet_type": "regular",
                "area_id": "2F",
                "quantity": 3,
                "menu_qty_per_serving": 100,
                "menu_unit_type": "g",
                "_source_refs": [{"source_row_index": 12}],
                "area_name": "２階",
            },
            {
                "facility": "FAC00009",
                "date": "2026-05-24",
                "daypart": "昼",
                "menu_name": "豚肉の生姜炒め",
                "menu_category": "主菜",
                "diet_type": "mixer",
                "area_id": "3F",
                "quantity": 2,
                "menu_qty_per_serving": 100,
                "menu_unit_type": "g",
                "_source_refs": [{"source_row_index": 12}],
                "area_name": "３階",
            },
            {
                "facility": "FAC00009",
                "date": "2026-05-24",
                "daypart": "昼",
                "menu_name": "豚肉の生姜炒め",
                "menu_category": "主菜",
                "diet_type": "soft",
                "area_id": "3F",
                "quantity": 2,
                "menu_qty_per_serving": 100,
                "menu_unit_type": "g",
                "_source_refs": [{"source_row_index": 12}],
                "area_name": "３階",
            },
        ],
        {},
        None,
    )

    assert [
        (row["商品名１"], row["メニュー"], row["時間"])
        for row in labels
    ] == [
        ("豚肉の生姜炒め", "主菜", "昼２階"),
        ("豚肉の生姜炒め", "主菜", "昼３階"),
        ("豚肉の生姜炒め", "主菜（軟菜）", "昼２階"),
        ("豚肉の生姜炒め", "主菜（軟菜）", "昼３階"),
        ("豚肉の生姜炒め", "主菜（ミキサー）", "昼２階"),
        ("豚肉の生姜炒め", "主菜（ミキサー）", "昼３階"),
        ("オクラのおろし和え", "副菜①", "昼２階"),
    ]


def _actual_bagging_candidate(monkeypatch, tmp_path, lines, **authored):
    from sqlalchemy import select
    from src.db import session_scope
    from src.models.order_output_artifact import OrderBaggingResult
    from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order
    registered = accepted_bagging_order(monkeypatch, tmp_path, lines, **authored)
    with session_scope() as session:
        receipt = session.execute(select(OrderBaggingResult).where(OrderBaggingResult.order_id == registered["id"])).scalar_one()
        from copy import deepcopy
        return registered, deepcopy(receipt.payload_json["materialization_candidate"])


def test_output_diet_type_override_maps_diabetes_to_regular(monkeypatch, tmp_path, isolated_formal_schema):
    registered, candidate = _actual_bagging_candidate(monkeypatch, tmp_path, [{"date": "2026-05-10", "daypart": "朝", "menu_name": "ごぼうと竹輪の煮物", "diet_type": "diabetes", "area_id": "X", "quantity_original": 4, "bag_type": "normal"}], output_diet_types={"diabetes": "regular"})
    lines = output_builder.build_order_lines_for_outputs({"id": registered["id"]}, accepted_candidate=candidate)
    assert lines[0]["diet_type"] == "regular"
    assert lines[0]["source_diet_type"] == "diabetes"


def test_output_diet_type_is_unchanged_without_facility_override(monkeypatch, tmp_path, isolated_formal_schema):
    registered, candidate = _actual_bagging_candidate(monkeypatch, tmp_path, [{"date": "2026-05-10", "daypart": "朝", "menu_name": "ごぼうと竹輪の煮物", "diet_type": "diabetes", "area_id": "X", "quantity_original": 4, "bag_type": "normal"}])
    lines = output_builder.build_order_lines_for_outputs({"id": registered["id"]}, accepted_candidate=candidate)
    assert lines[0]["diet_type"] == "diabetes"
    assert "source_diet_type" not in lines[0]




def test_monthly_entry_override_keeps_regular_and_soft_mixer_menu_items(monkeypatch, tmp_path, isolated_formal_schema):
    facility_config = {
            "fax_template_override": {
                "columns": [
                    {
                        "role": "quantity",
                        "diet_type": "soft",
                        "area_id": "2F",
                        "output_diet_type": "regular",
                    },
                    {
                        "role": "quantity",
                        "diet_type": "mixer",
                        "area_id": "3F",
                        "output_diet_type": "regular",
                    },
                ]
            }
        }
    configured = publish_configuration(monkeypatch, tmp_path, day="2026-07-12",
        columns=facility_config["fax_template_override"]["columns"])
    entries = [
            {
                "menu_date": "2026-07-12",
                "daypart": "夕食",
                "slot_index": 0,
                "name": "前日主菜",
                "category": "主菜",
                "diet_type": "regular",
            },
            {
                "menu_date": "2026-07-13",
                "daypart": "夕食",
                "slot_index": 0,
                "name": "チキン南蛮　添）ｷｬﾍﾞﾂ",
                "category": "主菜",
                "diet_type": "regular",
            },
            {
                "menu_date": "2026-07-13",
                "daypart": "夕食",
                "slot_index": 0,
                "name": "焼きチキン南蛮　添）ｷｬﾍﾞﾂ",
                "category": "主菜",
                "diet_type": "soft_mixer",
                "facility_override": "FAC00009",
            },
            {
                "menu_date": "2026-07-13",
                "daypart": "夕食",
                "slot_index": 1,
                "name": "豆腐の煮物",
                "category": "副菜",
                "diet_type": "regular",
            },
            {
                "menu_date": "2026-07-13",
                "daypart": "夕食",
                "slot_index": 2,
                "name": "ブロッコリーｺﾞﾏﾄﾞﾚ和え",
                "category": "副菜",
                "diet_type": "regular",
            },
        ]
    items = [
            {
                "name": "チキン南蛮　添）ｷｬﾍﾞﾂ",
                "unit_type": "count",
                "qty_per_serving": 2,
                "temp_type": "hot",
                "daypart": "夕食",
                "category": "主菜",
                "diet_type": "regular",
            },
            {
                "name": "焼きチキン南蛮　添）ｷｬﾍﾞﾂ",
                "unit_type": "count",
                "qty_per_serving": 2,
                "temp_type": "hot",
                "daypart": "夕食",
                "category": "主菜",
                "diet_type": "soft_mixer",
            },
            {
                "name": "豆腐の煮物",
                "unit_type": "g",
                "qty_per_serving": 100,
                "temp_type": "hot",
                "daypart": "夕食",
                "category": "副菜",
                "diet_type": "regular",
            },
        ]
    publish_sources(entries, items=items, physical_amount=(1, "g"))

    lines = produce(
        {
            "id": "ORD-test",
            "facility": "FAC00009",
            "week_value": "2026-07@2026-07-12~2026-07-18",
            "lines": [
                {
                    "date": "2026-07-13",
                    "daypart": "夕",
                    "menu_name": "焼きチキン南蛮",
                    "menu_category": "主菜",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "quantity_original": 4,
                    "source_row_index": 1,
                },
                {
                    "date": "2026-07-13",
                    "daypart": "夕",
                    "menu_name": "チキン南蛮",
                    "menu_category": "主菜",
                    "diet_type": "soft",
                    "area_id": "2F",
                    "quantity_original": 3,
                    "source_row_index": 1,
                },
                {
                    "date": "2026-07-13",
                    "daypart": "夕",
                    "menu_name": "チキン南蛮",
                    "menu_category": "主菜",
                    "diet_type": "mixer",
                    "area_id": "3F",
                    "quantity_original": 2,
                    "source_row_index": 1,
                },
                {
                    "date": "2026-07-13",
                    "daypart": "夕",
                    "menu_name": "豆腐の煮物",
                    "menu_category": "副菜",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "quantity_original": 4,
                    "source_row_index": 2,
                },
                {
                    "date": "2026-07-13",
                    "daypart": "夕",
                    "menu_name": "ブロッコリーｺﾞﾏﾄﾞﾚ和え",
                    "menu_category": "副菜",
                    "diet_type": "regular",
                    "area_id": "2F",
                    "quantity_original": 4,
                    "source_row_index": 3,
                },
            ],
        }, configured_input=configured,
    )

    assert lines[0]["menu_name"] == "チキン南蛮"
    assert lines[0]["menu_category"] == "主菜"
    assert lines[0]["menu_unit_type"] == "count"
    assert lines[0]["menu_qty_per_serving"] == 2
    assert lines[1]["menu_name"] == "焼きチキン南蛮"
    assert lines[1]["menu_category"] == "主菜"
    assert lines[1]["menu_unit_type"] == "count"
    assert lines[1]["menu_qty_per_serving"] == 2
    assert lines[1]["diet_type"] == "regular"
    assert lines[1]["source_diet_type"] == "soft"
    assert lines[2]["menu_name"] == "焼きチキン南蛮"
    assert lines[2]["menu_category"] == "主菜"
    assert lines[2]["menu_unit_type"] == "count"
    assert lines[2]["menu_qty_per_serving"] == 2
    assert lines[2]["diet_type"] == "regular"
    assert lines[2]["source_diet_type"] == "mixer"
    assert lines[3]["menu_name"] == "豆腐の煮物"
    assert lines[3]["menu_category"] == "副菜1"
    assert lines[4]["menu_name"] == "ブロッコリーｺﾞﾏﾄﾞﾚ和え"
    assert lines[4]["menu_category"] == "副菜2"

    bags = output_builder.build_bag_rows_for_outputs(
        {
            "id": "ORD-test",
            "facility": "FAC00009",
            "week_value": "2026-07@2026-07-12~2026-07-18",
        },
        order_lines=lines,
        facility_config=configured["facility"],
        configured_input=configured,
    )
    labels, _fields, label_format = output_builder._build_label_rows(bags, {}, None)
    assert label_format == "jp"

    chikin_labels = [row for row in labels if row["商品名１"] in {"チキン南蛮", "焼きチキン南蛮"}]
    assert all(row["メニュー"].startswith("主菜") for row in chikin_labels)
    assert all(row["内容詳細"] == "2個" for row in chikin_labels)
    assert any(row["商品名１"] == "チキン南蛮" and row["メニュー"] == "主菜" for row in chikin_labels)
    assert any(
        row["商品名１"] == "焼きチキン南蛮" and row["メニュー"] == "主菜（軟菜）"
        for row in chikin_labels
    )
    assert any(
        row["商品名１"] == "焼きチキン南蛮" and row["メニュー"] == "主菜（ミキサー）"
        for row in chikin_labels
    )
    assert not any(row["商品名１"] == "チキン南蛮" and row["メニュー"].startswith("副菜") for row in labels)

    tofu_label = next(row for row in labels if row["商品名１"] == "豆腐の煮物")
    assert tofu_label["メニュー"] == "副菜①"
    broccoli_label = next(row for row in labels if row["商品名１"] == "ブロッコリーｺﾞﾏﾄﾞﾚ和え")
    assert broccoli_label["メニュー"] == "副菜②"


def test_monthly_entry_override_blocks_unresolved_source_row(monkeypatch, tmp_path, isolated_formal_schema):
    configured = publish_configuration(monkeypatch, tmp_path, day="2026-07-13")
    publish_sources([
            {
                "menu_date": "2026-07-13",
                "daypart": "夕食",
                "slot_index": 0,
                "name": "チキン南蛮　添）ｷｬﾍﾞﾂ",
                "category": "主菜",
                "diet_type": "regular",
            }
        ], physical_amount=(1, "g"))

    base_order = {
        "id": "ORD-test",
        "facility": "FAC00009",
        "week_value": "2026-07@2026-07-12~2026-07-18",
    }

    with pytest.raises(ValueError, match="monthly_entry_source_row_unresolved"):
        produce(
            {
                **base_order,
                "lines": [
                    {
                        "date": "2026-07-13",
                        "daypart": "夕",
                        "menu_name": "チキン南蛮",
                        "menu_category": "主菜",
                        "diet_type": "soft",
                        "area_id": "2F",
                        "quantity_original": 3,
                        "source_row_index": 99,
                    }
                ],
            }, configured_input=configured,
        )
    with pytest.raises(ValueError, match="monthly_entry_source_row_unresolved"):
        produce(
            {
                **base_order,
                "lines": [
                    {
                        "date": None,
                        "daypart": "夕",
                        "menu_name": "チキン南蛮",
                        "menu_category": "主菜",
                        "diet_type": "soft",
                        "area_id": "2F",
                        "quantity_original": 3,
                        "source_row_index": 0,
                    }
                ],
            }, configured_input=configured,
        )


def test_monthly_entry_physical_rows_keep_diet_specific_rows_with_global_source_indexes():
    entries = [
        {
            "menu_date": "2026-07-12",
            "daypart": "夕食",
            "slot_index": idx,
            "name": f"前日{idx}",
            "category": "副菜",
            "diet_type": "regular",
        }
        for idx in range(8)
    ]
    entries.extend(
        [
            {
                "menu_date": "2026-07-13",
                "daypart": "朝食",
                "slot_index": idx,
                "name": f"朝{idx}",
                "category": "副菜",
                "diet_type": "regular",
            }
            for idx in range(2)
        ]
    )
    entries.extend(
        [
            {
                "menu_date": "2026-07-13",
                "daypart": "昼食",
                "slot_index": idx,
                "name": f"昼{idx}",
                "category": "副菜",
                "diet_type": "regular",
            }
            for idx in range(3)
        ]
    )
    entries.extend(
        [
            {
                "menu_date": "2026-07-13",
                "daypart": "夕食",
                "slot_index": 1,
                "name": "チキン南蛮　添）ｷｬﾍﾞﾂ",
                "category": "主菜",
                "diet_type": "regular",
            },
            {
                "menu_date": "2026-07-13",
                "daypart": "夕食",
                "slot_index": 1,
                "name": "焼きチキン南蛮　添）ｷｬﾍﾞﾂ",
                "category": "主菜",
                "diet_type": "soft_mixer",
                "facility_override": "FAC00009",
            },
            {
                "menu_date": "2026-07-13",
                "daypart": "夕食",
                "slot_index": 2,
                "name": "豆腐の煮物",
                "category": "副菜",
                "diet_type": "regular",
            },
            {
                "menu_date": "2026-07-13",
                "daypart": "夕食",
                "slot_index": 3,
                "name": "ブロッコリーｺﾞﾏﾄﾞﾚ和え",
                "category": "副菜",
                "diet_type": "regular",
            },
        ]
    )
    lines = [
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "焼きチキン南蛮　添）ｷｬﾍﾞﾂ",
            "diet_type": "soft",
            "source_row_index": 13,
        },
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "チキン南蛮　添）ｷｬﾍﾞﾂ",
            "diet_type": "regular",
            "source_row_index": 13,
        },
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "豆腐の煮物",
            "diet_type": "regular",
            "source_row_index": 14,
        },
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "ブロッコリーｺﾞﾏﾄﾞﾚ和え",
            "diet_type": "regular",
            "source_row_index": 15,
        },
    ]

    physical_rows = output_builder._build_monthly_entry_physical_rows(entries, lines)

    assert len(physical_rows) == 16
    assert len(physical_rows[13]) == 2
    assert output_builder._build_monthly_entry_physical_rows(entries, [lines[3]]) == physical_rows

    assert output_builder._resolve_monthly_entry_by_source_row(lines[0], physical_rows)["name"] == "焼きチキン南蛮　添）ｷｬﾍﾞﾂ"
    assert output_builder._resolve_monthly_entry_by_source_row(lines[1], physical_rows)["name"] == "チキン南蛮　添）ｷｬﾍﾞﾂ"
    assert output_builder._resolve_monthly_entry_by_source_row(lines[2], physical_rows)["name"] == "豆腐の煮物"
    assert output_builder._resolve_monthly_entry_by_source_row(lines[3], physical_rows)["name"] == "ブロッコリーｺﾞﾏﾄﾞﾚ和え"
    assert output_builder._resolve_monthly_entry_by_source_row(
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "焼きチキン南蛮　添）ｷｬﾍﾞﾂ",
            "diet_type": "regular",
            "source_row_index": 13,
        },
        physical_rows,
    )["name"] == "チキン南蛮　添）ｷｬﾍﾞﾂ"
    assert output_builder._resolve_monthly_entry_by_source_row(
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "チキン南蛮　添）ｷｬﾍﾞﾂ",
            "diet_type": "soft",
            "source_row_index": 13,
        },
        physical_rows,
    )["name"] == "焼きチキン南蛮　添）ｷｬﾍﾞﾂ"


@pytest.mark.parametrize("index", [-1, 1, True, 0.0, "0"])
def test_captured_physical_source_index_rejects_recovery(index):
    entries = [{"menu_date": "2026-07-13", "daypart": "昼", "slot_index": 0, "name": "own", "diet_type": "regular"}]
    with pytest.raises(ValueError, match="monthly_entry_source_row_unresolved"):
        output_builder._build_monthly_entry_physical_rows(entries, [{"source_row_index": index}])
    rows = output_builder._build_monthly_entry_physical_rows(entries)
    with pytest.raises(ValueError, match="monthly_entry_source_row_unresolved"):
        output_builder._resolve_monthly_entry_by_source_row({"source_row_index": index}, rows)


@pytest.mark.parametrize("slot", [None, -1, True, 0.0, "0"])
def test_captured_physical_slot_has_no_ordinal_substitution(slot):
    with pytest.raises(ValueError, match="monthly_entry_source_row_unresolved"):
        output_builder._build_monthly_entry_physical_rows([{"menu_date": "2026-07-13", "daypart": "昼", "slot_index": slot, "diet_type": "regular"}])


def test_captured_physical_same_diet_duplicate_and_ambiguous_choice_stop():
    base = {"menu_date": "2026-07-13", "daypart": "昼", "slot_index": 0, "name": "own", "diet_type": "regular"}
    with pytest.raises(ValueError, match="monthly_entry_source_row_unresolved"):
        output_builder._build_monthly_entry_physical_rows([base, dict(base, name="contradiction")])
    rows = output_builder._build_monthly_entry_physical_rows([dict(base, diet_type="soft"), dict(base, diet_type="mixer")])
    with pytest.raises(ValueError, match="monthly_entry_source_row_unresolved"):
        output_builder._resolve_monthly_entry_by_source_row({"source_row_index": 0, "diet_type": "soft_mixer"}, rows)


def test_label_slot_categories_ignore_excluded_split_monthly_lines(isolated_formal_schema):
    lines = [
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "焼きチキン南蛮",
            "menu_category": "主菜",
            "diet_type": "regular",
            "source_row_index": 13,
            "_exclude_from_outputs": True,
            "_monthly_entry_override_applied": True,
            "_monthly_entry_raw_category": "主菜",
        },
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "焼きチキン南蛮",
            "menu_category": "主菜",
            "diet_type": "soft",
            "source_row_index": 13,
            "_monthly_entry_override_applied": True,
            "_monthly_entry_raw_category": "主菜",
        },
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "チキン南蛮",
            "menu_category": "主菜",
            "diet_type": "regular",
            "source_row_index": 14,
            "_monthly_entry_override_applied": True,
            "_monthly_entry_raw_category": "主菜",
        },
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "チキン南蛮",
            "menu_category": "主菜",
            "diet_type": "soft",
            "source_row_index": 14,
            "_exclude_from_outputs": True,
            "_monthly_entry_override_applied": True,
            "_monthly_entry_raw_category": "主菜",
        },
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "豆腐の煮物",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 15,
        },
        {
            "date": "2026-07-13",
            "daypart": "夕",
            "menu_name": "ブロッコリーｺﾞﾏﾄﾞﾚ和え",
            "menu_category": "副菜",
            "diet_type": "regular",
            "source_row_index": 16,
        },
    ]

    entries = authored_entries([
        ("2026-07-13", "夕", 0, "焼きチキン南蛮", "主菜", "regular"),
        ("2026-07-13", "夕", 1, "チキン南蛮", "主菜", "regular"),
        ("2026-07-13", "夕", 2, "豆腐の煮物", "副菜", "regular"),
        ("2026-07-13", "夕", 3, "ブロッコリーｺﾞﾏﾄﾞﾚ和え", "副菜", "regular"),
    ])
    bound = captured_overrides(lines, entries,
        week="2026-07@2026-07-13~2026-07-13", source_indexes=[0, 0, 1, 1, 2, 3], physical_amount=(1, "g"))
    enriched = output_builder._apply_label_meal_slot_categories(bound)

    assert enriched[1]["menu_category"] == "主菜"
    assert enriched[2]["menu_category"] == "主菜"
    assert enriched[4]["menu_category"] == "副菜1"
    assert enriched[5]["menu_category"] == "副菜2"


def test_monthly_entry_override_regular_row_applies_to_soft_mixer_only(monkeypatch, tmp_path, isolated_formal_schema):
    configured = publish_configuration(monkeypatch, tmp_path, day="2026-07-13")
    publish_sources([
            {
                "menu_date": "2026-07-13",
                "daypart": "夕食",
                "slot_index": 1,
                "name": "豆腐の煮物",
                "category": "副菜",
                "diet_type": "regular",
            }
        ], physical_amount=(1, "g"))
    base_order = {
        "id": "ORD-test",
        "facility": "FAC00009",
        "week_value": "2026-07@2026-07-12~2026-07-18",
    }

    soft_lines = produce(
        {
            **base_order,
            "lines": [
                {
                    "date": "2026-07-13",
                    "daypart": "夕",
                    "menu_name": "豆腐の煮物",
                    "menu_category": "副菜",
                    "diet_type": "soft",
                    "area_id": "2F",
                    "quantity_original": 3,
                    "source_row_index": 0,
                }
            ],
        }, configured_input=configured,
    )
    assert soft_lines[0]["menu_name"] == "豆腐の煮物"

    with pytest.raises(ValueError, match="monthly_entry_source_row_unresolved"):
        produce(
            {
                **base_order,
                "lines": [
                    {
                        "date": "2026-07-13",
                        "daypart": "夕",
                        "menu_name": "豆腐の煮物",
                        "menu_category": "副菜",
                        "diet_type": "diabetes",
                        "area_id": "2F",
                        "quantity_original": 3,
                        "source_row_index": 0,
                    }
                ],
            }, configured_input=configured,
        )
    with pytest.raises(ValueError, match="monthly_entry_source_row_unresolved"):
        produce(
            {
                **base_order,
                "lines": [
                    {
                        "date": "2026-07-13",
                        "daypart": "夕",
                        "menu_name": "チキン南蛮",
                        "menu_category": "主菜",
                        "diet_type": "soft",
                        "area_id": "2F",
                        "quantity_original": 3,
                        "source_row_index": "not-a-row",
                    }
                ],
            }, configured_input=configured,
        )


def test_position_menu_entries_collapse_diet_specific_rows_to_physical_menu_rows(isolated_formal_schema):
    publish_sources([
                        {
                            "menu_date": "2026-07-13",
                            "daypart": "夕食",
                            "slot_index": 0,
                            "name": "チキン南蛮　添）ｷｬﾍﾞﾂ",
                            "category": "主菜",
                            "diet_type": "regular",
                        },
                        {
                            "menu_date": "2026-07-13",
                            "daypart": "夕食",
                            "slot_index": 0,
                            "name": "焼きチキン南蛮　添）ｷｬﾍﾞﾂ",
                            "category": "主菜",
                            "diet_type": "soft_mixer",
                            "facility_override": "FAC00009",
                        },
                        {
                            "menu_date": "2026-07-13",
                            "daypart": "夕食",
                            "slot_index": 1,
                            "name": "豆腐の煮物",
                            "category": "副菜",
                            "diet_type": "regular",
                        },
    ])

    entries = order_service._build_position_menu_entries("2026-07@2026-07-12~2026-07-18", "FAC00009")

    assert [entry["menu_name"] for entry in entries] == ["チキン南蛮　添）ｷｬﾍﾞﾂ", "豆腐の煮物"]
