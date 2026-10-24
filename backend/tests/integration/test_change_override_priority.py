import pathlib
import sys
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service, output_builder  # noqa: E402
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order
from tests.integration.test_cross_month_week_support import _template


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
        existing_week_code="2026-04@2026-04-19~2026-04-19",
        date_policy={}, template=_template(_fac00001_fields()),
    )

    assert [(line["menu_name"], line["diet_type"], line["quantity_original"]) for line in lines] == [
        ("Menu A", "regular", 34),
        ("Menu B", "regular", 35),
        ("Menu C", "regular", 36),
    ]


def test_materialization_lines_exclude_forbidden_without_changing_source_cells():
    rows = [["04/19", "昼", "Menu A", "31", "", "2", "4", "33", "", ""]]
    lines = order_service._build_materialization_lines_from_sheet_rows(
        fields=_fac00001_fields(),
        rows_payload=rows,
        received_at=datetime(2026, 4, 19, 9, 0, 0),
        existing_week_code="2026-04@2026-04-19~2026-04-19",
        date_policy={}, template=_template(_fac00001_fields()),
    )

    assert [(line["diet_type"], line["quantity_original"]) for line in lines] == [
        ("regular", 33),
    ]
    assert rows == [["04/19", "昼", "Menu A", "31", "", "2", "4", "33", "", ""]]


def test_materialization_lines_exclude_placeholder_quantity_columns():
    rows = [["04/19", "昼", "Menu A", "31", "12", "2", "", "", "", ""]]
    lines = order_service._build_materialization_lines_from_sheet_rows(
        fields=_fac00001_fields(),
        rows_payload=rows,
        received_at=datetime(2026, 4, 19, 9, 0, 0),
        existing_week_code="2026-04@2026-04-19~2026-04-19",
        date_policy={}, template=_template(_fac00001_fields()),
    )

    assert [(line["diet_type"], line["quantity_original"]) for line in lines] == [
        ("regular", 31),
    ]
    assert rows == [["04/19", "昼", "Menu A", "31", "12", "2", "", "", "", ""]]


def test_current_workflow_materializes_effective_regular_from_change_columns(monkeypatch, tmp_path, isolated_formal_schema):
    lines = []
    for menu, daypart, regular, change, quantity in (
        ("Menu A", "昼", 31, "change_1", 33),
        ("Menu B", "夕", 32, "change_2", 34),
    ):
        for diet, value in (("regular", regular), (change, quantity)):
            lines.append({"date": "2026-04-19", "daypart": daypart, "menu_name": menu,
                "diet_type": diet, "area_id": "X", "bag_type": "standard",
                "quantity_original": value})
    order = accepted_bagging_order(monkeypatch, tmp_path, lines, confirm=True)
    applied = order_service.get_order_by_id(order["id"])
    assert applied is not None
    assert [(line["menu_name"], line["diet_type"], line["quantity_original"])
            for line in applied["lines"]] == [
        ("Menu A", "regular", 33), ("Menu B", "regular", 34)]


def test_output_builder_collapses_owned_change_override_lines(monkeypatch, tmp_path, isolated_formal_schema):
    authored = [{"date": "2026-04-19", "daypart": "昼", "menu_name": "Menu A",
                 "diet_type": diet, "area_id": "X", "bag_type": "standard",
                 "quantity_original": value}
                for diet, value in (("regular", 31), ("change_1", 33), ("no_meat", 2))]
    order = accepted_bagging_order(monkeypatch, tmp_path, authored, confirm=True)
    lines = output_builder.build_order_lines_for_outputs(order_service.get_order_by_id(order["id"]))
    assert [(line["diet_type"], line["quantity_original"]) for line in lines] == [
        ("regular", 33)]
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    raw = sheet["rows"][0][sheet["fields"].index("qty.no_meat_x")]
    assert raw == authored[2]["quantity_original"] == 2
    assert type(raw) is int
