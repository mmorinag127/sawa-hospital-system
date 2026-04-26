import sys
import pathlib
import json
from datetime import datetime, timedelta
from urllib.error import HTTPError
from uuid import uuid4

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.main import app  # noqa: E402
from src.models.menu import MonthlyMenuEntry  # noqa: E402
from src.models.order import Order, OrderMenuSnapshot  # noqa: E402
from src.models.order_ocr_cache import OrderOcrCache  # noqa: E402
from src.models.output import Bag  # noqa: E402
from src.services import config_service, menu_service, order_service, output_builder  # noqa: E402
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


def _create_seed_order_for_facility(message_id: str, facility_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint=facility_id,
        week_hint="2026-02",
    )
    lines = [
        {
            "date": "2026-02-15",
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "X" if facility_id == "FAC00004" else "2F",
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
        metrics={"request_mode": "ocr_reparse"},
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
        metrics={"request_mode": "ocr_reparse"},
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("ocr_status") == "running"
    job = get_job(job_id)
    assert job is not None
    assert job.get("status") == "running"


def test_draft_sheet_surfaces_running_llm_reparse_health():
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-draft-llm-running")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="running")
    update_job(
        job_id,
        status="running",
        error_message=None,
        metrics={
            "request_mode": "llm_reparse",
            "requested_provider": "gemini",
            "llm_assist": True,
            "processing_stage": "ocr_pipeline",
            "result_state": "processing",
        },
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/draft-sheet")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("reparse_health") == "running"


def test_get_order_surfaces_awaiting_output_llm_reparse_as_active():
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-awaiting-llm-running")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="awaiting_output")
    update_job(
        job_id,
        status="awaiting_output",
        error_message="ocr_output_pending",
        metrics={
            "request_mode": "llm_reparse",
            "requested_provider": "gemini",
            "llm_assist": True,
            "processing_stage": "ocr_pipeline",
            "result_state": "awaiting_output",
            "order_id": order["id"],
            "output_reference": "file://pending-output.json",
        },
    )

    client = TestClient(app)
    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload.get("ocr_status") == "awaiting_output"

    workflow = client.get(f"/orders/{order['id']}/workflow-state")
    assert workflow.status_code == 200
    workflow_payload = workflow.json()
    reparse_state = workflow_payload.get("reparse_state") or {}
    assert reparse_state.get("status") == "awaiting_output"
    assert reparse_state.get("request_mode") == "llm_reparse"


def test_get_order_surfaces_awaiting_output_ocr_rerun_as_active():
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-awaiting-ocr-rerun")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="awaiting_output")
    update_job(
        job_id,
        status="awaiting_output",
        error_message="ocr_output_pending",
        metrics={
            "request_mode": "ocr_rerun",
            "processing_stage": "ocr_pipeline",
            "result_state": "awaiting_output",
            "order_id": order["id"],
            "output_reference": "file://pending-output.json",
        },
    )

    client = TestClient(app)
    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload.get("ocr_status") == "awaiting_output"

    workflow = client.get(f"/orders/{order['id']}/workflow-state")
    assert workflow.status_code == 200
    workflow_payload = workflow.json()
    reparse_state = workflow_payload.get("reparse_state") or {}
    assert reparse_state.get("status") == "awaiting_output"
    assert reparse_state.get("request_mode") == "ocr_rerun"
    assert workflow_payload.get("state") == "rerun_in_progress"


def test_get_order_prefers_live_metrics_error_for_active_ocr_rerun():
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-awaiting-ocr-rerun-metrics-error")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="awaiting_output")
    update_job(
        job_id,
        status="awaiting_output",
        error_message="OCR pipeline request timeout: The read operation timed out",
        metrics={
            "request_mode": "ocr_rerun",
            "processing_stage": "ocr_pipeline",
            "result_state": "awaiting_output",
            "error": "ocr_output_pending",
            "order_id": order["id"],
            "output_reference": "file://pending-output.json",
        },
    )

    client = TestClient(app)
    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload.get("ocr_status") == "awaiting_output"
    assert payload.get("ocr_error") == "ocr_output_pending"


def test_get_order_reconciles_ocr_rerun_when_pending_reference_has_newer_completed_output(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-awaiting-ocr-rerun-completed-neighbor")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|5|",
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr.png"}],
            "tables": [{"page_index": 1, "rows": [["02/15", "朝", "Menu A", "5"]]}],
        },
    )

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="awaiting_output")
    update_job(
        job_id,
        status="awaiting_output",
        error_message="ocr_output_pending",
        output_reference="file://pending-output.json",
        metrics={
            "request_mode": "ocr_rerun",
            "processing_stage": "ocr_pipeline",
            "result_state": "awaiting_output",
            "order_id": order["id"],
        },
    )

    def _load_output(output_ref: str):
        if output_ref == "gs://bucket/output/OCR-order-rerun-final.json":
            return {
                "status": "done",
                "stage": "done",
                "input_reference": "gs://bucket/input/OCR-order-rerun.pdf",
                "output_reference": output_ref,
                "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr.png"}],
                "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|8|",
                "tables": [{"page_index": 1, "rows": [["02/15", "朝", "Menu A", "8"]]}],
            }
        return {
            "status": "running",
            "stage": "ocr",
            "output_reference": output_ref,
        }

    monkeypatch.setattr(order_service, "_load_pipeline_output_once", _load_output)
    monkeypatch.setattr(
        order_service,
        "_list_latest_completed_ocr_outputs",
        lambda *_args, **_kwargs: [
            (
                "gs://bucket/output/OCR-order-rerun-final.json",
                {
                    "status": "done",
                    "stage": "done",
                    "input_reference": "gs://bucket/input/OCR-order-rerun.pdf",
                    "output_reference": "gs://bucket/output/OCR-order-rerun-final.json",
                    "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr.png"}],
                    "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|8|",
                    "tables": [{"page_index": 1, "rows": [["02/15", "朝", "Menu A", "8"]]}],
                    "template_resolution": {
                        "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                        "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
                        "confidence": 0.99,
                        "blocked": False,
                        "blocked_reasons": [],
                    },
                    "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["02/15", "朝", "Menu A", "8"]]}],
                    "table_box": [0.1, 0.2, 0.9, 0.8],
                    "grid_column_edges": [0.1, 0.3, 0.6, 0.9],
                    "grid_row_edges": [0.2, 0.4, 0.8],
                },
            )
        ],
    )

    client = TestClient(app)
    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload.get("ocr_status") == "done"
    assert payload.get("ocr_result_state") == "evidence_ready"
    assert payload.get("ocr_error") in {None, ""}
    assert ((payload.get("ocr_metrics") or {}).get("error")) in {None, ""}

    workflow = client.get(f"/orders/{order['id']}/workflow-state")
    assert workflow.status_code == 200
    workflow_payload = workflow.json()
    reparse_state = workflow_payload.get("reparse_state") or {}
    assert reparse_state.get("status") == "done"
    assert workflow_payload.get("state") != "rerun_in_progress"
    assert workflow_payload.get("ocr_last_reparse_error") in {None, ""}
    assert workflow_payload.get("ocr_reparse_status") not in {"failed", "running"}

    draft = client.get(f"/orders/{order['id']}/draft-sheet")
    assert draft.status_code == 200
    draft_payload = draft.json()
    assert draft_payload.get("reparse_status") == "done"
    assert draft_payload.get("reparse_error") in {None, ""}
    assert draft_payload.get("reparse_request_mode") == "ocr_rerun"

    output = client.get(f"/orders/{order['id']}/ocr-output")
    assert output.status_code == 200
    output_payload = output.json()
    assert output_payload.get("output_reference") == "gs://bucket/output/OCR-order-rerun-final.json"


