import pathlib
import sys
from datetime import datetime

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.services import order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _create_seed_order(message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 20, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    lines = [
        {
            "date": "2026-02-20",
            "daypart": "昼",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def test_system_status_and_admin_endpoints():
    order_service.clear_all()
    _create_seed_order("msg-system-api-001")
    client = TestClient(app)

    status_res = client.get("/system/status")
    assert status_res.status_code == 200
    status_payload = status_res.json()
    assert isinstance(status_payload.get("db_quota"), dict)
    quality = status_payload.get("ocr_reparse_quality") or {}
    assert isinstance(quality.get("gate"), dict)
    assert quality.get("gate", {}).get("status") in {"pass", "fail", "insufficient_data", "error"}

    quota_res = client.get("/system/db/quota")
    assert quota_res.status_code == 200
    assert quota_res.json().get("resource")

    download_res = client.get("/system/db/download")
    assert download_res.status_code == 200
    assert len(download_res.content) > 0

    bad_clear_res = client.post("/system/clear-all", json={"confirm": "INVALID"})
    assert bad_clear_res.status_code == 400

    clear_res = client.post(
        "/system/clear-all",
        json={"confirm": "CLEAR_ALL", "include_audit_logs": True},
    )
    assert clear_res.status_code == 200
    clear_payload = clear_res.json()
    assert clear_payload.get("result", {}).get("total_removed", 0) >= 1
