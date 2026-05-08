from __future__ import annotations

import sys
import types

import numpy as np

topk_stub = types.ModuleType("src.services.yomitoku_text_recognizer_topk")
topk_stub.YomitokuTextRecognizerTopKWrapper = object
sys.modules.setdefault("src.services.yomitoku_text_recognizer_topk", topk_stub)

from src.hakodate_best_method_runtime import compare_kasuga_digit_preprocess_methods as cmp
from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as render
from src.hakodate_best_method_runtime.render_best_method_overlay_all_facilities import (
    _select_template_owned_eval_regions,
)


def test_best_method_keeps_template_owned_regions_past_draft_rows() -> None:
    regions = [{"worksheet_row": row, "worksheet_col": 4} for row in range(11, 67)]

    selected = _select_template_owned_eval_regions(regions)

    assert selected == regions
    assert selected is not regions
    assert selected[0]["worksheet_row"] == 11
    assert selected[-1]["worksheet_row"] == 66


def test_best_method_json_preserves_merged_cell_metadata() -> None:
    record = {
        "region_id": "E11:E12",
        "sheet_cell": "E11",
        "worksheet_row": 11,
        "worksheet_col": 5,
        "grid_row_index": 2,
        "grid_col_index": 4,
        "field": "qty.regular_x",
        "field_label": "常食",
        "date": "04/26",
        "daypart": "朝",
        "menu_name": "大豆のトマト煮",
        "bbox": [10.0, 20.0, 30.0, 50.0],
        "merged_cell": {"range": "E11:E12", "min_row": 11, "max_row": 12, "min_col": 5, "max_col": 5},
        "logical_targets": [
            {"sheet_cell": "E11", "worksheet_row": 11, "worksheet_col": 5},
            {"sheet_cell": "E12", "worksheet_row": 12, "worksheet_col": 5},
        ],
        "covered_sheet_cells": ["E11", "E12"],
        "x_snap": {"snapped_left": 10, "snapped_right": 30},
        "ocr_contact_slot_index": 0,
        "processed_image": object(),
    }

    stripped = cmp._strip_record_for_json(record)

    assert stripped["region_id"] == "E11:E12"
    assert stripped["merged_cell"]["range"] == "E11:E12"
    assert [target["sheet_cell"] for target in stripped["logical_targets"]] == ["E11", "E12"]
    assert stripped["covered_sheet_cells"] == ["E11", "E12"]
    assert "processed_image" not in stripped


def test_best_method_overlay_does_not_draw_internal_merge_boundary(monkeypatch) -> None:
    monkeypatch.setattr(render, "_make_review_canvas", lambda **kwargs: kwargs["image"])
    raw_rectified = np.full((80, 80, 3), 255, dtype=np.uint8)
    regions = [
        {"region_id": "E11:E12", "sheet_cell": "E11", "bbox": [10.0, 10.0, 70.0, 50.0]},
        {"region_id": "F11", "sheet_cell": "F11", "bbox": [70.0, 10.0, 79.0, 30.0]},
        {"region_id": "F12", "sheet_cell": "F12", "bbox": [70.0, 30.0, 79.0, 50.0]},
    ]

    image = render._draw_overlay(
        raw_rectified_bgr=raw_rectified,
        regions=regions,
        records=[],
        quad_points=[],
        facility_code="FAC_TEST",
        order_id="ORD_TEST",
        details=[],
    )

    pixels = image.convert("RGB")
    assert pixels.getpixel((20, 30)) == (255, 255, 255)
