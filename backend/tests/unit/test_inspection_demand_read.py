"""Demand reads retain the full inspection contract at the point of use."""

import ast
from datetime import datetime
import json
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import event

from src.db import session_scope
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.ocr_job import OcrJob
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_workflow_v2_service as service
from src.services.ocr_history_read import read_history, read_history_detail, read_history_summary
from tests.helpers.authored_sheet_case import authored_sheet_case  # noqa: F401
from test_order_workflow_v2_service import (
    _create_order_with_evidence,
    _stamp_evidence_with_workflow_template,
    _standard_active_template_for_workflow_tests,
    authored_april_context,
    test_inspection_is_read_only_projection_of_current_lineage as seed_saved_lineage,
)


@pytest.fixture(autouse=True)
def isolated(isolated_formal_schema, monkeypatch):
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 4, 26, 12))
    monkeypatch.setattr(
        service._get_order_service_module(), "_signed_url_from_uri",
        lambda uri: "https://signed.invalid/" + uri.rsplit("/", 1)[-1],
    )
    return isolated_formal_schema


@pytest.fixture(scope="module")
def base_readers():
    source = subprocess.check_output(
        ["git", "show", "5e30eefee4f5daddc0f97b6c29cbd1ddc5172496:backend/src/services/order_workflow_v2_service.py"],
        cwd=Path(__file__).resolve().parents[3], text=True,
    )
    wanted = {"get_inspection", "get_workflow_readonly"}
    nodes = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert {node.name for node in nodes} == wanted
    for node in nodes:
        node.decorator_list = []
    namespace = dict(vars(service))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "base:workflow_reads", "exec"), namespace)
    return namespace["get_inspection"], namespace["get_workflow_readonly"]


@pytest.mark.parametrize("state", ["empty", "no_selected", "selected", "foreign_selected", "saved"])
def test_summary_full_and_detail_parity(state, authored_april_context, isolated, base_readers):
    if state == "saved":
        seed_saved_lineage(authored_april_context)
        with session_scope() as session:
            workflow = session.query(OrderWorkflowState).filter(OrderWorkflowState.draft_id.isnot(None)).one()
            order_id = workflow.order_id
    else:
        order_id, first, second = _create_order_with_evidence()
        foreign = _create_order_with_evidence()[1] if state == "foreign_selected" else None
        if state in {"selected", "foreign_selected"}:
            _, error = service.confirm_context(
                order_id=order_id, expected_revision=0, facility_id="FAC00001",
                week_start="2026-04-26", week_end="2026-04-30", template_id="template-fac00001",
            )
            assert error is None
            with session_scope() as session:
                workflow = session.get(OrderWorkflowState, order_id)
                workflow.evidence_run_id = first if state == "selected" else foreign
                workflow.state = "ocr_selected"
        if state == "empty":
            with session_scope() as session:
                session.query(OrderOcrEvidenceRun).filter_by(order_id=order_id).delete()
    base_inspection, base_workflow_readonly = base_readers
    baseline, baseline_error = base_inspection(order_id)
    assert baseline_error is None
    full, error = service.get_inspection(order_id)
    assert error is None
    workflow, workflow_error = service.get_workflow(order_id)
    assert workflow_error is None
    expected_workflow, expected_workflow_error = base_workflow_readonly(order_id)
    assert expected_workflow_error is None
    assert full["workflow"] == expected_workflow == workflow
    assert {key: value for key, value in full.items() if key != "workflow"} == {
        key: value for key, value in baseline.items() if key != "workflow"
    }
    assert service.get_inspection(order_id, history_details=True) == (full, None)
    statements = []
    def track(conn, cursor, statement, parameters, context, executemany):
        if "FROM order_ocr_evidence_runs" in statement:
            statements.append((statement, parameters))
    event.listen(isolated, "before_cursor_execute", track)
    try:
        summary, error = service.get_inspection(order_id, history_details=False)
    finally:
        event.remove(isolated, "before_cursor_execute", track)
    assert error is None
    assert {k: v for k, v in summary.items() if k != "ocr_results"} == {
        k: v for k, v in full.items() if k != "ocr_results"
    }
    assert [r["ocr_result_id"] for r in summary["ocr_results"]] == [
        r["ocr_result_id"] for r in full["ocr_results"]
    ]
    # All large-column reads must be single-row lookups, never the history query.
    history_queries = [sql for sql, _ in statements if "ORDER BY" in sql and "order_ocr_evidence_runs.id =" not in sql]
    assert len(history_queries) == 1
    assert all(name not in history_queries[0] for name in (
        "payload_json", "artifact_manifest_json", "capabilities_json", "degraded_reasons_json",
    ))
    for small, expected in zip(summary["ocr_results"], full["ocr_results"]):
        if expected["selected"]:
            assert json.dumps(small, ensure_ascii=True) == json.dumps(expected, ensure_ascii=True)
        else:
            assert small["detail_state"] == "not_loaded"
            assert "selectable" not in small
            assert "overlay_status" not in small
            assert "artifact_manifest" not in small
            assert all(expected[k] == v for k, v in small.items() if k != "detail_state")
        detail, error = service.get_inspection_detail(order_id, small["ocr_result_id"])
        assert error is None
        assert detail.pop("workflow_revision") == full["workflow"]["revision"]
        assert json.dumps(detail, ensure_ascii=True) == json.dumps(expected, ensure_ascii=True)


