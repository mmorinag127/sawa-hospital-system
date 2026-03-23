import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import draft_sheet_service, order_service, template_resolution_service, workflow_state_service  # noqa: E402
from src.services.ocr_job_service import create_job, get_job as get_ocr_job, update_job  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _seed_order(
    *,
    message_id: str,
    facility_hint: str | None = None,
    week_hint: str | None = "2026-03",
) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy-workflow.pdf",
        received_at=datetime(2026, 3, 22, 9, 0, 0),
        facility_hint=facility_hint,
        week_hint=week_hint,
    )
    return order_service.create_order_from_ingest(payload, lines=None)


def _persist_evidence(
    order_id: str,
    *,
    facility_choice_required: bool = False,
    degraded: bool = False,
    extra_payload: dict | None = None,
) -> dict:
    payload = {
        "input_reference": "gs://bucket/orders/order.pdf",
        "pages": [
            {
                "page_index": 1,
                "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
            }
        ],
        "table_raw": "\n".join(
            [
                "|日付|区分|メニュー|常食|",
                "|---|---|---|---|",
                "|03/22|朝|Menu A|5|",
            ]
        ),
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
            "confidence": 0.96,
            "blocked": False,
            "blocked_reasons": [],
        },
        "quantity_subgrid_passes": [
            {
                "page_index": 1,
                "cells": [{"row_index": 0, "column_index": 3, "text": "5"}],
            }
        ],
        "table_box": [0.1, 0.2, 0.9, 0.8],
        "grid_column_edges": [0.1, 0.3, 0.6, 0.9],
        "grid_row_edges": [0.2, 0.4, 0.8],
    }
    if degraded:
        payload.pop("input_reference", None)
        payload.pop("pages", None)
    if facility_choice_required:
        payload["facility_candidates"] = [
            {"facility_id": "FAC00001", "facility_name": "Facility 1", "score": 0.81},
            {"facility_id": "FAC00002", "facility_name": "Facility 2", "score": 0.76},
        ]
    if isinstance(extra_payload, dict):
        payload.update(extra_payload)
    evidence = order_service.persist_ocr_evidence_run(order_id, payload)
    assert isinstance(evidence, dict)
    return evidence


