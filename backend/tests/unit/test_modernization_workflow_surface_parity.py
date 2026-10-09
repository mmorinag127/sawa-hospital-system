"""Owned projection/API unit boundaries with manually seeded OCR selection.

Actual app/routes/read-only middleware and SQLite/PDF/dated JSON are used.
Local AUTH_DISABLED and no TestClient lifespan: not native recognition,
operator flow, startup/formal schema, production authentication, PG or live
proof. Normal sheet-source generation requires native artifacts and is excluded.
Persisted Order.lines are not an unconfirmed draft's output quantities.
"""

from copy import deepcopy

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from test_modernization_explicit_ocr_quantity_contract import PublicSavedCandidate
from src import db
from src.models.document import OrderDocument
from src.models.order import Order
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_version import OrderVersion
from src.services import order_service as orders
from src.services.workflow_attempt_guard_service import selected_version_snapshot


@pytest.fixture
def surface(tmp_path, monkeypatch):
    fixture = PublicSavedCandidate(tmp_path, monkeypatch)
    fixture.save(["7", "", "", "0"], enabled=True)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    with fixture.session_scope() as session:
        orders._record_current_order_version(
            session, session.get(Order, fixture.order_id),
            session.get(OrderDocument, "UNIT-DOC"),
        )
    from src.main import app

    # The fixture's separate output app/dependency overrides are never used.
    client = TestClient(app)
    try:
        yield fixture, client
    finally:
        client.close()
        fixture.close()


def database_snapshot():
    with db.session_scope() as session:
        return {
            table.name: deepcopy([
                dict(row) for row in session.execute(
                    select(table).order_by(*table.primary_key.columns),
                ).mappings()
            ]) for table in db.Base.metadata.sorted_tables
        }


def get_json(client, path, status=200):
    response = client.get(path)
    assert response.status_code == status, response.text
    return response.json()


def read_surfaces(fixture, client):
    root = f"/orders/{fixture.order_id}"
    return (
        get_json(client, root), get_json(client, root + "/workflow-v2"),
        get_json(client, root + "/workflow-v2/sheet"),
        get_json(client, root + "/workflow-v2/ocr-results"),
    )


def assert_saved_identity(fixture, sheet, original):
    assert sheet["order_id"] == fixture.order_id
    assert sheet["saved_sheet_id"] == fixture.draft_id
    assert sheet["template_version_id"] == fixture.projection_id
    assert sheet["source_ocr_result_id"] == fixture.evidence_id
    assert sheet["sheet"] == original
    assert [row[4] for row in sheet["sheet"]["rows"]] == ["7", "", "", "0"]


def assert_no_output_candidate(fixture, error):
    candidate = orders.build_confirm_materialization_candidate(fixture.order_id)
    assert candidate == {"error": error, "lines": []}


def test_valid_saved_raw_quantities_and_selected_identity_agree_across_read_surfaces(surface):
    f, client = surface
    original = f.saved()
    before = database_snapshot()
    detail, workflow, sheet, results = read_surfaces(f, client)
    assert detail["id"] == workflow["order_id"] == results["order_id"] == f.order_id
    assert detail["template_version_id"] == workflow["template_version_id"] == f.projection_id
    assert detail["document_id"] == "UNIT-DOC"
    assert len(detail["versions"]) == 1
    assert detail["current_version"] == detail["versions"][0]
    with f.session_scope() as session:
        version = session.execute(
            select(OrderVersion).where(OrderVersion.order_id == f.order_id),
        ).scalar_one()
        assert detail["current_version"]["selected_version_input"] == selected_version_snapshot(version)
    # No persisted lines exist in this unconfirmed fixture; GET must not
    # manufacture them from the working draft or the newer unselected draft.
    assert before["order_lines"] == []
    assert detail["lines"] == []
    assert workflow["selected_ocr_result_id"] == results["selected_ocr_result_id"] == f.evidence_id
    assert workflow["saved_sheet_id"] == f.draft_id
    assert workflow["confirmed_snapshot_id"] is None
    assert workflow["menu_amount_resolution"] == {
        "status": "resolved", "blocker": None, "message": None, "affected_lines": [],
    }
    assert_saved_identity(f, sheet, original)
    assert results["candidate_template_version_id"] == f.projection_id
    assert results["hidden_template_mismatch_result_count"] == 0
    assert len(results["results"]) == 1
    evidence = results["results"][0]
    assert evidence["ocr_result_id"] == f.evidence_id
    assert evidence["order_id"] == f.order_id
    assert evidence["template_version_id"] == f.projection_id
    assert evidence["selected"] is True and evidence["selectable"] is True
    assert evidence["status"] == "ready"
    candidate = orders.build_confirm_materialization_candidate(f.order_id)
    assert candidate["error"] is None and candidate["draft_id"] == f.draft_id
    assert [(line["source_row_index"], line["quantity_original"])
            for line in candidate["lines"]] == [(0, 7), (3, 0)]
    assert f.saved() == original
    assert database_snapshot() == before


