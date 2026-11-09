"""Ruling slant is corrected before counting, without a requested row count."""
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from openpyxl import load_workbook

from src.services import hakodate_step_review_pipeline_service as subject


def make_grid(slope):
    image = np.full((700, 900, 3), 255, np.uint8)
    for x in (30, 110, 210, 490, 610, 730, 850):
        cv2.line(image, (x, 40), (x, 660), (0, 0, 0), 2)
    for y in range(70, 650, 60):
        cv2.line(image, (30, round(y+slope*(30-440))),
                 (850, round(y+slope*(850-440))), (0, 0, 0), 2)
    return image


@pytest.mark.parametrize("slope", [-0.025, 0.025, 0.0])
def test_observed_lines_are_level_before_detection_without_mutating_input(slope):
    raw = make_grid(slope)
    before = raw.copy()
    result, proof = subject.prepare_rectified_ruling_canvas(raw, table_bbox=[30, 40, 850, 660])
    assert proof["applied"]
    assert np.array_equal(raw, before)
    for y in range(130, 590, 60):
        measured = []
        for x in (60, 180, 360, 570, 800):
            roi = result[y-6:y+7, x-2:x+3].mean(axis=(1, 2))
            measured.append(int(np.argmin(roi)))
        assert max(measured)-min(measured) <= 2


def test_legacy_saved_y_coordinates_keep_the_exact_original_canvas():
    raw = make_grid(-0.025)
    result, proof = subject.prepare_rectified_ruling_canvas(raw,
        table_bbox=[30, 40, 850, 660], row_axis_override={"coordinate_space": {"mode": "template_canvas"}})
    assert result is raw
    assert proof["reason"] == "legacy_operator_canvas_preserved"


def test_blank_page_is_not_given_invented_rulings():
    raw = np.full((700, 900, 3), 255, np.uint8)
    result, proof = subject.prepare_rectified_ruling_canvas(raw, table_bbox=[30, 40, 850, 660])
    assert result is raw
    assert not proof["applied"]


def test_reported_shonan_input_has_one_boundary_per_physical_ruling():
    location = os.environ.get("SAWA_SHONAN_GEOMETRY_FIXTURE")
    if not location:
        pytest.skip("Private reported-order fixture is not distributed with source")
    root = Path(location)
    assert hashlib.sha256((root/'reported-ORD8b3b2ede.pdf').read_bytes()).hexdigest() == 'b3e734ab520dd949279208af03e9b7541aaa948075527f7fc88698fe2c6d21c9'
    raw = cv2.imread(str(root/'shonan-current-raw.png'))
    axes = json.loads((root/'shonan-current-lines.json').read_text())
    xs, ys = axes['xs'], axes['ys']
    result, proof = subject.prepare_rectified_ruling_canvas(raw, table_bbox=[xs[0],ys[0],xs[-1],ys[-1]])
    assert proof['applied']
    state = json.loads((root/'shonan-current-state.json').read_text())
    facility = state['metrics']['reparse_read_input']['configured_input']['facility']
    ws = load_workbook(root/'shonan-current.xlsx')['facility_template']
    _, _, evidence, _ = subject._align_axes(rectified_fax=result, template_xs=xs,
        template_ys=ys, worksheet=ws, fax_template=facility['fax_template'], diagnostic_only=True)
    row = evidence['row_intersection_y_match']
    (root/'shonan-slant-after-axis.json').write_text(json.dumps(evidence))
    cv2.imwrite(str(root/'shonan-slant-after.png'), result)
    assert row.get('used'), row.get('reason')
    source = row['structural_match']['canonical_day_source']
    assert source['observed_physical_row_count'] == 56
    assert source['observed_date_column_boundary_indexes'] == [0,8,16,24,32,40,48,56]
