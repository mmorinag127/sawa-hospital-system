from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services import order_service


@pytest.fixture
def first_pass_transaction():
    # Transaction double: source behavior coverage, not database concurrency proof.
    job = SimpleNamespace(id="actual-ingest-job", status="running", metrics={}, error_message=None)
    state = {"quantity": 0, "document_status": "processing", "evidence_id": None}
    session = SimpleNamespace(get=lambda model, key: job if key == job.id else None)

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

    return job, state, transaction


@pytest.mark.parametrize("quantity", [0, 12])
def test_own_success_keeps_terminal_and_business_result_in_caller_transaction(monkeypatch, first_pass_transaction, quantity):
    job, state, transaction = first_pass_transaction
    seen = []
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda session, job_id, order_id: seen.append((session, job_id, order_id)) or True)
    with transaction() as session:
        state.update(quantity=quantity, document_status="success", evidence_id="own-evidence")
        receipt = order_service._complete_first_pass_job_in_session(
            session, SimpleNamespace(id="order"), SimpleNamespace(id="document"),
            {"job_id": job.id, "execution_id": "captured-generation"},
            {"id": state["evidence_id"]}, applied=True,
        )
        assert seen == [(session, job.id, "order")]
        assert receipt["evidence_run_id"] == "own-evidence"
        assert receipt["execution_id"] == "captured-generation"
        assert job.metrics["first_pass_terminal_receipt"] == receipt
        assert job.status == "done"
        assert job.metrics["adopted"] is False
    assert state["quantity"] == quantity


@pytest.mark.parametrize("failure", ["superseded", "post_write_failure"])
def test_failure_rolls_back_own_business_result_and_terminal_together(monkeypatch, first_pass_transaction, failure):
    job, state, transaction = first_pass_transaction
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args: failure != "superseded")
    with pytest.raises(ValueError):
        with transaction() as session:
            state.update(quantity=12, document_status="success", evidence_id="own-evidence")
            order_service._complete_first_pass_job_in_session(
                session, SimpleNamespace(id="order"), SimpleNamespace(id="document"),
                {"job_id": job.id, "execution_id": "captured-generation"},
                {"id": "own-evidence"}, applied=True,
            )
            raise ValueError("post_write_failure")
    assert state == {"quantity": 0, "document_status": "processing", "evidence_id": None}
    assert job.status == "running"
    assert job.metrics == {}


def test_rejected_receipt_retains_own_evidence_without_success(monkeypatch, first_pass_transaction):
    job, _, transaction = first_pass_transaction
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args: True)
    with transaction() as session:
        receipt = order_service._complete_first_pass_job_in_session(
            session, SimpleNamespace(id="order"), SimpleNamespace(id="document"),
            {"job_id": job.id, "execution_id": "captured-generation"},
            {"id": "own-diagnostic"}, applied=False,
        )
    assert receipt["status"] == "failed"
    assert receipt["evidence_run_id"] == "own-diagnostic"
    assert job.metrics["result_state"] == "review_required"
    assert job.metrics["adopted"] is False
