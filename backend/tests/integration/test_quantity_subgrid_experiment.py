from __future__ import annotations

from src.services.quantity_subgrid_experiment import (
    extract_quantity_digit_context,
    infer_quantity_subgrid,
    is_suspicious_quantity_text,
    normalize_digit_candidate,
)


def test_infer_quantity_subgrid_uses_body_rows_and_keeps_leading_blank_quantity_column():
    table = {
        "row_count": 8,
        "col_count": 11,
        "rows": [
            ["日付", "区分", "", "献立", "常食", "", "軟菜", "", "ミキサー", "", "備考欄"],
            ["", "", "", "", "2F", "3F", "2F", "3F", "2F", "3F", ""],
            ["3/22", "朝", "30", "厚揚げとさつま芋の煮物", "", "4", "5", "1", "4", "3", ""],
            ["", "", "2015", "カリフラワーサラダ", "", "4", "5", "1", "4", "3", ""],
            ["", "習", "主A", "鶏じゃが", "", "4", "5", "1", "4", "3", ""],
            ["", "", "10", "ブロッコリーのおかか醤油", "", "4", "5", "1", "4", "3", ""],
            ["", "", "割2", "キャベツのゴマドレ和え", "", "4", "5", "1", "4", "3", ""],
            ["", "6", "ま", "豚肉と白菜の中華煮", "", "4", "5", "1", "4", "3", ""],
        ],
        "cells": [
            {"row_index": 2, "col_index": 3, "bbox": [0.20, 0.20, 0.40, 0.24]},
            {"row_index": 2, "col_index": 4, "bbox": [0.40, 0.20, 0.48, 0.24]},
            {"row_index": 2, "col_index": 5, "bbox": [0.48, 0.20, 0.56, 0.24]},
            {"row_index": 2, "col_index": 6, "bbox": [0.56, 0.20, 0.64, 0.24]},
            {"row_index": 2, "col_index": 7, "bbox": [0.64, 0.20, 0.72, 0.24]},
            {"row_index": 2, "col_index": 8, "bbox": [0.72, 0.20, 0.80, 0.24]},
            {"row_index": 2, "col_index": 9, "bbox": [0.80, 0.20, 0.88, 0.24]},
            {"row_index": 3, "col_index": 3, "bbox": [0.20, 0.24, 0.40, 0.28]},
            {"row_index": 3, "col_index": 4, "bbox": [0.40, 0.24, 0.48, 0.28]},
            {"row_index": 3, "col_index": 5, "bbox": [0.48, 0.24, 0.56, 0.28]},
            {"row_index": 3, "col_index": 6, "bbox": [0.56, 0.24, 0.64, 0.28]},
            {"row_index": 3, "col_index": 7, "bbox": [0.64, 0.24, 0.72, 0.28]},
            {"row_index": 3, "col_index": 8, "bbox": [0.72, 0.24, 0.80, 0.28]},
            {"row_index": 3, "col_index": 9, "bbox": [0.80, 0.24, 0.88, 0.28]},
            {"row_index": 1, "col_index": 4, "bbox": [0.40, 0.16, 0.48, 0.20]},
            {"row_index": 1, "col_index": 5, "bbox": [0.48, 0.16, 0.56, 0.20]},
            {"row_index": 1, "col_index": 6, "bbox": [0.56, 0.16, 0.64, 0.20]},
            {"row_index": 1, "col_index": 7, "bbox": [0.64, 0.16, 0.72, 0.20]},
            {"row_index": 1, "col_index": 8, "bbox": [0.72, 0.16, 0.80, 0.20]},
            {"row_index": 1, "col_index": 9, "bbox": [0.80, 0.16, 0.88, 0.20]},
        ],
    }

    spec = infer_quantity_subgrid(table)

    assert spec is not None
    assert spec.body_start_row == 2
    assert spec.menu_col_index == 3
    assert spec.quantity_start_col_index == 4
    assert spec.quantity_col_count == 7
    assert spec.crop_box_norm == [0.4, 0.2, 0.88, 0.28]


def test_normalize_digit_candidate_maps_common_confusables():
    assert normalize_digit_candidate("で") == "2"
    assert normalize_digit_candidate("2.") == "2"
    assert normalize_digit_candidate("O") == "0"
    assert normalize_digit_candidate("B") == "8"
    assert normalize_digit_candidate("女子") == ""


def test_extract_quantity_digit_context_uses_nearest_numeric_neighbors():
    rows = [
        [""],
        ["4"],
        ["で"],
        ["4"],
    ]

    prev_value, next_value = extract_quantity_digit_context(rows, 2, 0)

    assert prev_value == "4"
    assert next_value == "4"
    assert is_suspicious_quantity_text("で") is True
    assert is_suspicious_quantity_text("42") is False
