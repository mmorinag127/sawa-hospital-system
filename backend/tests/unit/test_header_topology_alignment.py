"""Observed header structure, not row-count fitting or recognition fixtures."""
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.services import hakodate_step_review_pipeline_service as subject


def test_outer_frame_identity_cannot_be_skipped_to_improve_gap_score():
    template = [13, 115, 164, 251, 989, 1171, 1324, 1417, 1510, 1603,
                1696, 1789, 1883, 1976, 2069, 2162, 2255, 2349]
    observed = [15, 100.94, 213, 895.75, 995, 1094.05, 1192.5, 1291.75,
                1390, 1488.06, 1586.5, 1685, 1784.5, 1882.25, 1981.5, 2080, 2346]
    corrected, evidence = subject._structural_match_header_x_clusters(
        template_xs=template, x_clusters=[{"value": x} for x in observed],
    )
    assert corrected is None
    assert evidence["reason"] == "header_axis_count_requires_review"
    assert evidence["template_count"] == len(template)
    assert evidence["cluster_count"] == len(observed)


@pytest.mark.parametrize("offset,scale", [(0, 1), (37, 2.5)])
def test_staggered_group_headers_match_column_topology_not_global_y(offset, scale):
    contract = [{"column_votes": votes} for votes in (
        [0, 1, 3, 4], [3, 4], [1, 2, 3, 4], [0, 1, 2, 3, 4],
    )]
    # The leaf boundary starts earlier in the left branch of the physical header.
    levels = [[0, 1, 3, 4], [1, 2, 3, 4], [3, 4], [0, 1, 2, 3, 4]]
    clusters = [{"value": offset + scale * index, "column_votes": votes}
                for index, votes in enumerate(levels)]
    matches, evidence = subject._match_printed_header_topology(clusters, contract)
    assert matches == {0: [0], 1: [1], 2: [1, 2], 3: [3]}
    assert len(evidence["candidates"]) == 1


@pytest.mark.parametrize("change", ["missing", "extra", "wrong_column"])
def test_missing_extra_or_wrong_header_edges_are_not_accepted(change):
    contract = [{"column_votes": votes} for votes in ([0, 2], [1, 2], [0, 1, 2])]
    levels = [[0, 2], [1, 2], [0, 1, 2]]
    if change == "missing":
        levels[1] = [2]
    elif change == "extra":
        levels.insert(1, [1])
    else:
        levels[0] = [0, 1]
    matches, _ = subject._match_printed_header_topology(
        [{"value": index, "column_votes": votes} for index, votes in enumerate(levels)], contract,
    )
    assert matches is None


def test_private_reported_original_uses_authored_facility_week_and_menu(tmp_path):
    location = os.environ.get("SAWA_KASUGA_GEOMETRY_FIXTURE")
    if not location:
        pytest.skip("Private reported original is not distributed with source")
    import fitz
    from openpyxl import load_workbook
    from src.services import master_order_form_template_service as master
    from src.services import hakodate_fixed_quad_registration_service as registration

    root = Path(location)
    pdf = root / "reported-ORD108ac1e3.pdf"
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert digest == "27b30e1dde2bd9e83c5b9f97f8adbb17fc38bb4e8337f35c2f85a67decbe70a2"
    config = json.loads((root / "kasuga-config.json").read_text())
    diagnostic = json.loads((root / "kasuga-row-diag.json").read_text())
    quad = json.loads((root / "kasuga-quad.json").read_text())
    assert config["facility_id"] == "FAC00003"
    assert config["week_start"] == "2026-11-15" and config["week_end"] == "2026-11-21"
    workbook_path = tmp_path / "reported-canonical.xlsx"
    master.build_facility_template_xlsx(
        facility_config=config["config"], master_template_path=master.MASTER_TEMPLATE_PATH,
        output_path=workbook_path, week_value="2026-11@2026-11-15~2026-11-21",
        week_menu_entries=diagnostic["source_receipt"]["physical_menu_entries"],
    )
    width, height = diagnostic["canvas_size"]
    xs, ys = registration.registration_template_axes_from_workbook(
        workbook_path, sheet_name=master.FACILITY_TEMPLATE_SHEET_NAME,
        canvas_width=width, canvas_height=height,
    )
    with fitz.open(pdf) as doc:
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(1864 / page.rect.width, 1864 / page.rect.width), alpha=False)
        original = cv2.cvtColor(np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3), cv2.COLOR_RGB2BGR)
    image = registration.rectify_fax_to_template_grid(
        original, quad_px=quad["suggested_quad_px"], table_bbox=[xs[0], ys[0], xs[-1], ys[-1]],
        canvas_width=width, canvas_height=height,
    )
    corrected_xs, header = subject._header_intersection_correct_xs(
        rectified_fax=image, template_xs=xs, template_ys=ys,
    )
    assert corrected_xs is not None, header
    assert header["observed_header_extent"] is not None
    assert header["structural_match"]["skipped_template_indexes"] == []
    workbook = load_workbook(workbook_path)
    try:
        plan = subject.canonical_logical_target_plan(
            worksheet=workbook[master.FACILITY_TEMPLATE_SHEET_NAME],
            fax_template=config["config"]["fax_template"],
            draft_sheet={"fields": ["date", "daypart", "menu"], "rows": [
                [entry["menu_date"], entry["daypart"], entry["name"]]
                for entry in diagnostic["source_receipt"]["physical_menu_entries"]
            ]},
        )
        assert {target["field"] for target in plan if target["role"] == "quantity"} == {
            column["name"] for column in config["config"]["fax_template"]["columns"]
            if column["role"] == "quantity"
        }
        corrected, rows = subject._row_intersection_correct_ys(
            rectified_fax=image, corrected_xs=corrected_xs, template_ys=ys,
            worksheet=workbook[master.FACILITY_TEMPLATE_SHEET_NAME],
        )
    finally:
        workbook.close()
    assert corrected is not None, rows
    day_source = rows["structural_match"]["canonical_day_source"]
    assert day_source["observed_physical_row_count"] == 56
    assert day_source["observed_date_column_boundary_indexes"] == list(range(0, 57, 8))
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
