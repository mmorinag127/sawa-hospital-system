"""Recorded-job projection, owned SQLite/HTTP read and serializer contracts.

Jobs are explicitly authored records. HTTP uses the unit suite's auth-disabled
mode without lifespan; this is not proof of OCR execution or real auth.
"""

import ast
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from src import db
from src.models.ocr_job import OcrJob
from src.models.order_workflow_state import OrderWorkflowState
from src.services import ocr_job_service, order_service
from src.services import order_workflow_v2_service as workflow
from src.services import recorded_hospital_job_status as adapter
from test_modernization_explicit_ocr_quantity_contract import PublicSavedCandidate
from tests.helpers.authored_sheet_case import authored_sheet_case  # noqa: F401

BASE = "4d0e6319d3e84e392389755041a89a872ff97d29"
ROOT = Path(__file__).resolve().parents[3]


def functions(text):
    return {node.name: node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}


def original_functions(path, *, source=BASE):
    return functions(subprocess.check_output(["git", "show", f"{source}:{path}"], cwd=ROOT).decode())


def test_single_adapter_is_exact_original_and_both_consumers_share_it():
    name = "_recorded_hospital_job_status"
    original = original_functions("backend/src/services/order_service.py")[name]
    current = functions(Path(adapter.__file__).read_text())[name]
    assert ast.dump(current) == ast.dump(original)
    assert order_service._recorded_hospital_job_status is adapter._recorded_hospital_job_status
    assert workflow._recorded_hospital_job_status is adapter._recorded_hospital_job_status


def test_workflow_serializer_preserves_approved_receipt_and_recorded_job_projection():
    # Scope this extraction contract to the adapter addition. Approved workflow,
    # receipt, saved-source and CAS fixes have their own behavioral regressions.
    path = "backend/src/services/order_workflow_v2_service.py"
    original = original_functions(path, source="9d046f3be08ed895ca5cc791a3576812a2de9412")["_serialize_workflow"]
    current = functions((ROOT / path).read_text())["_serialize_workflow"]
    original_result = next(node for node in original.body if isinstance(node, ast.Return)).value
    result = next(node for node in current.body if isinstance(node, ast.Return)).value
    old_keys = {key.value for key in original_result.keys if isinstance(key, ast.Constant)}
    current_keys = [key.value for key in result.keys if isinstance(key, ast.Constant)]
    assert old_keys <= set(current_keys)
    assert current_keys.count("common_job_status") == 1
    index = next(i for i, key in enumerate(result.keys)
                 if isinstance(key, ast.Constant) and key.value == "common_job_status")
    assert ast.unparse(result.values[index]) == "_recorded_workflow_job_status(row, meta, ocr_job)"
    assert ast.dump(current) == ast.dump(original)
    assert callable(workflow._recorded_workflow_job_status)
    assert order_service._recorded_hospital_job_status is workflow._recorded_hospital_job_status


