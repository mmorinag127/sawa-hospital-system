"""Observed-ruling geometry only; no OCR inference or business-data mutation."""
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.services import hakodate_step_review_pipeline_service as subject


def join(image, points, xs):
    raw = subject._cluster_header_axis_values(points, "y", tolerance_px=18)
    return subject._join_observed_row_ruling_fragments(image, points, raw, xs)


def point(x, y):
    return {"x": x, "y": y, "bbox": [x - 3, y - 3, 7, 7]}


def test_actual_detector_joins_sloping_rulings_without_dropping_intersections():
    image = np.full((400, 430, 3), 255, np.uint8)
    xs = [15, 45, 75, 110, 290, 330, 370, 410]
    for x in xs:
        cv2.line(image, (x, 15), (x, 380), (0, 0, 0), 2)
    for y in (40, 100, 160, 220, 280, 340):
        cv2.polylines(image, [np.array([[15, y+26], [110, y+26], [290, y], [410, y]])],
                      False, (0, 0, 0), 2)
    points, _ = subject._detect_table_intersections_for_row_axis(
        image, corrected_xs=xs, template_ys=[40, 366],
    )
    raw = subject._cluster_header_axis_values(points, "y", tolerance_px=18)
    assert len(raw) > 6
    merged, proof = join(image, points, xs)
    assert len(merged) == 6
    assert proof["joined_clusters"]
    assert sorted(i for c in merged for i in c["point_indexes"]) == list(range(len(points)))


@pytest.mark.parametrize("connected", [False, True])
def test_only_connected_ink_between_disjoint_columns_can_join(connected):
    image = np.full((180, 320, 3), 255, np.uint8)
    xs = [10, 40, 70, 250, 280, 310]
    points = [point(x, 120 if i < 3 else 94) for i, x in enumerate(xs)]
    cv2.line(image, (10,120), (70,120), (0,0,0), 2)
    cv2.line(image, (250,94), (310,94), (0,0,0), 2)
    if connected:
        cv2.line(image, (70,120), (250,94), (0,0,0), 2)
    merged, evidence = join(image, points, xs)
    assert len(merged) == (1 if connected else 2)
    assert bool(evidence["joined_clusters"]) == connected


def test_two_real_close_rows_and_diagonal_cancellation_marks_remain_distinct():
    image = np.full((180, 320, 3), 255, np.uint8)
    xs = [10,40,70,250,280,310]
    points = [point(x,y) for y in (94,120) for x in xs]
    for y in (94,120):
        cv2.line(image,(10,y),(310,y),(0,0,0),2)
    cv2.line(image,(70,94),(250,120),(0,0,0),2)
    merged, evidence = join(image,points,xs)
    assert len(merged) == 2
    assert evidence["joined_clusters"] == []


def test_ambiguous_connected_alternatives_are_not_fitted_to_one_row():
    image = np.full((180,320,3),255,np.uint8)
    xs = [10,40,250,280]
    points = [point(10,80),point(40,80),point(250,105),point(280,105),
              point(10,130),point(40,130)]
    for y in (80,130):
        cv2.line(image,(10,y),(40,y),(0,0,0),2)
        cv2.line(image,(40,y),(250,105),(0,0,0),2)
    cv2.line(image,(250,105),(280,105),(0,0,0),2)
    merged, evidence = join(image,points,xs)
    assert len(merged) == 3
    assert evidence["joined_clusters"] == []


def test_reported_pdf_geometry_when_private_fixture_is_available():
    location = os.environ.get("SAWA_REPORTED_RULING_FIXTURE")
    if not location:
        pytest.skip("Private reported-order fixture is not distributed with source")
    import fitz
    from src.services import hakodate_fixed_quad_registration_service as registration
    root = Path(location)
    pdf = (root / "original.pdf").read_bytes()
    expected = "39a93ea01241d73ebb6b344b8bb4a84b678b9b91cf46b4ead5f63b0086290f2b"
    assert hashlib.sha256(pdf).hexdigest() == expected
    diag = json.loads((root / "row-diagnostics.json").read_text())
    quad = json.loads((root / "quad.json").read_text())
    with fitz.open(stream=pdf,filetype="pdf") as doc:
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(1864/page.rect.width,1864/page.rect.width),alpha=False)
        original = cv2.cvtColor(np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3),cv2.COLOR_RGB2BGR)
    image = registration.rectify_fax_to_template_grid(original,
        quad_px=quad["suggested_quad_px"],table_bbox=[13,447,2347,4182],canvas_width=2362,canvas_height=4273)
    points, _ = subject._detect_table_intersections_for_row_axis(image,
        corrected_xs=diag["x_levels"],template_ys=[447,4182])
    raw = subject._cluster_header_axis_values(points,"y",tolerance_px=18)
    assert len(raw) == 61
    merged, evidence = join(image,points,diag["x_levels"])
    observed = subject._filter_structural_y_clusters(subject._enrich_y_clusters_with_column_votes(
        points=points,y_clusters=merged,corrected_xs=diag["x_levels"]))
    assert len(observed)-3 == diag["axis_evidence"]["canonical_day_source"]["canonical_physical_row_count"] == 56
    assert len(evidence["joined_clusters"]) == 2
    assert sorted(i for c in merged for i in c["point_indexes"]) == list(range(len(points)))
    assert hashlib.sha256((root / "original.pdf").read_bytes()).hexdigest() == expected
