import numpy as np
from openpyxl import Workbook

from src.services.hakodate_step_review_pipeline_service import (
    _draw_merge_aware_grid,
    _physical_internal_horizontal_lines,
    _post_menu_target_regions,
    _step_review_merge_regions_for_grid,
    _step_review_physical_row_map,
    _step_review_worksheet_row_to_grid_index,
)


def test_physical_internal_horizontal_lines_detects_full_width_rule() -> None:
    mask = np.zeros((80, 80), dtype=np.uint8)
    mask[30, 10:50] = 255

    hits = _physical_internal_horizontal_lines(
        horizontal_line_mask=mask,
        row_edges=[10.0, 30.0, 50.0],
        bbox=[10.0, 10.0, 50.0, 50.0],
        start_row_index=0,
        row_span=2,
    )

    assert hits == [
        {
            "boundary_row_index": 1,
            "expected_y": 30.0,
            "detected_y": 30,
            "line_ratio": 1.0,
        }
    ]


def test_physical_internal_horizontal_lines_ignores_short_marks() -> None:
    mask = np.zeros((80, 80), dtype=np.uint8)
    mask[30, 10:20] = 255

    hits = _physical_internal_horizontal_lines(
        horizontal_line_mask=mask,
        row_edges=[10.0, 30.0, 50.0],
        bbox=[10.0, 10.0, 50.0, 50.0],
        start_row_index=0,
        row_span=2,
    )

    assert hits == []


def test_physical_internal_horizontal_lines_ignores_unmerged_rows() -> None:
    mask = np.zeros((80, 80), dtype=np.uint8)
    mask[30, 10:50] = 255

    hits = _physical_internal_horizontal_lines(
        horizontal_line_mask=mask,
        row_edges=[10.0, 30.0, 50.0],
        bbox=[10.0, 10.0, 50.0, 50.0],
        start_row_index=0,
        row_span=1,
    )

    assert hits == []


def test_step_review_body_rows_start_after_preserved_two_stage_header_boundary() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.cell(row=11, column=4, value="先頭メニュー")
    worksheet.cell(row=66, column=4, value="最終メニュー")
    worksheet.cell(row=67, column=4, value="")

    rows = _step_review_physical_row_map(worksheet, row_count=58)

    assert rows[2]["worksheet_row"] == 11
    assert rows[2]["menu_name"] == "先頭メニュー"
    assert rows[57]["worksheet_row"] == 66
    assert 58 not in rows


def test_step_review_worksheet_row_to_grid_index_preserves_two_stage_header_boundary() -> None:
    assert _step_review_worksheet_row_to_grid_index(7) == 0
    assert _step_review_worksheet_row_to_grid_index(8) == 0
    assert _step_review_worksheet_row_to_grid_index(9) == 1
    assert _step_review_worksheet_row_to_grid_index(10) == 1
    assert _step_review_worksheet_row_to_grid_index(11) == 2
    assert _step_review_worksheet_row_to_grid_index(12) == 3
    assert _step_review_worksheet_row_to_grid_index(66) == 57
    assert _step_review_worksheet_row_to_grid_index(67) is None


def test_step_review_header_merge_regions_use_grid_band_span() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.merge_cells("G7:H8")
    worksheet["G7"] = "禁食"
    worksheet.merge_cells("G9:G10")
    worksheet["G9"] = "肉禁"
    worksheet.merge_cells("H9:H10")
    worksheet["H9"] = "魚禁"
    worksheet.merge_cells("E7:E10")
    worksheet["E7"] = "常食"

    regions = {
        str(region["range"]): region
        for region in _step_review_merge_regions_for_grid(
            worksheet,
            row_edges=[float(i) for i in range(59)],
            column_edges=[float(i) for i in range(13)],
        )
    }

    assert regions["G7:H8"]["start_row_index"] == 0
    assert regions["G7:H8"]["end_row_index"] == 0
    assert regions["G7:H8"]["row_span"] == 1
    assert regions["G9:G10"]["start_row_index"] == 1
    assert regions["G9:G10"]["end_row_index"] == 1
    assert regions["G9:G10"]["row_span"] == 1
    assert regions["H9:H10"]["start_row_index"] == 1
    assert regions["E7:E10"]["start_row_index"] == 0
    assert regions["E7:E10"]["end_row_index"] == 1
    assert regions["E7:E10"]["row_span"] == 2


def test_step_review_grid_keeps_template_merge_when_fax_has_internal_line() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.merge_cells("E11:E12")
    row_edges = [float(index * 10) for index in range(59)]
    column_edges = [float(index * 10) for index in range(6)]
    horizontal_mask = np.zeros((600, 80), dtype=np.uint8)
    horizontal_mask[30, 40:50] = 255
    rectified_fax = np.full((600, 80, 3), 255, dtype=np.uint8)

    _image, evidence = _draw_merge_aware_grid(
        worksheet=worksheet,
        rectified_fax=rectified_fax,
        xs=column_edges,
        ys=row_edges,
        horizontal_line_mask=horizontal_mask,
    )

    assert evidence["merge_region_count"] == 1
    assert evidence["retained_merge_region_count"] == 1
    assert evidence["physically_split_merge_region_count"] == 0
    assert evidence["physically_split_merge_regions"] == []
    assert evidence["merged_ranges"] == ["E11:E12"]


def test_step_review_target_regions_keep_template_merge_center() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["D7"] = "献立"
    worksheet["D11"] = "先頭メニュー"
    worksheet["D12"] = "結合内メニュー"
    worksheet.merge_cells("E11:E12")
    row_edges = [float(index * 10) for index in range(59)]
    column_edges = [float(index * 10) for index in range(6)]
    horizontal_mask = np.zeros((600, 80), dtype=np.uint8)
    horizontal_mask[30, 40:50] = 255

    regions, evidence = _post_menu_target_regions(
        worksheet=worksheet,
        column_edges=column_edges,
        row_edges=row_edges,
        horizontal_line_mask=horizontal_mask,
    )

    by_id = {str(region["region_id"]): region for region in regions}
    assert "E11:E12" in by_id
    assert "E11" not in by_id
    assert by_id["E11:E12"]["bbox"] == [40.0, 20.0, 50.0, 40.0]
    assert by_id["E11:E12"]["merged_cell"]["range"] == "E11:E12"
    assert by_id["E11:E12"]["covered_sheet_cells"] == ["E11", "E12"]
    assert evidence["physical_split_excel_merge_count"] == 0
    assert evidence["physical_split_excel_merge_ranges"] == []
