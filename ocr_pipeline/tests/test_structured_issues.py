from __future__ import annotations

import unittest

from app.issue_detection import detect_table_cell_issues


class StructuredIssuesTest(unittest.TestCase):
    def test_detect_table_cell_issues_flags_multiline_and_merged_numeric_cells(self):
        issues = detect_table_cell_issues(
            tables=[
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "cells": [
                        {
                            "row_index": 2,
                            "col_index": 3,
                            "row_span": 1,
                            "col_span": 1,
                            "text": "6\n9",
                            "bbox": [0.1, 0.2, 0.2, 0.3],
                        },
                        {
                            "row_index": 3,
                            "col_index": 4,
                            "row_span": 2,
                            "col_span": 1,
                            "text": "48",
                            "bbox": [0.2, 0.3, 0.3, 0.4],
                        },
                        {
                            "row_index": 4,
                            "col_index": 5,
                            "row_span": 1,
                            "col_span": 1,
                            "text": "12/14\n(日)",
                            "bbox": [0.3, 0.4, 0.4, 0.5],
                        },
                    ],
                }
            ],
            template={"header_rows": 2},
        )

        issue_codes = {(issue["source_row_index"], issue["column_index"], issue["issue_code"]) for issue in issues}

        self.assertIn((0, 3, "multiline_numeric_cell"), issue_codes)
        self.assertIn((1, 4, "merged_numeric_cell"), issue_codes)


if __name__ == "__main__":
    unittest.main()
