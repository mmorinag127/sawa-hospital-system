import pathlib
import sys
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service, output_builder  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _seed_order(message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy-change-override.pdf",
        received_at=datetime(2026, 4, 19, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    return order_service.create_order_from_ingest(payload, lines=[])


def _fac00001_fields() -> list[str]:
    return [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "qty.unknown_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.change_1_x",
        "qty.change_2_x",
        "remarks",
    ]


def _fac00001_header() -> list[str]:
    return [
        "日付",
        "区分",
        "メニュー",
        "常食",
        "不明",
        "肉禁",
        "魚禁",
        "変更1",
        "変更2",
        "備考",
    ]


def test_materialization_lines_prioritize_change2_then_change1_over_regular():
    lines = order_service._build_materialization_lines_from_sheet_rows(
        fields=_fac00001_fields(),
        rows_payload=[
            ["04/19", "昼", "Menu A", "31", "", "", "", "33", "34", ""],
            ["04/19", "夕", "Menu B", "32", "", "", "", "35", "", ""],
            ["04/19", "夕", "Menu C", "36", "", "", "", "", "", ""],
        ],
        received_at=datetime(2026, 4, 19, 9, 0, 0),
    )

    assert [(line["menu_name"], line["diet_type"], line["quantity_original"]) for line in lines] == [
        ("Menu A", "regular", 34),
        ("Menu B", "regular", 35),
        ("Menu C", "regular", 36),
    ]


def test_materialization_lines_keep_forbidden_columns_separate():
    lines = order_service._build_materialization_lines_from_sheet_rows(
        fields=_fac00001_fields(),
        rows_payload=[
            ["04/19", "昼", "Menu A", "31", "", "2", "4", "33", "", ""],
        ],
        received_at=datetime(2026, 4, 19, 9, 0, 0),
    )

    assert [(line["diet_type"], line["quantity_original"]) for line in lines] == [
        ("regular", 33),
        ("no_meat", 2),
        ("no_fish", 4),
    ]


def test_materialization_lines_exclude_placeholder_quantity_columns():
    lines = order_service._build_materialization_lines_from_sheet_rows(
        fields=_fac00001_fields(),
        rows_payload=[
            ["04/19", "昼", "Menu A", "31", "12", "2", "", "", "", ""],
        ],
        received_at=datetime(2026, 4, 19, 9, 0, 0),
    )

    assert [(line["diet_type"], line["quantity_original"]) for line in lines] == [
        ("regular", 31),
        ("no_meat", 2),
    ]


def test_apply_latest_draft_materializes_effective_regular_from_change_columns():
    order_service.clear_all()
    seeded = _seed_order("msg-change-override-apply-001")
    order = order_service.get_order_by_id(seeded["id"])
    assert order is not None
    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=_fac00001_header(),
        rows=[
            ["04/19", "昼", "Menu A", "31", "", "", "", "33", "", ""],
            ["04/19", "夕", "Menu B", "32", "", "", "", "", "34", ""],
        ],
        fields=_fac00001_fields(),
        row_ids=["row-1", "row-2"],
        ui_mode="sheet",
    )
    assert error is None
    assert saved is not None

    applied, apply_error = order_service.apply_latest_draft(
        order["id"],
        draft_record=saved["draft"],
    )

    assert apply_error is None
    assert applied is not None
    assert [
        (line["menu_name"], line["diet_type"], line["quantity_original"])
        for line in applied["lines"]
    ] == [
        ("Menu A", "regular", 33),
        ("Menu B", "regular", 34),
    ]


def test_output_builder_collapses_legacy_change_override_lines():
    lines = output_builder.build_order_lines_for_outputs(
        {
            "id": "ORD-change-output-001",
            "facility": "FAC00001",
            "week": None,
            "week_value": None,
            "lines": [
                {
                    "date": "2026-04-19",
                    "daypart": "昼",
                    "menu_name": "Menu A",
                    "diet_type": "regular",
                    "area_id": "X",
                    "bag_type": None,
                    "quantity_original": 31,
                    "quantity_corrected": None,
                    "change_note": None,
                },
                {
                    "date": "2026-04-19",
                    "daypart": "昼",
                    "menu_name": "Menu A",
                    "diet_type": "change_1",
                    "area_id": "X",
                    "bag_type": None,
                    "quantity_original": 33,
                    "quantity_corrected": None,
                    "change_note": None,
                },
                {
                    "date": "2026-04-19",
                    "daypart": "昼",
                    "menu_name": "Menu A",
                    "diet_type": "no_meat",
                    "area_id": "X",
                    "bag_type": None,
                    "quantity_original": 2,
                    "quantity_corrected": None,
                    "change_note": None,
                },
            ],
        }
    )

    assert [(line["diet_type"], line["quantity_original"]) for line in lines] == [
        ("regular", 33),
        ("no_meat", 2),
    ]
