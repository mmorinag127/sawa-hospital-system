import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.structure_guided_ocr import (  # noqa: E402
    assign_words_to_structure_table,
    assign_words_to_structure_table_by_overlap,
    build_sequence_guided_table,
    repair_menu_tail_quantity_shift,
    table_rows_to_markdown,
)


def test_assign_words_to_structure_table_places_words_by_cell_bbox() -> None:
    structure_table = {
        "row_count": 2,
        "col_count": 3,
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "cells": [
            {"row_index": 0, "col_index": 0, "bbox": [0.0, 0.0, 0.2, 0.5]},
            {"row_index": 0, "col_index": 1, "bbox": [0.2, 0.0, 0.4, 0.5]},
            {"row_index": 0, "col_index": 2, "bbox": [0.4, 0.0, 1.0, 0.5]},
            {"row_index": 1, "col_index": 0, "bbox": [0.0, 0.5, 0.2, 1.0]},
            {"row_index": 1, "col_index": 1, "bbox": [0.2, 0.5, 0.4, 1.0]},
            {"row_index": 1, "col_index": 2, "bbox": [0.4, 0.5, 1.0, 1.0]},
        ],
    }
    words = [
        {"text": "4/5", "x": 0.1, "y": 0.2},
        {"text": "朝", "x": 0.3, "y": 0.2},
        {"text": "Menu", "x": 0.55, "y": 0.18},
        {"text": "A", "x": 0.63, "y": 0.22},
        {"text": "12", "x": 0.3, "y": 0.7},
        {"text": "7", "x": 0.6, "y": 0.72},
    ]

    assigned = assign_words_to_structure_table(structure_table=structure_table, words=words)

    assert assigned["rows"] == [
        ["4/5", "朝", "Menu A"],
        ["", "12", "7"],
    ]


def test_table_rows_to_markdown_renders_markdown_table() -> None:
    markdown = table_rows_to_markdown([["日付", "区分"], ["4/5", "朝"]])

    assert "| 日付 | 区分 |" in markdown
    assert "| 4/5 | 朝 |" in markdown


def test_assign_words_to_structure_table_by_overlap_prefers_word_box_overlap_over_point_location() -> None:
    structure_table = {
        "row_count": 1,
        "col_count": 2,
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "cells": [
            {"row_index": 0, "col_index": 0, "bbox": [0.0, 0.0, 0.50, 1.0]},
            {"row_index": 0, "col_index": 1, "bbox": [0.50, 0.0, 1.0, 1.0]},
        ],
    }
    words = [
        {
            "text": "102",
            "x": 0.49,
            "y": 0.50,
            "box": [0.48, 0.40, 0.70, 0.60],
        }
    ]

    assigned = assign_words_to_structure_table(structure_table=structure_table, words=words)
    overlap_assigned = assign_words_to_structure_table_by_overlap(
        structure_table=structure_table,
        words=words,
    )

    assert assigned["rows"] == [["102", ""]]
    assert overlap_assigned["rows"] == [["", "102"]]


def test_build_sequence_guided_table_maps_observed_rows_to_canonical_menu_sequence() -> None:
    structure_table = {
        "row_count": 5,
        "col_count": 7,
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "rows": [
            ["日付", "区分", "補助", "献立", "常食", "職員", "肉禁"],
            ["", "", "", "", "", "", ""],
            ["", "", "", "", "", "", ""],
            ["", "", "", "", "", "", ""],
            ["", "", "", "", "", "", ""],
        ],
        "cells": [
            {"row_index": row_index, "col_index": col_index, "bbox": [0.0, 0.0, 0.1, 0.1]}
            for row_index in range(5)
            for col_index in range(7)
        ],
    }
    observed_table = {
        "row_count": 5,
        "col_count": 7,
        "rows": [
            ["4 日", "区分", "", "献立", "常食", "職員", "肉禁"],
            ["", "", "", "", "", "", ""],
            ["4/26", "朝", "a", "大豆のトマト煮", "102", "2", "2"],
            ["", "", "店", "胡瓜のフレンチサラダ", "104", "2", ""],
            ["", "", "¥", "サワラの揚げ浸し 添) ホーレン草", "101", "2", "3"],
        ],
    }
    canonical_rows = [
        {"row_index": 2, "date": "4/26", "daypart": "朝", "aux": "副1", "menu_name": "大豆のトマト煮"},
        {"row_index": 3, "date": "", "daypart": "", "aux": "副2", "menu_name": "胡瓜のフレンチサラダ"},
        {"row_index": 4, "date": "", "daypart": "昼", "aux": "主A", "menu_name": "サワラの揚げ浸し 添)ホーレン草"},
    ]

    guided = build_sequence_guided_table(
        structure_table=structure_table,
        observed_table=observed_table,
        canonical_rows=canonical_rows,
    )

    assert guided["rows"][2] == ["4/26", "朝", "副1", "大豆のトマト煮", "102", "2", "2"]
    assert guided["rows"][3] == ["", "", "副2", "胡瓜のフレンチサラダ", "104", "2", ""]
    assert guided["rows"][4] == ["", "昼", "主A", "サワラの揚げ浸し 添)ホーレン草", "101", "2", "3"]


def test_repair_menu_tail_quantity_shift_moves_trailing_digits_into_quantity_band() -> None:
    rows = [
        ["4/26", "朝", "副①", "大豆のトマト煮 102", "2", "2", ""],
        ["", "", "副②", "胡瓜のフレンチサラダ 104", "2", "", ""],
    ]

    repaired = repair_menu_tail_quantity_shift(rows=rows)

    assert repaired == [
        ["4/26", "朝", "副①", "大豆のトマト煮", "102", "2", "2"],
        ["", "", "副②", "胡瓜のフレンチサラダ", "104", "2", ""],
    ]