def recorded_pair(attempt="attempt-A", status="done"):
    row = OrderWorkflowState(order_id="order-A", revision=7, state="confirmed", headline="existing headline",
                             blockers_json=["existing blocker"], warnings_json=["existing warning"])
    meta = {"ocr_job_id": "selected-A", "ocr_job_attempt_id": attempt}
    job = OcrJob(id="selected-A", order_id="order-A", status=status,
                 input_reference=" raw input ", input_artifact_digest=" recorded digest ",
                 output_reference=" saved artifact ", metrics={"workflow_attempt_id": attempt},
                 created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    return row, meta, job


@pytest.mark.parametrize("attempt", [None, 0, "0", " raw attempt "])
@pytest.mark.parametrize("status,expected", [("done", "succeeded"), ("failed", "failed"),
                                           ("unknown raw", None), ("completed", None)])
def test_projection_preserves_recorded_raw_missing_zero_and_never_borrows_workflow_state(attempt, status, expected):
    row, meta, job = recorded_pair(attempt, status)
    job.error_message = " raw error " if status == "failed" else None
    before = deepcopy(ocr_job_service._job_to_dict(job))
    result = workflow._serialize_workflow(row, ocr_job=job, meta_override=meta)
    record = result["common_job_status"]
    assert record == {
        "domain": "hospital", "jobId": "selected-A", "state": expected,
        "sourceState": status, "sourceLabel": {"done": "完了", "completed": "完了", "failed": "失敗"}.get(status, status),
        "inputVersion": " recorded digest ", "inputReference": " raw input ",
        "attemptIdentity": attempt, "artifactReference": " saved artifact ",
        "failure": {"code": None, "message": " raw error "} if status == "failed" else None,
    }
    assert type(record["attemptIdentity"]) is type(attempt)
    assert result["state"] == "confirmed"
    assert result["headline"] == "existing headline"
    assert result["blockers"] == ["existing blocker"]
    assert result["warnings"] == ["existing warning"]
    assert result["ocr_job"] == workflow._serialize_ocr_job(job)
    assert ocr_job_service._job_to_dict(job) == before


@pytest.mark.parametrize("fault", ["no-job", "wrong-job", "missing-selected", "wrong-order", "missing-order",
                                   "wrong-attempt", "missing-attempt", "attempt-type", "bad-metrics"])
def test_missing_or_foreign_ownership_never_exposes_common_projection(fault):
    row, meta, job = recorded_pair()
    if fault == "no-job":
        job = None
    elif fault == "wrong-job":
        job.id = "latest-but-not-selected"
    elif fault == "missing-selected":
        meta.pop("ocr_job_id")
    elif fault == "wrong-order":
        job.order_id = "foreign-order"
    elif fault == "missing-order":
        job.order_id = None
    elif fault == "wrong-attempt":
        job.metrics["workflow_attempt_id"] = "newer-attempt"
    elif fault == "missing-attempt":
        job.metrics = {}
    elif fault == "attempt-type":
        meta["ocr_job_attempt_id"] = 0
        job.metrics["workflow_attempt_id"] = False
    else:
        job.metrics = "not a metadata object"
    assert workflow._serialize_workflow(row, ocr_job=job, meta_override=meta)["common_job_status"] is None


def test_owned_record_without_optional_references_remains_missing():
    row, meta, job = recorded_pair(None, "pending")
    job.metrics = None
    meta.pop("ocr_job_attempt_id")
    job.input_reference = job.output_reference = job.input_artifact_digest = None
    record = workflow._serialize_workflow(row, ocr_job=job, meta_override=meta)["common_job_status"]
    assert record["state"] == "pending"
    for field in ["inputVersion", "inputReference", "attemptIdentity", "artifactReference"]:
        assert record[field] is None


def snapshot(fixture):
    with fixture.engine.connect() as connection:
        return {table.name: deepcopy([dict(row) for row in connection.execute(
            select(table).order_by(*table.primary_key.columns)).mappings()])
            for table in db.Base.metadata.sorted_tables}


def test_readonly_workflow_refuses_unowned_job_pointer_without_borrowing_newer_record(tmp_path, monkeypatch):
    fixture = PublicSavedCandidate(tmp_path, monkeypatch)
    try:
        fixture.save(["7", "", "", "0"], enabled=True)
        now = datetime.utcnow()
        with fixture.session_scope() as session:
            row = session.get(OrderWorkflowState, fixture.order_id)
            meta = workflow._workflow_meta(row)
            meta.update(ocr_job_id="selected-A", ocr_job_attempt_id="attempt-A")
            row.secondary_actions_json = {**row.secondary_actions_json, workflow.WORKFLOW_V2_META_KEY: meta}
            for job_id, attempt, status, created in [
                ("selected-A", "attempt-A", "failed", now - timedelta(minutes=1)),
                ("newer-B", "attempt-B", "done", now),
            ]:
                session.add(OcrJob(id=job_id, order_id=fixture.order_id, status=status,
                                  input_reference=f"recorded-{job_id}", input_artifact_digest="digest-A",
                                  output_reference=f"artifact-{job_id}", metrics={"workflow_attempt_id": attempt},
                                  error_message="selected failure" if status == "failed" else None,
                                  created_at=created, updated_at=created))
        before = snapshot(fixture)
        statements = []

        def observe(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement.lstrip().split(None, 1)[0].upper())

        event.listen(fixture.engine, "before_cursor_execute", observe)
        try:
            result, error = workflow.get_workflow_readonly(fixture.order_id)
            from src.main import app

            client = TestClient(app)
            try:
                response = client.get(f"/orders/{fixture.order_id}/workflow-v2")
            finally:
                client.close()
        finally:
            event.remove(fixture.engine, "before_cursor_execute", observe)
        assert error is None
        assert response.status_code == 200, response.text
        assert response.json()["common_job_status"] == result["common_job_status"]
        assert response.json()["ocr_job"] == result["ocr_job"]
        assert result["ocr_job"] is None
        assert result["common_job_status"] is None
        assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE"}
        assert snapshot(fixture) == before
        assert [row[4] for row in fixture.saved()["rows"]] == ["7", "", "", "0"]
    finally:
        fixture.engine.dispose()


def test_real_owned_failed_job_read_does_not_borrow_newer_unowned_record(authored_sheet_case):
    fixture = authored_sheet_case(quantity=0, include_remarks=True,
        cell_values=lambda region: "" if region["role"] == "note" else "0")
    fixture.save(fixture.selected_sheet)
    saved = order_service.get_current_sheet_context(fixture.id)
    fixture.prepare_provider()
    selected_job = fixture.job
    attempt = fixture.queued["ocr_job"]["workflow_attempt_id"]
    with fixture.execution():
        _result, error = order_service._persist_ocr_rerun_completion(
            fixture.id, selected_job, None, error="selected failure")
    assert error == "selected failure"
    now = datetime.utcnow() + timedelta(minutes=1)
    with db.session_scope() as session:
        session.add(OcrJob(id="newer-unowned", order_id=fixture.id, status="done",
            metrics={"workflow_attempt_id": "unowned-attempt"}, created_at=now, updated_at=now))
    before = snapshot(fixture)
    with fixture.readonly():
        result, error = workflow.get_workflow_readonly(fixture.id)
        response = fixture.get("workflow-v2")
        assert error is None
        assert response["common_job_status"] == result["common_job_status"]
        assert response["ocr_job"] == result["ocr_job"]
        assert result["ocr_job"]["ocr_job_id"] == selected_job
        assert result["common_job_status"]["jobId"] == selected_job
        assert result["common_job_status"]["attemptIdentity"] == attempt
        assert result["common_job_status"]["state"] == "failed"
        assert result["common_job_status"]["failure"]["message"] == "selected failure"
        current = order_service.get_current_sheet_context(fixture.id)
        assert current["draft_id"] == saved["draft_id"]
        assert current["rows"] == saved["rows"] == [["03/22", "朝", "Menu A", "0", ""]]
    assert snapshot(fixture) == before
