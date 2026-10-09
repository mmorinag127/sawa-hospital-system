"""Synthetic copied-master/layout/geometry proofs, not native FAX or staging proof."""

from copy import copy
from datetime import date, timedelta
import json
from shutil import copyfile

from openpyxl import load_workbook
from openpyxl.worksheet.print_settings import PrintArea
import pytest

from src.services import hakodate_assignment_service as assignment
from src.services import hakodate_step_review_pipeline_service as review
from src.services import master_order_form_template_service as master


START = date(2026, 9, 27)
WEEK = "2026-09@2026-09-27~2026-10-03"
MEALS = ("朝", "昼", "夕")


def facility():
    return {
        "facility_id": "DYNAMIC-SYNTHETIC", "facility_name": "合成行計画試験",
        "fax_template": {
            "columns": [
                {"index": 0, "role": "date", "name": "date_mmdd", "header": "日付"},
                {"index": 1, "role": "daypart", "name": "daypart", "header": "区分"},
                {"index": 2, "role": "menu_name", "name": "menu", "header": "献立"},
                {"index": 3, "role": "quantity", "name": "qty.regular_x", "header": "常食",
                 "diet_type": "regular", "area_id": "X"},
                {"index": 4, "role": "quantity", "name": "qty.no_meat_x", "header": "肉禁",
                 "diet_type": "no_meat", "area_id": "X"},
                {"index": 5, "role": "note", "name": "remarks", "header": "備考"},
            ],
            "body_merge_policy": {"mode": "daypart", "columns": ["qty.regular_x"], "required": True},
        },
    }


def entries_for(days, sizes):
    result = []
    for offset in days:
        for meal, count in zip(MEALS, sizes):
            for slot in range(count):
                result.append({"menu_date": (START + timedelta(days=offset)).isoformat(),
                               "daypart": meal, "category": "主" if slot == 0 else "",
                               "name": f"physical-{offset}-{meal}-{slot}", "sort_order": slot + 1})
    return result


@pytest.fixture
def copied_master(tmp_path):
    original = master.MASTER_TEMPLATE_PATH.read_bytes()
    path = tmp_path / "copied-locked-master.xlsx"
    copyfile(master.MASTER_TEMPLATE_PATH, path)
    yield path
    assert master.MASTER_TEMPLATE_PATH.read_bytes() == original
    assert path.read_bytes() == original


def build_reload(tmp_path, copied_master, entries, *, week=WEEK):
    output = tmp_path / "generated.xlsx"
    master.build_facility_template_xlsx(
        facility_config=facility(), output_path=output, master_template_path=copied_master,
        week_value=week, week_menu_entries=entries,
    )
    return load_workbook(output)


CASES = [("56", range(7), (2, 3, 3), 0), ("57", range(7), (2, 3, 3), 1),
         ("58", range(7), (2, 3, 3), 2), ("four-per-meal", range(7), (4, 4, 4), 0),
         ("five-per-meal", range(7), (5, 5, 5), 0),
         ("September-four-days", range(4), (4, 5, 4), 0),
         ("October-three-days", range(4, 7), (5, 4, 5), 0)]


