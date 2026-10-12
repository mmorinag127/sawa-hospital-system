"""Stored legacy metadata compatibility, not force-write or OCR acceptance proof.

The existing surface fixture uses owned SQLite/PDF/dated JSON and a manually
seeded selection. Its local auth bypass is component-only; the separate hard-410
contracts exercise registered operator authentication. No recognizer is invoked.
"""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from test_modernization_workflow_surface_parity import database_snapshot, get_json, surface
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.services import order_service as orders


@pytest.mark.parametrize("mode,quantity,nested", [
    pytest.param("forced_weekly_menu_overwrite", "7", False, id="weekly-quantity-7"),
    pytest.param("forced_weekly_menu_overwrite", "", True, id="weekly-blank"),
    pytest.param("forced_facility_schema_overwrite", "", False, id="schema-blank"),
    pytest.param("forced_facility_schema_overwrite", "9", True, id="schema-quantity-9"),
    pytest.param(None, "8", False, id="saved-quantity-8"),
])
def test_stored_historical_sheet_keeps_quantities_metadata_and_menu_on_read(surface, mode, quantity, nested):
    f, client = surface
    saved = f.saved()
    saved["rows"][0][3:5] = ["Menu A", quantity]
    saved["warnings"] = [mode] if mode else []
    if mode:
        saved["repair_metadata"] = {"mode": mode, "blank_quantities": quantity == "", "origin": "operator"}
        if not nested:
            saved["repair_mode"] = mode
        saved["source"] = "forced_weekly_menu" if mode == "forced_weekly_menu_overwrite" else "forced_facility_schema"
        saved["enrichment_source"] = "operator_forced_repair"
        if quantity == "":
            saved["warnings"].append("forced_quantity_manual_entry_required")
    else:
        saved["source"] = "draft_ready"
    # These are explicitly stored historical rows, not a fabricated producer
    # success. A different selected evidence payload must not rewrite the draft.
    competing = deepcopy(saved)
    competing["rows"][0][3:5] = ["WRONG", "99"]
    with f.session_scope() as session:
        draft = session.get(OrderSheetDraft, f.draft_id)
        draft.draft_sheet_json = deepcopy(saved)
        draft.warnings_json = list(saved["warnings"])
        evidence = session.get(OrderOcrEvidenceRun, f.evidence_id)
        evidence.payload_json = {"tables": [{"rows": competing["rows"]}]}
        evidence.artifact_digest = sha256(json.dumps(evidence.payload_json).encode()).hexdigest()
    before = database_snapshot()
    sources = {path: path.read_bytes() for path in (f.master_path, f.source, f.pdf)}
    current = orders.get_current_sheet_context(f.order_id)
    assert current["draft_id"] == f.draft_id
    assert current["base_evidence_run_id"] == f.evidence_id
    assert current["rows"][0][3:5] == ["Menu A", quantity]
    assert current["draft_payload"] == saved
    assert current["repair_mode"] == mode
    assert current["warnings"] == saved["warnings"]
    if mode:
        captured = orders._capture_synchronous_cache_write_input(f.order_id)
        assert captured["draft"]["draft_sheet_json"] == saved
        assert orders._draft_record_requires_current_sheet_semantic_rebase(f.order_id, captured["draft"]) == (False, None)
        assert orders._maybe_refresh_semantic_sheet_draft(f.order_id, captured["draft"]) == captured["draft"]
        assert orders._rebase_draft_record_to_facility_schema(
            f.order_id, captured["draft"], edited_by="current-sheet-refresh", synchronous_write_input=captured,
        ) == captured["draft"]
    sheet, error = orders.get_ocr_sheet(f.order_id)
    assert error is None
    assert sheet["rows"][0][3:5] == ["Menu A", quantity]
    assert sheet.get("repair_mode") == saved.get("repair_mode")
    assert sheet.get("repair_metadata") == (saved["repair_metadata"] if mode else {})
    assert set(saved["warnings"]) <= set(sheet["warnings"])
    public = get_json(client, f"/orders/{f.order_id}/workflow-v2/sheet")
    assert public["saved_sheet_id"] == f.draft_id
    assert public["source_ocr_result_id"] == f.evidence_id
    assert public["sheet"] == saved
    assert f.saved() == saved
    assert database_snapshot() == before
    assert {path: path.read_bytes() for path in sources} == sources
