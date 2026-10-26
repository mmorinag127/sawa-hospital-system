"""Owned SQLite/service and in-process HTTP boundaries with manually seeded OCR.

Not recognizer/native OCR, production auth, formal schema, startup/lifespan,
operator FAX, PG or live proof. The actual app is used without lifespan; the
fixture's separate output-only app and its dependency overrides are not used.
"""

from copy import deepcopy

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from test_modernization_explicit_ocr_quantity_contract import PublicSavedCandidate
from src import db
from src.models.document import OrderDocument
from src.models.order import Order
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.uploaded_pdf import UploadedPdf
from src.services import order_workflow_v2_service as workflow
from src.services import output_builder
from src.services import order_service as orders
from src.services.workflow_attempt_guard_service import (
    SelectedPdfReceiptError, capture_selected_pdf_receipt,
)


@pytest.fixture
def saved_receipt(tmp_path, monkeypatch):
    fixture = PublicSavedCandidate(tmp_path, monkeypatch)
    fixture.save(["7", "", "", "0"], enabled=True)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    try:
        yield fixture
    finally:
        fixture.engine.dispose()


def database_snapshot(fixture):
    with fixture.session_scope() as session:
        return {
            table.name: deepcopy([
                dict(row) for row in session.execute(
                    select(table).order_by(*table.primary_key.columns),
                ).mappings()
            ]) for table in db.Base.metadata.sorted_tables
        }


FAULTS = [
    ("missing-current-document", "selected_pdf_document_required"),
    ("unowned-document", "selected_pdf_document_required"),
    ("uri-mismatch", "selected_pdf_document_required"),
    ("empty-owned-uri", "selected_pdf_input_mismatch"),
    ("missing-digest", "selected_pdf_digest_required"),
    ("invalid-digest", "selected_pdf_digest_required"),
    ("invalid-generation", "selected_pdf_generation_required"),
    ("duplicate-generation", "selected_pdf_generation_required"),
]


def author_invalid_pdf_state(fixture, fault):
    with fixture.session_scope() as session:
        order = session.get(Order, fixture.order_id)
        document = session.get(OrderDocument, "UNIT-DOC")
        uploaded = session.get(UploadedPdf, "UNIT-PDF")
        if fault == "missing-current-document":
            order.current_document_id = None
        elif fault == "unowned-document":
            document.source_email_id = "ORD-FOREIGN"
            uploaded.message_id = "ORD-FOREIGN"
        elif fault == "uri-mismatch":
            order.document_uri = str(fixture.root / "different-unselected.pdf")
        elif fault == "empty-owned-uri":
            order.document_uri = document.storage_uri = uploaded.storage_uri = ""
        elif fault in {"missing-digest", "invalid-digest"}:
            # The real schema forbids NULL; an empty digest is a legal stored
            # row that the actual receipt validator must explicitly reject.
            uploaded.content_sha256 = "" if fault == "missing-digest" else "not-a-sha256"
        elif fault in {"invalid-generation", "duplicate-generation"}:
            query = "generation=invalid" if fault == "invalid-generation" else "generation=1&generation=2"
            uri = f"gs://synthetic-unit-not-live/owned.pdf?{query}"
            order.document_uri = document.storage_uri = uploaded.storage_uri = uri
        else:
            raise AssertionError(f"Unknown authored fault: {fault}")


def read_resolution(fixture):
    with fixture.session_scope() as session:
        return workflow._workflow_menu_amount_resolution(
            session, session.get(Order, fixture.order_id),
            session.get(OrderWorkflowState, fixture.order_id),
        )


def http_workflow(fixture):
    from src.main import app

    # Deliberately no context manager/lifespan or formal-schema receipt.
    client = TestClient(app)
    try:
        response = client.get(f"/orders/{fixture.order_id}/workflow-v2")
    finally:
        client.close()
    assert response.status_code == 200, response.text
    return response.json()


def test_valid_saved_quantities_and_readonly_http_resolution_are_unchanged(saved_receipt):
    f = saved_receipt
    original = f.saved()
    before = database_snapshot(f)
    with f.session_scope() as session:
        order = session.get(Order, f.order_id)
        assert capture_selected_pdf_receipt(session, order) == f.receipt
        candidate = workflow._build_materialization_candidate_for_saved_sheet(
            session=session, order=order,
            saved_sheet=session.get(OrderSheetDraft, f.draft_id),
        )
    assert candidate["error"] is None
    assert [(line["source_row_index"], line["quantity_original"]) for line in candidate["lines"]] == [(0, 7), (3, 0)]
    resolution = read_resolution(f)
    assert resolution["status"] == "resolved"
    assert resolution["affected_lines"] == []
    public = http_workflow(f)
    assert public["menu_amount_resolution"] == resolution
    assert f.saved() == original
    assert [row[4] for row in f.saved()["rows"]] == ["7", "", "", "0"]
    assert database_snapshot(f) == before


