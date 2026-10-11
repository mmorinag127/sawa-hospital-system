"""Local public-candidate and mapper boundaries, not HTTP bagging/OCR/live proof.

The unchanged PublicSavedCandidate creates real SQLite models, a synthetic local
PDF/receipt and canonical authored JSON projections. OCR selection is manually
seeded, not an executed recognizer. Only its existing IO/session redirections
are used; no identity, ownership, configuration or materialization guard mocks.
"""

from copy import deepcopy
from datetime import date
from hashlib import sha256

import fitz
import pytest

from test_modernization_explicit_ocr_quantity_contract import FIELDS, PublicSavedCandidate
from src.models.document import OrderDocument
from src.models.menu import MonthlyMenu, MonthlyMenuEntry
from src.models.order import Order
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.uploaded_pdf import UploadedPdf
from src.services import order_service as orders
from src.services.workflow_attempt_guard_service import capture_selected_pdf_receipt


@pytest.fixture
def saved(tmp_path, monkeypatch):
    fixture = PublicSavedCandidate(tmp_path, monkeypatch)
    fixture.save(["7", "0", "", ""], enabled=True)
    yield fixture
    fixture.close()


def save_rows(saved, rows, *, roles=(), fields=None):
    """Author config through real projection adoption; edit only the selected draft."""
    saved.authored["fax_template"]["fill_forward_roles"] = list(roles)
    saved.republish()
    payload = deepcopy(saved.payload)
    payload["rows"] = deepcopy(rows)
    if fields is not None:
        payload["fields"] = list(fields)
    saved.payload = deepcopy(payload)
    with saved.session_scope() as session:
        session.get(OrderSheetDraft, saved.draft_id).draft_sheet_json = deepcopy(payload)
    return payload


def captured_candidate(saved, original):
    captured = orders._capture_synchronous_cache_write_input(saved.order_id)
    assert captured is not None
    assert captured["draft_id"] == saved.draft_id
    assert captured["evidence_run_id"] == saved.evidence_id
    assert captured["selected_pdf_receipt"] == saved.receipt
    assert captured["draft"]["draft_sheet_json"] == original
    assert captured["configured_input"]["selected_authored_version"]["version_id"] == "authored-March"
    assert captured["configured_input"]["facility"]["fax_template"]["fill_forward_roles"] == saved.authored["fax_template"]["fill_forward_roles"]
    assert captured["physical_menu_entries"]
    result = orders.build_confirm_materialization_candidate(saved.order_id)
    assert saved.saved() == original
    assert result is not None and result["source"] == "draft_sheet"
    assert result["draft_id"] == saved.draft_id
    return result


def assert_identity_block(result, *, row, column, raw):
    assert result["error"] == "sheet_identity_review_required"
    assert result["line_count"] == 0 and result["lines"] == []
    matches = [issue for issue in result["cell_issues"]
               if issue["row_index"] == row and issue["column_index"] == column]
    assert len(matches) == 1
    assert matches[0]["raw"] == matches[0]["value"] == raw
    assert matches[0]["field"] == FIELDS[column]
    assert matches[0]["source"] == "draft_sheet"
    assert matches[0]["issue_code"] == {
        0: "sheet_date_identity_unresolved", 1: "sheet_daypart_identity_unresolved",
        3: "sheet_menu_identity_unresolved",
    }[column]


