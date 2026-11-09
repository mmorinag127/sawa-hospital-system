from copy import deepcopy

import numpy as np
import pytest

from src.services import ocr_row_recovery_service as recovery
from src.services import hakodate_fixed_quad_registration_service as registration


@pytest.mark.parametrize("error,action", [
    ("fax_date_column_physical_boundaries_changed", "review_row_day_boundaries"),
    ("fax_physical_body_count_changed", "review_row_day_boundaries"),
    ("fax_body_header_boundary_unresolved", "review_row_day_boundaries"),
    ("header_axis_unresolved:header_x_cluster_count_mismatch", "review_header_boundaries"),
    ("edge locked quad estimation failed: ['bottom_missing']", "review_or_edit_quad"),
    ("quad_estimation_failed", "review_or_edit_quad"),
])
def test_geometry_is_operator_review_not_success_or_terminal_rejection(error, action):
    details = recovery.failure_details(error)
    assert details["result_state"] == "review_required"
    assert details["recovery_action"] == action
    assert details["error_detail"] == error
    assert details["error_user_message"]


@pytest.mark.parametrize("error", ["provider_timeout", "template_version_mismatch", "selected_pdf_input_mismatch"])
def test_non_geometry_integrity_failures_are_not_manual_geometry(error):
    assert recovery.failure_details(error) is None


@pytest.mark.parametrize("evidence", [{}, {"fax_x_clusters": None}, {"fax_x_clusters": []}])
def test_missing_header_candidates_remain_empty(evidence):
    assert recovery.header_candidates(evidence) == []


def test_missing_last_date_boundary_stays_unconfirmed():
    days = [{"date": f"2026-11-{15 + i}", "expected_band_count": 8} for i in range(7)]
    match = {"canonical_day_source": {"canonical_physical_row_count": 56,
        "observed_physical_row_count": 56, "observed_date_column_boundary_indexes": [0, 8, 16, 24, 32, 40, 48]},
        "missing_date_column_boundary_indexes": [56]}
    before = deepcopy(match)
    assert recovery.observed_day_candidates(days, match) == [0, 8, 16, 24, 32, 40, 48, None]
    guide = recovery.review_guidance("fax_date_column_physical_boundaries_changed", axis="row", match=match)
    assert guide["missing_boundaries"] == [56]
    assert guide["expected_body_count"] == guide["observed_body_count"] == 56
    assert match == before
    match["canonical_day_source"]["observed_physical_row_count"] = 58
    assert recovery.observed_day_candidates(days, match) is None


def test_raw_review_canvas_never_reads_overlay_pixels(monkeypatch):
    from src.services import order_service
    raw = np.full((40, 30, 3), 255, dtype=np.uint8)
    overlay = np.zeros_like(raw)
    monkeypatch.setattr(registration.cv2, "imread", lambda path: overlay)
    monkeypatch.setattr(registration, "render_pdf_page_to_bgr", lambda *a, **kw: raw)
    calls = []
    def rectify(image, **kwargs):
        assert image is raw
        calls.append(kwargs)
        return image.copy()
    monkeypatch.setattr(registration, "rectify_fax_to_template_grid", rectify)
    result = order_service._geometry_review_canvas({"step2_png": "display-only", "fax_pdf": "original",
        "selected_page": 1, "quad_px": [[1, 1], [20, 1], [20, 30], [1, 30]],
        "template_axes_x": [1, 20], "template_axes_y": [1, 30]})
    assert np.array_equal(result, raw)
    assert calls[0]["canvas_width"] == 30 and calls[0]["canvas_height"] == 40


@pytest.mark.parametrize("rotation", [0, 180])
def test_reestimated_quad_retains_requested_rotation(monkeypatch, rotation):
    quad = [[1, 2], [100, 2], [100, 200], [1, 200]]
    monkeypatch.setattr(registration, "estimate_edge_locked_quad_from_pdf", lambda *a, **kw: {
        "status": "ok", "refined_quad_px": deepcopy(quad)})
    result, _, _ = registration.resolve_fixed_quad_px_for_manifest_item({"fax_pdf": "original",
        "quad_source": "edge_locked_v4_estimated_from_fax_pdf", "fax_rotation_degrees": rotation,
        "quad_px": quad[2:] + quad[:2] if rotation else quad}, render_width=1864)
    assert result == (quad[2:] + quad[:2] if rotation else quad)


def test_operator_quad_is_not_rotated_twice():
    quad = [[100, 200], [1, 200], [1, 2], [100, 2]]
    result, _, _ = registration.resolve_fixed_quad_px_for_manifest_item({"fax_pdf": "original",
        "quad_source": "operator_manual", "fax_rotation_degrees": 180, "quad_px": quad})
    assert result == quad


def test_header_frame_outside_rectification_target_is_still_observed():
    from src.services import hakodate_step_review_pipeline_service as pipeline
    image = np.full((500, 300, 3), 255, dtype=np.uint8)
    for x in [5, 60, 100, 160, 250]:
        registration.cv2.line(image, (x, 50), (x, 450), (0, 0, 0), 2)
    for y in [50, 90, 130, 210, 290, 370, 450]:
        registration.cv2.line(image, (5, y), (250, y), (0, 0, 0), 2)
    points, _ = pipeline._detect_header_intersections_for_axis_alignment(
        image, table_bbox=[13, 50, 250, 450], template_ys=[50, 90, 130, 450])
    assert any(point["x"] < 13 for point in points)


def test_equal_column_counts_preserve_observed_outer_ink():
    from src.services import hakodate_step_review_pipeline_service as pipeline
    observed = [5, 60, 100, 160, 250]
    corrected, proof = pipeline._structural_match_header_x_clusters(
        template_xs=[13, 65, 105, 165, 249], x_clusters=[{"value": x} for x in observed])
    assert corrected == observed
    assert proof["skipped_template_indexes"] == []
