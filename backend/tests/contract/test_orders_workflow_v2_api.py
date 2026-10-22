"""HTTP/service-boundary contracts; enqueue spies are not persisted OCR proof."""

from __future__ import annotations

import pathlib
import sys
import os
from contextlib import contextmanager
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src import db  # noqa: E402
from src.main import app  # noqa: E402
from tests.contract.auth_support import hospital_admin, hospital_operator  # noqa: E402, F401


def test_workflow_v2_facility_template_columns_validation_error_is_actionable(monkeypatch) -> None:
    client = TestClient(app)

    def fake_save(order_id: str, columns: list[dict], *, expected_revision: int) -> tuple[dict, str]:
        assert expected_revision == 7
        _ = order_id, columns
        return {
            "validation": {
                "errors": ["template_source_index_missing"],
                "warnings": [],
            },
        }, "validation_error"

    monkeypatch.setattr(orders_api.order_workflow_v2_service, "save_facility_template_columns", fake_save)

    res = client.put(
        "/orders/ORDcontract/workflow-v2/facility-template-columns",
        headers={"If-Match": "7"},
        json={"columns": [{"role": "quantity", "header": "常食"}]},
    )

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["error"] == "validation_error"
    assert detail["validation"]["errors"] == ["template_source_index_missing"]


def test_workflow_v2_ocr_run_blocks_unresolved_template_before_job_enqueue(monkeypatch) -> None:
    client = TestClient(app)

    def fake_prerequisites(order_id: str, *, expected_revision: int) -> tuple[dict, str]:
        assert expected_revision == 7
        return {
            "order_id": order_id,
            "state": "facility_template_unresolved",
            "blockers": ["facility_template_unresolved"],
        }, "facility_template_unresolved"

    def fail_enqueue(*_args, **_kwargs):
        raise AssertionError("OCR enqueue must not run when facility template is unresolved")

    monkeypatch.setattr(orders_api.order_workflow_v2_service, "ensure_ocr_prerequisites", fake_prerequisites)
    monkeypatch.setattr(orders_api, "_enqueue_workflow_v2_evidence_rerun", fail_enqueue)

    res = client.post("/orders/ORDcontract/workflow-v2/ocr-runs", headers={"If-Match": "7"}, json={"mode": "hakodate"})
    assert res.status_code == 400
    assert res.json()["detail"] == "facility_template_unresolved"


def test_workflow_v2_ocr_run_forwards_selected_document_id(monkeypatch) -> None:
    client = TestClient(app)
    captured: dict[str, object] = {}

    def fake_prerequisites(order_id, *, expected_revision):
        assert expected_revision == 7
        return {"order_id": order_id, "state": "context_confirmed", "revision": 8}, None

    monkeypatch.setattr(
        orders_api.order_workflow_v2_service,
        "ensure_ocr_prerequisites",
        fake_prerequisites,
    )

    def fake_enqueue(order_id: str, **kwargs):
        assert kwargs["expected_revision"] == 8
        captured["order_id"] = order_id
        captured["selected_document_id"] = kwargs.get("selected_document_id")
        return {"accepted": True, "ocr_job_id": f"OCR-{order_id}", "workflow": {"order_id": order_id}}

    monkeypatch.setattr(orders_api, "_enqueue_workflow_v2_evidence_rerun", fake_enqueue)

    res = client.post(
        "/orders/ORDcontract/workflow-v2/ocr-runs",
        headers={"If-Match": "7"},
        json={"mode": "hakodate", "document_id": "DOC-history-001"},
    )

    assert res.status_code == 202
    assert captured == {"order_id": "ORDcontract", "selected_document_id": "DOC-history-001"}