@pytest.mark.parametrize("raw_date,expected", [
    ("2026/3/1", "2026-03-01"), ("2026-03-01", "2026-03-01"),
    ("3/1(日)", "2026-03-01"), ("2026/3/1(日曜日)", "2026-03-01"),
    ("2026/3/2(月)", "2026-03-02"), ("2026年3月2日(月)", "2026-03-02"),
    ("2026年3月2日（月曜）", "2026-03-02"),
])
def test_public_explicit_date_formats_keep_quantities_zero_blank_and_source_indices(saved, raw_date, expected):
    rows = deepcopy(saved.payload["rows"])
    for row in rows:
        row[0] = raw_date
    original = save_rows(saved, rows)
    result = captured_candidate(saved, original)
    assert result["error"] is None and result["line_count"] == 2
    assert [(line["date"], line["daypart"], line["menu_name"], line["quantity_original"],
             line["source_row_index"], line["source_col_index"], line["source_field"])
            for line in result["lines"]] == [
        (expected, "朝", "選択献立0", 7, 0, 4, "qty.regular_x"),
        (expected, "朝", "選択献立1", 0, 1, 4, "qty.regular_x"),
    ]
    assert result["synchronous_write_input"]["draft_id"] == saved.draft_id
    assert orders._build_materialization_quantity_guard_detail(
        fields=FIELDS, rows_payload=original["rows"], candidate_lines=result["lines"],
        template=saved.authored["fax_template"]) is None


@pytest.mark.parametrize("roles,continuation", [
    (["date"], ["", "朝"]), (["daypart"], ["3/1", ""]),
    (["date", "daypart"], ["", ""]),
])
def test_public_authored_continuations_only_resolve_identity_copies(saved, roles, continuation):
    rows = deepcopy(saved.payload["rows"])
    rows[1][:2] = continuation
    original = save_rows(saved, rows, roles=roles)
    result = captured_candidate(saved, original)
    assert result["error"] is None
    assert [(line["date"], line["daypart"], line["quantity_original"], line["source_row_index"])
            for line in result["lines"]] == [("2026-03-01", "朝", 7, 0), ("2026-03-01", "朝", 0, 1)]
    assert saved.saved()["rows"][1][:2] == continuation


@pytest.mark.parametrize("row,column,value,roles", [
    (1, 0, "", []), (1, 1, "", []), (0, 0, "", ["date", "daypart"]),
    (0, 1, "", ["date", "daypart"]), (1, 3, "", ["date", "daypart", "menu"]),
    (1, 0, "月", ["date", "daypart"]), (1, 0, "2026/2/30", ["date", "daypart"]),
    (1, 0, "2026/3/1(月)", []), (1, 0, "3/1(月)", []),
    (1, 0, "2026年3月1日(月)", []), (1, 0, "3/1(月火)", []),
    (1, 1, "unknown", ["date", "daypart"]), (1, 1, "朝昼", ["date", "daypart"]),
    (1, 1, "breakfast/lunch", ["date", "daypart"]),
])
def test_public_invalid_identity_blocks_all_lines_with_readable_original_cell(saved, row, column, value, roles):
    rows = deepcopy(saved.payload["rows"])
    rows[row][column] = value
    original = save_rows(saved, rows, roles=roles)
    assert_identity_block(captured_candidate(saved, original), row=row, column=column, raw=value)


def test_public_changed_date_resets_authored_daypart_continuation(saved):
    rows = deepcopy(saved.payload["rows"])
    rows[1][:2] = ["3/2", ""]
    original = save_rows(saved, rows, roles=["date", "daypart"])
    assert_identity_block(captured_candidate(saved, original), row=1, column=1, raw="")


@pytest.mark.parametrize("alias,expected", [
    ("朝食", "朝"), ("breakfast", "朝"), ("昼食", "昼"),
    ("lunch", "昼"), ("夕食", "夕"), ("dinner", "夕"),
])
def test_public_known_explicit_meal_aliases_remain_valid_without_monthly_overwrite(saved, alias, expected):
    rows = deepcopy(saved.payload["rows"])
    rows[0][1] = alias
    original = save_rows(saved, rows)
    result = captured_candidate(saved, original)
    assert result["error"] is None and result["line_count"] == 2
    assert result["lines"][0]["daypart"] == expected
    assert result["lines"][0]["quantity_original"] == 7
    assert result["lines"][1]["daypart"] == "朝"


