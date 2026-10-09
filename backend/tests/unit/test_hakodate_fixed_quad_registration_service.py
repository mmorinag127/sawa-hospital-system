import cv2
import numpy as np
import pytest
from copy import deepcopy
from shutil import copyfile

from test_modernization_dynamic_form_layout import facility, entries_for, WEEK
from src.services import master_order_form_template_service as master

from src.services.hakodate_fixed_quad_registration_service import (
    canonical_template_axes_from_workbook,
    extract_template_axes_from_image,
    resolve_template_axes_from_manifest_or_image,
    rectify_fax_to_template_grid,
)


@pytest.fixture
def generated_source(tmp_path):
    original = master.MASTER_TEMPLATE_PATH.read_bytes()
    copied = tmp_path / "master-copy.xlsx"
    copyfile(master.MASTER_TEMPLATE_PATH, copied)
    config = deepcopy(facility())
    config["fax_template"]["columns"] = config["fax_template"]["columns"][:3] + [
        {"index": 3 + index, "role": "quantity", "name": f"qty.{diet}_x",
         "header": diet, "diet_type": diet, "area_id": "X"}
        for index, diet in enumerate(("regular", "no_meat", "no_fish", "staff"))
    ]
    rows = entries_for(range(7), (2, 3, 3))
    rows.append({**rows[-1], "name": "last-57", "sort_order": 4})
    output = master.build_facility_template_xlsx(
        facility_config=config, master_template_path=copied,
        output_path=tmp_path / "generated.xlsx", week_value=WEEK, week_menu_entries=rows,
    )
    yield output
    assert master.MASTER_TEMPLATE_PATH.read_bytes() == original
    assert copied.read_bytes() == original


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
        expected_x_edge_count=4,
        expected_y_edge_count=60,
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
            expected_x_edge_count=4,
            expected_y_edge_count=60,
        )


def test_canonical_template_axes_from_workbook_uses_semantic_row_edges(tmp_path, generated_source) -> None:
    workbook_path = generated_source

    xs, ys = canonical_template_axes_from_workbook(
        workbook_path,
        sheet_name=master.FACILITY_TEMPLATE_SHEET_NAME,
        canvas_width=1200,
        canvas_height=2400,
    )

    assert len(xs) == 9
    assert len(ys) == 60
    assert ys == sorted(ys)
    assert all(ys[index] < ys[index + 1] for index in range(len(ys) - 1))


def test_manifest_explicit_template_axes_are_used_without_image_line_detection(generated_source) -> None:
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    xs = [10 + index * 10 for index in range(9)]
    ys = [10 + index for index in range(60)]

    resolved_xs, resolved_ys, all_xs, all_ys = resolve_template_axes_from_manifest_or_image(
        item={"template_axes_x": xs, "template_axes_y": ys,
              "template_xlsx": generated_source, "template_sheet_name": master.FACILITY_TEMPLATE_SHEET_NAME},
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
