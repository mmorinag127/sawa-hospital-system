import cv2
import numpy as np
from openpyxl import Workbook
import pytest

from src.services.hakodate_fixed_quad_registration_service import (
    canonical_template_axes_from_workbook,
    extract_template_axes_from_image,
    resolve_template_axes_from_manifest_or_image,
    rectify_fax_to_template_grid,
)


def test_extract_template_axes_fills_partial_header_line_by_gap_without_table_external_fallback() -> None:
    image = np.full((4000, 400, 3), 255, dtype=np.uint8)
    xs = [10, 100, 250, 390]
    external_y = 80
    partial_header_y = 180
    table_ys = [120, 240] + [300 + (index * 60) for index in range(57)]
    for x in xs:
        cv2.line(image, (x, 0), (x, 3999), (0, 0, 0), 2)
    cv2.line(image, (0, external_y), (399, external_y), (0, 0, 0), 2)
    for y in table_ys:
        cv2.line(image, (0, y), (399, y), (0, 0, 0), 2)
    cv2.line(image, (120, partial_header_y), (205, partial_header_y), (0, 0, 0), 2)

    table_xs, table_ys, all_xs, all_ys = extract_template_axes_from_image(
        image,
        manifest_template_bbox=[10, 120, 390, 3600],
    )

    assert table_xs == xs
    assert all_xs == xs
    assert external_y in all_ys
    assert partial_header_y in all_ys
    assert external_y not in table_ys
    assert partial_header_y in table_ys
    assert table_ys[:3] == [120, partial_header_y, 240]
    assert len(table_ys) == 60


def test_extract_template_axes_blocks_incomplete_table_instead_of_using_external_line() -> None:
    image = np.full((900, 400, 3), 255, dtype=np.uint8)
    xs = [10, 100, 250, 390]
    external_y = 80
    table_ys = [120 + (index * 10) for index in range(59)]
    for x in xs:
        cv2.line(image, (x, 0), (x, 899), (0, 0, 0), 2)
    cv2.line(image, (0, external_y), (399, external_y), (0, 0, 0), 2)
    for y in table_ys:
        cv2.line(image, (0, y), (399, y), (0, 0, 0), 2)

    with pytest.raises(ValueError, match="template table y axes incomplete"):
        extract_template_axes_from_image(
            image,
            manifest_template_bbox=[10, 120, 390, 860],
        )


def test_canonical_template_axes_from_workbook_uses_semantic_row_edges(tmp_path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "2026-04"
    worksheet.print_area = "A1:H69"
    for row in range(1, 70):
        worksheet.row_dimensions[row].height = 10 + (row % 3)
    for col in range(1, 9):
        worksheet.column_dimensions[chr(64 + col)].width = 8 + col
    workbook_path = tmp_path / "template.xlsx"
    workbook.save(workbook_path)

    xs, ys = canonical_template_axes_from_workbook(
        workbook_path,
        sheet_name="2026-04",
        canvas_width=1200,
        canvas_height=2400,
    )

    assert len(xs) == 9
    assert len(ys) == 60
    assert ys == sorted(ys)
    assert all(ys[index] < ys[index + 1] for index in range(len(ys) - 1))


def test_manifest_explicit_template_axes_are_used_without_image_line_detection() -> None:
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    xs = [10, 30, 60, 90]
    ys = [10 + index for index in range(60)]

    resolved_xs, resolved_ys, all_xs, all_ys = resolve_template_axes_from_manifest_or_image(
        item={"template_axes_x": xs, "template_axes_y": ys},
        template_image=image,
        manifest_template_bbox=[0, 0, 99, 99],
    )

    assert resolved_xs == xs
    assert resolved_ys == ys
    assert all_xs == xs
    assert all_ys == ys


def test_rectify_fax_to_template_grid_uses_quad_and_template_outer_grid() -> None:
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 20), (80, 80), (0, 0, 0), 2)

    rectified = rectify_fax_to_template_grid(
        image,
        quad_px=[[20, 20], [80, 20], [80, 80], [20, 80]],
        table_bbox=[10, 30, 90, 70],
        canvas_width=120,
        canvas_height=110,
    )

    assert rectified.shape == (110, 120, 3)
    assert rectified[30:71, 10:91].mean() < 250
