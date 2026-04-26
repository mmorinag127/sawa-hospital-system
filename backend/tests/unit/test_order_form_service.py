import pathlib
import sys
from datetime import datetime

import pytest
from openpyxl import Workbook, load_workbook

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_form_service  # noqa: E402


def _metadata_rows(workbook):
    return {key: value for key, value in workbook["設定"].iter_rows(min_row=2, values_only=True)}


def _build_source_workbook(path: pathlib.Path, sheet_name: str) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name
    worksheet["A2"] = "【発　注　連　絡　表】"
    worksheet["G3"] = "締切日1月1日まで"
    worksheet["A4"] = ""
    worksheet["D7"] = "献立"
    workbook.save(path)


def test_build_week_ranges_for_month_matches_real_sample_boundaries() -> None:
    march = [
        order_form_service._format_week_sheet_name(start_date, end_date)
        for start_date, end_date in order_form_service._build_week_ranges_for_month("2026-03")
    ]
    april = [
        order_form_service._format_week_sheet_name(start_date, end_date)
        for start_date, end_date in order_form_service._build_week_ranges_for_month("2026-04")
    ]
    may = [
        order_form_service._format_week_sheet_name(start_date, end_date)
        for start_date, end_date in order_form_service._build_week_ranges_for_month("2026-05")
    ]

    assert march == [
        "3月1日～3月7日",
        "3月8日～3月14日",
        "3月15日～3月21日",
        "3月22日～3月28日",
        "3月29日～3月31日",
    ]
    assert april == [
        "4月1日～4月4日",
        "4月5日～4月11日",
        "4月12日～4月18日",
        "4月19日～4月25日",
        "4月26日～4月30日",
    ]
    assert may == [
        "5月1日～5月2日",
        "5月3日～5月9日",
        "5月10日～5月16日",
        "5月17日～5月23日",
        "5月24日～5月30日",
        "5月31日～5月31日",
    ]


def test_build_fax_base_template_excel_adds_guides_and_keeps_logo(tmp_path):
    output = order_form_service.build_fax_base_template_excel(
        fax_template_id="fax_layout_regular_forbidden_v1",
        week_sheet_name="3月22日～3月28日",
        output_dir=tmp_path,
    )

    workbook = load_workbook(output)
    worksheet = workbook["3月22日～3月28日"]

    assert "A1" in {cell.coordinate for row in worksheet["A1:L1"] for cell in row if cell.fill.fill_type}
    assert worksheet["A3"].value == "施設名記入欄"
    assert "TEMPLATE=fax_layout_regular_forbidden_v1" in worksheet["B1"].value
    assert "$L$69" in str(worksheet.print_area)
    assert len(getattr(worksheet, "_images", [])) == 1
    assert workbook["設定"].sheet_state == "hidden"


def test_build_fax_order_form_excel_uses_facility_config_and_hidden_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": "テスト施設",
            "fax_template_id": "fax_layout_regular_diabetes_v1",
        },
    )

    output = order_form_service.build_fax_order_form_excel(
        facility_id="FACTEST01",
        week_sheet_name="3月22日～3月28日",
        output_dir=tmp_path,
    )

    workbook = load_workbook(output)
    worksheet = workbook["3月22日～3月28日"]
    metadata = workbook["設定"]
    rows = {key: value for key, value in metadata.iter_rows(min_row=2, values_only=True)}

    assert worksheet["A3"].value == "テスト施設"
    assert "FACILITY=FACTEST01:テスト施設" in worksheet["B1"].value
    assert rows["facility_id"] == "FACTEST01"
    assert rows["facility_name"] == "テスト施設"
    assert rows["fax_template_id"] == "fax_layout_regular_diabetes_v1"