def test_download_document_uses_selected_document_version(monkeypatch) -> None:
    client = TestClient(app)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda _order_id, *, allow_unresolved_menu_amount=False: {
            "id": "ORDdocversion",
            "document": "gs://bucket/current.pdf",
            "versions": [
                {
                    "document_id": "DOC-v1",
                    "version_no": 1,
                    "storage_uri": "gs://bucket/old.pdf",
                    "is_current": False,
                },
                {
                    "document_id": "DOC-v2",
                    "version_no": 2,
                    "storage_uri": "gs://bucket/current.pdf",
                    "is_current": True,
                },
            ],
            "current_version": {
                "document_id": "DOC-v2",
                "version_no": 2,
                "storage_uri": "gs://bucket/current.pdf",
                "is_current": True,
            },
        },
    )

    def fake_load(uri: str):
        captured["uri"] = uri
        return b"%PDF-selected"

    from hashlib import sha256
    from src.services import workflow_attempt_guard_service
    receipt = {"document_id": "DOC-v1", "storage_uri": "gs://bucket/old.pdf",
               "sha256": sha256(b"%PDF-selected").hexdigest(), "generation": None}
    def capture_selected(session, order, selected):
        assert selected["document_id"] == "DOC-v1"
        assert selected["storage_uri"] == receipt["storage_uri"]
        return receipt
    monkeypatch.setattr(workflow_attempt_guard_service, "capture_selected_pdf_receipt", capture_selected)
    def load_selected(uri, captured_receipt):
        assert captured_receipt is receipt
        return fake_load(uri), "original", "direct"
    monkeypatch.setattr(orders_api, "_load_document_bytes", load_selected)

    res = client.get("/orders/ORDdocversion/document?document_id=DOC-v1")

    assert res.status_code == 200
    assert res.content == b"%PDF-selected"
    assert captured["uri"] == "gs://bucket/old.pdf"
    assert res.headers["x-sawa-document-id"] == "DOC-v1"
    assert res.headers["x-sawa-document-version"] == "1"


def test_workflow_v2_evidence_rerun_sets_selected_document_as_ocr_input(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        orders_api.order_workflow_v2_service,
        "get_workflow",
        lambda order_id: (
            {
                "order_id": order_id,
                "facility_id": "FAC00001",
                "week_start": "2026-01-05",
                "week_end": "2026-01-11",
                "template_version_id": "TPL-v1",
            },
            None,
        ),
    )
    monkeypatch.setattr(orders_api.order_workflow_v2_service, "workflow_has_confirmed_ocr_context", lambda _workflow: True)
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda _order_id, *, allow_unresolved_menu_amount=False: {
            "id": "ORDocrinput",
            "facility": "FAC00001",
            "document": "gs://bucket/current.pdf",
            "versions": [
                {
                    "document_id": "DOC-v1",
                    "version_no": 1,
                    "storage_uri": "gs://bucket/old.pdf",
                    "is_current": False,
                },
                {
                    "document_id": "DOC-v2",
                    "version_no": 2,
                    "storage_uri": "gs://bucket/current.pdf",
                    "is_current": True,
                },
            ],
            "current_version": {
                "document_id": "DOC-v2",
                "version_no": 2,
                "storage_uri": "gs://bucket/current.pdf",
                "is_current": True,
            },
            "lines_updated_at": None,
        },
    )
    monkeypatch.setattr(orders_api.config_service, "get_facility_config", lambda _facility: {"facility_id": "FAC00001"})
    delivery = object()  # Opaque dispatch-boundary token, not a receipt.
    def mark_queued(order_id, job_id, **kwargs):
        assert order_id == "ORDocrinput" and job_id == "OCR-ORDocrinput"
        assert kwargs["expected_revision"] == 7
        assert kwargs["request_mode"] == "ocr_rerun"
        assert kwargs["stale_action"] == "retry"
        assert kwargs["delivery_selected_document_id"] == "DOC-v1"
        captured.update(kwargs)
        return {"workflow": {"order_id": order_id, "ocr_job_id": job_id}, "delivery": delivery}, None
    monkeypatch.setattr(orders_api.order_workflow_v2_service, "mark_ocr_run_queued", mark_queued)
    dispatched = []
    monkeypatch.setattr(orders_api, "_dispatch_workflow_ocr_delivery", dispatched.append)

    result = orders_api._enqueue_workflow_v2_evidence_rerun(
        "ORDocrinput",
        selected_document_id="DOC-v1",
        expected_revision=7,
    )

    assert result["accepted"] is True
    assert captured["input_reference"] == "gs://bucket/old.pdf"
    assert captured["job_metrics"]["selected_document_id"] == "DOC-v1"
    assert captured["job_metrics"]["selected_document_version"] == 1
    assert dispatched == [delivery]


