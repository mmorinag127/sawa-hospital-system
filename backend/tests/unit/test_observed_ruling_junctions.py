"""Observed ink support, without row-count fitting or synthetic gap pixels."""
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.services import hakodate_step_review_pipeline_service as subject


def masks(*, scale=1, gap=3, arm=6, mirrored=False, connected=True):
    image = np.zeros((30 * scale, 100 * scale), np.uint8)
    vertical = image.copy()
    horizontal = image.copy()
    y = 15 * scale
    vertical[2 * scale:28 * scale, 10 * scale:12 * scale] = 255
    image[:] = vertical
    start = 12 * scale
    if connected:
        image[y:y + scale, start:start + arm * scale] = 255
    end = start + (arm + gap) * scale
    image[y:y + scale, end:90 * scale] = 255
    horizontal[y:y + scale, end:90 * scale] = 255
    if mirrored:
        image, horizontal, vertical = [cv2.flip(item, 1) for item in (image, horizontal, vertical)]
    return image, horizontal, vertical, y


@pytest.mark.parametrize('scale', [1, 2, 3])
@pytest.mark.parametrize('mirrored', [False, True])
@pytest.mark.parametrize('gap', [0, 3])
def test_attached_observed_arm_survives_opening_without_painting_gaps(scale, mirrored, gap):
    ink, horizontal, vertical, y = masks(scale=scale, gap=gap, mirrored=mirrored)
    before = ink.copy()
    restored = subject._restore_supported_ruling_junctions(
        ink, horizontal, vertical, opening_width=42 * scale)
    assert np.any((restored[y] > 0) & (vertical[y] > 0))
    assert not np.any((restored > 0) & (ink == 0))
    assert np.array_equal(ink, before)


@pytest.mark.parametrize('mirrored', [False, True])
@pytest.mark.parametrize('unsupported', ['absent_arm', 'gap_larger_than_support', 'different_row'])
def test_no_observed_connection_is_not_promoted_to_a_junction(mirrored, unsupported):
    ink, horizontal, vertical, y = masks(
        gap=12 if unsupported == 'gap_larger_than_support' else 3,
        connected=unsupported != 'absent_arm', mirrored=mirrored)
    if unsupported == 'different_row':
        # Keep the attached arm, but put the surviving long line elsewhere.
        ink[horizontal > 0] = 0
        horizontal = np.roll(horizontal, 4, axis=0)
        ink |= horizontal
    restored = subject._restore_supported_ruling_junctions(ink, horizontal, vertical, opening_width=42)
    assert not np.any((restored[y] > 0) & (vertical[y] > 0))
    assert not np.any((restored > 0) & (ink == 0))


def test_reported_original_automatic_geometry_without_operator_override(tmp_path):
    location = os.environ.get('SAWA_PIA_GEOMETRY_FIXTURE')
    if not location:
        pytest.skip('Private source PDF and captured facility/week are not distributed with source')
    from openpyxl import load_workbook
    from src.services import master_order_form_template_service as master
    from src.services import workbook_pdf_renderer as renderer
    from src.services import hakodate_fixed_quad_registration_service as registration
    from src.services.order_service import _estimate_hakodate_template_bbox_from_rendered_image

    root = Path(location)
    pdf = root / 'prod-unprocessed-FAC00015-20261115.pdf'
    digest = '31cc44a4adce84cf1b16865ca8e1fdb601914e4b2e071fca1cddd78bed313257'
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
    captured = json.loads((root / 'pia-failed-metrics.json').read_text())['reparse_read_input']
    diagnostic = json.loads((root / 'pia-row-diagnostic.json').read_text())
    assert captured['configured_input']['facility']['facility_id'] == 'FAC00015'
    assert captured['order']['week_code'] == '2026-11@2026-11-15~2026-11-21'
    width, height = diagnostic['canvas_size']
    workbook = tmp_path / 'original-facility-week.xlsx'
    template_pdf = tmp_path / 'original-facility-week.pdf'
    master.build_facility_template_xlsx(
        facility_config=captured['configured_input']['facility'], output_path=workbook,
        week_value=captured['order']['week_code'], week_menu_entries=captured['physical_menu_entries'])
    renderer.render_workbook_path_to_pdf(workbook, output_path=template_pdf,
        sheet_name=master.FACILITY_TEMPLATE_SHEET_NAME)
    rendered = registration.render_pdf_page_to_bgr(str(template_pdf), width=width)
    bbox = _estimate_hakodate_template_bbox_from_rendered_image(rendered)
    xs, ys = registration.registration_template_axes_from_workbook(workbook,
        sheet_name=master.FACILITY_TEMPLATE_SHEET_NAME, canvas_width=width, canvas_height=height, table_bbox=bbox)
    quad = registration.estimate_edge_locked_quad_from_pdf(str(pdf), dpi=220, render_width=1864, page=1)
    assert quad['status'] == 'ok'
    raw = registration.rectify_fax_to_template_grid(
        registration.render_pdf_page_to_bgr(str(pdf), width=1864, page=1), quad_px=quad['refined_quad_px'],
        table_bbox=[xs[0], ys[0], xs[-1], ys[-1]], canvas_width=width, canvas_height=height)
    loaded = load_workbook(workbook)
    try:
        _, _, evidence, _ = subject._align_axes(rectified_fax=raw, template_xs=xs, template_ys=ys,
            worksheet=loaded[master.FACILITY_TEMPLATE_SHEET_NAME],
            fax_template=captured['configured_input']['facility']['fax_template'], diagnostic_only=True)
    finally:
        loaded.close()
    result = evidence['row_intersection_y_match']
    assert result['used'] is True, result.get('reason')
    assert result.get('method') != 'operator_row_axis_override'
    assert result.get('missing_date_column_boundary_indexes', []) == []
    assert result['structural_match']['body_day_boundary_indexes'] == [0, 8, 16, 24, 32, 40, 48, 56]
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
