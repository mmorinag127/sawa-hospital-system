import numpy as np

from src.services.hakodate_step_review_pipeline_service import (
    snap_regions_x_to_local_fax_rulings,
)


def test_snap_regions_x_to_fax_lines_uses_row_local_ruling_shift() -> None:
    image = np.full((260, 260, 3), 255, dtype=np.uint8)
    template_xs = [50, 100, 150, 200]
    row_ys = [30, 80, 130, 180, 230]
    for row_index, y in enumerate(row_ys):
        shift = row_index * 3
        image[max(0, y - 1) : y + 2, 40:220] = 0
        for x in template_xs:
            image[max(0, y - 18) : min(image.shape[0], y + 18), x + shift - 1 : x + shift + 2] = 0
    regions = [
        {"region_id": "E11", "sheet_cell": "E11", "bbox": [50.0, 30.0, 100.0, 80.0]},
        {"region_id": "F11", "sheet_cell": "F11", "bbox": [100.0, 30.0, 150.0, 80.0]},
        {"region_id": "E12", "sheet_cell": "E12", "bbox": [50.0, 180.0, 100.0, 230.0]},
        {"region_id": "F12", "sheet_cell": "F12", "bbox": [100.0, 180.0, 150.0, 230.0]},
    ]

    snapped, evidence = snap_regions_x_to_local_fax_rulings(image, regions)

    assert evidence["applied"] is True
    by_cell = {region["sheet_cell"]: region for region in snapped}
    top_delta = by_cell["E11"]["bbox"][0] - regions[0]["bbox"][0]
    bottom_delta = by_cell["E12"]["bbox"][0] - regions[2]["bbox"][0]
    assert top_delta > 0.0
    assert bottom_delta > top_delta + 5.0
    assert by_cell["E12"]["local_grid_snap"]["method"] == "row_edge_local_fax_ruling_polygon_snap_v5"


def test_snap_regions_x_to_fax_lines_blocks_when_matches_are_insufficient() -> None:
    image = np.full((120, 160, 3), 255, dtype=np.uint8)
    regions = [
        {"region_id": "E11", "sheet_cell": "E11", "bbox": [50.0, 30.0, 100.0, 80.0]},
        {"region_id": "F11", "sheet_cell": "F11", "bbox": [100.0, 30.0, 150.0, 80.0]},
    ]

    snapped, evidence = snap_regions_x_to_local_fax_rulings(image, regions)

    assert evidence["applied"] is False
    assert evidence["reason"] == "local_grid_snap_insufficient_matches"
    assert snapped == regions


def test_snap_regions_x_to_fax_lines_uses_lowest_outer_bottom_ruling() -> None:
    image = np.full((260, 260, 3), 255, dtype=np.uint8)
    template_xs = [50, 100, 150]
    actual_bottom = 170
    for y in [40, 90, 140, actual_bottom]:
        image[max(0, y - 1) : y + 2, 40:180] = 0
        for x in template_xs:
            image[max(0, y - 18) : min(image.shape[0], y + 18), x - 1 : x + 2] = 0
    regions = [
        {"region_id": "E11", "sheet_cell": "E11", "bbox": [50.0, 40.0, 100.0, 90.0]},
        {"region_id": "E12", "sheet_cell": "E12", "bbox": [50.0, 140.0, 100.0, 240.0]},
        {"region_id": "F12", "sheet_cell": "F12", "bbox": [100.0, 140.0, 150.0, 240.0]},
    ]

    snapped, evidence = snap_regions_x_to_local_fax_rulings(image, regions)

    assert evidence["applied"] is True
    assert evidence["outer_y_snap"]["bottom_template_y"] == 240
    assert abs(float(evidence["outer_y_snap"]["bottom_snapped_y"]) - actual_bottom) < 2.0
    by_cell = {region["sheet_cell"]: region for region in snapped}
    assert abs(float(by_cell["E12"]["bbox"][3]) - actual_bottom) < 2.0
    assert abs(float(by_cell["F12"]["bbox"][3]) - actual_bottom) < 2.0
