import pathlib
import sys
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import config_service, facility_service, order_service, output_builder  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _seed_order(message_id: str, *, facility_hint: str = "FAC00014") -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy-expanded-cell.pdf",
        received_at=datetime(2026, 3, 21, 9, 0, 0),
        facility_hint=facility_hint,
        week_hint=None,
    )
    return order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-03-21",
                "daypart": "朝",
                "menu_name": "Original Menu",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 2,
            }
        ],
    )


def _fac00014_fields() -> list[str]:
    return [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "qty.staff_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.sesame_allergy_x",
        "qty.change_1_x",
        "remarks",
    ]


def _fac00014_header() -> list[str]:
    return [
        "日付",
        "区分",
        "メニュー",
        "常食",
        "職員",
        "肉禁",
        "魚禁",
        "ゴマアレルギー",
        "変更1",
        "備考欄",
    ]


def _fac00016_fields() -> list[str]:
    return [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "qty.diabetes_x",
        "remarks",
    ]


def _fac00016_header() -> list[str]:
    return ["日付", "区分", "メニュー", "常食", "糖尿", "備考欄"]


def test_apply_expanded_cell_same_daypart_copy_fills_two_row_cluster_from_single_value():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.soft_x", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["04/05", "朝", "Menu A", "44", "", ""]},
        {"values": ["04/05", "朝", "Menu B", "", "", ""]},
        {"values": ["04/05", "昼", "Menu C", "", "", ""]},
    ]

    filled = order_service._apply_expanded_cell_same_daypart_copy(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
    )

    assert filled == 1
    assert rows[0]["values"][3] == "44"
    assert rows[1]["values"][3] == "44"
    assert rows[2]["values"][3] == ""


def test_apply_expanded_cell_same_daypart_copy_skips_four_row_cluster():
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"]
    quantity_index = order_service._build_sheet_quantity_index(fields)
    rows = [
        {"values": ["04/05", "朝", "Menu A", "48", ""]},
        {"values": ["04/05", "朝", "Menu B", "", ""]},
        {"values": ["04/05", "朝", "Menu C", "", ""]},
        {"values": ["04/05", "朝", "Menu D", "", ""]},
    ]

    filled = order_service._apply_expanded_cell_same_daypart_copy(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
    )

    assert filled == 0
    assert rows[1]["values"][3] == ""
    assert rows[2]["values"][3] == ""
    assert rows[3]["values"][3] == ""


def test_manual_facility_flag_enables_expanded_cell_copy_for_unmerged_template():
    previous_config = facility_service.get_facility_config("FAC00014") or {}
    next_config = dict(previous_config)
    next_config["expanded_cell_same_daypart_copy_enabled"] = True
    assert facility_service.update_config("FAC00014", next_config)
    try:
        facility_config = config_service.get_facility_config("FAC00014")
        assert order_service._expanded_cell_same_daypart_copy_enabled(  # noqa: SLF001
            facility_config,
            week_sheet_name="4月26日～4月30日",
        )
    finally:
        assert facility_service.update_config("FAC00014", previous_config)


def test_build_confirm_materialization_candidate_auto_detects_template_merged_quantity_cells():
    order_service.clear_all()
    previous_config = facility_service.get_facility_config("FAC00016") or {}
    next_config = dict(previous_config)
    next_config.pop("expanded_cell_same_daypart_copy_enabled", None)
    assert facility_service.update_config("FAC00016", next_config)
    try:
        order = _seed_order("msg-expanded-cell-confirm-auto-template", facility_hint="FAC00016")
        saved, error = order_service.save_ocr_sheet_exact(
            order["id"],
            header=_fac00016_header(),
            rows=[
                ["03/21", "朝", "Menu A", "44", "", ""],
                ["03/21", "朝", "Menu B", "", "", ""],
                ["03/21", "昼", "Menu C", "", "", ""],
            ],
            fields=_fac00016_fields(),
            row_ids=["auto-row-1", "auto-row-2", "auto-row-3"],
            ui_mode="sheet",
        )
        assert error is None
        assert saved is not None

        candidate = order_service.build_confirm_materialization_candidate(order["id"])

        assert isinstance(candidate, dict)
        assert candidate["error"] is None
        assert [
            (line["menu_name"], line["quantity_original"])
            for line in candidate["lines"]
        ] == [("Menu A", 44), ("Menu B", 44)]
    finally:
        assert facility_service.update_config("FAC00016", previous_config)


def test_expanded_cell_copy_uses_explicit_facility_template_merged_quantity_cells():
    previous_config = facility_service.get_facility_config("FAC00007") or {}
    next_config = dict(previous_config)
    next_config.pop("expanded_cell_same_daypart_copy_enabled", None)
    assert facility_service.update_config("FAC00007", next_config)
    try:
        facility_config = config_service.get_facility_config("FAC00007")
        assert order_service._expanded_cell_same_daypart_copy_enabled(  # noqa: SLF001
            facility_config,
            week_sheet_name="4月26日～4月30日",
        )
    finally:
        assert facility_service.update_config("FAC00007", previous_config)


def test_expanded_cell_copy_does_not_enable_for_unmerged_template_without_manual_flag():
    previous_config = facility_service.get_facility_config("FAC00014") or {}
    next_config = dict(previous_config)
    next_config.pop("expanded_cell_same_daypart_copy_enabled", None)
    assert facility_service.update_config("FAC00014", next_config)
    try:
        facility_config = config_service.get_facility_config("FAC00014")
        assert not order_service._expanded_cell_same_daypart_copy_enabled(  # noqa: SLF001
            facility_config,
            week_sheet_name="4月26日～4月30日",
        )
    finally:
        assert facility_service.update_config("FAC00014", previous_config)


def test_build_order_lines_for_outputs_auto_detects_template_merged_quantity_cells():
    order_service.clear_all()
    previous_config = facility_service.get_facility_config("FAC00016") or {}
    next_config = dict(previous_config)
    next_config.pop("expanded_cell_same_daypart_copy_enabled", None)
    assert facility_service.update_config("FAC00016", next_config)
    try:
        order = _seed_order("msg-expanded-cell-output-auto-template", facility_hint="FAC00016")
        saved, error = order_service.save_ocr_sheet_exact(
            order["id"],
            header=_fac00016_header(),
            rows=[
                ["03/21", "朝", "Menu A", "48", "", ""],
                ["03/21", "朝", "Menu B", "", "", ""],
            ],
            fields=_fac00016_fields(),
            row_ids=["auto-output-row-1", "auto-output-row-2"],
            ui_mode="sheet",
        )
        assert error is None
        assert saved is not None

        applied, apply_error = order_service.apply_latest_draft(order["id"])
        assert apply_error is None
        assert applied is not None

        lines = output_builder.build_order_lines_for_outputs(order_service.get_order_by_id(order["id"]))

        assert [
            (line["menu_name"], line["quantity_original"])
            for line in lines
        ] == [("Menu A", 48), ("Menu B", 48)]
    finally:
        assert facility_service.update_config("FAC00016", previous_config)
