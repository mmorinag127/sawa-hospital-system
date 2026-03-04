import sys
import pathlib
import json
from datetime import datetime

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.main import app  # noqa: E402
from src.services import order_service  # noqa: E402
from src.services.ocr_job_service import create_job, get_job, update_job  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _create_seed_order(message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02",
    )
    lines = [
        {
            "date": "2026-02-15",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 5,
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def test_get_order_does_not_override_running_with_cached_payload(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-001")
    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="running")

    def _fake_cached_payload(_order_id: str):
        return {
            "status": "success",
            "rows": [["dummy"]],
            "metrics": {"provider": "old"},
        }

    monkeypatch.setattr(order_service, "get_cached_ocr_payload", _fake_cached_payload)

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("ocr_status") == "running"


def test_get_order_preserves_terminal_reparse_failure_status(tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-001b")
    output_path = tmp_path / "ocr_output_done.json"
    output_path.write_text(json.dumps({"status": "done", "table_raw": "|a|b|"}), encoding="utf-8")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="failed")
    update_job(
        job_id,
        status="failed",
        output_reference=f"file://{output_path}",
        error_message="sheet_date_anchor_drift",
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("ocr_status") == "failed"
    assert payload.get("ocr_error") == "sheet_date_anchor_drift"


def test_get_order_does_not_mark_reparse_job_done_while_running(tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-001c")
    output_path = tmp_path / "ocr_output_done_running.json"
    output_path.write_text(json.dumps({"status": "done", "table_raw": "|a|b|"}), encoding="utf-8")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="running")
    update_job(
        job_id,
        status="running",
        output_reference=f"file://{output_path}",
        error_message=None,
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("ocr_status") == "running"
    job = get_job(job_id)
    assert job is not None
    assert job.get("status") == "running"


def test_list_orders_include_ocr_keeps_running_status():
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-002")
    create_job(f"OCR-{order['id']}", input_reference=order["document"], status="running")

    client = TestClient(app)
    res = client.get("/orders?include_ocr=true")
    assert res.status_code == 200
    rows = res.json().get("orders") or []
    row = next(item for item in rows if item.get("id") == order["id"])
    assert row.get("ocr_status") == "running"


def test_reparse_endpoint_marks_job_running_before_background(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003")
    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="done")
    update_job(job_id, status="done", metrics={"changed": False}, error_message=None)

    monkeypatch.setattr(orders_api, "_run_reparse_background", lambda *_args, **_kwargs: None)

    client = TestClient(app)
    res = client.post(f"/orders/{order['id']}/reparse", json={"ocr_provider": "gemini", "llm_assist": True})
    assert res.status_code == 202
    job = get_job(job_id)
    assert job is not None
    assert job.get("status") == "running"
    assert job.get("error_message") in {None, ""}


def test_run_reparse_background_marks_job_failed_on_crash(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003b")
    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="running")

    def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(order_service, "reparse_order", _raise)

    orders_api._run_reparse_background(order["id"], None, "gemini", True)

    job = get_job(job_id)
    assert job is not None
    assert job.get("status") == "failed"
    assert "reparse_crashed:boom" in str(job.get("error_message") or "")


def test_get_ocr_output_includes_reparse_debug_from_cache(tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-004")
    output_path = tmp_path / "ocr_output.json"
    output_path.write_text(json.dumps({"status": "done", "table_raw": "|a|b|"}), encoding="utf-8")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="done")
    update_job(
        job_id,
        status="done",
        output_reference=f"file://{output_path}",
        error_message=None,
    )
    order_service._save_order_ocr_cache(  # noqa: SLF001
        order["id"],
        {
            "_reparse_debug": {
                "provider": "gemini",
                "requested_provider": "gemini",
                "row_count": 55,
            }
        },
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/ocr-output")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("status") == "done"
    assert payload.get("_reparse_debug", {}).get("provider") == "gemini"
    assert payload.get("_reparse_debug", {}).get("row_count") == 55


def test_get_ocr_output_refreshes_stale_reparse_debug_with_latest_job_metrics(tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-005")
    output_path = tmp_path / "ocr_output_latest.json"
    output_path.write_text(json.dumps({"status": "done", "table_raw": "|a|b|"}), encoding="utf-8")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="done")
    update_job(
        job_id,
        status="done",
        output_reference=f"file://{output_path}",
        error_message=None,
        metrics={
            "provider": "gemini",
            "requested_provider": "gemini",
            "row_count": 54,
            "line_count": 216,
            "before_count": 78,
            "after_count": 216,
            "changed": True,
            "llm_assist": True,
        },
    )
    order_service._save_order_ocr_cache(  # noqa: SLF001
        order["id"],
        {
            "_reparse_debug": {
                "provider": "gemini",
                "requested_provider": "gemini",
                "row_count": 34,
                "line_count": 78,
                "before_count": 78,
                "after_count": 78,
                "changed": False,
            }
        },
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/ocr-output")
    assert res.status_code == 200
    payload = res.json()
    debug = payload.get("_reparse_debug") or {}
    assert debug.get("provider") == "gemini"
    assert debug.get("requested_provider") == "gemini"
    assert debug.get("row_count") == 54
    assert debug.get("line_count") == 216
    assert debug.get("after_count") == 216
    assert debug.get("changed") is True


def test_get_ocr_output_clears_stale_reparse_error_on_success_metrics(tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-006")
    output_path = tmp_path / "ocr_output_success.json"
    output_path.write_text(json.dumps({"status": "done", "table_raw": "|a|b|"}), encoding="utf-8")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="done")
    update_job(
        job_id,
        status="done",
        output_reference=f"file://{output_path}",
        error_message=None,
        metrics={
            "provider": "gemini",
            "requested_provider": "gemini",
            "row_count": 43,
            "line_count": 29,
            "before_count": 7,
            "after_count": 29,
            "changed": True,
            "llm_assist": True,
        },
    )
    order_service._save_order_ocr_cache(  # noqa: SLF001
        order["id"],
        {
            "_reparse_debug": {
                "provider": "gemini",
                "error": "sheet_row_coverage_low",
                "reject_reasons": ["sheet_row_coverage_low"],
                "validation_detail": {"expected_row_count": 56, "actual_row_count": 43},
            }
        },
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/ocr-output")
    assert res.status_code == 200
    payload = res.json()
    debug = payload.get("_reparse_debug") or {}
    assert debug.get("provider") == "gemini"
    assert debug.get("error") in {None, ""}
    assert debug.get("reject_reasons") == []
    assert debug.get("validation_detail") == {}
