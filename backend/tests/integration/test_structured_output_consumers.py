import pathlib
import sys
from datetime import datetime, date

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import config_service, order_service, output_builder  # noqa: E402
from src.workers import ingest_worker  # noqa: E402


def _ingest_payload(message_id: str):
    return type(
        "obj",
        (),
        {
            "message_id": message_id,
            "pdf_uri": "file:///tmp/dummy.pdf",
            "received_at": datetime(2026, 2, 15, 9, 0, 0),
            "facility_hint": "FAC00001",
            "week_hint": "2026-02",
        },
    )


def test_build_ocr_menu_meta_reads_structured_payload_from_cache():
    order_service.clear_all()
    order = order_service.create_order_from_ingest(_ingest_payload("structured-meta-001"), lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                ["日付", "区分", "献立", "数量", "備考"],
                                ["", "", "", "常食", ""],
                                ["2/15", "朝", "Menu A", "12", ""],
                                ["", "昼", "Menu B", "8", "note"],
                            ],
                        }
                    ],
                }
            ]
        },
    )

    meta = output_builder._build_ocr_menu_meta(
        order_service.get_order_by_id(order["id"]),
        {
            "fax_template": {
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_x",
                    "remarks",
                ]
            }
        },
    )

    entries = meta.get("entries") or []
    assert len(entries) == 2
    assert entries[0]["date"] == date(2026, 2, 15)
    assert entries[0]["menu_name"] == "Menu A"
    assert entries[0]["quantity_map"]["regular_x"] == 12.0
    assert entries[1]["daypart"] == "昼"
    assert entries[1]["note"] == "note"
    assert entries[1]["source"] == "structured_rows"


def test_build_ocr_menu_meta_merges_roi_overlay_quantities():
    payload = {
        "roi_overlay_policy": "merge",
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "備考"],
                            ["", "", "", "常食", ""],
                            ["2/15", "朝", "Menu A", "", ""],
                            ["", "昼", "Menu B", "", "note"],
                        ],
                    }
                ],
            }
        ],
        "roi_cell_issues": [
            {
                "row_index": 1,
                "field": "qty.regular_x",
                "issue_code": "low_confidence",
                "severity": "warning",
                "confidence": 0.42,
            }
        ],
        "roi_overlay_rows": [
            {"row_index": 0, "qty.regular_x": 12},
            {"row_index": 1, "qty.regular_x": 8},
        ],
    }
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ]
    }

    entries = output_builder._extract_ocr_entries_from_structured_payload(payload, template, 2026)

    assert len(entries) == 2
    assert entries[0]["date"] == date(2026, 2, 15)
    assert entries[0]["quantity_map"]["regular_x"] == 12.0
    assert entries[1]["quantity_map"]["regular_x"] == 8.0
    assert entries[1]["needs_review"] is True
    assert entries[1]["ocr_issues"][0]["issue_code"] == "low_confidence"


def test_build_ocr_menu_meta_marks_yomitoku_structured_cell_issues_for_review():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "備考"],
                            ["", "", "", "常食", ""],
                            ["2/15", "朝", "Menu A", "12", ""],
                            ["", "昼", "Menu B", "66", "note"],
                        ],
                    }
                ],
            }
        ],
        "yomitoku_cell_issues": [
            {
                "table_id": "p1_t1",
                "source_row_index": 1,
                "column_index": 3,
                "issue_code": "numeric_outlier",
                "severity": "warning",
                "value": 66,
                "max_allowed": 50,
                "source": "yomitoku_structured",
            }
        ],
    }
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ]
    }

    entries = output_builder._extract_ocr_entries_from_structured_payload(payload, template, 2026)

    assert len(entries) == 2
    assert entries[1]["needs_review"] is True
    assert entries[1]["ocr_issues"][0]["issue_code"] == "numeric_outlier"
    assert entries[1]["ocr_issues"][0]["column_index"] == 3


