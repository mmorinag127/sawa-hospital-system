import sys
import pathlib
import json
import pytest
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.main import app  # noqa: E402
from src.models.menu import MonthlyMenuEntry, MonthlyMenuItem  # noqa: E402
from src.models.order import Order, OrderMenuSnapshot  # noqa: E402
from src.models.order_ocr_cache import OrderOcrCache  # noqa: E402
from src.models.output import Bag  # noqa: E402
from src.services import config_service, menu_service, order_service, output_builder  # noqa: E402
from src.services.ocr_job_service import create_job, get_job, update_job  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402
from tests.helpers.owned_order import owned_registration  # noqa: E402
from tests.helpers.owned_output import owned_outputs  # noqa: E402
from tests.contract.test_orders_draft_review_api import _refuse
from tests.contract.test_orders_ocr_sheet_history_api import history_case  # noqa: F401
from tests.helpers.job_diagnostics_corruption import inject_stale_job_diagnostics as _inject_stale_job_diagnostics
from tests.helpers.job_diagnostics_corruption import inject_unowned_job_diagnostics


def _create_seed_order(owned_registration, message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint="2026-02@2026-02-15~2026-02-21",
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
    return owned_registration(message_id=payload.message_id, facility_id=payload.facility_hint,
        received_at=payload.received_at, week=payload.week_hint,
        effective_date=lines[0]["date"], lines=lines)


def _create_seed_order_for_facility(owned_registration, message_id: str, facility_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        facility_hint=facility_id,
        week_hint="2026-02@2026-02-15~2026-02-21",
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
    return owned_registration(message_id=payload.message_id, facility_id=payload.facility_hint,
        received_at=payload.received_at, week=payload.week_hint,
        effective_date=lines[0]["date"], lines=lines)


def test_owned_running_job_is_not_completed_by_unowned_cache_success(history_case):
    from datetime import date
    period = "2026-02@2026-02-15~2026-02-21"
    f = history_case(week=period, period=period, received=datetime(2026, 2, 15, 9),
        lines=[{"date": date(2026, 2, 15), "daypart": "朝", "menu_name": "Menu A",
                "diet_type": "regular", "area_id": "2F", "bag_type": "standard", "quantity_original": 5}],
        menu_entries=[{"id": "status-feb-15", "monthly_menu_id": "2026-02",
            "menu_date": date(2026, 2, 15), "daypart": "朝", "name": "Menu A",
            "slot_index": 0, "diet_type": "regular"}])
    f.queue()
    with f.execution():
        progress = order_service._update_reparse_job_progress(f.job,
            status="running", processing_stage="ocr_pipeline")
    assert progress is not None and progress["status"] == "running"
    cached_input = {"status": "success", "rows": [["dummy"]], "metrics": {"provider": "old"}}
    with session_scope() as session:
        cached = session.get(OrderOcrCache, f.id)
        if cached is None:
            session.add(OrderOcrCache(order_id=f.id, payload=cached_input))
        else:
            cached.payload = cached_input
    before_job = get_job(f.job)
    assert before_job["status"] == "running"
    payload = f.get()
    assert payload["ocr_status"] == "running"
    assert payload.get("ocr_error") in {None, ""}
    with f.readonly():
        response = f.client.get("/orders?include_ocr=true", headers=f.actor.headers)
    assert response.status_code == 200
    (row,) = [item for item in response.json()["orders"] if item["id"] == f.id]
    assert row["ocr_status"] == "running"
    assert get_job(f.job) == before_job
    with session_scope() as session:
        assert session.get(OrderOcrCache, f.id).payload == cached_input


def test_list_orders_include_ocr_does_not_refresh_workflow_state_on_read(monkeypatch, history_case):
    case = _owned_status_case(history_case)
    original = order_service.get_order_workflow_state

    def no_refresh(order_id, *, refresh=False):
        assert refresh is False, "GET /orders must not refresh workflow state"
        return original(order_id, refresh=False)

    monkeypatch.setattr(order_service, "get_order_workflow_state", no_refresh)
    with case.readonly():
        response = case.client.get("/orders?include_ocr=true", headers=case.actor.headers)
        assert response.status_code == 200, response.text
        assert any(row["id"] == case.id for row in response.json()["orders"])


@pytest.mark.parametrize("failure", ["sheet_date_anchor_drift", "main_ocr_failed:gemini"],
    ids=["native_anchor_failure_keeps_saved_five", "historical_provider_failure_detail_keeps_saved_five"])
def test_owned_terminal_failure_cannot_be_completed_by_stale_done_output(history_case, monkeypatch, tmp_path, failure):
    from copy import deepcopy
    from src.services import hakodate_cell_ocr_batch_service as batch

    case = _owned_status_case(history_case)
    selected_sheet = case.produce(cell_values="5")["sheet"]
    case.save(selected_sheet)
    selected = case.evidence
    calls = case.prepare_provider(cell_values="9")
    output_path = tmp_path / ("ocr_output_done_llm.json" if failure.startswith("main_ocr") else "ocr_output_done.json")
    stale_output = {"status": "done", "table_raw": "|a|b|"}
    output_path.write_text(json.dumps(stale_output), encoding="utf-8")
    case.protected += (output_path,)

    def fail(**_kwargs):
        raise RuntimeError(failure)

    monkeypatch.setattr(batch, "build_hakodate_best_method_for_manifest_item", fail)
    with case.execution():
        result, error = order_service.rerun_ocr_evidence_only(
            case.id, job_id=case.job, project_sheet=False, refresh_workflow=False)
    assert isinstance(result, dict)
    assert result["terminal_receipt"]["status"] == "failed"
    assert result["terminal_receipt"]["evidence_run_id"] is None
    assert error == "hakodate_live_rerun_failed"
    assert calls == []
    terminal = deepcopy(get_job(case.job)["metrics"]["rerun_terminal_receipt"])
    _inject_stale_job_diagnostics(case, output_reference=f"file://{output_path}")
    assert get_job(case.job)["metrics"]["rerun_terminal_receipt"] == terminal
    job = get_job(case.job)
    assert job["status"] == "failed"
    receipt = job["metrics"]["rerun_terminal_receipt"]
    assert receipt["status"] == "failed"
    assert receipt == result["terminal_receipt"]
    assert job["metrics"]["error_detail"] == failure
    assert job["metrics"]["request_mode"] == "ocr_rerun"
    payload = case.get()
    assert payload.get("ocr_status") == "failed"
    assert payload.get("ocr_error") == "hakodate_live_rerun_failed"
    assert get_job(case.job) == job
    assert json.loads(output_path.read_text()) == stale_output
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == selected
        assert current["rows"] == selected_sheet["rows"]
        assert case.get("workflow-v2")["state"] == "ocr_failed"


def test_get_order_does_not_mark_reparse_job_done_while_running(tmp_path, history_case):
    case = _owned_status_case(history_case)
    case.queue()
    output_path = tmp_path / "ocr_output_done_running.json"
    output_payload = {"status": "done", "table_raw": "|a|b|"}
    output_path.write_text(json.dumps(output_payload), encoding="utf-8")
    case.protected += (output_path,)
    with case.execution():
        progress = order_service._update_reparse_job_progress(case.job,
            status="running", processing_stage="ocr_pipeline", result_state="running",
            metrics_patch={"request_mode": "ocr_reparse"})
        assert progress is not None and progress["status"] == "running"
        updated, accepted = order_service.update_job_for_workflow_attempt(
            case.job, case.queued["ocr_job"]["workflow_attempt_id"], order_id=case.id,
            expected_workflow_revision=case.queued["workflow"]["revision"],
            output_reference=f"file://{output_path}")
    assert accepted is True and updated["output_reference"] == f"file://{output_path}"
    before = get_job(case.job)
    payload = case.get()
    assert payload.get("ocr_status") == "running"
    job = get_job(case.job)
    assert job is not None
    assert job.get("status") == "running"
    assert job == before
    assert job["metrics"].get("rerun_terminal_receipt") is None
    assert job["metrics"]["request_mode"] == "ocr_reparse"
    assert json.loads(output_path.read_text()) == output_payload
    with case.readonly():
        assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None


def _owned_status_case(history_case):
    from datetime import date
    period = "2026-02@2026-02-15~2026-02-21"
    case = history_case(week=period, period=period, received=datetime(2026, 2, 15, 9),
        lines=[{"date": date(2026, 2, 15), "daypart": "朝", "menu_name": "Menu A",
            "diet_type": "regular", "area_id": "2F", "bag_type": "standard", "quantity_original": 5}],
        menu_entries=[{"id": "status-awaiting-feb-15", "monthly_menu_id": "2026-02",
            "menu_date": date(2026, 2, 15), "daypart": "朝", "name": "Menu A",
            "slot_index": 0, "diet_type": "regular"}])
    return case


def _awaiting_output_status_case(history_case, *, request_mode, error_message="ocr_output_pending", extra_metrics=None):
    case = _owned_status_case(history_case)
    case.queue()
    with case.execution():
        progress = order_service._update_reparse_job_progress(case.job,
            status="awaiting_output", processing_stage="ocr_pipeline", result_state="awaiting_output",
            error_message=error_message, metrics_patch={"request_mode": request_mode,
                "order_id": case.id, "output_reference": "file://pending-output.json",
                **(extra_metrics or {})})
    assert progress is not None and progress["status"] == "awaiting_output"
    return case


def test_get_order_surfaces_awaiting_output_llm_reparse_as_active(history_case):
    case = _awaiting_output_status_case(history_case, request_mode="llm_reparse", extra_metrics={
            "request_mode": "llm_reparse",
            "requested_provider": "gemini",
            "llm_assist": True,
        })
    before_job = get_job(case.job)
    payload = case.get()
    assert payload.get("ocr_status") == "awaiting_output"
    workflow_payload = case.get("workflow-v2")
    reparse_state = workflow_payload["ocr_job"]
    assert reparse_state.get("status") == "awaiting_output"
    assert get_job(case.job)["metrics"]["request_mode"] == "llm_reparse"
    assert get_job(case.job) == before_job


def test_get_order_surfaces_awaiting_output_ocr_rerun_as_active(history_case):
    case = _awaiting_output_status_case(history_case, request_mode="ocr_rerun")
    before_job = get_job(case.job)
    payload = case.get()
    assert payload.get("ocr_status") == "awaiting_output"
    workflow_payload = case.get("workflow-v2")
    reparse_state = workflow_payload["ocr_job"]
    assert reparse_state.get("status") == "awaiting_output"
    assert get_job(case.job)["metrics"]["request_mode"] == "ocr_rerun"
    assert workflow_payload.get("state") == "ocr_running"
    assert get_job(case.job) == before_job


def test_get_order_prefers_live_metrics_error_for_active_ocr_rerun(history_case):
    case = _awaiting_output_status_case(history_case, request_mode="ocr_rerun",
        error_message="OCR pipeline request timeout: The read operation timed out",
        extra_metrics={"error": "ocr_output_pending"})
    before_job = get_job(case.job)
    payload = case.get()
    assert payload.get("ocr_status") == "awaiting_output"
    assert payload.get("ocr_error") == "ocr_output_pending"
    assert get_job(case.job) == before_job


@pytest.mark.parametrize("neighbor_age", ["newer", "older"])
def test_get_order_does_not_promote_unowned_completed_neighbor_output(history_case, tmp_path, neighbor_age):
    case = _awaiting_output_status_case(history_case, request_mode="ocr_rerun")
    cached_input = {'table_raw': '|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|5|', 'pages': [{'page_index': 1, 'ocr_overlay_uri': 'gs://bucket/ocr.png'}], 'tables': [{'page_index': 1, 'rows': [['02/15', '朝', 'Menu A', '5']]}]}
    completed_outputs = [('gs://bucket/output/OCR-order-rerun-final.json', {'status': 'done', 'stage': 'done', 'input_reference': 'gs://bucket/input/OCR-order-rerun.pdf', 'output_reference': 'gs://bucket/output/OCR-order-rerun-final.json', 'pages': [{'page_index': 1, 'ocr_overlay_uri': 'gs://bucket/ocr.png'}], 'table_raw': '|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|8|', 'tables': [{'page_index': 1, 'rows': [['02/15', '朝', 'Menu A', '8']]}], 'template_resolution': {'resolved_template_id': 'fax_layout_regular_soft_mixer_forbidden_v1', 'candidate_template_ids': ['fax_layout_regular_soft_mixer_forbidden_v1'], 'confidence': 0.99, 'blocked': False, 'blocked_reasons': []}, 'quantity_subgrid_passes': [{'page_index': 1, 'normalized_rows': [['02/15', '朝', 'Menu A', '8']]}], 'table_box': [0.1, 0.2, 0.9, 0.8], 'grid_column_edges': [0.1, 0.3, 0.6, 0.9], 'grid_row_edges': [0.2, 0.4, 0.8]})]
    order = {"id": case.id}
    if neighbor_age == "older":
        completed_outputs = [('gs://bucket/output/OCR-ORD596231b6_20260422_154441_050299.pdf.json', {'status': 'done', 'stage': 'done', 'input_reference': 'gs://bucket/input/OCR-order-rerun-old.pdf', 'output_reference': f"gs://bucket/output/OCR-{order['id']}_20260422_154441_050299.pdf.json", 'pages': [{'page_index': 1, 'ocr_overlay_uri': 'gs://bucket/ocr.png'}], 'table_raw': '|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|8|', 'tables': [{'page_index': 1, 'rows': [['02/15', '朝', 'Menu A', '8']]}], 'template_resolution': {'resolved_template_id': 'fax_layout_regular_soft_mixer_forbidden_v1', 'candidate_template_ids': ['fax_layout_regular_soft_mixer_forbidden_v1'], 'confidence': 0.99, 'blocked': False, 'blocked_reasons': []}, 'quantity_subgrid_passes': [{'page_index': 1, 'normalized_rows': [['02/15', '朝', 'Menu A', '8']]}], 'table_box': [0.1, 0.2, 0.9, 0.8], 'grid_column_edges': [0.1, 0.3, 0.6, 0.9], 'grid_row_edges': [0.2, 0.4, 0.8]})]
    (neighbor_reference, neighbor_payload), = completed_outputs
    assert neighbor_reference.startswith("gs://bucket/output/OCR-")
    assert neighbor_payload["tables"][0]["rows"] == [["02/15", "朝", "Menu A", "8"]]
    neighbor = tmp_path / neighbor_reference.rsplit("/", 1)[1]
    neighbor.write_text(json.dumps(neighbor_payload), encoding="utf-8")
    pending = tmp_path / ("pending-output.json" if neighbor_age == "newer" else
                          f"OCR-{case.id}_20260422_160712_447631.pdf.json")
    pending.write_text(json.dumps({"status": "running", "stage": "ocr",
                                  "output_reference": "file://pending-output.json"}), encoding="utf-8")
    case.protected += (neighbor, pending)
    with session_scope() as session:
        cached = session.get(OrderOcrCache, case.id)
        if cached is None:
            session.add(OrderOcrCache(order_id=case.id, payload=cached_input))
        else:
            cached.payload = cached_input
    with case.execution():
        if neighbor_age == "older":
            progress = order_service._update_reparse_job_progress(case.job, metrics_patch={
                "awaiting_output_since": "2026-04-22T16:07:12",
                "stage_updated_at": "2026-04-22T16:07:12"})
            assert progress is not None
        updated, accepted = order_service.update_job_for_workflow_attempt(
            case.job, case.queued["ocr_job"]["workflow_attempt_id"], order_id=case.id,
            expected_workflow_revision=case.queued["workflow"]["revision"],
            output_reference=str(pending))
    assert accepted is True and updated["output_reference"] == str(pending)
    before_job = get_job(case.job)
    payload = case.get()
    assert payload.get("ocr_status") == "awaiting_output"
    assert payload.get("ocr_error") == "ocr_output_pending"
    workflow_payload = case.get("workflow-v2")
    assert workflow_payload["ocr_job"]["status"] == "awaiting_output"
    assert workflow_payload["state"] == "ocr_running"
    assert get_job(case.job) == before_job
    assert get_job(case.job)["metrics"].get("rerun_terminal_receipt") is None
    assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None
    with session_scope() as session:
        assert session.get(OrderOcrCache, case.id).payload == cached_input
    assert payload["lines"][0]["quantity_original"] == 5


def test_owned_completed_rerun_publishes_eight_without_neighbor_discovery(history_case):
    case = _owned_status_case(history_case)
    source = case.produce(cell_values="8")
    assert source["selected_ocr_result_id"] == case.evidence
    assert str(source["sheet"]["rows"][0][3]) == "8"
    job = get_job(case.job)
    assert job["status"] == "done"
    receipt = job["metrics"]["rerun_terminal_receipt"]
    assert receipt["status"] == "done"
    assert receipt["evidence_run_id"] == case.evidence
    payload = case.get()
    assert payload["ocr_status"] == "done"
    assert payload.get("ocr_error") in {None, ""}
    assert (payload.get("ocr_metrics") or {}).get("error") in {None, ""}
    workflow_payload = case.get("workflow-v2")
    assert workflow_payload["ocr_job"]["status"] == "done"
    assert workflow_payload["state"] != "ocr_running"
    assert workflow_payload["ocr_job"]["error_message"] in {None, ""}
    case.save(source["sheet"])
    saved = case.get("workflow-v2/sheet")
    assert saved["sheet"]["rows"] == source["sheet"]["rows"]
    assert get_job(case.job) == job




def test_done_owned_rerun_keeps_stale_pending_diagnostics_readonly(history_case, tmp_path):
    case = _owned_status_case(history_case)
    source = case.produce(cell_values="8")
    order = {"id": case.id}
    real_receipt = get_job(case.job)["metrics"]["rerun_terminal_receipt"]
    assert real_receipt["status"] == "done"
    assert real_receipt["evidence_run_id"] == case.evidence
    output_path = tmp_path / "ocr_done_final.json"
    cached_input = {'status': 'done', 'stage': 'done', 'input_reference': 'file://dummy-order.pdf', 'output_reference': f'file://{output_path}', 'pages': [{'page_index': 1, 'ocr_overlay_uri': 'gs://bucket/ocr.png'}], 'table_raw': '|日付|区分|メニュー|常食|\n|---|---|---|---|\n|02/15|朝|Menu A|8|', 'tables': [{'page_index': 1, 'rows': [['02/15', '朝', 'Menu A', '8']]}]}
    output_path.write_text(json.dumps(cached_input, ensure_ascii=False), encoding="utf-8")
    case.protected += (output_path,)
    stale_metrics = {'request_mode': 'ocr_rerun', 'processing_stage': 'evidence_ready', 'result_state': 'evidence_ready', 'error': 'ocr_output_pending', 'trigger_error': 'OCR pipeline request timeout: The read operation timed out', 'awaiting_output_since': '2026-04-22T16:26:04.410692', 'next_recovery_at': '2026-04-22T16:36:04.410692', 'evidence_run_id': 'OEV-stale-done', 'new_evidence_available': True, 'order_id': order['id']}
    # Deliberate stale diagnostic metadata, never a fabricated evidence/receipt.
    _inject_stale_job_diagnostics(case, error_message="ocr_output_pending",
        metrics={**get_job(case.job)["metrics"], **stale_metrics})
    with session_scope() as session:
        cached = session.get(OrderOcrCache, case.id)
        if cached is None:
            session.add(OrderOcrCache(order_id=case.id, payload=cached_input))
        else:
            cached.payload = cached_input
    before_job = get_job(case.job)
    detail_payload = case.get()
    assert detail_payload["ocr_status"] == "done"
    assert detail_payload["ocr_error"] == "ocr_output_pending"
    assert detail_payload["ocr_metrics"]["error"] == "ocr_output_pending"
    assert detail_payload["ocr_metrics"]["trigger_error"] == stale_metrics["trigger_error"]
    workflow_payload = case.get("workflow-v2")
    assert workflow_payload["ocr_job"]["status"] == "done"
    assert workflow_payload["ocr_job"]["error_message"] == "ocr_output_pending"
    assert workflow_payload["state"] != "ocr_running"
    assert case.get("workflow-v2/sheet-source")["sheet"] == source["sheet"]
    retained_job = get_job(case.job)
    assert isinstance(retained_job, dict)
    assert retained_job == before_job
    assert retained_job["metrics"]["rerun_terminal_receipt"] == real_receipt
    assert retained_job["metrics"]["error"] == "ocr_output_pending"
    assert retained_job["metrics"]["trigger_error"] == stale_metrics["trigger_error"]
    assert retained_job["metrics"]["awaiting_output_since"] == stale_metrics["awaiting_output_since"]
    assert retained_job["metrics"]["next_recovery_at"] == stale_metrics["next_recovery_at"]
    with session_scope() as session:
        assert session.get(OrderOcrCache, case.id).payload == cached_input


def test_detail_reads_do_not_wait_for_missing_ocr_output_and_surface_rerun(monkeypatch, history_case):
    from src.services import hakodate_cell_ocr_batch_service as batch

    case = _owned_status_case(history_case)
    calls = case.prepare_provider(cell_values="5")

    def missing_external_output(**_kwargs):
        raise FileNotFoundError("ocr_output_missing")

    monkeypatch.setattr(batch, "build_hakodate_best_method_for_manifest_item", missing_external_output)
    with case.execution():
        result, error = order_service.rerun_ocr_evidence_only(case.id, job_id=case.job,
            project_sheet=False, refresh_workflow=False)
    assert result["terminal_receipt"]["status"] == "failed"
    assert result["terminal_receipt"]["evidence_run_id"] is None
    assert error == "hakodate_live_rerun_failed" and calls == []
    before = get_job(case.job)
    assert before["status"] == "failed"

    def no_recovery(*_args, **_kwargs):
        raise AssertionError("HTTP reads cannot wait for or discover missing OCR output")

    monkeypatch.setattr(order_service, "_load_pipeline_output_with_retry", no_recovery)
    monkeypatch.setattr(order_service, "_load_pipeline_output_once", no_recovery)
    with case.readonly():
        detail_response = case.client.get(f"/orders/{case.id}", headers=case.actor.headers)
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()
        assert detail["ocr_status"] not in {"done", "running", "awaiting_output"}
        assert detail.get("ocr_error")
        output = case.client.get(f"/orders/{case.id}/ocr-output", headers=case.actor.headers)
        assert output.status_code == 404, output.text
        assert output.json() == {"detail": "ocr output not found"}
        assert get_job(case.job) == before
        assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None


def test_get_ocr_output_returns_pending_when_active_ocr_rerun_output_is_pending(monkeypatch, history_case):
    case = _awaiting_output_status_case(history_case, request_mode="ocr_rerun",
        error_message="OCR pipeline request timeout: The read operation timed out")
    stale = {
        "status": "done",
        "table_raw": "|日付|区分|メニュー|常食|\\n|---|---|---|---|\\n|02/15|朝|Menu A|5|",
        "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/ocr-old.png"}],
        "output_reference": "gs://bucket/output/OCR-old.pdf.json",
    }
    with session_scope() as session:
        session.add(OrderOcrCache(order_id=case.id, payload=stale))
    output_ref = f"gs://bucket/output/OCR-{case.id}_20260422_162548_904035.pdf.json"
    with case.execution():
        updated, accepted = order_service.update_job_for_workflow_attempt(
            case.job, case.queued["ocr_job"]["workflow_attempt_id"], order_id=case.id,
            expected_workflow_revision=case.queued["workflow"]["revision"],
            output_reference=output_ref)
    assert accepted is True and updated["output_reference"] == output_ref

    def unexpected_discovery(*_args, **_kwargs):
        raise AssertionError("pending HTTP read must not discover or load pipeline output")

    monkeypatch.setattr(order_service, "_load_pipeline_output_once", unexpected_discovery)
    before = get_job(case.job)
    with case.readonly():
        response = case.client.get(f"/orders/{case.id}/ocr-output", headers=case.actor.headers)
        assert response.status_code == 202
        assert response.json() == {"pending": True}
        assert get_job(case.job) == before
        assert before["status"] == "awaiting_output"
        assert before["metrics"].get("rerun_terminal_receipt") is None
        assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == stale


