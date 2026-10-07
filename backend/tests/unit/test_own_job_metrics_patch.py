from copy import deepcopy

import pytest

from src.services import ocr_job_service, order_service


@pytest.fixture
def own_job_row(isolated_formal_schema):
    from src.db import session_scope
    from src.models.order import Order
    from src.models.ocr_job import OcrJob

    metrics = {
        "workflow_attempt_id": "queue-attempt", "workflow_completion_revision": 7,
        "reparse_read_input": {"lines": [{"quantity_original": 0, "quantity_corrected": None}]},
        "first_pass_source": {"evidence_run_id": "explicit-source"},
        "selected_version_input": {"id": "explicit-version", "storage_uri": "own-input"},
        "pending_receipt": {"workflow_revision": 7, "output_reference": "own-output"},
        "processing_stage": "queued", "auto_recovery_count": 2,
    }
    with session_scope() as session:
        session.add(Order(id="metrics-order", document_uri="own-input", message_id="own-message"))
        session.flush()
        session.add(OcrJob(id="metrics-job", order_id="metrics-order", status="running",
                           input_reference="own-input", metrics=deepcopy(metrics)))
    return "metrics-job", metrics


def test_real_workflow_cas_progress_preserves_fenced_input(monkeypatch, own_job_row):
    from src.db import session_scope
    from src.models.ocr_job import OcrJob

    job_id, original = own_job_row
    calls = []
    def guard(session, **kwargs):
        calls.append(kwargs)
        return kwargs["workflow_attempt_id"] == "queue-attempt" and kwargs["expected_workflow_revision"] == 7
    monkeypatch.setattr(ocr_job_service, "lock_current_workflow_attempt", guard)
    updated, accepted = ocr_job_service.update_job_for_workflow_attempt(
        job_id, "queue-attempt", order_id="metrics-order", expected_workflow_revision=7,
        metrics={"processing_stage": "recognizing"},
    )
    assert accepted
    assert updated["metrics"] == {**original, "processing_stage": "recognizing"}
    with session_scope() as session:
        job = session.get(OcrJob, job_id)
        assert job.metrics["workflow_attempt_id"] == "queue-attempt"
        assert job.metrics["reparse_read_input"]["lines"] == [{"quantity_original": 0, "quantity_corrected": None}]
    _, accepted = ocr_job_service.update_job_for_workflow_attempt(
        job_id, "queue-attempt", order_id="metrics-order", expected_workflow_revision=7,
        metrics={"processing_stage": "next-stage", "workflow_attempt_id": "old-attempt",
                 "reparse_read_input": {}, "pending_receipt": {}, "first_pass_source": {},
                 "adopted": True, "unrecognized_source": "stale-caller", "evidence_run_id": "old"},
    )
    assert accepted
    with session_scope() as session:
        metrics = session.get(OcrJob, job_id).metrics
        assert metrics["reparse_read_input"] == original["reparse_read_input"]
        assert metrics["pending_receipt"] == original["pending_receipt"]
        assert metrics["first_pass_source"] == original["first_pass_source"]
        assert "adopted" not in metrics
        assert "unrecognized_source" not in metrics
        assert "evidence_run_id" not in metrics
    assert len(calls) == 2


def test_guarded_outcome_telemetry_cannot_replace_execution_lineage():
    current = {"workflow_attempt_id": "own", "pending_receipt": {"zero": 0},
               "first_pass_source": {"evidence_run_id": "own-source"}}
    patched = ocr_job_service.merge_own_job_metrics_patch(
        current, {"pipeline_specific_diagnostic": {"zero": 0, "null": None},
                  "workflow_attempt_id": "other", "pending_receipt": {}, "first_pass_source": {}},
        own_outcome=True,
    )
    assert patched == {**current, "pipeline_specific_diagnostic": {"zero": 0, "null": None}}


