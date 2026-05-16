import pathlib
import sys
import zipfile
from datetime import date as dt_date

from openpyxl import Workbook, load_workbook

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import output_builder  # noqa: E402


TARGET_DATE = dt_date(2026, 3, 22)


def _border_signature(border) -> tuple:
    return tuple(
        (
            side.style,
            side.color.type if side.color else None,
            side.color.rgb if side.color and side.color.type == "rgb" else None,
        )
        for side in (border.left, border.right, border.top, border.bottom)
    )


def _cell_excel_signature(cell) -> tuple:
    fill = cell.fill
    font = cell.font
    alignment = cell.alignment
    return (
        cell.value,
        cell.data_type,
        cell.number_format,
        _border_signature(cell.border),
        fill.fill_type,
        fill.fgColor.type,
        fill.fgColor.rgb if fill.fgColor.type == "rgb" else fill.fgColor.indexed,
        font.name,
        font.sz,
        font.bold,
        font.italic,
        font.color.type if font.color else None,
        font.color.rgb if font.color and font.color.type == "rgb" else None,
        alignment.horizontal,
        alignment.vertical,
        alignment.wrap_text,
        alignment.shrink_to_fit,
    )


def _dimension_signature(ws) -> tuple:
    return (
        tuple((key, item.width, item.hidden) for key, item in sorted(ws.column_dimensions.items())),
        tuple((key, item.height, item.hidden) for key, item in sorted(ws.row_dimensions.items())),
    )


def _page_signature(ws) -> tuple:
    def _rounded(value):
        return round(value, 10) if isinstance(value, float) else value

    return (
        ws.sheet_view.showGridLines,
        ws.freeze_panes,
        ws.page_setup.orientation,
        ws.page_setup.paperSize,
        ws.page_setup.scale,
        ws.page_setup.fitToWidth,
        ws.page_setup.fitToHeight,
        _rounded(ws.page_margins.left),
        _rounded(ws.page_margins.right),
        _rounded(ws.page_margins.top),
        _rounded(ws.page_margins.bottom),
        _rounded(ws.page_margins.header),
        _rounded(ws.page_margins.footer),
        ws.print_options.horizontalCentered,
        ws.print_options.verticalCentered,
        tuple(ws.print_area),
    )


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
    assert workbook.sheetnames == ["メニュー", "大和なでしこ", "そよかぜ"]
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


def test_weekly_weight_workbook_reproduces_sample_visible_values(tmp_path, monkeypatch):
    sample_path = ROOT.parent.parent / "input_example" / "2026.0512" / "May 10-16 2026 Weight.xlsx"
    sample_book = load_workbook(sample_path, data_only=True)
    sample_ws = sample_book.worksheets[0]
    target_date = dt_date(2026, 5, 12)
    source_rows = []
    current_date = None
    current_daypart = None
    for row_idx in range(11, 67):
        raw_date = sample_ws.cell(row=row_idx, column=1).value
        if hasattr(raw_date, "date"):
            current_date = raw_date.date()
        raw_daypart = sample_ws.cell(row=row_idx, column=2).value
        if raw_daypart in {"朝", "昼", "夕"}:
            current_daypart = raw_daypart
        slot = sample_ws.cell(row=row_idx, column=3).value
        regular_menu = sample_ws.cell(row=row_idx, column=4).value
        regular_quantity = sample_ws.cell(row=row_idx, column=5).value
        regular_weight = sample_ws.cell(row=row_idx, column=6).value
        soft_quantity = sample_ws.cell(row=row_idx, column=7).value
        soft_weight = sample_ws.cell(row=row_idx, column=8).value
        soft_menu = sample_ws.cell(row=row_idx, column=9).value or regular_menu
        if regular_menu and regular_weight not in (None, ""):
            source_rows.append(
                {
                    "date": current_date,
                    "daypart": current_daypart,
                    "menu_category": slot,
                    "menu_name": regular_menu,
                    "diet_type": "regular",
                    "quantity_corrected": regular_quantity,
                    "menu_qty_per_serving": float(regular_weight) * 1000 / float(regular_quantity)
                    if isinstance(regular_weight, (int, float))
                    else None,
                    "menu_unit_type": "g",
                    "actual_amount_label": regular_weight if not isinstance(regular_weight, (int, float)) else None,
                }
            )
        if soft_menu and soft_weight not in (None, ""):
            source_rows.append(
                {
                    "date": current_date,
                    "daypart": current_daypart,
                    "menu_category": slot,
                    "menu_name": soft_menu,
                    "diet_type": "soft",
                    "quantity_corrected": soft_quantity,
                    "menu_qty_per_serving": float(soft_weight) * 1000 / float(soft_quantity)
                    if isinstance(soft_weight, (int, float))
                    else None,
                    "menu_unit_type": "g",
                    "actual_amount_label": soft_weight if not isinstance(soft_weight, (int, float)) else None,
                }
            )

    monkeypatch.setattr(output_builder, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        output_builder.order_service,
        "list_orders_by_line_date",
        lambda target_date, status=None: [{"id": f"ORD-{target_date.isoformat()}", "facility": "FAC001"}],
    )
    monkeypatch.setattr(
        output_builder,
        "_prepare_output_context",
        lambda order_id, **kwargs: {"order_lines": source_rows},
    )

    generated_path = output_builder.build_weekly_weight_summary_workbook(target_date)
    assert generated_path is not None
    generated_book = load_workbook(generated_path, data_only=False)
    sample_book_for_style = load_workbook(sample_path, data_only=False)
    generated_ws = generated_book.worksheets[0]
    sample_ws = sample_book_for_style.worksheets[0]

    assert generated_ws.title == sample_ws.title
    assert generated_ws.max_row == sample_ws.max_row
    assert generated_ws.max_column == sample_ws.max_column
    assert {str(item) for item in generated_ws.merged_cells.ranges} == {
        str(item) for item in sample_ws.merged_cells.ranges
    }
    assert _dimension_signature(generated_ws) == _dimension_signature(sample_ws)
    assert _page_signature(generated_ws) == _page_signature(sample_ws)
    for row_idx in range(1, sample_ws.max_row + 1):
        for col_idx in range(1, sample_ws.max_column + 1):
            assert _cell_excel_signature(generated_ws.cell(row=row_idx, column=col_idx)) == _cell_excel_signature(
                sample_ws.cell(row=row_idx, column=col_idx)
            ), (
                f"cell {generated_ws.cell(row=row_idx, column=col_idx).coordinate} differs"
            )


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


