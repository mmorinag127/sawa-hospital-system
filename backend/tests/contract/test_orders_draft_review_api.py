"""Retired workflow APIs refuse writes; current positive mapping is documented in
legacy_workflow_migration.md. These are authored components, not live FAX proof.
"""
from copy import deepcopy
import pytest
from src.db import session_scope
from src.models.order import Order, OrderLine
from src.models.output import Bag, DeliveryNote, LabelRow
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from tests.contract.test_orders_ocr_sheet_history_api import history_case, _sheet, LEGACY  # noqa: F401


def _refuse(f, method, endpoint, body):
    with f.readonly():
        response = f.client.request(method, f"/orders/{f.id}/{endpoint}",
            json=body, headers=f.actor.headers)
    assert response.status_code == 410
    assert response.json() == {"detail": {**LEGACY, "endpoint": endpoint}}


RETIRED_CASES = [
    ("refuse_draft_read_without_legacy_menu_resolution", "test_order_detail_and_draft_sheet_expose_same_blocker_reason_for_missing_menu", "GET", "draft-sheet", None),
    ("refuse_legacy_workflow_state_read", "test_order_endpoints_expose_draft_ready_state_from_saved_sheet_and_reject_reason", "GET", "workflow-state", None),
    ("refuse_legacy_confirm_latest_sheet", "test_confirm_endpoint_uses_latest_draft_without_draft_newer_block", "POST", "confirm", None),
    ("refuse_legacy_save_stale_revision", "test_ocr_sheet_save_returns_stale_revision_conflict_for_outdated_revision", "POST", "ocr-sheet-save", {"expected_revision_id": "stale", "rows": [["02/15", "朝", "Menu A", "9", "draft-stale"]]}),
    ("refuse_legacy_line_write_stale_digest", "test_update_lines_returns_stale_lines_conflict_when_lines_changed_elsewhere", "PUT", "lines", {"expected_lines_digest": "stale", "lines": [{"quantity_original": 7}]}),
    ("refuse_legacy_apply_stale_digest", "test_ocr_apply_returns_stale_lines_conflict_after_remote_line_update", "POST", "ocr-apply", {"expected_lines_digest": "stale", "rows": [["02/15", "朝", "Menu A", "8", "apply"]]}),
    ("refuse_legacy_apply_saved_sheet", "test_ocr_apply_clears_saved_draft_state_after_successful_apply", "POST", "ocr-apply", {"rows": [["02/15", "朝", "Menu A", "9", "saved"]]}),
    ("refuse_legacy_save_after_apply", "test_ocr_sheet_save_returns_stale_lines_conflict_after_apply_changes_lines", "POST", "ocr-sheet-save", {"expected_lines_digest": "old", "rows": [["02/15", "朝", "Menu A", "9", "after-apply"]]}),
    ("refuse_legacy_confirm_stale_revision", "test_confirm_returns_stale_revision_conflict_when_draft_changed_elsewhere", "POST", "confirm", {"expected_revision_id": "stale"}),
    ("refuse_legacy_confirm_background_dispatch", "test_confirm_route_defers_post_confirm_side_effects_to_background", "POST", "confirm", None),
    ("refuse_legacy_confirm_cached_revision", "test_confirm_uses_authoritative_current_sheet_revision_not_stale_cached_payload", "POST", "confirm", {"expected_revision_id": "cached"}),
    ("refuse_legacy_facility_write_stale_digest", "test_facility_and_week_updates_return_stale_conflicts", "POST", "facility", {"facility": "FAC00002", "expected_lines_digest": "stale"}),
    ("refuse_legacy_week_write", "test_week_save_conflict_guard_accepts_current_promoted_week_value", "POST", "week", {"week": "2026-02@2026-02-16~2026-02-22"}),
    ("refuse_legacy_ocr_sheet_read", "test_order_endpoints_expose_reparse_stage_and_retained_lines_for_recoverable_result", "GET", "ocr-sheet", None),
    ("refuse_legacy_reparse_retry", "test_stale_reparse_job_is_marked_failed_and_allows_retry", "POST", "reparse", {"ocr_provider": "gemini", "stale_action": "wait"}),
    ("refuse_legacy_draft_history_read", "test_persisted_ocr_revision_history_survives_missing_cache_payload", "GET", "draft-sheet", None),
]


def test_retired_draft_review_table_refuses_without_any_side_effect(history_case):
    f = history_case()
    f.produce()
    saved = f.save(_sheet("8", "operator-edited", "retained-row"))["saved_sheet"]
    for case_id, _old_name, method, endpoint, body in RETIRED_CASES:
        _refuse(f, method, endpoint, body)
        assert f.get("workflow-v2/sheet")["sheet"] == saved["sheet"], case_id


def test_current_sheet_save_keeps_latest_operator_values_and_rejects_stale_revision(history_case):
    f = history_case()
    f.produce()
    previous = f.revision()
    first = f.save(_sheet("8", "first", "row-first"))["saved_sheet"]
    second = f.save(_sheet("9", "latest", "row-latest"))["saved_sheet"]
    assert first["sheet"]["rows"][0][3] == "8"
    assert second["sheet"]["rows"][0][3] == "9"
    with f.readonly():
        response = f.client.put(f"/orders/{f.id}/workflow-v2/sheet",
            headers={**f.actor.headers, "If-Match": f'"{previous}"'},
            json={"sheet": _sheet("11", "stale", "row-stale")})
    assert response.status_code == 409
    assert f.get("workflow-v2/sheet")["sheet"] == second["sheet"]
    with session_scope() as session:
        draft = session.get(OrderSheetDraft, second["saved_sheet_id"])
        assert draft.draft_sheet_json == second["sheet"]


@pytest.mark.parametrize("valid_template", [False, True])
def test_explicit_same_evidence_reselect_invalidates_only_mismatched_saved_selection(history_case, valid_template):
    f = history_case()
    f.produce()
    saved = f.save(_sheet("9", "operator-retained", "retained-row"))["saved_sheet"]
    with session_scope() as session:
        draft = session.get(OrderSheetDraft, saved["saved_sheet_id"])
        if not valid_template:
            draft.template_version_id = None  # Deliberate identity corruption, not an operator edit.
        archived_sheet = deepcopy(draft.draft_sheet_json)
        evidence = deepcopy(session.get(OrderOcrEvidenceRun, f.evidence).payload_json)
        facility = session.get(Order, f.id).facility_code
        session.add(OrderLine(id="derived-line", order_id=f.id, quantity_original=9, quantity_corrected=9))
        session.add(Bag(id="derived-bag", order_id=f.id, quantity=9, source_saved_sheet_id=draft.id))
        session.flush()
        session.add(LabelRow(id="derived-label", order_id=f.id, bag_id="derived-bag", payload_json={"quantity": 9}))
        session.add(DeliveryNote(id="derived-delivery", order_id=f.id, facility_code=facility))
    response = f.client.post(f"/orders/{f.id}/workflow-v2/ocr-results/{f.evidence}/select",
        headers={**f.actor.headers, "If-Match": str(f.revision())})
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["selected_ocr_result_id"] == f.evidence
    assert current["saved_sheet_id"] == (saved["saved_sheet_id"] if valid_template else None)
    with session_scope() as session:
        assert session.get(OrderSheetDraft, saved["saved_sheet_id"]).draft_sheet_json == archived_sheet
        assert session.get(OrderOcrEvidenceRun, f.evidence).payload_json == evidence
        for model in (OrderLine, Bag, LabelRow, DeliveryNote):
            assert session.query(model).filter_by(order_id=f.id).count() == int(valid_template)
