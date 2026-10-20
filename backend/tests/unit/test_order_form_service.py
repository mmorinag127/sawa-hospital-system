import pathlib
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_form_service  # noqa: E402


def _metadata_rows(workbook):
    return {key: value for key, value in workbook["設定"].iter_rows(min_row=2, values_only=True)}


def _cell_date(value):
    return value.date() if hasattr(value, "date") else value


def _build_source_workbook(path: pathlib.Path, sheet_name: str) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name
    worksheet["A2"] = "【発　注　連　絡　表】"
    worksheet["G3"] = "締切日1月1日まで"
    worksheet["A4"] = ""
    worksheet["D7"] = "献立"
    worksheet["E7"] = "常食"
    worksheet["F7"] = "肉禁"
    workbook.save(path)


def _authored_columns(quantities):
    return {"fax_template": {"columns": [
        {"role": "date", "source_index": 0},
        {"role": "daypart", "source_index": 1},
        {"role": "category", "source_index": 2},
        {"role": "menu", "source_index": 3},
        *[{"role": "quantity", "source_index": index - 1, "name": f"qty.{diet}_x",
           "diet_type": diet, "header": header} for index, diet, header in quantities],
    ]}}


def _bind_monthly_export(monkeypatch, facility_id, month, source, quantities):
    from copy import deepcopy
    facility = deepcopy(order_form_service.config_service.get_facility_config(facility_id))
    facility.update(_authored_columns(quantities))
    facility["order_form_month_sources"] = {month: str(source)}
    snapshot = SimpleNamespace(master={}, revision="test-revision", source="unit-fixture")
    monkeypatch.setattr(order_form_service.canonical_facility_store, "read_snapshot", lambda: snapshot)
    monkeypatch.setattr(order_form_service, "_resolve_monthly_facility", lambda _snapshot, _id, _month: deepcopy(facility))
    return facility


_PACKAGED_QUANTITIES = {
    "fax_layout_regular_forbidden_v1": [(5, "regular", "常食"), (7, "no_meat", "肉禁"), (8, "no_fish", "魚禁"), (9, "change_1", "変更①"), (10, "change_2", "変更②")],
    "fax_layout_floor_2f3f_v1": [(5, "regular_hana", "花"), (6, "regular_tsuki", "月"), (7, "soft_hana", "花"), (8, "soft_tsuki", "月"), (9, "mixer_hana", "花"), (10, "mixer_tsuki", "月"), (11, "no_fish", "魚禁(常食)")],
    "fax_layout_regular_soft_mixer_forbidden_v1": [(5, "regular", "常食"), (6, "packaged", "常食"), (7, "soft", "軟菜"), (8, "mixer", "ミキサー"), (9, "no_meat", "肉禁"), (10, "no_fish", "魚禁")],
    "fax_layout_regular_staff_daycare_v1": [(5, "regular", "常食"), (6, "staff", "職員"), (7, "no_meat", "肉禁"), (8, "no_fish", "魚禁"), (9, "no_sesame", "ゴマ禁アレルギー"), (10, "change_1", "変更①")],
    "fax_layout_regular_diabetes_v1": [(5, "regular", "常食"), (6, "diabetes", "糖尿"), (7, "no_meat", "肉禁"), (8, "no_fish", "魚禁"), (9, "change_1", "変更①"), (10, "change_2", "変更②")],
    "fax_layout_regular_staff_daycare_other_forbidden_v1": [(5, "regular", "常食"), (6, "staff", "職員"), (7, "daycare", "通所"), (8, "no_meat", "肉禁"), (9, "no_fish", "魚禁"), (10, "other", "その他"), (11, "change_1", "変更①"), (12, "change_2", "変更②")],
    "fax_layout_soft_packaging_forbidden_v1": [(5, "soft", "軟菜"), (6, "packaged", "袋分け"), (7, "no_meat", "肉禁"), (8, "no_fish", "魚禁"), (9, "change_1", "変更①"), (10, "change_2", "変更②")],
}


