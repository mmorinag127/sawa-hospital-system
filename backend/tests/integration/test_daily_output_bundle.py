import pathlib
import sys
from datetime import date as dt_date

from openpyxl import Workbook, load_workbook

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import output_builder  # noqa: E402


TARGET_DATE = dt_date(2026, 3, 22)


def _make_context(order_id: str, facility_code: str, facility_name: str, menu_name: str) -> dict:
    return {
        "bags": [{"date": TARGET_DATE, "menu_name": menu_name}],
        "label_profile": {},
        "facility_config": {"facility_name": facility_name},
        "order_for_outputs": {"id": order_id, "facility": facility_code, "lines": []},
        "invoice_template": {
            "columns": [
                {"name": "日付", "source": "date"},
                {"name": "区分", "source": "daypart"},
                {"name": "メニュー", "source": "menu_name"},
                {"name": "常食", "source": "quantity"},
            ],
            "template_uri": None,
            "include_menu_name": True,
            "sheet_name": None,
        },
        "quantity_rules": {"zero_as_empty": True},
        "ocr_menu_meta": None,
    }


def test_build_daily_output_bundle_labels_groups_orders_per_facility(tmp_path, monkeypatch):
    monkeypatch.setattr(output_builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        output_builder.order_service,
        "list_orders_by_line_date",
        lambda target_date, status=None: [
            {"id": "ORD-1", "facility": "FAC001"},
            {"id": "ORD-2", "facility": "FAC001"},
            {"id": "ORD-3", "facility": "FAC002"},
        ],
    )
    monkeypatch.setattr(
        output_builder.config_service,
        "get_facility_config",
        lambda facility_code: {
            "FAC001": {"facility_name": "そよかぜ"},
            "FAC002": {"facility_name": "大和なでしこ"},
        }.get(facility_code, {}),
    )
    monkeypatch.setattr(
        output_builder,
        "_prepare_output_context",
        lambda order_id: {
            "ORD-1": _make_context("ORD-1", "FAC001", "そよかぜ", "献立A"),
            "ORD-2": _make_context("ORD-2", "FAC001", "そよかぜ", "献立B"),
            "ORD-3": _make_context("ORD-3", "FAC002", "大和なでしこ", "献立C"),
        }[order_id],
    )
    monkeypatch.setattr(
        output_builder,
        "_build_label_rows",
        lambda bags, label_profile, facility_name: (
            [{"メニュー": bag.get("menu_name") or ""} for bag in bags],
            ["メニュー"],
            "jp",
        ),
    )

    bundle_path, summary = output_builder.build_daily_output_bundle(
        TARGET_DATE,
        bundle_type="labels",
    )

    assert bundle_path.suffix == ".xlsx"
    assert summary["file_format"] == "xlsx"
    assert summary["success_orders"] == 2
    workbook = load_workbook(bundle_path)
    assert workbook.sheetnames == ["そよかぜ", "大和なでしこ"]
    assert workbook["そよかぜ"]["A2"].value == "献立A"
    assert workbook["そよかぜ"]["A3"].value == "献立B"
    assert workbook["大和なでしこ"]["A2"].value == "献立C"


def test_build_daily_output_bundle_delivery_groups_and_merges_quantities(tmp_path, monkeypatch):
    monkeypatch.setattr(output_builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        output_builder.order_service,
        "list_orders_by_line_date",
        lambda target_date, status=None: [
            {"id": "ORD-1", "facility": "FAC001"},
            {"id": "ORD-2", "facility": "FAC001"},
        ],
    )
    monkeypatch.setattr(
        output_builder.config_service,
        "get_facility_config",
        lambda facility_code: {"facility_name": "そよかぜ"},
    )
    monkeypatch.setattr(
        output_builder,
        "_prepare_output_context",
        lambda order_id: _make_context(order_id, "FAC001", "そよかぜ", "献立A"),
    )
    monkeypatch.setattr(
        output_builder,
        "_build_delivery_rows",
        lambda order, template, quantity_rules, facility_config, menu_meta: [
            {
                "date": TARGET_DATE,
                "daypart": "朝",
                "menu_category": "主菜",
                "menu_name": "献立A",
                "menu_display": "主菜 献立A",
                "_order_index": 1,
                "常食": 2 if order.get("id") == "ORD-1" else 3,
            }
        ],
    )

    def _fake_write_delivery_note(path, rows, columns, template_uri, include_menu_name, sheet_name=None, facility_name=None):
        workbook = Workbook()
        ws = workbook.active
        ws.title = "納品書"
        ws["A1"] = "メニュー"
        ws["B1"] = "常食"
        for index, row in enumerate(rows, start=2):
            ws.cell(row=index, column=1).value = row.get("menu_name")
            ws.cell(row=index, column=2).value = row.get("常食")
        workbook.save(path)

    monkeypatch.setattr(output_builder, "_write_delivery_note", _fake_write_delivery_note)

    bundle_path, summary = output_builder.build_daily_output_bundle(
        TARGET_DATE,
        bundle_type="delivery",
    )

    assert bundle_path.suffix == ".xlsx"
    assert summary["success_orders"] == 1
    workbook = load_workbook(bundle_path)
    assert workbook.sheetnames == ["そよかぜ"]
    assert workbook["そよかぜ"]["A2"].value == "献立A"
    assert workbook["そよかぜ"]["B2"].value == 5


