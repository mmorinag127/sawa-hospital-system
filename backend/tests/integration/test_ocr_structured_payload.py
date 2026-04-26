import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service  # noqa: E402
from src.services.fax_extractor import (  # noqa: E402
    _rows_from_pipeline_payload,
    rows_from_structured_payload,
)


def _template() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.soft_x",
            "remarks",
        ]
    }


def _template_staff_daycare() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.daycare_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "remarks",
        ]
    }


def _template_fureai() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
        "columns": [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "aux", "header": "副区分"},
            {"index": 3, "role": "menu_name", "header": "献立"},
            {"index": 4, "role": "aux", "header": "合計"},
            {"index": 5, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
            {"index": 6, "role": "quantity", "header": "通所", "diet_type": "daycare", "area_id": "X"},
            {"index": 7, "role": "quantity", "header": "職員", "diet_type": "staff", "area_id": "X"},
            {"index": 8, "role": "quantity", "header": "肉禁", "diet_type": "no_meat", "area_id": "X"},
            {"index": 9, "role": "quantity", "header": "魚禁", "diet_type": "no_fish", "area_id": "X"},
            {"index": 10, "role": "quantity", "header": "揚げ物禁", "diet_type": "no_fried", "area_id": "X"},
            {"index": 11, "role": "quantity", "header": "変更1", "diet_type": "change_1", "area_id": "X"},
            {"index": 12, "role": "note", "header": "備考欄"},
        ],
    }


def _template_fureai_compact() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ]
    }


def _template_staff_forbidden_sesame() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.forbidden_other_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "remarks",
        ]
    }


def _template_staff_forbidden_sesame_with_aux() -> dict:
    return {
        "columns": [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "aux", "header": "補助区分"},
            {"index": 3, "role": "menu_name", "header": "献立"},
            {"index": 4, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
            {"index": 5, "role": "quantity", "header": "職員", "diet_type": "staff", "area_id": "X"},
            {"index": 6, "role": "quantity", "header": "肉禁", "diet_type": "no_meat", "area_id": "X"},
            {"index": 7, "role": "quantity", "header": "肉卵魚禁", "diet_type": "forbidden_other", "area_id": "X"},
            {"index": 8, "role": "quantity", "header": "魚禁", "diet_type": "no_fish", "area_id": "X"},
            {"index": 9, "role": "quantity", "header": "ゴマアレルギー", "diet_type": "sesame_allergy", "area_id": "X"},
            {"index": 10, "role": "note", "header": "備考欄"},
        ],
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.forbidden_other_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "remarks",
        ],
    }


def _template_staff_forbidden_sesame_with_aux() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.forbidden_other_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "remarks",
        ],
        "columns": [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "aux", "header": "副区分"},
            {"index": 3, "role": "menu_name", "header": "献立"},
            {"index": 4, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
            {"index": 5, "role": "quantity", "header": "職員", "diet_type": "staff", "area_id": "X"},
            {"index": 6, "role": "quantity", "header": "肉禁", "diet_type": "no_meat", "area_id": "X"},
            {"index": 7, "role": "quantity", "header": "肉卵魚禁", "diet_type": "forbidden_other", "area_id": "X"},
            {"index": 8, "role": "quantity", "header": "魚禁", "diet_type": "no_fish", "area_id": "X"},
            {"index": 9, "role": "quantity", "header": "ゴマアレルギー", "diet_type": "sesame_allergy", "area_id": "X"},
            {"index": 10, "role": "note", "header": "備考欄"},
        ],
    }


def _template_diabetes_forbidden_change() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.diabetes_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.change_1_x",
            "qty.change_2_x",
            "remarks",
        ]
    }


def _template_mixed() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.regular_bag_x",
            "qty.soft_x",
            "qty.mixer_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "remarks",
        ]
    }


def _template_drifted_floor_split() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.soft_2f",
            "qty.soft_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
            "remarks",
        ],
        "columns": [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "menu_name", "header": "メニュー"},
            {"index": 3, "role": "quantity", "header": "常食2F", "diet_type": "regular", "area_id": "2F"},
            {"index": 4, "role": "quantity", "header": "常食3F", "diet_type": "regular", "area_id": "3F"},
            {"index": 5, "role": "quantity", "header": "軟菜2F", "diet_type": "soft", "area_id": "2F"},
            {"index": 6, "role": "quantity", "header": "軟菜3F", "diet_type": "soft", "area_id": "3F"},
            {"index": 7, "role": "quantity", "header": "ミキサー2F", "diet_type": "mixer", "area_id": "2F"},
            {"index": 8, "role": "quantity", "header": "ミキサー3F", "diet_type": "mixer", "area_id": "3F"},
            {"index": 9, "role": "note", "header": "備考"},
        ],
    }


