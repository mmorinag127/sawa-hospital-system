from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services import order_service


@pytest.fixture
def rerun_transaction(monkeypatch):
    # Transaction double verifies caller Session propagation, not SQL concurrency.
    job = SimpleNamespace(id="real-job", status="running", metrics={}, error_message=None)
    state = {"evidence": None, "cache": None}
    session = SimpleNamespace(get=lambda model, key: job if key == job.id else None, flush=lambda: None)
    execution = {"job_id": job.id, "order_id": "order", "execution_id": "start-generation"}

    @contextmanager
    def transaction():
        before_state, before_job = deepcopy(state), deepcopy(vars(job))
        try:
            yield session
        except BaseException:
            state.clear()
            state.update(before_state)
            vars(job).clear()
            vars(job).update(before_job)
            raise

    def evidence(order_id, payload, **kwargs):
        assert kwargs["write_session"] is session
        assert kwargs["workflow_job_id"] == job.id
        state["evidence"] = deepcopy(payload)
        return {"id": "own-evidence", "payload_json": {**payload, "_ocr_execution": execution}}

    def cache(order_id, payload, **kwargs):
        assert kwargs["write_session"] is session
        assert kwargs["job_id"] == job.id
        assert kwargs["preserve_existing_artifacts"] is False
        assert kwargs["persist_evidence"] is False
        state["cache"] = deepcopy(payload)
        return payload

    monkeypatch.setattr(order_service, "_ocr_result_transaction", transaction)
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args, source_failure=None: True)
    monkeypatch.setattr(order_service, "current_nonworkflow_execution", lambda: execution)
    monkeypatch.setattr(order_service, "persist_ocr_evidence_run", evidence)
    monkeypatch.setattr(order_service, "_save_order_ocr_cache", cache)
    token = order_service._workflow_ocr_attempt_id.set(None)
    try:
        yield job, state, session
    finally:
        order_service._workflow_ocr_attempt_id.reset(token)


@pytest.mark.parametrize("quantity", [0, 12, None])
def test_own_result_and_terminal_share_transaction(rerun_transaction, quantity):
    job, state, _ = rerun_transaction
    payload = {"recognized_quantity": quantity}
    result, error = order_service._persist_ocr_rerun_completion("order", job.id, payload)
    assert error is None
    assert state["evidence"] == payload
    assert state["cache"]["recognized_quantity"] == quantity
    receipt = result["terminal_receipt"]
    assert receipt["job_id"] == job.id
    assert receipt["execution_id"] == "start-generation"
    assert receipt["evidence_run_id"] == result["id"] == "own-evidence"
    assert job.metrics["rerun_terminal_receipt"] == receipt
    assert job.status == "done"
    assert job.metrics["adopted"] is False


@pytest.mark.parametrize("failure", ["superseded", "cache_failure", "terminal_failure"])
def test_mid_failure_rolls_back_evidence_cache_and_terminal(monkeypatch, rerun_transaction, failure):
    job, state, _ = rerun_transaction
    if failure == "superseded":
        monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args, source_failure=None: False)
    elif failure == "cache_failure":
        monkeypatch.setattr(order_service, "_save_order_ocr_cache", lambda *args, **kwargs: None)
    else:
        def fail(*args, **kwargs):
            raise ValueError("terminal_failure")
        monkeypatch.setattr(order_service, "_complete_ocr_rerun_in_session", fail)
    with pytest.raises(ValueError):
        order_service._persist_ocr_rerun_completion("order", job.id, {"recognized_quantity": 0})
    assert state == {"evidence": None, "cache": None}
    assert job.status == "running"
    assert job.metrics == {}


def test_rejected_diagnostic_keeps_own_id_without_canonical_cache(rerun_transaction):
    job, state, _ = rerun_transaction
    result, error = order_service._persist_ocr_rerun_completion(
        "order", job.id, {"recognized_quantity": 0}, error="schema_mismatch",
    )
    assert error == "schema_mismatch"
    assert result["terminal_receipt"]["evidence_run_id"] == "own-evidence"
    assert job.status == "failed"
    assert state["cache"] is None
    assert job.metrics["adopted"] is False


@pytest.mark.parametrize("reason,raw", [("hakodate_ocr_quantity_non_numeric", "--"),
                                      ("hakodate_ocr_quantity_low_confidence", "12")])
def test_rejected_recognition_keeps_cell_diagnostics_with_terminal_receipt(rerun_transaction, reason, raw):
    job, state, _ = rerun_transaction
    review = {"count": 1, "cells": [{"sheet_cell": "H17", "raw_text": raw, "reason": reason}]}
    result, error = order_service._persist_ocr_rerun_completion(
        "order", job.id, {"hakodate_recognition_review": review}, error="evidence_unusable",
        metrics_patch={"error_detail": "Hakodate canonical evidence blocked: hakodate_ocr_quantity_unresolved"},
    )
    assert error == "evidence_unusable"
    assert job.status == "failed"
    assert job.metrics["recognition_review"] == review
    assert job.metrics["evidence_run_id"] == result["terminal_receipt"]["evidence_run_id"]
    assert job.metrics["recovery_action"] == "review_recognition_cells"
    assert "hakodate_ocr_quantity_unresolved" in job.metrics["error_detail"]
    assert state["cache"] is None


