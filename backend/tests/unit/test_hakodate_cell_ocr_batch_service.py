import sys
from types import ModuleType

import numpy as np
import pytest

from src.services import hakodate_cell_ocr_batch_service
from src.services.hakodate_cell_ocr_batch_service import (
    _analysis_to_yomitoku_words,
    assign_yomitoku_words_to_contact_regions,
    build_cell_contact_sheet,
    sheet_assignments_from_ocr_regions,
    sheet_value_grid_from_assignments,
    validate_cell_ocr_mapping,
)


def test_assign_yomitoku_words_to_contact_regions_keeps_slot_identity() -> None:
    regions = [
        {
            "region_id": "E11",
            "ocr_contact_slot": [0, 0, 100, 50],
            "logical_targets": [{"sheet_cell": "E11"}],
        },
        {
            "region_id": "F11",
            "ocr_contact_slot": [100, 0, 200, 50],
            "logical_targets": [{"sheet_cell": "F11"}],
        },
    ]
    words = [
        {"text": "12", "x": 0.25, "y": 0.5},
        {"text": "7", "x": 0.75, "y": 0.5},
    ]

    assigned = assign_yomitoku_words_to_contact_regions(
        words=words,
        regions=regions,
        sheet_size=(200, 50),
    )

    by_id = {str(region["region_id"]): region for region in assigned}
    assert by_id["E11"]["ocr_text"] == "12"
    assert by_id["E11"]["ocr_normalized"] == "12"
    assert by_id["F11"]["ocr_text"] == "7"
    assert by_id["F11"]["ocr_normalized"] == "7"


def test_sheet_assignments_expand_merged_region_to_logical_targets() -> None:
    regions = [
        {
            "region_id": "E11:E12",
            "bbox": [10.0, 20.0, 30.0, 50.0],
            "ocr_cell_crop_bbox_px": [11, 22, 29, 48],
            "ocr_text": "5",
            "ocr_normalized": "5",
            "merged_cell": {"range": "E11:E12"},
            "logical_targets": [
                {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "grid_row_index": 2,
                    "grid_col_index": 4,
                    "field": "qty.regular",
                    "field_label": "常食",
                    "menu_name": "大豆のトマト煮",
                },
                {
                    "sheet_cell": "E12",
                    "worksheet_row": 12,
                    "worksheet_col": 5,
                    "grid_row_index": 3,
                    "grid_col_index": 4,
                    "field": "qty.regular",
                    "field_label": "常食",
                    "menu_name": "胡瓜のフレンチサラダ",
                },
            ],
        }
    ]

    assignments = sheet_assignments_from_ocr_regions(regions)

    assert [item["sheet_cell"] for item in assignments] == ["E11", "E12"]
    assert [item["value_normalized"] for item in assignments] == ["5", "5"]
    assert all(item["source_region_id"] == "E11:E12" for item in assignments)


def test_build_cell_contact_sheet_uses_only_valid_regions() -> None:
    image = np.full((100, 200, 3), 255, dtype=np.uint8)
    image[20:40, 20:60] = 0
    regions = [
        {"region_id": "E11", "bbox": [10.0, 10.0, 80.0, 50.0]},
        {"region_id": "bad", "bbox": [50.0, 50.0, 10.0, 60.0]},
    ]

    sheet, usable = build_cell_contact_sheet(
        rectified_fax_bgr=image,
        regions=regions,
        slot_width=80,
        slot_height=60,
        columns=2,
    )

    assert sheet.size == (160, 60)
    assert [region["region_id"] for region in usable] == ["E11"]
    assert usable[0]["ocr_contact_slot"] == [0, 0, 80, 60]
    assert usable[0]["ocr_cell_crop_bbox_px"] == [9, 2, 81, 58]


def test_sheet_value_grid_from_assignments_builds_cell_and_row_views() -> None:
    assignments = [
        {
            "sheet_cell": "E11",
            "worksheet_row": 11,
            "worksheet_col": 5,
            "value_text": "12",
            "value_normalized": "12",
            "field_label": "常食",
            "menu_name": "大豆のトマト煮",
            "source_region_id": "E11",
        },
        {
            "sheet_cell": "G11",
            "worksheet_row": 11,
            "worksheet_col": 7,
            "value_text": "3",
            "value_normalized": "3",
            "field_label": "肉禁",
            "menu_name": "大豆のトマト煮",
            "source_region_id": "G11",
        },
    ]

    grid = sheet_value_grid_from_assignments(assignments)

    assert grid["columns"] == ["E", "G"]
    assert grid["cells"]["E11"]["value_text"] == "12"
    assert grid["cells"]["G11"]["field_label"] == "肉禁"
    assert grid["rows"] == [
        {
            "worksheet_row": 11,
            "values_by_column": {"E": "12", "G": "3"},
            "cells_by_column": {
                "E": grid["cells"]["E11"],
                "G": grid["cells"]["G11"],
            },
        }
    ]