@pytest.mark.parametrize("label,days,sizes,extra", CASES, ids=[case[0] for case in CASES])
def test_actual_saved_reloaded_layout_and_all_consumers_share_every_physical_row(
    tmp_path, copied_master, label, days, sizes, extra,
):
    entries = entries_for(days, sizes)
    for index in range(extra):
        entries.append({**entries[-1], "name": f"last-extra-{index}", "sort_order": 4 + index})
    expected_count = len(entries)
    workbook = build_reload(tmp_path, copied_master, entries)
    try:
        ws = workbook[master.FACILITY_TEMPLATE_SHEET_NAME]
        plan = master.validated_body_row_plan(ws)
        end = 10 + expected_count
        assert len(plan) == expected_count
        assert [row["source_index"] for row in plan] == list(range(expected_count))
        assert [(row["date"], row["daypart"], row["category"], row["name"]) for row in plan] == [
            (entry["menu_date"], entry["daypart"], entry["category"], entry["name"]) for entry in entries
        ]
        assert ws.cell(end, 4).value == entries[-1]["name"]
        assert master.validated_body_end_row(ws) == end
        assert [region.bounds for region in PrintArea.from_string(ws.print_area).ranges] == [(1, 1, 7, end + 2)]
        assert all(ws.cell(end, column).border.bottom.style == "thick" for column in range(1, 8))
        assert ws.cell(end + 1, 4).value is None
        assert ws.row_dimensions[end].height == ws.row_dimensions[11].height
        spans = []
        for offset, entry in enumerate(entries):
            key = (entry["menu_date"], entry["daypart"])
            if spans and spans[-1][2] == key:
                spans[-1] = (spans[-1][0], 11 + offset, key)
            else:
                spans.append((11 + offset, 11 + offset, key))
        assert {(region.min_row, region.max_row) for region in ws.merged_cells.ranges
                if region.min_col == region.max_col == 5 and region.min_row >= 11} == {
                    (first, last) for first, last, _key in spans}
        assert {(region.min_row, region.max_row) for region in ws.merged_cells.ranges
                if region.min_col == region.max_col == 2 and region.min_row >= 11} == {
                    (first, last) for first, last, _key in spans}
        for row in range(11, end):
            assert ws.cell(row, 6).border.bottom.style == "thin"
            assert ws.cell(row, 6).border.right.style == "thin"

        draft = {"fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.no_meat_x", "remarks"],
                 "rows": [[entry["menu_date"], entry["daypart"], entry["name"], "7", "0", ""] for entry in entries]}
        logical = review.canonical_logical_target_plan(worksheet=ws, fax_template=facility()["fax_template"], draft_sheet=draft)
        covered = [cell for region in logical for cell in region["covered_sheet_cells"]]
        assert len(covered) == expected_count * 3
        assert set(covered) == {f"{column}{row}" for row in range(11, end + 1) for column in "EFG"}
        grid = assignment._structure_grid_from_rendered_worksheet(ws, dpi=144)
        assert grid["detected_row_count"] == expected_count + 2
        assert len(grid["row_edges"]) == expected_count + 3
        maps = [assignment._workbook_physical_row_map(ws, row_count=expected_count + 2),
                review._step_review_physical_row_map(ws, row_count=expected_count + 2)]
        for row_map in maps:
            assert list(row_map) == list(range(2, expected_count + 2))
            assert [row["worksheet_row"] for row in row_map.values()] == list(range(11, end + 1))
            assert [row["menu_name"] for row in row_map.values()] == [entry["name"] for entry in entries]
        assert review._step_review_worksheet_row_to_grid_index(end, body_end_row=end) == expected_count + 1
        assert review._step_review_worksheet_row_to_grid_index(end + 1, body_end_row=end) is None
        regions, metadata = review._post_menu_target_regions(
            worksheet=ws, column_edges=grid["column_edges"], row_edges=grid["row_edges"],
            fax_template=facility()["fax_template"], draft_sheet=draft,
            menu_body_row_indexes=list(range(expected_count)),
        )
        assert metadata["logical_target_count"] == expected_count * 3
        assert {cell for region in regions for cell in region["covered_sheet_cells"]} == set(covered)
        for region in regions:
            indexes = [target["grid_row_index"] for target in region["logical_targets"]]
            column = region["worksheet_col"] - 1
            assert region["bbox"] == pytest.approx([
                grid["column_edges"][column], grid["row_edges"][min(indexes)],
                grid["column_edges"][column + 1], grid["row_edges"][max(indexes) + 1],
            ])
    finally:
        workbook.close()


def schema_cell(workbook, key):
    return next(row[1] for row in workbook["generated_template_schema"].iter_rows() if row[0].value == key)


