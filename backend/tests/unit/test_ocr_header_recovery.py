import numpy as np
import pytest
from PIL import Image

from src.services import hakodate_step_review_pipeline_service as pipeline
from src.services import ocr_row_recovery_service as recovery


@pytest.fixture
def axis_environment(monkeypatch):
    monkeypatch.setattr(pipeline, "_nearest_line_positions_in_rectified", lambda *a: (np.array([[0, 100]]), np.array([[0], [100]]), {}))
    monkeypatch.setattr(pipeline, "_detect_vertical_candidates", lambda *a: [0, 100])
    monkeypatch.setattr(pipeline, "_gap_order_match", lambda *a: ([0, 100], {"used": True}))
    monkeypatch.setattr(pipeline, "_post_menu_boundary_preserving_xs", lambda **kw: pytest.fail("unresolved header must not be post-adjusted"))
    monkeypatch.setattr(pipeline, "_row_intersection_correct_ys", lambda **kw: pytest.fail("unresolved header must not enter row recovery"))
    monkeypatch.setattr(pipeline, "_draw_axis_match", lambda *a, **kw: Image.new("RGB", (100, 100)))
    return dict(rectified_fax=np.zeros((100, 100, 3), dtype=np.uint8), template_xs=[0, 25, 50, 75, 100],
                template_ys=[0, 50, 100], worksheet=object())


def test_unresolved_header_blocks_normal_ocr_without_median_or_template_fallback(axis_environment, monkeypatch):
    monkeypatch.setattr(pipeline, "_header_intersection_correct_xs", lambda **kw: (None, {
        "reason": "header_x_cluster_count_mismatch",
        "fax_x_clusters": [{"value": 4.0}, {"value": 52.0}],
    }))

    with pytest.raises(ValueError, match="header_axis_unresolved:header_x_cluster_count_mismatch"):
        pipeline._align_axes(**axis_environment)


def test_unresolved_header_diagnostic_returns_only_detected_candidates(axis_environment, monkeypatch):
    monkeypatch.setattr(pipeline, "_header_intersection_correct_xs", lambda **kw: (None, {
        "reason": "header_y_cluster_count_mismatch",
        "fax_x_clusters": [{"value": 4.25}, {"value": 52.5}, {"value": float("nan")}],
    }))

    xs, ys, evidence, _image = pipeline._align_axes(**axis_environment, diagnostic_only=True)

    assert xs == [4.25, 52.5]
    assert ys == []
    assert evidence["header_intersection_x_match"]["reason"] == "header_y_cluster_count_mismatch"
    assert evidence["post_menu_x_match"]["reason"] == "header_axis_unresolved"


def test_header_failure_routes_to_existing_header_review_action():
    result = recovery.failure_details("header_axis_unresolved:header_x_cluster_count_mismatch")

    assert result == {
        "error": "header_axis_unresolved",
        "error_detail": "header_axis_unresolved:header_x_cluster_count_mismatch",
        "processing_stage": "header_boundaries",
        "recovery_action": "review_header_boundaries",
        "error_user_message": "ヘッダー境界の補正を確認してください。",
    }


@pytest.mark.parametrize("space", [None, [], "invalid", {"mode": "template_canvas", "width": 100, "height": 101}])
def test_invalid_saved_header_stops_ocr_but_keeps_diagnostic_image(axis_environment, space):
    override = {"corrected_xs": [0, 25, 50, 75, 100], "coordinate_space": space}
    with pytest.raises(ValueError, match="header_axis_override_invalid"):
        pipeline._align_axes(**axis_environment, header_axis_override=override)
    xs, ys, evidence, image = pipeline._align_axes(
        **axis_environment, header_axis_override=override, diagnostic_only=True,
    )
    assert xs == ys == []
    assert image.size == (100, 100)
    assert evidence["header_intersection_x_match"]["used"] is False
    assert evidence["header_intersection_x_match"]["reason"] == "header_axis_override_invalid"