def test_owned_running_detail_does_not_reconcile_unowned_first_pass_file(history_case, monkeypatch, tmp_path):
    case = _owned_status_case(history_case)
    case.queue()
    with case.execution():
        progress = order_service._update_reparse_job_progress(case.job,
            status="running", processing_stage="ocr_pipeline")
    assert progress is not None and progress["status"] == "running"
    owned_before = get_job(case.job)
    baseline = case.get()
    output_path = tmp_path / "unowned-first-pass.json"
    original = {"status": "done", "table_raw": "|02/15|朝|Menu A|",
        "pages": [{"tables": [{"rows": [["02/15", "朝", "Menu A"]]}]}]}
    output_path.write_text(json.dumps(original), encoding="utf-8")
    case.protected += (output_path,)
    job_id = f"OCR-{order_service.get_order_by_id(case.id)['message_id']}"
    _, fresh = create_job(job_id, input_reference=str(case.pdf), status="running")
    assert fresh is True
    inject_unowned_job_diagnostics(case.engine, job_id, output_reference=f"file://{output_path}",
        metrics={"request_mode": "ingest_first_pass"}, error_message=None)
    diagnostic = get_job(job_id)
    assert diagnostic["metrics"].get("workflow_attempt_id") is None
    assert diagnostic["metrics"].get("rerun_terminal_receipt") is None

    def unexpected_reconcile(*_args, **_kwargs):
        raise AssertionError("detail GET cannot reconcile an unowned first-pass file")

    monkeypatch.setattr(order_service, "reconcile_completed_ocr_job", unexpected_reconcile)
    with case.readonly():
        response = case.client.get(f"/orders/{case.id}", headers=case.actor.headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        for key in ("ocr_status", "ocr_error", "ocr_result_state", "ocr_processing_stage"):
            assert payload.get(key) == baseline.get(key)
        assert get_job(job_id) == diagnostic
        assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None
        assert get_job(case.job) == owned_before
        assert payload["ocr_status"] == "running"
    assert json.loads(output_path.read_text()) == original


def test_unowned_done_message_job_does_not_publish_evidence_on_detail(history_case, monkeypatch, tmp_path):
    case = _owned_status_case(history_case)
    baseline = case.get()
    output_path = tmp_path / "unowned-first-pass.json"
    original = {"status": "done", "table_raw": "|02/15|朝|Menu A|",
        "pages": [{"tables": [{"rows": [["02/15", "朝", "Menu A"]]}]}]}
    output_path.write_text(json.dumps(original), encoding="utf-8")
    case.protected += (output_path,)
    job_id = f"OCR-{order_service.get_order_by_id(case.id)['message_id']}"
    _, fresh = create_job(job_id, input_reference=baseline["document"], status="done")
    assert fresh is True
    inject_unowned_job_diagnostics(case.engine, job_id, output_reference=f"file://{output_path}",
        metrics={"request_mode": "ingest_first_pass"}, error_message=None)
    diagnostic = get_job(job_id)
    assert diagnostic["metrics"].get("workflow_attempt_id") is None
    assert diagnostic["metrics"].get("rerun_terminal_receipt") is None

    def unexpected_reconcile(*_args, **_kwargs):
        raise AssertionError("detail GET cannot reconcile an unowned first-pass file")

    monkeypatch.setattr(order_service, "reconcile_completed_ocr_job", unexpected_reconcile)
    with case.readonly():
        response = case.client.get(f"/orders/{case.id}", headers=case.actor.headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        for key in ("ocr_status", "ocr_error", "ocr_result_state", "ocr_processing_stage"):
            assert payload.get(key) == baseline.get(key)
        assert get_job(job_id) == diagnostic
        assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None
    assert json.loads(output_path.read_text()) == original


def test_get_order_does_not_reconcile_done_first_pass_output_when_persisted_evidence_exists(monkeypatch, tmp_path, history_case):
    case = _owned_status_case(history_case)
    source = case.produce(cell_values="5")
    case.save(source["sheet"])
    selected = order_service.get_ocr_evidence_run(case.evidence)
    output_path = tmp_path / "ocr_output_done_first_pass_already_persisted.json"
    original = {"status": "done", "table_raw": "|02/15|朝|Menu A|",
        "pages": [{"tables": [{"rows": [["02/15", "朝", "Menu A"]]}]}]}
    output_path.write_text(json.dumps(original), encoding="utf-8")
    case.protected += (output_path,)
    job_id = f"OCR-unowned-first-pass-{case.id}"
    created, fresh = create_job(job_id, input_reference=str(case.pdf),
        status="done", order_id=case.id)
    assert fresh is True
    inject_unowned_job_diagnostics(case.engine, job_id, output_reference=f"file://{output_path}",
        metrics={"request_mode": "ingest_first_pass"}, error_message=None)
    diagnostic = get_job(job_id)
    assert diagnostic["metrics"].get("workflow_attempt_id") is None
    assert diagnostic["metrics"].get("rerun_terminal_receipt") is None
    seen = []
    reconcile = order_service.reconcile_completed_ocr_job

    def observe(target_job_id):
        seen.append(target_job_id)
        return reconcile(target_job_id)

    monkeypatch.setattr(order_service, "reconcile_completed_ocr_job", observe)
    with case.readonly():
        response = case.client.get(f"/orders/{case.id}", headers=case.actor.headers)
        assert response.status_code == 200, response.text
        assert seen == []
        assert order_service.get_ocr_evidence_run(case.evidence) == selected
        assert order_service.get_current_sheet_context(case.id)["rows"] == source["sheet"]["rows"]
        assert get_job(job_id) == diagnostic
    assert json.loads(output_path.read_text()) == original


def test_unowned_done_order_job_does_not_publish_evidence_on_detail(history_case, monkeypatch, tmp_path):
    case = _owned_status_case(history_case)
    baseline = case.get()
    output_path = tmp_path / "unowned-first-pass.json"
    original = {"status": "done", "table_raw": "|02/15|朝|Menu A|",
        "pages": [{"tables": [{"rows": [["02/15", "朝", "Menu A"]]}]}]}
    output_path.write_text(json.dumps(original), encoding="utf-8")
    case.protected += (output_path,)
    job_id = f"OCR-{case.id}"
    _, fresh = create_job(job_id, input_reference=baseline["document"], status="done", order_id=case.id)
    assert fresh is True
    inject_unowned_job_diagnostics(case.engine, job_id, output_reference=f"file://{output_path}",
        metrics={"request_mode": "ingest_first_pass"}, error_message=None)
    diagnostic = get_job(job_id)
    assert diagnostic["metrics"].get("workflow_attempt_id") is None
    assert diagnostic["metrics"].get("rerun_terminal_receipt") is None

    def unexpected_reconcile(*_args, **_kwargs):
        raise AssertionError("detail GET cannot reconcile an unowned first-pass file")

    monkeypatch.setattr(order_service, "reconcile_completed_ocr_job", unexpected_reconcile)
    with case.readonly():
        response = case.client.get(f"/orders/{case.id}", headers=case.actor.headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        for key in ("ocr_status", "ocr_error", "ocr_result_state", "ocr_processing_stage"):
            assert payload.get(key) == baseline.get(key)
        assert get_job(job_id) == diagnostic
        assert order_service.get_latest_ocr_evidence_run(case.id, backfill_from_cache=False) is None
    assert json.loads(output_path.read_text()) == original


def test_list_orders_include_ocr_keeps_running_status(history_case):
    case = _owned_status_case(history_case)
    case.queue()
    with case.execution():
        progress = order_service._update_reparse_job_progress(case.job,
            status="running", processing_stage="ocr_pipeline")
    assert progress is not None and progress["status"] == "running"
    before = get_job(case.job)
    with case.readonly():
        response = case.client.get("/orders?include_ocr=true", headers=case.actor.headers)
        assert response.status_code == 200, response.text
        (row,) = [item for item in response.json()["orders"] if item["id"] == case.id]
        assert row["ocr_status"] == "running"
        assert get_job(case.job) == before


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


def test_list_orders_applies_limit_after_database_ordering():
    order_service.clear_all()
    with session_scope() as session:
        for index in range(3):
            session.add(
                Order(
                    id=f"ORDlimit{index}",
                    facility_code="FAC00001",
                    week_code="2026-02",
                    status="要確認",
                    document_uri=f"file://dummy-limit-{index}.pdf",
                    message_id=f"msg-status-api-limit-{index}",
                    received_at=datetime(2026, 2, 15, 9, index, 0),
                )
            )

    rows = order_service.list_orders(include_archived=False, limit=2)

    assert [row.get("id") for row in rows] == ["ORDlimit2", "ORDlimit1"]


def test_list_orders_page_uses_cursor_for_next_page():
    order_service.clear_all()
    with session_scope() as session:
        for index in range(5):
            session.add(
                Order(
                    id=f"ORDpage{index}",
                    facility_code="FAC00001",
                    week_code="2026-02",
                    status="要確認",
                    document_uri=f"file://dummy-page-{index}.pdf",
                    message_id=f"msg-status-api-page-{index}",
                    received_at=datetime(2026, 2, 15, 9, index, 0),
                )
            )

    first_page = order_service.list_orders_page(include_archived=False, limit=2)
    assert [row.get("id") for row in first_page["orders"]] == ["ORDpage4", "ORDpage3"]
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] == {
        "received_at": datetime(2026, 2, 15, 9, 3, 0).isoformat(),
        "id": "ORDpage3",
    }

    second_page = order_service.list_orders_page(
        include_archived=False,
        limit=2,
        before_received_at=first_page["next_cursor"]["received_at"],
        before_id=first_page["next_cursor"]["id"],
    )
    assert [row.get("id") for row in second_page["orders"]] == ["ORDpage2", "ORDpage1"]


def test_get_bags_rebuilds_stale_materialized_payload(owned_outputs):
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-status-api-bags-001",
        pdf_uri="file://dummy-bags.pdf",
        received_at=datetime(2026, 4, 18, 9, 0, 0),
        facility_hint="FAC00003",
        week_hint="2026-04@2026-04-18~2026-04-18",
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
    order = owned_outputs.create_order_from_ingest(payload, physical_slot=0, lines=lines,
        recipe={"qty_per_serving": 40, "unit_type": "g", "category": "主菜"})
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


def test_build_order_lines_for_outputs_uses_owned_period_not_stale_display_metadata(owned_outputs):
    payload = IngestEmailPayload(message_id="owned-output-week", pdf_uri="component:authored",
        received_at=datetime(2026, 4, 18), facility_hint="FAC00003",
        week_hint="2026-04@2026-04-12~2026-04-18")
    order = owned_outputs.create_order_from_ingest(payload, physical_slot=0, lines=[{
        "date": "2026-04-18", "daypart": "昼", "menu_name": "筑前煮", "diet_type": "regular",
        "area_id": "2F", "bag_type": "standard", "quantity_original": 9}],
        recipe={"qty_per_serving": 40, "unit_type": "g", "category": "副菜"})
    displayed = {**order, "stored_week_value": "2026-04", "week_value": "2026-05"}
    lines = output_builder.build_order_lines_for_outputs(displayed)
    assert [line.get("menu_name") for line in lines] == ["筑前煮"]
    assert [str(line.get("date")) for line in lines] == ["2026-04-18"]


def test_get_daily_bags_groups_rows_by_menu_diet_and_bag_type(owned_outputs):
    order_service.clear_all()
    month_id = "2026-02"
    menu_csv = "menu\n筑前煮\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "筑前煮")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 50, "unit_type": "g", "daypart": "昼", "category": "主A", "diet_type": "regular"},
    )
    payload = IngestEmailPayload(
        message_id="msg-status-api-daily-bags-001",
        pdf_uri="file://dummy-daily-bags.pdf",
        received_at=datetime(2026, 2, 18, 9, 0, 0),
        facility_hint="FAC00003",
        week_hint="2026-02@2026-02-18~2026-02-18",
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
    owned_outputs.create_order_from_ingest(payload, physical_slot=0, lines=lines,
        daily_label_comparable_diet_types=["mixer"])

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


def test_get_daily_bags_uses_current_lines_without_materialized_rebuild(monkeypatch, owned_outputs):
    order_service.clear_all()
    month_id = "2026-02"
    menu_csv = "menu\n筑前煮\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "筑前煮")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 50, "unit_type": "g", "daypart": "昼", "category": "主A", "diet_type": "regular"},
    )
    payload = IngestEmailPayload(
        message_id="msg-status-api-daily-bags-stale-001",
        pdf_uri="file://dummy-daily-bags-stale.pdf",
        received_at=datetime(2026, 2, 18, 9, 0, 0),
        facility_hint="FAC00003",
        week_hint="2026-02@2026-02-18~2026-02-18",
    )
    order = owned_outputs.create_order_from_ingest(
        payload,
        physical_slot=0, lines=[
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


def test_daily_bags_prefers_line_daypart_over_snapshot_daypart(owned_outputs):
    order_service.clear_all()
    month_id = "2026-03"
    menu_csv = "menu\n豆腐の煮物\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "豆腐の煮物")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 70, "unit_type": "g", "daypart": "昼食", "category": "主菜", "diet_type": "regular"},
    )
    payload = IngestEmailPayload(
        message_id="msg-status-api-daily-bags-daypart-001",
        pdf_uri="file://dummy-daily-bags-daypart.pdf",
        received_at=datetime(2026, 3, 24, 9, 0, 0),
        facility_hint="FAC00003",
        week_hint="2026-03@2026-03-24~2026-03-24",
    )
    order = owned_outputs.create_order_from_ingest(
        payload,
        physical_slot=0, lines=[
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


def test_daily_bags_prefers_monthly_menu_entry_category_over_menu_master(owned_outputs):
    order_service.clear_all()
    month_id = "2099-03"
    menu_name = f"豆腐の煮物-{uuid4().hex[:6]}"
    menu_csv = f"menu\n{menu_name}\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, menu_name)
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 70, "unit_type": "g", "daypart": "昼食", "category": "主菜", "diet_type": "regular"},
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
                diet_type="regular",
                facility_override=None,
            )
        )
        session.add(MonthlyMenuItem(
            id=f"MI-daily-side-{uuid4().hex}", monthly_menu_id=month_id,
            menu_master_id=menu_item["menu_master_id"], name=menu_name,
            daypart="昼食", diet_type="regular", facility_override="FAC00003",
            category="副菜", qty_per_serving=70, unit_type="g",
            master_resolution_mode="manual",
        ))
    order = owned_outputs.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-entry-001",
            pdf_uri="file://dummy-daily-bags-entry.pdf",
            received_at=datetime(2099, 3, 24, 9, 0, 0),
            facility_hint="FAC00003",
            week_hint="2099-03@2099-03-24~2099-03-24",
        ),
        recipe={"qty_per_serving": 70, "unit_type": "g", "category": "主菜"},
        physical_category="副菜", physical_slot=2, prepared_menu_sources=True, lines=[
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
    owned_outputs.assert_confirmed(order["id"])

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2099-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == menu_name)
    assert group["menu_category"] == "副菜1"
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["calculation_basis_label"] == "70g/人"
    assert regular["total_amount_label"] == "630g"


