import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))
sys.path.append(str(ROOT.parent / "ocr_pipeline"))

from app.rois import crop_rois  # noqa: E402


def _blank_image():
    return np.full((120, 100, 3), 255, dtype=np.uint8)


def test_crop_rois_dynamic_rows_skips_header_rows_by_default():
    rois = crop_rois(
        _blank_image(),
        {
            "header_rows": 2,
            "rois": {
                "qty": {
                    "schema": {
                        "rows": 0,
                        "cols": 2,
                        "row_names": ["h0", "h1", "r0", "r1"],
                        "col_names": ["c0", "c1"],
                    },
                    "dynamic_rows": True,
                    "column_edges": [0.0, 0.5, 1.0],
                    "row_edges": [0.0, 0.15, 0.3, 0.65, 1.0],
                    "cell_inset_px": 0,
                },
                "table_box": [0.0, 0.0, 1.0, 1.0],
            },
        },
    )

    assert rois["qty_schema"]["rows"] == 2
    assert rois["qty_schema"]["row_names"] == ["r0", "r1"]
    assert len(rois["qty_cells"]) == 4


def test_crop_rois_dynamic_rows_respects_explicit_skip_with_row_box():
    rois = crop_rois(
        _blank_image(),
        {
            "header_rows": 0,
            "rois": {
                "qty": {
                    "schema": {
                        "rows": 0,
                        "cols": 2,
                        "row_names": ["h0", "h1", "r0", "r1"],
                        "col_names": ["c0", "c1"],
                    },
                    "dynamic_rows": True,
                    "column_edges": [0.0, 0.5, 1.0],
                    "row_edges": [0.0, 0.15, 0.3, 0.65, 1.0],
                    "row_box": [0.0, 0.0, 1.0, 1.0],
                    "skip_top_rows": 2,
                    "cell_inset_px": 0,
                },
                "table_box": [0.0, 0.0, 1.0, 1.0],
            },
        },
    )

    assert rois["qty_schema"]["rows"] == 2
    assert rois["qty_schema"]["row_names"] == ["r0", "r1"]
    assert len(rois["qty_cells"]) == 4
