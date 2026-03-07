import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service  # noqa: E402
from src.services.fax_extractor import (  # noqa: E402
    _rows_from_pipeline_payload,
    rows_from_structured_payload,
)


def _template() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.soft_x",
            "remarks",
        ]
    }


def _template_staff_daycare() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.daycare_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "remarks",
        ]
    }


def _template_mixed() -> dict:
    return {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.regular_bag_x",
            "qty.soft_x",
            "qty.mixer_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "remarks",
        ]
    }


def test_rows_from_pipeline_payload_merges_multiple_markdown_tables():
    payload = {
        "table_raw": """
|日付|区分|献立|常食|軟菜|備考|
|-|-|-|-|-|-|
|2/15|朝|じゃが芋のコンソメ煮|20|1||

|日付|区分|献立|常食|軟菜|備考|
|-|-|-|-|-|-|
|2/15|昼|キャベツサラダ|18|2|note|
""".strip()
    }

    rows = _rows_from_pipeline_payload(payload, _template())

    assert rows == [
        ["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""],
        ["2/15", "昼", "キャベツサラダ", "18", "2", "note"],
    ]


def test_rows_from_structured_payload_maps_multi_row_headers_and_merges_tables():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "", "備考"],
                            ["", "", "", "常食", "軟菜", ""],
                            ["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""],
                        ],
                    },
                    {
                        "table_id": "p1_t2",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "", "備考"],
                            ["", "", "", "常食", "軟菜", ""],
                            ["2/15", "昼", "キャベツサラダ", "18", "2", "note"],
                        ],
                    },
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template())

    assert rows == [
        ["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""],
        ["2/15", "昼", "キャベツサラダ", "18", "2", "note"],
    ]


def test_extract_sheet_rows_from_payload_prefers_structured_tables_over_table_raw():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "", "備考"],
                            ["", "", "", "常食", "軟菜", ""],
                            ["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""],
                            ["2/15", "昼", "キャベツサラダ", "18", "2", "note"],
                        ],
                    }
                ],
            }
        ],
        "table_raw": """
|日付|区分|献立|常食|軟菜|備考|
|-|-|-|-|-|-|
|2/15|朝|誤ったメニュー|99|9|wrong|
""".strip(),
    }

    rows = order_service._extract_sheet_rows_from_payload(payload, _template())

    assert rows == [
        ["2/15", "朝", "じゃが芋のコンソメ煮", "20", "1", ""],
        ["2/15", "昼", "キャベツサラダ", "18", "2", "note"],
    ]


def test_extract_sheet_rows_from_payload_merges_roi_overlay_rows():
    payload = {
        "roi_overlay_policy": "merge",
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "軟菜", "備考"],
                            ["2/15", "朝", "じゃが芋のコンソメ煮", "", "", ""],
                            ["2/15", "昼", "キャベツサラダ", "", "", "note"],
                        ],
                    }
                ],
            }
        ],
        "roi_overlay_rows": [
            {"row_index": 0, "qty.regular_x": 12},
            {"row_index": 1, "qty.soft_x": 2},
        ],
    }

    rows = order_service._extract_sheet_rows_from_payload(payload, _template())

    assert rows == [
        ["2/15", "朝", "じゃが芋のコンソメ煮", "12", "", ""],
        ["2/15", "昼", "キャベツサラダ", "", "2", "note"],
    ]


def test_extract_sheet_rows_from_payload_keeps_structured_rows_primary_when_overlay_is_audit_only():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "軟菜", "備考"],
                            ["2/15", "朝", "じゃが芋のコンソメ煮", "7", "", ""],
                            ["2/15", "昼", "キャベツサラダ", "", "3", "note"],
                        ],
                    }
                ],
            }
        ],
        "roi_overlay_rows": [
            {"row_index": 0, "qty.regular_x": 12},
            {"row_index": 1, "qty.soft_x": 2},
        ],
    }

    rows = order_service._extract_sheet_rows_from_payload(payload, _template())

    assert rows == [
        ["2/15", "朝", "じゃが芋のコンソメ煮", "7", "", ""],
        ["2/15", "昼", "キャベツサラダ", "", "3", "note"],
    ]


def test_rows_from_structured_payload_maps_staff_daycare_and_forbidden_subheaders():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "職員", "通所", "禁食", "", "備考"],
                            ["", "", "", "", "", "", "肉禁", "魚禁", ""],
                            ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "note"],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_staff_daycare())

    assert rows == [
        ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "note"],
    ]


def test_rows_from_structured_payload_maps_mixed_regular_bag_and_forbidden_subheaders():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "常食", "", "軟菜", "ミキサー", "禁食", "", "備考"],
                            ["", "", "", "通常", "袋分け", "", "", "肉禁", "魚禁", ""],
                            ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "5", "note"],
                        ],
                    }
                ],
            }
        ]
    }

    rows = rows_from_structured_payload(payload, _template_mixed())

    assert rows == [
        ["2/15", "朝", "Menu A", "12", "1", "2", "3", "4", "5", "note"],
    ]


def test_extract_payload_cell_issues_derives_yomitoku_multiline_and_merged_quantity_cells():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "", "備考"],
                            ["", "", "", "常食", "軟菜", ""],
                            ["2/15", "朝", "Menu A", "12", "", ""],
                            ["2/15", "昼", "Menu B", "6\n9", "", ""],
                        ],
                        "cells": [
                            {
                                "row_index": 2,
                                "col_index": 3,
                                "row_span": 2,
                                "col_span": 1,
                                "text": "12",
                                "bbox": [0.10, 0.20, 0.18, 0.28],
                            },
                            {
                                "row_index": 3,
                                "col_index": 3,
                                "row_span": 1,
                                "col_span": 1,
                                "text": "6\n9",
                                "bbox": [0.10, 0.30, 0.18, 0.38],
                            },
                        ],
                    }
                ],
            }
        ]
    }

    issues = order_service._extract_payload_cell_issues(payload, _template())

    assert len(issues) == 2
    merged_issue = next(issue for issue in issues if issue["issue_code"] == "merged_numeric_cell")
    multiline_issue = next(issue for issue in issues if issue["issue_code"] == "multiline_numeric_cell")
    assert merged_issue["field"] == "qty.regular_x"
    assert merged_issue["source"] == "yomitoku_structured"
    assert merged_issue["source_row_index"] == 0
    assert merged_issue["row_span"] == 2
    assert multiline_issue["field"] == "qty.regular_x"
    assert multiline_issue["source"] == "yomitoku_structured"
    assert multiline_issue["source_row_index"] == 1
    assert multiline_issue["value"] == "6\n9"
