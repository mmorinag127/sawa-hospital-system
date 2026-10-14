"""Operator geometry is input, never a candidate to fit to an estimate."""
from collections import Counter
from datetime import date, timedelta
import math

from src.services import sheet_week_service


def failure_details(message):
    """Only known geometry failures authorize a geometry correction action."""
    code = message.split(":", 1)[0]
    row_codes = {"fax_day_boundaries_disagree_with_monthly_menu", "fax_date_column_physical_boundaries_changed",
                 "fax_physical_body_count_changed", "fax_body_header_boundary_unresolved",
                 "fax_header_sequence_ambiguous", "fax_calendar_day_boundaries_ambiguous",
                 "fax_row_boundaries_unresolved", "fax_row_boundaries_shorter_than_menu"}
    if code.startswith("row_axis_") or code in row_codes:
        return {"error": code, "error_detail": message, "processing_stage": "row_day_boundaries",
                "recovery_action": "review_row_day_boundaries",
                "error_user_message": "行・日境界で停止しました。原画像と停止理由を確認して補正してください。"}
    if code.startswith("header_axis_"):
        return {"error": code, "error_detail": message, "processing_stage": "header_boundaries",
                "recovery_action": "review_header_boundaries",
                "error_user_message": "ヘッダー境界の補正を確認してください。"}
    return None


def header_candidates(evidence):
    """Return only detector-produced header x positions for operator review."""
    if not isinstance(evidence, dict):
        return []
    clusters = evidence.get("fax_x_clusters")
    if not isinstance(clusters, list):
        clusters = evidence.get("raw_fax_x_clusters")
    candidates = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        value = cluster.get("value")
        if type(value) in (int, float) and math.isfinite(value):
            candidates.append(float(value))
    return candidates


def coordinates(value, *, canvas_height=None):
    if not isinstance(value, dict):
        raise ValueError("row_axis_override_invalid")
    space = value.get("coordinate_space")
    if not isinstance(space, dict) or space.get("mode") != "template_canvas":
        raise ValueError("row_axis_coordinate_space_invalid")
    if any(type(space.get(key)) is not int or space[key] <= 0 for key in ("width", "height")):
        raise ValueError("row_axis_coordinate_space_invalid")
    if canvas_height is not None and space["height"] != canvas_height:
        raise ValueError("row_axis_canvas_changed")
    ys = value.get("corrected_ys")
    if not isinstance(ys, list) or len(ys) < 2 or any(
        type(y) not in (int, float) or not math.isfinite(y) or not 0 <= y <= space["height"] for y in ys
    ):
        raise ValueError("row_axis_ys_invalid")
    if any(b <= a for a, b in zip(ys, ys[1:])):
        raise ValueError("row_axis_ys_not_monotonic")
    return list(ys)


def calendar_contract(physical_plan):
    dates = [date.fromisoformat(row["date"]) for row in physical_plan]
    if not dates or dates != sorted(dates):
        raise ValueError("fax_canonical_day_identity_ambiguous")
    _, start, end = sheet_week_service.parse_sheet_week_value(sheet_week_service.build_calendar_week_value(dates[0]))
    if start is None or end is None or dates[-1] > end:
        raise ValueError("fax_calendar_week_unresolved")
    counts = Counter(dates)
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "expected_band_count": counts.get(start + timedelta(days=i))} for i in range(7)]


def manual_match(value, *, physical_plan, header_bands, canvas_height, canvas_width):
    ys = coordinates(value, canvas_height=canvas_height)
    if value["coordinate_space"]["width"] != canvas_width:
        raise ValueError("row_axis_canvas_changed")
    days = calendar_contract(physical_plan)
    body_count = len(ys) - header_bands - 1
    boundaries = validate_days(value, days=days, body_count=body_count)
    return ys, {"used": True, "method": "operator_row_axis_override",
                "reason": "applied_operator_row_axis_override", "corrected_ys": ys,
                "structural_match": {"used": True, "detected_body_band_count": body_count,
                                     "body_day_boundary_indexes": list(boundaries),
                                     "day_labels": list(value["day_labels"]),
                                     "source": "operator_row_axis_override"}}


def validate_days(value, *, days, body_count):
    if value.get("day_labels") != [day["date"] for day in days]:
        raise ValueError("row_axis_calendar_days_required")
    boundaries = value.get("body_day_boundary_indexes")
    if (not isinstance(boundaries, list) or len(boundaries) != 8
            or any(type(b) is not int for b in boundaries) or boundaries[0] != 0
            or boundaries[-1] != body_count
            or any(b <= a for a, b in zip(boundaries, boundaries[1:]))):
        raise ValueError("row_axis_day_boundaries_invalid")
    for day, first, stop in zip(days, boundaries, boundaries[1:]):
        expected = day["expected_band_count"]
        if expected is not None and stop - first != expected:
            raise ValueError(f"row_axis_day_band_count_mismatch:{day['date']}:expected={expected}:actual={stop-first}")
    return list(boundaries)
