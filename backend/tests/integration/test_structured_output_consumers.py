import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import config_service, output_builder  # noqa: E402
from src.workers import ingest_worker  # noqa: E402


def test_build_delivery_rows_ignores_legacy_ocr_raw_rows():
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
                    "quantity_map": {"regular_x": 99.0},
                    "note": "",
                    "source": "structured_rows",
                }
            ]
        },
    )

    assert rows == []


def test_build_delivery_rows_uses_only_materialized_saved_sheet_rows():
    rows = output_builder._build_delivery_rows(
        {
            "id": "ORD-MATERIALIZED-DELIVERY",
            "facility": "FAC00001",
            "lines": [
                {
                    "date": date(2026, 2, 15),
                    "daypart": "朝",
                    "menu_category": "主菜",
                    "menu_name": "Menu A",
                    "diet_type": "regular",
                    "area_id": "X",
                    "quantity": 12.0,
                    "source_row_index": 0,
                }
            ],
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
                    "category": "主菜",
                    "menu_name": "Menu A",
                    "index": 0,
                    "quantity_map": {"regular_x": 99.0},
                    "note": "",
                    "source": "structured_rows",
                }
            ]
        },
    )

    assert len(rows) == 1
    assert rows[0]["qty_regular_total"] == 12.0
    assert rows[0]["menu_display"] == "主菜 Menu A"


def test_menu_entry_override_does_not_replace_existing_daypart_or_category():
    rows = output_builder._apply_menu_entry_overrides(
        [
            {
                "date": date(2026, 5, 24),
                "daypart": "夕",
                "menu_category": "主菜",
                "menu_name": "Menu A",
                "quantity_original": 1,
            }
        ],
        [
            {
                "menu_date": "2026-05-24",
                "daypart": "昼",
                "category": "副菜",
                "name": "Menu A",
            }
        ],
    )

    assert rows[0]["daypart"] == "夕"
    assert rows[0]["menu_category"] == "主菜"


def test_menu_entry_override_fills_missing_daypart_and_category():
    rows = output_builder._apply_menu_entry_overrides(
        [
            {
                "date": date(2026, 5, 24),
                "menu_name": "Menu A",
                "quantity_original": 1,
            }
        ],
        [
            {
                "menu_date": "2026-05-24",
                "daypart": "昼",
                "category": "副菜",
                "name": "Menu A",
            }
        ],
    )

    assert rows[0]["daypart"] == "昼"
    assert rows[0]["menu_category"] == "副菜"


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