def test_public_operator_menu_edit_is_not_overwritten_by_monthly_menu(saved):
    rows = deepcopy(saved.payload["rows"])
    rows[0][3] = "operator-authored revised menu"
    original = save_rows(saved, rows)
    result = captured_candidate(saved, original)
    assert result["error"] is None
    assert result["lines"][0]["menu_name"] == "operator-authored revised menu"
    assert result["lines"][0]["quantity_original"] == 7
    assert result["synchronous_write_input"]["physical_menu_entries"][0]["menu_name"] == "選択献立0"


def test_public_repeated_menu_rows_remain_distinct_by_explicit_source_row(saved):
    rows = deepcopy(saved.payload["rows"])
    rows[1][3] = rows[0][3]
    original = save_rows(saved, rows)
    result = captured_candidate(saved, original)
    assert result["error"] is None and result["line_count"] == 2
    assert [(line["menu_name"], line["source_row_index"], line["quantity_original"])
            for line in result["lines"]] == [("選択献立0", 0, 7), ("選択献立0", 1, 0)]


@pytest.mark.parametrize("fields", [
    ["col1", "col2", "col3", "col4", *FIELDS[4:]],
    ["unresolved_date", *FIELDS[1:]],
    [*FIELDS[:3], "unresolved_menu", *FIELDS[4:]],
])
def test_public_unsupported_semantics_cannot_escape_through_legacy_header_parser(saved, fields):
    original = save_rows(saved, saved.payload["rows"], fields=fields)
    # Keep a genuine authored header, so a successful result cannot rely on removing it.
    original["header"] = ["日付", "時間", "区分", "献立", "常食", "肉禁", "魚禁", "職員"]
    with saved.session_scope() as session:
        session.get(OrderSheetDraft, saved.draft_id).draft_sheet_json = deepcopy(original)
    result = captured_candidate(saved, original)
    assert result["error"] == "sheet_identity_review_required"
    assert result["line_count"] == 0 and result["lines"] == []
    assert result["cell_issues"][0]["issue_code"] == "sheet_template_field_invalid"
    assert result["cell_issues"][0]["raw"] == fields


@pytest.mark.parametrize("padding", [[], [""] * 8, [None] * 8])
def test_public_fully_blank_padding_does_not_create_lines_or_renumber_sources(saved, padding):
    rows = deepcopy(saved.payload["rows"])
    rows.insert(1, padding)
    original = save_rows(saved, rows)
    result = captured_candidate(saved, original)
    assert result["error"] is None and result["line_count"] == 2
    assert [(line["source_row_index"], line["quantity_original"])
            for line in result["lines"]] == [(0, 7), (2, 0)]


def test_public_zero_is_not_blank_padding_and_requires_its_own_identity(saved):
    rows = deepcopy(saved.payload["rows"])
    rows.insert(1, ["", "", "", "", "0", "", "", ""])
    original = save_rows(saved, rows)
    result = captured_candidate(saved, original)
    assert_identity_block(result, row=1, column=0, raw="")
    assert_identity_block(result, row=1, column=1, raw="")
    assert_identity_block(result, row=1, column=3, raw="")


def test_public_uncertain_quantity_uses_original_preflight_without_identity_rescue(saved):
    rows = deepcopy(saved.payload["rows"])
    rows[1][4] = "?"
    original = save_rows(saved, rows)
    result = captured_candidate(saved, original)
    assert result["error"] == "sheet_ocr_review_required"
    assert result["line_count"] == 0 and result["lines"] == []
    assert [(issue["row_index"], issue["column_index"], issue["raw"])
            for issue in result["cell_issues"]] == [(1, 4, "?")]


@pytest.mark.parametrize("start,end", [(date(2026, 3, 31), date(2026, 4, 1)),
                                        (date(2026, 12, 31), date(2027, 1, 1))])
