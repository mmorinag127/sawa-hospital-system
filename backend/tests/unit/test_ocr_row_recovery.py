from copy import deepcopy
from datetime import date, timedelta

import numpy as np
import pytest
from PIL import Image

from src.services import ocr_row_recovery_service as recovery
from src.services import hakodate_step_review_pipeline_service as pipeline

WEEK = "2026-10@2026-10-25~2026-10-31"

def plan(first=0, last=6):
    return [{"date": (date(2026, 10, 25) + timedelta(days=i)).isoformat()}
            for i in range(first, last + 1) for _ in range(8)]


def authored():
    return {"corrected_ys": [float(i * 10) + 0.123456 for i in range(60)],
            "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 600},
            "body_day_boundary_indexes": list(range(0, 57, 8)),
            "day_labels": [day["date"] for day in recovery.calendar_contract(plan(), week_value=WEEK)]}


def match(value, rows=None):
    return recovery.manual_match(value, physical_plan=plan() if rows is None else rows,
                                 week_value=WEEK,
                                 header_bands=3, canvas_height=600, canvas_width=100)


def test_manual_coordinates_and_day_mapping_are_exact():
    value = authored()
    before = deepcopy(value)
    ys, evidence = match(value)
    assert ys == value["corrected_ys"]
    assert evidence["structural_match"]["body_day_boundary_indexes"] == [0, 8, 16, 24, 32, 40, 48, 56]
    assert value == before


def test_partial_week_keeps_blank_physical_bands():
    value = authored()
    ys, evidence = match(value, plan(2, 4))
    assert len(ys) == 60
    assert evidence["structural_match"]["detected_body_band_count"] == 56
    assert recovery.calendar_contract(plan(2, 4), week_value=WEEK)[0]["expected_band_count"] is None


@pytest.mark.parametrize("period,dates,fax_dates", [
    ("2026-10@2026-10-01~2026-10-03", ["2026-10-01", "2026-10-03"], ["2026-09-27", "2026-10-03"]),
    ("2026-10@2026-10-28~2026-10-30", ["2026-10-28", "2026-10-30"], ["2026-10-25", "2026-10-31"]),
])
def test_full_week_fax_calendar_keeps_target_month_menu_rows_separate(period, dates, fax_dates):
    rows = [{"date": day} for day in dates]
    before = deepcopy(rows)
    days = recovery.calendar_contract(rows, week_value=period)
    assert days[0]["date"] == fax_dates[0] and days[-1]["date"] == fax_dates[-1]
    count = 7
    assert len(days) == count
    assert {day["date"]: day["expected_band_count"] for day in days if day["expected_band_count"] is not None} == dict.fromkeys(dates, 1)
    assert rows == before
    body_count = count
    value = {"corrected_ys": list(range(body_count + 2)),
        "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 100},
        "body_day_boundary_indexes": list(range(body_count + 1)),
        "day_labels": [day["date"] for day in days]}
    ys, evidence = recovery.manual_match(value, physical_plan=rows, week_value=period,
        header_bands=1, canvas_height=100, canvas_width=100)
    assert ys == value["corrected_ys"]
    assert evidence["structural_match"]["day_labels"] == value["day_labels"]


@pytest.mark.parametrize("period", [None, "2026-10", "2026-10@2026-10-27~2026-10-31"])
def test_calendar_cannot_guess_a_period_or_accept_rows_outside_it(period):
    with pytest.raises(ValueError, match="fax_calendar_week_unresolved"):
        recovery.calendar_contract(plan(), week_value=period)


def test_target_period_cannot_span_two_full_week_faxes():
    with pytest.raises(ValueError, match="fax_calendar_week_unresolved"):
        recovery.calendar_contract([{"date": "2026-10-26"}, {"date": "2026-11-01"}],
            week_value="2026-10@2026-10-26~2026-11-01")


@pytest.mark.parametrize("boundaries", [None, [0, 8, 16, 32, 40, 48, 56], [0, 8, 16, 24, 32, 40, 48, 55],
                                      [0, 8, 16, 16, 32, 40, 48, 56], [0, 8, 16, 25, 32, 40, 48, 56]])