def test_get_order_keeps_ocr_rerun_active_when_only_older_completed_output_exists(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-awaiting-ocr-rerun-older-output")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|5|",
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr.png"}],
            "tables": [{"page_index": 1, "rows": [["02/15", "朝", "Menu A", "5"]]}],
        },
    )

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="awaiting_output")
    update_job(
        job_id,
        status="awaiting_output",
        error_message="ocr_output_pending",
        output_reference=f"gs://bucket/output/OCR-{order['id']}_20260422_160712_447631.pdf.json",
        metrics={
            "request_mode": "ocr_rerun",
            "processing_stage": "ocr_pipeline",
            "result_state": "awaiting_output",
            "order_id": order["id"],
            "awaiting_output_since": "2026-04-22T16:07:12",
            "stage_updated_at": "2026-04-22T16:07:12",
        },
    )

    monkeypatch.setattr(
        order_service,
        "_load_pipeline_output_once",
        lambda output_ref: {
            "status": "running",
            "stage": "ocr",
            "output_reference": output_ref,
        },
    )
    monkeypatch.setattr(
        order_service,
        "_list_latest_completed_ocr_outputs",
        lambda *_args, **_kwargs: [
            (
                "gs://bucket/output/OCR-ORD596231b6_20260422_154441_050299.pdf.json",
                {
                    "status": "done",
                    "stage": "done",
                    "input_reference": "gs://bucket/input/OCR-order-rerun-old.pdf",
                    "output_reference": f"gs://bucket/output/OCR-{order['id']}_20260422_154441_050299.pdf.json",
                    "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr.png"}],
                    "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|8|",
                    "tables": [{"page_index": 1, "rows": [["02/15", "朝", "Menu A", "8"]]}],
                    "template_resolution": {
                        "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                        "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
                        "confidence": 0.99,
                        "blocked": False,
                        "blocked_reasons": [],
                    },
                    "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["02/15", "朝", "Menu A", "8"]]}],
                    "table_box": [0.1, 0.2, 0.9, 0.8],
                    "grid_column_edges": [0.1, 0.3, 0.6, 0.9],
                    "grid_row_edges": [0.2, 0.4, 0.8],
                },
            )
        ],
    )

    client = TestClient(app)
    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload.get("ocr_status") == "awaiting_output"

    workflow = client.get(f"/orders/{order['id']}/workflow-state")
    assert workflow.status_code == 200
    workflow_payload = workflow.json()
    reparse_state = workflow_payload.get("reparse_state") or {}
    assert reparse_state.get("status") in {"awaiting_output", "recovering"}
    assert workflow_payload.get("state") == "rerun_in_progress"