def test_saved_sheet_version_mismatch_blocks_reads_and_never_uses_newer_draft(surface):
    f, client = surface
    original = f.saved()
    with f.session_scope() as session:
        session.get(OrderSheetDraft, f.draft_id).template_version_id = None
    before = database_snapshot()
    root = f"/orders/{f.order_id}/workflow-v2"
    assert get_json(client, root + "/sheet", 400) == {"detail": "saved_sheet_template_mismatch"}
    workflow = get_json(client, root)
    assert workflow["state"] == "saved_sheet_template_mismatch"
    assert workflow["saved_sheet_id"] == f.draft_id
    assert "saved_sheet_template_mismatch" in workflow["blockers"]
    assert workflow["menu_amount_resolution"] == {
        "status": "unresolved", "blocker": "saved_sheet_template_mismatch",
        "message": "saved_sheet_template_mismatch", "affected_lines": [],
    }
    assert_no_output_candidate(f, "synchronous_input_missing")
    assert f.saved() == original
    assert database_snapshot() == before


@pytest.mark.parametrize("fault,blocker", [
    ("foreign-owner", "selected_ocr_missing"),
    ("failed", "ocr_result_execution_lineage_mismatch"),
    ("error", "ocr_result_execution_lineage_mismatch"),
])
def test_invalid_selected_evidence_is_not_success_or_output_but_owned_failure_stays_visible(
    surface, fault, blocker,
):
    f, client = surface
    original = f.saved()
    with f.session_scope() as session:
        evidence = session.get(OrderOcrEvidenceRun, f.evidence_id)
        if fault == "foreign-owner":
            evidence.order_id = "ORD-FOREIGN"
        else:
            evidence.status = fault
    before = database_snapshot()
    detail, workflow, sheet, results = read_surfaces(f, client)
    assert detail["lines"] == []
    assert workflow["selected_ocr_result_id"] == f.evidence_id
    assert blocker in workflow["blockers"]
    assert workflow["confirmed_snapshot_id"] is None
    assert workflow["menu_amount_resolution"] == {
        "status": "unresolved", "blocker": "synchronous_input_conflict",
        "message": "synchronous_input_conflict", "affected_lines": [],
    }
    assert_saved_identity(f, sheet, original)
    assert results["selected_ocr_result_id"] == f.evidence_id
    assert blocker in results["blockers"]
    if fault == "foreign-owner":
        assert results["results"] == []
    else:
        assert len(results["results"]) == 1
        evidence = results["results"][0]
        assert evidence["ocr_result_id"] == f.evidence_id
        assert evidence["order_id"] == f.order_id
        assert evidence["status"] == fault
        assert evidence["selected"] is True
        assert evidence["selectable"] is False
    assert_no_output_candidate(f, "synchronous_input_missing")
    assert f.saved() == original
    assert database_snapshot() == before


@pytest.mark.parametrize("fault", ["missing-current-document", "foreign-document"])
def test_retired_pdf_blocks_output_without_repairing_saved_sheet_or_evidence_identity(surface, fault):
    f, client = surface
    original = f.saved()
    with f.session_scope() as session:
        if fault == "missing-current-document":
            session.get(Order, f.order_id).current_document_id = None
        else:
            session.get(OrderDocument, "UNIT-DOC").source_email_id = "ORD-FOREIGN"
    before = database_snapshot()
    detail, workflow, sheet, results = read_surfaces(f, client)
    assert detail["document_id"] == (None if fault == "missing-current-document" else "UNIT-DOC")
    assert detail["lines"] == []
    assert workflow["selected_ocr_result_id"] == results["selected_ocr_result_id"] == f.evidence_id
    assert workflow["saved_sheet_id"] == f.draft_id
    assert "selected_pdf_document_required" in workflow["blockers"]
    assert workflow["menu_amount_resolution"] == {
        "status": "unresolved", "blocker": "selected_pdf_document_required",
        "message": "selected_pdf_document_required", "affected_lines": [],
    }
    assert_saved_identity(f, sheet, original)
    assert len(results["results"]) == 1
    assert results["results"][0]["ocr_result_id"] == f.evidence_id
    assert results["results"][0]["order_id"] == f.order_id
    with pytest.raises(ValueError, match="^selected_pdf_document_required$"):
        orders.build_confirm_materialization_candidate(f.order_id)
    assert f.saved() == original
    assert database_snapshot() == before


@pytest.mark.parametrize("endpoint", ["ocr-sheet", "draft-sheet", "workflow-state"])
def test_retired_legacy_surface_returns_exact_410_without_state_or_quantity_writes(surface, endpoint):
    f, client = surface
    before = database_snapshot()
    assert get_json(client, f"/orders/{f.order_id}/{endpoint}", 410) == {
        "detail": {
            "error": "legacy_order_workflow_disabled", "endpoint": endpoint,
            "message": "注文処理は workflow-v2 に移行しました。この旧 order workflow endpoint は使用できません。",
            "replacement": "workflow-v2",
        },
    }
    assert database_snapshot() == before
