from __future__ import annotations

import unittest

import numpy as np

from app.rois import crop_rois


class HeaderSchemaInferenceTest(unittest.TestCase):
    def test_crop_rois_infers_quantity_columns_from_header_tokens(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        template = {
            "table_box": [0.0, 0.0, 1.0, 1.0],
            "auto_header_band": [0.10, 0.20],
            "auto_headers": [
                {"name": "regular_x", "match_groups": [["常食"]]},
                {"name": "diabetes_x", "match_groups": [["糖尿"]]},
            ],
            "rois": {
                "qty": {
                    "schema": {
                        "rows": 2,
                        "cols": 2,
                        "row_names": ["r0", "r1"],
                        "col_names": [],
                    },
                    "boxes_row_major": [],
                    "row_edges": [0.20, 0.60, 1.00],
                }
            },
        }
        words = [
            {"text": "常食", "x": 0.40, "y": 0.15},
            {"text": "糖尿", "x": 0.70, "y": 0.15},
        ]

        rois = crop_rois(image, template, ocr_words=words)

        schema = rois["qty_schema"]
        self.assertEqual(schema["col_names"], ["regular_x", "diabetes_x"])
        self.assertEqual(schema["rows"], 2)
        self.assertEqual(schema["cols"], 2)
        self.assertEqual(len(rois["qty_cells"]), 4)


if __name__ == "__main__":
    unittest.main()
