"""Source-authored regression definitions; execution belongs to parent verification."""
import pytest
from copy import deepcopy
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from src.models.ocr_job import OcrJob
from src.models.workflow_ocr_delivery import WorkflowOcrDelivery

from src.api import orders, worker
from src.services import order_service
from src.services import workflow_ocr_delivery_dispatch_service as dispatch
from src.services import workflow_ocr_delivery_service as delivery


@pytest.fixture
def terminal_tracking_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'terminal_tracking.sqlite'}")
    try:
        OcrJob.__table__.create(engine)
        WorkflowOcrDelivery.__table__.create(engine)
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _record_terminal_validation(monkeypatch):
    checked = []
    def record(session):
        checked.append(delivery.terminal_jobs_for_commit(session))
    monkeypatch.setattr(delivery, "require_terminal_receipts_before_commit", record)
    return checked


def test_terminal_tracking_commit_then_session_reuse(terminal_tracking_session, monkeypatch):
    session = terminal_tracking_session
    checked = _record_terminal_validation(monkeypatch)
    session.execute(text("SELECT 1"))
    delivery.track_terminal_job(session, "committed-job")
    session.commit()
    assert checked == [{"committed-job"}]
    assert "workflow_delivery_terminal_jobs" not in session.info
    session.execute(text("SELECT 1"))
    session.commit()
    assert checked == [{"committed-job"}]


def test_terminal_receipt_refusal_rollback_then_session_reuse(terminal_tracking_session, monkeypatch):
    session = terminal_tracking_session
    session.execute(text("SELECT 1"))
    delivery.track_terminal_job(session, "aborted-job")
    # The real receipt validator refuses a missing job; lifecycle events are real.
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="terminal_transaction_incomplete"):
        session.commit()
    session.rollback()
    assert "workflow_delivery_terminal_jobs" not in session.info
    session.execute(text("SELECT 1"))
    session.commit()


def test_nested_rollback_preserves_parent_tracking(terminal_tracking_session, monkeypatch):
    session = terminal_tracking_session
    checked = _record_terminal_validation(monkeypatch)
    session.execute(text("SELECT 1"))
    parent = session.get_transaction()
    delivery.track_terminal_job(session, "parent-job")
    child = session.begin_nested()
    delivery.track_terminal_job(session, "child-job")
    delivery.track_terminal_job(session, "parent-job")
    child.rollback()
    assert session.info["workflow_delivery_terminal_jobs"] == {parent: {"parent-job"}}
    session.commit()
    assert checked == [{"parent-job"}]
    assert "workflow_delivery_terminal_jobs" not in session.info


def test_nested_commit_merges_jobs_for_outer_validation(terminal_tracking_session, monkeypatch):
    session = terminal_tracking_session
    checked = _record_terminal_validation(monkeypatch)
    session.execute(text("SELECT 1"))
    parent = session.get_transaction()
    delivery.track_terminal_job(session, "parent-job")
    child = session.begin_nested()
    delivery.track_terminal_job(session, "child-job")
    child.commit()
    assert checked == [{"parent-job", "child-job"}]
    assert session.info["workflow_delivery_terminal_jobs"] == {parent: {"parent-job", "child-job"}}
    session.commit()
    assert checked == [{"parent-job", "child-job"}, {"parent-job", "child-job"}]
    assert "workflow_delivery_terminal_jobs" not in session.info


def test_nested_commit_then_outer_rollback_does_not_leak(terminal_tracking_session, monkeypatch):
    session = terminal_tracking_session
    checked = _record_terminal_validation(monkeypatch)
    session.execute(text("SELECT 1"))
    child = session.begin_nested()
    delivery.track_terminal_job(session, "child-job")
    child.commit()
    session.rollback()
    assert "workflow_delivery_terminal_jobs" not in session.info
    session.execute(text("SELECT 1"))
    session.commit()
    assert checked == [{"child-job"}]


def test_inner_commit_outer_savepoint_rollback_preserves_root(terminal_tracking_session, monkeypatch):
    session = terminal_tracking_session
    checked = _record_terminal_validation(monkeypatch)
    session.execute(text("SELECT 1"))
    root = session.get_transaction()
    delivery.track_terminal_job(session, "root-job")
    outer = session.begin_nested()
    delivery.track_terminal_job(session, "outer-job")
    inner = session.begin_nested()
    delivery.track_terminal_job(session, "inner-job")
    inner.commit()
    outer.rollback()
    assert session.info["workflow_delivery_terminal_jobs"] == {root: {"root-job"}}
    session.commit()
    assert checked == [{"root-job", "outer-job", "inner-job"}, {"root-job"}]


