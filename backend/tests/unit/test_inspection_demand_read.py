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
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_workflow_v2_service as service
from src.services.ocr_history_read import read_history, read_history_detail, read_history_summary
from test_order_workflow_v2_service import (
    _create_order_with_evidence,
    _standard_active_template_for_workflow_tests,
    authored_april_context,
    test_inspection_is_read_only_projection_of_current_lineage as seed_saved_lineage,
)


@pytest.fixture(scope="module")
def base_inspection():
    source = subprocess.check_output(
        ["git", "show", "6e2d0856088ffff99966c6d97e4a46037b4ac5ea:backend/src/services/order_workflow_v2_service.py"],
        cwd=Path(__file__).resolve().parents[3], text=True,
    )
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "get_inspection")
    node.decorator_list = []
    namespace = dict(vars(service))
    exec(compile(ast.Module(body=[node], type_ignores=[]), "base:get_inspection", "exec"), namespace)
    return namespace["get_inspection"]


@pytest.fixture(autouse=True)
def isolated(isolated_formal_schema, monkeypatch):
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 4, 26, 12))
    monkeypatch.setattr(
        service._get_order_service_module(), "_signed_url_from_uri",
        lambda uri: "https://signed.invalid/" + uri.rsplit("/", 1)[-1],
    )
    return isolated_formal_schema


@pytest.mark.parametrize("state", ["empty", "no_selected", "selected", "missing_selected", "saved"])
def test_summary_full_and_detail_parity(state, authored_april_context, isolated, base_inspection):
    if state == "saved":
        seed_saved_lineage(authored_april_context)
        with session_scope() as session:
            workflow = session.query(OrderWorkflowState).filter(OrderWorkflowState.draft_id.isnot(None)).one()
            order_id = workflow.order_id
    else:
        order_id, first, second = _create_order_with_evidence()
        if state in {"selected", "missing_selected"}:
            _, error = service.confirm_context(
                order_id=order_id, expected_revision=0, facility_id="FAC00001",
                week_start="2026-04-26", week_end="2026-04-30", template_id="template-fac00001",
            )
            assert error is None
            with session_scope() as session:
                workflow = session.get(OrderWorkflowState, order_id)
                workflow.evidence_run_id = first if state == "selected" else "absent"
                workflow.state = "ocr_selected"
        if state == "empty":
            with session_scope() as session:
                session.query(OrderOcrEvidenceRun).filter_by(order_id=order_id).delete()
    full, error = service.get_inspection(order_id)
    assert error is None
    base_full, base_error = base_inspection(order_id)
    assert base_error is None
    assert json.dumps(full, ensure_ascii=True) == json.dumps(base_full, ensure_ascii=True)
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


def test_api_auth_flags_and_detail_contract(monkeypatch):
    from fastapi.testclient import TestClient
    from src.main import app
    from tests.contract.auth_support import registered_hospital_operator
    order, first, second = _create_order_with_evidence()
    other, foreign, _ = _create_order_with_evidence()
    client = TestClient(app)
    url = f"/orders/{order}/workflow-v2/inspection"
    detail_url = f"/orders/{order}/workflow-v2/inspection-detail/{first}"
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
        assert client.get(url + "?history_details=invalid", headers=operator.headers).status_code == 422
        assert client.get(detail_url).status_code == 401
        assert client.get(url + "?history_details=false").status_code == 401
    with registered_hospital_operator(monkeypatch, systems=()) as denied:
        assert client.get(detail_url, headers=denied.headers).status_code == 403
        assert client.get(url + "?history_details=false", headers=denied.headers).status_code == 403


def test_candidate_sidecar_keeps_full_contract():
    order, _, _ = _create_order_with_evidence()
    assert service.get_inspection(order, include_ocr_results=True, history_details=False) == (
        service.get_inspection(order, include_ocr_results=True)
    )
