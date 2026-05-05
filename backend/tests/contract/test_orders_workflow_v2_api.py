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
