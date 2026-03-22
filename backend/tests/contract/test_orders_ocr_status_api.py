import sys
import pathlib
import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.main import app  # noqa: E402
from src.models.order import Order  # noqa: E402
from src.models.output import Bag  # noqa: E402
from src.services import menu_service, order_service  # noqa: E402
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


def test_get_order_prefers_cached_success_over_non_reparse_running_status(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-001")
    job_id = f"OCR-{order['message_id']}"
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
    assert payload.get("ocr_status") == "success"
    assert payload.get("ocr_error") in {None, ""}


def test_list_orders_include_ocr_prefers_cached_success_over_non_reparse_running_status(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-001-list")
    job_id = f"OCR-{order['message_id']}"
    create_job(job_id, input_reference=order["document"], status="running")

    def _fake_cached_payload(_order_id: str):
        return {
            "status": "success",
            "rows": [["dummy"]],
            "metrics": {"provider": "old"},
        }

    monkeypatch.setattr(order_service, "get_cached_ocr_payload", _fake_cached_payload)

    client = TestClient(app)
    res = client.get("/orders?include_ocr=true")
    assert res.status_code == 200
    rows = res.json().get("orders") or []
    row = next(item for item in rows if item.get("id") == order["id"])
    assert row.get("ocr_status") == "success"


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


def test_list_orders_is_stably_sorted_by_received_at_and_id():
    order_service.clear_all()
    received_at = datetime(2026, 2, 15, 9, 0, 0)
    first_id = "ORDsortB"
    second_id = "ORDsortA"
    with session_scope() as session:
        session.add(
            Order(
                id=first_id,
                facility_code="FAC00001",
                week_code="2026-02",
                status="要確認",
                document_uri="file://dummy-sort-001.pdf",
                message_id="msg-status-api-sort-001",
                received_at=received_at,
            )
        )
        session.add(
            Order(
                id=second_id,
                facility_code="FAC00001",
                week_code="2026-02",
                status="要確認",
                document_uri="file://dummy-sort-002.pdf",
                message_id="msg-status-api-sort-002",
                received_at=received_at,
            )
        )

    expected = [first_id, second_id]
    expected.sort(reverse=True)

    client = TestClient(app)
    res = client.get("/orders")
    assert res.status_code == 200
    rows = res.json().get("orders") or []
    ids = [row.get("id") for row in rows[:2]]
    assert ids == expected


def test_confirm_endpoint_blocks_when_weekly_menu_missing(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-confirm-block")
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda _order_id, refresh=False: {
            "state": "draft_ready",
            "apply_gate": {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []},
        },
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "get_ocr_sheet",
        lambda _order_id: {"warnings": ["sheet_weekly_menu_missing"]},
    )

    client = TestClient(app)
    res = client.post(f"/orders/{order['id']}/confirm")

    assert res.status_code == 409
    detail = res.json().get("detail") or {}
    assert detail.get("error") == "weekly_menu_missing"


def test_get_bags_rebuilds_stale_materialized_payload():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-status-api-bags-001",
        pdf_uri="file://dummy-bags.pdf",
        received_at=datetime(2026, 4, 18, 9, 0, 0),
        facility_hint="FAC00003",
        week_hint="2026-04",
    )
    lines = [
        {
            "date": "2026-04-18",
            "daypart": "昼",
            "menu_name": "筑前煮",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 9,
        },
        {
            "date": "2026-04-18",
            "daypart": "昼",
            "menu_name": "筑前煮",
            "diet_type": "regular",
            "area_id": "3F",
            "bag_type": "standard",
            "quantity_original": 8,
        },
    ]
    order = order_service.create_order_from_ingest(payload, lines=lines)
    with session_scope() as session:
        session.add(
                Bag(
                    id="BAGstale001",
                    order_id=order["id"],
                    date=datetime(2026, 4, 18).date(),
                    daypart="昼",
                    menu_name="筑前煮",
                    diet_type="regular",
                    area_id=None,
                bag_type="large",
                quantity=99,
            )
        )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/bags")

    assert res.status_code == 200
    rows = res.json().get("bags") or []
    chikuzen = [row for row in rows if row.get("menu_name") == "筑前煮"]
    assert {(row.get("diet_type"), row.get("area_id"), row.get("quantity")) for row in chikuzen} == {
        ("regular", "2F", 9.0),
        ("regular", "3F", 8.0),
    }


def test_get_daily_bags_groups_rows_by_menu_diet_and_bag_type():
    order_service.clear_all()
    month_id = "2026-02"
    menu_csv = "menu\n筑前煮\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "筑前煮")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 50, "unit_type": "g", "daypart": "昼", "category": "主A"},
    )
    payload = IngestEmailPayload(
        message_id="msg-status-api-daily-bags-001",
        pdf_uri="file://dummy-daily-bags.pdf",
        received_at=datetime(2026, 2, 18, 9, 0, 0),
        facility_hint="FAC00003",
        week_hint="2026-02",
    )
    lines = [
        {
            "date": "2026-02-18",
            "daypart": "昼",
            "menu_name": "筑前煮",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 9,
        },
        {
            "date": "2026-02-18",
            "daypart": "昼",
            "menu_name": "筑前煮",
            "diet_type": "regular",
            "area_id": "3F",
            "bag_type": "standard",
            "quantity_original": 8,
        },
        {
            "date": "2026-02-18",
            "daypart": "昼",
            "menu_name": "筑前煮",
            "diet_type": "mixer",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 2,
        },
        {
            "date": "2026-02-18",
            "daypart": "昼",
            "menu_name": "筑前煮",
            "diet_type": "mixer",
            "area_id": "3F",
            "bag_type": "standard",
            "quantity_original": 3,
        },
    ]
    order_service.create_order_from_ingest(payload, lines=lines)

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-02-18")

    assert res.status_code == 200
    payload = res.json()
    assert payload["date"] == "2026-02-18"
    group = next(item for item in payload["groups"] if item.get("menu_name") == "筑前煮")
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["total_quantity"] == 17.0
    assert regular["total_amount_label"] == "850g"
    medium = next(item for item in regular["bag_type_groups"] if item.get("bag_type") == "medium")
    assert medium["bag_count"] == 2
    assert medium["total_amount_label"] == "850g"
    assert {item["amount_label"] for item in medium["breakdowns"]} == {"400g", "450g"}


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


