import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import pytest

from src.services import fax_extractor  # noqa: E402
from src.services.fax_extractor import _get_row_fields, _rows_from_pipeline_payload, extract_fax_data  # noqa: E402


def test_rows_from_pipeline_payload_supports_flat_dotted_qty_keys():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.soft_2f",
            "remarks",
        ]
    }
    payload = {
        "rows": [
            {
                "date_mmdd": "2/15",
                "daypart": "朝",
                "menu": "じゃが芋のコンソメ煮",
                "qty.regular_2f": "20",
                "qty.soft_2f": "1",
                "remarks": "",
            }
        ]
    }

    rows = _rows_from_pipeline_payload(payload, template)

    assert rows == [["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""]]


def test_rows_from_pipeline_payload_supports_nested_qty_object():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.soft_2f",
            "remarks",
        ]
    }
    payload = {
        "rows": [
            {
                "date_mmdd": "2/15",
                "daypart": "朝",
                "menu": "キャベツサラダ",
                "qty": {"regular_2f": "18", "soft_2f": "2"},
                "remarks": "ok",
            }
        ]
    }

    rows = _rows_from_pipeline_payload(payload, template)

    assert rows == [["2/15", "朝", "キャベツサラダ", "18", "2", "ok"]]


def test_rows_from_pipeline_payload_expands_row_index_for_quantity_only_rows():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
        ]
    }
    payload = {
        "rows": [
            {"row_index": 0, "qty.regular_2f": "20"},
            {"row_index": 2, "qty.regular_2f": "11"},
        ]
    }

    rows = _rows_from_pipeline_payload(payload, template)

    assert rows == [
        ["", "", "", "20"],
        ["", "", "", ""],
        ["", "", "", "11"],
    ]


def test_rows_from_pipeline_payload_clamps_full_table_rows_to_expected_count():
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
        ],
        "llm_full_table_mode": True,
        "llm_full_table_expected_row_count": 2,
    }
    payload = {
        "_ocr_debug": {},
        "rows": [
            {"row_index": 0, "date_mmdd": "04/26", "daypart": "朝", "menu": "Menu A", "qty.regular_2f": "20"},
            {"row_index": 1, "date_mmdd": "04/26", "daypart": "昼", "menu": "Menu B", "qty.regular_2f": "11"},
            {"row_index": 4, "date_mmdd": "04/30", "daypart": "夕", "menu": "Noise", "qty.regular_2f": "99"},
        ],
    }

    rows = _rows_from_pipeline_payload(payload, template)

    assert rows == [
        ["04/26", "朝", "Menu A", "20"],
        ["04/26", "昼", "Menu B", "11"],
    ]
    assert payload["_ocr_debug"]["returned_row_indexes"] == [0, 1]
    assert payload["_ocr_debug"]["dropped_row_indexes"] == [4]