def test_build_order_form_excel_creates_weekly_sheets_and_metadata(tmp_path, monkeypatch):
    source_name = "source.xlsx"
    _build_source_workbook(tmp_path / source_name, order_form_service._DEFAULT_WEEK_SHEET)

    monkeypatch.setattr(order_form_service, "_FAX_SOURCE_TEMPLATE_DIR", tmp_path)
    monkeypatch.setattr(order_form_service, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setitem(
        order_form_service._FAX_FAMILY_SOURCE_MAP,
        "fax_layout_regular_forbidden_v1",
        {
            "source_workbook": source_name,
            "family_label": "共通・禁食2種",
        },
    )
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": "週次テスト施設",
            "fax_template_id": "fax_layout_regular_forbidden_v1",
        },
    )
    monkeypatch.setattr(
        order_form_service.menu_service,
        "get_menu_for_facility",
        lambda month_id, facility_id: {
            "entries": [
                {"menu_date": "2026-03-01", "daypart": "朝", "category": "副①", "name": "A"},
                {"menu_date": "2026-03-01", "daypart": "昼", "category": "主", "name": "B"},
                {"menu_date": "2026-03-08", "daypart": "朝", "category": "副①", "name": "C"},
                {"menu_date": "2026-03-29", "daypart": "夕", "category": "主", "name": "D"},
                {"menu_date": "2026-03-31", "daypart": "朝", "category": "副①", "name": "E"},
            ]
        },
    )

    output = order_form_service.build_order_form_excel(facility_id="FACTEST01", month_id="2026-03")

    workbook = load_workbook(output)
    assert workbook.sheetnames[:5] == [
        "3月1日～3月7日",
        "3月8日～3月14日",
        "3月15日～3月21日",
        "3月22日～3月28日",
        "3月29日～3月31日",
    ]

    first_sheet = workbook["3月1日～3月7日"]
    last_sheet = workbook["3月29日～3月31日"]
    rows = _metadata_rows(workbook)

    assert first_sheet["A4"].value == "週次テスト施設"
    assert "WEEK=3月1日～3月7日" in first_sheet["B1"].value
    assert first_sheet["A11"].value.date() == datetime(2026, 3, 1).date()
    assert first_sheet["B11"].value == "朝"
    assert first_sheet["C12"].value == "主"
    assert first_sheet["D12"].value == "B"
    assert first_sheet["G3"].value == "締切日2月13日まで"
    assert last_sheet["A11"].value.date() == datetime(2026, 3, 29).date()
    assert last_sheet["A12"].value.date() == datetime(2026, 3, 31).date()
    assert rows["month_id"] == "2026-03"
    assert rows["sheet_count"] == 5
    assert rows["entry_count"] == 5
    assert rows["week_1_sheet_name"] == "3月1日～3月7日"
    assert workbook["設定"].sheet_state == "hidden"


def test_clear_week_sheet_body_preserves_quantity_body_merges_for_diabetes_template() -> None:
    source_path = order_form_service._resolve_source_workbook_path("いこいの森プラス　2604.xlsx")
    workbook = load_workbook(source_path)
    worksheet = workbook["4月26日～4月30日"]

    assert "E11:E12" in {str(item) for item in worksheet.merged_cells.ranges}
    assert "F11:F12" in {str(item) for item in worksheet.merged_cells.ranges}

    order_form_service._clear_week_sheet_body(worksheet)
    merged_ranges = {str(item) for item in worksheet.merged_cells.ranges}

    assert "E11:E12" in merged_ranges
    assert "F11:F12" in merged_ranges
    assert worksheet["E11"].value is None
    assert worksheet["F11"].value is None


def test_build_order_form_excel_supports_six_week_months(tmp_path, monkeypatch):
    source_name = "source.xlsx"
    _build_source_workbook(tmp_path / source_name, order_form_service._DEFAULT_WEEK_SHEET)

    monkeypatch.setattr(order_form_service, "_FAX_SOURCE_TEMPLATE_DIR", tmp_path)
    monkeypatch.setattr(order_form_service, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setitem(
        order_form_service._FAX_FAMILY_SOURCE_MAP,
        "fax_layout_regular_forbidden_v1",
        {
            "source_workbook": source_name,
            "family_label": "共通・禁食2種",
        },
    )
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": "6週施設",
            "fax_template_id": "fax_layout_regular_forbidden_v1",
        },
    )
    monkeypatch.setattr(
        order_form_service.menu_service,
        "get_menu_for_facility",
        lambda month_id, facility_id: {
            "entries": [
                {"menu_date": "2026-05-01", "daypart": "朝", "category": "副①", "name": "A"},
                {"menu_date": "2026-05-31", "daypart": "夕", "category": "主", "name": "B"},
            ]
        },
    )

    output = order_form_service.build_order_form_excel(facility_id="FAC6WEEK", month_id="2026-05")

    workbook = load_workbook(output)
    assert workbook.sheetnames[:6] == [
        "5月1日～5月2日",
        "5月3日～5月9日",
        "5月10日～5月16日",
        "5月17日～5月23日",
        "5月24日～5月30日",
        "5月31日～5月31日",
    ]
    assert _metadata_rows(workbook)["sheet_count"] == 6


