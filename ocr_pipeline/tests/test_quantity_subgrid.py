import pathlib
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from app import quantity_subgrid  # noqa: E402


class QuantitySubgridTests(unittest.TestCase):
    def test_normalize_quantity_subgrid_table_rows_uses_neighbor_consensus_for_confusables(self):
        table = {
            "rows": [
                ["4", "2", "2", "5", "2", "", ""],
                ["4", "で", "2", "5", "2.", "", ""],
                ["4", "2", "2", "5", "て", "", ""],
            ]
        }

        normalized_rows, patches = quantity_subgrid.normalize_quantity_subgrid_table_rows(table)

        self.assertEqual(normalized_rows[1][1], "2")
        self.assertEqual(normalized_rows[1][4], "2")
        self.assertEqual(normalized_rows[2][4], "2")
        self.assertEqual(
            [(patch["row_index"], patch["col_index"], patch["normalized_text"]) for patch in patches],
            [(1, 1, "2"), (1, 4, "2"), (2, 4, "2")],
        )

    def test_build_quantity_subgrid_second_passes_crops_numeric_band_and_runs_yomitoku(self):
        page_image = np.zeros((100, 200, 3), dtype=np.uint8)
        full_page = SimpleNamespace(
            page_index=1,
            tables=[
                {
                    "row_count": 4,
                    "col_count": 7,
                    "rows": [
                        ["日付", "区分", "", "献立", "常食", "軟菜", "備考"],
                        ["", "", "", "", "2F", "2F", ""],
                        ["3/22", "朝", "", "Menu A", "4", "5", ""],
                        ["", "", "", "Menu B", "4", "5", ""],
                    ],
                    "cells": [
                        {"row_index": 0, "col_index": 3, "bbox": [0.25, 0.05, 0.45, 0.12]},
                        {"row_index": 0, "col_index": 4, "bbox": [0.46, 0.05, 0.58, 0.12]},
                        {"row_index": 0, "col_index": 5, "bbox": [0.59, 0.05, 0.71, 0.12]},
                        {"row_index": 2, "col_index": 3, "bbox": [0.25, 0.20, 0.45, 0.28]},
                        {"row_index": 2, "col_index": 4, "bbox": [0.46, 0.20, 0.58, 0.28]},
                        {"row_index": 2, "col_index": 5, "bbox": [0.59, 0.20, 0.71, 0.28]},
                        {"row_index": 3, "col_index": 3, "bbox": [0.25, 0.29, 0.45, 0.37]},
                        {"row_index": 3, "col_index": 4, "bbox": [0.46, 0.29, 0.58, 0.37]},
                        {"row_index": 3, "col_index": 5, "bbox": [0.59, 0.29, 0.71, 0.37]},
                    ],
                }
            ],
        )
        sub_page = SimpleNamespace(
            markdown_text="|4|2|\n|-|-|\n|4|で|\n|4|2|",
            tables=[{"rows": [["4", "2"], ["4", "で"], ["4", "2"]], "row_count": 3, "col_count": 2}],
        )

        with mock.patch.object(
            quantity_subgrid,
            "run_yomitoku",
            return_value=([sub_page], b"OCRPDF", b"LAYOUTPDF"),
        ) as run_mock:
            passes = quantity_subgrid.build_quantity_subgrid_second_passes(
                page_results=[full_page],
                page_images=[(1, page_image)],
                dpi=200,
                device="cpu",
                visualize=True,
                ignore_line_break=True,
                no_figure=True,
                figure_width=800,
                figure_dir="figures",
                max_passes=1,
            )

        self.assertEqual(len(passes), 1)
        item = passes[0]
        self.assertEqual(item.page_index, 1)
        self.assertEqual(item.table_index, 0)
        self.assertEqual(item.spec.body_start_row, 2)
        self.assertGreaterEqual(item.spec.quantity_start_col_index, 1)
        self.assertEqual(item.markdown_text, "|4|2|\n|-|-|\n|4|で|\n|4|2|")
        self.assertEqual(item.normalized_rows, [["4", "2"], ["4", "2"], ["4", "2"]])
        self.assertEqual(len(item.normalization_patches), 1)
        self.assertEqual(item.normalization_patches[0]["normalized_text"], "2")
        self.assertEqual(item.ocr_pdf, b"OCRPDF")
        self.assertEqual(item.layout_pdf, b"LAYOUTPDF")
        self.assertTrue(item.crop_png_bytes)
        run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
