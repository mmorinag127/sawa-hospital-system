from copy import deepcopy

import pytest

from src.services import apply_gate_service


@pytest.mark.parametrize("source", ["draft_sheet", "ocr_table", None])
@pytest.mark.parametrize("ready", [False, True])
def test_warning_normalization_preserves_review_and_blocking_tokens(source, ready):
    warnings = ["", " numeric_trust_low ", "numeric_trust_low", "quantity_review_required"]
    normalized = apply_gate_service.filter_stale_issue_tokens(
        warnings, source=source, clean_saved_draft=ready,
        position_fallback_semantics_ready=ready, stale_authoritative_sheet=ready,
    )
    assert normalized == ["numeric_trust_low", "quantity_review_required"]
    assert apply_gate_service.gate_requires_human_review(apply_warnings=normalized)
    assert not apply_gate_service.gate_can_apply(apply_blockers=[], apply_warnings=normalized)
    assert not apply_gate_service.gate_can_confirm(confirm_blockers=[], confirm_warnings=normalized)
    sheet_gate = apply_gate_service.evaluate_sheet_gate(
        rows=[["10/01", "朝", "Fish", "2"]], source=source, has_semantic_fields=True,
        blockers=["template_unresolved", " template_unresolved "],
        warnings=["sheet_quantity_column_unmapped", "sheet_quantity_column_unmapped"],
        clean_saved_draft=ready, position_fallback_semantics_ready=ready,
        authoritative_persisted_draft=ready,
    )
    assert sheet_gate["apply_blockers"] == ["template_unresolved", "sheet_quantity_column_unmapped"]
    assert sheet_gate["confirm_blockers"] == sheet_gate["apply_blockers"]


def test_draft_newer_than_lines_allows_apply_but_blocks_confirm() -> None:
    sheet_gate = apply_gate_service.evaluate_sheet_gate(
        rows=[["04/26", "朝", "Menu A", "1"]],
        source="hakodate_ocr_evidence_sheet",
        has_semantic_fields=True,
        blockers=[],
        warnings=[],
        draft_newer_than_lines=True,
    )
    apply_gate = apply_gate_service.evaluate_apply_gate(
        order_payload={"facility": "FAC00001", "week_value": "2026-04@2026-04-26~2026-04-30"},
        evidence_run={"capabilities_json": {"step2_view_ready": True, "step2_edit_ready": True}},
        draft_sheet={
            "source": "hakodate_ocr_evidence_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular"],
            "rows": [["04/26", "朝", "Menu A", "1"]],
        },
        candidate_resolution={"resolutions": {}},
        menu_context={},
        sheet_gate=sheet_gate,
    )

    assert apply_gate["can_apply"] is True
    assert apply_gate["can_confirm"] is False
    assert "draft_newer_than_lines" in apply_gate["confirm_blockers"]
    assert "draft_newer_than_lines" in apply_gate["warnings"]


@pytest.mark.parametrize("changes,expected", [
    ({}, True),
    ({"warnings_json": ["llm_patch_applied"]}, True),
    ({"warnings_json": ["hakodate_ocr_evidence_assignment"]}, True),
    ({"draft_sheet_json": {"rows": [["0"]], "warnings": ["hakodate_ocr_evidence_assignment"]}}, True),
    ({"warnings_json": ["hakodate_ocr_evidence_assignment", "unknown_warning"]}, False),
    ({"draft_sheet_json": {"rows": [["0"]], "warnings": ["unknown_warning"]}}, False),
    ({"warnings_json": ["hakodate_ocr_evidence_assignment_unknown"]}, False),
    ({"warnings_json": ["hakodate_ocr_evidence_assignment"], "blockers_json": ["sheet_ocr_review_required"]}, False),
    ({"draft_sheet_json": {"rows": []}}, False),
    ({"draft_sheet_json": {}}, False),
    ({"id": ""}, False),
    (None, False),
])
def test_saved_draft_provenance_is_not_a_validation_warning(changes, expected):
    draft = None if changes is None else {
        "id": "saved", "draft_sheet_json": {"rows": [["0"]]},
        "warnings_json": [], "blockers_json": [], **changes,
    }
    original = deepcopy(draft)
    assert apply_gate_service.has_clean_saved_draft(draft) is expected
    assert draft == original