def test_build_ocr_menu_meta_derives_yomitoku_structured_review_issues_from_cells():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "備考"],
                            ["", "", "", "常食", ""],
                            ["2/15", "朝", "Menu A", "12", ""],
                            ["", "昼", "Menu B", "6\n9", "note"],
                        ],
                        "cells": [
                            {
                                "row_index": 3,
                                "col_index": 3,
                                "row_span": 1,
                                "col_span": 1,
                                "text": "6\n9",
                                "bbox": [0.10, 0.30, 0.18, 0.38],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ]
    }

    entries = output_builder._extract_ocr_entries_from_structured_payload(payload, template, 2026)

    assert len(entries) == 2
    assert entries[0].get("needs_review") is not True
    assert entries[1]["needs_review"] is True
    assert entries[1]["ocr_issues"][0]["issue_code"] == "multiline_numeric_cell"
    assert entries[1]["ocr_issues"][0]["source"] == "yomitoku_structured"


def test_build_ocr_menu_meta_exposes_review_required_entries():
    order_service.clear_all()
    order = order_service.create_order_from_ingest(_ingest_payload("structured-meta-review-001"), lines=[])
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "rows": [
                {
                    "row_index": 0,
                    "date_mmdd": "02/15",
                    "daypart": "朝",
                    "menu": "Menu A",
                    "qty": {"regular_x": 12},
                }
            ],
            "roi_cell_issues": [
                {
                    "row_index": 0,
                    "field": "qty.regular_x",
                    "issue_code": "sanity_fail",
                    "severity": "warning",
                    "value": 66,
                    "max_allowed": 50,
                }
            ],
        },
    )

    meta = output_builder._build_ocr_menu_meta(
        order_service.get_order_by_id(order["id"]),
        {
            "fax_template": {
                "main_ocr_row_fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_x",
                    "remarks",
                ]
            }
        },
    )

    entries = meta.get("entries") or []
    assert len(entries) == 1
    assert entries[0]["needs_review"] is True
    assert entries[0]["ocr_issues"][0]["issue_code"] == "sanity_fail"
    assert meta.get("review_required_count") == 1


def test_extract_ocr_entries_uses_payload_template_id_for_mixed_layout():
    payload = {
        "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
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
        ],
    }
    fallback_template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.soft_2f",
            "qty.soft_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
            "remarks",
        ]
    }

    entries = output_builder._extract_ocr_entries_from_structured_payload(payload, fallback_template, 2026)

    assert len(entries) == 1
    assert entries[0]["quantity_map"] == {
        "regular_x": 12.0,
        "regular_bag_x": 1.0,
        "soft_x": 2.0,
        "mixer_x": 3.0,
        "no_meat_x": 4.0,
        "no_fish_x": 5.0,
    }


def test_build_delivery_rows_uses_quantity_map_for_generic_area_columns():
    rows = output_builder._build_delivery_rows(
        {
            "id": "ORD-STRUCTURED-DELIVERY",
            "facility": "FAC00001",
            "lines": [],
        },
        {
            "prefer_ocr_raw_rows": True,
            "columns": [
                {
                    "name": "qty_regular_total",
                    "source": "quantity",
                    "diet_type": "regular",
                    "area_id": "X",
                }
            ],
        },
        {"zero_as_empty": True},
        {},
        {
            "entries": [
                {
                    "date": date(2026, 2, 15),
                    "daypart": "朝",
                    "category": "",
                    "menu_name": "Menu A",
                    "index": 0,
                    "quantity_map": {"regular_x": 12.0},
                    "note": "",
                    "source": "structured_rows",
                }
            ]
        },
    )

    assert len(rows) == 1
    assert rows[0]["qty_regular_total"] == 12.0
    assert rows[0]["menu_display"] == "Menu A"


def test_build_delivery_rows_sums_regular_bag_into_generic_regular_column():
    rows = output_builder._build_delivery_rows(
        {
            "id": "ORD-STRUCTURED-DELIVERY-BAG",
            "facility": "FAC00006",
            "lines": [],
        },
        {
            "prefer_ocr_raw_rows": True,
            "columns": [
                {
                    "name": "qty_regular_total",
                    "source": "quantity",
                    "diet_type": "regular",
                    "area_id": "X",
                }
            ],
        },
        {"zero_as_empty": True},
        {},
        {
            "entries": [
                {
                    "date": date(2026, 2, 15),
                    "daypart": "朝",
                    "category": "",
                    "menu_name": "Menu A",
                    "index": 0,
                    "quantity_map": {"regular_x": 12.0, "regular_bag_x": 3.0},
                    "note": "",
                    "source": "structured_rows",
                }
            ]
        },
    )

    assert len(rows) == 1
    assert rows[0]["qty_regular_total"] == 15.0