def test_daily_bags_uses_explicit_meal_recipe_and_physical_category_over_master_defaults(owned_outputs):
    order_service.clear_all()
    month_id = "2099-03"
    menu_name = f"豆腐の煮物-{uuid4().hex[:6]}"
    menu_csv = f"menu\n{menu_name}\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, menu_name)
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 70, "unit_type": "g", "daypart": "朝食", "category": "主菜", "diet_type": "regular"},
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
                diet_type="regular",
            )
        )
        session.add(MonthlyMenuItem(id=f"MI-lunch-{uuid4().hex}", monthly_menu_id=month_id,
            menu_master_id=menu_item["menu_master_id"], name=menu_name, daypart="昼",
            diet_type="regular", facility_override="FAC00003", category="副菜",
            qty_per_serving=70, unit_type="g", master_resolution_mode="manual"))

    owned_outputs.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-entry-001",
            pdf_uri="file://dummy-daily-bags-entry.pdf",
            received_at=datetime(2099, 3, 24, 9, 0, 0),
            facility_hint="FAC00003",
            week_hint="2099-03@2099-03-24~2099-03-24",
        ),
        recipe={"qty_per_serving": 70, "unit_type": "g", "category": "主菜"},
        physical_category="副菜", physical_slot=1, prepared_menu_sources=True, lines=[
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
    assert group["menu_category"] == "副菜1"
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["calculation_basis_label"] == "70g/人"
    assert regular["total_amount_label"] == "630g"


def test_daily_bags_prefers_current_menu_qty_over_stale_snapshot_qty(owned_outputs):
    order_service.clear_all()
    month_id = "2026-03"
    menu_name = f"南瓜サラダ-{uuid4().hex[:6]}"
    menu_csv = f"menu\n{menu_name}\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, menu_name)
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 50, "unit_type": "g", "daypart": "夕食", "category": "副菜", "diet_type": "regular"},
    )
    order = owned_outputs.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-snapshot-qty-001",
            pdf_uri="file://dummy-daily-bags-snapshot-qty.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00003",
            week_hint="2026-03@2026-03-24~2026-03-24",
        ),
        physical_slot=0, lines=[
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
    owned_outputs.assert_confirmed(order["id"])
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


def test_daily_bags_treats_staff_as_regular(owned_outputs):
    order_service.clear_all()
    month_id = "2026-03"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜", "diet_type": "regular"},
    )
    order = owned_outputs.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-staff-001",
            pdf_uri="file://dummy-daily-bags-staff.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00014",
            week_hint="2026-03@2026-03-24~2026-03-24",
        ),
        physical_slot=0, daily_label_comparable_diet_types=["staff"], lines=[
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
    owned_outputs.assert_confirmed(order["id"])

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == "ホイコーロー")
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["total_quantity"] == 12.0
    assert regular["calculation_basis_label"] == "100g/人"
    assert not any(item.get("diet_type") == "staff" for item in group["diet_groups"])