def test_superseded_progress_cannot_patch_or_replace(monkeypatch, own_job_row):
    from src.db import session_scope
    from src.models.ocr_job import OcrJob

    job_id, original = own_job_row
    monkeypatch.setattr(ocr_job_service, "lock_current_workflow_attempt", lambda *args, **kwargs: False)
    result, accepted = ocr_job_service.update_job_for_workflow_attempt(
        job_id, "old-attempt", order_id="metrics-order", expected_workflow_revision=6,
        metrics={"processing_stage": "late", "workflow_attempt_id": "old-attempt"},
    )
    assert result is None and not accepted
    with session_scope() as session:
        assert session.get(OcrJob, job_id).metrics == original


def test_nonworkflow_recovery_patch_preserves_original_boundary_and_receipts(monkeypatch, own_job_row):
    from src.db import session_scope
    from src.models.ocr_job import OcrJob

    job_id, _ = own_job_row
    boundary = {"job_id": job_id, "order_id": "metrics-order", "job_order_id": "metrics-order",
                "input_reference": "own-input", "execution_id": "start-execution"}
    with session_scope() as session:
        job = session.get(OcrJob, job_id)
        job.metrics = {"nonworkflow_execution": boundary, "reparse_read_input": {"zero": 0},
                       "first_pass_terminal_receipt": {"evidence_run_id": "own-firstpass"}, "auto_recovery_count": 2}
    monkeypatch.setattr(ocr_job_service, "current_nonworkflow_execution", lambda: boundary)
    monkeypatch.setattr(ocr_job_service, "lock_actual_write_ownership", lambda *args, **kwargs: True)
    updated = ocr_job_service.update_job(
        job_id, recovery_count_increment=True,
        metrics={"processing_stage": "recovering", "nonworkflow_execution": {},
                 "reparse_read_input": {}, "first_pass_terminal_receipt": {}, "auto_recovery_count": 999},
    )
    assert updated["metrics"]["nonworkflow_execution"] == boundary
    assert updated["metrics"]["reparse_read_input"] == {"zero": 0}
    assert updated["metrics"]["first_pass_terminal_receipt"] == {"evidence_run_id": "own-firstpass"}
    assert updated["metrics"]["auto_recovery_count"] == 3


def test_progress_producer_does_not_read_latest_metrics(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("producer recaptured metrics")
    monkeypatch.setattr(order_service, "get_ocr_job", forbidden)
    assert order_service._merge_ocr_job_metrics("real-job", {"processing_stage": "running"}) == {"processing_stage": "running"}


def test_actual_reparse_progress_keeps_next_cas_usable(monkeypatch, own_job_row):
    job_id, original = own_job_row
    monkeypatch.setattr(ocr_job_service, "lock_current_workflow_attempt", lambda *args, **kwargs: True)
    with order_service.workflow_ocr_write_context("queue-attempt", 7, job_id, "metrics-order"):
        first = order_service._update_reparse_job_progress(job_id, processing_stage="recognizing")
        second = order_service._update_reparse_job_progress(job_id, processing_stage="validation")
    assert first["metrics"]["workflow_attempt_id"] == "queue-attempt"
    assert second["metrics"]["workflow_completion_revision"] == 7
    assert second["metrics"]["processing_stage"] == "validation"
    assert second["metrics"]["reparse_read_input"] == original["reparse_read_input"]


def test_terminal_receipt_cannot_be_overwritten_by_progress(monkeypatch, own_job_row):
    from src.db import session_scope
    from src.models.ocr_job import OcrJob

    job_id, original = own_job_row
    receipt = {"evidence_run_id": "own-evidence", "status": "done"}
    with session_scope() as session:
        job = session.get(OcrJob, job_id)
        job.status = "done"
        job.metrics = {**original, "rerun_terminal_receipt": receipt}
    monkeypatch.setattr(ocr_job_service, "lock_current_workflow_attempt", lambda *args, **kwargs: True)
    updated, accepted = ocr_job_service.update_job_for_workflow_attempt(
        job_id, "queue-attempt", order_id="metrics-order", expected_workflow_revision=7,
        status="running", metrics={"processing_stage": "late", "rerun_terminal_receipt": {}},
    )
    assert updated is None and not accepted
    with session_scope() as session:
        job = session.get(OcrJob, job_id)
        assert job.status == "done"
        assert job.metrics["rerun_terminal_receipt"] == receipt
