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