def test_invalid_day_mapping_blocks_without_mutation(boundaries):
    value = authored()
    value["body_day_boundary_indexes"] = boundaries
    before = deepcopy(value)
    with pytest.raises(ValueError, match="row_axis_day_"):
        match(value)
    assert value == before


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 601, True])
def test_invalid_coordinates_block(bad):
    value = authored()
    value["corrected_ys"][5] = bad
    with pytest.raises(ValueError, match="row_axis_ys"):
        match(value)


def test_no_fitting_to_template_count():
    value = authored()
    assert pipeline.normalize_row_axis_override(value, expected_count=10, canvas_height=600) == value["corrected_ys"]


def test_different_calendar_is_rejected():
    value = authored()
    value["day_labels"][3] = "2026-11-04"
    with pytest.raises(ValueError, match="row_axis_calendar_days_required"):
        match(value)


@pytest.fixture
def axis_environment(monkeypatch):
    monkeypatch.setattr(pipeline, "_nearest_line_positions_in_rectified", lambda *a: (np.array([[0, 100]]), np.array([[0], [600]]), {}))
    monkeypatch.setattr(pipeline, "_detect_vertical_candidates", lambda *a: [0, 100])
    monkeypatch.setattr(pipeline, "_gap_order_match", lambda *a: ([0, 100], {"used": True}))
    monkeypatch.setattr(pipeline, "_header_intersection_correct_xs", lambda **kw: ([0, 100], {}))
    monkeypatch.setattr(pipeline, "_post_menu_boundary_preserving_xs", lambda **kw: (kw["matched_xs"], {}))
    monkeypatch.setattr(pipeline, "_draw_axis_match", lambda *a, **kw: Image.new("RGB", (100, 600)))
    monkeypatch.setattr(pipeline.master_order_form_template_service, "validated_body_row_plan", lambda ws: plan())
    monkeypatch.setattr(pipeline.master_order_form_template_service, "validated_body_week_value", lambda ws: WEEK)
    monkeypatch.setattr(pipeline, "STEP_REVIEW_HEADER_BANDS", 3)
    return dict(rectified_fax=np.zeros((600, 100, 3), dtype=np.uint8), template_xs=[0, 100],
                template_ys=[0, 600], worksheet=object())


def test_manual_path_never_invokes_failed_estimation(axis_environment, monkeypatch):
    def fail(**kw):
        pytest.fail("manual input must precede automatic estimation")
    monkeypatch.setattr(pipeline, "_row_intersection_correct_ys", fail)
    _, ys, evidence, _ = pipeline._align_axes(**axis_environment, row_axis_override=authored())
    assert ys == authored()["corrected_ys"]
    assert evidence["row_intersection_y_match"]["structural_match"]["body_day_boundary_indexes"][3] == 24


def test_auto_failure_returns_diagnostic_not_success(axis_environment, monkeypatch):
    monkeypatch.setattr(pipeline, "_row_intersection_correct_ys", lambda **kw: (None, {"reason": "fax_date_column_physical_boundaries_changed"}))
    with pytest.raises(ValueError, match="fax_date_column_physical_boundaries_changed"):
        pipeline._align_axes(**axis_environment)
    _, ys, evidence, image = pipeline._align_axes(**axis_environment, diagnostic_only=True)
    assert ys == []
    assert image.size == (100, 600)
    assert evidence["row_intersection_y_match"]["used"] is False
    assert evidence["row_intersection_y_match"]["diagnostic_only"] is True


def test_old_saved_coordinates_remain_editable(axis_environment):
    value = authored()
    del value["day_labels"]
    del value["body_day_boundary_indexes"]
    with pytest.raises(ValueError, match="row_axis_calendar_days_required"):
        pipeline._align_axes(**axis_environment, row_axis_override=value)
    _, ys, evidence, _ = pipeline._align_axes(**axis_environment, row_axis_override=value, diagnostic_only=True)
    assert ys == value["corrected_ys"]
    assert evidence["row_intersection_y_match"]["used"] is False