def test_build_delivery_rows_prefers_highest_regular_round_over_generic_regular_sum():
    rows = output_builder._build_delivery_rows(
        {
            "id": "ORD-STRUCTURED-DELIVERY-REGULAR-ROUNDS",
            "facility": "FAC00006",
            "lines": [],
        },
        {
            "prefer_ocr_raw_rows": True,
            "columns": [
                {
                    "name": "qty_regular_total",
                    "source": "quantity",
                    "diet_type": "regular",
                    "area_id": "X",
                }
            ],
        },
        {"zero_as_empty": True},
        {},
        {
            "entries": [
                {
                    "date": date(2026, 2, 15),
                    "daypart": "朝",
                    "category": "",
                    "menu_name": "Menu A",
                    "index": 0,
                    "quantity_map": {
                        "regular_x": 11.0,
                        "change_1_x": 13.0,
                        "change_2_x": 17.0,
                        "regular_bag_x": 3.0,
                    },
                    "note": "",
                    "source": "structured_rows",
                }
            ]
        },
    )

    assert len(rows) == 1
    assert rows[0]["qty_regular_total"] == 17.0


def test_build_delivery_rows_does_not_duplicate_generic_regular_into_specific_area_column():
    rows = output_builder._build_delivery_rows(
        {
            "id": "ORD-STRUCTURED-DELIVERY-AREA",
            "facility": "FAC00006",
            "lines": [],
        },
        {
            "prefer_ocr_raw_rows": True,
            "columns": [
                {
                    "name": "qty_regular_2f",
                    "source": "quantity",
                    "diet_type": "regular",
                    "area_id": "2F",
                }
            ],
        },
        {"zero_as_empty": True},
        {},
        {
            "entries": [
                {
                    "date": date(2026, 2, 15),
                    "daypart": "朝",
                    "category": "",
                    "menu_name": "Menu A",
                    "index": 0,
                    "quantity_map": {"regular_x": 12.0, "regular_bag_x": 3.0},
                    "note": "",
                    "source": "structured_rows",
                }
            ]
        },
    )

    assert len(rows) == 1
    assert rows[0].get("qty_regular_2f") is None


def test_build_delivery_rows_sums_forbidden_subcategories_for_generic_forbidden_column():
    rows = output_builder._build_delivery_rows(
        {
            "id": "ORD-STRUCTURED-FORBIDDEN",
            "facility": "FAC00002",
            "lines": [],
        },
        {
            "prefer_ocr_raw_rows": True,
            "columns": [
                {
                    "name": "qty_forbidden_total",
                    "source": "quantity",
                    "diet_type": "禁食",
                    "area_id": "X",
                }
            ],
        },
        {"zero_as_empty": True},
        {},
        {
            "entries": [
                {
                    "date": date(2026, 2, 15),
                    "daypart": "朝",
                    "category": "",
                    "menu_name": "Menu A",
                    "index": 0,
                    "quantity_map": {"no_meat_x": 3.0, "no_fish_x": 4.0},
                    "note": "",
                    "source": "structured_rows",
                }
            ]
        },
    )

    assert len(rows) == 1
    assert rows[0]["qty_forbidden_total"] == 7.0


def test_build_pipeline_match_text_includes_structured_rows_for_facility_match():
    match_text = ingest_worker._build_pipeline_match_text(
        {
            "pages": [
                {
                    "page_index": 1,
                    "tables": [
                        {
                            "table_id": "p1_t1",
                            "rows": [
                                ["施設名"],
                                ["大和なでしこ"],
                            ],
                        }
                    ],
                }
            ]
        }
    )

    assert "大和なでしこ" in match_text
    candidates = config_service.match_facility_candidates(match_text)
    assert candidates
    assert candidates[0]["facility_id"] == "FAC00001"
    assert candidates[0]["auto"] is True


def test_build_pipeline_match_text_includes_roi_facility_name():
    match_text = ingest_worker._build_pipeline_match_text(
        {
            "roi_extraction": {
                "facility_name": "大和なでしこ",
                "menu_band": "Menu A",
            }
        }
    )

    assert "大和なでしこ" in match_text
    candidates = config_service.match_facility_candidates(match_text)
    assert candidates
    assert candidates[0]["facility_id"] == "FAC00001"


