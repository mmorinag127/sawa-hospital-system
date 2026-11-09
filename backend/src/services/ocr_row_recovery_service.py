"""Operator geometry is input, never a candidate to fit to an estimate."""
from collections import Counter
from datetime import date, timedelta
import math

from src.services import sheet_week_service
from src.services.hakodate_physical_menu_row_service import full_week_fax_period


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
                "result_state": "review_required",
                "error_user_message": "行・日付の境界を確認してください。原本の横罫線と日付の開始位置を指定すると再開できます。"}
    if code.startswith("header_axis_") or code in {"header_boundaries_unresolved", "corrected_x_not_monotonic", "post_menu_boundaries_unresolved"}:
        return {"error": code, "error_detail": message, "processing_stage": "header_boundaries",
                "recovery_action": "review_header_boundaries",
                "result_state": "review_required",
                "error_user_message": "列の境界を確認してください。原本の縦罫線を指定すると再開できます。"}
    if code == "quad_estimation_failed" or message.startswith("edge locked quad estimation failed"):
        return {"error": "quad_estimation_failed", "error_detail": message,
                "processing_stage": "quad_estimation", "result_state": "review_required",
                "recovery_action": "review_or_edit_quad",
                "error_user_message": "表の四隅を確認してください。左上・右上・右下・左下を指定すると再開できます。"}
    return None


def review_guidance(reason, *, axis, match):
    """Describe the observed mismatch without inventing missing coordinates."""
    messages = {
        "fax_date_column_physical_boundaries_changed": "日付欄の区切りを確認できません。各日の最初の行と、表の最下端を確認してください。",
        "fax_physical_body_count_changed": "検出した行数が献立の行数と一致しません。一本の罫線が二重になっていないか、抜けた横線がないか確認してください。",
        "fax_body_header_boundary_unresolved": "見出しと本文の境目を確認できません。表の向き・列境界・最初の献立行の上端を確認してください。",
        "fax_calendar_day_boundaries_ambiguous": "日付の切り替わりが複数の位置に見つかりました。各日の最初の行を選択してください。",
        "fax_header_sequence_ambiguous": "見出しと本文の区切りを一意に決められません。本文より上の横線を確認してください。",
    }
    source = match.get("canonical_day_source") or (match.get("structural_match") or {}).get("canonical_day_source") or {}
    return {
        "message": messages.get(reason, "原本の縦罫線に列境界を合わせてください。" if axis == "header" else "原本の横罫線に行境界を合わせ、各日の最初の行を選択してください。"),
        "missing_boundaries": match.get("missing_date_column_boundary_indexes") or [],
        "extra_boundaries": match.get("extra_date_column_boundary_indexes") or [],
        "expected_body_count": source.get("canonical_physical_row_count"),
        "observed_body_count": source.get("observed_physical_row_count"),
    }


def observed_day_candidates(days, match):
    structural = match.get("structural_match") or {}
    if structural.get("body_day_boundary_indexes"):
        return structural["body_day_boundary_indexes"]
    source = match.get("canonical_day_source") or {}
    if source.get("canonical_physical_row_count") != source.get("observed_physical_row_count"):
        return None
    if not days or any(day["expected_band_count"] is None for day in days):
        return None
    observed = set(source.get("observed_date_column_boundary_indexes") or [])
    offsets = [0]
    for day in days:
        offsets.append(offsets[-1] + day["expected_band_count"])
    return [offset if offset in observed else None for offset in offsets]


def header_candidates(evidence):
    """Return only detector-produced header x positions for operator review."""
    if not isinstance(evidence, dict):
        return []
    clusters = evidence.get("fax_x_clusters")
    if not isinstance(clusters, list):
        clusters = evidence.get("raw_fax_x_clusters")
    if not isinstance(clusters, list):
        return []
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


def calendar_contract(physical_plan, *, week_value):
    dates = [date.fromisoformat(row["date"]) for row in physical_plan]
    if not dates or dates != sorted(dates):
        raise ValueError("fax_canonical_day_identity_ambiguous")
    _, start, end = sheet_week_service.parse_sheet_week_value(week_value)
    if start is None or end is None or dates[0] < start or dates[-1] > end:
        raise ValueError("fax_calendar_week_unresolved")
    counts = Counter(dates)
    fax_start, fax_end = full_week_fax_period(start, end)
    return [{"date": (fax_start + timedelta(days=i)).isoformat(),
             "expected_band_count": counts.get(fax_start + timedelta(days=i))}
            for i in range((fax_end - fax_start).days + 1)]


def manual_match(value, *, physical_plan, week_value, header_bands, canvas_height, canvas_width):
    ys = coordinates(value, canvas_height=canvas_height)
    if value["coordinate_space"]["width"] != canvas_width:
        raise ValueError("row_axis_canvas_changed")
    days = calendar_contract(physical_plan, week_value=week_value)
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
    if (not isinstance(boundaries, list) or len(boundaries) != len(days) + 1
            or any(type(b) is not int for b in boundaries) or boundaries[0] != 0
            or boundaries[-1] != body_count
            or any(b <= a for a, b in zip(boundaries, boundaries[1:]))):
        raise ValueError("row_axis_day_boundaries_invalid")
    for day, first, stop in zip(days, boundaries, boundaries[1:]):
        expected = day["expected_band_count"]
        if expected is not None and stop - first != expected:
            raise ValueError(f"row_axis_day_band_count_mismatch:{day['date']}:expected={expected}:actual={stop-first}")
    return list(boundaries)