def test_draft_sheet_surfaces_awaiting_output_ocr_rerun_state():
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-draft-awaiting-ocr-rerun")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="awaiting_output")
    update_job(
        job_id,
        status="awaiting_output",
        error_message="OCR pipeline request timeout: The read operation timed out",
        metrics={
            "request_mode": "ocr_rerun",
            "processing_stage": "ocr_pipeline",
            "result_state": "awaiting_output",
            "error": "ocr_output_pending",
            "order_id": order["id"],
            "output_reference": "file://pending-output.json",
        },
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/draft-sheet")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("reparse_health") == "awaiting_output"
    assert payload.get("reparse_status") == "awaiting_output"
    assert payload.get("reparse_error") == "ocr_output_pending"
    assert payload.get("reparse_request_mode") == "ocr_rerun"


def test_done_ocr_rerun_heals_stale_pending_error_across_detail_surfaces(tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-done-ocr-rerun-stale-pending")
    output_path = tmp_path / "ocr_done_final.json"
    output_path.write_text(
        json.dumps(
            {
                "status": "done",
                "stage": "done",
                "input_reference": "file://dummy-order.pdf",
                "output_reference": f"file://{output_path}",
                "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr.png"}],
                "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|8|",
                "tables": [{"page_index": 1, "rows": [["02/15", "朝", "Menu A", "8"]]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "done",
            "stage": "done",
            "input_reference": "file://dummy-order.pdf",
            "output_reference": f"file://{output_path}",
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr.png"}],
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|8|",
            "tables": [{"page_index": 1, "rows": [["02/15", "朝", "Menu A", "8"]]}],
        },
    )

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="done")
    update_job(
        job_id,
        status="done",
        output_reference=f"file://{output_path}",
        error_message="ocr_output_pending",
        metrics={
            "request_mode": "ocr_rerun",
            "processing_stage": "evidence_ready",
            "result_state": "evidence_ready",
            "error": "ocr_output_pending",
            "trigger_error": "OCR pipeline request timeout: The read operation timed out",
            "awaiting_output_since": "2026-04-22T16:26:04.410692",
            "next_recovery_at": "2026-04-22T16:36:04.410692",
            "evidence_run_id": "OEV-stale-done",
            "new_evidence_available": True,
            "order_id": order["id"],
        },
    )

    client = TestClient(app)

    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload.get("ocr_status") == "done"
    assert detail_payload.get("ocr_error") in {None, ""}
    assert ((detail_payload.get("ocr_metrics") or {}).get("error")) in {None, ""}
    assert ((detail_payload.get("ocr_metrics") or {}).get("trigger_error")) in {None, ""}

    draft = client.get(f"/orders/{order['id']}/draft-sheet")
    assert draft.status_code == 200
    draft_payload = draft.json()
    assert draft_payload.get("reparse_status") == "done"
    assert draft_payload.get("reparse_error") in {None, ""}

    workflow = client.get(f"/orders/{order['id']}/workflow-state")
    assert workflow.status_code == 200
    workflow_payload = workflow.json()
    reparse_state = workflow_payload.get("reparse_state") or {}
    assert reparse_state.get("status") == "done"
    assert workflow_payload.get("ocr_last_reparse_error") in {None, ""}
    assert workflow_payload.get("ocr_reparse_status") not in {"failed", "running"}

    healed_job = get_job(job_id)
    assert healed_job is not None
    assert healed_job.get("error_message") in {None, ""}
    healed_metrics = healed_job.get("metrics") or {}
    assert healed_metrics.get("error") in {None, ""}
    assert healed_metrics.get("trigger_error") in {None, ""}
    assert healed_metrics.get("awaiting_output_since") in {None, ""}
    assert healed_metrics.get("next_recovery_at") in {None, ""}


def test_detail_reads_do_not_wait_for_missing_ocr_output_and_surface_rerun(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-loading-root-fix")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="failed")
    update_job(
        job_id,
        status="failed",
        output_reference="file://missing-output.json",
        error_message="ocr_output_missing",
        metrics={"order_id": order["id"]},
    )

    def _raise_missing(_uri: str):
        raise FileNotFoundError("missing object")

    def _retry_called(*_args, **_kwargs):
        raise AssertionError("detail read path must not retry OCR output recovery")

    monkeypatch.setattr(orders_api, "load_bytes_from_uri", _raise_missing)
    monkeypatch.setattr(order_service, "load_bytes_from_uri", _raise_missing)
    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", _retry_called)

    client = TestClient(app)

    detail_res = client.get(f"/orders/{order['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail.get("ocr_status") == "blocked"
    assert detail.get("ocr_error") == "ocr_evidence_recovery_required"
    assert detail.get("ocr_result_state") == "blocked"
    assert detail.get("ocr_processing_stage") == "evidence_unavailable"
    assert detail.get("ocr_updated_at") is None
    workflow = detail.get("workflow_state") or {}
    assert workflow.get("state") == "uploaded"
    assert workflow.get("primary_action") == "run_ocr_pipeline"
    assert "evidence_view_unavailable" in (workflow.get("blockers_json") or [])

    output_res = client.get(f"/orders/{order['id']}/ocr-output")
    assert output_res.status_code == 409
    assert output_res.json() == {
        "recovery_required": True,
        "detail": "ocr evidence recovery required",
    }


def test_get_ocr_output_returns_pending_when_active_ocr_rerun_output_is_pending(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-ocr-output-active-rerun-pending")
    order_service._save_order_ocr_cache(  # noqa: SLF001
        order["id"],
        {
            "status": "done",
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|5|",
            "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-old.png"}],
            "output_reference": "gs://bucket/output/OCR-old.pdf.json",
        },
    )

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="awaiting_output")
    update_job(
        job_id,
        status="awaiting_output",
        error_message="OCR pipeline request timeout: The read operation timed out",
        output_reference=f"gs://bucket/output/OCR-{order['id']}_20260422_162548_904035.pdf.json",
        metrics={
            "request_mode": "ocr_rerun",
            "processing_stage": "ocr_pipeline",
            "result_state": "awaiting_output",
            "error": "ocr_output_pending",
            "order_id": order["id"],
        },
    )

    monkeypatch.setattr(
        order_service,
        "_load_pipeline_output_once",
        lambda output_ref: {
            "status": "running",
            "stage": "ocr",
            "output_reference": output_ref,
        },
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/ocr-output")
    assert res.status_code == 202
    assert res.json() == {"pending": True}


def test_get_order_reconciles_completed_first_pass_output(monkeypatch, tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-first-pass-reconcile")
    output_path = tmp_path / "ocr_output_done_first_pass.json"
    output_path.write_text(
        json.dumps({"status": "done", "table_raw": "|02/15|朝|Menu A|", "pages": [{"tables": [{"rows": [["02/15", "朝", "Menu A"]]}]}]}),
        encoding="utf-8",
    )

    job_id = f"OCR-{order['message_id']}"
    create_job(job_id, input_reference=order["document"], status="running")
    update_job(
        job_id,
        status="running",
        output_reference=f"file://{output_path}",
        error_message=None,
    )

    seen: list[str] = []

    def _fake_reconcile(target_job_id: str) -> bool:
        seen.append(target_job_id)
        update_job(target_job_id, status="done", error_message=None, metrics={"processing_stage": "evidence_ready"})
        return True

    monkeypatch.setattr(orders_api.order_service, "reconcile_completed_ocr_job", _fake_reconcile)

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}")
    assert res.status_code == 200
    payload = res.json()
    assert seen == [job_id]
    assert payload.get("ocr_status") == "done"


def test_get_order_reconciles_done_first_pass_output_without_evidence(monkeypatch, tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-first-pass-done-missing-evidence")
    output_path = tmp_path / "ocr_output_done_first_pass_missing_evidence.json"
    output_path.write_text(
        json.dumps({"status": "done", "table_raw": "|02/15|朝|Menu A|", "pages": [{"tables": [{"rows": [["02/15", "朝", "Menu A"]]}]}]}),
        encoding="utf-8",
    )

    job_id = f"OCR-{order['message_id']}"
    create_job(job_id, input_reference=order["document"], status="done")
    update_job(
        job_id,
        status="done",
        output_reference=f"file://{output_path}",
        error_message=None,
    )

    seen: list[str] = []

    def _fake_reconcile(target_job_id: str) -> bool:
        seen.append(target_job_id)
        return True

    monkeypatch.setattr(orders_api.order_service, "reconcile_completed_ocr_job", _fake_reconcile)

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}")
    assert res.status_code == 200
    assert seen == [job_id]


def test_get_order_does_not_reconcile_done_first_pass_output_when_persisted_evidence_exists(monkeypatch, tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-first-pass-already-persisted")
    output_path = tmp_path / "ocr_output_done_first_pass_already_persisted.json"
    output_path.write_text(
        json.dumps({"status": "done", "table_raw": "|02/15|朝|Menu A|", "pages": [{"tables": [{"rows": [["02/15", "朝", "Menu A"]]}]}]}),
        encoding="utf-8",
    )

    job_id = f"OCR-{order['message_id']}"
    create_job(job_id, input_reference=order["document"], status="done")
    update_job(
        job_id,
        status="done",
        output_reference=f"file://{output_path}",
        error_message=None,
    )

    seen: list[str] = []

    monkeypatch.setattr(
        orders_api.order_service,
        "get_latest_ocr_evidence_run",
        lambda *_args, **_kwargs: {"id": "OEVexisting"},
    )

    def _fake_reconcile(target_job_id: str) -> bool:
        seen.append(target_job_id)
        return True

    monkeypatch.setattr(orders_api.order_service, "reconcile_completed_ocr_job", _fake_reconcile)

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}")
    assert res.status_code == 200
    assert seen == []


def test_get_order_reconciles_done_order_bound_first_pass_output_without_evidence(monkeypatch, tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-order-bound-first-pass")
    output_path = tmp_path / "ocr_output_done_order_bound_first_pass.json"
    output_path.write_text(
        json.dumps({"status": "done", "table_raw": "|02/15|朝|Menu A|", "pages": [{"tables": [{"rows": [["02/15", "朝", "Menu A"]]}]}]}),
        encoding="utf-8",
    )

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="done")
    update_job(
        job_id,
        status="done",
        output_reference=f"file://{output_path}",
        error_message=None,
        metrics={"request_mode": "ingest_first_pass"},
    )

    seen: list[str] = []

    def _fake_reconcile(target_job_id: str) -> bool:
        seen.append(target_job_id)
        return True

    monkeypatch.setattr(orders_api.order_service, "reconcile_completed_ocr_job", _fake_reconcile)

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}")
    assert res.status_code == 200
    assert seen == [job_id]


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
            "apply_gate": {
                "can_apply": False,
                "can_confirm": False,
                "blockers": ["weekly_menu_missing"],
                "confirm_blockers": ["weekly_menu_missing"],
                "warnings": [],
                "confirm_warnings": [],
            },
        },
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


def test_build_order_lines_for_outputs_prefers_stored_week_value_over_resolved_week(monkeypatch):
    order = {
        "id": "ORDbagsweek001",
        "facility": "FAC00003",
        "stored_week_value": "2026-04",
        "persisted_week_value": "2026-04@2026-04-12~2026-04-18",
        "week_value": "2026-04@2026-04-12~2026-04-18",
        "lines": [
            {
                "date": "2026-04-18",
                "daypart": "昼",
                "menu_name": "筑前煮",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 9,
            }
        ],
    }

    def _fake_entries(week_value, facility_id):
        if week_value == "2026-04@2026-04-12~2026-04-18":
            return [
                {
                    "date": "2026-04-18",
                    "daypart": "昼",
                    "menu": "別メニュー",
                }
            ]
        return []

    monkeypatch.setattr(order_service, "_collect_menu_entries_for_week", _fake_entries)
    monkeypatch.setattr(order_service, "_collect_menu_items_for_week", lambda *_args, **_kwargs: [])

    lines = output_builder.build_order_lines_for_outputs(order)

    assert [line.get("menu_name") for line in lines] == ["筑前煮"]


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
    assert group["menu_category"] == "主A"
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["total_quantity"] == 17.0
    assert regular["calculation_basis_label"] == "50g/人"
    assert any((breakdown.get("order_refs") or []) for bag_type in regular["bag_type_groups"] for breakdown in bag_type["breakdowns"])
    mixer = next(item for item in group["diet_groups"] if item.get("diet_type") == "mixer")
    assert mixer["calculation_basis_label"] == "50g/人"


def test_get_daily_bags_uses_current_lines_without_materialized_rebuild(monkeypatch):
    order_service.clear_all()
    month_id = "2026-02-stale"
    menu_csv = "menu\n筑前煮\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "筑前煮")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 50, "unit_type": "g", "daypart": "昼", "category": "主A"},
    )
    payload = IngestEmailPayload(
        message_id="msg-status-api-daily-bags-stale-001",
        pdf_uri="file://dummy-daily-bags-stale.pdf",
        received_at=datetime(2026, 2, 18, 9, 0, 0),
        facility_hint="FAC00003",
        week_hint="2026-02",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
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
        ],
    )

    with session_scope() as session:
        session.add(
            Bag(
                id=f"BAG{order['id'][-6:]}A",
                order_id=order["id"],
                date=datetime(2026, 2, 18).date(),
                daypart="昼",
                menu_name="筑前煮",
                diet_type="regular",
                area_id=None,
                bag_type="large",
                quantity=99,
            )
        )

    monkeypatch.setattr(
        output_builder,
        "rebuild_bags",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("daily-bags read should not rebuild bags")),
    )

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-02-18")

    assert res.status_code == 200
    payload = res.json()
    group = next(item for item in payload["groups"] if item.get("menu_name") == "筑前煮")
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["total_quantity"] == 17.0
    assert regular["calculation_basis_label"] == "50g/人"
    assert all(
        row.get("area_id") in {"2F", "3F"}
        for bag_type in regular["bag_type_groups"]
        for breakdown in bag_type["breakdowns"]
        for row in breakdown.get("order_refs") or []
    )


