"""Owned SQLite delivery execution, not recognizer, HTTP/OIDC, PG or live proof.

The existing fixture creates real dated JSON, monthly SQL, PDF/version receipts
and workflow context. This negative fixture separately registers an explicitly
gridless FAX retaining facility/period, so actual quad estimation fails before
the recognizer; the positive generated FAX remains unchanged. The
negative commit case uses a SQLAlchemy transaction event, not a
replacement completion, ownership, assignment or receipt implementation.
"""

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import event
from reportlab.pdfgen import canvas

from test_modernization_owned_ocr_workflow import (
    JOB, ORDER, FACILITY, START, END, TEMPLATE, OwnedProducer, _owned_workflow_database_snapshot,
    _queue_and_claim_owned_workflow_attempt,
)
from src import db
from src.api import worker
from src.models.ocr_job import OcrJob
from src.models.order_workflow_state import OrderWorkflowState
from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
from src.services import workflow_ocr_delivery_service as delivery
from src.services import workflow_ocr_delivery_execution_service as execution
from src.services import ocr_execution_lock_service as slots
from src.services import master_order_form_template_service as master
from src.services import order_workflow_v2_service as workflow


@pytest.fixture
def owned_failure(tmp_path, monkeypatch):
    f = OwnedProducer(tmp_path, monkeypatch)
    monkeypatch.setattr(execution, "get_engine", lambda: f.engine)
    monkeypatch.setattr(slots, "get_engine", lambda: f.engine)
    try:
        positive_path = f.pdf
        positive_bytes = positive_path.read_bytes()
        negative_path = tmp_path / "synthetic-gridless-negative.pdf"
        pdf = canvas.Canvas(str(negative_path), pagesize=(595, 842))
        pdf.drawString(40, 800, f"Facility: {FACILITY}")
        pdf.drawString(40, 780, f"Period: {START.isoformat()} - {END.isoformat()}")
        pdf.drawString(40, 760, "Synthetic negative: no outer grid or table rulings")
        pdf.save()
        with db.session_scope() as session:
            f.add_document(session, "DOC-NEGATIVE", "PDF-NEGATIVE", negative_path,
                           message_id="UNIT-NATIVE-NEGATIVE-MESSAGE")
        current, error = workflow.get_workflow_readonly(ORDER)
        assert error is None, error
        f.context, error = workflow.confirm_context(
            order_id=ORDER, facility_id=FACILITY, week_start=START.isoformat(),
            week_end=END.isoformat(), template_id=TEMPLATE, expected_revision=current["revision"],
        )
        assert error is None, (error, f.context)
        f.pdf = negative_path
        assert positive_path.read_bytes() == positive_bytes
        yield f
        assert positive_path.read_bytes() == positive_bytes
    finally:
        f.engine.dispose()
        assert sha256(master.MASTER_TEMPLATE_PATH.read_bytes()).hexdigest() == f.locked_hash


def execute_delivery(claim):
    """Actual worker execution segment; excludes the HTTP OIDC entry guard."""
    with execution.execution_mutex(claim["delivery_id"]) as acquired:
        assert acquired
        with delivery.claim_context(claim):
            return worker._execute_claim(claim)


def stable_data(snapshot):
    # Progress writes to the job and claim bookkeeping are intentional. All
    # source, quantity, selected evidence, draft and cache tables stay intact.
    return {name: rows for name, rows in snapshot.items()
            if name not in {OcrJob.__tablename__, WorkflowOcrDelivery.__tablename__,
                            OrderWorkflowState.__tablename__}}


def assert_failed_terminal(claim, result):
    receipt = result["terminal_receipt"]
    assert receipt["order_id"] == ORDER
    assert receipt["job_id"] == JOB
    assert receipt["workflow_attempt_id"] == claim["workflow_attempt_id"]
    assert receipt["workflow_revision"] == claim["workflow_revision"]
    assert receipt["status"] == "failed"
    assert receipt["error"] == "quad_estimation_failed"
    assert receipt["evidence_run_id"] is None
    with db.session_scope() as session:
        job = session.get(OcrJob, JOB)
        state = session.get(OrderWorkflowState, ORDER)
        row = session.get(WorkflowOcrDelivery, claim["delivery_id"])
        persisted = job.metrics["rerun_terminal_receipt"]
        assert row.result_receipt == persisted
        assert {key: persisted[key] for key in receipt} == receipt
        assert persisted["workflow_delivery_owner"] == {
            key: claim[key] for key in ("delivery_id", "generation", "claim_token")}
        assert job.status == "failed"
        assert job.error_message == receipt["error"]
        assert job.metrics["processing_stage"] == "rejected_review_saved"
        assert "edge locked quad estimation failed" in job.metrics["error_detail"]
        assert job.metrics["result_state"] == "review_required"
        assert state.state == "ocr_failed"
        assert state.revision == claim["workflow_revision"] + 1
        assert state.blockers_json == [receipt["error"]]
        assert state.evidence_run_id is None and state.draft_id is None
        assert row.state == delivery.CLAIMED


