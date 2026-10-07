from types import SimpleNamespace

import pytest

from src.services import order_service
from src.services.workflow_attempt_guard_service import lock_selected_version_input, selected_version_snapshot


@pytest.fixture
def selected_version():
    values = {
        "id": "version", "order_id": "order", "document_id": "selected-document",
        "storage_uri": "original-uri", "version_no": 2, "is_current": False,
        "received_at": "2026-10-07T00:00:00",
        "line_snapshot": [{"quantity_original": 0, "quantity_corrected": None}],
    }
    row = SimpleNamespace(**values)
    row.__table__ = SimpleNamespace(columns=[SimpleNamespace(key=key) for key in values])
    return row


@pytest.mark.parametrize("change", [None, "storage_uri", "line_snapshot", "missing"])
def test_shared_guard_compares_exact_selected_version(selected_version, change):
    captured = selected_version_snapshot(selected_version)
    if change == "storage_uri":
        selected_version.storage_uri = "changed-uri"
    elif change == "line_snapshot":
        selected_version.line_snapshot = [{"quantity_original": 12}]
    session = SimpleNamespace(get=lambda model, key, **kwargs: None if change == "missing" else selected_version)
    assert lock_selected_version_input(session, "order", captured) is (change is None)
    assert captured["line_snapshot"] == [{"quantity_original": 0, "quantity_corrected": None}]


def test_after_capture_selection_uses_original_uri_without_version_reread(monkeypatch, selected_version):
    captured = selected_version_snapshot(selected_version)
    selected_version.storage_uri = "changed-uri"
    monkeypatch.setattr(order_service, "_workflow_ocr_job_id", SimpleNamespace(get=lambda: "actual-job"))
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args: True)
    monkeypatch.setattr(order_service, "current_nonworkflow_execution", lambda: {
        "selected_version_input": captured, "input_reference": "original-uri",
    })
    session = SimpleNamespace(get=lambda *args, **kwargs: pytest.fail("input re-selection"))
    uri, metadata = order_service._captured_order_document_selection(session, SimpleNamespace(id="order"), "selected-document")
    assert uri == "original-uri"
    assert metadata["selected_document_version"] == 2


def test_changed_input_stops_before_returning_uri(monkeypatch):
    monkeypatch.setattr(order_service, "_workflow_ocr_job_id", SimpleNamespace(get=lambda: "actual-job"))
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args: False)
    uri, metadata = order_service._captured_order_document_selection(None, SimpleNamespace(id="order"), "selected-document")
    assert uri is None
    assert metadata["error"] == "selected_document_input_changed"
