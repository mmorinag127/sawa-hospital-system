from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.api import orders
from src.services import order_service, order_workflow_v2_service


@pytest.fixture
def completion_transaction(monkeypatch):
    # Caller transaction double; real completion helpers are invoked below.
    job = SimpleNamespace(status="running", metrics={}, error_message=None)
    workflow = {"revision": 7, "state": "ocr_running"}
    session = SimpleNamespace(get=lambda model, key: job, flush=lambda: None)

    @contextmanager
    def transaction():
        before_job, before_workflow = deepcopy(vars(job)), deepcopy(workflow)
        try:
            yield session
        except BaseException:
            vars(job).clear()
            vars(job).update(before_job)
            workflow.clear()
            workflow.update(before_workflow)
            raise

    def mark(order_id, **kwargs):
        assert kwargs["write_session"] is session
        assert kwargs["expected_revision"] == 7
        assert kwargs["workflow_attempt_id"] == "captured-attempt"
        workflow.update(revision=8, state="ocr_failed")
        job.status = "failed"
        job.error_message = kwargs["error"]
        return deepcopy(workflow), kwargs["error"]

    monkeypatch.setattr(order_service, "_ocr_result_transaction", transaction)
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args, source_failure=None: True)
    monkeypatch.setattr(order_service, "current_nonworkflow_execution", lambda: None)
    monkeypatch.setattr(order_workflow_v2_service, "mark_ocr_run_completed", mark)
    return job, workflow, session


@pytest.mark.parametrize("failure", ["ocr_execution_slot_timeout", "ocr_rerun_crashed", "ocr_dispatch_failed"])
def test_failure_receipt_commits_job_and_workflow_together(completion_transaction, failure):
    job, workflow, _ = completion_transaction
    receipt, error = orders._complete_workflow_ocr_run(
        "order", "real-job", expected_revision=7,
        workflow_attempt_id="captured-attempt", error=failure,
    )
    assert error == failure
    assert receipt == job.metrics["rerun_terminal_receipt"]
    assert receipt["workflow_revision"] == 7
    assert receipt["workflow_attempt_id"] == "captured-attempt"
    assert receipt["job_id"] == "real-job"
    assert receipt["evidence_run_id"] is None
    assert job.status == "failed"
    assert workflow == {"revision": 8, "state": "ocr_failed"}


def test_superseded_failure_mutates_neither_job_nor_workflow(monkeypatch, completion_transaction):
    job, workflow, _ = completion_transaction
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args, source_failure=None: False)
    receipt, error = orders._complete_workflow_ocr_run(
        "order", "real-job", expected_revision=7,
        workflow_attempt_id="captured-attempt", error="crash",
    )
    assert receipt is None
    assert error == "reparse_attempt_superseded"
    assert job.status == "running"
    assert job.metrics == {}
    assert workflow == {"revision": 7, "state": "ocr_running"}


def test_mid_terminal_failure_rolls_back_both(monkeypatch, completion_transaction):
    job, workflow, session = completion_transaction
    def fail_flush():
        raise ValueError("receipt_write_failed")
    session.flush = fail_flush
    receipt, error = orders._complete_workflow_ocr_run(
        "order", "real-job", expected_revision=7,
        workflow_attempt_id="captured-attempt", error="crash",
    )
    assert receipt is None
    assert error == "receipt_write_failed"
    assert job.status == "running"
    assert job.metrics == {}
    assert workflow == {"revision": 7, "state": "ocr_running"}


def test_missing_capture_stops_without_entering_transaction(monkeypatch):
    def forbidden():
        raise AssertionError("missing capture entered write transaction")
    monkeypatch.setattr(order_service, "_ocr_result_transaction", forbidden)
    receipt, error = orders._complete_workflow_ocr_run(
        "order", "real-job", expected_revision=None,
        workflow_attempt_id=None, error="crash",
    )
    assert receipt is None
    assert error == order_workflow_v2_service.WORKFLOW_REVISION_REQUIRED


@pytest.mark.parametrize("failure", ["timeout", "crash"])
def test_actual_rerun_callback_failure_uses_captured_atomic_completion(monkeypatch, completion_transaction, failure):
    job, workflow, _ = completion_transaction
    monkeypatch.setattr(orders, "get_ocr_job", lambda job_id: {"metrics": {}})
    monkeypatch.setattr(orders.ocr_job_service, "update_job_for_workflow_attempt", lambda *args, **kwargs: (job, True))
    @contextmanager
    def slot(**kwargs):
        assert kwargs == {"order_id": "order", "job_id": "real-job"}
        if failure == "timeout":
            raise orders.OcrExecutionSlotTimeout("timeout")
        yield {}
    def crash(*args, **kwargs):
        raise RuntimeError("pipeline crash")
    monkeypatch.setattr(orders, "acquire_ocr_execution_slot", slot)
    monkeypatch.setattr(order_service, "rerun_ocr_evidence_only", crash)
    orders._run_ocr_rerun_background(
        "order", "real-job", expected_revision=7, workflow_attempt_id="captured-attempt",
    )
    receipt = job.metrics["rerun_terminal_receipt"]
    assert receipt["workflow_revision"] == 7
    assert receipt["workflow_attempt_id"] == "captured-attempt"
    assert receipt["evidence_run_id"] is None
    assert receipt["status"] == job.status == "failed"
    assert workflow["revision"] == 8


def test_dispatch_failure_uses_same_atomic_completion(completion_transaction):
    job, workflow, _ = completion_transaction
    def dispatch(*args):
        raise RuntimeError("dispatch failed")
    with pytest.raises(orders.HTTPException) as raised:
        orders._dispatch_workflow_ocr_task(
            SimpleNamespace(add_task=dispatch), lambda: None,
            order_id="order", job_id="real-job", expected_revision=7,
            workflow_attempt_id="captured-attempt",
        )
    assert raised.value.status_code == 503
    assert job.metrics["rerun_terminal_receipt"]["error"] == "ocr_dispatch_failed"
    assert workflow["revision"] == 8