def test_columns_authoritative_template_preserves_aux_display_columns():
    template = {
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
        "columns_authoritative": True,
        "columns": [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "aux", "header": "副区分"},
            {"index": 3, "role": "menu_name", "header": "メニュー"},
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
    payload = {
        "rows": [
            [
                "4/26",
                "昼",
                "主",
                "親子煮",
                "12",
                "10",
                "1",
                "1",
                "",
                "",
                "",
                "",
                "",
            ]
        ]
    }

    fields = _get_row_fields(template)
    rows = _rows_from_pipeline_payload(payload, template)

    assert fields == [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "qty.daycare_x",
        "qty.staff_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.no_fried_x",
        "qty.change_1_x",
        "remarks",
    ]
    assert rows == [
        [
            "4/26",
            "昼",
            "主",
            "親子煮",
            "12",
            "10",
            "1",
            "1",
            "",
            "",
            "",
            "",
            "",
        ]
    ]


def test_columns_authoritative_template_uses_explicit_source_indexes_for_hidden_raw_columns():
    template = {
        "columns_authoritative": True,
        "columns": [
            {"index": 0, "source_index": 0, "role": "date", "header": "日付"},
            {"index": 1, "source_index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "source_index": 3, "role": "menu_name", "header": "メニュー"},
            {"index": 3, "source_index": 4, "role": "quantity", "header": "常食1回目", "diet_type": "regular", "area_id": "X"},
            {"index": 4, "source_index": 5, "role": "quantity", "header": "常食2回目", "diet_type": "change_1", "area_id": "X"},
            {"index": 5, "source_index": 6, "role": "quantity", "header": "常食3回目", "diet_type": "change_2", "area_id": "X"},
            {"index": 6, "source_index": 7, "role": "quantity", "header": "常食袋分け", "diet_type": "regular_bag", "area_id": "X"},
            {"index": 7, "source_index": 8, "role": "quantity", "header": "軟菜", "diet_type": "soft", "area_id": "X"},
            {"index": 8, "source_index": 9, "role": "quantity", "header": "ミキサー", "diet_type": "mixer", "area_id": "X"},
            {"index": 9, "source_index": 10, "role": "quantity", "header": "禁食肉禁", "diet_type": "no_meat", "area_id": "X"},
            {"index": 10, "source_index": 11, "role": "quantity", "header": "禁食魚禁", "diet_type": "no_fish", "area_id": "X"},
            {"index": 11, "source_index": 12, "role": "note", "header": "備考欄"},
        ],
    }

    assert fax_extractor._template_explicit_quantity_source_indexes(template, observed_width=13) == [4, 5, 6, 7, 8, 9, 10, 11]
    assert fax_extractor._template_explicit_source_indexes_for_roles(
        template,
        roles={"date", "daypart", "menu_name"},
        observed_width=13,
    ) == [0, 1, 3]


def test_columns_authoritative_template_blocks_sparse_raw_mapping_without_source_indexes():
    template = {
        "columns_authoritative": True,
        "columns": [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "menu_name", "header": "メニュー"},
            {"index": 3, "role": "quantity", "header": "常食1回目", "diet_type": "regular", "area_id": "X"},
            {"index": 4, "role": "quantity", "header": "常食2回目", "diet_type": "change_1", "area_id": "X"},
            {"index": 5, "role": "quantity", "header": "常食3回目", "diet_type": "change_2", "area_id": "X"},
            {"index": 6, "role": "quantity", "header": "常食袋分け", "diet_type": "regular_bag", "area_id": "X"},
            {"index": 7, "role": "quantity", "header": "軟菜", "diet_type": "soft", "area_id": "X"},
            {"index": 8, "role": "quantity", "header": "ミキサー", "diet_type": "mixer", "area_id": "X"},
            {"index": 9, "role": "quantity", "header": "禁食肉禁", "diet_type": "no_meat", "area_id": "X"},
            {"index": 10, "role": "quantity", "header": "禁食魚禁", "diet_type": "no_fish", "area_id": "X"},
            {"index": 11, "role": "note", "header": "備考欄"},
        ],
    }

    assert fax_extractor._template_explicit_quantity_source_indexes(template, observed_width=13) == []
    assert fax_extractor._template_explicit_source_indexes_for_roles(
        template,
        roles={"date", "daypart", "menu_name"},
        observed_width=13,
    ) == []


def test_mapped_indexes_from_columns_authoritative_skips_nonexplicit_sparse_columns():
    template = {
        "columns_authoritative": True,
        "columns": [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "source_index": 3, "role": "menu_name", "header": "メニュー"},
            {"index": 3, "source_index": 4, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
            {"index": 4, "source_index": 5, "role": "quantity", "header": "-", "diet_type": "placeholder", "area_id": "X"},
            {"index": 5, "source_index": 6, "role": "quantity", "header": "肉禁", "diet_type": "no_meat", "area_id": "X"},
            {"index": 6, "source_index": 7, "role": "quantity", "header": "魚禁", "diet_type": "no_fish", "area_id": "X"},
            {"index": 7, "source_index": 10, "role": "note", "header": "備考欄"},
        ],
    }
    fields = [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "qty.placeholder_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "remarks",
    ]

    mapped = fax_extractor._mapped_indexes_from_template_columns(
        template=template,
        fields=fields,
        observed_width=11,
    )

    assert mapped == {
        3: 2,
        4: 3,
        5: 4,
        6: 5,
        7: 6,
        10: 7,
    }


def test_extract_fax_data_disables_gemini_pipeline_fallback_in_llm_full_table_mode(monkeypatch):
    monkeypatch.setattr(fax_extractor, "detect_table_grid", lambda *_args, **_kwargs: None)

    def _fake_run_gemini_ocr(*, pdf_bytes, template, facility_id=None):  # noqa: ARG001
        raise RuntimeError("Gemini OCR timeout after 240s")

    def _fake_run_ocr_pipeline(**_kwargs):
        raise AssertionError("pipeline fallback must not run in llm_full_table_mode")

    monkeypatch.setattr("src.services.gemini_ocr_service.run_gemini_ocr", _fake_run_gemini_ocr)
    monkeypatch.setattr(fax_extractor, "run_ocr_pipeline", _fake_run_ocr_pipeline)

    with pytest.raises(RuntimeError, match="Gemini OCR timeout after 240s"):
        extract_fax_data(
            b"%PDF-1.4\n%EOF\n",
            {
                "main_ocr_provider": "gemini",
                "main_ocr_row_fields": ["date_mmdd", "daypart", "menu"],
                "llm_full_table_mode": True,
            },
        )


def test_extract_fax_data_keeps_gemini_pipeline_fallback_for_non_llm_mode(monkeypatch):
    monkeypatch.setattr(fax_extractor, "detect_table_grid", lambda *_args, **_kwargs: None)

    def _fake_run_gemini_ocr(*, pdf_bytes, template, facility_id=None):  # noqa: ARG001
        raise RuntimeError("Gemini OCR timeout after 90s")

    def _fake_run_ocr_pipeline(**_kwargs):
        return {
            "rows": [["4/26", "朝", "Menu A", "7"]],
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|4/26|朝|Menu A|7|",
        }

    monkeypatch.setattr("src.services.gemini_ocr_service.run_gemini_ocr", _fake_run_gemini_ocr)
    monkeypatch.setattr(fax_extractor, "run_ocr_pipeline", _fake_run_ocr_pipeline)

    extracted = extract_fax_data(
        b"%PDF-1.4\n%EOF\n",
        {
            "main_ocr_provider": "gemini",
            "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x"],
        },
    )

    assert extracted.ocr_provider == "gemini_fallback_pipeline"
    assert extracted.table_rows == [["4/26", "朝", "Menu A", "7"]]