def test_internal_native_failure_persists_real_terminal_then_allows_worker_ack(owned_failure):
    f = owned_failure
    claim = _queue_and_claim_owned_workflow_attempt(f)
    before = _owned_workflow_database_snapshot()
    pdf_before, expected_before = f.pdf.read_bytes(), deepcopy(f.expected)
    result = execute_delivery(claim)
    assert_failed_terminal(claim, result)
    assert stable_data(_owned_workflow_database_snapshot()) == stable_data(before)
    assert delivery.acknowledge_claim(claim["delivery_id"], claim["generation"],
                                      claim["claim_token"]) is True
    with db.session_scope() as session:
        row = session.get(WorkflowOcrDelivery, claim["delivery_id"])
        assert row.state == delivery.ACKNOWLEDGED
        assert row.lease_expires_at is None
    assert f.pdf.read_bytes() == pdf_before and f.expected == expected_before


def test_terminal_commit_failure_cannot_ack_and_expired_claim_can_recover(owned_failure):
    f = owned_failure
    claim = _queue_and_claim_owned_workflow_attempt(f)
    before = _owned_workflow_database_snapshot()
    failed_commits = []

    def reject_terminal_commit(session):
        for instance in session.identity_map.values():
            if (isinstance(instance, OcrJob) and instance.id == JOB
                    and isinstance((instance.metrics or {}).get("rerun_terminal_receipt"), dict)):
                failed_commits.append(deepcopy(instance.metrics["rerun_terminal_receipt"]))
                raise RuntimeError("synthetic terminal commit failure")

    event.listen(f.sessions, "before_commit", reject_terminal_commit)
    try:
        with pytest.raises(RuntimeError, match="^synthetic terminal commit failure$"):
            execute_delivery(claim)
    finally:
        event.remove(f.sessions, "before_commit", reject_terminal_commit)
    assert len(failed_commits) == 1
    assert failed_commits[0]["workflow_attempt_id"] == claim["workflow_attempt_id"]
    after = _owned_workflow_database_snapshot()
    assert stable_data(after) == stable_data(before)
    assert after[OrderWorkflowState.__tablename__] == before[OrderWorkflowState.__tablename__]
    assert after[WorkflowOcrDelivery.__tablename__] == before[WorkflowOcrDelivery.__tablename__]
    with db.session_scope() as session:
        job = session.get(OcrJob, JOB)
        assert job.status == "running"
        assert job.metrics.get("rerun_terminal_receipt") is None
    with pytest.raises(ValueError, match="^workflow_ocr_delivery_persisted_result_missing$"):
        delivery.acknowledge_claim(claim["delivery_id"], claim["generation"], claim["claim_token"])
    assert _owned_workflow_database_snapshot() == after
    assert delivery.claim_delivery(claim["delivery_id"], claim["generation"],
                                   lease_expires_at=datetime.utcnow() + timedelta(minutes=5)) == {"duplicate": True}

    # Author elapsed lease time, not a replacement guard or fabricated receipt.
    with db.session_scope() as session:
        session.get(WorkflowOcrDelivery, claim["delivery_id"]).lease_expires_at = (
            datetime.utcnow() - timedelta(seconds=1))
    successor = delivery.claim_delivery(claim["delivery_id"], claim["generation"],
                                        lease_expires_at=datetime.utcnow() + timedelta(minutes=5))
    assert not successor.get("duplicate")
    assert successor["claim_token"] != claim["claim_token"]
    for key in ("delivery_id", "generation", "workflow_attempt_id", "workflow_revision",
                "selected_pdf_receipt", "request_identity"):
        assert successor[key] == claim[key]
    assert delivery.acknowledge_claim(claim["delivery_id"], claim["generation"],
                                      claim["claim_token"]) is False
    result = execute_delivery(successor)
    assert_failed_terminal(successor, result)
    assert delivery.acknowledge_claim(successor["delivery_id"], successor["generation"],
                                      successor["claim_token"]) is True
    assert stable_data(_owned_workflow_database_snapshot()) == stable_data(before)