def test_daily_bags_treats_daycare_as_regular(owned_outputs):
    order_service.clear_all()
    month_id = "2026-03"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜", "diet_type": "regular"},
    )
    order = owned_outputs.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-daycare-001",
            pdf_uri="file://dummy-daily-bags-daycare.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00004",
            week_hint="2026-03@2026-03-24~2026-03-24",
        ),
        physical_slot=0, daily_label_comparable_diet_types=["daycare"], lines=[
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
    owned_outputs.assert_confirmed(order["id"])

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == "ホイコーロー")
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["total_quantity"] == 13.0
    assert regular["calculation_basis_label"] == "100g/人"
    assert not any(item.get("diet_type") == "daycare" for item in group["diet_groups"])


def test_daily_bags_treats_regular_bag_and_1600_as_regular(owned_outputs):
    order_service.clear_all()
    month_id = "2026-03"
    menu_csv = "menu\nホイコーロー\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "ホイコーロー")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 100, "unit_type": "g", "daypart": "昼食", "category": "主菜", "diet_type": "regular"},
    )
    order = owned_outputs.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-regular-bucket-001",
            pdf_uri="file://dummy-daily-bags-regular-bucket.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00004",
            week_hint="2026-03@2026-03-24~2026-03-24",
        ),
        physical_slot=0, daily_label_comparable_diet_types=[], lines=[
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
    owned_outputs.assert_confirmed(order["id"])

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == "ホイコーロー")
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["total_quantity"] == 12.0
    assert regular["calculation_basis_label"] == "100g/人"
    assert not any(item.get("diet_type") in {"regular_bag", "regular_1600kcal"} for item in group["diet_groups"])


