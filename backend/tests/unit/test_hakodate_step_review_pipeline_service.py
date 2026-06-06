import numpy as np
from openpyxl import Workbook

from src.services.hakodate_step_review_pipeline_service import (
    _detect_vertical_candidates,
    _draw_merge_aware_grid,
    _fit_extra_y_clusters_to_template_count,
    _ordered_match_y_clusters_to_template,
    _physical_internal_horizontal_lines,
    _post_menu_boundary_preserving_xs,
    _post_menu_target_regions,
    _row_height_outlier_evidence,
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


def test_full_table_row_axis_match_interpolates_missing_template_rows() -> None:
    template_ys = [0, 10, 20, 30, 40, 50]
    y_clusters = [
        {"cluster_index": 0, "value": 0.0},
        {"cluster_index": 1, "value": 10.0},
        {"cluster_index": 2, "value": 20.0},
        {"cluster_index": 3, "value": 40.0},
        {"cluster_index": 4, "value": 50.0},
    ]

    corrected, evidence = _ordered_match_y_clusters_to_template(
        template_ys=template_ys,
        y_clusters=y_clusters,
    )

    assert evidence["used"] is True
    assert evidence["skipped_template_indexes"] == [3]
    assert evidence["skipped_cluster_indexes"] == []
    assert corrected == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]


def test_full_table_row_axis_match_keeps_detected_boundary_when_extra_cluster_exists() -> None:
    template_ys = [0, 10, 20, 30, 40, 50]
    y_clusters = [
        {"cluster_index": 0, "value": 0.0},
        {"cluster_index": 1, "value": 10.0},
        {"cluster_index": 2, "value": 20.0},
        {"cluster_index": 3, "value": 30.0},
        {"cluster_index": 4, "value": 40.0},
        {"cluster_index": 5, "value": 50.0},
        {"cluster_index": 6, "value": 110.0},
    ]

    corrected, evidence = _ordered_match_y_clusters_to_template(
        template_ys=template_ys,
        y_clusters=y_clusters,
    )

    assert evidence["used"] is True
    assert evidence["method"] == "ordered_full_table_y_intersection_match_with_extra_cluster_fit"
    assert evidence["skipped_cluster_indexes"] == [6]
    assert corrected == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]


def test_extra_y_cluster_fit_prefers_balanced_row_heights_over_middle_skip() -> None:
    fitted, evidence = _fit_extra_y_clusters_to_template_count(
        template_ys=[0, 10, 20, 30, 40, 50],
        clusters=[0, 10, 20, 30, 40, 50, 110],
    )

    assert evidence["used"] is True
    assert evidence["skipped_cluster_indexes"] == [6]
    assert fitted == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]


def test_row_height_outlier_evidence_requires_manual_review() -> None:
    evidence = _row_height_outlier_evidence([0, 10, 20, 30, 55, 65])

    assert evidence["manual_review_required"] is True
    assert evidence["reason"] == "row_height_outlier_detected"
    assert evidence["outliers"] == [
        {"row_band_index": 3, "height": 25.0, "median_ratio": 2.5}
    ]


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