def test_resolution_shares_analysis_without_skipping_capture_validation(saved_receipt, monkeypatch):
    from src.services.canonical_ocr_artifact_service import _EvidenceAnalysis

    f = saved_receipt
    expected = read_resolution(f)
    before = database_snapshot(f)
    analysis = _EvidenceAnalysis()
    calls = []
    original_select = orders.ocr_evidence_service.evidence_run_selectability_error
    original_serialize = orders.ocr_evidence_service._serialize_evidence_run

    def select(row, **kwargs):
        calls.append(("select", kwargs.get("_analysis")))
        return original_select(row, **kwargs)

    def serialize(row, **kwargs):
        calls.append(("serialize", kwargs.get("_analysis")))
        return original_serialize(row, **kwargs)

    monkeypatch.setattr(orders.ocr_evidence_service, "evidence_run_selectability_error", select)
    monkeypatch.setattr(orders.ocr_evidence_service, "_serialize_evidence_run", serialize)
    with f.session_scope() as session:
        actual = workflow._workflow_menu_amount_resolution(
            session, session.get(Order, f.order_id),
            session.get(OrderWorkflowState, f.order_id), _analysis=analysis,
        )
    assert actual == expected
    assert any(name == "select" for name, _ in calls)
    assert any(name == "serialize" for name, _ in calls)
    assert all(value is analysis for _, value in calls)
    assert database_snapshot(f) == before


def test_read_candidate_omits_write_snapshots_but_preserves_output_and_rejects_writes(saved_receipt, monkeypatch):
    from src.services import workflow_attempt_guard_service as guard, ocr_revision_store, draft_sheet_service

    f = saved_receipt
    before = database_snapshot(f)
    with f.session_scope() as session:
        kwargs = dict(session=session, order=session.get(Order, f.order_id),
                      saved_sheet=session.get(OrderSheetDraft, f.draft_id))
        expected = workflow._build_materialization_candidate_for_saved_sheet(**kwargs)

        def forbidden(*args, **kwargs):
            raise AssertionError("read projection built a complete execution/write snapshot")

        monkeypatch.setattr(orders, "synchronous_input_versions", forbidden)
        monkeypatch.setattr(guard, "_order_execution_input", forbidden)
        actual = workflow._build_materialization_candidate_for_saved_sheet(
            **kwargs, _for_read_projection=True,
        )
        proof = actual.pop("synchronous_write_input")
        expected.pop("synchronous_write_input")
        assert actual == expected
        assert proof["read_projection_only"] is True
        assert proof["input_versions"] is None
        assert orders._synchronous_cache_write_fence(session, f.order_id, proof) is False
        with pytest.raises(ValueError, match="revision_write_ownership_conflict"):
            ocr_revision_store.append_revision(
                f.order_id, {"revision_id": "read-proof-must-not-write",
                             "base_evidence_run_id": proof["evidence_run_id"]},
                session=session, synchronous_write_input=proof,
            )
        assert draft_sheet_service.persist_sheet_draft(
            order_id=f.order_id, draft_sheet_json=f.saved(),
            base_evidence_run_id=proof["evidence_run_id"],
            synchronous_write_input=proof, write_session=session,
        ) is None
    assert database_snapshot(f) == before


@pytest.mark.parametrize("fault,code", FAULTS)
def test_invalid_receipt_is_typed_unresolved_and_readonly_without_old_lines(saved_receipt, fault, code):
    f = saved_receipt
    # Prove the complete valid source/candidate before introducing this fault.
    assert read_resolution(f)["status"] == "resolved"
    original = f.saved()
    author_invalid_pdf_state(f, fault)
    before = database_snapshot(f)
    with f.session_scope() as session:
        with pytest.raises(SelectedPdfReceiptError) as raised:
            capture_selected_pdf_receipt(session, session.get(Order, f.order_id))
        assert isinstance(raised.value, ValueError)
        assert str(raised.value) == code
    expected = {"status": "unresolved", "blocker": code, "message": code, "affected_lines": []}
    assert read_resolution(f) == expected
    public = http_workflow(f)
    assert public["menu_amount_resolution"] == expected
    assert code in public["blockers"]
    assert public["state"] != "confirmed"
    assert f.saved() == original
    assert database_snapshot(f) == before


@pytest.mark.parametrize("fault,code", FAULTS)
def test_materialization_candidate_retains_typed_receipt_exception(saved_receipt, fault, code):
    f = saved_receipt
    assert read_resolution(f)["status"] == "resolved"
    author_invalid_pdf_state(f, fault)
    before = database_snapshot(f)
    with f.session_scope() as session:
        with pytest.raises(SelectedPdfReceiptError) as raised:
            workflow._build_materialization_candidate_for_saved_sheet(
                session=session, order=session.get(Order, f.order_id),
                saved_sheet=session.get(OrderSheetDraft, f.draft_id),
            )
        assert str(raised.value) == code
    with pytest.raises(SelectedPdfReceiptError) as raised:
        orders.build_confirm_materialization_candidate(f.order_id)
    assert str(raised.value) == code
    assert database_snapshot(f) == before


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_unexpected_external_builder_error_is_not_converted_to_receipt_blocker(saved_receipt, monkeypatch, error_type):
    f = saved_receipt
    assert read_resolution(f)["status"] == "resolved"
    before = database_snapshot(f)
    failure = error_type("synthetic external menu metadata builder failure")

    def fail_external_builder(*args, **kwargs):
        raise failure

    # Only this negative exception-propagation case injects an external failure;
    # capture, ownership, lineage and materialization guards remain actual code.
    monkeypatch.setattr(output_builder, "_build_ocr_menu_meta", fail_external_builder)
    with pytest.raises(error_type) as raised:
        read_resolution(f)
    assert raised.value is failure
    assert database_snapshot(f) == before
