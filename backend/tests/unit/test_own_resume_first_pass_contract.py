from contextlib import contextmanager
from types import SimpleNamespace
from copy import deepcopy

import pytest

from src.services import order_service


@pytest.fixture
def own_resume_session(monkeypatch):
    job = SimpleNamespace(order_id="order", output_reference="own-output", metrics={})
    session = SimpleNamespace(get=lambda model, key: job)

    @contextmanager
    def scope():
        yield session

    monkeypatch.setattr(order_service, "session_scope", scope)
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda actual, job_id, order_id: actual is session and job_id == "actual-job" and order_id == "order")
    monkeypatch.setattr(order_service, "_payload_has_first_pass_ocr_content", lambda payload: bool(payload.get("tables")))

    def forbidden_active_read(*args, **kwargs):
        pytest.fail("Explicit own resume must not read active or cached evidence")

    monkeypatch.setattr(order_service, "_load_existing_first_pass_payload_for_reparse", forbidden_active_read)
    monkeypatch.setattr(order_service, "_get_ocr_output_without_legacy_edits", forbidden_active_read)


@pytest.mark.parametrize("old_active_present", [False, True])
def test_unreceipted_incompatible_schema_stops_without_active_substitution(monkeypatch, own_resume_session, old_active_present):
    # Active availability cannot change the explicit own-input decision.
    active_reads = []
    def active_read(*args, **kwargs):
        active_reads.append(args)
        return ({"tables": [{"rows": [[99]]}]} if old_active_present else None), None
    monkeypatch.setattr(order_service, "_load_active_ocr_payload", active_read)
    monkeypatch.setattr(order_service, "_payload_first_pass_schema_matches_template", lambda payload, template: False)
    result, error = order_service._resolve_own_resume_first_pass(
        "order", "actual-job", {"tables": [{"rows": [[0]]}]}, {},
        output_reference="own-output",
    )
    assert result is None
    assert error == "own_first_pass_delivery_mismatch"
    assert active_reads == []


def test_zero_values_do_not_authorize_an_unreceipted_result(monkeypatch, own_resume_session):
    monkeypatch.setattr(order_service, "_payload_first_pass_schema_matches_template", lambda payload, template: True)
    monkeypatch.setattr(order_service, "_annotate_payload_with_template_field_schema", lambda payload, template: payload)
    payload = {"tables": [{"rows": [[0, "0"]]}]}
    before = deepcopy(payload)
    result, error = order_service._resolve_own_resume_first_pass(
        "order", "actual-job", payload, {}, output_reference="own-output",
    )
    assert error == "own_first_pass_delivery_mismatch"
    assert result is None
    assert payload == before


def test_missing_own_result_does_not_read_active(own_resume_session):
    result, error = order_service._resolve_own_resume_first_pass("order", "actual-job", {}, {})
    assert result is None
    assert error == "own_first_pass_missing"


@pytest.mark.parametrize("changed_source", [False, True])
def test_omitted_resume_uses_exact_start_source_not_changed_active(monkeypatch, changed_source):
    from src.services.workflow_attempt_guard_service import captured_evidence_source

    payload = {"tables": [{"rows": [[0, "0"]]}]}
    values = {"id": "explicit-start-evidence", "order_id": "order", "payload_json": payload}
    source = captured_evidence_source("order", values)
    if changed_source:
        values["payload_json"] = {"tables": [{"rows": [[99]]}]}
    evidence = SimpleNamespace(**values)
    evidence.__table__ = SimpleNamespace(columns=[SimpleNamespace(key=key) for key in values])
    session = SimpleNamespace(get=lambda model, key: evidence if key == source["evidence_run_id"] else None)
    @contextmanager
    def scope():
        yield session
    monkeypatch.setattr(order_service, "session_scope", scope)
    monkeypatch.setattr(order_service, "_workflow_ocr_job_id", SimpleNamespace(get=lambda: "actual-job"))
    monkeypatch.setattr(order_service, "current_nonworkflow_execution", lambda: {"first_pass_source": source})
    monkeypatch.setattr(order_service, "_workflow_attempt_write_fence", lambda *args: True)
    monkeypatch.setattr(order_service.ocr_evidence_service, "evidence_run_is_selectable", lambda row: True)
    monkeypatch.setattr(order_service, "_payload_has_first_pass_ocr_content", lambda value: True)
    monkeypatch.setattr(order_service, "_payload_first_pass_schema_matches_template", lambda value, template: True)
    monkeypatch.setattr(order_service, "_annotate_payload_with_template_field_schema", lambda value, template: value)
    monkeypatch.setattr(order_service, "_get_ocr_output_without_legacy_edits", lambda *args, **kwargs: pytest.fail("active recapture"))
    result, error = order_service._load_existing_first_pass_payload_for_reparse("order", template={})
    if changed_source:
        assert result is None
        assert error == "first_pass_selected_input_mismatch"
    else:
        assert error is None
        assert result == payload
        assert result["tables"][0]["rows"][0] == [0, "0"]