def test_workflow_v2_header_axis_review_endpoints(monkeypatch) -> None:
    client = TestClient(app)
    saved: dict[str, object] = {}

    def fake_get(order_id: str, *, document_id) -> tuple[dict, None]:
        assert document_id is None
        return {
            "order_id": order_id,
            "status": "ready",
            "x_positions": [10.0, 20.0],
            "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 80},
        }, None

    def fake_save(order_id: str, *, corrected_xs: list[float], coordinate_space: dict, source_receipt, expected_revision) -> tuple[dict, None]:
        assert source_receipt is None
        assert expected_revision == 7
        saved["order_id"] = order_id
        saved["corrected_xs"] = corrected_xs
        saved["coordinate_space"] = coordinate_space
        return {"order_id": order_id, "state": "context_confirmed"}, None

    monkeypatch.setattr(orders_api.order_workflow_v2_service, "get_header_axis_review", fake_get)
    monkeypatch.setattr(orders_api.order_workflow_v2_service, "save_header_axis_review_decision", fake_save)

    res = client.get("/orders/ORDcontract/workflow-v2/header-axis-review")
    assert res.status_code == 200
    assert res.json()["x_positions"] == [10.0, 20.0]

    saved_res = client.put(
        "/orders/ORDcontract/workflow-v2/header-axis-review",
        headers={"If-Match": "7"},
        json={
            "corrected_xs": [11.0, 22.0],
            "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 80},
        },
    )
    assert saved_res.status_code == 200
    assert saved["order_id"] == "ORDcontract"
    assert saved["corrected_xs"] == [11.0, 22.0]