def test_execution_exception_with_retained_review_is_hard_failure(rerun_transaction):
    job, state, _ = rerun_transaction
    job.metrics = {"recognition_review": {"count": 1}, "recovery_action": "review_recognition_cells",
                   "error_user_message": "文字認識は完了しました"}
    payload = {"hakodate_recognition_review": {"count": 1, "cells": [
        {"sheet_cell": "H17", "raw_text": "-", "reason": "hakodate_ocr_quantity_non_numeric"}]}}
    _, error = order_service._persist_ocr_rerun_completion(
        "order", job.id, payload, error="database_write_failed",
        metrics_patch={"error_detail": "database unavailable", "processing_stage": "persistence"})
    assert error == "database_write_failed"
    assert job.metrics["result_state"] == "hard_failed"
    assert job.metrics["processing_stage"] == "persistence"
    assert job.metrics["recognition_review"] is None
    assert job.metrics["recovery_action"] is None
    assert job.metrics["error_user_message"] is None
    assert job.metrics["error_detail"] == "database unavailable"
    assert state["cache"] is None


def test_missing_own_result_never_reports_success(rerun_transaction):
    job, state, _ = rerun_transaction
    with pytest.raises(ValueError, match="reparse_pipeline_result_missing"):
        order_service._persist_ocr_rerun_completion("order", job.id, None)
    assert state == {"evidence": None, "cache": None}
    assert job.status == "running"


def test_workflow_terminal_receives_captured_tokens_and_same_session(monkeypatch, rerun_transaction):
    from src.services import order_workflow_v2_service

    job, _, session = rerun_transaction
    attempt_token = order_service._workflow_ocr_attempt_id.set("queue-attempt")
    revision_token = order_service._workflow_ocr_revision.set(7)
    seen = []

    def complete(order_id, **kwargs):
        seen.append((order_id, kwargs))
        assert kwargs["write_session"] is session
        job.status = "done"
        return {"revision": 8}, None

    monkeypatch.setattr(order_workflow_v2_service, "mark_ocr_run_completed", complete)
    try:
        result, error = order_service._persist_ocr_rerun_completion("order", job.id, {"recognized_quantity": 0})
    finally:
        order_service._workflow_ocr_revision.reset(revision_token)
        order_service._workflow_ocr_attempt_id.reset(attempt_token)
    assert error is None
    assert seen[0][1]["expected_revision"] == 7
    assert seen[0][1]["workflow_attempt_id"] == "queue-attempt"
    assert seen[0][1]["evidence_run_id"] == result["id"] == "own-evidence"
    assert result["terminal_receipt"]["workflow_revision"] == 7


def test_workflow_conflict_rolls_back_own_evidence(monkeypatch, rerun_transaction):
    from src.services import order_workflow_v2_service

    job, state, _ = rerun_transaction
    token = order_service._workflow_ocr_attempt_id.set("old-attempt")
    monkeypatch.setattr(order_workflow_v2_service, "mark_ocr_run_completed", lambda *args, **kwargs: (None, order_workflow_v2_service.WORKFLOW_REVISION_CONFLICT))
    try:
        with pytest.raises(ValueError, match=order_workflow_v2_service.WORKFLOW_REVISION_CONFLICT):
            order_service._persist_ocr_rerun_completion("order", job.id, {"recognized_quantity": 0})
    finally:
        order_service._workflow_ocr_attempt_id.reset(token)
    assert state == {"evidence": None, "cache": None}
    assert job.status == "running"
    assert job.metrics == {}


def test_reconcile_true_requires_actual_own_terminal_receipt(monkeypatch, rerun_transaction):
    job, state, _ = rerun_transaction
    monkeypatch.setattr(order_service, "get_ocr_job", lambda job_id: {
        "id": job.id, "order_id": "order", "status": "running",
        "metrics": {"request_mode": "ocr_rerun"}, "output_reference": "own-output",
    })
    monkeypatch.setattr(order_service, "get_job_request_mode", lambda job: "ocr_rerun")
    monkeypatch.setattr(order_service, "_load_pipeline_output_once", lambda ref: {"recognized_quantity": 0})
    monkeypatch.setattr(order_service, "_output_is_pending", lambda payload: False)
    monkeypatch.setattr(order_service.ocr_evidence_service, "classify_evidence_payload", lambda payload, *, expected_hakodate_source=None: {"persistable": True})
    assert order_service._reconcile_finished_ocr_rerun("order") is True
    assert state["evidence"] == {"recognized_quantity": 0}
    assert job.metrics["rerun_terminal_receipt"]["evidence_run_id"] == "own-evidence"
    assert job.status == "done"


def test_reconcile_does_not_accept_success_without_receipt(monkeypatch, rerun_transaction):
    job, _, _ = rerun_transaction
    monkeypatch.setattr(order_service, "get_ocr_job", lambda job_id: {
        "id": job.id, "order_id": "order", "status": "running", "metrics": {},
        "output_reference": "own-output",
    })
    monkeypatch.setattr(order_service, "get_job_request_mode", lambda job: "ocr_rerun")
    monkeypatch.setattr(order_service, "_load_pipeline_output_once", lambda ref: {})
    monkeypatch.setattr(order_service, "_output_is_pending", lambda payload: False)
    monkeypatch.setattr(order_service.ocr_evidence_service, "classify_evidence_payload", lambda payload, *, expected_hakodate_source=None: {"persistable": True})
    monkeypatch.setattr(order_service, "_persist_ocr_rerun_completion", lambda *args, **kwargs: ({"id": "own-evidence"}, None))
    assert order_service._reconcile_finished_ocr_rerun("order") is False
