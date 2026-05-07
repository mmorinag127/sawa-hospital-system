from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from src.services import config_service
from src.services import master_order_form_template_service as service


def _facility_config(columns: list[dict]) -> dict:
    return {
        "facility_id": "FACTEST",
        "facility_name": "テスト施設",
        "fax_template": {
            "columns": columns,
        },
    }


def test_build_facility_template_from_master_writes_quantity_columns(tmp_path: Path) -> None:
    output = service.build_facility_template_xlsx(
        facility_config=_facility_config(
            [
                {"index": 0, "role": "date", "header": "日付", "name": "date_mmdd"},
                {"index": 1, "role": "daypart", "header": "区分", "name": "daypart"},
                {"index": 2, "role": "menu_name", "header": "献立", "name": "menu"},
                {"index": 3, "role": "quantity", "header": "常食", "name": "qty.regular_x"},
                {"index": 4, "role": "quantity", "header": "肉禁", "name": "qty.no_meat_x"},
                {"index": 5, "role": "quantity", "header": "魚禁", "name": "qty.no_fish_x"},
                {"index": 6, "role": "quantity", "header": "変更1", "name": "qty.change_1_x"},
                {"index": 7, "role": "quantity", "header": "変更2", "name": "qty.change_2_x"},
                {"index": 8, "role": "note", "header": "備考欄", "name": "remarks"},
            ]
        ),
        output_path=tmp_path / "facility_template.xlsx",
    )

    workbook = load_workbook(output)
    worksheet = workbook["facility_template"]
    schema = workbook["generated_template_schema"]

    assert worksheet["A4"].value == "テスト施設"
    assert "A4:E5" in {str(item) for item in worksheet.merged_cells.ranges}
    assert worksheet["A4"].alignment.horizontal == "center"
    assert worksheet["A4"].alignment.shrink_to_fit is True
    assert worksheet["A4"].font.bold is True
    assert worksheet["E7"].value == "常食"
    assert worksheet["F7"].value == "肉禁"
    assert worksheet["G7"].value == "魚禁"
    assert worksheet["H7"].value == "変更1"
    assert worksheet["I7"].value == "変更2"
    assert worksheet["J7"].value == "備考欄"
    assert str(worksheet.print_area) == "'facility_template'!$A$1:$J$64"
    assert schema.sheet_state == "hidden"
    assert schema["B8"].value == 6


def test_build_facility_template_from_master_merges_consecutive_header_groups(tmp_path: Path) -> None:
    output = service.build_facility_template_xlsx(
        facility_config=_facility_config(
            [
                {"index": 0, "role": "date", "header": "日付", "name": "date_mmdd"},
                {"index": 1, "role": "daypart", "header": "区分", "name": "daypart"},
                {"index": 2, "role": "menu_name", "header": "献立", "name": "menu"},
                {"index": 3, "role": "quantity", "header": "常食", "name": "qty.regular_x"},
                {
                    "index": 4,
                    "role": "quantity",
                    "header": "肉禁",
                    "header_group": "禁食",
                    "name": "qty.no_meat_x",
                },
                {
                    "index": 5,
                    "role": "quantity",
                    "header": "魚禁",
                    "header_group": "禁食",
                    "name": "qty.no_fish_x",
                },
                {"index": 6, "role": "note", "header": "備考欄", "name": "remarks"},
            ]
        ),
        output_path=tmp_path / "facility_template_grouped.xlsx",
    )

    workbook = load_workbook(output)
    worksheet = workbook["facility_template"]
    merged_ranges = {str(item) for item in worksheet.merged_cells.ranges}

    assert "F7:G7" in merged_ranges
    assert worksheet["F7"].value == "禁食"
    assert worksheet["F8"].value == "肉禁"
    assert worksheet["G8"].value == "魚禁"
    assert worksheet["E7"].value == "常食"
    assert worksheet["H7"].value == "備考欄"


def test_build_facility_template_from_master_supports_all_master_facilities(tmp_path: Path) -> None:
    master = config_service.load_facility_master()
    facility_ids = [facility["facility_id"] for facility in master.get("facilities", []) if facility.get("facility_id")]

    assert facility_ids
    for facility in master.get("facilities", []):
        facility_id = facility.get("facility_id")
        if not facility_id:
            continue
        resolved = config_service._build_facility_config(facility_id=facility_id, facility=facility)  # noqa: SLF001
        output = service.build_facility_template_xlsx(
            facility_config=resolved,
            output_path=tmp_path / f"{facility_id}_facility_template.xlsx",
        )
        workbook = load_workbook(output, read_only=True)
        worksheet = workbook["facility_template"]
        schema = workbook["generated_template_schema"]

        assert worksheet["E7"].value
        assert schema["B8"].value >= 1


def test_build_facility_template_from_master_uses_explicit_header_groups_only(tmp_path: Path) -> None:
    output = service.build_facility_template_xlsx(
        facility_config=_facility_config(
            [
                {"index": 0, "role": "date", "header": "日付", "name": "date_mmdd"},
                {"index": 1, "role": "daypart", "header": "区分", "name": "daypart"},
                {"index": 2, "role": "menu_name", "header": "献立", "name": "menu"},
                {"index": 3, "role": "quantity", "header": "常食2F", "name": "qty.regular_2f"},
                {"index": 4, "role": "quantity", "header": "常食3F", "name": "qty.regular_3f"},
                {"index": 5, "role": "note", "header": "備考", "name": "remarks"},
            ]
        ),
        output_path=tmp_path / "facility_template_ungrouped.xlsx",
    )

    workbook = load_workbook(output)
    worksheet = workbook["facility_template"]
    merged_ranges = {str(item) for item in worksheet.merged_cells.ranges}

    assert "E7:F7" not in merged_ranges
    assert worksheet["E7"].value == "常食2F"
    assert worksheet["F7"].value == "常食3F"


def test_build_facility_template_from_master_applies_configured_two_level_headers(tmp_path: Path) -> None:
    master = config_service.load_facility_master()
    facility = next(item for item in master.get("facilities", []) if item.get("facility_id") == "FAC00003")
    resolved = config_service._build_facility_config(facility_id="FAC00003", facility=facility)  # noqa: SLF001

    output = service.build_facility_template_xlsx(
        facility_config=resolved,
        output_path=tmp_path / "FAC00003_facility_template.xlsx",
    )

    workbook = load_workbook(output)
    worksheet = workbook["facility_template"]
    merged_ranges = {str(item) for item in worksheet.merged_cells.ranges}

    assert "E7:F7" in merged_ranges
    assert "G7:H7" in merged_ranges
    assert "I7:J7" in merged_ranges
    assert worksheet["E7"].value == "常食"
    assert worksheet["E8"].value == "2F"
    assert worksheet["F8"].value == "3F"
    assert worksheet["G7"].value == "軟菜"
    assert worksheet["G8"].value == "2F"
    assert worksheet["H8"].value == "3F"
    assert worksheet["I7"].value == "ミキサー"
    assert worksheet["I8"].value == "2F"
    assert worksheet["J8"].value == "3F"