def test_validate_cell_ocr_mapping_proves_region_to_sheet_value_propagation() -> None:
    regions = [
        {
            "region_id": "E11",
            "ocr_contact_slot": [0, 0, 80, 60],
            "ocr_text": "12",
            "ocr_normalized": "12",
            "ocr_word_count": 1,
            "ocr_words": [{"text": "12", "x": 40, "y": 30}],
            "logical_targets": [
                {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                }
            ],
        }
    ]
    assignments = sheet_assignments_from_ocr_regions(regions)
    sheet_values = sheet_value_grid_from_assignments(assignments)

    validation = validate_cell_ocr_mapping(
        ocr_regions=regions,
        sheet_assignments=assignments,
        sheet_values=sheet_values,
    )

    assert validation["ok"] is True
    assert validation["error_count"] == 0
    assert validation["assigned_word_count"] == 1
    assert validation["recognized_region_count"] == 1
    assert validation["recognized_assignment_count"] == 1


def test_validate_cell_ocr_mapping_rejects_stale_parsed_text() -> None:
    regions = [
        {
            "region_id": "E11",
            "ocr_contact_slot": [0, 0, 80, 60],
            "ocr_text": "99",
            "ocr_normalized": "99",
            "ocr_word_count": 1,
            "ocr_words": [{"text": "12", "x": 40, "y": 30}],
            "logical_targets": [
                {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                }
            ],
        }
    ]
    assignments = sheet_assignments_from_ocr_regions(regions)
    sheet_values = sheet_value_grid_from_assignments(assignments)

    validation = validate_cell_ocr_mapping(
        ocr_regions=regions,
        sheet_assignments=assignments,
        sheet_values=sheet_values,
    )

    assert validation["ok"] is False
    assert any("raw OCR words do not reparse" in error for error in validation["errors"])


def test_best_method_entrypoint_uses_accepted_best_method_runtime(monkeypatch, tmp_path) -> None:
    fax_pdf = tmp_path / "fax.pdf"
    template_pdf = tmp_path / "template.pdf"
    step2_png = tmp_path / "step2.png"
    fax_pdf.write_bytes(b"%PDF-fax")
    template_pdf.write_bytes(b"%PDF-template")
    step2_png.write_bytes(b"png")
    item = {
        "facility_code": "FAC_TEST",
        "order_id": "ORD_TEST",
        "fax_pdf": str(fax_pdf),
        "template_pdf": str(template_pdf),
        "step2_png": str(step2_png),
    }
    calls = []

    def fake_best_method_runtime(**kwargs):
        calls.append(kwargs)
        return (
            {
                "engine": "opencv_knn_leave_one_out_k5",
                "metrics": {
                    "numeric_eval_cell_count": 10,
                    "pred_nonempty_count": 3,
                },
                "outputs": {
                    "records": str(tmp_path / "best_method_records.json"),
                    "ocr_regions": str(tmp_path / "best_method_ocr_regions.json"),
                    "overlay": str(tmp_path / "best_method_overlay.png"),
                },
            },
            "review-page",
        )

    fake_runtime_module = ModuleType("src.hakodate_best_method_runtime.render_best_method_overlay_all_facilities")
    fake_runtime_module.build_best_method_for_manifest_item = fake_best_method_runtime
    monkeypatch.setitem(
        sys.modules,
        "src.hakodate_best_method_runtime.render_best_method_overlay_all_facilities",
        fake_runtime_module,
    )

    result, review_page = hakodate_cell_ocr_batch_service.build_hakodate_best_method_for_manifest_item(
        item=item,
        page=1,
        draft_sheet={"rows": [["must", "not", "be", "truth"]]},
        output_dir=tmp_path,
    )

    assert result.ocr_engine == "opencv_knn_leave_one_out_k5"
    assert result.outputs["records"].endswith("best_method_records.json")
    assert result.physical_region_count == 10
    assert result.recognized_region_count == 3
    assert review_page == "review-page"
    assert calls == [
        {
            "item": item,
            "page_index": 1,
            "draft_sheet": {"rows": [["must", "not", "be", "truth"]]},
            "output_dir": tmp_path,
            "render_width": 1864,
        }
    ]


def test_local_yomitoku_word_parser_normalizes_absolute_boxes() -> None:
    words = _analysis_to_yomitoku_words(
        {
            "words": [
                {"content": "１２", "box": [10, 20, 30, 40]},
                {"contents": "A", "points": [[50, 60], [70, 60], [70, 80], [50, 80]]},
                {"content": "", "box": [0, 0, 1, 1]},
            ]
        },
        width=100,
        height=200,
    )

    assert words[0] == {
        "text": "１２",
        "x": pytest.approx(0.2),
        "y": pytest.approx(0.15),
        "box": pytest.approx([0.1, 0.1, 0.3, 0.2]),
    }
    assert words[1] == {"text": "A", "x": pytest.approx(0.6), "y": pytest.approx(0.35), "box": None}
