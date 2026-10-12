from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from src.api import orders
from src.services import order_workflow_v2_service as workflow_service


@pytest.fixture
def readonly_jobs(monkeypatch):
    job = {
        "id": "OCR-order", "order_id": "order", "status": "running",
        "updated_at": datetime.utcnow() - timedelta(hours=2),
        "metrics": {"request_mode": "ocr_reparse", "workflow_attempt_id": "old-attempt",
                    "workflow_completion_revision": 7, "processing_stage": "waiting"},
    }
    def forbidden(*args, **kwargs):
        raise AssertionError("GET entered completion/token recapture")
    monkeypatch.setattr(orders, "_complete_workflow_ocr_run", forbidden)
    monkeypatch.setattr(orders.ocr_job_service, "update_job_for_workflow_attempt", forbidden)
    monkeypatch.setattr(orders.order_workflow_v2_service, "mark_ocr_run_completed", forbidden)
    monkeypatch.setattr(orders, "_is_job_stale", lambda job: True)
    monkeypatch.setattr(orders, "_get_ocr_stale_minutes", lambda: 30)
    monkeypatch.setattr(orders, "get_ocr_jobs", lambda ids: {job["id"]: job})
    monkeypatch.setattr(orders, "get_latest_order_job", lambda order_id: job)
    monkeypatch.setattr(orders, "get_ocr_job", lambda job_id: job)
    return job


@pytest.mark.parametrize("status", ["running", "failed", "done"])
def test_stale_diagnostic_keeps_exact_job_state_and_tokens(readonly_jobs, status):
    job = readonly_jobs
    job["status"] = status
    before = deepcopy(job)
    order = {"id": "order", "ocr_status": status, "lines": [{"quantity": 0}]}
    result = orders._mark_stale_order_reparse_job(order, job, force=True)
    assert result is job
    assert job == before
    assert order["ocr_status"] == status
    assert order["lines"] == [{"quantity": 0}]
    assert ("ocr_stale_diagnostic" in order) is (status == "running")


@pytest.mark.parametrize("include_ocr", [True, False])
def test_actual_list_get_never_completes_stale_job(monkeypatch, readonly_jobs, include_ocr):
    job = readonly_jobs
    before = deepcopy(job)
    order = {"id": "order", "ocr_job_id": job["id"], "lines": [{"quantity": 0}]}
    monkeypatch.setattr(orders.order_service, "list_orders_page", lambda **kwargs: {"orders": [order]})
    monkeypatch.setattr(orders.order_service, "_load_order_ocr_cache_map", lambda ids: {})
    monkeypatch.setattr(orders.order_service, "get_cached_ocr_payload", lambda order_id: None)
    monkeypatch.setattr(orders.workflow_state_service, "list_workflow_states", lambda ids: {})
    monkeypatch.setattr(orders, "_attach_order_review_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(orders, "_attach_order_workflow_context", lambda *args, **kwargs: None)
    result = orders.list_orders(include_ocr=include_ocr, include_runtime=True, limit=None)
    assert result["orders"][0]["ocr_status"] == "running"
    assert result["orders"][0]["lines"] == [{"quantity": 0}]
    assert job == before


def test_actual_detail_get_returns_persisted_failure_not_fake_running(monkeypatch, readonly_jobs):
    job = readonly_jobs
    job.update(status="failed", error_message="ReadTimeout")
    before = deepcopy(job)
    monkeypatch.setattr(orders.order_service, "get_order_by_id", lambda *args, **kwargs: {"id": "order", "lines": [{"quantity": 0}]})
    monkeypatch.setattr(orders, "_attach_order_workflow_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(orders, "_attach_order_review_summary", lambda *args, **kwargs: None)
    result = orders.get_order("order")
    assert result["ocr_status"] == "failed"
    assert result["ocr_error"] == "ReadTimeout"
    assert job == before


def test_cache_status_is_diagnostic_not_terminal_substitution(monkeypatch):
    monkeypatch.setattr(orders.order_service, "get_cached_ocr_payload", lambda order_id: {"status": "success", "pages": [{}]})
    order = {"id": "order", "ocr_status": "running"}
    assert orders._apply_cached_status_override(order, "order", None) is None
    assert order["ocr_status"] == "running"
    assert order["ocr_cache_diagnostic"]["status"] == "success"


@pytest.mark.parametrize("getter", [
    workflow_service.get_saved_sheet, workflow_service.get_inspection,
    workflow_service.list_ocr_results, workflow_service.build_sheet_from_selected_ocr,
    lambda order_id: workflow_service.get_sheet_auto_edit_job(order_id=order_id),
])
def test_workflow_gets_use_no_conditional_write_lock(monkeypatch, getter):
    session = Session()
    @contextmanager
    def readonly_session():
        with session:
            yield session
    def forbidden(*args, **kwargs):
        raise AssertionError("GET entered mutation workflow lock")
    monkeypatch.setattr(workflow_service, "session_scope", readonly_session)
    monkeypatch.setattr(workflow_service, "_get_order_or_error", lambda *args: (SimpleNamespace(id="order"), None))
    monkeypatch.setattr(workflow_service, "_get_workflow", forbidden)
    def readonly_lookup(actual_session, order_id):
        assert actual_session is session
        assert order_id == "order"
        raise LookupError("readonly_lookup_reached")
    monkeypatch.setattr(workflow_service, "_get_workflow_readonly", readonly_lookup)
    # Entry boundary proof; does not substitute for full ORM projection coverage.
    with pytest.raises(LookupError, match="readonly_lookup_reached"):
        getter("order")


def test_real_saved_sheet_get_emits_no_dml_or_bootstrap(isolated_formal_schema):
    from sqlalchemy import event
    from src.db import session_scope
    from src.models.order import Order, OrderLine
    from src.models.order_workflow_state import OrderWorkflowState

    order_id = "readonly-order"
    with session_scope() as session:
        session.add(Order(id=order_id, document_uri="explicit-source", message_id="explicit-message"))
        session.add(OrderLine(id="readonly-line", order_id=order_id, quantity_original=0, quantity_corrected=None))
    with session_scope() as session:
        bind = session.get_bind()
    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(bind, "before_cursor_execute", record)
    try:
        result, error = workflow_service.get_saved_sheet(order_id)
    finally:
        event.remove(bind, "before_cursor_execute", record)
    assert result is None
    assert error == "workflow_not_initialized"
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    with session_scope() as session:
        assert session.get(OrderWorkflowState, order_id) is None
        line = session.get(OrderLine, "readonly-line")
        assert line.quantity_original == 0
        assert line.quantity_corrected is None
