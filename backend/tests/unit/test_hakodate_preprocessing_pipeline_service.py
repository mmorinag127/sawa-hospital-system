import numpy as np

from src.services.hakodate_preprocessing_pipeline_service import (
    snap_target_region_x_boundaries,
    target_cell_map_from_regions,
)


def test_snap_target_region_x_boundaries_uses_detected_fax_lines() -> None:
    image = np.full((140, 260, 3), 255, dtype=np.uint8)
    image[10:130, 48:51] = 0
    image[10:130, 108:111] = 0
    image[10:130, 168:171] = 0
    regions = [
        {"region_id": "E11", "sheet_cell": "E11", "bbox": [50.0, 20.0, 110.0, 60.0]},
        {"region_id": "F11", "sheet_cell": "F11", "bbox": [110.0, 20.0, 170.0, 60.0]},
    ]

    snapped, evidence = snap_target_region_x_boundaries(image, regions)

    assert evidence["applied"] is True
    assert evidence["snapped_boundaries"] == [49, 109, 169]
    assert snapped[0]["bbox"] == [49.0, 20.0, 109.0, 60.0]
    assert snapped[1]["bbox"] == [109.0, 20.0, 169.0, 60.0]


def test_target_cell_map_preserves_sheet_identity_and_centers() -> None:
    cells = target_cell_map_from_regions(
        [
            {
                "region_id": "E11",
                "sheet_cell": "E11",
                "worksheet_row": 11,
                "worksheet_col": 5,
                "field": "qty.regular",
                "bbox": [10.0, 20.0, 30.0, 60.0],
                "x_snap": {"snapped_left": 10, "snapped_right": 30},
            }
        ]
    )

    assert cells == [
        {
            "target_cell_id": "E11",
            "region_id": "E11",
            "sheet_cell": "E11",
            "worksheet_row": 11,
            "worksheet_col": 5,
            "semantic_field": "qty.regular",
            "field_label": None,
            "date": None,
            "daypart": None,
            "menu_name": None,
            "bbox": [10.0, 20.0, 30.0, 60.0],
            "center": [20.0, 40.0],
            "merged_cell": None,
            "logical_targets": [],
            "covered_sheet_cells": [],
            "x_snap": {"snapped_left": 10, "snapped_right": 30},
        }
    ]


def test_target_cell_map_uses_logical_target_metadata() -> None:
    cells = target_cell_map_from_regions(
        [
            {
                "region_id": "E11:E12",
                "sheet_cell": "E11",
                "worksheet_row": 11,
                "worksheet_col": 5,
                "field": "qty.regular",
                "bbox": [10.0, 20.0, 30.0, 80.0],
                "merged_cell": {"range": "E11:E12"},
                "covered_sheet_cells": ["E11", "E12"],
                "logical_targets": [
                    {
                        "sheet_cell": "E11",
                        "worksheet_row": 11,
                        "worksheet_col": 5,
                        "date": "2026-04-26",
                        "daypart": "朝",
                        "menu_name": "肉じゃが",
                    },
                    {
                        "sheet_cell": "E12",
                        "worksheet_row": 12,
                        "worksheet_col": 5,
                        "date": "2026-04-26",
                        "daypart": "昼",
                        "menu_name": "魚焼",
                    },
                ],
            }
        ]
    )

    assert cells[0]["date"] == "2026-04-26"
    assert cells[0]["daypart"] == "朝"
    assert cells[0]["menu_name"] == "肉じゃが"
    assert cells[0]["covered_sheet_cells"] == ["E11", "E12"]
    assert cells[0]["merged_cell"] == {"range": "E11:E12"}