@pytest.mark.parametrize("tamper,code", [
    ("schema", "generated_body_schema_missing"), ("rowcount", "generated_body_row_plan_changed"),
    ("menu", "generated_body_row_identity_changed"), ("date", "generated_body_row_identity_changed"),
    ("meal", "generated_body_row_identity_changed"), ("category", "generated_body_row_identity_changed"),
    ("source-index", "generated_body_row_identity_invalid"), ("merge", "generated_body_merge_plan_changed"),
    ("border", "generated_body_end_border_changed"), ("print", "generated_body_row_plan_changed"),
])
def test_saved_workbook_tamper_stops_shared_validation(tmp_path, copied_master, tamper, code):
    workbook = build_reload(tmp_path, copied_master, entries_for(range(4), (4, 4, 4)))
    try:
        ws = workbook[master.FACILITY_TEMPLATE_SHEET_NAME]
        if tamper == "schema":
            del workbook["generated_template_schema"]
        elif tamper == "rowcount":
            schema_cell(workbook, "week_menu_rows").value = 47
        elif tamper in {"menu", "date", "meal", "category"}:
            ws.cell(11, {"date": 1, "meal": 2, "category": 3, "menu": 4}[tamper]).value = "changed"
        elif tamper == "source-index":
            cell = schema_cell(workbook, "week_menu_physical_row_plan")
            plan = json.loads(cell.value)
            plan[1]["source_index"] = plan[0]["source_index"]
            cell.value = json.dumps(plan)
        elif tamper == "merge":
            ws.unmerge_cells("E11:E14")
        elif tamper == "border":
            border = copy(ws.cell(58, 7).border)
            border.bottom = None
            ws.cell(58, 7).border = border
        else:
            ws.print_area = "A1:G59"
        output = tmp_path / "tampered.xlsx"
        workbook.save(output)
        reloaded = load_workbook(output)
        try:
            with pytest.raises(master.FacilityTemplateBuildError, match=f"^{code}$"):
                master.validated_body_row_plan(reloaded[master.FACILITY_TEMPLATE_SHEET_NAME])
        finally:
            reloaded.close()
    finally:
        workbook.close()


@pytest.mark.parametrize("field,value", [("date_mmdd", "2026-09-28"), ("daypart", "昼"), ("menu", "wrong-menu")])
def test_logical_plan_rejects_same_count_draft_identity_substitution(tmp_path, copied_master, field, value):
    entries = entries_for(range(3), (4, 4, 4))
    workbook = build_reload(tmp_path, copied_master, entries)
    try:
        fields = ["date_mmdd", "daypart", "menu"]
        draft = {"fields": fields, "rows": [[entry["menu_date"], entry["daypart"], entry["name"]] for entry in entries]}
        draft["rows"][0][fields.index(field)] = value
        with pytest.raises(ValueError, match="^hakodate_physical_row_changed$"):
            review.canonical_logical_target_plan(worksheet=workbook[master.FACILITY_TEMPLATE_SHEET_NAME],
                                                 fax_template=facility()["fax_template"], draft_sheet=draft)
    finally:
        workbook.close()


@pytest.mark.parametrize("week,entries,code", [(WEEK, [], "week_menu_entries_missing"),
    ("not-a-week", [], "week_menu_week_invalid"),
    (WEEK, [{"menu_date": "?", "daypart": "朝", "name": "invalid"}], "week_menu_entry_identity_unresolved")])
def test_explicit_week_unresolved_inputs_never_generate_static_body(tmp_path, copied_master, week, entries, code):
    with pytest.raises(master.FacilityTemplateBuildError, match=f"^{code}$"):
        build_reload(tmp_path, copied_master, entries, week=week)
    assert not (tmp_path / "generated.xlsx").exists()


def test_weekless_prototype_keeps_explicit_static_contract(tmp_path, copied_master):
    workbook = build_reload(tmp_path, copied_master, None, week=None)
    try:
        ws = workbook[master.FACILITY_TEMPLATE_SHEET_NAME]
        assert master.validated_body_row_plan(ws, allow_static=True) is None
        assert master.validated_body_end_row(ws, allow_static=True) == 67
        assert [region.bounds for region in PrintArea.from_string(ws.print_area).ranges] == [(1, 1, 7, 69)]
        assert ws["A4"].value == facility()["facility_name"]
        assert schema_cell(workbook, "week_menu_rows").value == 0
        assert {"E11:E12", "E13:E15", "E16:E18"}.issubset({str(region) for region in ws.merged_cells.ranges})
    finally:
        workbook.close()