@pytest.mark.parametrize(
    ("fax_template_id", "facility_name"),
    [
        ("fax_layout_regular_forbidden_v1", "共通確認"),
        ("fax_layout_floor_2f3f_v1", "GH確認"),
        ("fax_layout_regular_soft_mixer_forbidden_v1", "藍確認"),
        ("fax_layout_regular_staff_daycare_v1", "湘南確認"),
        ("fax_layout_regular_diabetes_v1", "糖尿確認"),
        ("fax_layout_regular_staff_daycare_other_forbidden_v1", "ふれあい確認"),
        ("fax_layout_soft_packaging_forbidden_v1", "池袋確認"),
    ],
)
def test_build_order_form_excel_supports_all_fax_families(tmp_path, monkeypatch, fax_template_id, facility_name):
    monkeypatch.setattr(order_form_service, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": facility_name,
            "fax_template_id": fax_template_id,
        },
    )
    monkeypatch.setattr(
        order_form_service.menu_service,
        "get_menu_for_facility",
        lambda month_id, facility_id: {
            "entries": [
                {"menu_date": "2026-03-01", "daypart": "朝", "category": "副①", "name": "A"},
                {"menu_date": "2026-03-02", "daypart": "昼", "category": "主", "name": "B"},
                {"menu_date": "2026-03-29", "daypart": "夕", "category": "副②", "name": "C"},
            ]
        },
    )

    output = order_form_service.build_order_form_excel(facility_id="FACTESTALL", month_id="2026-03")
    workbook = load_workbook(output)

    assert workbook.sheetnames[:5] == [
        "3月1日～3月7日",
        "3月8日～3月14日",
        "3月15日～3月21日",
        "3月22日～3月28日",
        "3月29日～3月31日",
    ]
    assert workbook[workbook.sheetnames[0]]["A4"].value == facility_name
    assert fax_template_id in workbook[workbook.sheetnames[0]]["B1"].value


def test_infer_fax_template_id_from_facility_falls_back_from_invoice_columns() -> None:
    assert (
        order_form_service._infer_fax_template_id_from_facility(
            {
                "facility_name": "大和なでしこ",
                "invoice_template": {
                    "columns": [
                        {"header": "常食", "diet_type": "regular"},
                        {"header": "禁食", "diet_type": "禁食"},
                    ]
                },
            }
        )
        == "fax_layout_regular_forbidden_v1"
    )
    assert (
        order_form_service._infer_fax_template_id_from_facility(
            {
                "facility_name": "佐古",
                "invoice_template": {
                    "columns": [
                        {"header": "常食2F", "diet_type": "regular", "area_id": "2F"},
                        {"header": "常食3F", "diet_type": "regular", "area_id": "3F"},
                        {"header": "軟菜", "diet_type": "soft"},
                    ]
                },
            }
        )
        == "fax_layout_floor_2f3f_v1"
    )


def test_build_order_form_excel_uses_inferred_template_id_in_output_name(tmp_path, monkeypatch):
    source_name = "source.xlsx"
    _build_source_workbook(tmp_path / source_name, order_form_service._DEFAULT_WEEK_SHEET)

    monkeypatch.setattr(order_form_service, "_FAX_SOURCE_TEMPLATE_DIR", tmp_path)
    monkeypatch.setattr(order_form_service, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setitem(
        order_form_service._FAX_FAMILY_SOURCE_MAP,
        "fax_layout_floor_2f3f_v1",
        {
            "source_workbook": source_name,
            "family_label": "2F/3F分割",
        },
    )
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": "推定施設",
            "invoice_template": {
                "columns": [
                    {"header": "常食2F", "diet_type": "regular", "area_id": "2F"},
                    {"header": "常食3F", "diet_type": "regular", "area_id": "3F"},
                    {"header": "軟菜", "diet_type": "soft"},
                ]
            },
        },
    )
    monkeypatch.setattr(
        order_form_service.menu_service,
        "get_menu_for_facility",
        lambda month_id, facility_id: {
            "entries": [
                {"menu_date": "2026-03-01", "daypart": "朝", "category": "副①", "name": "A"},
            ]
        },
    )

    output = order_form_service.build_order_form_excel(facility_id="FACINF01", month_id="2026-03")

    assert "fax_layout_floor_2f3f_v1" in output.name


def test_packaged_order_form_sources_exist_for_manifest() -> None:
    specs = order_form_service.list_fax_order_form_template_specs()
    assert specs
    for spec in specs:
        month_sources = spec.get("month_sources") or {}
        assert month_sources
        for filename in month_sources.values():
            assert order_form_service._resolve_source_workbook_path(filename).exists()