def test_failed_day_estimate_keeps_observed_lines_as_diagnostics(axis_environment, monkeypatch):
    monkeypatch.setattr(pipeline, "_row_intersection_correct_ys", lambda **kw: (None, {
        "reason": "fax_date_column_physical_boundaries_changed", "corrected_ys": [0, 10, 20],
        "canonical_day_source": {"observed_date_column_boundary_indexes": [0, 8, 16, 32, 40, 48, 56]}}))
    _, ys, evidence, _ = pipeline._align_axes(**axis_environment, diagnostic_only=True)
    assert ys == [0, 10, 20]
    assert evidence["row_intersection_y_match"]["used"] is False
    with pytest.raises(ValueError, match="fax_date_column_physical_boundaries_changed"):
        pipeline._align_axes(**axis_environment)


def test_manual_header_is_never_reestimated_or_post_adjusted(axis_environment, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("automatic x adjustment after authored header")
    for name in ("_post_menu_boundary_preserving_xs", "_header_intersection_correct_xs", "_nearest_line_positions_in_rectified"):
        monkeypatch.setattr(pipeline, name, fail)
    header = {"corrected_xs": [0.123456, 99.654321], "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 600}}
    xs, _, evidence, _ = pipeline._align_axes(**axis_environment, header_axis_override=header, row_axis_override=authored())
    assert xs == header["corrected_xs"]
    assert evidence["header_intersection_x_match"]["corrected_xs"] == xs


@pytest.mark.parametrize("xs", [[0], [10, 0], [0, float("nan")], [0, 101]])
def test_invalid_manual_header_blocks_before_detection(axis_environment, monkeypatch, xs):
    monkeypatch.setattr(pipeline, "_nearest_line_positions_in_rectified", lambda *a: pytest.fail("invalid manual header fell back"))
    with pytest.raises(ValueError, match="header_axis_override_invalid"):
        pipeline._align_axes(**axis_environment, header_axis_override={"corrected_xs": xs,
            "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 600}})


@pytest.mark.parametrize("message,stage", [("fax_date_column_physical_boundaries_changed", "row_day_boundaries"),
    ("row_axis_day_band_count_mismatch:2026-10-28:expected=8:actual=9", "row_day_boundaries"),
    ("header_axis_override_invalid", "header_boundaries"), ("provider_timeout", None), ("template_missing", None)])
def test_error_action_only_routes_known_geometry_failures(message, stage):
    result = recovery.failure_details(message)
    if stage is None:
        assert result is None
    else:
        assert result["processing_stage"] == stage
        assert result["error_detail"] == message


@pytest.mark.parametrize("gap,arm,continuation,restored", [(3, True, True, True), (4, True, True, True),
    (8, True, True, False), (3, False, True, False), (3, True, False, False)])
def test_supported_short_ruling_not_threshold_only(gap, arm, continuation, restored):
    cv2 = pipeline.cv2
    ink = np.zeros((200, 2362), dtype=np.uint8)
    ink[10:190, 13:18] = 255
    if arm:
        ink[98:103, 18:40] = 255
    if continuation:
        ink[98:103, 40 + gap:150] = 255
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (42, 1)))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 18)))
    result = pipeline._restore_supported_ruling_junctions(ink, horizontal, vertical, opening_width=42)
    assert bool(result[100, 20]) is restored
    assert np.all(result[(ink == 0) & (horizontal == 0)] == 0)


@pytest.mark.parametrize("mirror", [False, True])
def test_short_arm_with_multiple_supported_interruptions(mirror):
    ink = np.zeros((200, 2362), dtype=np.uint8)
    ink[10:190, 11:14] = 255
    ink[98:103, 14:38] = 255
    ink[98:103, 20] = 0
    ink[98:103, 41:150] = 255
    if mirror:
        ink = np.fliplr(ink).copy()
    cv2 = pipeline.cv2
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (42, 1)))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 18)))
    result = pipeline._restore_supported_ruling_junctions(ink, horizontal, vertical, opening_width=42)
    assert result[100, ink.shape[1] - 16 if mirror else 15] > 0
    assert np.all(result[(ink == 0) & (horizontal == 0)] == 0)