def test_step_review_target_regions_include_all_post_menu_columns() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["D7"] = "献立"
    worksheet["E7"] = "常食"
    worksheet["F7"] = "糖尿"
    worksheet["G7"] = "備考欄"
    worksheet["H7"] = "肉禁"
    worksheet["I7"] = "魚禁"
    worksheet["D11"] = "先頭メニュー"
    row_edges = [float(index * 10) for index in range(59)]
    column_edges = [float(index * 10) for index in range(10)]
    fax_template = {
        "columns": [
            {"index": 0, "role": "date", "field": "date_mmdd", "header": "日付"},
            {"index": 1, "role": "daypart", "field": "daypart", "header": "区分"},
            {"index": 3, "role": "menu_name", "field": "menu", "header": "献立"},
            {
                "index": 4,
                "role": "quantity",
                "name": "qty.regular_x",
                "diet_type": "regular",
                "area_id": "X",
                "header": "常食",
            },
            {
                "index": 5,
                "role": "quantity",
                "name": "qty.diabetes_x",
                "diet_type": "diabetes",
                "area_id": "X",
                "header": "糖尿",
            },
            {"index": 6, "role": "note", "field": "remarks", "header": "備考欄"},
        ],
    }

    regions, evidence = _post_menu_target_regions(
        worksheet=worksheet,
        column_edges=column_edges,
        row_edges=row_edges,
        fax_template=fax_template,
    )

    first_row_regions = [region for region in regions if int(region["worksheet_row"]) == 11]
    assert [region["sheet_cell"] for region in first_row_regions] == ["E11", "F11", "G11", "H11", "I11"]
    assert [region["field"] for region in first_row_regions] == [
        "qty.regular_x",
        "qty.diabetes_x",
        "note",
        "qty.no_meat_x",
        "qty.no_fish_x",
    ]
    assert evidence["template_column_restricted"] is False
    assert evidence["target_selection_mode"] == "all_physical_columns_right_of_menu"
    assert evidence["target_worksheet_cols"] == [5, 6, 7, 8, 9]
    assert evidence["canonical_target_worksheet_cols"] == [5, 6, 7]


def test_step_review_target_regions_skip_blank_menu_rows() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["D7"] = "献立"
    worksheet["E7"] = "常食"
    worksheet["D11"] = "先頭メニュー"
    worksheet["D12"] = ""
    worksheet["D13"] = "次メニュー"
    row_edges = [float(index * 10) for index in range(59)]
    column_edges = [float(index * 10) for index in range(6)]

    regions, evidence = _post_menu_target_regions(
        worksheet=worksheet,
        column_edges=column_edges,
        row_edges=row_edges,
    )

    worksheet_rows = {int(region["worksheet_row"]) for region in regions}
    assert 11 in worksheet_rows
    assert 12 not in worksheet_rows
    assert 13 in worksheet_rows
    assert evidence["blank_menu_row_count"] > 0


def test_detect_vertical_candidates_rejects_partial_height_correction_line() -> None:
    rectified = np.full((260, 360, 3), 255, dtype=np.uint8)
    for x in [40, 140, 240, 340]:
        rectified[30:230, x - 1 : x + 2] = 0
    rectified[95:165, 190 - 4 : 190 + 5] = 0

    candidates = _detect_vertical_candidates(rectified, [30, 30, 350, 230])

    rounded = [int(round(value)) for value in candidates]
    assert 190 not in rounded
    assert {40, 140, 240, 340}.issubset(set(rounded))


def test_post_menu_boundary_preserving_xs_keeps_first_quantity_line() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["D7"] = "献立"
    worksheet["E7"] = "常食"
    worksheet["F7"] = "月"
    worksheet["G7"] = "軟菜"
    worksheet["H7"] = "月"
    worksheet["I7"] = "ミキサー"
    worksheet["J7"] = "月"
    worksheet["K7"] = "備考"
    matched_xs = [15.0, 102.0, 139.0, 212.0, 1071.0, 1251.0, 1435.0, 1617.0, 1800.0, 1982.0, 2167.0, 2347.0]
    fax_candidates = [15.0, 102.0, 139.0, 212.0, 887.0, 1071.0, 1087.0, 1105.0, 1251.0, 1435.0, 1617.0, 1800.0, 1982.0, 2167.0, 2347.0]

    adjusted, evidence = _post_menu_boundary_preserving_xs(
        worksheet=worksheet,
        matched_xs=matched_xs,
        fax_x_candidates=fax_candidates,
    )

    assert evidence["used"] is True
    assert adjusted[4] == 887.0
    assert adjusted[-1] == 2347.0
    assert 1087.0 not in adjusted
    assert 1105.0 not in adjusted
    assert 2167.0 not in adjusted