def test_extract_ocr_entries_reads_generic_cell_issues():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "備考"],
                            ["", "", "", "常食", ""],
                            ["2/15", "朝", "Menu A", "12", ""],
                        ],
                    }
                ],
            }
        ],
        "cell_issues": [
            {
                "table_id": "p1_t1",
                "page_index": 1,
                "source_row_index": 0,
                "column_index": 3,
                "issue_code": "merged_numeric_cell",
                "severity": "high",
                "source": "yomitoku_structured",
                "bbox": [0.1, 0.2, 0.3, 0.4],
            }
        ],
    }
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ]
    }

    entries = output_builder._extract_ocr_entries_from_structured_payload(payload, template, 2026)

    assert len(entries) == 1
    assert entries[0]["needs_review"] is True
    assert entries[0]["ocr_issues"][0]["issue_code"] == "merged_numeric_cell"
    assert entries[0]["ocr_issues"][0]["source"] == "yomitoku_structured"


def test_extract_ocr_entries_reads_llm_review_issues_from_latest_revision():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日付", "区分", "献立", "数量", "備考"],
                            ["", "", "", "常食", ""],
                            ["2/15", "朝", "Menu A", "12", ""],
                        ],
                    }
                ],
            }
        ],
        "_edited_ocr": {
            "latest": {
                "llm_review": {
                    "issues": [
                        {
                            "issue_id": "iss-1",
                            "row_id": "row-1",
                            "row_index": 0,
                            "source_row_index": 0,
                            "field": "qty.regular_x",
                            "issue_code": "review_required",
                            "reason": "digit uncertain",
                            "source": "llm_review",
                        }
                    ]
                }
            }
        },
    }
    template = {
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "remarks",
        ]
    }

    entries = output_builder._extract_ocr_entries_from_structured_payload(payload, template, 2026)

    assert len(entries) == 1
    assert entries[0]["needs_review"] is True
    assert entries[0]["ocr_issues"][0]["issue_code"] == "review_required"
    assert entries[0]["ocr_issues"][0]["source"] == "llm_review"


def test_extract_ocr_entries_ignores_fureai_total_aux_column_for_quantity_map():
    payload = {
        "pages": [
            {
                "page_index": 1,
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "rows": [
                            ["日 付", "区 分", "", "献立", "合計", "#☆", "通所", "職員", "平森", "肉蒸", "魚禁", "揚物禁", "変更1", "備考欄"],
                            ["3/22\n(日)", "材", "副作\n四", "厚揚げとさつま芋の煮物", "", "72", "", "", "", "", "", "", "", ""],
                            ["", "香", "±A", "鶏じゃが", "67", "66", "", "", "", "", "", "", "", "鶏魚1"],
                        ],
                    }
                ],
            }
        ]
    }
    template = {
        "columns": [
            {"index": 0, "role": "date", "header": "日付"},
            {"index": 1, "role": "daypart", "header": "区分"},
            {"index": 2, "role": "aux", "header": "副区分"},
            {"index": 3, "role": "menu_name", "header": "献立"},
            {"index": 4, "role": "aux", "header": "合計"},
            {"index": 5, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
            {"index": 6, "role": "quantity", "header": "通所", "diet_type": "daycare", "area_id": "X"},
            {"index": 7, "role": "quantity", "header": "職員", "diet_type": "staff", "area_id": "X"},
            {"index": 8, "role": "quantity", "header": "肉禁", "diet_type": "no_meat", "area_id": "X"},
            {"index": 9, "role": "quantity", "header": "魚禁", "diet_type": "no_fish", "area_id": "X"},
            {"index": 10, "role": "quantity", "header": "揚げ物禁", "diet_type": "no_fried", "area_id": "X"},
            {"index": 11, "role": "quantity", "header": "変更1", "diet_type": "change_1", "area_id": "X"},
            {"index": 12, "role": "note", "header": "備考欄"},
        ],
        "main_ocr_row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
    }

    entries = output_builder._extract_ocr_entries_from_structured_payload(payload, template, 2026)

    assert len(entries) == 2
    assert entries[0]["quantity_map"] == {"regular_x": 72.0}
    assert entries[1]["quantity_map"] == {"regular_x": 66.0}
