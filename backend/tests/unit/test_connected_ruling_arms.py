"""Connected source ink survives line extraction without synthetic junctions."""
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.services import hakodate_step_review_pipeline_service as subject


def stepped_masks(*, scale, mirrored, step_direction, disconnected=False):
    ink = np.zeros((60 * scale, 160 * scale), np.uint8)
    vertical = np.zeros_like(ink)
    horizontal = np.zeros_like(ink)
    vertical[5 * scale:55 * scale, 15 * scale:18 * scale] = 255
    ink[:] = vertical
    y = 25 * scale
    arm_y = y + step_direction * 3 * scale
    ink[arm_y:arm_y + 3 * scale, 15 * scale:39 * scale] = 255
    ink[min(y, arm_y):max(y, arm_y) + 3 * scale, 35 * scale:39 * scale] = 255
    horizontal[y:y + 3 * scale, 35 * scale:145 * scale] = 255
    ink |= horizontal
    if disconnected:
        ink[:, 29 * scale:33 * scale] = 0
    if mirrored:
        ink, horizontal, vertical = [cv2.flip(mask, 1) for mask in (ink, horizontal, vertical)]
    return ink, horizontal, vertical, arm_y


@pytest.mark.parametrize("scale", [1, 2, 3])
@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("step_direction", [-1, 1])
def test_connected_stepped_arms_are_traced_without_changing_source(scale, mirrored, step_direction):
    ink, horizontal, vertical, arm_y = stepped_masks(
        scale=scale, mirrored=mirrored, step_direction=step_direction)
    saved = [mask.copy() for mask in (ink, horizontal, vertical)]
    restored = subject._restore_connected_ruling_arms(
        ink, horizontal, vertical, opening_width=42 * scale)
    assert np.any((restored[arm_y:arm_y + 3 * scale] > 0) & (vertical[arm_y:arm_y + 3 * scale] > 0))
    assert not np.any((restored > 0) & (ink == 0))
    for actual, before in zip((ink, horizontal, vertical), saved, strict=True):
        assert np.array_equal(actual, before)


@pytest.mark.parametrize("scale", [1, 2, 3])
@pytest.mark.parametrize("mirrored", [False, True])
def test_tracing_does_not_cross_real_white_gap(scale, mirrored):
    ink, horizontal, vertical, arm_y = stepped_masks(
        scale=scale, mirrored=mirrored, step_direction=1, disconnected=True)
    restored = subject._restore_connected_ruling_arms(
        ink, horizontal, vertical, opening_width=42 * scale)
    assert not np.any((restored[arm_y:arm_y + 3 * scale] > 0) & (vertical[arm_y:arm_y + 3 * scale] > 0))
    assert not np.any((restored > 0) & (ink == 0))


@pytest.mark.parametrize("mirrored", [False, True])
def test_nearby_separate_row_is_not_followed_along_vertical_axis(mirrored):
    ink, horizontal, vertical, _ = stepped_masks(scale=1, mirrored=mirrored, step_direction=1)
    horizontal = np.roll(horizontal, 16, axis=0)
    ink |= horizontal
    restored = subject._restore_connected_ruling_arms(ink, horizontal, vertical, opening_width=42)
    assert not np.any((restored > 0) & (vertical > 0))


def test_trace_stops_at_first_vertical_without_searching_for_a_successful_farther_axis():
    ink, horizontal, vertical, _ = stepped_masks(scale=1, mirrored=False, step_direction=1)
    # This nearer vertical has no connection to the seed. The actual connected
    # vertical further away must not be chosen to manufacture a successful fit.
    vertical[18:23, 28:30] = 255
    ink |= vertical
    restored = subject._restore_connected_ruling_arms(ink, horizontal, vertical, opening_width=42)
    assert not np.any(restored[:, 15:18])


def test_reported_original_body_mapping_with_connected_source_junctions():
    location = os.environ.get("SAWA_STEPPED_JUNCTION_FIXTURE")
    if not location:
        pytest.skip("Private immutable original and captured facility/week required")
    from openpyxl import load_workbook
    from src.services import hakodate_fixed_quad_registration_service as registration
    from src.services import master_order_form_template_service as master

    root = Path(location)
    pdf = root / "ORDd8b83c80-original.pdf"
    digest = "1f5b46cb67a60a13d5e4c74df65415bb5190977f3bb81882460923bf5531de07"
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
    captured = json.loads((root / "ORDd8b83c80-readonly.json").read_text())
    payload = captured["jobs"][0]["metrics"]["nonworkflow_execution"]
    source = payload["orders"]["ORDd8b83c80"]
    assert source["order"]["facility_code"] == "FAC00015"
    assert source["order"]["week_code"] == "2026-11@2026-11-22~2026-11-28"
    folder = root / "ORDd8b83c80-geometry"
    reg = json.loads((folder / "registration/summary.json").read_text())
    raw = registration.rectify_fax_to_template_grid(
        registration.render_pdf_page_to_bgr(str(pdf), width=1864), quad_px=reg["quad_px"],
        table_bbox=reg["template_outer_grid_bbox_used"], canvas_width=2362, canvas_height=4273)
    image, _ = subject.prepare_rectified_ruling_canvas(raw, table_bbox=reg["template_outer_grid_bbox_used"])
    workbook = load_workbook(folder / "template.xlsx")
    try:
        _, _, axis, _ = subject._align_axes(rectified_fax=image,
            template_xs=reg["template_axes_x"], template_ys=reg["template_axes_y_all"],
            worksheet=workbook[master.FACILITY_TEMPLATE_SHEET_NAME],
            fax_template=source["configured_input"]["facility"]["fax_template"], diagnostic_only=True)
    finally:
        workbook.close()
    rows = axis["row_intersection_y_match"]
    assert rows["used"] is True, rows.get("reason")
    assert rows.get("method") != "operator_row_axis_override"
    assert rows["structural_match"]["body_day_boundary_indexes"] == list(range(0, 57, 8))
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
