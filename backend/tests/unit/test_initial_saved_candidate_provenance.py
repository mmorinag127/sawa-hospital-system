"""Formal SQLite candidate persistence; no recognizer or live-parity claim."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from copy import deepcopy

import pytest
from sqlalchemy.orm import Session

from test_initial_saved_import_service import editable_import
from test_initial_saved_source_service import saved
from src.models.order import Order
from src.services import initial_saved_import_service as imports
from src.services import order_workflow_v2_service as workflow
from src.services import workflow_ocr_delivery_service as delivery
from src.services import workflow_attempt_guard_service as guard
from src.services import ocr_evidence_service as evidence


@pytest.fixture
def claimed_import(editable_import, monkeypatch):
    session, sheet, _ = editable_import
    for name, value in {"PROJECT_ID": "synthetic", "LOCATION": "synthetic", "QUEUE": "synthetic",
                        "TARGET_URL": "https://synthetic.invalid", "SERVICE_ACCOUNT_EMAIL": "worker@synthetic.iam.gserviceaccount.com",
                        "AUDIENCE": "https://synthetic.invalid", "DISPATCH_DEADLINE_SECONDS": "600"}.items():
        monkeypatch.setenv("HOSPITAL_WORKFLOW_OCR_DELIVERY_" + name, value)
    @contextmanager
    def delivery_scope():
        with Session(session.bind) as writer:
            try:
                yield writer
                writer.commit()
            except BaseException:
                writer.rollback()
                raise
    monkeypatch.setattr(delivery, "session_scope", delivery_scope)
    monkeypatch.setattr(guard, "session_scope", delivery_scope)
    queued, error = workflow.mark_ocr_run_queued("O", "NEW-JOB", input_reference=session.get(Order, "O").document_uri,
                                               request_mode="ocr_rerun", expected_revision=1)
    assert error is None, error
    session.rollback()
    claim = delivery.claim_delivery(queued["delivery"]["id"], 1, lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
    assert not claim.get("duplicate")
    return session, sheet, claim


def test_saved_candidate_persistence_uses_verified_frozen_template_without_adoption(claimed_import):
    session, sheet, claim = claimed_import
    before = deepcopy(imports.get_binding(session, "O"))
    with delivery.claim_context(claim), guard.workflow_execution_context("NEW-JOB", "O", claim["workflow_attempt_id"], claim["workflow_revision"]):
        assert guard.lock_current_execution_write(session, "O")
        created = evidence.persist_evidence_run("O", {"engine": "synthetic-source", "raw": "unadopted native provenance case"},
            template_version_id="T", status="diagnostic_only", source="native-owned-source", producer_version="native-provenance/v1",
            workflow_job_id="NEW-JOB", workflow_attempt_id=claim["workflow_attempt_id"], expected_workflow_revision=claim["workflow_revision"],
            write_session=session)
        assert created is not None and created["created"]
        assert created["id"] != before["snapshot"]["evidence"]["id"]
        assert created["template_version_id"] == "T"
        session.commit()
    after = imports.get_binding(session, "O")
    assert after["configuration"] == before["configuration"]
    for key in ("draft", "evidence", "template", "confirmed", "bagging", "output", "lines"):
        assert after["snapshot"][key] == before["snapshot"][key]
    assert after["snapshot"]["workflow"]["evidence_run_id"] == before["snapshot"]["workflow"]["evidence_run_id"]


@pytest.mark.parametrize("fault", ["receipt", "import", "config", "lineage", "proof", "capture-template", "physical-template", "saved-quantity", "declared-null", "foreign-template"])
def test_saved_candidate_template_validation_rejects_changed_source(claimed_import, fault):
    from src.models.facility_template_version import FacilityTemplateVersion
    from src.models.order_sheet_draft import OrderSheetDraft
    session, _, claim = claimed_import
    before = deepcopy(imports.get_binding(session, "O"))
    with delivery.claim_context(claim), guard.workflow_execution_context("NEW-JOB", "O", claim["workflow_attempt_id"], claim["workflow_revision"]):
        assert guard.lock_current_execution_write(session, "O")
        captured = guard.processing_start_read_input()
        template_id = "T"
        if fault == "receipt": captured["initial_saved_source_receipt"]["source_sha256"] = "0" * 64
        elif fault == "import": captured["initial_saved_import"]["configuration_id"] = "0" * 64
        elif fault == "config": captured["configured_input"]["facility"]["facility_name"] = "different source"
        elif fault == "lineage": captured["original_template_lineage"]["order_id"] = "OTHER"
        elif fault == "proof": captured["original_template_source_proof"]["source_sha256"] = "0" * 64
        elif fault == "capture-template": captured["original_template_projection"]["status"] = "changed"
        elif fault == "physical-template":
            session.get(FacilityTemplateVersion, "T").config_json = {"changed": True}
            session.flush()
        elif fault == "saved-quantity":
            sheet = deepcopy(session.get(OrderSheetDraft, "D").draft_sheet_json)
            sheet["rows"][1][3] = 7
            session.get(OrderSheetDraft, "D").draft_sheet_json = sheet
            session.flush()
        elif fault == "declared-null": captured["initial_saved_import"] = None
        else: template_id = "OTHER"
        with pytest.raises(ValueError, match="initial_saved_"):
            imports.validate_candidate_template_source(session, "O", captured, template_id)
        session.rollback()
    assert imports.get_binding(session, "O") == before


def test_unowned_saved_candidate_cannot_persist(claimed_import):
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    session, _, claim = claimed_import
    before = session.query(OrderOcrEvidenceRun).count()
    with delivery.claim_context({**claim, "claim_token": "wrong"}), guard.workflow_execution_context("NEW-JOB", "O", claim["workflow_attempt_id"], claim["workflow_revision"]):
        with pytest.raises(ValueError, match="configured_execution_input_changed"):
            evidence.persist_evidence_run("O", {"engine": "synthetic-source"}, template_version_id="T", status="diagnostic_only",
                producer_version="native-provenance/v1", workflow_job_id="NEW-JOB", workflow_attempt_id=claim["workflow_attempt_id"],
                expected_workflow_revision=claim["workflow_revision"], write_session=session)
    session.rollback()
    assert session.query(OrderOcrEvidenceRun).count() == before
