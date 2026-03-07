import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from app.issue_detection import detect_table_cell_issues, merge_cell_issues  # noqa: E402


class IssueDetectionTests(unittest.TestCase):
    def test_detect_table_cell_issues_flags_multiline_and_merged_numeric_cells(self):
        issues = detect_table_cell_issues(
            tables=[
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "cells": [
                        {
                            "row_index": 2,
                            "col_index": 4,
                            "row_span": 2,
                            "col_span": 1,
                            "text": "6\n9",
                            "bbox": [0.1, 0.2, 0.3, 0.4],
                        },
                        {
                            "row_index": 2,
                            "col_index": 0,
                            "row_span": 6,
                            "col_span": 1,
                            "text": "12/14\n(日)",
                            "bbox": [0.0, 0.0, 0.1, 0.2],
                        },
                    ],
                }
            ]
        )

        self.assertEqual(
            {(issue["issue_code"], issue["row_index"], issue["col_index"]) for issue in issues},
            {
                ("multiline_numeric_cell", 2, 4),
                ("merged_numeric_cell", 2, 4),
            },
        )
        merged = next(issue for issue in issues if issue["issue_code"] == "merged_numeric_cell")
        self.assertEqual(merged["bbox"], [0.1, 0.2, 0.3, 0.4])
        self.assertEqual(merged["row_span"], 2)
        self.assertEqual(merged["source"], "yomitoku_structured")

    def test_detect_table_cell_issues_flags_floor_header_template_mismatch(self):
        issues = detect_table_cell_issues(
            tables=[
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "bbox": [0.0, 0.1, 0.9, 0.9],
                    "rows": [
                        ["日付", "区分", "献立", "常食", "軟菜", "ミキサー"],
                        ["", "", "", "", "禁食", "魚禁"],
                    ],
                }
            ],
            template_id="fax_layout_floor_2f3f_v1",
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["issue_code"], "header_template_mismatch")
        self.assertEqual(issues[0]["matched_template_id"], "fax_layout_floor_2f3f_v1")
        self.assertIn("禁食", issues[0]["header_tokens"])

    def test_merge_cell_issues_preserves_structured_and_roi_issues(self):
        merged = merge_cell_issues(
            [
                {
                    "source": "yomitoku_structured",
                    "page_index": 1,
                    "table_id": "p1_t1",
                    "row_index": 2,
                    "col_index": 4,
                    "issue_code": "merged_numeric_cell",
                }
            ],
            [
                {
                    "source": "template_roi",
                    "page_index": 1,
                    "table_id": "",
                    "row_index": 2,
                    "col_index": 4,
                    "issue_code": "low_confidence",
                }
            ],
        )

        self.assertEqual(len(merged), 2)
        self.assertEqual(
            {issue["source"] for issue in merged},
            {"yomitoku_structured", "template_roi"},
        )


if __name__ == "__main__":
    unittest.main()