def test_get_daily_bags_returns_empty_groups_when_target_date_has_no_output_rows():
    order_service.clear_all()

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-02-18")

    assert res.status_code == 200
    payload = res.json()
    assert payload["date"] == "2026-02-18"
    assert payload["order_count"] == 0
    assert payload["groups"] == []


def test_daily_bags_prefers_line_daypart_over_snapshot_daypart():
    order_service.clear_all()
    month_id = f"2026-03-status-api-daypart-{uuid4().hex[:8]}"
    menu_csv = "menu\n豆腐の煮物\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "豆腐の煮物")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 70, "unit_type": "g", "daypart": "朝食", "category": "主菜"},
    )
    payload = IngestEmailPayload(
        message_id="msg-status-api-daily-bags-daypart-001",
        pdf_uri="file://dummy-daily-bags-daypart.pdf",
        received_at=datetime(2026, 3, 24, 9, 0, 0),
        facility_hint="FAC00003",
        week_hint="2026-03",
    )
    order = order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "豆腐の煮物",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 9,
            }
        ],
    )
    with session_scope() as session:
        session.add(
            OrderMenuSnapshot(
                id="OMSstatusdaily001",
                order_id=order["id"],
                snapshot_json={
                    "version": 1,
                    "generated_at": "2026-03-24T09:00:00",
                    "menu_items": {
                        "豆腐の煮物": {
                            "daypart": "朝食",
                            "category": "主菜",
                            "qty_per_serving": 70.0,
                            "unit_type": "g",
                        }
                    },
                },
            )
        )

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == "豆腐の煮物")
    assert group["daypart"] == "昼"
    assert group["menu_category"] == "主菜"