def test_public_explicit_selected_period_resolves_month_and_year_boundary_not_received_time(saved, start, end):
    # Author the period and local PDF together; never repair identities after capture.
    saved.master["facilities"][0]["versions"][0]["valid_to"] = end.isoformat()
    saved.republish()
    week = f"{start:%Y-%m}@{start.isoformat()}~{end.isoformat()}"
    document = fitz.open()
    document.new_page().insert_text((50, 50), f"Synthetic unit F / {week}; not operator FAX")
    pdf_bytes = document.tobytes()
    document.close()
    saved.pdf.write_bytes(pdf_bytes)
    with saved.session_scope() as session:
        order = session.get(Order, saved.order_id)
        order.week_code = week
        session.get(OrderDocument, "UNIT-DOC").week_code = week
        session.get(UploadedPdf, "UNIT-PDF").content_sha256 = sha256(pdf_bytes).hexdigest()
        workflow = session.get(OrderWorkflowState, saved.order_id)
        metadata = deepcopy(workflow.secondary_actions_json)
        metadata["workflow_v2"].update(week_start=start.isoformat(), week_end=end.isoformat())
        workflow.secondary_actions_json = metadata
        for index, day in enumerate((start, end)):
            month = day.strftime("%Y-%m")
            if session.get(MonthlyMenu, month) is None:
                session.add(MonthlyMenu(id=month, month_start=day.replace(day=1)))
                session.flush()
            session.add(MonthlyMenuEntry(id=f"UNIT-BOUNDARY-{index}", monthly_menu_id=month,
                                        menu_date=day, daypart="朝", category="主", diet_type="regular",
                                        name=f"選択献立{index}", slot_index=0))
        session.flush()
        saved.receipt = capture_selected_pdf_receipt(session, order)
    rows = deepcopy(saved.payload["rows"][:2])
    for row, day in zip(rows, (start, end)):
        row[0] = f"{day.month}/{day.day}"
    original = save_rows(saved, rows)
    result = captured_candidate(saved, original)
    assert result["error"] is None and result["derived_week_code"] == week
    assert [(line["date"], line["quantity_original"], line["source_row_index"])
            for line in result["lines"]] == [(start.isoformat(), 7, 0), (end.isoformat(), 0, 1)]
    assert result["synchronous_write_input"]["input_versions"]["order"]["received_at"].date() == date(2026, 3, 1)


def test_shared_canonical_mapper_preserves_both_sides_authored_continuations_and_raw_values():
    source = [["2026/3/2(月)", "朝", "主", "First", "7", "", "", ""],
              ["", "", "主", "Second", "0", "", "", ""]]
    targets = [{"values": deepcopy(row)} for row in source]
    before = deepcopy((source, targets))
    mapping, counts, stages = orders._build_canonical_payload_row_mapping(
        rows=targets, fields=FIELDS, payload_rows=source,
        template={"fill_forward_roles": ["date", "daypart"]})
    assert mapping == {0: 0, 1: 1}
    assert counts == {"exact": 2, "partial": 0, "neighbor": 0, "row_index": 0}
    assert stages == {0: "exact", 1: "exact"}
    assert (source, targets) == before


@pytest.mark.parametrize("side", ["source", "target"])
@pytest.mark.parametrize("invalid", ["2026/3/1(月)", "2026/3/1(月火)", "2025/3/1"])
def test_shared_canonical_mapper_rejects_explicit_weekday_or_year_contradiction_before_copy(side, invalid):
    source = [["2026/3/1(日)", "朝", "主", "Menu", "7", "", "", ""]]
    targets = [{"values": deepcopy(source[0])}]
    (source[0] if side == "source" else targets[0]["values"])[0] = invalid
    before = deepcopy((source, targets))
    with pytest.raises(orders._CanonicalOcrProjectionError, match="^sheet_canonical_mismatch$"):
        orders._build_canonical_payload_row_mapping(
            rows=targets, fields=FIELDS, payload_rows=source,
            template={"fill_forward_roles": ["date", "daypart"]})
    assert (source, targets) == before