def test_rows_from_pipeline_payload_merges_multiple_markdown_tables():
    payload = {
        "table_raw": """
|日付|区分|献立|常食|軟菜|備考|
|-|-|-|-|-|-|
|2/15|朝|じゃが芋のコンソメ煮|20|1||

|日付|区分|献立|常食|軟菜|備考|
|-|-|-|-|-|-|
|2/15|昼|キャベツサラダ|18|2|note|
""".strip()
    }

    rows = _rows_from_pipeline_payload(payload, _template())

    assert rows == [
        ["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""],
        ["2/15", "昼", "キャベツサラダ", "18", "2", "note"],
    ]


def test_rows_from_structured_payload_maps_multi_row_headers_and_merges_tables():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "", "備考"],
                            ["", "", "", "常食", "軟菜", ""],
                            ["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""],
                        ],
                    },
                    {
                        "table_id": "p1_t2",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "", "備考"],
                            ["", "", "", "常食", "軟菜", ""],
                            ["2/15", "昼", "キャベツサラダ", "18", "2", "note"],
                        ],
                    },
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template())

    assert rows == [
        ["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""],
        ["2/15", "昼", "キャベツサラダ", "18", "2", "note"],
    ]


def test_extract_sheet_rows_from_payload_prefers_structured_tables_over_table_raw():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "", "備考"],
                            ["", "", "", "常食", "軟菜", ""],
                            ["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""],
                            ["2/15", "昼", "キャベツサラダ", "18", "2", "note"],
                        ],
                    }
                ],
            }
        ],
        "table_raw": """
|日付|区分|献立|常食|軟菜|備考|
|-|-|-|-|-|-|
|2/15|朝|誤ったメニュー|99|9|wrong|
""".strip(),
    }

    rows = order_service._extract_sheet_rows_from_payload(payload, _template())

    assert rows == [
        ["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""],
        ["2/15", "昼", "キャベツサラダ", "18", "2", "note"],
    ]


def test_extract_sheet_rows_from_payload_merges_roi_overlay_rows():
    payload = {
        "roi_overlay_policy": "merge",
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "軟菜", "備考"],
                            ["2/15", "朝", "じゃが芋のコンソメ煮", "", "", ""],
                            ["2/15", "昼", "キャベツサラダ", "", "", "note"],
                        ],
                    }
                ],
            }
        ],
        "roi_overlay_rows": [
            {"row_index": 0, "qty.regular_x": 12},
            {"row_index": 1, "qty.soft_x": 2},
        ],
    }

    rows = order_service._extract_sheet_rows_from_payload(payload, _template())

    assert rows == [
        ["2/15", "朝", "じゃが芋のコンソメ煮", "12", "", ""],
        ["2/15", "昼", "キャベツサラダ", "", "2", "note"],
    ]


def test_extract_sheet_rows_from_payload_keeps_structured_rows_primary_when_overlay_is_audit_only():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "軟菜", "備考"],
                            ["2/15", "朝", "じゃが芋のコンソメ煮", "7", "", ""],
                            ["2/15", "昼", "キャベツサラダ", "", "3", "note"],
                        ],
                    }
                ],
            }
        ],
        "roi_overlay_rows": [
            {"row_index": 0, "qty.regular_x": 12},
            {"row_index": 1, "qty.soft_x": 2},
        ],
    }

    rows = order_service._extract_sheet_rows_from_payload(payload, _template())

    assert rows == [
        ["2/15", "朝", "じゃが芋のコンソメ煮", "7", "", ""],
        ["2/15", "昼", "キャベツサラダ", "", "3", "note"],
    ]


def test_rows_from_structured_payload_preserves_fureai_total_aux_column_gap():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日 付", "区 分", "", "献立", "合計", "常会", "通所", "職員", "肉禁", "禁食\n魚禁", "埼玉県", "史更1", "備考欄"],
                            ["3/22\n(日)", "材", "副作\n四", "厚揚げとさつま芋の煮物", "", "72", "", "", "", "", "", "", ""],
                            ["", "", "", "カリフラワーサラダ", "", "72", "", "", "", "", "", "", ""],
                            ["", "香", "±A", "鶏じゃが", "67", "66", "", "", "", "", "", "", "鶏魚1"],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_fureai())

    assert rows[0][:6] == ["3/22 (日)", "材", "厚揚げとさつま芋の煮物", "72", "", ""]
    assert rows[1][:6] == ["", "", "カリフラワーサラダ", "72", "", ""]
    assert rows[2][:6] == ["", "香", "鶏じゃが", "66", "", ""]
    assert rows[2][-1] == "鶏魚1"


