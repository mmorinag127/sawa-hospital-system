from src.services import output_builder


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
                "area_id": "2F",
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
                "area_id": "2F",
            },
            {
                **mixer_tamago,
                "facility": "FAC00009",
                "quantity": 4,
                "area_id": "2F",
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
            },
        ],
        {},
        None,
    )

    assert [
        (row["商品名１"], row["メニュー"], row["時間"])
        for row in labels
    ] == [
        ("豚肉の生姜炒め", "主菜", "昼　2階"),
        ("豚肉の生姜炒め", "主菜", "昼　3階"),
        ("豚肉の生姜炒め", "主菜（軟菜）", "昼　2階"),
        ("豚肉の生姜炒め", "主菜（軟菜）", "昼　3階"),
        ("豚肉の生姜炒め", "主菜（ミキサー）", "昼　2階"),
        ("豚肉の生姜炒め", "主菜（ミキサー）", "昼　3階"),
        ("オクラのおろし和え", "副菜①", "昼　2階"),
    ]


def test_output_diet_type_override_maps_diabetes_to_regular(monkeypatch):
    facility_config = {
        "fax_template_override": {
            "columns": [
                {
                    "role": "quantity",
                    "diet_type": "diabetes",
                    "area_id": "X",
                    "output_diet_type": "regular",
                }
            ]
        }
    }
    monkeypatch.setattr(output_builder.config_service, "get_facility_config", lambda _facility_id: facility_config)
    monkeypatch.setattr(output_builder.order_service, "_expanded_cell_same_daypart_copy_enabled", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(output_builder.order_service, "_week_sheet_name_from_week_value", lambda _value: "")
    monkeypatch.setattr(output_builder.order_service, "_apply_change_override_priority_to_lines", lambda lines: lines)
    monkeypatch.setattr(output_builder.order_service, "_collect_menu_entries_for_week", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(output_builder.order_service, "_collect_menu_items_for_week", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(output_builder, "get_order_menu_snapshot", lambda _order_id: {})
    monkeypatch.setattr(output_builder.daily_output_override_service, "apply_overrides_to_lines", lambda lines, _facility_id: lines)

    lines = output_builder.build_order_lines_for_outputs(
        {
            "id": "ORD-test",
            "facility": "FAC00016",
            "week_value": "2026-05",
            "lines": [
                {
                    "date": "2026-05-10",
                    "daypart": "朝",
                    "menu_name": "ごぼうと竹輪の煮物",
                    "diet_type": "diabetes",
                    "area_id": "X",
                    "quantity": 4,
                }
            ],
        }
    )

    assert lines[0]["diet_type"] == "regular"
    assert lines[0]["source_diet_type"] == "diabetes"


def test_output_diet_type_is_unchanged_without_facility_override(monkeypatch):
    monkeypatch.setattr(output_builder.config_service, "get_facility_config", lambda _facility_id: {"fax_template_override": {"columns": []}})
    monkeypatch.setattr(output_builder.order_service, "_expanded_cell_same_daypart_copy_enabled", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(output_builder.order_service, "_week_sheet_name_from_week_value", lambda _value: "")
    monkeypatch.setattr(output_builder.order_service, "_apply_change_override_priority_to_lines", lambda lines: lines)
    monkeypatch.setattr(output_builder.order_service, "_collect_menu_entries_for_week", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(output_builder.order_service, "_collect_menu_items_for_week", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(output_builder, "get_order_menu_snapshot", lambda _order_id: {})
    monkeypatch.setattr(output_builder.daily_output_override_service, "apply_overrides_to_lines", lambda lines, _facility_id: lines)

    lines = output_builder.build_order_lines_for_outputs(
        {
            "id": "ORD-test",
            "facility": "FAC00016",
            "week_value": "2026-05",
            "lines": [
                {
                    "date": "2026-05-10",
                    "daypart": "朝",
                    "menu_name": "ごぼうと竹輪の煮物",
                    "diet_type": "diabetes",
                    "area_id": "X",
                    "quantity": 4,
                }
            ],
        }
    )

    assert lines[0]["diet_type"] == "diabetes"
    assert "source_diet_type" not in lines[0]