class _FakeSessionScope:
    def __init__(self, order):
        self.order = order

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, _model, order_id):
        if self.order and self.order.id == order_id:
            return self.order
        return None


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
    assert str(worksheet["B1"].value).startswith("発注書作成: ")
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
    assert str(worksheet["B1"].value).startswith("発注書作成: ")
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
                {"menu_date": "2026-03-15", "daypart": "朝", "category": "副①", "name": "C2"},
                {"menu_date": "2026-03-22", "daypart": "朝", "category": "副①", "name": "C3"},
                {"menu_date": "2026-03-29", "daypart": "夕", "category": "主", "name": "D"},
                {"menu_date": "2026-03-31", "daypart": "朝", "category": "副①", "name": "E"},
            ]
        },
    )

    _bind_monthly_export(monkeypatch, "FACTEST01", "2026-03", tmp_path / source_name,
                         [(5, "regular", "常食"), (6, "no_meat", "肉禁")])
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
    assert str(first_sheet["B1"].value).startswith("発注書作成: ")
    assert first_sheet["A11"].value.date() == datetime(2026, 3, 1).date()
    assert first_sheet["B11"].value == "朝"
    assert first_sheet["C12"].value == "主"
    assert first_sheet["D12"].value == "B"
    assert first_sheet["G3"].value == "締切日2月13日まで"
    assert last_sheet["A11"].value.date() == datetime(2026, 3, 29).date()
    assert last_sheet["A12"].value.date() == datetime(2026, 3, 31).date()
    assert rows["month_id"] == "2026-03"
    assert rows["sheet_count"] == 5
    assert rows["entry_count"] == 7
    assert rows["week_1_sheet_name"] == "3月1日～3月7日"
    assert workbook["設定"].sheet_state == "hidden"


