from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.main import app  # noqa: E402


def test_workflow_v2_facility_template_columns_is_the_only_write_endpoint(monkeypatch) -> None:
    client = TestClient(app)
    captured: dict[str, object] = {}

    def fake_save(order_id: str, columns: list[dict]) -> tuple[dict, None]:
        captured["order_id"] = order_id
        captured["columns"] = columns
        return {
            "workflow": {
                "order_id": order_id,
                "state": "context_confirmed",
                "template_version_id": "FTVcontract",
            },
            "template_version": {"id": "FTVcontract"},
        }, None

    monkeypatch.setattr(orders_api.order_workflow_v2_service, "save_facility_template_columns", fake_save)

    ok = client.put(
        "/orders/ORDcontract/workflow-v2/facility-template-columns",
        json={"columns": [{"role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"}]},
    )
    assert ok.status_code == 200
    assert ok.json()["workflow"]["template_version_id"] == "FTVcontract"
    assert captured["order_id"] == "ORDcontract"

    legacy = client.put(
        "/orders/ORDcontract/facility-template-columns",
        json={"columns": [{"role": "quantity", "header": "常食"}]},
    )
    assert legacy.status_code == 410
    assert legacy.json()["detail"]["error"] == "legacy_order_workflow_disabled"
    assert legacy.json()["detail"]["replacement"] == "workflow-v2"


def test_workflow_v2_facility_template_columns_validation_error_is_actionable(monkeypatch) -> None:
    client = TestClient(app)

    def fake_save(order_id: str, columns: list[dict]) -> tuple[dict, str]:
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
        json={"columns": [{"role": "quantity", "header": "常食"}]},
    )

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["error"] == "validation_error"
    assert detail["validation"]["errors"] == ["template_source_index_missing"]


def test_workflow_v2_ocr_run_blocks_unresolved_template_before_job_enqueue(monkeypatch) -> None:
    client = TestClient(app)

    def fake_prerequisites(order_id: str) -> tuple[dict, str]:
        return {
            "order_id": order_id,
            "state": "facility_template_unresolved",
            "blockers": ["facility_template_unresolved"],
        }, "facility_template_unresolved"

    def fail_enqueue(*_args, **_kwargs):
        raise AssertionError("OCR enqueue must not run when facility template is unresolved")

    monkeypatch.setattr(orders_api.order_workflow_v2_service, "ensure_ocr_prerequisites", fake_prerequisites)
    monkeypatch.setattr(orders_api, "_enqueue_workflow_v2_evidence_rerun", fail_enqueue)

    res = client.post("/orders/ORDcontract/workflow-v2/ocr-runs", json={"mode": "hakodate"})
    assert res.status_code == 400
    assert res.json()["detail"] == "facility_template_unresolved"


