from itertools import accumulate

import numpy as np
import pytest

from src.services import hakodate_step_review_pipeline_service as pipeline
from src.services.hakodate_cell_ocr_batch_service import _validate_menu_day_boundaries


def _sheet(meal_counts):
    row_ids = [
        f"2026-09-{day:02d}__{meal}__{slot}"
        for day, counts in enumerate(meal_counts, start=1)
        for meal, count in zip(("breakfast", "lunch", "dinner"), counts)
        for slot in range(1, count + 1)
    ]
    return {
        "physical_menu_row_ids": row_ids,
        "physical_menu_row_count": len(row_ids),
    }


def _observed_axis(monkeypatch, day_counts, blank_counts=(), header_count=2):
    boundaries = [0, *accumulate((*day_counts, *blank_counts))]
    body_count = sum(day_counts) + sum(blank_counts)
    # Unequal observed heights expose interpolation as well as truncation.
    ys = [40.0]
    for index in range(header_count + body_count):
        ys.append(ys[-1] + 30 + index % 3 * 4)
    xs = [float(value) for value in range(50, 851, 100)]
    points = []
    for index, y in enumerate(ys):
        body_index = index - header_count
        columns = range(len(xs)) if body_index in boundaries else range(3, len(xs))
        points.extend({"x": xs[column], "y": y} for column in columns)

    def detect(image, *, corrected_xs, template_ys):
        assert corrected_xs == xs
        assert len(template_ys) - pipeline.STEP_REVIEW_HEADER_BANDS - 1 == 57
        return points, {"intersection_count": len(points)}

    monkeypatch.setattr(pipeline, "_detect_table_intersections_for_row_axis", detect)
    template_ys = np.linspace(ys[0], ys[-1], 60).astype(int).tolist()
    corrected, evidence = pipeline._row_intersection_correct_ys(
        rectified_fax=np.zeros((int(ys[-1]) + 1, 901, 3), dtype=np.uint8),
        corrected_xs=xs,
        template_ys=template_ys,
    )
    return corrected, evidence, ys, boundaries


@pytest.mark.parametrize("blank_counts", [(2, 3, 4), (8, 8, 8), (7, 9, 11)])
def test_month_boundary_preserves_all_observed_rows(monkeypatch, blank_counts):
    corrected, evidence, observed, boundaries = _observed_axis(
        monkeypatch, (8, 8, 8, 8), blank_counts
    )

    assert corrected == observed
    assert len(corrected) == 2 + 32 + sum(blank_counts) + 1
    assert evidence["used"] is True
    structural = evidence["structural_match"]
    assert structural["reason"] == "observed_fax_boundaries_including_blank_bands"
    assert structural["detected_body_band_count"] == 32 + sum(blank_counts)
    assert structural["body_day_boundary_indexes"] == boundaries
    assert _validate_menu_day_boundaries(_sheet([(2, 3, 3)] * 4), evidence) is None


@pytest.mark.parametrize(
    "meal_counts",
    [
        [(2, 3, 3), (1, 3, 2), (3, 4, 2), (2, 2, 3), (1, 2, 3), (3, 3, 4), (2, 3, 3)],
        [(3, 4, 3), (2, 3, 4), (2, 2, 3), (3, 3, 3), (1, 4, 3), (2, 4, 4), (3, 2, 3)],
    ],
)
def test_fullweek_variable_meal_counts_preserve_observed_boundaries(monkeypatch, meal_counts):
    day_counts = tuple(map(sum, meal_counts))
    corrected, evidence, observed, boundaries = _observed_axis(monkeypatch, day_counts)

    assert corrected == observed
    assert len(corrected) == 2 + sum(day_counts) + 1
    assert evidence["used"] is True
    assert evidence["structural_match"]["detected_body_band_count"] == sum(day_counts)
    assert evidence["structural_match"]["body_day_boundary_indexes"] == boundaries
    assert _validate_menu_day_boundaries(_sheet(meal_counts), evidence) is None


def test_missing_header_boundary_blocks_row_axis(monkeypatch):
    corrected, evidence, _, _ = _observed_axis(monkeypatch, (8, 8, 8, 8), header_count=1)

    assert corrected is None
    assert evidence["used"] is False
    assert evidence["reason"] == "fax_body_header_boundary_unresolved"


@pytest.mark.parametrize(
    "boundaries",
    [[], [8, 16, 24, 32], [0, 8, 24, 32], [0, 8, 16, 24],
     [0, 8, 16, 24, 31], [0, 8, 12, 16, 24, 32], [0, 8, 8, 16, 24, 32]],
)
def test_menu_day_boundaries_reject_nonexact_observed_indexes(boundaries):
    with pytest.raises(ValueError, match="fax_day_boundaries_disagree_with_monthly_menu"):
        _validate_menu_day_boundaries(
            _sheet([(2, 3, 3)] * 4),
            {"structural_match": {"body_day_boundary_indexes": boundaries}},
        )


def test_menu_day_boundaries_reject_missing_structural_evidence():
    with pytest.raises(ValueError, match="fax_day_boundaries_disagree_with_monthly_menu"):
        _validate_menu_day_boundaries(_sheet([(2, 3, 3)] * 4), {})


@pytest.mark.parametrize("change", ["absent", "missing", "additional", "duplicate"])
def test_menu_day_boundaries_reject_unresolved_physical_ids(change):
    sheet = _sheet([(2, 3, 3)] * 4)
    if change == "absent":
        del sheet["physical_menu_row_ids"]
    elif change == "missing":
        sheet["physical_menu_row_ids"].pop(12)
    elif change == "additional":
        sheet["physical_menu_row_ids"].append("2026-09-04__dinner__4")
    else:
        del sheet["physical_menu_row_count"]
        sheet["physical_menu_row_ids"].append(sheet["physical_menu_row_ids"][0])

    with pytest.raises(ValueError, match="physical_menu_row_ids_unresolved"):
        _validate_menu_day_boundaries(
            sheet, {"structural_match": {"body_day_boundary_indexes": [0, 8, 16, 24, 32]}}
        )
