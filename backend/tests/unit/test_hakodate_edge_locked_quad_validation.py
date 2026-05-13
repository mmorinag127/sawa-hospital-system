from __future__ import annotations

from src.services.hakodate_fixed_quad_registration_service import _edge_locked_reasons


def _metric(
    *,
    hit_rate: float = 1.0,
    mean_abs_offset_px: float = 1.0,
    max_abs_offset_px: float = 2.0,
    gap_max_px_est: float = 0.0,
) -> dict[str, float]:
    return {
        "hit_rate": hit_rate,
        "mean_abs_offset_px": mean_abs_offset_px,
        "max_abs_offset_px": max_abs_offset_px,
        "gap_max_px_est": gap_max_px_est,
    }


def test_single_borderline_offset_with_strong_edge_is_warning_not_blocker() -> None:
    metrics = {
        "top": _metric(),
        "right": _metric(mean_abs_offset_px=4.708, max_abs_offset_px=14.0),
        "bottom": _metric(),
        "left": _metric(),
    }
    reasons, warnings = _edge_locked_reasons(
        metrics,
        {"top": "fit", "right": "fit", "bottom": "fit", "left": "fit"},
        "large_grid_component",
    )
    assert reasons == []
    assert warnings == ["right_offset_borderline:4.708"]


def test_borderline_offset_with_weak_edge_remains_blocker() -> None:
    metrics = {
        "top": _metric(),
        "right": _metric(hit_rate=0.9, mean_abs_offset_px=4.708, max_abs_offset_px=14.0),
        "bottom": _metric(),
        "left": _metric(),
    }
    reasons, warnings = _edge_locked_reasons(
        metrics,
        {"top": "fit", "right": "fit", "bottom": "fit", "left": "fit"},
        "large_grid_component",
    )
    assert reasons == ["right_offset_high:4.708"]
    assert warnings == []


def test_multiple_borderline_offsets_are_blocked() -> None:
    metrics = {
        "top": _metric(mean_abs_offset_px=4.8, max_abs_offset_px=12.0),
        "right": _metric(mean_abs_offset_px=4.708, max_abs_offset_px=14.0),
        "bottom": _metric(),
        "left": _metric(),
    }
    reasons, warnings = _edge_locked_reasons(
        metrics,
        {"top": "fit", "right": "fit", "bottom": "fit", "left": "fit"},
        "large_grid_component",
    )
    assert reasons == ["multiple_edge_offset_borderline:top,right"]
    assert warnings == ["top_offset_borderline:4.8", "right_offset_borderline:4.708"]