def test_refresh_workflow_state_moves_from_choice_required_to_apply_ready_after_facility_choice(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-001", facility_hint=None, week_hint="2026-03")
    _persist_evidence(order["id"], facility_choice_required=True)
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "identity_choice_required"
    assert workflow["primary_action"] == "resolve_identity_choice"
    assert workflow["apply_gate"]["can_apply"] is False
    assert "facility_choice_required" in (workflow["apply_gate"]["blockers"] or [])
    decisions = workflow.get("critical_decisions") or []
    assert any(item.get("decision_type") == "facility" for item in decisions)

    result, error = order_service.choose_critical_decision(
        order["id"],
        "facility",
        "FAC00001",
        selected_by="test",
    )

    assert error is None
    assert isinstance(result, dict)
    next_workflow = result.get("workflow_state") or {}
    assert next_workflow.get("state") == "apply_ready"
    assert next_workflow.get("apply_gate", {}).get("can_apply") is True


def test_refresh_workflow_state_returns_uploaded_without_evidence():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-002", facility_hint="FAC00001", week_hint="2026-03")

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "uploaded"
    assert workflow["primary_action"] == "run_ocr_pipeline"
    blockers = workflow["blockers_json"] or []
    assert "evidence_view_unavailable" in blockers
    assert "evidence_edit_unavailable" in blockers


def test_refresh_workflow_state_returns_recovery_required_for_degraded_evidence():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-003", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(order["id"], degraded=True)

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "recovery_required"
    assert workflow["primary_action"] == "recover_ocr_evidence"
    assert workflow["apply_gate"]["can_apply"] is False
    assert "evidence_view_unavailable" in (workflow["apply_gate"]["blockers"] or [])


def test_refresh_workflow_state_returns_semantic_shell_only_for_partial_semantic_evidence(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-semantic-shell", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(
        order["id"],
        extra_payload={
            "template_id": "unknown-template",
            "template_resolution": {
                "resolved_template_id": "unknown-template",
                "candidate_template_ids": ["unknown-template"],
                "confidence": 0.91,
                "blocked": False,
                "blocked_reasons": [],
            },
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "semantic_shell_only"
    assert workflow["primary_action"] == "rerun_ocr_pipeline"
    assert "semantic_shell_only" in (workflow["apply_gate"]["blockers"] or [])


def test_refresh_workflow_state_does_not_get_stuck_semantic_shell_only_when_resolved_template_can_supply_grid(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-template-grid-reuse", facility_hint="FAC00006", week_hint="2026-03")
    _persist_evidence(
        order["id"],
        extra_payload={
            "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "template_resolution": template_resolution_service.build_template_resolution(
                requested_template_id="fax_layout_regular_soft_mixer_forbidden_v1",
                requested_template_ids=[
                    "fax_layout_regular_soft_mixer_forbidden_v1",
                    "fax_layout_floor_2f3f_v1",
                ],
                resolved_template_id="fax_layout_regular_soft_mixer_forbidden_v1",
                classification={
                    "matched_template_id": "fax_layout_floor_2f3f_v1",
                    "confidence": 0.94,
                    "candidates": [
                        {"id": "fax_layout_floor_2f3f_v1", "score": 0.94},
                        {"id": "fax_layout_regular_soft_mixer_forbidden_v1", "score": 0.91},
                    ],
                },
                page_correction_summary={"pages": [{"mode": "template_warp", "template_id": "fax_layout_regular_soft_mixer_forbidden_v1"}]},
            ),
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["primary_action"] == "apply_draft"
    assert "semantic_shell_only" not in (workflow["apply_gate"]["blockers"] or [])


def test_refresh_workflow_state_returns_new_evidence_available_when_latest_evidence_differs_from_draft(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-new-evidence", facility_hint="FAC00001", week_hint="2026-03")
    first = _persist_evidence(order["id"], extra_payload={"table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|5|"})
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
        base_evidence_run_id=first["id"],
        edited_by="tester",
    )
    assert isinstance(saved, dict)
    assert saved["base_evidence_run_id"] == first["id"]
    second = _persist_evidence(
        order["id"],
        extra_payload={
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|8|",
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "new_evidence_available"
    assert workflow["primary_action"] == "switch_to_new_evidence"
    assert workflow["evidence_run_id"] == first["id"]
    assert workflow["active_evidence_run_id"] == first["id"]
    assert workflow["candidate_evidence_run_id"] == second["id"]
    assert workflow["apply_gate"]["can_apply"] is True


def test_refresh_workflow_state_uses_rerun_job_metrics_to_surface_new_candidate(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-rerun-candidate", facility_hint="FAC00001", week_hint="2026-03")
    first = _persist_evidence(order["id"], extra_payload={"table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|5|"})
    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
        base_evidence_run_id=first["id"],
        edited_by="tester",
    )
    assert isinstance(saved, dict)
    assert saved["base_evidence_run_id"] == first["id"]
    second = _persist_evidence(
        order["id"],
        extra_payload={"table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|8|"},
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "get_ocr_job",
        lambda _job_id: {
            "id": f"OCR-{order['id']}",
            "status": "done",
            "metrics": {
                "request_mode": "ocr_rerun",
                "new_evidence_available": True,
                "evidence_run_id": second["id"],
            },
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "describe_job_state",
        lambda _job: {"status": "done", "job_id": f"OCR-{order['id']}"},
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "new_evidence_available"
    assert workflow["active_evidence_run_id"] == first["id"]
    assert workflow["candidate_evidence_run_id"] == second["id"]


def test_refresh_workflow_state_ignores_stale_rerun_candidate_when_latest_is_already_active(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-stale-rerun-candidate", facility_hint="FAC00001", week_hint="2026-03")
    older = _persist_evidence(order["id"], extra_payload={"table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|5|"})
    latest = _persist_evidence(order["id"], extra_payload={"table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|8|"})
    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "8"]],
            "row_ids": ["row-1"],
        },
        base_evidence_run_id=latest["id"],
        edited_by="tester",
    )
    assert isinstance(saved, dict)
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "get_ocr_job",
        lambda _job_id: {
            "id": f"OCR-{order['id']}",
            "status": "done",
            "metrics": {
                "request_mode": "ocr_rerun",
                "new_evidence_available": True,
                "evidence_run_id": older["id"],
            },
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "describe_job_state",
        lambda _job: {"status": "done", "job_id": f"OCR-{order['id']}"},
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["active_evidence_run_id"] == latest["id"]
    assert workflow["candidate_evidence_run_id"] is None


def test_refresh_workflow_state_returns_rerun_in_progress_when_ocr_rerun_running(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-rerun-running", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(order["id"])
    monkeypatch.setattr(
        workflow_state_service,
        "get_ocr_job",
        lambda _job_id: {
            "id": f"OCR-{order['id']}",
            "status": "running",
            "metrics": {"processing_stage": "ocr_pipeline", "request_mode": "ocr_rerun"},
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "describe_job_state",
        lambda _job: {"status": "running", "job_id": f"OCR-{order['id']}"},
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "rerun_in_progress"
    assert workflow["primary_action"] == "wait_for_rerun"


def test_get_order_workflow_state_reconciles_completed_rerun_output(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-rerun-reconcile", facility_hint="FAC00001", week_hint="2026-03")
    old_evidence = _persist_evidence(
        order["id"],
        extra_payload={
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|5|",
        },
    )
    assert isinstance(old_evidence, dict)
    create_job(f"OCR-{order['id']}", input_reference="file://dummy-workflow.pdf", status="running")
    update_job(
        f"OCR-{order['id']}",
        status="running",
        metrics={
            "processing_stage": "ocr_pipeline",
            "request_mode": "ocr_rerun",
            "status": "running",
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    new_payload = {
        "status": "done",
        "stage": "done",
        "input_reference": "gs://bucket/input/OCR-order-rerun.pdf",
        "output_reference": "gs://bucket/output/OCR-order-rerun.pdf.json",
        "pages": [
            {
                "page_index": 1,
                "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
            }
        ],
        "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|8|",
        "tables": [{"page_index": 1, "rows": [["03/22", "朝", "Menu A", "8"]]}],
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
            "confidence": 0.99,
            "blocked": False,
            "blocked_reasons": [],
        },
        "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/22", "朝", "Menu A", "8"]]}],
        "table_box": [0.1, 0.2, 0.9, 0.8],
        "grid_column_edges": [0.1, 0.3, 0.6, 0.9],
        "grid_row_edges": [0.2, 0.4, 0.8],
    }
    monkeypatch.setattr(
        order_service,
        "_list_latest_completed_ocr_outputs",
        lambda *_args, **_kwargs: [("gs://bucket/output/OCR-order-rerun.pdf.json", new_payload)],
    )

    workflow = order_service.get_order_workflow_state(order["id"], refresh=True)

    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    rerun_job = get_ocr_job(f"OCR-{order['id']}")

    assert isinstance(workflow, dict)
    assert isinstance(latest_evidence, dict)
    assert latest_evidence["source"] == "ocr-rerun-reconcile"
    assert latest_evidence["id"] != old_evidence["id"]
    assert isinstance(rerun_job, dict)
    assert rerun_job["status"] == "done"
    assert rerun_job["output_reference"] == "gs://bucket/output/OCR-order-rerun.pdf.json"


def test_get_order_workflow_state_reconciles_completed_rerun_output_after_timeout_failure(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-rerun-timeout-reconcile", facility_hint="FAC00001", week_hint="2026-03")
    old_evidence = _persist_evidence(
        order["id"],
        extra_payload={
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|5|",
        },
    )
    assert isinstance(old_evidence, dict)
    create_job(f"OCR-{order['id']}", input_reference="file://dummy-workflow.pdf", status="failed")
    update_job(
        f"OCR-{order['id']}",
        status="failed",
        error_message="evidence_rerun_failed:OCR pipeline output not found",
        metrics={
            "processing_stage": "ocr_pipeline",
            "request_mode": "ocr_rerun",
            "status": "failed",
            "error": "evidence_rerun_failed",
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    new_payload = {
        "status": "done",
        "stage": "done",
        "input_reference": "gs://bucket/input/OCR-order-rerun-timeout.pdf",
        "output_reference": "gs://bucket/output/OCR-order-rerun-timeout.pdf.json",
        "pages": [
            {
                "page_index": 1,
                "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
            }
        ],
        "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|9|",
        "tables": [{"page_index": 1, "rows": [["03/22", "朝", "Menu A", "9"]]}],
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
            "confidence": 0.99,
            "blocked": False,
            "blocked_reasons": [],
        },
        "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/22", "朝", "Menu A", "9"]]}],
        "table_box": [0.1, 0.2, 0.9, 0.8],
        "grid_column_edges": [0.1, 0.3, 0.6, 0.9],
        "grid_row_edges": [0.2, 0.4, 0.8],
    }
    monkeypatch.setattr(
        order_service,
        "_list_latest_completed_ocr_outputs",
        lambda *_args, **_kwargs: [("gs://bucket/output/OCR-order-rerun-timeout.pdf.json", new_payload)],
    )

    workflow = order_service.get_order_workflow_state(order["id"], refresh=True)

    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    rerun_job = get_ocr_job(f"OCR-{order['id']}")

    assert isinstance(workflow, dict)
    assert isinstance(latest_evidence, dict)
    assert latest_evidence["source"] == "ocr-rerun-reconcile"
    assert latest_evidence["id"] != old_evidence["id"]
    assert isinstance(rerun_job, dict)
    assert rerun_job["status"] == "done"
    assert rerun_job["output_reference"] == "gs://bucket/output/OCR-order-rerun-timeout.pdf.json"


def test_refresh_workflow_state_returns_rerun_failed_keep_current_when_latest_rerun_failed(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-rerun-failed", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(order["id"])
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
        edited_by="tester",
    )
    monkeypatch.setattr(
        workflow_state_service,
        "get_ocr_job",
        lambda _job_id: {
            "id": f"OCR-{order['id']}",
            "status": "failed",
            "metrics": {"processing_stage": "ocr_pipeline", "request_mode": "ocr_rerun"},
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "describe_job_state",
        lambda _job: {"status": "hard_failed", "job_id": f"OCR-{order['id']}"},
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "rerun_failed_keep_current"
    assert workflow["primary_action"] == "rerun_ocr_pipeline"
    assert "rerun_failed_keep_current" in (workflow["warnings_json"] or [])


def test_refresh_workflow_state_does_not_treat_llm_reparse_as_rerun_state(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-llm-reparse-running", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(order["id"])
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
        edited_by="tester",
    )
    monkeypatch.setattr(
        workflow_state_service,
        "get_ocr_job",
        lambda _job_id: {
            "id": f"OCR-{order['id']}",
            "status": "running",
            "metrics": {"processing_stage": "llm_reparse", "request_mode": "llm_reparse"},
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "describe_job_state",
        lambda _job: {"status": "running", "job_id": f"OCR-{order['id']}"},
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] != "rerun_in_progress"
    assert workflow["primary_action"] in {"apply_draft", "edit_draft"}


def test_refresh_workflow_state_returns_layout_choice_required_for_column_mapping_candidates():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-003b", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(
        order["id"],
        extra_payload={
            "column_mapping_candidates": [
                {"value": "cols-a", "label": "常食 / 軟菜 / ミキサー", "score": 0.62},
                {"value": "cols-b", "label": "常食 / 常食(袋分け) / 軟菜", "score": 0.58},
            ],
        },
    )
    from unittest.mock import patch

    with patch.object(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    ):
        workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "layout_choice_required"
    assert workflow["primary_action"] == "resolve_layout_choice"
    assert "column_mapping_choice_required" in (workflow["apply_gate"]["blockers"] or [])
    decisions = workflow.get("critical_decisions") or []
    assert any(item.get("decision_type") == "column_mapping" for item in decisions)


def test_refresh_workflow_state_unblocks_after_column_mapping_choice(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-003bb", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(
        order["id"],
        extra_payload={
            "column_mapping_candidates": [
                {"value": "cols-a", "label": "常食 / 軟菜 / ミキサー", "score": 0.62},
                {"value": "cols-b", "label": "常食 / 常食(袋分け) / 軟菜", "score": 0.58},
            ],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    first = workflow_state_service.refresh_workflow_state(order["id"])
    assert first["state"] == "layout_choice_required"

    result, error = order_service.choose_critical_decision(
        order["id"],
        "column_mapping",
        "cols-a",
        selected_by="test",
    )

    assert error is None
    assert isinstance(result, dict)
    next_workflow = result.get("workflow_state") or {}
    assert next_workflow.get("state") == "apply_ready"
    assert next_workflow.get("apply_gate", {}).get("can_apply") is True


def test_refresh_workflow_state_returns_draft_blocked_when_template_is_unresolved():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-003c", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(
        order["id"],
        extra_payload={
            "template_resolution": {
                "resolved_template_id": "",
                "candidate_template_ids": [],
                "confidence": 0.2,
                "blocked": True,
                "blocked_reasons": ["template_resolution_missing"],
            },
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "draft_blocked"
    assert workflow["primary_action"] == "resolve_blockers"
    assert "template_unresolved" in (workflow["apply_gate"]["blockers"] or [])


def test_refresh_workflow_state_returns_review_required_for_high_risk_quantity_signals():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-004", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(
        order["id"],
        extra_payload={
            "cell_issues": [
                {"issue_code": "column_swap"},
                {"issue_code": "merged_numeric_cell"},
            ],
            "failed_cells": [
                {"row_index": 0, "column_index": 3},
            ],
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "review_required"
    assert workflow["primary_action"] == "review_critical_cells"
    warnings = workflow["apply_gate"]["warnings"] or []
    assert "column_mapping_review_required" in warnings
    assert "quantity_review_required" in warnings


def test_refresh_workflow_state_returns_review_required_for_low_trust_populated_draft():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-low-trust-draft", facility_hint="FAC00001", week_hint="2026-03")
    evidence = _persist_evidence(
        order["id"],
        extra_payload={
            "cell_issues": [{"issue_code": "merged_numeric_cell"}],
        },
    )
    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食", "備考"],
            "rows": [["03/22", "朝", "Menu A", "21", ""]],
            "row_ids": ["draft-row-1"],
        },
        base_evidence_run_id=evidence["id"],
        warnings=["sheet_payload_mapping_low_confidence"],
    )
    assert saved is not None

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "review_required"
    assert workflow["primary_action"] == "review_critical_cells"
    assert "numeric_trust_low" in (workflow["apply_gate"]["warnings"] or [])


def test_refresh_workflow_state_uses_semantic_initial_draft_for_low_trust_review(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-semantic-bootstrap-review", facility_hint="FAC00006", week_hint="2026-03")
    _persist_evidence(
        order["id"],
        extra_payload={
            "cell_issues": [{"issue_code": "merged_numeric_cell"}],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    monkeypatch.setattr(
        order_service,
        "build_initial_sheet_draft",
        lambda _order_id: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.mixer_x"],
            "header": ["日付", "区分", "メニュー", "常食", "ミキサー"],
            "rows": [["03/22", "朝", "Menu A", "21", "3"]],
            "row_ids": ["row-1"],
            "warnings": ["sheet_payload_mapping_low_confidence", "sheet_ocr_review_required"],
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "review_required"
    assert workflow["primary_action"] == "review_critical_cells"
    assert workflow["candidate_evidence_run_id"] is None
    assert "numeric_trust_low" in (workflow["apply_gate"]["warnings"] or [])


def test_refresh_workflow_state_blocks_when_weekly_menu_is_missing():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-missing-menu", facility_hint="FAC00001", week_hint="2199-11")
    _persist_evidence(order["id"])

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "draft_blocked"
    blockers = workflow["apply_gate"]["blockers"] or []
    assert "weekly_menu_missing" in blockers
    assert workflow["apply_gate"]["can_apply"] is False
    assert workflow["apply_gate"]["can_confirm"] is False


def test_refresh_workflow_state_uses_saved_draft_sheet_blockers():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-draft-blockers", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(order["id"])
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "row_ids": ["draft-row-1"],
            "ui_mode": "sheet",
        },
        blockers=[],
        warnings=["sheet_weekly_menu_missing"],
    )
    assert saved is not None

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    apply_gate = workflow["apply_gate"]
    assert "weekly_menu_missing" in (apply_gate.get("apply_blockers") or [])
    assert "weekly_menu_missing" in (apply_gate.get("confirm_blockers") or [])
    assert apply_gate["can_apply"] is False
    assert apply_gate["can_confirm"] is False


def test_refresh_workflow_state_returns_layout_choice_required_for_critical_quantity_candidates():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-005", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(
        order["id"],
        extra_payload={
            "critical_quantity_candidates": [
                {
                    "candidate_id": "qty-a",
                    "candidate_type": "critical_quantity_candidate",
                    "value": "qty-a",
                    "label": "3を採用",
                    "score": 0.81,
                    "reason": "high_impact_total_cell",
                    "evidence_ref": {"page": 1, "row_index": 7, "column_index": 4},
                    "critical": True,
                },
                {
                    "candidate_id": "qty-b",
                    "candidate_type": "critical_quantity_candidate",
                    "value": "qty-b",
                    "label": "8を採用",
                    "score": 0.76,
                    "reason": "high_impact_total_cell",
                    "evidence_ref": {"page": 1, "row_index": 7, "column_index": 4},
                    "critical": True,
                },
            ],
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "layout_choice_required"
    assert workflow["primary_action"] == "resolve_layout_choice"
    blockers = workflow["apply_gate"]["blockers"] or []
    assert "quantity_choice_required" in blockers
    decisions = workflow.get("critical_decisions") or []
    quantity_decision = next(item for item in decisions if item.get("decision_type") == "quantity")
    assert quantity_decision["candidate_set_json"]["ambiguity_scope"] == "high_impact_quantity"


def test_refresh_workflow_state_clears_review_required_after_quantity_choice(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-005b", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(
        order["id"],
        extra_payload={
            "cell_issues": [
                {"issue_code": "merged_numeric_cell"},
            ],
            "quantity_candidates": [
                {"value": "qty-a", "label": "3を採用", "score": 0.81},
                {"value": "qty-b", "label": "8を採用", "score": 0.76},
            ],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    first = workflow_state_service.refresh_workflow_state(order["id"])

    assert first["state"] == "layout_choice_required"
    assert "quantity_choice_required" in (first["apply_gate"]["blockers"] or [])

    result, error = order_service.choose_critical_decision(
        order["id"],
        "quantity",
        "qty-a",
        selected_by="test",
    )

    assert error is None
    assert isinstance(result, dict)
    next_workflow = result.get("workflow_state") or {}
    assert next_workflow.get("state") == "apply_ready"
    assert next_workflow.get("apply_gate", {}).get("can_apply") is True
    warnings = next_workflow.get("apply_gate", {}).get("warnings") or []
    assert "quantity_review_required" not in warnings
