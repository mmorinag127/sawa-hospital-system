import numpy as np
import cv2

from src.services.hakodate_fixed_quad_registration_service import (
    extract_template_axes_from_image,
    rectify_fax_to_template_grid,
)


def test_extract_template_axes_keeps_template_lines_and_filters_header() -> None:
    image = np.full((300, 400, 3), 255, dtype=np.uint8)
    xs = [10, 100, 250, 390]
    ys_all = [20, 80, 140, 200, 260]
    for x in xs:
        cv2.line(image, (x, 0), (x, 299), (0, 0, 0), 2)
    for y in ys_all:
        cv2.line(image, (0, y), (399, y), (0, 0, 0), 2)

    table_xs, table_ys, all_xs, all_ys = extract_template_axes_from_image(
        image,
        manifest_template_bbox=[10, 100, 390, 260],
    )

    assert table_xs == xs
    assert all_xs == xs
    assert all_ys == ys_all
    assert table_ys == [140, 200, 260]


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
