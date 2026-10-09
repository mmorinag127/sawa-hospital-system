"""Actual registered retirement routes; synthetic auth, no lifespan/OCR/staging proof."""

from fastapi.testclient import TestClient
import pytest


@pytest.mark.parametrize("endpoint", ["ocr-pages", "ocr-sheet"])
def test_registered_legacy_ocr_routes_remain_gone(monkeypatch, endpoint):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    from src.main import app

    client = TestClient(app)
    try:
        response = client.get(f"/orders/SYNTHETIC-RETIRED-ROUTE/{endpoint}")
        assert response.status_code == 410, response.text
        assert response.json() == {"detail": {
            "error": "legacy_order_workflow_disabled",
            "endpoint": endpoint,
            "message": "注文処理は workflow-v2 に移行しました。この旧 order workflow endpoint は使用できません。",
            "replacement": "workflow-v2",
        }}
    finally:
        client.close()
