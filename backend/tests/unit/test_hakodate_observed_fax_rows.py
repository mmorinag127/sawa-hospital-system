from itertools import accumulate
from datetime import date, timedelta

import numpy as np
import pytest
import cv2

from src.services import hakodate_step_review_pipeline_service as pipeline
from src.services.hakodate_cell_ocr_batch_service import _validate_menu_day_boundaries


def _sheet(meal_counts, start=date(2026, 9, 27)):
    row_ids = [
        f"{(start + timedelta(days=day)).isoformat()}__{meal}__{slot}"
        for day, counts in enumerate(meal_counts)
        for meal, count in zip(("breakfast", "lunch", "dinner"), counts)
        for slot in range(1, count + 1)
    ]
    return {
        "physical_menu_row_ids": row_ids,
        "physical_menu_row_count": len(row_ids),
        "week_id": f"{start:%Y-%m}@{start.isoformat()}~{(start + timedelta(days=len(meal_counts)-1)).isoformat()}",
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
    assert _validate_menu_day_boundaries(_sheet([(2, 3, 3)] * 4), evidence) == list(range(32))


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
    assert _validate_menu_day_boundaries(_sheet(meal_counts), evidence) == list(range(sum(day_counts)))


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


@pytest.mark.parametrize("blank_counts", [(8, 8, 8, 8), (2, 3, 4, 5), (9, 7, 6, 10)])
def test_leading_blank_days_map_to_observed_october_rows(monkeypatch, blank_counts):
    _, evidence, _, _ = _observed_axis(monkeypatch, (*blank_counts, 8, 8, 8))
    indexes = _validate_menu_day_boundaries(_sheet([(2, 3, 3)] * 3, date(2026, 10, 1)), evidence)
    assert indexes == list(range(sum(blank_counts), sum(blank_counts) + 24))


def test_leading_and_trailing_blank_days_with_variable_menu_counts(monkeypatch):
    _, evidence, _, _ = _observed_axis(monkeypatch, (5, 6, 9, 10, 11, 4, 3))
    sheet = _sheet([(2, 4, 3), (3, 4, 3), (3, 4, 4)], date(2026, 9, 29))
    assert _validate_menu_day_boundaries(sheet, evidence) == list(range(11, 41))


def test_wrong_target_day_count_is_not_replaced_with_another_matching_day(monkeypatch):
    _, evidence, _, _ = _observed_axis(monkeypatch, (8, 8, 8, 8, 9, 8, 8))
    with pytest.raises(ValueError, match="fax_day_boundaries_disagree_with_monthly_menu"):
        _validate_menu_day_boundaries(_sheet([(2, 3, 3)] * 3, date(2026, 10, 1)), evidence)


def test_missing_monthly_day_blocks_mapping(monkeypatch):
    _, evidence, _, _ = _observed_axis(monkeypatch, (8,) * 7)
    sheet = _sheet([(2, 3, 3)] * 3, date(2026, 10, 1))
    del sheet["physical_menu_row_ids"][8:16]
    sheet["physical_menu_row_count"] = 16
    with pytest.raises(ValueError, match="physical_menu_days_disagree_with_order_period"):
        _validate_menu_day_boundaries(sheet, evidence)


def test_broken_thin_fax_rulings_preserve_leading_blank_cells():
    xs = [float(value) for value in range(50, 851, 100)]
    ys = [50 + 32 * index for index in range(59)]
    image = np.full((1950, 901, 3), 255, dtype=np.uint8)
    for x in xs:
        cv2.line(image, (int(x), ys[0]), (int(x), ys[-1]), (0, 0, 0), 2)
    for index, y in enumerate(ys):
        if index in (0, 2) or (index > 2 and (index - 2) % 8 == 0):
            cv2.line(image, (50, y), (850, y), (0, 0, 0), 2)
        elif index == 1:
            cv2.line(image, (450, y), (850, y), (0, 0, 0), 2)
        else:
            # One-pixel gaps along the unfilled FAX rows, not missing rows.
            for x in range(350, 850, 6):
                cv2.line(image, (x, y), (x + 4, y), (0, 0, 0), 1)
    corrected, evidence = pipeline._row_intersection_correct_ys(
        rectified_fax=image, corrected_xs=xs, template_ys=ys,
    )
    assert len(corrected) == len(ys)
    assert evidence["structural_match"]["body_day_boundary_indexes"] == list(range(0, 57, 8))
    assert _validate_menu_day_boundaries(
        _sheet([(2, 3, 3)] * 3, date(2026, 10, 1)), evidence,
    ) == list(range(32, 56))


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