def _saved_form_input(monkeypatch, tmp_path, *, start="2026-03-01", end="2026-03-07",
                      source_month="2026-03", source_week="3月1日～3月7日"):
    from copy import deepcopy
    from datetime import date

    source = tmp_path / "saved-source.xlsx"
    _build_source_workbook(source, source_week)
    workbook = load_workbook(source)
    workbook[source_week]["E7"] = "常食"
    workbook[source_week]["F7"] = "肉禁"
    workbook.save(source)
    workbook.close()
    template = {"columns": [
        {"role": "date", "source_index": 0},
        {"role": "daypart", "source_index": 1},
        {"role": "category", "source_index": 2},
        {"role": "menu", "source_index": 3},
        {"role": "quantity", "source_index": 4, "name": "qty.regular_x",
         "diet_type": "regular", "header": "常食"},
        {"role": "quantity", "source_index": 5, "name": "qty.no_meat_x",
         "diet_type": "no_meat", "header": "肉禁"},
    ]}
    captured = {
        "order_id": "ORD-SAVED-SHEET-001", "facility_id": "FACTEST01",
        "received_at": datetime.fromisoformat(start), "fax_version": {},
        "draft_id": "ODR-DRAFT-001", "month_id": start[:7],
        "week_code": f"{start[:7]}@{start}~{end}",
        "week_start": date.fromisoformat(start), "week_end": date.fromisoformat(end),
        "facility": {
            "facility_id": "FACTEST01", "facility_name": "保存済み施設",
            "fax_template_id": "fax_layout_regular_forbidden_v1",
            "fax_template": template,
            "order_form_month_sources": {source_month: str(source)},
        },
        "draft_payload": {
            "fields": ["date_mmdd", "daypart", "category", "menu", "qty.regular_x", "qty.no_meat_x"],
            "rows": [[start, "朝", "主", "テスト献立A", "12", "3"],
                     [start, "昼", "副", "テスト献立B", "4", ""]],
        },
    }
    calls = []
    def capture(_session, order_id):
        assert order_id == captured["order_id"]
        calls.append(order_id)
        return deepcopy(captured)
    monkeypatch.setattr(order_form_service, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(order_form_service, "session_scope", lambda: _FakeSessionScope(None))
    # These workbook-unit tests begin at the immutable captured-input boundary.
    monkeypatch.setattr(order_form_service, "_capture_saved_sheet_order_form_input", capture)
    return captured, source, calls


def test_build_saved_sheet_order_form_excel_writes_saved_quantities_to_week_form(tmp_path, monkeypatch):
    captured, source, calls = _saved_form_input(monkeypatch, tmp_path)
    original = source.read_bytes()
    output = order_form_service.build_saved_sheet_order_form_excel(order_id=captured["order_id"])
    workbook = load_workbook(output)
    worksheet = workbook["3月1日～3月7日"]
    metadata = _metadata_rows(workbook)
    assert worksheet["A4"].value == "保存済み施設"
    assert worksheet["A11"].value.date() == datetime(2026, 3, 1).date()
    assert worksheet["B11"].value == "朝"
    assert worksheet["C11"].value == "主"
    assert worksheet["D11"].value == "テスト献立A"
    assert worksheet["E11"].value == 12
    assert worksheet["F11"].value == 3
    assert worksheet["B12"].value == "昼"
    assert worksheet["D12"].value == "テスト献立B"
    assert worksheet["E12"].value == 4
    assert worksheet["F12"].value is None
    assert str(worksheet["B1"].value).startswith("FAX読取シートExcel作成: ")
    assert metadata["mode"] == "saved_sheet"
    assert metadata["order_id"] == captured["order_id"]
    assert metadata["saved_sheet_draft_id"] == "ODR-DRAFT-001"
    assert calls == [captured["order_id"], captured["order_id"]]
    assert source.read_bytes() == original
    workbook.close()


@pytest.mark.parametrize("start,end,source_month,source_week,message", [
    ("2026-03-08", "2026-03-14", "2026-03", "3月1日～3月7日",
     "saved sheet week not found in configured workbook"),
    ("2026-05-10", "2026-05-16", "2026-04", "4月26日～4月30日",
     "saved sheet source workbook not configured"),
])
def test_saved_form_rejects_missing_week_or_month_without_substituting_source(
    tmp_path, monkeypatch, start, end, source_month, source_week, message,
):
    captured, source, calls = _saved_form_input(
        monkeypatch, tmp_path, start=start, end=end,
        source_month=source_month, source_week=source_week,
    )
    original = source.read_bytes()
    with pytest.raises(ValueError, match=message):
        order_form_service.build_saved_sheet_order_form_excel(order_id=captured["order_id"])
    assert calls == [captured["order_id"]]
    assert list(tmp_path.iterdir()) == [source]
    assert source.read_bytes() == original


def test_saved_form_rejects_changed_captured_input_before_publishing(tmp_path, monkeypatch):
    from copy import deepcopy
    captured, source, _calls = _saved_form_input(monkeypatch, tmp_path)
    reads = []
    def capture(_session, _order_id):
        result = deepcopy(captured)
        if reads:
            result["draft_payload"]["rows"][0][4] = "99"
        reads.append(result)
        return result
    monkeypatch.setattr(order_form_service, "_capture_saved_sheet_order_form_input", capture)
    with pytest.raises(ValueError, match="saved sheet output input changed"):
        order_form_service.build_saved_sheet_order_form_excel(order_id=captured["order_id"])
    assert len(reads) == 2
    assert list(tmp_path.iterdir()) == [source]


def test_write_saved_sheet_rows_maps_workflow_v2_fields_by_header_role() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["A7"] = "日　付"
    worksheet["B7"] = "区　分"
    worksheet["C7"] = "副区分"
    worksheet["D7"] = "献立"
    worksheet["E7"] = "合計"
    worksheet["F7"] = "常食"
    worksheet["G7"] = "通所"
    worksheet["H7"] = "職員"
    worksheet["I7"] = "禁食"
    worksheet["I9"] = "肉禁"
    worksheet["J9"] = "魚禁"
    worksheet["K9"] = "その他"
    worksheet["L7"] = "変更①"
    worksheet["M7"] = "備考欄"
    facility = _authored_columns([(5, "total", "合計"), (6, "regular", "常食"), (7, "daycare", "通所"), (8, "staff", "職員"),
        (9, "no_meat", "肉禁"), (10, "no_fish", "魚禁"), (11, "no_fried", "その他"), (12, "change_1", "変更①")])
    facility["fax_template"]["columns"].extend([
        {"role": "note", "source_index": 12},
    ])

    written = order_form_service._write_saved_sheet_rows_to_order_form(
        worksheet,
        fields=[
            "date_mmdd",
            "daypart",
            "category",
            "menu",
            "qty.total_x",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
        header=["日付", "区分", "副区分", "メニュー", "合計"],
        rows=[
            ["05/10", "昼", "主", "豚肉と白菜のすき煮", 65, "60", "2", "1", "3", "4", "5", "6", "備考"],
        ],
        received_at=datetime(2026, 5, 10, 9, 0, 0),
        template=facility["fax_template"], facility_config=facility,
        week_code="2026-05@2026-05-10~2026-05-16",
    )

    assert written == 1
    assert _cell_date(worksheet["A11"].value) == datetime(2026, 5, 10).date()
    assert worksheet["B11"].value == "昼"
    assert worksheet["C11"].value == "主"
    assert worksheet["D11"].value == "豚肉と白菜のすき煮"
    assert worksheet["E11"].value == 65
    assert worksheet["F11"].value == 60
    assert worksheet["G11"].value == 2
    assert worksheet["H11"].value == 1
    assert worksheet["I11"].value == 3
    assert worksheet["J11"].value == 4
    assert worksheet["K11"].value == 5
    assert worksheet["L11"].value == 6


def test_write_saved_sheet_rows_rejects_unknown_quantity_without_shifting_columns() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["A7"] = "日　付"
    worksheet["B7"] = "区　分"
    worksheet["D7"] = "献立"
    worksheet["E7"] = "常食"
    worksheet["G7"] = "禁食"
    worksheet["G9"] = "肉禁"
    worksheet["H9"] = "魚禁"
    worksheet["I7"] = "変更①"
    worksheet["J7"] = "変更②"
    facility = _authored_columns([(5, "regular", "常食"), (7, "no_meat", "肉禁"),
        (8, "no_fish", "魚禁"), (9, "change_1", "変更①"), (10, "change_2", "変更②")])
    facility["fax_template"]["columns"].append({"role": "note", "source_index": 10})
    fields = ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.unknown_x",
              "qty.no_meat_x", "qty.no_fish_x", "qty.change_1_x", "qty.change_2_x", "remarks"]
    rows = [["05/10", "昼", "豚肉と白菜のすき煮", "32", "", "1", "2", "3", "4", ""]]
    with pytest.raises(ValueError, match="saved sheet canonical field unmapped"):
        order_form_service._write_saved_sheet_rows_to_order_form(
            worksheet, fields=fields, rows=rows, received_at=datetime(2026, 5, 10),
            template=facility["fax_template"], facility_config=facility,
            week_code="2026-05@2026-05-10~2026-05-16",
        )
    assert worksheet["E11"].value is None
    written = order_form_service._write_saved_sheet_rows_to_order_form(
        worksheet,
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.change_1_x",
            "qty.change_2_x",
            "remarks",
        ],
        header=["日付", "区分", "メニュー", "常食", "不明"],
        rows=[
            ["05/10", "昼", "豚肉と白菜のすき煮", "32", "1", "2", "3", "4", ""],
        ],
        received_at=datetime(2026, 5, 10, 9, 0, 0),
        template=facility["fax_template"], facility_config=facility,
        week_code="2026-05@2026-05-10~2026-05-16",
    )

    assert written == 1
    assert worksheet["E11"].value == 32
    assert worksheet["G11"].value == 1
    assert worksheet["H11"].value == 2
    assert worksheet["I11"].value == 3
    assert worksheet["J11"].value == 4


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