def test_order_boundary_old_template_and_fresh_validation(monkeypatch):
    order, first, second = _create_order_with_evidence()
    other, foreign, _ = _create_order_with_evidence()
    assert service.get_inspection_detail(order, foreign) == (None, "ocr_result_not_found")
    assert service.get_inspection_detail("absent", first) == (None, "order_not_found")
    assert service.get_inspection_detail(order, "absent") == (None, "ocr_result_not_found")
    calls = []
    original = service.ocr_evidence_service.evidence_run_is_selectable
    def validate(row, **kwargs):
        calls.append(row.id)
        return original(row, **kwargs)
    monkeypatch.setattr(service.ocr_evidence_service, "evidence_run_is_selectable", validate)
    for _ in range(2):
        detail, error = service.get_inspection_detail(order, first)
        assert error is None
        assert detail["workflow_revision"] == 0
        assert detail["selectable"] is True
    assert calls == [first, first]
    with session_scope() as session:
        row = session.get(OrderOcrEvidenceRun, first)
        row.payload_json = {"hakodate_overlay": {"uri": "gs://synthetic/new.png"}}
    detail, error = service.get_inspection_detail(order, first)
    assert error is None
    assert detail["overlay_url"] == "https://signed.invalid/new.png"


def test_history_projection_preserves_database_and_order():
    order, first, second = _create_order_with_evidence()
    payload = {"z": 1, "diagnostic_pipeline_output": {"large": [1, 2]}, "a": "\x00"}
    with session_scope() as session:
        for id in (first, second):
            row = session.get(OrderOcrEvidenceRun, id)
            row.created_at = datetime(2026, 4, 26)
            row.payload_json = payload
    with session_scope() as session:
        full = read_history(session, order)
        summary = read_history_summary(session, order)
        assert [r.id for r in full] == [r.id for r in summary] == sorted([first, second], reverse=True)
        assert not any(hasattr(r, "payload_json") for r in summary)
        for row in full:
            assert vars(read_history_detail(session, order, row.id)) == vars(row)
            assert list(row.payload_json) == ["z", "a"]
            assert session.get(OrderOcrEvidenceRun, row.id).payload_json == payload


def test_api_auth_flags_and_detail_contract(monkeypatch, authored_april_context):
    from fastapi.testclient import TestClient
    from src.main import app
    from tests.contract.auth_support import registered_hospital_operator
    order, first, second = _create_order_with_evidence()
    other, foreign, _ = _create_order_with_evidence()
    client = TestClient(app)
    url = f"/orders/{order}/workflow-v2/inspection"
    detail_url = f"/orders/{order}/workflow-v2/inspection-detail/{first}"
    progress_url = f"/orders/{order}/workflow-v2/ocr-progress"
    with registered_hospital_operator(monkeypatch) as operator:
        full = client.get(url, headers=operator.headers)
        assert full.status_code == 200
        assert client.get(url + "?history_details=true", headers=operator.headers).json() == full.json()
        small = client.get(url + "?history_details=false", headers=operator.headers)
        assert small.status_code == 200
        assert all(r["detail_state"] == "not_loaded" for r in small.json()["ocr_results"])
        detail = client.get(detail_url, headers=operator.headers)
        assert detail.status_code == 200
        value = detail.json()
        assert value.pop("workflow_revision") == 0
        assert value == next(r for r in full.json()["ocr_results"] if r["ocr_result_id"] == first)
        assert client.get(detail_url.replace(first, foreign), headers=operator.headers).status_code == 404
        assert client.get(progress_url, headers=operator.headers).status_code == 200
        assert client.get(url + "?history_details=invalid", headers=operator.headers).status_code == 422
        assert client.get(detail_url).status_code == 401
        assert client.get(progress_url).status_code == 401
        assert client.get(url + "?history_details=false").status_code == 401
    with registered_hospital_operator(monkeypatch, systems=()) as denied:
        assert client.get(detail_url, headers=denied.headers).status_code == 403
        assert client.get(progress_url, headers=denied.headers).status_code == 403
        assert client.get(url + "?history_details=false", headers=denied.headers).status_code == 403