def _seed_terminal_validation_rows(session, job_id, *, complete):
    receipt = {"job_id": job_id, "status": "failed"}
    delivery_id = f"delivery-{job_id}"
    # Core inserts isolate commit validation from runner/claim setup. Receipt
    # lookup and transaction lifecycle use the real Session and persisted rows.
    session.execute(OcrJob.__table__.insert().values(
        id=job_id, status="failed", input_reference=f"input-{job_id}",
        metrics={"workflow_ocr_delivery_id": delivery_id,
                 **({"rerun_terminal_receipt": receipt} if complete else {})}))
    session.execute(WorkflowOcrDelivery.__table__.insert().values(
        id=delivery_id, order_id="original-order", job_id=job_id,
        workflow_attempt_id=f"attempt-{job_id}", workflow_revision=8,
        delivery_generation=1, task_name=f"task-{job_id}", task_contract={},
        identity_sha256="unit-validation-identity", request_mode="ocr_reparse",
        selected_pdf_receipt={}, request_identity={}, state="CLAIMED",
        result_receipt=receipt if complete else None))


def test_outer_commit_with_open_savepoint_refuses_missing_parent_receipt(terminal_tracking_session):
    session = terminal_tracking_session
    _seed_terminal_validation_rows(session, "parent-job", complete=False)
    session.commit()
    session.execute(text("SELECT 1"))
    parent = session.get_transaction()
    delivery.track_terminal_job(session, "parent-job")
    child = session.begin_nested()
    _seed_terminal_validation_rows(session, "child-job", complete=True)
    delivery.track_terminal_job(session, "child-job")
    with pytest.raises(ValueError, match="terminal_transaction_incomplete"):
        session.commit()
    assert session.get_transaction() is parent
    assert session.get_nested_transaction() is child
    assert child.is_active
    session.rollback()
    assert "workflow_delivery_terminal_jobs" not in session.info
    session.execute(text("SELECT 1"))
    session.commit()


def test_outer_commit_with_open_savepoint_validates_parent_and_child(terminal_tracking_session, monkeypatch):
    session = terminal_tracking_session
    _seed_terminal_validation_rows(session, "parent-job", complete=True)
    session.commit()
    session.execute(text("SELECT 1"))
    delivery.track_terminal_job(session, "parent-job")
    child = session.begin_nested()
    _seed_terminal_validation_rows(session, "child-job", complete=True)
    delivery.track_terminal_job(session, "child-job")
    checked = []
    validate = delivery.require_terminal_receipts_before_commit
    def validate_and_record(current):
        checked.append(delivery.terminal_jobs_for_commit(current))
        validate(current)
    monkeypatch.setattr(delivery, "require_terminal_receipts_before_commit", validate_and_record)
    session.commit()
    assert checked and all(jobs == {"parent-job", "child-job"} for jobs in checked)
    assert not child.is_active
    assert session.get_transaction() is None
    assert "workflow_delivery_terminal_jobs" not in session.info
    assert session.get(OcrJob, "child-job").metrics["rerun_terminal_receipt"] == (
        session.get(WorkflowOcrDelivery, "delivery-child-job").result_receipt)
    session.commit()


def _receipt(status="failed"):
    return {"order_id": "original-order", "job_id": "original-job",
        "workflow_attempt_id": "original-attempt", "workflow_revision": 8,
        "evidence_run_id": None, "status": status, "error": "recognized_business_failure"}


def _run():
    return orders._run_reparse_background(
        "original-order", None, llm_assist=False,
        expected_revision=8, workflow_attempt_id="original-attempt", ocr_job_id="original-job")


def test_terminal_persister_refusal_propagates(monkeypatch):
    monkeypatch.setattr(order_service, "reparse_order", lambda *a, **k: (None, "recognized_business_failure"))
    monkeypatch.setattr(orders, "_complete_workflow_ocr_run", lambda *a, **k: (None, "result_storage_refused"))
    with pytest.raises(ValueError, match="result_storage_refused"):
        _run()


def test_actual_terminal_transaction_persister_exception_propagates(monkeypatch):
    monkeypatch.setattr(order_service, "reparse_order", lambda *a, **k: (None, "recognized_business_failure"))
    def fail_persist(*args, **kwargs):
        raise RuntimeError("terminal_database_write_failed")
    monkeypatch.setattr(order_service, "_complete_ocr_rerun_in_session", fail_persist)
    with pytest.raises(RuntimeError, match="terminal_database_write_failed"):
        _run()


def test_persisted_business_failure_returns_failure_receipt(monkeypatch):
    receipt = _receipt()
    monkeypatch.setattr(order_service, "reparse_order", lambda *a, **k: (
        {"terminal_receipt": receipt, "reparse_evidence_run_id": None}, receipt["error"]))
    assert _run() == {"terminal_receipt": receipt}