def test_facility_template_merge_detection_uses_facility_settings_only() -> None:
    name_only_facility = {
        "facility_id": "FAC00007",
        "facility_name": "ゆうゆう（株）百々家",
        "fax_template_id": "fax_layout_regular_forbidden_v1",
    }

    assert not order_form_service.facility_template_has_vertical_merged_quantity_cells(
        name_only_facility,
        week_sheet_name="4月26日～4月30日",
    )


def test_facility_template_merge_detection_uses_body_merge_policy() -> None:
    facility = {
        "facility_id": "FAC00007",
        "facility_name": "ゆうゆう（株）百々家",
        "fax_template_id": "fax_layout_regular_forbidden_v1",
        "fax_template": {
            "body_merge_policy": {
                "mode": "daypart",
                "columns": ["qty.regular_x"],
            },
        },
    }

    assert order_form_service.facility_template_has_vertical_merged_quantity_cells(
        facility,
        week_sheet_name="4月26日～4月30日",
    )

    facility["fax_template_override"] = facility.pop("fax_template")
    assert not order_form_service.facility_template_has_vertical_merged_quantity_cells(
        facility,
        week_sheet_name="4月26日～4月30日",
    )


def test_facility_template_scan_uses_registered_source_workbook_uri(tmp_path) -> None:
    source_path = tmp_path / "registered.xlsx"
    _build_source_workbook(source_path, "4月26日～4月30日")
    facility = {
        "facility_id": "FACURI",
        "facility_name": "登録テンプレート施設",
        "fax_template_id": "fax_layout_regular_forbidden_v1",
        "order_form_source_workbook_uri": str(source_path),
    }

    assert order_form_service.resolve_facility_source_workbook_name_for_week_sheet(
        facility,
        "4月26日～4月30日",
    ) == str(source_path)