def test_daily_bags_prefers_monthly_menu_entry_category_over_menu_master():
    order_service.clear_all()
    month_id = "2099-03"
    menu_name = f"豆腐の煮物-{uuid4().hex[:6]}"
    menu_csv = f"menu\n{menu_name}\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, menu_name)
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 70, "unit_type": "g", "daypart": "昼食", "category": "主菜"},
    )
    with session_scope() as session:
        session.add(
            MonthlyMenuEntry(
                id=f"MMEdaily{uuid4().hex[:6]}",
                monthly_menu_id=month_id,
                menu_date=datetime(2099, 3, 24).date(),
                daypart="昼食",
                name=menu_name,
                category="副菜",
                slot_index=2,
                facility_override=None,
            )
        )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-entry-001",
            pdf_uri="file://dummy-daily-bags-entry.pdf",
            received_at=datetime(2099, 3, 24, 9, 0, 0),
            facility_hint="FAC00003",
            week_hint=month_id,
        ),
        lines=[
            {
                "date": "2099-03-24",
                "daypart": "昼",
                "menu_name": menu_name,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 9,
            }
        ],
    )
    order_service.set_status(order["id"], "確定")

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2099-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == menu_name)
    assert group["menu_category"] == "副菜"
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["calculation_basis_label"] == "40g/人"
    assert regular["total_amount_label"] == "360g"


def test_daily_bags_prefers_monthly_menu_entry_over_item_defaults():
    order_service.clear_all()
    month_id = "2099-03"
    menu_name = f"豆腐の煮物-{uuid4().hex[:6]}"
    menu_csv = f"menu\n{menu_name}\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, menu_name)
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 70, "unit_type": "g", "daypart": "朝食", "category": "主菜"},
    )
    with session_scope() as session:
        session.add(
            MonthlyMenuEntry(
                id=f"MME{uuid4().hex[:7]}",
                monthly_menu_id=month_id,
                menu_date=datetime(2099, 3, 24).date(),
                daypart="昼",
                name=menu_name,
                category="副菜",
                slot_index=1,
            )
        )

    order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-entry-001",
            pdf_uri="file://dummy-daily-bags-entry.pdf",
            received_at=datetime(2099, 3, 24, 9, 0, 0),
            facility_hint="FAC00003",
            week_hint=month_id,
        ),
        lines=[
            {
                "date": "2099-03-24",
                "daypart": "昼",
                "menu_name": menu_name,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 9,
            }
        ],
    )

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2099-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == menu_name)
    assert group["menu_category"] == "副菜"
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["calculation_basis_label"] == "40g/人"
    assert regular["total_amount_label"] == "360g"


def test_daily_bags_prefers_current_menu_qty_over_stale_snapshot_qty():
    order_service.clear_all()
    month_id = "2099-03"
    menu_name = f"南瓜サラダ-{uuid4().hex[:6]}"
    menu_csv = f"menu\n{menu_name}\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, menu_name)
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 50, "unit_type": "g", "daypart": "夕食", "category": "副菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-snapshot-qty-001",
            pdf_uri="file://dummy-daily-bags-snapshot-qty.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00003",
            week_hint=month_id,
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "夕",
                "menu_name": menu_name,
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 9,
            }
        ],
    )
    order_service.set_status(order["id"], "確定")
    with session_scope() as session:
        session.add(
            OrderMenuSnapshot(
                id=f"OMSsnapshotqty{uuid4().hex[:6]}",
                order_id=order["id"],
                snapshot_json={
                    "version": 1,
                    "generated_at": "2026-03-24T09:00:00",
                    "menu_items": {
                        menu_name: {
                            "daypart": "夕食",
                            "category": "副菜",
                            "qty_per_serving": 40.0,
                            "unit_type": "g",
                        }
                    },
                },
            )
        )

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == menu_name)
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["calculation_basis_label"] == "50g/人"
    assert regular["total_amount_label"] == "450g"


def test_daily_bags_treats_staff_as_regular():
    order_service.clear_all()
    month_id = f"2026-03-status-api-staff-{uuid4().hex[:8]}"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-staff-001",
            pdf_uri="file://dummy-daily-bags-staff.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00014",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 9,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "staff",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 3,
            },
        ],
    )
    order_service.set_status(order["id"], "確定")

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == "ホイコーロー")
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["total_quantity"] == 12.0
    assert regular["calculation_basis_label"] == "100g/人"
    assert not any(item.get("diet_type") == "staff" for item in group["diet_groups"])


def test_daily_bags_treats_daycare_as_regular():
    order_service.clear_all()
    month_id = f"2026-03-status-api-daycare-{uuid4().hex[:8]}"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-daycare-001",
            pdf_uri="file://dummy-daily-bags-daycare.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00004",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 9,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "daycare",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 4,
            },
        ],
    )
    order_service.set_status(order["id"], "確定")

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == "ホイコーロー")
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["total_quantity"] == 13.0
    assert regular["calculation_basis_label"] == "100g/人"
    assert not any(item.get("diet_type") == "daycare" for item in group["diet_groups"])


def test_daily_bags_treats_regular_bag_and_1600_as_regular():
    order_service.clear_all()
    month_id = f"2026-03-status-api-regular-bucket-{uuid4().hex[:8]}"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-regular-bucket-001",
            pdf_uri="file://dummy-daily-bags-regular-bucket.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00004",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 9,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular_bag",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "ホイコーロー",
                "diet_type": "regular_1600kcal",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 2,
            },
        ],
    )
    order_service.set_status(order["id"], "確定")

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == "ホイコーロー")
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["total_quantity"] == 12.0
    assert regular["calculation_basis_label"] == "100g/人"
    assert not any(item.get("diet_type") in {"regular_bag", "regular_1600kcal"} for item in group["diet_groups"])


def test_daily_bags_splits_garnish_and_canonicalizes_cut_units():
    order_service.clear_all()
    month_id = f"2026-03-status-api-garnish-cut-{uuid4().hex[:8]}"
    menu_csv = "menu\n白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 1, "unit_type": "枚", "daypart": "夕食", "category": "主菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-garnish-cut-001",
            pdf_uri="file://dummy-daily-bags-garnish-cut.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00001",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "夕",
                "menu_name": "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 4,
            },
        ],
    )
    order_service.set_status(order["id"], "確定")

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    groups = res.json()["groups"]

    main_group = next(item for item in groups if item.get("menu_name") == "白身魚のフライ")
    main_regular = next(item for item in main_group["diet_groups"] if item.get("diet_type") == "regular")
    assert main_regular["total_quantity"] == 4.0
    assert main_regular["calculation_basis_label"] == "1切/人"
    assert main_regular["total_amount_label"] == "4切"

    garnish_group = next(item for item in groups if item.get("menu_name") == "ﾌﾞﾛｯｺﾘｰ")
    garnish_regular = next(item for item in garnish_group["diet_groups"] if item.get("diet_type") == "regular")
    assert garnish_group["menu_category"] == "添え"
    assert garnish_regular["total_quantity"] == 4.0
    assert garnish_regular["calculation_basis_label"] == "30g/人"
    assert garnish_regular["total_amount_label"] == "120g"