def test_empty_normal_return_is_not_success(monkeypatch):
    monkeypatch.setattr(order_service, "reparse_order", lambda *a, **k: ({}, None))
    with pytest.raises(ValueError, match="reparse_persisted_result_missing"):
        _run()


def test_original_pending_branch_remains_pending(monkeypatch):
    pending = {"job_id": "original-job", "order_id": "original-order",
        "workflow_attempt_id": "original-attempt", "workflow_revision": 8,
        "execution_id": None, "input_reference": "original-input",
        "output_reference": "original-output"}
    result = {"outcome": "pending", "status": "awaiting_output",
        "pending_receipt": pending, "output_reference": pending["output_reference"]}
    monkeypatch.setattr(order_service, "reparse_order", lambda *a, **k: (result, None))
    def forbid_completion(*args, **kwargs):
        pytest.fail("pending handoff must not become a synthetic business failure")
    monkeypatch.setattr(orders, "_complete_workflow_ocr_run", forbid_completion)
    assert _run() == result


@pytest.mark.parametrize("generation", [None, True, False, 0, -1, "1"])
def test_missing_or_noninteger_generation_stops(generation):
    with pytest.raises(ValueError, match="generation_required"):
        delivery.inspect_generation("original-delivery", generation)


@pytest.mark.parametrize("authorization", [None, "", "Basic value", "Bearer "])
def test_worker_rejects_missing_bearer(monkeypatch, authorization):
    prefix = "HOSPITAL_WORKFLOW_OCR_DELIVERY_WORKER_"
    for key, value in {"EMAIL": "worker@example.iam.gserviceaccount.com",
                       "SUBJECT": "original-subject", "AUDIENCE": "original-audience"}.items():
        monkeypatch.setenv(prefix + key, value)
    with pytest.raises(HTTPException) as exc:
        worker._required_worker_identity(None, authorization)
    assert exc.value.status_code == 401


def test_wrong_subject_or_audience_is_forbidden(monkeypatch):
    prefix = "HOSPITAL_WORKFLOW_OCR_DELIVERY_WORKER_"
    for key, value in {"EMAIL": "worker@example.iam.gserviceaccount.com",
                       "SUBJECT": "original-subject", "AUDIENCE": "original-audience"}.items():
        monkeypatch.setenv(prefix + key, value)
    monkeypatch.setattr(worker.id_token, "verify_oauth2_token", lambda *a: {
        "email": "worker@example.iam.gserviceaccount.com", "sub": "other-subject",
        "aud": "original-audience", "email_verified": True})
    with pytest.raises(HTTPException) as exc:
        worker._required_worker_identity(None, "Bearer opaque-token")
    assert exc.value.status_code == 403


def test_exact_lookup_notfound_is_distinct_from_unknown():
    from google.api_core.exceptions import NotFound, PermissionDenied
    contract = {"name": "projects/source/locations/source/queues/source/tasks/original"}
    class Client:
        error = NotFound("exact task absent")
        def get_task(self, **kwargs):
            assert kwargs["request"]["name"] == contract["name"]
            raise self.error
    client = Client()
    assert dispatch.inspect_task(client, contract) == "missing"
    client.error = PermissionDenied("lookup authorization unresolved")
    with pytest.raises(RuntimeError, match="lookup_unknown"):
        dispatch.inspect_task(client, contract)


@pytest.mark.parametrize("conflict", ["body", "oauth_token", "deadline_nanos", "exact"])
def test_bodyless_workflow_contract_rejects_foreign_request_content(conflict):
    from google.cloud import tasks_v2
    contract = {"name": "projects/fixture/locations/fixture/queues/fixture/tasks/original",
        "http_request": {"url": "https://fixture.invalid/worker/original", "http_method": "POST",
            "oidc_token": {"service_account_email": "fixture@fixture.iam.gserviceaccount.com", "audience": "fixture"}},
        "dispatch_deadline": {"seconds": 15}}
    request = deepcopy(contract["http_request"])
    request["http_method"] = tasks_v2.HttpMethod.POST
    if conflict == "body":
        request["body"] = b'{"foreign":true}'
    elif conflict == "oauth_token":
        request["oauth_token"] = {"service_account_email": "foreign@fixture.iam.gserviceaccount.com", "scope": "foreign"}
    task = tasks_v2.Task(name=contract["name"], http_request=request, dispatch_deadline=contract["dispatch_deadline"])
    if conflict == "deadline_nanos":
        tasks_v2.Task.pb(task).dispatch_deadline.nanos = 1
    class Client:
        def get_task(self, **kwargs):
            assert kwargs["request"]["response_view"] == tasks_v2.Task.View.FULL
            return task
    if conflict == "exact":
        assert dispatch.inspect_task(Client(), contract) == "exists"
        return
    with pytest.raises(RuntimeError, match="task_contract_mismatch"):
        dispatch.inspect_task(Client(), contract)