def test_reparse_endpoint_defaults_to_llm_assist_for_user_requested_reparse(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003-default-llm")
    captured: dict[str, object] = {}

    def _fake_run(order_id, ocr_prompt, prompt_preset=None, ocr_provider=None, ocr_model=None, llm_assist=False):
        captured["order_id"] = order_id
        captured["ocr_prompt"] = ocr_prompt
        captured["prompt_preset"] = prompt_preset
        captured["ocr_provider"] = ocr_provider
        captured["ocr_model"] = ocr_model
        captured["llm_assist"] = llm_assist

    monkeypatch.setattr(orders_api, "_run_reparse_background", _fake_run)

    client = TestClient(app)
    res = client.post(f"/orders/{order['id']}/reparse")

    assert res.status_code == 202
    assert captured["order_id"] == order["id"]
    assert captured["ocr_provider"] is None
    assert captured["ocr_model"] is None
    assert captured["llm_assist"] is True


def test_reparse_endpoint_passes_explicit_ocr_model(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003-model")
    captured: dict[str, object] = {}

    def _fake_run(order_id, ocr_prompt, prompt_preset=None, ocr_provider=None, ocr_model=None, llm_assist=False):
        captured["order_id"] = order_id
        captured["ocr_prompt"] = ocr_prompt
        captured["prompt_preset"] = prompt_preset
        captured["ocr_provider"] = ocr_provider
        captured["ocr_model"] = ocr_model
        captured["llm_assist"] = llm_assist

    monkeypatch.setattr(orders_api, "_run_reparse_background", _fake_run)

    client = TestClient(app)
    res = client.post(
        f"/orders/{order['id']}/reparse",
        json={"ocr_provider": "gemini", "ocr_model": "gemini-2.5-pro", "llm_assist": True},
    )

    assert res.status_code == 202
    assert captured["order_id"] == order["id"]
    assert captured["ocr_provider"] == "gemini"
    assert captured["ocr_model"] == "gemini-2.5-pro"
    assert captured["llm_assist"] is True


def test_reparse_endpoint_accepts_llm_model_alias(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003-llm-model")
    captured: dict[str, object] = {}

    def _fake_run(order_id, ocr_prompt, prompt_preset=None, ocr_provider=None, ocr_model=None, llm_assist=False):
        captured["order_id"] = order_id
        captured["prompt_preset"] = prompt_preset
        captured["ocr_provider"] = ocr_provider
        captured["ocr_model"] = ocr_model
        captured["llm_assist"] = llm_assist

    monkeypatch.setattr(orders_api, "_run_reparse_background", _fake_run)

    client = TestClient(app)
    res = client.post(
        f"/orders/{order['id']}/reparse",
        json={"ocr_provider": "gemini", "llm_model": "gemini-2.5-flash", "llm_assist": True},
    )

    assert res.status_code == 202
    assert captured["order_id"] == order["id"]
    assert captured["ocr_provider"] == "gemini"
    assert captured["ocr_model"] == "gemini-2.5-flash"
    assert captured["llm_assist"] is True


def test_reparse_endpoint_accepts_prompt_preset(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003-preset")
    captured: dict[str, object] = {}

    def _fake_run(order_id, ocr_prompt, prompt_preset=None, ocr_provider=None, ocr_model=None, llm_assist=False):
        captured["order_id"] = order_id
        captured["ocr_prompt"] = ocr_prompt
        captured["prompt_preset"] = prompt_preset
        captured["ocr_provider"] = ocr_provider
        captured["ocr_model"] = ocr_model
        captured["llm_assist"] = llm_assist

    monkeypatch.setattr(orders_api, "_run_reparse_background", _fake_run)

    client = TestClient(app)
    res = client.post(
        f"/orders/{order['id']}/reparse",
        json={"ocr_provider": "gemini", "llm_assist": True, "prompt_preset": "row_alignment"},
    )

    assert res.status_code == 202
    assert captured["order_id"] == order["id"]
    assert captured["prompt_preset"] == "row_alignment"
    assert captured["llm_assist"] is True


def test_ocr_recover_endpoint_requests_pipeline_first_pass(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003-recover")
    captured: dict[str, object] = {}

    def _fake_run(order_id, ocr_prompt, prompt_preset=None, ocr_provider=None, ocr_model=None, llm_assist=False):
        captured["order_id"] = order_id
        captured["ocr_prompt"] = ocr_prompt
        captured["prompt_preset"] = prompt_preset
        captured["ocr_provider"] = ocr_provider
        captured["ocr_model"] = ocr_model
        captured["llm_assist"] = llm_assist

    monkeypatch.setattr(orders_api, "_run_reparse_background", _fake_run)

    client = TestClient(app)
    res = client.post(f"/orders/{order['id']}/ocr-recover")

    assert res.status_code == 202
    assert res.json()["accepted"] is True
    assert res.json()["mode"] == "pipeline_recovery"
    assert captured["order_id"] == order["id"]
    assert captured["ocr_prompt"] is None
    assert captured["ocr_provider"] == "pipeline"
    assert captured["ocr_model"] is None
    assert captured["llm_assist"] is False


def test_ocr_recover_endpoint_retries_stale_job_with_pipeline_first_pass(monkeypatch):
    order_service.clear_all()
    monkeypatch.setenv("OCR_JOB_STALE_MINUTES", "1")
    order = _create_seed_order("msg-status-api-003-recover-stale")
    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="running")
    update_job(
        job_id,
        status="running",
        metrics={
            "processing_stage": "llm_reparse",
            "result_state": "processing",
            "stage_updated_at": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        },
    )
    captured: dict[str, object] = {}

    def _fake_run(order_id, ocr_prompt, prompt_preset=None, ocr_provider=None, ocr_model=None, llm_assist=False):
        captured["order_id"] = order_id
        captured["ocr_prompt"] = ocr_prompt
        captured["prompt_preset"] = prompt_preset
        captured["ocr_provider"] = ocr_provider
        captured["ocr_model"] = ocr_model
        captured["llm_assist"] = llm_assist

    monkeypatch.setattr(orders_api, "_run_reparse_background", _fake_run)

    client = TestClient(app)
    res = client.post(f"/orders/{order['id']}/ocr-recover")

    assert res.status_code == 202
    assert res.json()["accepted"] is True
    assert res.json()["mode"] == "pipeline_recovery"
    job = get_job(job_id)
    assert job is not None
    assert job.get("status") == "running"
    assert job.get("metrics", {}).get("processing_stage") == "queued"
    assert captured["order_id"] == order["id"]
    assert captured["ocr_provider"] == "pipeline"
    assert captured["ocr_model"] is None
    assert captured["llm_assist"] is False


def test_run_reparse_background_marks_job_failed_on_crash(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003b")
    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="running")

    def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(order_service, "reparse_order", _raise)

    orders_api._run_reparse_background(order["id"], None, None, "gemini", None, True)

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


def test_get_ocr_output_keeps_reparse_warning_fields_on_success_metrics(tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-007")
    output_path = tmp_path / "ocr_output_warning.json"
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
            "row_count": 62,
            "line_count": 248,
            "before_count": 78,
            "after_count": 248,
            "changed": True,
            "llm_assist": True,
            "warning_reasons": ["sheet_column_anomaly"],
            "warning_detail": {
                "quality_issue": "column_anomaly",
                "column_anomaly_count": 1,
            },
        },
    )
    order_service._save_order_ocr_cache(  # noqa: SLF001
        order["id"],
        {
            "_reparse_debug": {
                "provider": "gemini",
                "warning_reasons": [],
                "warning_detail": {},
            }
        },
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/ocr-output")
    assert res.status_code == 200
    payload = res.json()
    debug = payload.get("_reparse_debug") or {}
    assert debug.get("provider") == "gemini"
    assert debug.get("warning_reasons") == ["sheet_column_anomaly"]
    assert (debug.get("warning_detail") or {}).get("quality_issue") == "column_anomaly"