def test_rows_from_structured_payload_ignores_total_and_helper_aux_columns_for_fureai():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日 付", "区 分", "", "献立", "合計", "#☆", "通所", "職員", "平森", "肉蒸", "魚禁", "揚物禁", "変更1", "備考欄"],
                            ["3/22\n(日)", "材", "副作\n四", "厚揚げとさつま芋の煮物", "", "72", "", "", "", "", "", "", "", ""],
                            ["", "香", "±A", "鶏じゃが", "67", "66", "", "", "", "", "", "", "", "鶏魚1"],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_fureai())

    assert rows[0][:7] == ["3/22 (日)", "材", "厚揚げとさつま芋の煮物", "72", "", "", ""]
    assert rows[1][:7] == ["", "香", "鶏じゃが", "66", "", "", ""]


def test_rows_from_structured_payload_maps_staff_daycare_and_forbidden_subheaders():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "職員", "通所", "禁食", "", "備考"],
                            ["", "", "", "", "", "", "肉禁", "魚禁", ""],
                            ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "note"],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_staff_daycare())

    assert rows == [
        ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "note"],
    ]


def test_rows_from_structured_payload_maps_daycare_staff_and_no_fried_subheaders():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "通所", "職員", "禁食", "", "", "変更1", "備考"],
                            ["", "", "", "", "", "", "肉禁", "魚禁", "揚げ物禁", "", ""],
                            ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "5", "6", "note"],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_fureai_compact())

    assert rows == [
        ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "5", "6", "note"],
    ]


def test_rows_from_structured_payload_preserves_staff_forbidden_other_with_aux_column():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区 分", "", "献立", "常\n食", "職員", "禁食", "", "", "", "備考欄"],
                            ["", "", "", "", "", "", "肉禁", "肉・卵・魚禁", "魚禁", "ゴマアレルギー", ""],
                            ["3/22\n(日)", "", "CH", "厚揚げとさつま芋の煮物", "105", "2", "", "", "", "", ""],
                            ["", "", "W2", "カリフラワーサラダ", "105", "2", "", "", "", "", ""],
                            ["", "", "主A", "鶏じゃが", "102", "2", "2", "", "", "", ""],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_staff_forbidden_sesame_with_aux())

    assert rows[0] == ["3/22 (日)", "", "厚揚げとさつま芋の煮物", "105", "2", "", "", "", "", ""]
    assert rows[1] == ["", "", "カリフラワーサラダ", "105", "2", "", "", "", "", ""]
    assert rows[2] == ["", "", "鶏じゃが", "102", "2", "2", "", "", "", ""]


def test_rows_from_structured_payload_maps_mixed_regular_bag_and_forbidden_subheaders():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "", "軟菜", "ミキサー", "禁食", "", "備考"],
                            ["", "", "", "通常", "袋分け", "", "", "肉禁", "魚禁", ""],
                            ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "5", "note"],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_mixed())

    assert rows == [
        ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "5", "note"],
    ]


def test_rows_from_structured_payload_prefers_observed_projection_when_explicit_columns_shift_menu_column():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["", "日付", "区 分", "", "献立", "##", "44日", "禁食【軟菜】", "", "備考欄"],
                            ["", "", "", "", "", "", "", "肉禁", "魚禁", ""],
                            ["", "4/5\n(日)", "ま", "...", "豚肉の卵とじ", "0", "0", "", "", ""],
                            ["", "", "", "***", "いんげんのカニ和え", "0", "0", "", "", ""],
                            ["", "", "口", "VT", "サワラの西京焼き 添)小松菜", "58", "2", "", "", ""],
                            ["", "", "", "OK", "じゃが芋の煮物", "58", "2", "", "", ""],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_drifted_floor_split())

    assert rows is not None
    assert rows[0][:5] == ["4/5 (日)", "ま", "豚肉の卵とじ", "0", "0"]
    assert rows[1][:5] == ["", "", "いんげんのカニ和え", "0", "0"]
    assert rows[2][:5] == ["", "口", "サワラの西京焼き 添)小松菜", "58", "2"]


def test_rows_from_structured_payload_prefers_quantity_column_order_when_header_family_is_stale():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
                            ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "5", "note"],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_staff_forbidden_sesame())

    assert rows == [
        ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "5", "note"],
    ]


