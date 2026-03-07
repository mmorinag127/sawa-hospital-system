import sys
import pathlib
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.api import ingest as ingest_api  # noqa: E402
from src.api import worker as worker_api  # noqa: E402
from src.services import order_service  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.document import OrderDocument  # noqa: E402
from sqlalchemy import select  # noqa: E402


def test_ingest_contract_creates_order(tmp_path):
    order_service.clear_all()
    client = TestClient(app)
    pdf_path = tmp_path / "file1.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    payload = {
        "message_id": "msg-1",
        "pdf_uri": str(pdf_path),
        "received_at": "2025-12-23T10:00:00",
        "facility_hint": "FAC001",
        "week_hint": "WEK2025W52",
    }
    res = client.post("/ingest/email", json=payload)
    assert res.status_code == 202
    orders = order_service.list_orders()
    assert len(orders) == 1
    assert orders[0]["status"] == "要確認"
    assert orders[0]["facility"] == "FAC001"
    with session_scope() as session:
        document = session.execute(select(OrderDocument)).scalars().first()
        assert document is not None
        assert document.storage_uri == str(pdf_path)
        assert document.ocr_attempts >= 1


def test_gmail_scan_handles_invalid_grant(monkeypatch):
    client = TestClient(app)
    captured: dict[str, str] = {}

    def _raise_refresh(_: dict):
        raise RefreshError("Token has been expired or revoked.")

    def _capture_watch_error(code: str, message: str | None = None):
        captured["code"] = code
        captured["message"] = message or ""

    monkeypatch.setattr(ingest_api, "ingest_from_notification", _raise_refresh)
    monkeypatch.setattr(ingest_api, "save_watch_error", _capture_watch_error)

    res = client.post("/ingest/gmail-scan?sync=true&max_jobs=1&skip_ocr=true")
    assert res.status_code == 202
    body = res.json()
    assert body["accepted"] == 0
    assert body["error"] == "invalid_grant"
    assert captured["code"] == "invalid_grant"


def test_watch_refresh_returns_503_on_invalid_grant(monkeypatch):
    client = TestClient(app)

    def _raise_refresh():
        raise RefreshError("Token has been expired or revoked.")

    monkeypatch.setattr(worker_api, "refresh_gmail_watch", _raise_refresh)

    res = client.post("/watch-refresh")
    assert res.status_code == 503
    assert res.json().get("detail") == "invalid_grant"