def test_facility_template_scan_month_uri_replaces_default_source(tmp_path) -> None:
    default_path = tmp_path / "default.xlsx"
    month_path = tmp_path / "month.xlsx"
    _build_source_workbook(default_path, "3月22日～3月28日")
    _build_source_workbook(month_path, "4月26日～4月30日")
    facility = {
        "facility_id": "FACURI",
        "facility_name": "登録テンプレート施設",
        "fax_template_id": "fax_layout_regular_forbidden_v1",
        "order_form_source_workbook_uri": str(default_path),
        "order_form_month_source_uris": {
            "2026-04": str(month_path),
        },
    }

    assert order_form_service.resolve_facility_source_workbook_name_for_week_sheet(
        facility,
        "4月26日～4月30日",
    ) == str(month_path)


def test_build_fax_order_form_excel_uses_explicit_facility_source_workbook(tmp_path, monkeypatch):
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": "ゆうゆう（株）百々家",
            "fax_template_id": "fax_layout_regular_forbidden_v1",
            "order_form_month_sources": {
                "2026-03": "百々家 2603.xlsx",
                "2026-04": "百々家 2604.xlsx",
            },
        },
    )

    output = order_form_service.build_fax_order_form_excel(
        facility_id="FACMOMO",
        week_sheet_name="4月26日～4月30日",
        output_dir=tmp_path,
    )

    workbook = load_workbook(output)
    worksheet = workbook["4月26日～4月30日"]
    metadata = _metadata_rows(workbook)

    assert metadata["source_workbook"] == "百々家 2604.xlsx"
    assert "A11:A15" in {str(item) for item in worksheet.merged_cells.ranges}
    assert "A16:A18" in {str(item) for item in worksheet.merged_cells.ranges}
    assert "B11:B12" in {str(item) for item in worksheet.merged_cells.ranges}
    assert "B13:B15" in {str(item) for item in worksheet.merged_cells.ranges}
    assert "E11:E12" in {str(item) for item in worksheet.merged_cells.ranges}


def test_write_week_entries_restores_weekday_to_template_weekday_block() -> None:
    source_path = order_form_service._resolve_source_workbook_path("共通　2604.xlsx")
    workbook = load_workbook(source_path)
    worksheet = workbook["4月26日～4月30日"]

    order_form_service._clear_week_sheet_body(worksheet)
    order_form_service._write_week_entries(
        worksheet,
        [{**entry, "_source_index": index} for index, entry in enumerate([
            {"_menu_date_obj": datetime(2026, 4, 26).date(), "daypart": "朝", "category": "副①", "name": "A"},
            {"_menu_date_obj": datetime(2026, 4, 26).date(), "daypart": "朝", "category": "副②", "name": "B"},
            {"_menu_date_obj": datetime(2026, 4, 26).date(), "daypart": "昼", "category": "主", "name": "C"},
            {"_menu_date_obj": datetime(2026, 4, 26).date(), "daypart": "昼", "category": "副①", "name": "D"},
            {"_menu_date_obj": datetime(2026, 4, 26).date(), "daypart": "昼", "category": "副②", "name": "E"},
            {"_menu_date_obj": datetime(2026, 4, 26).date(), "daypart": "夕", "category": "主", "name": "F"},
            {"_menu_date_obj": datetime(2026, 4, 26).date(), "daypart": "夕", "category": "副①", "name": "G"},
            {"_menu_date_obj": datetime(2026, 4, 26).date(), "daypart": "夕", "category": "副②", "name": "H"},
        ])],
        facility_config=_authored_columns(_PACKAGED_QUANTITIES["fax_layout_regular_forbidden_v1"]),
    )

    assert _cell_date(worksheet["A11"].value) == datetime(2026, 4, 26).date()
    assert "A11:A17" in {str(region) for region in worksheet.merged_cells.ranges}
    assert worksheet["A18"].value == "日"