@contextmanager
def _legacy_workflow_boundary(monkeypatch):
    """Real auth reads are allowed; legacy services and SQL writes are not."""
    def fail_legacy_helper(*_args, **_kwargs):
        raise AssertionError("legacy current workflow helper must not run")

    for name in (
        "ensure_hakodate_evidence_draft_current", "get_order_workflow_state",
        "list_order_critical_decisions", "get_ocr_pages", "get_ocr_sheet",
        "get_candidate_draft_preview", "choose_critical_decision", "switch_draft_to_latest_evidence",
        "apply_submitted_ocr_sheet", "confirm_order", "confirm_order_authoritatively",
        "get_current_sheet_context", "save_ocr_sheet_exact", "persist_sheet_draft",
        "review_ocr_table_with_llm", "apply_patch_candidate_to_draft", "_load_pipeline_output_with_retry",
    ):
        monkeypatch.setattr(orders_api.order_service, name, fail_legacy_helper)
    for name in ("_dispatch_workflow_ocr_delivery", "_run_ocr_rerun_background",
                 "_enqueue_order_reparse_job", "_enqueue_workflow_v2_evidence_rerun"):
        monkeypatch.setattr(orders_api, name, fail_legacy_helper)

    def snapshot():
        with db.session_scope() as session:
            return {table.name: deepcopy([dict(row) for row in session.execute(
                select(table).order_by(*table.primary_key.columns)).mappings()])
                for table in db.Base.metadata.sorted_tables}

    statements = []
    def observe(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    before = snapshot()
    event.listen(db.engine, "before_cursor_execute", observe)
    try:
        yield
    finally:
        event.remove(db.engine, "before_cursor_execute", observe)
    assert all(statement.lstrip().upper().startswith("SELECT ") for statement in statements)
    assert snapshot() == before


_LEGACY_DETAIL = {
    "error": "legacy_order_workflow_disabled",
    "message": "注文処理は workflow-v2 に移行しました。この旧 order workflow endpoint は使用できません。",
    "replacement": "workflow-v2",
}


@pytest.mark.parametrize("method,path,endpoint,kwargs", [
    pytest.param("GET", "/orders/ORDcontract/draft-sheet", "draft-sheet", {}, id="draft-get"),
    pytest.param("GET", "/orders/ORDcontract/draft-sheet", "draft-sheet", {"params": {"compact": 1}}, id="draft-compact"),
    pytest.param("GET", "/orders/ORDcontract/workflow-state", "workflow-state", {}, id="workflow-state"),
    pytest.param("GET", "/orders/ORDcontract/critical-decisions", "critical-decisions", {}, id="decisions-get"),
    pytest.param("POST", "/orders/ORDcontract/critical-decisions/facility", "critical-decisions", {"json": {}}, id="decision-facility"),
    pytest.param("POST", "/orders/ORDcontract/critical-decisions/week", "critical-decisions", {"json": {}}, id="decision-week"),
    pytest.param("POST", "/orders/ORDcontract/critical-decisions/template", "critical-decisions", {"json": {}}, id="decision-template"),
    pytest.param("GET", "/orders/ORDcontract/ocr-pages", "ocr-pages", {}, id="pages"),
    pytest.param("GET", "/orders/ORDcontract/ocr-sheet", "ocr-sheet", {}, id="sheet"),
    pytest.param("GET", "/orders/ORDcontract/draft-sheet/candidate-preview", "draft-sheet/candidate-preview", {}, id="candidate-preview"),
    pytest.param("POST", "/orders/ORDcontract/draft-sheet", "draft-sheet", {"json": {}}, id="draft-post"),
    pytest.param("POST", "/orders/ORDcontract/draft-sheet/switch-evidence", "draft-sheet/switch-evidence", {"json": {}}, id="switch-evidence"),
    pytest.param("POST", "/orders/ORDcontract/draft-sheet/keep-current", "draft-sheet/keep-current", {"json": {}}, id="keep-current"),
    pytest.param("POST", "/orders/ORDcontract/confirm", "confirm", {"json": {}}, id="confirm"),
    pytest.param("POST", "/orders/ORDcontract/ocr-rerun", "ocr-rerun", {"json": {}}, id="rerun"),
    pytest.param("POST", "/orders/ORDcontract/ocr-apply", "ocr-apply", {"json": {}}, id="apply-empty"),
    pytest.param("POST", "/orders/ORDcontract/ocr-apply", "ocr-apply", {"json": {
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["02/15", "朝", "Menu B", "6", "api"]], "ui_mode": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": ["row-api-no-fac-1"],
    }}, id="apply-authored-sheet"),
    pytest.param("POST", "/orders/ORDcontract/ocr-sheet-save", "ocr-sheet-save", {"json": {}}, id="sheet-save"),
    pytest.param("POST", "/orders/ORDcontract/facility", "facility", {"json": {}}, id="facility"),
    pytest.param("POST", "/orders/ORDcontract/week", "week", {"json": {"week": "2026-01@2026-02-01~2026-02-07"}}, id="week-invalid"),
    pytest.param("PUT", "/orders/ORDcontract/facility-template-columns", "facility-template-columns", {"json": {"columns": [
        {"index": 0, "role": "date", "header": "日付", "name": "date"},
        {"index": 1, "role": "quantity", "header": "常食2F", "name": "qty.regular_2f", "diet_type": "regular", "area_id": "2F"},
    ]}}, id="template-columns"),
    pytest.param("PUT", "/orders/ORDcontract/lines", "lines", {"json": {"lines": [{
        "line_id": "line-contract-lunch-updated", "date": "2099-11-15", "daypart": "昼", "menu_name": "昼メニュー",
        "diet_type": "regular", "area_id": "2F", "bag_type": "standard", "quantity_original": 20,
    }]}}, id="lines"),
    pytest.param("POST", "/orders/ORDcontract/reparse", "reparse", {"json": {"ocr_provider": "gemini"}}, id="reparse"),
    pytest.param("POST", "/orders/ORDcontract/reparse", "reparse", {"json": {"ocr_provider": "gemini", "stale_action": "wait"}}, id="reparse-wait"),
    pytest.param("POST", "/orders/ORDcontract/draft-sheet/force-weekly-menu", "draft-sheet/force-weekly-menu", {}, id="force-weekly"),
    pytest.param("POST", "/orders/ORDcontract/draft-sheet/force-weekly-menu", "draft-sheet/force-weekly-menu", {"json": {"blank_quantities": True}}, id="force-weekly-blank"),
    pytest.param("POST", "/orders/ORDcontract/draft-sheet/force-facility-schema", "draft-sheet/force-facility-schema", {"json": {"blank_quantities": True}}, id="force-schema"),
    pytest.param("POST", "/orders/ORDpatch/draft-sheet/apply-patch-candidate", "draft-sheet/apply-patch-candidate", {"json": {"candidate_id": "OPCtest"}}, id="patch-candidate"),
    pytest.param("POST", "/orders/ORDmissing/draft-sheet/apply-patch-candidate", "draft-sheet/apply-patch-candidate", {"json": {"candidate_id": "OPCmissing"}}, id="patch-missing"),
    pytest.param("POST", "/orders/ORDcontract/ocr-review", "ocr-review", {"json": {"ocr_provider": "gemini", "ocr_prompt": "verify baseline against pdf", "pdf_variant": "corrected"}}, id="review-prompt"),
    pytest.param("POST", "/orders/ORDmissing/ocr-review", "ocr-review", {"json": {}}, id="review-missing"),
    pytest.param("POST", "/orders/ORDdummy/ocr-review", "ocr-review", {"json": {}}, id="review-empty"),
    pytest.param("POST", "/orders/ORDdummy/ocr-review", "ocr-review", {"json": {"ocr_provider": "pipeline"}}, id="review-invalid-provider"),
    pytest.param("POST", "/orders/ORDdummy/ocr-review", "ocr-review", {"json": {"pdf_variant": "auto"}}, id="review-invalid-variant"),
])
def test_legacy_current_workflow_endpoints_are_hard_410(monkeypatch, hospital_operator, method, path, endpoint, kwargs) -> None:
    assert os.environ["AUTH_DISABLED"] == "false"
    with _legacy_workflow_boundary(monkeypatch):
        response = TestClient(app).request(method, path, headers=hospital_operator.headers, **kwargs)
        assert response.status_code == 410
        assert response.json() == {"detail": {**_LEGACY_DETAIL, "endpoint": endpoint}}
        assert hospital_operator.verifier.called


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_legacy_template_columns_requires_registered_operator_or_admin(monkeypatch, request, role):
    actor = request.getfixturevalue("hospital_" + role)
    assert os.environ["AUTH_DISABLED"] == "false"
    client = TestClient(app)
    columns = [
        {"index": 0, "role": "date", "header": "日付", "name": "date"},
        {"index": 1, "role": "quantity", "header": "常食2F", "name": "qty.regular_2f", "diet_type": "regular", "area_id": "2F"},
    ]
    with _legacy_workflow_boundary(monkeypatch):
        response = client.put("/orders/ORDcontract/facility-template-columns", json={"columns": columns}, headers=actor.headers)
        assert response.status_code == 410
        assert response.json() == {"detail": {**_LEGACY_DETAIL, "endpoint": "facility-template-columns"}}
        assert actor.verifier.called
        for headers in ({}, {"Authorization": "Basic b3BlcmF0b3I6b3BlcmF0b3Itc2VjcmV0"}, {"Authorization": "Bearer invalid-google-token"}):
            denied = client.put("/orders/ORDcontract/facility-template-columns", json={"columns": columns}, headers=headers)
            assert denied.status_code == 401
            assert denied.json() == {"detail": "Unauthorized"}


def test_workflow_v2_split_step4_endpoints_are_retired() -> None:
    client = TestClient(app)

    for path in (
        "/orders/ORDcontract/workflow-v2/bagging/confirm",
        "/orders/ORDcontract/workflow-v2/outputs/review",
    ):
        response = client.post(path, json={})
        assert response.status_code == 410
        assert response.json()["detail"] == "workflow_v2_step4_unified_use_bagging"