def test_candidate_sidecar_filters_current_template_without_loading_all_details(authored_april_context):
    order, first, _ = _create_order_with_evidence()
    context, error = service.confirm_context(
        order_id=order, expected_revision=0, facility_id="FAC00001",
        week_start="2026-04-26", week_end="2026-04-30", template_id="template-fac00001",
    )
    assert error is None
    with session_scope() as session:
        session.get(OrderWorkflowState, order).evidence_run_id = first
        session.get(OrderWorkflowState, order).state = "ocr_selected"
    template_version_id = context["template_version_id"]
    assert _stamp_evidence_with_workflow_template(order, first) == template_version_id
    summary, error = service.get_inspection(order, include_ocr_results=True, history_details=False)
    assert error is None
    candidates = summary["ocr_candidates"]
    full, error = service.get_inspection(order, include_ocr_results=True, history_details=True)
    assert error is None
    assert len(candidates["results"]) == 1
    assert [item["ocr_result_id"] for item in candidates["results"]] == [first]
    assert [item["ocr_result_id"] for item in full["ocr_candidates"]["results"]] == [first]
    assert candidates["hidden_template_mismatch_result_count"] == 1
    assert candidates["hidden_template_mismatch_result_count"] == full["ocr_candidates"]["hidden_template_mismatch_result_count"]
    assert candidates["blockers"] == full["ocr_candidates"]["blockers"]
    assert candidates["candidate_template_version_id"] == full["ocr_candidates"]["candidate_template_version_id"] == template_version_id
    assert candidates["selected_ocr_result_id"] == first
    assert candidates["selected_ocr_result_id"] == full["ocr_candidates"]["selected_ocr_result_id"]
    assert candidates["workflow_state"] == summary["workflow"]["state"]
    assert all(item["template_version_id"] == candidates["candidate_template_version_id"] for item in candidates["results"])
    assert all(item.get("detail_state") != "not_loaded" for item in candidates["results"] if item["selected"])
    assert all(item.get("detail_state") == "not_loaded" for item in candidates["results"] if not item["selected"])


def test_ocr_progress_reads_only_the_recorded_owned_job(monkeypatch, authored_sheet_case):
    case = authored_sheet_case(quantity=5, produce=False, facility="inspection-progress-component")
    case.queue()
    order_id = case.id
    attempt = case.queued["ocr_job"]["workflow_attempt_id"]
    calls = []
    original = service._recorded_workflow_job_status
    def recorded(*args):
        calls.append(args[0].order_id)
        return original(*args)
    monkeypatch.setattr(service, "_recorded_workflow_job_status", recorded)
    progress, error = service.get_ocr_progress(order_id)
    assert error is None
    assert progress["order_id"] == order_id
    assert progress["state"] == "ocr_running"
    assert progress["ocr_job"]["ocr_job_id"] == case.job
    assert progress["ocr_job"]["workflow_attempt_id"] == attempt
    assert calls == [order_id]
    assert progress["common_job_status"]["jobId"] == case.job
    assert progress["common_job_status"]["attemptIdentity"] == attempt

    with session_scope() as session:
        job = session.get(OcrJob, case.job)
        job.metrics = {**job.metrics, "workflow_attempt_id": "other"}
    assert service.get_ocr_progress(order_id) == (None, "ocr_recorded_job_attempt_mismatch")


def test_ocr_progress_refuses_fabricated_native_owner_and_terminal_receipt():
    order_id, _, _ = _create_order_with_evidence()
    with session_scope() as session:
        session.add(OrderWorkflowState(order_id=order_id, revision=1, state="ocr_running",
            secondary_actions_json={service.WORKFLOW_V2_META_KEY: {"ocr_job_id": "native-progress-job"}}))
        session.add(OcrJob(
            id="native-progress-job", order_id=order_id, status="running", input_reference="gs://test/input.pdf",
            metrics={
                "workflow_attempt_id": None,
                "nonworkflow_execution": {"ingest_ocr_mode": "hakodate", "execution_id": "native-1", "intake_protocol": "native_ingest_v2"},
                "rerun_terminal_receipt": {"status": "running"},
            },
            created_at=datetime(2026, 4, 26, 12), updated_at=datetime(2026, 4, 26, 12),
        ))
    progress, error = service.get_ocr_progress(order_id)
    assert progress is None
    assert error == "ocr_recorded_job_input_missing"
    with session_scope() as session:
        job = session.get(OcrJob, "native-progress-job")
        assert job.status == "running"
        assert job.metrics["nonworkflow_execution"]["execution_id"] == "native-1"
        assert job.metrics["rerun_terminal_receipt"] == {"status": "running"}