def test_write_week_entries_expands_for_all_63_rows_without_overflow() -> None:
    source_path = order_form_service._resolve_source_workbook_path("共通　2604.xlsx")
    workbook = load_workbook(source_path)
    worksheet = workbook["4月26日～4月30日"]
    start_date = datetime(2026, 7, 5).date()
    entries = []
    for day_offset in range(7):
        menu_date = start_date + timedelta(days=day_offset)
        entries.extend(
            [
                {"_menu_date_obj": menu_date, "daypart": "朝", "category": "副①", "name": f"朝A{day_offset}"},
                {"_menu_date_obj": menu_date, "daypart": "朝", "category": "副②", "name": f"朝B{day_offset}"},
                {"_menu_date_obj": menu_date, "daypart": "昼", "category": "主", "name": f"昼A{day_offset}"},
                {"_menu_date_obj": menu_date, "daypart": "昼", "category": "副①", "name": f"昼B{day_offset}"},
                {"_menu_date_obj": menu_date, "daypart": "昼", "category": "副②", "name": f"昼C{day_offset}"},
                {"_menu_date_obj": menu_date, "daypart": "昼", "category": "副③", "name": f"昼D{day_offset}"},
                {"_menu_date_obj": menu_date, "daypart": "夕", "category": "主", "name": f"夕A{day_offset}"},
                {"_menu_date_obj": menu_date, "daypart": "夕", "category": "副①", "name": f"夕B{day_offset}"},
                {"_menu_date_obj": menu_date, "daypart": "夕", "category": "副②", "name": f"夕C{day_offset}"},
            ]
        )

    order_form_service._clear_week_sheet_body(worksheet)
    result = order_form_service._write_week_entries(worksheet,
        [{**entry, "_source_index": index} for index, entry in enumerate(entries)],
        facility_config=_authored_columns(_PACKAGED_QUANTITIES["fax_layout_regular_forbidden_v1"]))

    assert result.source_rows == 63
    assert int(result) == 63
    assert result.overflow_rows == 0
    assert result.overflow_entries == []
    assert [worksheet.cell(11 + index, 4).value for index in range(63)] == [entry["name"] for entry in entries]
    assert worksheet["D73"].value == "夕C6"
    assert worksheet["D74"].value is None


def test_clear_week_sheet_body_uses_header_detected_quantity_columns_for_sibling_template() -> None:
    source_path = order_form_service._resolve_source_workbook_path("百々家 2604.xlsx")
    workbook = load_workbook(source_path)
    worksheet = workbook["4月26日～4月30日"]

    assert "E11:E12" in {str(item) for item in worksheet.merged_cells.ranges}

    order_form_service._clear_week_sheet_body(worksheet)
    merged_ranges = {str(item) for item in worksheet.merged_cells.ranges}

    assert "E11:E12" in merged_ranges
    assert worksheet["E11"].value is None


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
                *[{"menu_date": f"2026-05-{day:02}", "daypart": "朝", "category": "副①", "name": f"週{day}"}
                  for day in (3, 10, 17, 24)],
                {"menu_date": "2026-05-31", "daypart": "夕", "category": "主", "name": "B"},
            ]
        },
    )

    _bind_monthly_export(monkeypatch, "FAC6WEEK", "2026-05", tmp_path / source_name,
                         [(5, "regular", "常食"), (6, "no_meat", "肉禁")])
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
def test_build_order_form_excel_validates_all_packaged_family_sheets(tmp_path, monkeypatch, fax_template_id, facility_name):
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
                *[{"menu_date": f"2026-03-{day:02}", "daypart": "朝", "category": "副①", "name": f"週{day}"}
                  for day in (8, 15, 22)],
                {"menu_date": "2026-03-29", "daypart": "夕", "category": "副②", "name": "C"},
            ]
        },
    )

    source = order_form_service._FAX_FAMILY_SOURCE_MAP[fax_template_id]["month_sources"]["2026-03"]
    source_path = order_form_service._resolve_source_workbook_path(source)
    original = source_path.read_bytes()
    _bind_monthly_export(monkeypatch, "FACTESTALL", "2026-03", source, _PACKAGED_QUANTITIES[fax_template_id])
    if fax_template_id == "fax_layout_regular_soft_mixer_forbidden_v1":
        # Actual asset changes soft/mixer from G/H to I/J after week 1.
        # One authored layout cannot silently remap those later worksheets.
        with pytest.raises(ValueError, match="source workbook quantity header mismatch:qty.soft_x"):
            order_form_service.build_order_form_excel(facility_id="FACTESTALL", month_id="2026-03")
        assert list(tmp_path.iterdir()) == []
        assert source_path.read_bytes() == original
        return
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
    assert str(workbook[workbook.sheetnames[0]]["B1"].value).startswith("発注書作成: ")
    assert source_path.read_bytes() == original