def test_reference_daily_delivery_preserves_table_borders(tmp_path):
    grouped_outputs = {
        "FAC00012": {
            "facility_code": "FAC00012",
            "facility_name": "ふれあいの丘",
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

    actual = load_workbook(output_path, data_only=False)
    expected = load_workbook(output_builder.DAILY_DELIVERY_REFERENCE_TEMPLATE, data_only=False)

    for sheet_name in expected.sheetnames:
        assert sheet_name in actual.sheetnames
        actual_ws = actual[sheet_name]
        expected_ws = expected[sheet_name]
        for row_idx in range(12, 20):
            for col_idx in range(1, max(expected_ws.max_column, actual_ws.max_column) + 1):
                actual_border = actual_ws.cell(row=row_idx, column=col_idx).border
                expected_border = expected_ws.cell(row=row_idx, column=col_idx).border
                if row_idx == 19 and actual_border.bottom.style == "medium" and expected_border.bottom.style is None:
                    continue
                assert _border_signature(actual_border) == _border_signature(expected_border), (
                    f"{sheet_name}!{actual_ws.cell(row=row_idx, column=col_idx).coordinate} "
                    "border differs from reference template"
                )


def test_reference_daily_delivery_writes_excel_readable_workbook(tmp_path):
    grouped_outputs = {
        "FAC00012": {
            "facility_code": "FAC00012",
            "facility_name": "ふれあいの丘",
            "invoice_template": {},
            "contexts": [],
        }
    }
    workbook = output_builder._create_reference_daily_delivery_workbook(  # noqa: SLF001
        target_date=dt_date(2026, 5, 10),
        grouped_outputs=grouped_outputs,
    )
    output_path = tmp_path / "delivery.xlsx"
    output_builder._save_reference_daily_delivery_workbook_preserving_template_package(  # noqa: SLF001
        workbook,
        output_path,
    )

    saved = load_workbook(output_path, data_only=False)
    assert "ふれあいの丘" in saved.sheetnames


def test_reference_daily_delivery_removes_static_artifacts(tmp_path):
    workbook = output_builder._create_reference_daily_delivery_workbook(  # noqa: SLF001
        target_date=dt_date(2026, 5, 10),
        grouped_outputs={},
    )
    output_path = tmp_path / "delivery.xlsx"
    output_builder._save_reference_daily_delivery_workbook_preserving_template_package(  # noqa: SLF001
        workbook,
        output_path,
    )

    saved = load_workbook(output_path, data_only=True)
    assert saved["山城"]["C27"].value is None
    for row_idx in range(12, 20):
        assert saved["池袋病院"].cell(row=row_idx, column=5).value is None
        assert saved["池袋病院"].cell(row=row_idx, column=6).value is None


def test_daily_bundle_blocks_embedding_templated_delivery_workbook(tmp_path):
    template_path = tmp_path / "delivery_template.xlsx"
    workbook = Workbook()
    workbook.active.title = "Template"
    workbook.save(template_path)

    try:
        output_builder._create_daily_delivery_sheet(  # noqa: SLF001
            Workbook(),
            set(),
            title_seed="delivery",
            rows=[{"date": dt_date(2026, 5, 10), "menu_name": "A"}],
            invoice_template={
                "template_uri": template_path.as_uri(),
                "columns": [{"name": "メニュー", "source": "menu_name", "column_index": 1}],
            },
            facility_name="施設",
            order_id="ORD-test",
        )
    except ValueError as exc:
        assert str(exc) == "templated delivery notes cannot be embedded into a rebuilt daily bundle workbook"
    else:
        raise AssertionError("templated delivery embedding should be blocked")