def test_build_order_lines_for_outputs_matches_split_main_line_to_combined_menu_item(monkeypatch):
    order = {
        "id": "ORD-garnish-alias-001",
        "facility": "FAC00001",
        "week_value": "2026-03",
        "lines": [
            {
                "date": "2026-03-24",
                "daypart": "夕",
                "menu_name": "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ",
                "diet_type": "regular",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 4,
            }
        ],
    }

    monkeypatch.setattr(output_builder.config_service, "get_facility_config", lambda _facility_id: None)
    monkeypatch.setattr(output_builder.menu_service, "get_menu_entries_for_facility", lambda _month_id, _facility_id: [])
    monkeypatch.setattr(
        output_builder.menu_service,
        "get_menu_items_for_facility",
        lambda _month_id, _facility_id: [
            {
                "name": "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ",
                "qty_per_serving": 1,
                "unit_type": "枚",
                "daypart": "夕食",
                "category": "主菜",
            }
        ],
    )
    monkeypatch.setattr(output_builder, "get_order_menu_snapshot", lambda _order_id: {})

    lines = output_builder.build_order_lines_for_outputs(order)
    main_line = next(item for item in lines if item.get("menu_name") == "白身魚のフライ")
    garnish_line = next(item for item in lines if item.get("menu_name") == "ﾌﾞﾛｯｺﾘｰ")

    assert main_line["menu_category"] == "主菜"
    assert main_line["menu_qty_per_serving"] == 1
    assert main_line["menu_unit_type"] == "枚"
    assert garnish_line["menu_category"] == "添え"
    assert garnish_line["menu_qty_per_serving"] == 30
    assert garnish_line["menu_unit_type"] == "g"


def test_daily_bags_merges_forbidden_diets():
    order_service.clear_all()
    month_id = f"2026-03-status-api-forbidden-bucket-{uuid4().hex[:8]}"
    menu_csv = "menu\n豆腐の煮物\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "豆腐の煮物")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 40, "unit_type": "g", "daypart": "昼食", "category": "副菜"},
    )
    order = order_service.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-forbidden-bucket-001",
            pdf_uri="file://dummy-daily-bags-forbidden-bucket.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00014",
            week_hint="2026-03",
        ),
        lines=[
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "豆腐の煮物",
                "diet_type": "no_meat",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 2,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "豆腐の煮物",
                "diet_type": "no_fish",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 1,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "豆腐の煮物",
                "diet_type": "forbidden_other",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 3,
            },
            {
                "date": "2026-03-24",
                "daypart": "昼",
                "menu_name": "豆腐の煮物",
                "diet_type": "no_fried",
                "area_id": "X",
                "bag_type": "standard",
                "quantity_original": 4,
            },
        ],
    )
    order_service.set_status(order["id"], "確定")

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == "豆腐の煮物")
    forbidden = next(item for item in group["diet_groups"] if item.get("diet_type") == "forbidden")
    assert forbidden["total_quantity"] == 10.0
    assert forbidden["calculation_basis_label"] == "40g/人"
    assert not any(
        item.get("diet_type") in {"no_meat", "no_fish", "forbidden_other", "no_fried"}
        for item in group["diet_groups"]
    )


def test_download_document_falls_back_to_signed_url_when_direct_gcs_read_fails(monkeypatch):
    client = TestClient(app)
    order_id = "ORD-document-fallback-001"
    pdf_bytes = b"%PDF-1.4 fallback"

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda _order_id: {"id": order_id, "document": "gs://bucket/path/document.pdf"},
    )

    def _raise(_uri: str):
        raise RuntimeError("direct read failed")

    monkeypatch.setattr(orders_api, "load_bytes_from_uri", _raise)
    monkeypatch.setattr(
        orders_api.order_service,
        "_signed_url_from_uri",
        lambda _uri: "https://signed.example/document.pdf",
    )

    class _Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    monkeypatch.setattr(orders_api, "urlopen", lambda *_args, **_kwargs: _Response(pdf_bytes))

    res = client.get(f"/orders/{order_id}/document")

    assert res.status_code == 200
    assert res.content == pdf_bytes
    assert res.headers["content-type"].startswith("application/pdf")
    assert res.headers["x-sawa-document-source"] == "original"
    assert res.headers["x-sawa-document-variant"] == "signed_url"


def test_download_document_falls_back_to_archived_ocr_input_when_canonical_uri_is_missing(monkeypatch):
    client = TestClient(app)
    order_id = "ORD-document-fallback-ocr-001"
    archived_pdf = b"%PDF-archived-original"

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda _order_id: {"id": order_id, "document": "gs://bucket/path/document.pdf"},
    )

    def _raise(_uri: str):
        raise FileNotFoundError("missing object")

    monkeypatch.setattr(orders_api, "load_bytes_from_uri", _raise)
    monkeypatch.setattr(
        orders_api.order_service,
        "_signed_url_from_uri",
        lambda _uri: "https://signed.example/document.pdf",
    )

    def _urlopen(uri, *_args, **_kwargs):
        if uri == "https://signed.example/document.pdf":
            raise HTTPError(uri, 404, "Not Found", None, None)
        raise AssertionError(f"unexpected urlopen uri: {uri}")

    monkeypatch.setattr(orders_api, "urlopen", _urlopen)
    monkeypatch.setattr(
        orders_api.order_service,
        "get_ocr_output",
        lambda _order_id: (
            {
                "input_reference": "gs://bucket/input/ocr-input.pdf",
                "combined": {
                    "ocr_pdf": "gs://bucket/output/ocr.pdf",
                    "layout_pdf": "gs://bucket/output/layout.pdf",
                },
            },
            None,
        ),
    )

    def _load(uri: str):
        if uri == "gs://bucket/path/document.pdf":
            raise FileNotFoundError("missing object")
        if uri == "gs://bucket/input/ocr-input.pdf":
            return archived_pdf
        raise AssertionError(f"unexpected load uri: {uri}")

    monkeypatch.setattr(orders_api, "load_bytes_from_uri", _load)

    res = client.get(f"/orders/{order_id}/document")

    assert res.status_code == 200
    assert res.content == archived_pdf
    assert res.headers["x-sawa-document-source"] == "original_archive"
    assert res.headers["x-sawa-document-variant"] == "ocr_input_reference"


