"""Recorded-status component definitions; no provider/operator-flow proof."""

import ast
import subprocess
from pathlib import Path

import pytest

from src.services import order_service as service

BASE = "9d046f3be08ed895ca5cc791a3576812a2de9412"
ROOT = Path(__file__).resolve().parents[3]


def test_business_function_ast_unchanged():
    path = "backend/src/services/order_service.py"
    before = ast.parse(subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT).decode())
    after = ast.parse((ROOT / path).read_text())
    from src.services import recorded_hospital_job_status as adapter
    assert service._recorded_hospital_job_status is adapter._recorded_hospital_job_status
    old = next(node for node in before.body if getattr(node, "name", None) == "get_hakodate_pipeline_job_status")
    new = next(node for node in after.body if getattr(node, "name", None) == old.name)
    assert ast.dump(old.args) == ast.dump(new.args)
    assert ast.dump(old.returns) == ast.dump(new.returns)
    assert ast.dump(new) == ast.dump(old)


def test_error_record_and_missing_fields_are_not_inferred():
    job = {"id": "own", "status": "failed", "error_message": "saved failure", "metrics": {}}
    result = service._recorded_hospital_job_status(job, {"status": "hard_failed"})
    assert result["failure"] == {"code": None, "message": "saved failure"}
    assert result["inputVersion"] is None
    assert result["inputReference"] is None
    assert result["artifactReference"] is None
    assert result["attemptIdentity"] is None


def test_recorded_values_and_zero_are_not_replaced():
    job = {"id": "own", "status": "done", "input_artifact_digest": "captured-digest",
           "input_reference": "captured-input", "output_reference": "saved-output",
           "metrics": {"workflow_attempt_id": 0}}
    actual = service._recorded_hospital_job_status(job, {"status": "done"})
    assert actual == {"domain": "hospital", "jobId": "own", "inputVersion": "captured-digest",
                      "inputReference": "captured-input", "attemptIdentity": 0,
                      "state": "succeeded", "sourceState": "done", "sourceLabel": "完了",
                      "failure": None, "artifactReference": "saved-output"}


@pytest.mark.parametrize("value", [None, 0, "0", " raw reference "])
def test_json_compatible_record_values_keep_type_and_spelling(value):
    job = {"id": "own", "status": "running", "input_artifact_digest": value,
           "input_reference": value, "output_reference": value,
           "metrics": {"workflow_attempt_id": value}}
    result = service._recorded_hospital_job_status(job, {"status": "running"})
    for key in ("inputVersion", "inputReference", "artifactReference", "attemptIdentity"):
        assert result[key] == value
        assert type(result[key]) is type(value)


@pytest.mark.parametrize("raw", ["success", "completed", "DONE", " done ", None])
def test_described_done_requires_exact_saved_done(raw):
    result = service._recorded_hospital_job_status({"id": "own", "status": raw}, {"status": "done"})
    assert result["state"] is None
    assert result["sourceState"] == raw


@pytest.mark.parametrize("status", ["awaiting_output", "recovering", "stalled", "unknown", "success", "completed"])
def test_unresolved_states_never_become_success(status):
    result = service._recorded_hospital_job_status({"id": "own", "status": status}, {"status": status})
    assert result["state"] is None
    assert result["sourceState"] == status
    assert result["inputVersion"] is None
    assert result["attemptIdentity"] is None


def test_effective_failure_does_not_claim_saved_done_success():
    result = service._recorded_hospital_job_status({"id": "own", "status": "done"}, {"status": "hard_failed"})
    assert result["state"] == "failed"
    assert result["sourceState"] == "done"
    assert result["sourceLabel"] == "失敗"


@pytest.mark.parametrize("job_id,order_id,expected", [("own", "order", "execution"), ("foreign", "order", None), ("own", "foreign", None)])
def test_nonworkflow_ownership_only_from_record(job_id, order_id, expected):
    job = {"id": "own", "order_id": "order", "metrics": {"nonworkflow_execution": {
        "job_id": job_id, "order_id": order_id, "execution_id": "execution"}}}
    assert service._recorded_hospital_job_status(job, {})["attemptIdentity"] == expected


def test_competing_ownership_is_not_selected():
    job = {"id": "own", "order_id": "order", "metrics": {"workflow_attempt_id": "workflow",
           "nonworkflow_execution": {"job_id": "own", "order_id": "order", "execution_id": "other"}}}
    assert service._recorded_hospital_job_status(job, {})["attemptIdentity"] is None


@pytest.mark.parametrize("job", [{}, {"id": None}, {"id": ""}])
def test_no_fabricated_job_id(job):
    assert service._recorded_hospital_job_status(job, {}) is None


def test_existing_response_fields_and_recorded_job_binding(monkeypatch):
    job = {"id": "own", "status": "running", "metrics": {"request_mode": "ocr_reparse"}}
    state = {"status": "running", "job_id": "own", "blockers": ["unchanged"]}
    monkeypatch.setattr(service, "get_recorded_order_job", lambda order_id: job)
    monkeypatch.setattr(service, "describe_ocr_job_state", lambda value: state if value is job else {"status": "idle"})
    result = service.get_hakodate_pipeline_job_status("order")
    assert {key: value for key, value in result.items() if key != "job_status"} == {
        **state, "request_mode": "ocr_reparse", "processing_stage": None,
        "result_state": None, "error": None, "output_reference": None}
    job["metrics"]["request_mode"] = "other"
    current = service.get_hakodate_pipeline_job_status("order")
    assert current["request_mode"] == "other" and current["status"] == "running"
    assert current["job_status"]["jobId"] == "own"
    monkeypatch.setattr(service, "get_recorded_order_job", lambda order_id: None)
    assert service.get_hakodate_pipeline_job_status("order") == {"status": "idle", "job_status": None}
    def rejected(order_id):
        raise ValueError("ocr_recorded_job_input_changed")
    monkeypatch.setattr(service, "get_recorded_order_job", rejected)
    assert service.get_hakodate_pipeline_job_status("order") == {
        "status": "blocked", "blockers": ["ocr_recorded_job_input_changed"], "job_status": None}
