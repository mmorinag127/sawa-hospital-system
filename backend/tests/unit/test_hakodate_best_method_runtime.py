import numpy as np

from src.hakodate_best_method_runtime.render_best_method_overlay_all_facilities import (
    _snap_regions_x_to_fax_lines_all_targets,
)


def _regions_for_boundaries(boundaries: list[int]) -> list[dict]:
    regions: list[dict] = []
    for left, right in zip(boundaries, boundaries[1:]):
        regions.append(
            {
                "bbox": [float(left), 60.0, float(right), 220.0],
                "sheet_cell": f"R11C{len(regions) + 5}",
            }
        )
    return regions


def test_runtime_x_snap_is_disabled_after_axis_alignment() -> None:
    rectified = np.full((280, 460, 3), 255, dtype=np.uint8)
    for x in [60, 160, 260, 360]:
        rectified[40:240, x - 1 : x + 2] = 0
    rectified[65:215, 210 - 5 : 210 + 6] = 0
    regions = _regions_for_boundaries([60, 160, 260, 360])

    snapped, evidence = _snap_regions_x_to_fax_lines_all_targets(rectified, regions)

    assert evidence["applied"] is False
    assert evidence["reason"] == "disabled_after_header_intersection_axis_alignment"
    assert snapped == regions


def test_runtime_x_snap_does_not_move_regions_when_spurious_line_exists() -> None:
    rectified = np.full((280, 460, 3), 255, dtype=np.uint8)
    for x in [60, 160, 360]:
        rectified[40:240, x - 1 : x + 2] = 0
    rectified[40:240, 210 - 5 : 210 + 6] = 0
    regions = _regions_for_boundaries([60, 160, 260, 360])

    snapped, evidence = _snap_regions_x_to_fax_lines_all_targets(rectified, regions)

    assert evidence["applied"] is False
    assert evidence["reason"] == "disabled_after_header_intersection_axis_alignment"
    assert snapped == regions