def test_download_document_returns_404_when_source_and_ocr_artifacts_missing(monkeypatch):
    client = TestClient(app)
    order_id = "ORD-document-missing-001"

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda _order_id: {"id": order_id, "document": "gs://bucket/path/document.pdf"},
    )

    def _raise(_uri: str):
        raise FileNotFoundError("missing object")

    monkeypatch.setattr(orders_api, "load_bytes_from_uri", _raise)
    monkeypatch.setattr(
        orders_api.order_service,
        "_signed_url_from_uri",
        lambda _uri: "https://signed.example/document.pdf",
    )
    monkeypatch.setattr(
        orders_api,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPError("https://signed.example/document.pdf", 404, "Not Found", None, None)),
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "get_ocr_output",
        lambda _order_id: ({"combined": {}}, None),
    )

    res = client.get(f"/orders/{order_id}/document")

    assert res.status_code == 404
    assert res.json()["detail"] == "document not found"


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
    assert job.get("output_reference") in {None, ""}
    assert (job.get("metrics") or {}).get("request_mode") == "llm_reparse"


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


def test_get_order_preserves_terminal_llm_reparse_failure_status_against_cached_done(tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-001b-llm")
    output_path = tmp_path / "ocr_output_done_llm.json"
    output_path.write_text(json.dumps({"status": "done", "table_raw": "|a|b|"}), encoding="utf-8")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="failed")
    update_job(
        job_id,
        status="failed",
        output_reference=f"file://{output_path}",
        error_message="main_ocr_failed:gemini",
        metrics={
            "request_mode": "llm_reparse",
            "requested_provider": "gemini",
            "llm_assist": True,
            "processing_stage": "inference",
            "result_state": "hard_failed",
        },
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("ocr_status") == "failed"
    assert payload.get("ocr_error") == "main_ocr_failed:gemini"
    assert (payload.get("ocr_metrics") or {}).get("request_mode") == "llm_reparse"


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


def test_reparse_endpoint_accepts_merged_cell_prompt_preset(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003-merged-preset")
    captured: dict[str, object] = {}

    def _fake_run(order_id, ocr_prompt, prompt_preset=None, ocr_provider=None, ocr_model=None, llm_assist=False):
        captured["order_id"] = order_id
        captured["prompt_preset"] = prompt_preset
        captured["ocr_provider"] = ocr_provider
        captured["llm_assist"] = llm_assist

    monkeypatch.setattr(orders_api, "_run_reparse_background", _fake_run)

    client = TestClient(app)
    res = client.post(
        f"/orders/{order['id']}/reparse",
        json={"ocr_provider": "gemini", "llm_assist": True, "prompt_preset": "merged_cell_quantity_spans"},
    )

    assert res.status_code == 202
    assert captured["order_id"] == order["id"]
    assert captured["prompt_preset"] == "merged_cell_quantity_spans"
    assert captured["ocr_provider"] == "gemini"
    assert captured["llm_assist"] is True


def test_ocr_recover_endpoint_requests_pipeline_first_pass(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003-recover")
    captured: dict[str, object] = {}

    def _fake_run(order_id, ocr_job_id):
        captured["order_id"] = order_id
        captured["ocr_job_id"] = ocr_job_id

    monkeypatch.setattr(orders_api, "_run_ocr_rerun_background", _fake_run)

    client = TestClient(app)
    res = client.post(f"/orders/{order['id']}/ocr-recover")

    assert res.status_code == 202
    assert res.json()["accepted"] is True
    assert res.json()["mode"] == "pipeline_recovery"
    assert captured["order_id"] == order["id"]
    assert captured["ocr_job_id"] == f"OCR-{order['id']}"


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

    def _fake_run(order_id, ocr_job_id):
        captured["order_id"] = order_id
        captured["ocr_job_id"] = ocr_job_id

    monkeypatch.setattr(orders_api, "_run_ocr_rerun_background", _fake_run)

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
    assert captured["ocr_job_id"] == job_id


def test_run_reparse_background_marks_job_failed_on_crash(monkeypatch):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-003b")
    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="running")
    update_job(
        job_id,
        status="running",
        metrics={
            "request_mode": "llm_reparse",
            "requested_provider": "gemini",
            "llm_assist": True,
            "processing_stage": "queued",
            "result_state": "processing",
        },
    )

    def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(order_service, "reparse_order", _raise)

    orders_api._run_reparse_background(order["id"], None, None, "gemini", None, True)

    job = get_job(job_id)
    assert job is not None
    assert job.get("status") == "failed"
    assert "reparse_crashed:boom" in str(job.get("error_message") or "")
    metrics = job.get("metrics") or {}
    assert metrics.get("request_mode") == "llm_reparse"
    assert metrics.get("requested_provider") == "gemini"
    assert metrics.get("processing_stage") == "crashed"
    assert metrics.get("result_state") == "hard_failed"
    assert metrics.get("error") == "reparse_crashed"
    assert metrics.get("crash_detail") == "boom"


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


def test_get_ocr_output_surfaces_failed_llm_reparse_debug_from_job_metrics(tmp_path):
    order_service.clear_all()
    order = _create_seed_order("msg-status-api-005-failed-llm")
    output_path = tmp_path / "ocr_output_failed_llm.json"
    output_path.write_text(json.dumps({"status": "done", "table_raw": "|a|b|"}), encoding="utf-8")

    job_id = f"OCR-{order['id']}"
    create_job(job_id, input_reference=order["document"], status="failed")
    update_job(
        job_id,
        status="failed",
        output_reference=f"file://{output_path}",
        error_message="main_ocr_failed:gemini",
        metrics={
            "request_mode": "llm_reparse",
            "requested_provider": "gemini",
            "llm_assist": True,
            "processing_stage": "inference",
            "result_state": "hard_failed",
            "error": "main_ocr_failed:gemini",
            "confirmed_lines_retained": True,
        },
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/ocr-output")
    assert res.status_code == 200
    payload = res.json()
    debug = payload.get("_reparse_debug") or {}
    assert debug.get("provider") == "gemini"
    assert debug.get("requested_provider") == "gemini"
    assert debug.get("error") == "main_ocr_failed:gemini"
    assert debug.get("processing_stage") == "inference"
    assert debug.get("result_state") == "hard_failed"


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


def test_get_ocr_output_api_recanonicalizes_stale_aux_position_fallback():
    order_service.clear_all()
    order = _create_seed_order_for_facility("msg-status-api-fac00004-aux-001", "FAC00004")
    rows = [
        ["", "", "", "", "", "", "", "", "", "", "", "山田菜", "備考欄"],
        ["", "", "", "献立", "合計", "#☆", "通所", "職員", "平森", "", "", "", ""],
        ["日 付", "区分", "", "", "", "", "", "", "肉蒸", "魚禁", "揚げ物", "", ""],
        ["", "", "", "", "70", "", "", "", "", "", "", "", ""],
        ["4/26", "朝", "主", "鶏じゃが", "67", "66", "", "", "", "", "", "", ""],
    ]
    order_service._save_order_ocr_cache(  # noqa: SLF001
        order["id"],
        {
            "status": "done",
            "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": rows,
                    "cells": [
                        {"row_index": r, "col_index": c, "text": value, "bbox": [float(c), float(r), float(c + 1), float(r + 1)]}
                        for r, row in enumerate(rows)
                        for c, value in enumerate(row)
                    ],
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                }
            ],
            "column_mapping_resolution": {
                "resolved_value": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                "resolved_column_mapping_id": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "decision_source": "position_fallback",
                "partial_quantity_mapping": False,
                "confidence": 0.91,
                "evidence_ref": {"page_index": 1, "table_id": "p1_t1", "source_col_indexes": [4, 5, 6, 7, 8, 9, 10]},
            },
            "column_mapping_candidates": [
                {
                    "candidate_id": "pcm-stale-aux",
                    "candidate_type": "position_fallback_candidate",
                    "value": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                    "label": "stale-aux",
                    "score": 0.91,
                    "decision_source": "position_fallback",
                    "auto_selectable": True,
                }
            ],
        },
    )

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/ocr-output")
    assert res.status_code == 200
    payload = res.json()
    resolution = payload.get("column_mapping_resolution") or {}
    assert resolution.get("resolved_value") == (
        "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
        "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
    )


def test_get_ocr_output_prefers_applied_reparse_revision_over_stale_reject_candidate():
    order_service.clear_all()
    order = _create_seed_order_for_facility("msg-status-api-fac00004-applied-001", "FAC00004")
    template = config_service.get_facility_config("FAC00004").get("fax_template")
    fields = order_service._row_fields_from_template(template)
    header = order_service._sheet_header_from_template(fields, template)
    quantity_fields = [field for field in fields if field.startswith("qty.")]
    aux_fields = [field for field in fields if field.startswith("aux.")]
    assert quantity_fields
    assert len(aux_fields) >= 2
    diet_key, area_key = order_service._quantity_meta_from_field(quantity_fields[0])
    assert diet_key and area_key

    def _build_row(*, aux_total: str, qty_value: str) -> list[str]:
        row = [""] * len(fields)
        row[fields.index("date_mmdd")] = "02/15"
        row[fields.index("daypart")] = "朝"
        menu_field = "menu" if "menu" in fields else "menu_name"
        row[fields.index(menu_field)] = "Menu A"
        row[fields.index(aux_fields[0])] = "主"
        row[fields.index(aux_fields[1])] = aux_total
        row[fields.index(quantity_fields[0])] = qty_value
        return row

    stale_rows = [_build_row(aux_total="5", qty_value="5")]
    stale_digest = order_service._sheet_digest(
        fields=fields,
        header=header,
        rows_payload=stale_rows,
        row_ids=["draft-1"],
    )
    order_service._append_edited_ocr_revision(  # noqa: SLF001
        order_id=order["id"],
        ui_mode="sheet",
        fields=fields,
        header=header,
        rows_payload=stale_rows,
        row_ids=["draft-1"],
        before_digest=stale_digest,
        after_digest=stale_digest,
        revision_meta={
            "sheet_save_only": True,
            "sheet_save_mode": "draft_candidate",
            "review_state": "auto_apply_blocked",
            "draft_from_reparse_reject": True,
            "raw_output_override": order_service._build_revision_raw_output_override(  # noqa: SLF001
                fields=fields,
                header=header,
                rows=stale_rows,
                template=template,
                provider="gemini",
            ),
        },
    )

    applied_rows = [_build_row(aux_total="70", qty_value="70")]
    order_service._publish_applied_reparse_revision(  # noqa: SLF001
        order_id=order["id"],
        template=template,
        previous_sheet_context={
            "fields": fields,
            "header": header,
            "rows": stale_rows,
            "row_ids": ["draft-1"],
        },
        current_sheet_context={
            "fields": fields,
            "header": header,
            "rows": applied_rows,
            "row_ids": ["row-1"],
        },
        ocr_rows=applied_rows,
        provider="gemini",
        llm_assist=True,
        warning_reasons=[],
    )
    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        assert cache is not None
        payload = dict(cache.payload or {})
        edited = dict(payload.get("_edited_ocr") or {})
        stale_latest = {
            "revision_id": "OCRREV-stale-latest",
            "edited_at": "2026-02-15T00:00:00",
            "ui_mode": "sheet",
            "fields": fields,
            "header": header,
            "row_ids": ["draft-1"],
            "rows": stale_rows,
            "row_count": len(stale_rows),
            "before_digest": stale_digest,
            "after_digest": stale_digest,
            "changed": False,
            "sheet_save_only": True,
            "sheet_save_mode": "draft_candidate",
            "review_state": "auto_apply_blocked",
            "draft_from_reparse_reject": True,
            "markdown": order_service._build_markdown_table_string(header, stale_rows),  # noqa: SLF001
        }
        edited["latest"] = stale_latest
        edited["revisions"] = [stale_latest]
        payload["_edited_ocr"] = edited
        payload["edited_table"] = {
            "header": header,
            "rows": stale_rows,
            "row_ids": ["draft-1"],
            "edited_at": stale_latest["edited_at"],
            "revision_id": stale_latest["revision_id"],
        }
        cache.payload = payload

    client = TestClient(app)
    res = client.get(f"/orders/{order['id']}/ocr-output")
    assert res.status_code == 200
    payload = res.json()
    latest = ((payload.get("_edited_ocr") or {}).get("latest") or {})
    assert latest.get("sheet_save_mode") == "applied"
    assert latest.get("reparse_applied") is True
    assert latest.get("row_count") == 1
    assert latest.get("rows", [])[0][fields.index(aux_fields[1])] == "70"
    assert (payload.get("edited_table") or {}).get("rows", [])[0][fields.index(aux_fields[1])] == "70"