def test_workflow_v2_ocr_run_forwards_selected_document_id(monkeypatch) -> None:
    client = TestClient(app)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        orders_api.order_workflow_v2_service,
        "ensure_ocr_prerequisites",
        lambda order_id: ({"order_id": order_id, "state": "context_confirmed"}, None),
    )

    def fake_enqueue(order_id: str, background_tasks, **kwargs):
        captured["order_id"] = order_id
        captured["selected_document_id"] = kwargs.get("selected_document_id")
        return {"accepted": True, "ocr_job_id": f"OCR-{order_id}", "workflow": {"order_id": order_id}}

    monkeypatch.setattr(orders_api, "_enqueue_workflow_v2_evidence_rerun", fake_enqueue)

    res = client.post(
        "/orders/ORDcontract/workflow-v2/ocr-runs",
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
        lambda _order_id: {
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

    monkeypatch.setattr(orders_api, "_load_document_bytes", lambda uri: (fake_load(uri), "original", "direct"))

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
        lambda _order_id: {
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
    monkeypatch.setattr(orders_api, "get_ocr_job", lambda _job_id: None)
    monkeypatch.setattr(orders_api, "describe_ocr_job_state", lambda _job: {"status": "idle"})
    monkeypatch.setattr(orders_api, "create_ocr_job", lambda *args, **kwargs: ({}, True))
    monkeypatch.setattr(
        orders_api,
        "update_ocr_job",
        lambda job_id, **kwargs: captured.update({"job_id": job_id, **kwargs}),
    )
    monkeypatch.setattr(
        orders_api.order_workflow_v2_service,
        "mark_ocr_run_queued",
        lambda order_id, job_id: ({"order_id": order_id, "ocr_job_id": job_id}, None),
    )

    class Background:
        def add_task(self, func, *args):
            captured["background_args"] = args

    result = orders_api._enqueue_workflow_v2_evidence_rerun(
        "ORDocrinput",
        Background(),
        selected_document_id="DOC-v1",
    )

    assert result["accepted"] is True
    assert captured["input_reference"] == "gs://bucket/old.pdf"
    assert captured["metrics"]["selected_document_id"] == "DOC-v1"
    assert captured["metrics"]["selected_document_version"] == 1
    assert captured["background_args"] == ("ORDocrinput", "OCR-ORDocrinput", "DOC-v1")


def test_workflow_v2_header_axis_review_endpoints(monkeypatch) -> None:
    client = TestClient(app)
    saved: dict[str, object] = {}

    def fake_get(order_id: str) -> tuple[dict, None]:
        return {
            "order_id": order_id,
            "status": "ready",
            "x_positions": [10.0, 20.0],
            "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 80},
        }, None

    def fake_save(order_id: str, *, corrected_xs: list[float], coordinate_space: dict) -> tuple[dict, None]:
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
        json={
            "corrected_xs": [11.0, 22.0],
            "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 80},
        },
    )
    assert saved_res.status_code == 200
    assert saved["order_id"] == "ORDcontract"
    assert saved["corrected_xs"] == [11.0, 22.0]


def test_legacy_current_workflow_endpoints_are_hard_410(monkeypatch) -> None:
    client = TestClient(app)

    def fail_legacy_helper(*_args, **_kwargs):
        raise AssertionError("legacy current workflow helper must not run")

    monkeypatch.setattr(orders_api.order_service, "ensure_hakodate_evidence_draft_current", fail_legacy_helper)
    monkeypatch.setattr(orders_api.order_service, "get_order_workflow_state", fail_legacy_helper)
    monkeypatch.setattr(orders_api.order_service, "list_order_critical_decisions", fail_legacy_helper)
    monkeypatch.setattr(orders_api.order_service, "get_ocr_pages", fail_legacy_helper)
    monkeypatch.setattr(orders_api.order_service, "get_ocr_sheet", fail_legacy_helper)
    monkeypatch.setattr(orders_api.order_service, "get_candidate_draft_preview", fail_legacy_helper)

    endpoints = [
        ("get", "/orders/ORDcontract/draft-sheet"),
        ("get", "/orders/ORDcontract/workflow-state"),
        ("get", "/orders/ORDcontract/critical-decisions"),
        ("get", "/orders/ORDcontract/ocr-pages"),
        ("get", "/orders/ORDcontract/ocr-sheet"),
        ("get", "/orders/ORDcontract/draft-sheet/candidate-preview"),
        ("post", "/orders/ORDcontract/draft-sheet"),
        ("post", "/orders/ORDcontract/draft-sheet/switch-evidence"),
        ("post", "/orders/ORDcontract/draft-sheet/keep-current"),
        ("post", "/orders/ORDcontract/confirm"),
    ]

    for method, path in endpoints:
        response = client.post(path, json={}) if method == "post" else client.get(path)
        assert response.status_code == 410
        detail = response.json()["detail"]
        assert detail["error"] == "legacy_order_workflow_disabled"
        assert detail["replacement"] == "workflow-v2"


def test_workflow_v2_split_step4_endpoints_are_retired() -> None:
    client = TestClient(app)

    for path in (
        "/orders/ORDcontract/workflow-v2/bagging/confirm",
        "/orders/ORDcontract/workflow-v2/outputs/review",
    ):
        response = client.post(path, json={})
        assert response.status_code == 410
        assert response.json()["detail"] == "workflow_v2_step4_unified_use_bagging"