def _template_floor_2f3f_v1_for_projection() -> dict:
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
            "qty.regular_x",
            "remarks",
        ],
        "auto_headers": [
            {"index": 0, "role": "date", "format": "MM/DD"},
            {"index": 1, "role": "menu_name"},
            {"index": 2, "role": "quantity", "diet_type": "regular", "area_id": "2F"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "3F"},
            {"index": 4, "role": "quantity", "diet_type": "soft", "area_id": "2F"},
            {"index": 5, "role": "quantity", "diet_type": "soft", "area_id": "3F"},
            {"index": 6, "role": "quantity", "diet_type": "mixer", "area_id": "2F"},
            {"index": 7, "role": "quantity", "diet_type": "mixer", "area_id": "3F"},
            {"index": 8, "role": "note"},
        ],
        "auto_numeric_columns": {
            "columns": [
                {"index": 2, "role": "quantity", "diet_type": "regular", "area_id": "2F"},
                {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "3F"},
                {"index": 4, "role": "quantity", "diet_type": "soft", "area_id": "2F"},
                {"index": 5, "role": "quantity", "diet_type": "soft", "area_id": "3F"},
                {"index": 6, "role": "quantity", "diet_type": "mixer", "area_id": "2F"},
                {"index": 7, "role": "quantity", "diet_type": "mixer", "area_id": "3F"},
            ],
            "tail_column": {"index": 8, "role": "note"},
        },
        "grid_columns": [
            {"index": 0, "role": "date", "format": "MM/DD"},
            {"index": 1, "role": "menu_name"},
            {"index": 2, "role": "quantity", "diet_type": "regular", "area_id": "2F"},
            {"index": 3, "role": "quantity", "diet_type": "regular", "area_id": "3F"},
            {"index": 4, "role": "quantity", "diet_type": "soft", "area_id": "2F"},
            {"index": 5, "role": "quantity", "diet_type": "soft", "area_id": "3F"},
            {"index": 6, "role": "quantity", "diet_type": "mixer", "area_id": "2F"},
            {"index": 7, "role": "quantity", "diet_type": "mixer", "area_id": "3F"},
            {"index": 8, "role": "note"},
        ],
    }