def test_infer_fax_template_id_from_facility_requires_explicit_template_id() -> None:
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
        is None
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
        is None
    )
    assert (
        order_form_service._infer_fax_template_id_from_facility(
            {"facility_name": "佐古", "fax_template_id": "山城"}
        )
        == "山城"
    )


def test_build_order_form_excel_rejects_inferred_template_id(tmp_path, monkeypatch):
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

    _bind_monthly_export(monkeypatch, "FACINF01", "2026-03", tmp_path / source_name,
                         [(5, "regular", "常食"), (6, "no_meat", "肉禁")])
    with pytest.raises(ValueError, match="facility fax_template_id not found"):
        order_form_service.build_order_form_excel(facility_id="FACINF01", month_id="2026-03")
    assert list(tmp_path.iterdir()) == [tmp_path / source_name]


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
                *[{"menu_date": f"2026-04-{day:02}", "daypart": "朝", "category": "副①", "name": f"週{day}"}
                  for day in (5, 12, 19)],
                {"menu_date": "2026-04-26", "daypart": "昼", "category": "主", "name": "B"},
            ]
        },
    )

    _bind_monthly_export(monkeypatch, "FACAPR01", "2026-04", "共通　2604.xlsx",
                         _PACKAGED_QUANTITIES["fax_layout_regular_forbidden_v1"])
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
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: {
            "facility_id": facility_id,
            "facility_name": "構造比較施設",
            "fax_template_id": "fax_layout_regular_forbidden_v1",
            "fax_template": {
                "columns": [
                    {"role": "date", "header": "日付", "name": "date_mmdd", "source_index": 0},
                    {"role": "daypart", "header": "区分", "name": "daypart", "source_index": 1},
                    {"role": "menu", "header": "献立", "name": "menu", "source_index": 2},
                    {"role": "quantity", "header": "常食", "name": "qty.regular_x", "source_index": 3},
                    {"role": "quantity", "header": "肉禁", "name": "qty.no_meat_x", "source_index": 4},
                ],
            },
        },
    )

    output = order_form_service.build_fax_structure_only_excel(
        facility_id="FACSTRUCT01",
        week_sheet_name="4月5日～4月11日",
        output_dir=tmp_path,
    )

    generated = load_workbook(output)
    generated_sheet = generated["facility_template"]
    schema = generated["generated_template_schema"]
    schema_rows = {str(row[0].value): row[1].value for row in schema.iter_rows(min_row=1, max_col=2) if row[0].value}

    assert generated_sheet["A4"].value == "構造比較施設"
    assert generated_sheet["B11"].value == "朝"
    assert generated_sheet["E11"].value is None
    assert schema_rows["source"] == "master_layout_template"
    assert schema_rows["facility_id"] == "FACSTRUCT01"

    legacy_facility = order_form_service.config_service.get_facility_config("FACSTRUCT01")
    legacy_facility["fax_template_override"] = legacy_facility.pop("fax_template")
    monkeypatch.setattr(
        order_form_service.config_service,
        "get_facility_config",
        lambda facility_id: legacy_facility,
    )
    with pytest.raises(ValueError, match="^facility_template_generated_columns_missing$"):
        order_form_service.build_fax_structure_only_excel(
            facility_id="FACSTRUCT01",
            week_sheet_name="4月5日～4月11日",
            output_dir=tmp_path / "old-key-only",
        )


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

    _bind_monthly_export(monkeypatch, "FACMISS01", "2026-03", "source.xlsx", [(5, "regular", "常食")])
    with pytest.raises(
        ValueError,
        match="saved sheet source workbook not configured",
    ):
        order_form_service.build_order_form_excel(facility_id="FACMISS01", month_id="2026-05")