def test_daily_bags_splits_garnish_and_canonicalizes_cut_units(owned_outputs):
    order_service.clear_all()
    month_id = "2026-03"
    menu_csv = "menu\n白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 1, "unit_type": "切", "daypart": "夕食", "category": "主菜", "diet_type": "regular"},
    )
    order = owned_outputs.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-garnish-cut-001",
            pdf_uri="file://dummy-daily-bags-garnish-cut.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00001",
            week_hint="2026-03@2026-03-24~2026-03-24",
        ),
        accessory_masters={"ﾌﾞﾛｯｺﾘｰ": {"qty_per_serving": 30, "unit_type": "g"}},
        physical_slot=0, lines=[
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
    owned_outputs.assert_confirmed(order["id"])

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


def test_build_order_lines_for_outputs_matches_owned_split_main_and_explicit_garnish_recipe(owned_outputs):
    payload = IngestEmailPayload(message_id="owned-garnish-alias", pdf_uri="component:authored",
        received_at=datetime(2026, 3, 24), facility_hint="FAC00001",
        week_hint="2026-03@2026-03-24~2026-03-24")
    order = owned_outputs.create_order_from_ingest(payload, physical_slot=0, lines=[{
        "date": "2026-03-24", "daypart": "夕", "menu_name": "白身魚のフライ 添)ﾌﾞﾛｯｺﾘｰ",
        "diet_type": "regular", "area_id": "X", "bag_type": "standard", "quantity_original": 4}],
        recipe={"qty_per_serving": 1, "unit_type": "枚", "category": "主菜"},
        accessory_masters={"ﾌﾞﾛｯｺﾘｰ": {"qty_per_serving": 30, "unit_type": "g"}})
    lines = output_builder.build_order_lines_for_outputs(order)
    main_line = next(item for item in lines if item.get("menu_name") == "白身魚のフライ")
    garnish_line = next(item for item in lines if item.get("menu_name") == "ﾌﾞﾛｯｺﾘｰ")
    assert main_line["menu_category"] == "主菜"
    assert main_line["menu_qty_per_serving"] == 1
    assert main_line["menu_unit_type"] == "cut"
    assert main_line["quantity_original"] == 4
    assert garnish_line["menu_category"] == "添え"
    assert garnish_line["menu_qty_per_serving"] == 30
    assert garnish_line["menu_unit_type"] == "g"


def test_daily_bags_excludes_forbidden_but_keeps_saved_quantities(owned_outputs):
    order_service.clear_all()
    month_id = "2026-03"
    menu_csv = "menu\n豆腐の煮物\n".encode("utf-8")
    menu_service.create_menu(month_id, menu_csv, "menu.csv")
    menu_item = menu_service.create_item_stub(month_id, "豆腐の煮物")
    menu_service.update_item(
        month_id,
        menu_item["id"],
        {"qty_per_serving": 40, "unit_type": "g", "daypart": "昼食", "category": "副菜", "diet_type": "regular"},
    )
    order = owned_outputs.create_order_from_ingest(
        IngestEmailPayload(
            message_id="msg-status-api-daily-bags-forbidden-bucket-001",
            pdf_uri="file://dummy-daily-bags-forbidden-bucket.pdf",
            received_at=datetime(2026, 3, 24, 9, 0, 0),
            facility_hint="FAC00014",
            week_hint="2026-03@2026-03-24~2026-03-24",
        ),
        physical_slot=0, lines=[
            {
                "date": "2026-03-24", "daypart": "昼", "menu_name": "豆腐の煮物",
                "diet_type": "regular", "area_id": "X", "bag_type": "standard",
                "quantity_original": 5,
            },
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
    owned_outputs.assert_confirmed(order["id"])

    client = TestClient(app)
    res = client.get("/orders/daily-bags?date=2026-03-24")

    assert res.status_code == 200
    group = next(item for item in res.json()["groups"] if item.get("menu_name") == "豆腐の煮物")
    regular = next(item for item in group["diet_groups"] if item.get("diet_type") == "regular")
    assert regular["total_quantity"] == 5.0
    assert regular["calculation_basis_label"] == "40g/人"
    assert not any(
        item.get("diet_type") in {"no_meat", "no_fish", "forbidden_other", "no_fried"}
        for item in group["diet_groups"]
    )
    sheet, error = order_service.get_ocr_sheet(order["id"])
    assert error is None
    for diet, quantity in (("no_meat", 2), ("no_fish", 1), ("forbidden_other", 3), ("no_fried", 4)):
        raw = sheet["rows"][0][sheet["fields"].index(f"qty.{diet}_x")]
        assert raw == quantity
        assert type(raw) is int


def test_download_document_does_not_fetch_signed_substitute_when_own_read_fails(monkeypatch):
    client = TestClient(app)
    order_id = "ORD-document-fallback-001"
    pdf_bytes = b"%PDF-1.4 fallback"

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda _order_id, *, allow_unresolved_menu_amount=False: {"id": order_id, "document": "gs://bucket/path/document.pdf"},
    )

    from hashlib import sha256
    from src.services import storage_service, workflow_attempt_guard_service
    monkeypatch.setattr(workflow_attempt_guard_service, "capture_selected_pdf_receipt", lambda *args: {
        "storage_uri": "gs://bucket/path/document.pdf", "generation": None,
        "sha256": sha256(pdf_bytes).hexdigest(),
    })
    def _raise(_uri: str, **kwargs):
        raise RuntimeError("direct read failed")

    monkeypatch.setattr(storage_service, "load_bytes_from_uri", _raise)
    monkeypatch.setattr(
        orders_api.order_service,
        "_signed_url_from_uri",
        lambda _uri: (_ for _ in ()).throw(AssertionError("signed source substitution")),
    )

    res = client.get(f"/orders/{order_id}/document")

    assert res.status_code == 409
    assert res.json()["detail"] == "selected_pdf_verification_failed"
    assert res.content != pdf_bytes


def test_download_document_never_borrows_archived_ocr_input_when_selected_source_missing(monkeypatch):
    client = TestClient(app)
    order_id = "ORD-document-fallback-ocr-001"
    archived_pdf = b"%PDF-archived-original"

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda _order_id, *, allow_unresolved_menu_amount=False: {"id": order_id, "document": "gs://bucket/path/document.pdf"},
    )

    from hashlib import sha256
    from src.services import storage_service, workflow_attempt_guard_service
    monkeypatch.setattr(workflow_attempt_guard_service, "capture_selected_pdf_receipt", lambda *args: {
        "storage_uri": "gs://bucket/path/document.pdf", "generation": None,
        "sha256": sha256(b"own-selected-pdf").hexdigest(),
    })
    def _raise(_uri: str, **kwargs):
        raise FileNotFoundError("missing object")

    monkeypatch.setattr(storage_service, "load_bytes_from_uri", _raise)
    monkeypatch.setattr(
        orders_api.order_service,
        "_signed_url_from_uri",
        lambda _uri: "https://signed.example/document.pdf",
    )

    def _get_ocr_output(_order_id, *, persist_cache):
        raise AssertionError("latest/archive evidence substitution")

    monkeypatch.setattr(orders_api.order_service, "get_ocr_output", _get_ocr_output)

    res = client.get(f"/orders/{order_id}/document")

    assert res.status_code == 409
    assert res.json()["detail"] == "selected_pdf_verification_failed"
    assert res.content != archived_pdf


def test_download_document_requires_authoritative_proof_before_source_read(monkeypatch):
    client = TestClient(app)
    order_id = "ORD-document-missing-001"

    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_by_id",
        lambda _order_id, *, allow_unresolved_menu_amount=False: {"id": order_id, "document": "gs://bucket/path/document.pdf"},
    )

    from src.services import storage_service, workflow_attempt_guard_service
    def missing_proof(*args):
        raise ValueError("selected_pdf_digest_required")
    monkeypatch.setattr(workflow_attempt_guard_service, "capture_selected_pdf_receipt", missing_proof)
    monkeypatch.setattr(storage_service, "load_bytes_from_uri", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read before digest proof")))
    monkeypatch.setattr(
        orders_api.order_service,
        "_signed_url_from_uri",
        lambda _uri: "https://signed.example/document.pdf",
    )
    def _get_ocr_output(_order_id, *, persist_cache):
        raise AssertionError("old evidence must not fill proof")

    monkeypatch.setattr(orders_api.order_service, "get_ocr_output", _get_ocr_output)

    res = client.get(f"/orders/{order_id}/document")

    assert res.status_code == 409
    assert res.json()["detail"] == "selected_pdf_digest_required"


def test_daily_bag_audit_gemini_retries_truncated_json(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    calls: list[dict] = []

    class _Response:
        def __init__(self, payload: dict):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    def _gemini_payload(text: str, finish_reason: str) -> dict:
        return {
            "candidates": [
                {
                    "finishReason": finish_reason,
                    "content": {"parts": [{"text": text}]},
                }
            ]
        }

    def _fake_urlopen(request, timeout):  # noqa: ARG001
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        if len(calls) == 1:
            return _Response(_gemini_payload('{"items":[{"finding_id":"daily-audit-1","summary":"', "MAX_TOKENS"))
        return _Response(
            _gemini_payload(
                json.dumps(
                    {
                        "items": [
                            {
                                "finding_id": "daily-audit-1",
                                "priority": "high",
                                "summary": "数量確認",
                                "operator_action": "元注文を確認",
                            }
                        ],
                        "notes": "ok",
                    },
                    ensure_ascii=False,
                ),
                "STOP",
            )
        )

    monkeypatch.setattr(order_service.urllib.request, "urlopen", _fake_urlopen)

    result = order_service._daily_audit_run_gemini(  # noqa: SLF001
        findings=[{"finding_id": "daily-audit-1", "severity": "high", "message": "x"}],
        target_date=datetime(2026, 5, 16).date(),
    )

    assert result["status"] == "completed"
    assert result["items"][0]["summary"] == "数量確認"
    assert len(calls) == 2
    assert calls[1]["generationConfig"]["maxOutputTokens"] > calls[0]["generationConfig"]["maxOutputTokens"]
    assert calls[0]["generationConfig"]["maxOutputTokens"] == 1024
    assert "responseSchema" not in calls[0]["generationConfig"]
    request_text = calls[0]["contents"][0]["parts"][0]["text"]
    assert '"findings":[{"id":"daily-audit-1"' in request_text
    assert "source_refs" not in request_text
    assert "reference_values" not in request_text


def test_daily_bag_audit_gemini_default_timeout_allows_long_live_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.delenv("DAILY_OUTPUT_AUDIT_GEMINI_TIMEOUT_SECONDS", raising=False)
    observed: dict[str, float] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {"items": [], "notes": "ok"},
                                            ensure_ascii=False,
                                        )
                                    }
                                ]
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")

    def _fake_urlopen(request, timeout):  # noqa: ARG001
        observed["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(order_service.urllib.request, "urlopen", _fake_urlopen)

    result = order_service._daily_audit_run_gemini(  # noqa: SLF001
        findings=[{"finding_id": "daily-audit-1", "severity": "high", "message": "x"}],
        target_date=datetime(2026, 5, 16).date(),
    )

    assert result["status"] == "completed"
    assert observed["timeout"] == 120.0


def test_daily_bag_audit_gemini_truncated_twice_falls_back_to_rule_summary(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "candidates": [
                        {
                            "finishReason": "MAX_TOKENS",
                            "content": {"parts": [{"text": '{"items":[{"finding_id":"daily-audit-1","summary":"'}]},
                        }
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr(order_service.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    result = order_service._daily_audit_run_gemini(  # noqa: SLF001
        findings=[
            {
                "finding_id": "daily-audit-1",
                "severity": "high",
                "message": "朝 / じゃが芋のコンソメ煮 / regular が 332。",
                "suggested_action": "元注文を確認してください。",
            }
        ],
        target_date=datetime(2026, 5, 16).date(),
    )

    assert result["status"] == "completed"
    assert result["fallback_reason"] == "gemini_output_truncated"
    assert result["items"] == [
        {
            "finding_id": "daily-audit-1",
            "priority": "high",
            "summary": "朝 / じゃが芋のコンソメ煮 / regular が 332。",
            "operator_action": "元注文を確認してください。",
        }
    ]
    assert len(result["attempts"]) == 2


def test_daily_bag_audit_returns_blocker_when_summary_cannot_be_built(monkeypatch):
    def _blocked_summary(*_args, **_kwargs):
        raise ValueError("draft_newer_than_lines requires materialized draft lines: draft_lines_empty")

    monkeypatch.setattr(order_service, "get_daily_bag_summary", _blocked_summary)

    result = order_service.get_daily_bag_audit(
        datetime(2026, 5, 16).date(),
        use_ai=True,
    )

    assert result["rule_based"]["status"] == "blocked"
    assert result["rule_based"]["error"] == "daily_bag_summary_blocked"
    assert result["ai"]["status"] == "blocked"
    assert result["ai"]["items"] == []




def test_owned_reparse_callback_propagates_crash_to_delivery_terminal_owner(history_case, monkeypatch):
    f = history_case()
    f.queue()
    def fail(**_kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(order_service, "reparse_order", lambda *_args, **kwargs: fail(**kwargs))
    with f.execution(), pytest.raises(orders_api.OcrBackgroundExecutionFailure, match="boom"):
        orders_api._run_reparse_background(f.id, None, expected_revision=f.queued["workflow"]["revision"],
            workflow_attempt_id=f.queued["ocr_job"]["workflow_attempt_id"], ocr_job_id=f.job)
    job = get_job(f.job)
    assert job["status"] == "running"
    assert "workflow_terminal_receipt" not in (job.get("metrics") or {})



def test_owned_reparse_callback_does_not_forge_terminal_receipt_on_system_exit(history_case, monkeypatch):
    f = history_case()
    f.queue()
    def fail(*_args, **_kwargs):
        raise SystemExit(1)
    monkeypatch.setattr(order_service, "reparse_order", fail)
    with f.execution(), pytest.raises(SystemExit) as raised:
        orders_api._run_reparse_background(f.id, None, expected_revision=f.queued["workflow"]["revision"],
            workflow_attempt_id=f.queued["ocr_job"]["workflow_attempt_id"], ocr_job_id=f.job)
    assert raised.value.code == 1
    assert get_job(f.job)["status"] == "running"



def test_owned_rerun_callback_propagates_system_exit_without_claiming_success(history_case, monkeypatch):
    f = history_case()
    f.queue()
    def fail(*_args, **_kwargs):
        raise SystemExit(1)
    monkeypatch.setattr(order_service, "rerun_ocr_evidence_only", fail)
    with f.execution(), pytest.raises(SystemExit) as raised:
        orders_api._run_ocr_rerun_background(f.id, f.job,
            expected_revision=f.queued["workflow"]["revision"],
            workflow_attempt_id=f.queued["ocr_job"]["workflow_attempt_id"])
    assert raised.value.code == 1
    assert get_job(f.job)["status"] == "running"



def test_owned_rerun_callback_acquires_real_slot_before_external_recognition(history_case, monkeypatch):
    from src.services import hakodate_cell_ocr_batch_service as batch
    f = history_case()
    calls = f.prepare_provider()
    provider = batch.build_hakodate_best_method_for_manifest_item
    stages = []
    def observe(**kwargs):
        metrics = get_job(f.job)["metrics"]
        stages.append(metrics["processing_stage"])
        assert metrics["ocr_execution_slot"]["backend"] == "local_semaphore"
        return provider(**kwargs)
    monkeypatch.setattr(batch, "build_hakodate_best_method_for_manifest_item", observe)
    with f.execution():
        result = orders_api._run_ocr_rerun_background(f.id, f.job,
            expected_revision=f.queued["workflow"]["revision"],
            workflow_attempt_id=f.queued["ocr_job"]["workflow_attempt_id"])
    assert stages == ["ocr_pipeline"]
    assert len(calls) == 1
    assert result["terminal_receipt"]["job_id"] == f.job
    assert result["terminal_receipt"]["order_id"] == f.id
    assert result["terminal_receipt"]["status"] == "done"



def test_owned_rerun_real_slot_timeout_persists_failed_owned_receipt(history_case, monkeypatch):
    from src.services.ocr_execution_lock_service import acquire_ocr_execution_slot
    f = history_case()
    f.queue()
    monkeypatch.setenv("WORKFLOW_V2_OCR_MAX_CONCURRENT", "1")
    monkeypatch.setenv("WORKFLOW_V2_OCR_SLOT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("WORKFLOW_V2_OCR_SLOT_POLL_SECONDS", "0.5")
    with acquire_ocr_execution_slot(order_id=f.id, job_id="owned-slot-holder"):
        with f.execution():
            result = orders_api._run_ocr_rerun_background(f.id, f.job,
                expected_revision=f.queued["workflow"]["revision"],
                workflow_attempt_id=f.queued["ocr_job"]["workflow_attempt_id"])
    receipt = result["terminal_receipt"]
    assert receipt["status"] == "failed"
    assert receipt["job_id"] == f.job and receipt["order_id"] == f.id
    job = get_job(f.job)
    assert job["status"] == "failed"
    assert job["error_message"] == "ocr_execution_slot_timeout"
    assert job["metrics"]["processing_stage"] == "ocr_execution_slot_timeout"



def _owned_debug_case(history_case, *, metrics, cached_debug):
    from copy import deepcopy

    case = _owned_status_case(history_case)
    sheet = case.produce(cell_values="5")["sheet"]
    case.save(sheet)
    selected = case.evidence
    terminal = deepcopy(get_job(case.job)["metrics"]["rerun_terminal_receipt"])
    _inject_stale_job_diagnostics(case, metrics={**get_job(case.job)["metrics"], **deepcopy(metrics)})
    assert get_job(case.job)["status"] == "done"
    assert get_job(case.job)["metrics"]["rerun_terminal_receipt"] == terminal
    with session_scope() as session:
        cached = session.get(OrderOcrCache, case.id)
        if cached is None:
            session.add(OrderOcrCache(order_id=case.id, payload={"_reparse_debug": deepcopy(cached_debug)}))
        else:
            cached.payload = {**(cached.payload or {}), "_reparse_debug": deepcopy(cached_debug)}
    return case, sheet, selected, get_job(case.job)


def _read_owned_debug(case, sheet, selected, job):
    evidence = order_service.get_ocr_evidence_run(selected)
    with case.readonly():
        response = case.client.get(f"/orders/{case.id}/ocr-output", headers=case.actor.headers)
        assert response.status_code == 200, response.text
        assert get_job(case.job) == job
        assert order_service.get_ocr_evidence_run(selected) == evidence
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == selected
        assert current["rows"] == sheet["rows"]
    return response.json()


def test_unselected_cache_debug_cannot_replace_owned_ocr_output(history_case):
    historical = {"provider": "gemini", "requested_provider": "gemini", "row_count": 55}
    # Historical formatting remains a pure diagnostic component, not OCR provenance.
    parsed = {"status": "done", "_reparse_debug": historical}
    retained, changed = order_service._sync_reparse_debug_from_job_metrics(parsed, {})
    assert changed is False
    assert retained["status"] == "done"
    assert retained["_reparse_debug"]["provider"] == "gemini"
    assert retained["_reparse_debug"]["row_count"] == 55
    case, sheet, selected, job = _owned_debug_case(history_case, metrics={}, cached_debug=historical)
    payload = _read_owned_debug(case, sheet, selected, job)
    assert (payload.get("_reparse_debug") or {}).get("row_count") != 55


def test_owned_output_debug_uses_explicit_job_diagnostics_without_changing_source(history_case):
    metrics = {"provider": "gemini", "requested_provider": "gemini", "row_count": 54,
        "line_count": 216, "before_count": 78, "after_count": 216,
        "changed": True, "llm_assist": True}
    stale = {"provider": "gemini", "requested_provider": "gemini", "row_count": 34,
        "line_count": 78, "before_count": 78, "after_count": 78, "changed": False}
    case, sheet, selected, job = _owned_debug_case(history_case, metrics=metrics, cached_debug=stale)
    payload = _read_owned_debug(case, sheet, selected, job)
    debug = payload.get("_reparse_debug") or {}
    assert debug.get("provider") == "gemini"
    assert debug.get("requested_provider") == "gemini"
    assert debug.get("row_count") == 54
    assert debug.get("line_count") == 216
    assert debug.get("after_count") == 216
    assert debug.get("changed") is True


def test_owned_output_displays_historical_failure_diagnostics_without_forging_failed_execution(history_case):
    metrics = {"request_mode": "llm_reparse", "requested_provider": "gemini",
        "llm_assist": True, "processing_stage": "inference", "result_state": "hard_failed",
        "error": "main_ocr_failed:gemini", "confirmed_lines_retained": True}
    case, sheet, selected, job = _owned_debug_case(history_case, metrics=metrics, cached_debug={})
    payload = _read_owned_debug(case, sheet, selected, job)
    debug = payload.get("_reparse_debug") or {}
    assert debug.get("provider") == "gemini"
    assert debug.get("requested_provider") == "gemini"
    assert debug.get("error") == "main_ocr_failed:gemini"
    assert debug.get("processing_stage") == "inference"
    assert debug.get("result_state") == "hard_failed"
    assert job["status"] == "done"
    assert job["metrics"]["rerun_terminal_receipt"]["status"] == "done"


def test_owned_output_debug_clears_stale_errors_without_overwriting_saved_five(history_case):
    metrics = {"provider": "gemini", "requested_provider": "gemini", "row_count": 43,
        "line_count": 29, "before_count": 7, "after_count": 29,
        "changed": True, "llm_assist": True}
    stale = {"provider": "gemini", "error": "sheet_row_coverage_low",
        "reject_reasons": ["sheet_row_coverage_low"],
        "validation_detail": {"expected_row_count": 56, "actual_row_count": 43}}
    merged, changed = order_service._sync_reparse_debug_from_job_metrics(
        {"_reparse_debug": stale}, {"status": "done", "metrics": metrics})
    assert changed is True
    assert merged["_reparse_debug"]["error"] is None
    assert merged["_reparse_debug"]["reject_reasons"] == []
    assert merged["_reparse_debug"]["validation_detail"] == {}
    case, sheet, selected, job = _owned_debug_case(history_case, metrics=metrics, cached_debug=stale)
    payload = _read_owned_debug(case, sheet, selected, job)
    debug = payload.get("_reparse_debug") or {}
    assert debug.get("provider") == "gemini"
    assert debug.get("error") in {None, ""}
    assert debug.get("reject_reasons", []) == []
    assert debug.get("validation_detail", {}) == {}


def test_owned_output_preserves_explicit_warning_diagnostics_and_saved_five(history_case):
    metrics = {"provider": "gemini", "requested_provider": "gemini", "row_count": 62,
        "line_count": 248, "before_count": 78, "after_count": 248,
        "changed": True, "llm_assist": True, "warning_reasons": ["sheet_column_anomaly"],
        "warning_detail": {"quality_issue": "column_anomaly", "column_anomaly_count": 1}}
    stale = {"provider": "gemini", "warning_reasons": [], "warning_detail": {}}
    case, sheet, selected, job = _owned_debug_case(history_case, metrics=metrics, cached_debug=stale)
    payload = _read_owned_debug(case, sheet, selected, job)
    debug = payload.get("_reparse_debug") or {}
    assert debug.get("provider") == "gemini"
    assert debug.get("warning_reasons") == ["sheet_column_anomaly"]
    assert debug.get("warning_detail", {}).get("quality_issue") == "column_anomaly"


def test_owned_output_refuses_stale_aux_position_mapping_without_copying_totals(history_case):
    from datetime import date
    from src.services.candidate_resolution_service import build_column_mapping_resolution
    from tests.integration.test_draft_sheet_service import _fac00004_owned_source, _legacy_aux_target_refused

    expected = [["04/26", "朝", "主", "鶏じゃが", "66", "", "", "", "", "", "", ""]]
    case, source, fields, header = _fac00004_owned_source(history_case,
        rows=expected, menu_entries=[{"id": "status-aux-april26", "monthly_menu_id": "2026-04",
            "menu_date": date(2026, 4, 26), "daypart": "朝", "name": "鶏じゃが",
            "category": "主", "slot_index": 0, "diet_type": "regular"}], lines=[])
    case.save(source["sheet"])
    _legacy_aux_target_refused(case, "67")
    _legacy_aux_target_refused(case, "70")
    rows = [
        ["", "", "", "", "", "", "", "", "", "", "", "山田菜", "備考欄"],
        ["", "", "", "献立", "合計", "#☆", "通所", "職員", "平森", "", "", "", ""],
        ["日 付", "区分", "", "", "", "", "", "", "肉蒸", "魚禁", "揚げ物", "", ""],
        ["", "", "", "", "70", "", "", "", "", "", "", "", ""],
        ["4/26", "朝", "主", "鶏じゃが", "67", "66", "", "", "", "", "", "", ""],
    ]
    stale = {
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
        }
    with session_scope() as session:
        cache = session.get(OrderOcrCache, case.id)
        if cache is None:
            session.add(OrderOcrCache(order_id=case.id, payload=stale))
        else:
            cache.payload = stale
    with case.readonly():
        resolution = build_column_mapping_resolution(stale)
        assert resolution["blocked"] is True
        assert "position_fallback_removed" in resolution["blocked_reasons"]
        assert resolution["resolved_value"] is None
        response = case.client.get(f"/orders/{case.id}/ocr-output", headers=case.actor.headers)
        assert response.status_code == 200, response.text
        current = order_service.get_current_sheet_context(case.id)
        assert current["base_evidence_run_id"] == case.evidence
        assert current["rows"] == expected
        assert case.get("workflow-v2/sheet-source")["sheet"]["rows"] == expected
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == stale


def test_formally_saved_seventy_is_not_replaced_by_stale_reject_five(history_case):
    from datetime import date
    from tests.integration.test_draft_sheet_service import _fac00004_owned_source, _legacy_aux_target_refused

    native_rows = [["02/15", "朝", "主", "Menu A", "5", "", "", "", "", "", "", ""]]
    case, source, native_fields, native_header = _fac00004_owned_source(history_case,
        rows=native_rows, menu_entries=[{"id": "status-applied-feb15", "monthly_menu_id": "2026-02",
            "menu_date": date(2026, 2, 15), "daypart": "朝", "name": "Menu A",
            "category": "主", "slot_index": 0, "diet_type": "regular"}], lines=[],
        period="2026-02@2026-02-15~2026-02-21", received=datetime(2026, 2, 15, 9))
    _legacy_aux_target_refused(case, "70")
    fields = [*native_fields[:4], "aux.col_4", *native_fields[4:]]
    header = [*native_header[:4], "合計", *native_header[4:]]
    quantity_fields = [field for field in fields if field.startswith("qty.")]
    aux_fields = [field for field in fields if field.startswith("aux.")]
    assert quantity_fields
    assert len(aux_fields) >= 2
    diet_key, area_key = order_service._quantity_meta_from_field(quantity_fields[0])
    assert diet_key and area_key

    def build_row(aux_total, quantity):
        row = [""] * len(fields)
        for field, value in {"date_mmdd": "02/15", "daypart": "朝", "menu": "Menu A",
            aux_fields[0]: "主", aux_fields[1]: aux_total, quantity_fields[0]: quantity}.items():
            row[fields.index(field)] = value
        return row

    stale_rows = [build_row("5", "5")]
    stale_digest = order_service._sheet_digest(fields=fields, header=header,
        rows_payload=stale_rows, row_ids=["draft-1"])
    applied_rows = [build_row("70", "70")]
    # These are explicit operator-edited display/quantity values, not recognized totals.
    edited = {**source["sheet"], "fields": fields, "header": header,
        "rows": applied_rows, "row_ids": ["row-1"]}
    case.save(edited)
    saved = order_service.get_current_sheet_context(case.id)
    assert saved["base_evidence_run_id"] == case.evidence
    assert saved["rows"] == applied_rows
    stale_latest = {
        "revision_id": "OCRREV-stale-latest",
        "edited_at": "2026-02-15T00:00:00",
        "ui_mode": "sheet", "fields": fields, "header": header,
        "row_ids": ["draft-1"], "rows": stale_rows, "row_count": len(stale_rows),
        "before_digest": stale_digest, "after_digest": stale_digest, "changed": False,
        "sheet_save_only": True, "sheet_save_mode": "draft_candidate",
        "review_state": "auto_apply_blocked", "draft_from_reparse_reject": True,
        "markdown": order_service._build_markdown_table_string(header, stale_rows),
    }
    with session_scope() as session:
        cache = session.get(OrderOcrCache, case.id)
        assert cache is not None
        payload = dict(cache.payload or {})
        edited_archive = dict(payload.get("_edited_ocr") or {})
        edited_archive["latest"] = stale_latest
        edited_archive["revisions"] = [stale_latest]
        payload["_edited_ocr"] = edited_archive
        payload["edited_table"] = {"header": header, "rows": stale_rows,
            "row_ids": ["draft-1"], "edited_at": stale_latest["edited_at"],
            "revision_id": stale_latest["revision_id"]}
        cache.payload = payload
    with case.readonly():
        current = order_service.get_current_sheet_context(case.id)
        assert current["draft_id"] == saved["draft_id"]
        assert current["base_evidence_run_id"] == case.evidence
        assert len(current["rows"]) == 1
        assert current["rows"][0][fields.index(aux_fields[1])] == "70"
        assert current["rows"][0][fields.index(quantity_fields[0])] == "70"
        assert current["rows"] == applied_rows
        # Native selected source stays five; the old applied-cache mirror is not authority.
        assert case.get("workflow-v2/sheet-source")["sheet"]["rows"] == native_rows
        with session_scope() as session:
            assert session.get(OrderOcrCache, case.id).payload == payload

STATUS_RETIRED_CASES = [
    ("refuse_draft_sheet_legacy_running_llm_reparse_health", "test_draft_sheet_surfaces_running_llm_reparse_health", "GET", "draft-sheet", None),
    ("refuse_draft_sheet_legacy_awaiting_output_ocr_rerun_state", "test_draft_sheet_surfaces_awaiting_output_ocr_rerun_state", "GET", "draft-sheet", None),
    ("refuse_confirm_endpoint_blocks_when_weekly_menu_missing", "test_confirm_endpoint_blocks_when_weekly_menu_missing", "POST", "confirm", None),
    ("refuse_reparse_endpoint_legacy_job_running_before_background", "test_reparse_endpoint_marks_job_running_before_background", "POST", "reparse", {"ocr_provider": "gemini", "llm_assist": True}),
    ("refuse_reparse_endpoint_rejects_removed_tesseract_provider", "test_reparse_endpoint_rejects_removed_tesseract_provider", "POST", "reparse", {"ocr_provider": "tesseract", "force": True}),
    ("refuse_ocr_rerun_endpoint_rejects_removed_tesseract_provider", "test_ocr_rerun_endpoint_rejects_removed_tesseract_provider", "POST", "ocr-rerun", {"ocr_provider": "tesseract"}),
    ("refuse_reparse_endpoint_legacy_llm_assist_for_user_requested_reparse", "test_reparse_endpoint_defaults_to_llm_assist_for_user_requested_reparse", "POST", "reparse", None),
    ("refuse_reparse_endpoint_legacy_explicit_ocr_model", "test_reparse_endpoint_passes_explicit_ocr_model", "POST", "reparse", {"ocr_provider": "gemini", "ocr_model": "gemini-2.5-pro", "llm_assist": True}),
    ("refuse_reparse_endpoint_legacy_llm_model_alias", "test_reparse_endpoint_accepts_llm_model_alias", "POST", "reparse", {"ocr_provider": "gemini", "llm_model": "gemini-2.5-flash", "llm_assist": True}),
    ("refuse_reparse_endpoint_legacy_prompt_preset", "test_reparse_endpoint_accepts_prompt_preset", "POST", "reparse", {"ocr_provider": "gemini", "llm_assist": True, "prompt_preset": "row_alignment"}),
    ("refuse_reparse_endpoint_legacy_merged_cell_prompt_preset", "test_reparse_endpoint_accepts_merged_cell_prompt_preset", "POST", "reparse", {"ocr_provider": "gemini", "llm_assist": True, "prompt_preset": "merged_cell_quantity_spans"}),
    ("refuse_ocr_recover_endpoint_legacy_pipeline_first_pass", "test_ocr_recover_endpoint_requests_pipeline_first_pass", "POST", "ocr-recover", None),
    ("refuse_ocr_recover_endpoint_legacy_stale_job_with_pipeline_first_pass", "test_ocr_recover_endpoint_retries_stale_job_with_pipeline_first_pass", "POST", "ocr-recover", None),
    ("refuse_ocr_rerun_endpoint_legacy_active_job", "test_ocr_rerun_endpoint_force_replaces_active_job", "POST", "ocr-rerun", {"force": True}),
]


def test_retired_status_requests_refuse_without_mutation_or_lost_saved_values(history_case):
    from tests.contract.test_orders_ocr_sheet_history_api import _sheet
    f = history_case()
    f.produce()
    saved = f.save(_sheet("8", "retained", "status-retained"))["saved_sheet"]
    for case_id, _old_name, method, endpoint, body in STATUS_RETIRED_CASES:
        _refuse(f, method, endpoint, body)
        assert f.get("workflow-v2/sheet")["sheet"] == saved["sheet"], case_id


def test_current_owned_detail_and_list_ignore_unowned_cache_success(history_case):
    from tests.contract.test_orders_ocr_sheet_history_api import _sheet
    f = history_case()
    f.produce()
    saved = f.save(_sheet("8", "operator-retained", "status-current"))["saved_sheet"]["sheet"]
    job = get_job(f.job)
    assert job["status"] == "done"
    with session_scope() as session:
        cached = session.get(OrderOcrCache, f.id)
        assert cached is not None
        cached.payload = {"status": "success", "rows": [["dummy"]], "metrics": {"provider": "old"}}
    detail = f.get()
    assert detail["ocr_status"] == "done"
    assert detail.get("ocr_error") in {None, ""}
    with f.readonly():
        response = f.client.get("/orders?include_ocr=true", headers=f.actor.headers)
    assert response.status_code == 200
    (listed,) = [row for row in response.json()["orders"] if row["id"] == f.id]
    assert listed["ocr_status"] == "done"
    assert f.get("workflow-v2/sheet")["sheet"] == saved
    assert get_job(f.job)["status"] == "done"