def test_floor_2f3f_template_family_enforces_strict_quantity_alignment_without_top_level_columns():
    template = _template_floor_2f3f_v1_for_projection()

    assert fax_extractor._template_explicit_quantity_source_indexes(template, observed_width=12) == []
    assert fax_extractor._template_requires_strict_quantity_source_alignment(
        template,
        observed_width=12,
    ) is True


def test_floor_2f3f_projection_preserves_mixer_columns_when_soft_headers_are_corrupted():
    template = _template_floor_2f3f_v1_for_projection()
    header = ["日付", "区 分", "", "献立", "常食", "", "", "返迎", "", "ミキサー", "", "原状(常盤"]
    subheader = ["", "", "", "", "花", "", "月", "", "", "", "", ""]
    data = [
        ["4/26\n(日)", "", "10\nOF", "大豆のトマト煮", "8", "", "", "", "", "2", "3\n3", ""],
        ["", "", "", "胡瓜のフレンチサラダ", "8", "", "", "", "", "2", "", ""],
        ["", "", "生A", "サワラの揚げ浸し 添) ホーレン草", "9", "", "", "", "", "2", "3", "1"],
    ]

    projected = fax_extractor._project_rows_from_header_and_data_internal(
        header=fax_extractor._merge_header_rows(header, subheader),
        data=data,
        template=template,
        allow_explicit_template_columns=True,
    )

    assert projected is not None
    meta, rows = projected
    fields = meta["fields"]

    assert meta["mapped_indexes"][4] == fields.index("qty.regular_2f")
    assert meta["mapped_indexes"][9] == fields.index("qty.mixer_2f")
    assert meta["mapped_indexes"][10] == fields.index("qty.mixer_3f")
    assert fields.index("qty.soft_2f") not in meta["mapped_indexes"].values()
    assert fields.index("qty.soft_3f") not in meta["mapped_indexes"].values()
    assert rows[0][fields.index("qty.soft_2f")] == ""
    assert rows[0][fields.index("qty.soft_3f")] == ""
    assert rows[0][fields.index("qty.mixer_2f")] == "2"
    assert rows[0][fields.index("qty.mixer_3f")] == "3\n3"


def test_floor_2f3f_projection_preserves_trailing_anchor_in_close_sibling_case():
    template = _template_floor_2f3f_v1_for_projection()
    header = ["日付", "区 分", "", "献立", "常食", "", "", "軟X", "", "ミキサー", "", "原状(常盤"]
    subheader = ["", "", "", "", "花", "", "月", "", "", "", "", ""]
    data = [
        ["4/26", "", "", "Menu A", "8", "", "", "", "", "2", "3", ""],
        ["4/27", "", "", "Menu B", "9", "", "", "", "", "1", "4", ""],
    ]

    projected = fax_extractor._project_rows_from_header_and_data_internal(
        header=fax_extractor._merge_header_rows(header, subheader),
        data=data,
        template=template,
        allow_explicit_template_columns=True,
    )

    assert projected is not None
    meta, rows = projected
    fields = meta["fields"]

    assert meta["mapped_indexes"][9] == fields.index("qty.mixer_2f")
    assert meta["mapped_indexes"][10] == fields.index("qty.mixer_3f")
    assert rows[1][fields.index("qty.mixer_2f")] == "1"
    assert rows[1][fields.index("qty.mixer_3f")] == "4"


def test_floor_2f3f_projection_blocks_quantity_assignment_when_no_quantity_anchor_survives():
    template = _template_floor_2f3f_v1_for_projection()
    fields = template["main_ocr_row_fields"]
    data = [
        ["4/26", "", "", "Menu A", "8", "", "", "", "", "2", "3", ""],
        ["4/27", "", "", "Menu B", "9", "", "", "", "", "1", "4", ""],
    ]

    mapping = fax_extractor._realign_quantity_mapping_by_numeric_block(
        data=data,
        fields=fields,
        mapped_indexes={0: 0, 3: 2, 11: 10},
        template=template,
    )

    assert mapping == {0: 0, 3: 2, 11: 10}