def test_build_order_form_excel_uses_month_specific_packaged_source_workbook(tmp_path, monkeypatch):
    monkeypatch.setattr(order_form_service, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": "4月施設",
            "fax_template_id": "fax_layout_regular_forbidden_v1",
        },
    )
    monkeypatch.setattr(
        order_form_service.menu_service,
        "get_menu_for_facility",
        lambda month_id, facility_id: {
            "entries": [
                {"menu_date": "2026-04-01", "daypart": "朝", "category": "副①", "name": "A"},
                {"menu_date": "2026-04-26", "daypart": "昼", "category": "主", "name": "B"},
            ]
        },
    )

    output = order_form_service.build_order_form_excel(facility_id="FACAPR01", month_id="2026-04")

    workbook = load_workbook(output)
    rows = _metadata_rows(workbook)
    assert rows["source_workbook"] == "共通　2604.xlsx"
    assert workbook.sheetnames[:5] == [
        "4月1日～4月4日",
        "4月5日～4月11日",
        "4月12日～4月18日",
        "4月19日～4月25日",
        "4月26日～4月30日",
    ]


def test_build_fax_order_form_excel_uses_month_specific_source_for_week_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": "4月FAX施設",
            "fax_template_id": "fax_layout_regular_forbidden_v1",
        },
    )

    output = order_form_service.build_fax_order_form_excel(
        facility_id="FACAPR02",
        week_sheet_name="4月1日～4月4日",
        output_dir=tmp_path,
    )

    workbook = load_workbook(output)
    metadata = _metadata_rows(workbook)
    assert metadata["source_workbook"] == "共通　2604.xlsx"
    assert workbook.sheetnames == ["4月1日～4月4日", "設定"]


def test_build_fax_structure_only_excel_clears_body_values(tmp_path, monkeypatch):
    source_name = "source.xlsx"
    _build_source_workbook(tmp_path / source_name, "4月5日～4月11日")
    workbook = load_workbook(tmp_path / source_name)
    worksheet = workbook["4月5日～4月11日"]
    worksheet["A11"] = datetime(2026, 4, 5)
    worksheet["B11"] = "朝"
    worksheet["C11"] = "副①"
    worksheet["D11"] = "Menu A"
    worksheet["G11"] = "12"
    workbook.save(tmp_path / source_name)

    monkeypatch.setattr(order_form_service, "_FAX_SOURCE_TEMPLATE_DIR", tmp_path)
    monkeypatch.setitem(
        order_form_service._FAX_FAMILY_SOURCE_MAP,
        "fax_layout_regular_forbidden_v1",
        {
            "source_workbook": source_name,
            "family_label": "共通・禁食2種",
        },
    )
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": "構造比較施設",
            "fax_template_id": "fax_layout_regular_forbidden_v1",
        },
    )

    output = order_form_service.build_fax_structure_only_excel(
        facility_id="FACSTRUCT01",
        week_sheet_name="4月5日～4月11日",
        output_dir=tmp_path,
    )

    generated = load_workbook(output)
    generated_sheet = generated["4月5日～4月11日"]
    rows = _metadata_rows(generated)

    assert generated_sheet["A3"].value == "構造比較施設"
    assert generated_sheet["A11"].value is None
    assert generated_sheet["B11"].value is None
    assert generated_sheet["C11"].value is None
    assert generated_sheet["D11"].value is None
    assert generated_sheet["G11"].value is None
    assert rows["facility_id"] == "FACSTRUCT01"
    assert rows["fax_template_id"] == "fax_layout_regular_forbidden_v1"


def test_build_order_form_excel_blocks_when_month_specific_source_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(order_form_service, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setitem(
        order_form_service._FAX_FAMILY_SOURCE_MAP,
        "fax_layout_missing_month_v1",
        {
            "family_label": "欠損確認",
            "month_sources": {"2026-03": "source.xlsx"},
            "source_workbook": "",
        },
    )
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": "欠損施設",
            "fax_template_id": "fax_layout_missing_month_v1",
        },
    )
    monkeypatch.setattr(
        order_form_service.menu_service,
        "get_menu_for_facility",
        lambda month_id, facility_id: {
            "entries": [
                {"menu_date": "2026-05-01", "daypart": "朝", "category": "副①", "name": "A"},
            ]
        },
    )

    with pytest.raises(
        ValueError,
        match="source workbook not configured for fax_template_id=fax_layout_missing_month_v1 month_id=2026-05",
    ):
        order_form_service.build_order_form_excel(facility_id="FACMISS01", month_id="2026-05")