def test_rows_from_structured_payload_maps_staff_other_forbidden_and_sesame_subheaders():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "職員", "禁食", "", "", "", "備考"],
                            ["", "", "", "", "", "肉禁", "肉卵魚禁", "魚禁", "ゴマアレルギー", ""],
                            ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "5", "note"],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_staff_forbidden_sesame())

    assert rows == [
        ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "5", "note"],
    ]


def test_rows_from_structured_payload_maps_staff_forbidden_sesame_with_aux_column_gap():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "", "献立", "常食", "職員", "禁食", "", "", "", "備考欄"],
                            ["", "", "", "", "", "", "肉禁", "肉卵魚禁", "魚禁", "ゴマアレルギー", ""],
                            ["3/22(日)", "", "CH", "厚揚げとさつま芋の煮物", "105", "2", "", "", "", "", ""],
                            ["", "", "W2", "カリフラワーサラダ", "105", "2", "", "", "", "", ""],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_staff_forbidden_sesame_with_aux())

    assert rows == [
        ["3/22(日)", "", "厚揚げとさつま芋の煮物", "105", "2", "", "", "", "", ""],
        ["", "", "カリフラワーサラダ", "105", "2", "", "", "", "", ""],
    ]


def test_rows_from_structured_payload_prefers_quantity_column_order_for_diabetes_family():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
                            ["2/15", "朝", "Menu A", "20", "4", "5", "6", "7", "8", "note"],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_diabetes_forbidden_change())

    assert rows == [
        ["2/15", "朝", "Menu A", "20", "4", "5", "6", "7", "8", "note"],
    ]


def test_rows_from_pipeline_payload_realigns_stale_floor_family_quantity_block_for_staff_facility():
    payload = {
        "table_raw": """
|日付|区 分||献立|常食||軟菜||ミキサー||備考欄|
|-|-|-|-|-|-|-|-|-|-|-|
|||||2F|3F|2F|3F|2F|3F||
|2/15|朝|副|Menu A||12|1|2|3|4||
""".strip()
    }

    rows = _rows_from_pipeline_payload(payload, _template_staff_forbidden_sesame())

    assert rows == [
        ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "", ""],
    ]


def test_rows_from_pipeline_payload_realigns_split_forbidden_headers_by_numeric_block_order():
    payload = {
        "table_raw": """
|日付|区分|メニュー|常食|禁食|||変更1||備考欄|
|-|-|-|-|-|-|-|-|-|-|
|||||肉禁|魚禁||変更2||
|2/15|朝|Menu A||20|4|5|6|7||
""".strip()
    }

    rows = _rows_from_pipeline_payload(payload, _template_diabetes_forbidden_change())

    assert rows == [
        ["2/15", "朝", "Menu A", "20", "4", "5", "6", "7", "", ""],
    ]


def test_extract_payload_cell_issues_derives_yomitoku_multiline_and_merged_quantity_cells():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "", "備考"],
                            ["", "", "", "常食", "軟菜", ""],
                            ["2/15", "朝", "Menu A", "12", "", ""],
                            ["2/15", "昼", "Menu B", "6\n9", "", ""],
                        ],
                        "cells": [
                            {
                                "row_index": 2,
                                "col_index": 3,
                                "row_span": 2,
                                "col_span": 1,
                                "text": "12",
                                "bbox": [0.10, 0.20, 0.18, 0.28],
                            },
                            {
                                "row_index": 3,
                                "col_index": 3,
                                "row_span": 1,
                                "col_span": 1,
                                "text": "6\n9",
                                "bbox": [0.10, 0.30, 0.18, 0.38],
                            },
                        ],
                    }
                ],
            }
        ]
    }

    issues = order_service._extract_payload_cell_issues(payload, _template())

    assert len(issues) == 2
    merged_issue = next(issue for issue in issues if issue["issue_code"] == "merged_numeric_cell")
    multiline_issue = next(issue for issue in issues if issue["issue_code"] == "multiline_numeric_cell")
    assert merged_issue["field"] == "qty.regular_x"
    assert merged_issue["source"] == "yomitoku_structured"
    assert merged_issue["source_row_index"] == 0
    assert merged_issue["row_span"] == 2
    assert multiline_issue["field"] == "qty.regular_x"
    assert multiline_issue["source"] == "yomitoku_structured"
    assert multiline_issue["source_row_index"] == 1
    assert multiline_issue["value"] == "6\n9"