def test_build_daily_output_bundle_both_uses_prefixed_sheet_titles(tmp_path, monkeypatch):
    monkeypatch.setattr(output_builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        output_builder.order_service,
        "list_orders_by_line_date",
        lambda target_date, status=None: [{"id": "ORD-1", "facility": "FAC001"}],
    )
    monkeypatch.setattr(
        output_builder.config_service,
        "get_facility_config",
        lambda facility_code: {"facility_name": "そよかぜ"},
    )
    monkeypatch.setattr(
        output_builder,
        "_prepare_output_context",
        lambda order_id: _make_context(order_id, "FAC001", "そよかぜ", "献立A"),
    )
    monkeypatch.setattr(
        output_builder,
        "_build_label_rows",
        lambda bags, label_profile, facility_name: (
            [{"メニュー": bag.get("menu_name") or ""} for bag in bags],
            ["メニュー"],
            "jp",
        ),
    )
    monkeypatch.setattr(
        output_builder,
        "_build_delivery_rows",
        lambda order, template, quantity_rules, facility_config, menu_meta: [
            {
                "date": TARGET_DATE,
                "daypart": "朝",
                "menu_category": "主菜",
                "menu_name": "献立A",
                "menu_display": "主菜 献立A",
                "_order_index": 1,
                "常食": 2,
            }
        ],
    )

    def _fake_write_delivery_note(path, rows, columns, template_uri, include_menu_name, sheet_name=None, facility_name=None):
        workbook = Workbook()
        ws = workbook.active
        ws["A1"] = "メニュー"
        ws["A2"] = rows[0].get("menu_name") if rows else ""
        workbook.save(path)

    monkeypatch.setattr(output_builder, "_write_delivery_note", _fake_write_delivery_note)

    bundle_path, _ = output_builder.build_daily_output_bundle(
        TARGET_DATE,
        bundle_type="both",
    )

    workbook = load_workbook(bundle_path)
    assert workbook.sheetnames == ["ラベル_そよかぜ", "納品書_そよかぜ"]


def test_build_daily_output_bundle_raises_when_no_rows_for_target_date(tmp_path, monkeypatch):
    monkeypatch.setattr(output_builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        output_builder.order_service,
        "list_orders_by_line_date",
        lambda target_date, status=None: [{"id": "ORD-1", "facility": "FAC001"}],
    )
    monkeypatch.setattr(
        output_builder.config_service,
        "get_facility_config",
        lambda facility_code: {"facility_name": "そよかぜ"},
    )
    monkeypatch.setattr(
        output_builder,
        "_prepare_output_context",
        lambda order_id: _make_context(order_id, "FAC001", "そよかぜ", "献立A"),
    )
    monkeypatch.setattr(
        output_builder,
        "_build_label_rows",
        lambda bags, label_profile, facility_name: ([], ["メニュー"], "jp"),
    )

    try:
        output_builder.build_daily_output_bundle(TARGET_DATE, bundle_type="labels")
    except ValueError as exc:
        assert str(exc) == "対象日の出力対象がありません"
    else:
        raise AssertionError("ValueError was not raised")


def test_reference_daily_delivery_materializes_static_formula_labels(tmp_path):
    grouped_outputs = {
        "FAC00013": {
            "facility_code": "FAC00013",
            "facility_name": "いこいの森",
            "invoice_template": {},
            "contexts": [],
        }
    }
    workbook = output_builder._create_reference_daily_delivery_workbook(  # noqa: SLF001
        target_date=dt_date(2026, 5, 10),
        grouped_outputs=grouped_outputs,
    )
    output_path = tmp_path / "delivery.xlsx"
    workbook.save(output_path)

    saved = load_workbook(output_path, data_only=True)
    ws = saved["いこいの森"]

    assert ws["A17"].value == "(日)"
    assert ws["D17"].value == "煮込みハンバーグ"
    assert ws["D18"].value == "ジャーマンポテト"
    assert ws["D19"].value == "ほうれん草の和え物"
