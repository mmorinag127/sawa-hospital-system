import json
import pathlib
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import config_service, critical_decision_service, draft_sheet_service, order_current_state_service, order_service, template_resolution_service, workflow_state_service  # noqa: E402
from src.services.ocr_job_service import create_job, get_job as get_ocr_job, update_job  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _structured_cells(rows: list[list[str]]) -> list[dict[str, object]]:
    cells: list[dict[str, object]] = []
    row_height = 1.0 / max(len(rows), 1)
    col_width = 1.0 / max(max((len(row) for row in rows), default=1), 1)
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            cells.append(
                {
                    "row_index": row_index,
                    "col_index": col_index,
                    "text": value,
                    "bbox": [
                        round(col_index * col_width, 4),
                        round(row_index * row_height, 4),
                        round((col_index + 1) * col_width, 4),
                        round((row_index + 1) * row_height, 4),
                    ],
                }
            )
    return cells


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


def _semantic_initial_draft(
    order_id: str,
    *,
    rows: list[list[str]] | None = None,
    fields: list[str] | None = None,
    header: list[str] | None = None,
    warnings: list[str] | None = None,
    source: str = "weekly_menu+ocr_payload",
) -> dict:
    normalized_fields = list(fields or ["date_mmdd", "daypart", "menu", "qty.regular_2f"])
    normalized_header = list(header or ["日付", "区分", "メニュー", "常食2F"])
    normalized_rows = [list(row) for row in (rows or [["03/22", "朝", "Menu A", "5"]])]
    return {
        "order_id": order_id,
        "source": source,
        "fields": normalized_fields,
        "header": normalized_header,
        "rows": normalized_rows,
        "row_ids": [f"row-{idx + 1}" for idx in range(len(normalized_rows))],
        "warnings": list(warnings or []),
    }


def test_refresh_workflow_state_persists_current_state_snapshot() -> None:
    order_service.clear_all()
    order = _seed_order(message_id="msg-current-state-snapshot-001")
    draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json=_semantic_initial_draft(order["id"], source="manual_draft"),
        draft_state="draft_ready",
        edited_by="tester",
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])
    snapshot = order_current_state_service.get_current_state_payload(order["id"])

    assert isinstance(workflow, dict)
    assert isinstance(snapshot, dict)
    assert snapshot["order_id"] == order["id"]
    assert snapshot["source"] == "manual_draft"
    assert snapshot["draft_id"]
    assert snapshot["resolved_week_id"] == "2026-03@2026-03-22~2026-03-28"


def test_persist_current_state_upserts_existing_order_row() -> None:
    order_service.clear_all()
    order = _seed_order(message_id="msg-current-state-upsert-001")

    first = order_current_state_service.persist_current_state(
        order_id=order["id"],
        state_json={"order_id": order["id"], "source": "seed-a"},
        draft_id="draft-a",
        evidence_run_id="evidence-a",
    )
    second = order_current_state_service.persist_current_state(
        order_id=order["id"],
        state_json={"order_id": order["id"], "source": "seed-b"},
        draft_id="draft-b",
        evidence_run_id="evidence-b",
    )
    persisted = order_current_state_service.get_current_state(order["id"])

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert isinstance(persisted, dict)
    assert persisted["draft_id"] == "draft-b"
    assert persisted["evidence_run_id"] == "evidence-b"
    assert persisted["state_json"]["source"] == "seed-b"


def test_get_current_sheet_context_prefers_persisted_current_state_snapshot(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order(message_id="msg-current-state-snapshot-002")
    draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json=_semantic_initial_draft(order["id"], source="manual_draft"),
        draft_state="draft_ready",
        edited_by="tester",
    )
    workflow_state_service.refresh_workflow_state(order["id"])
    persisted = order_current_state_service.get_current_state_payload(order["id"])

    assert isinstance(persisted, dict)

    def _unexpected_rebuild(*_args, **_kwargs):
        raise AssertionError("uncached current-sheet builder should not run when snapshot exists")

    monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", _unexpected_rebuild)

    current = order_service.get_current_sheet_context(order["id"])

    assert current == persisted


def test_get_current_sheet_context_rebuilds_when_current_state_snapshot_missing(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order(message_id="msg-current-state-snapshot-003")
    order_current_state_service.delete_current_state(order["id"])
    calls = {"count": 0}
    original_builder = order_service._build_current_sheet_context_uncached

    def _wrapped_builder(*args, **kwargs):
        calls["count"] += 1
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", _wrapped_builder)

    current = order_service.get_current_sheet_context(order["id"])
    snapshot = order_current_state_service.get_current_state_payload(order["id"])

    assert isinstance(current, dict)
    assert calls["count"] == 1
    assert isinstance(snapshot, dict)
    assert snapshot["resolved_week_id"] == current["resolved_week_id"]


def test_get_current_sheet_context_rebuilds_when_persisted_current_state_lines_timestamp_stale(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order(message_id="msg-current-state-snapshot-stale-lines-001")
    saved_draft = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json=_semantic_initial_draft(order["id"], source="manual_draft"),
        draft_state="draft_ready",
        edited_by="tester",
    )
    workflow_state_service.refresh_workflow_state(order["id"])
    stale_snapshot = order_current_state_service.get_current_state_payload(order["id"])

    assert isinstance(saved_draft, dict)
    assert isinstance(stale_snapshot, dict)

    monkeypatch.setattr(workflow_state_service, "refresh_workflow_state", lambda *_args, **_kwargs: None)
    assert order_service.update_lines(
        order["id"],
        [
            {
                "id": "line-stale-1",
                "line_id": "line-stale-1",
                "date": "2026-03-22",
                "daypart": "朝",
                "menu_name": "Menu A",
                "diet_type": None,
                "area_id": None,
                "bag_type": None,
                "quantity_original": 7,
                "quantity_corrected": None,
                "change_note": None,
            }
        ],
    ) is True

    order_current_state_service.persist_current_state(
        order_id=order["id"],
        state_json=stale_snapshot,
        draft_id=str(saved_draft.get("id") or "").strip() or None,
        evidence_run_id=str(stale_snapshot.get("base_evidence_run_id") or "").strip() or None,
    )

    calls = {"count": 0}
    original_builder = order_service._build_current_sheet_context_uncached

    def _wrapped_builder(*args, **kwargs):
        calls["count"] += 1
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", _wrapped_builder)

    current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    assert calls["count"] == 1
    assert current["order_payload"]["lines_updated_at"] != stale_snapshot["order_payload"]["lines_updated_at"]


def test_get_current_sheet_context_rebuilds_when_persisted_current_state_evidence_changes(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order(message_id="msg-current-state-snapshot-stale-evidence-001")
    saved_draft = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json=_semantic_initial_draft(order["id"], source="manual_draft"),
        draft_state="draft_ready",
        edited_by="tester",
    )
    workflow_state_service.refresh_workflow_state(order["id"])
    persisted = order_current_state_service.get_current_state_payload(order["id"])

    assert isinstance(saved_draft, dict)
    assert isinstance(persisted, dict)

    persisted["base_evidence_run_id"] = "OEV-old"
    order_current_state_service.persist_current_state(
        order_id=order["id"],
        state_json=persisted,
        draft_id=str(saved_draft.get("id") or "").strip() or None,
        evidence_run_id="OEV-old",
    )

    monkeypatch.setattr(order_service, "get_latest_ocr_evidence_run", lambda *_args, **_kwargs: {"id": "OEV-new"})
    calls = {"count": 0}
    original_builder = order_service._build_current_sheet_context_uncached

    def _wrapped_builder(*args, **kwargs):
        calls["count"] += 1
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", _wrapped_builder)

    current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    assert calls["count"] == 1


def test_get_current_sheet_context_does_not_reuse_transient_current_state_snapshot(monkeypatch) -> None:
    order_service.clear_all()
    order = _seed_order(message_id="msg-current-state-snapshot-004")
    order_current_state_service.persist_current_state(
        order_id=order["id"],
        state_json={
            "order_id": order["id"],
            "draft_id": None,
            "source": "weekly_menu",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", ""]],
            "row_ids": ["row-1"],
            "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
        },
        draft_id=None,
        evidence_run_id=None,
    )
    calls = {"count": 0}

    def _rebuilt_context(*_args, **_kwargs):
        calls["count"] += 1
        return {
            "order_id": order["id"],
            "draft_id": None,
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
            "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
        }

    monkeypatch.setattr(order_service, "_build_current_sheet_context_uncached", _rebuilt_context)

    current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    assert calls["count"] == 1
    assert current["source"] == "weekly_menu+ocr_payload"
    assert current["rows"][0][3] == "5"


def test_refresh_workflow_state_moves_from_choice_required_to_apply_ready_after_facility_choice(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-001", facility_hint=None, week_hint="2026-03")
    _persist_evidence(order["id"], facility_choice_required=True)
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
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
    assert next_workflow.get("state") == "draft_blocked"
    blockers = next_workflow.get("apply_gate", {}).get("blockers") or []
    assert "column_mapping_choice_required" not in blockers
    assert "draft_rows_empty" in blockers
    assert next_workflow.get("apply_gate", {}).get("can_apply") is False
    assert "column_mapping" not in (
        next_workflow.get("candidate_resolution", {}).get("gate_summary", {}).get("choice_required_types") or []
    )


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


def test_refresh_workflow_state_ignores_order_bound_first_pass_job_as_reparse_state(tmp_path):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-first-pass-order-job", facility_hint="FAC00001", week_hint="2026-03")
    job_id = f"OCR-{order['id']}"
    output_path = tmp_path / "first_pass_order_job_output.json"
    output_path.write_text('{"status":"done","table_raw":"|03/22|朝|Menu A|"}', encoding="utf-8")
    create_job(job_id, input_reference=order["document"], status="done")
    update_job(
        job_id,
        status="done",
        output_reference=f"file://{output_path}",
        metrics={"request_mode": "ingest_first_pass", "processing_stage": "ocr_pipeline"},
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "uploaded"
    assert (workflow.get("reparse_state") or {}).get("status") == "idle"


def test_list_workflow_states_serializes_within_session():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-list-001", facility_hint="FAC00001", week_hint="2026-03")

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    listed = workflow_state_service.list_workflow_states([order["id"]])

    assert listed[order["id"]]["order_id"] == order["id"]
    assert listed[order["id"]]["state"] == workflow["state"]


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
        "_build_menu_context_from_current_sheet_context",
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
        "_build_menu_context_from_current_sheet_context",
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
        lambda _order_id: _semantic_initial_draft(order["id"]),
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["primary_action"] == "apply_draft"
    assert "semantic_shell_only" not in (workflow["apply_gate"]["blockers"] or [])


def test_refresh_workflow_state_uses_position_fallback_for_missing_template_grid(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-position-fallback", facility_hint="FAC00001", week_hint="2026-03")
    rows = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]
    _persist_evidence(
        order["id"],
        extra_payload={
            "template_resolution": None,
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
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
        lambda _order_id: _semantic_initial_draft(
            order["id"],
            fields=[
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_2f",
                "qty.regular_3f",
                "qty.soft_2f",
                "qty.soft_3f",
                "qty.mixer_2f",
                "qty.mixer_3f",
                "remarks",
            ],
            header=rows[0],
            rows=[rows[1]],
        ),
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["primary_action"] == "apply_draft"
    assert "semantic_shell_only" not in (workflow["apply_gate"]["blockers"] or [])
    assert workflow["candidate_resolution"]["resolutions"]["column_mapping"]["decision_source"] == "position_fallback"


def test_refresh_workflow_state_keeps_numeric_review_when_position_fallback_has_high_risk_numeric_issues(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-position-fallback-high-risk", facility_hint="FAC00001", week_hint="2026-03")
    rows = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]
    _persist_evidence(
        order["id"],
        extra_payload={
            "template_resolution": None,
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
            "failed_cells": [{"row_index": 1, "col_index": 3, "reason": "merged_numeric_cell"}],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] in {"review_required", "semantic_shell_only", "draft_blocked"}
    assert "numeric_trust_low" in (workflow["apply_gate"]["warnings"] or [])
    assert workflow["candidate_resolution"]["resolutions"]["column_mapping"]["decision_source"] == "position_fallback"


def test_refresh_workflow_state_keeps_partial_position_fallback_apply_blocked(monkeypatch):
    config_service.reload_configs()
    order_service.clear_all()
    order = _seed_order(
        message_id="msg-workflow-state-position-fallback-partial",
        facility_hint="FAC00005",
        week_hint="2026-03",
    )
    rows = [
        ["日付", "", "区 分", "", "献立", "軟菜", "* # は", "熱食 【 軟菜 】", "", "変更1", "変更2", "備考欄"],
        ["", "", "", "", "", "", "", "茶室", "魚袋", "", "", ""],
        ["", "3/22\n(日)", "体", "学歴\nEND", "Menu A", "57", "2", "", "", "", "", ""],
        ["", "", "", "▼¥", "Menu B", "58", "4", "", "", "", "", ""],
    ]
    _persist_evidence(
        order["id"],
        extra_payload={
            "template_resolution": None,
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] in {"review_required", "semantic_shell_only", "draft_blocked"}
    assert workflow["candidate_resolution"]["resolutions"]["column_mapping"]["decision_source"] == "position_fallback"
    assert workflow["candidate_resolution"]["resolutions"]["column_mapping"]["partial_quantity_mapping"] is True
    assert workflow["apply_gate"]["can_apply"] is False
    assert "sheet_quantity_column_unmapped" in (workflow["apply_gate"]["blockers"] or [])


def test_refresh_workflow_state_blocks_malformed_raw_ocr_projection(monkeypatch):
    config_service.reload_configs()
    order_service.clear_all()
    order = _seed_order(
        message_id="msg-workflow-state-structural-projection-001",
        facility_hint="FAC00005",
        week_hint="2026-04",
    )
    rows = [
        ["", "日付", "区 分", "", "献立", "##", "44日", "禁食【軟菜】", "", "備考欄"],
        ["", "", "", "", "", "", "", "肉禁", "魚禁", ""],
        ["", "4/5\n(日)", "ま", "...", "\"", "0", "0", "", "", ""],
        ["", "", "", "***", "&", "0", "0", "", "", ""],
        ["", "", "口", "VT", "9", "58", "2", "", "", ""],
        ["", "", "", "OK", "<", "58", "2", "", "", ""],
    ]
    _persist_evidence(
        order["id"],
        extra_payload={
            "template_id": "fax_layout_soft_packaging_forbidden_v1",
            "template_resolution": None,
            "quantity_subgrid_passes": [],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-04",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "draft_blocked"
    assert workflow["apply_gate"]["can_apply"] is False
    assert "sheet_quantity_column_unmapped" in (workflow["apply_gate"]["blockers"] or [])


def test_refresh_workflow_state_does_not_use_position_fallback_when_facility_conflicts_with_evidence(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-position-fallback-facility-conflict", facility_hint="FAC00001", week_hint="2026-03")
    rows = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]
    _persist_evidence(
        order["id"],
        extra_payload={
            "template_resolution": None,
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
            "facility_candidates": [
                {"facility_id": "FAC99999", "facility_name": "別施設", "score": 0.96},
                {"facility_id": "FAC88888", "facility_name": "次点施設", "score": 0.52},
            ],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] != "apply_ready"
    assert workflow["candidate_resolution"]["resolutions"]["column_mapping"].get("decision_source") != "position_fallback"


def test_refresh_workflow_state_requires_choice_for_stale_ambiguous_position_fallback(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-position-fallback-stale-ambiguous", facility_hint="FAC00001", week_hint="2026-03")
    rows_a = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "5", "4", "3", "2", "1", "1", ""],
    ]
    rows_b = [
        ["日付", "区分", "メニュー", "補助", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/22", "朝", "献立A", "", "5", "4", "3", "2", "1", "1", ""],
    ]
    _persist_evidence(
        order["id"],
        extra_payload={
            "template_resolution": {
                "resolved_template_id": "fax_layout_floor_2f3f_v1",
                "confidence": 0.99,
                "blocked": False,
                "blocked_reasons": [],
                "decision_source": "position_fallback",
            },
            "column_mapping_resolution": {
                "resolved_value": "3:qty.regular_2f|4:qty.regular_3f|5:qty.soft_2f|6:qty.soft_3f|7:qty.mixer_2f|8:qty.mixer_3f",
                "resolved_column_mapping_id": "3:qty.regular_2f|4:qty.regular_3f|5:qty.soft_2f|6:qty.soft_3f|7:qty.mixer_2f|8:qty.mixer_3f",
                "confidence": 0.99,
                "blocked": False,
                "blocked_reasons": [],
                "decision_source": "position_fallback",
            },
            "column_mapping_candidates": [
                {
                    "value": "3:qty.regular_2f|4:qty.regular_3f|5:qty.soft_2f|6:qty.soft_3f|7:qty.mixer_2f|8:qty.mixer_3f",
                    "label": "常食2F / 常食3F",
                    "score": 0.99,
                    "decision_source": "position_fallback",
                },
                {
                    "value": "4:qty.regular_2f|5:qty.regular_3f|6:qty.soft_2f|7:qty.soft_3f|8:qty.mixer_2f|9:qty.mixer_3f",
                    "label": "常食2F / 常食3F",
                    "score": 0.99,
                    "decision_source": "position_fallback",
                },
            ],
            "table_box": [0.0, 0.0, 1.0, 1.0],
            "grid_column_edges": [0.0, 0.1, 0.2, 0.3, 0.4],
            "grid_row_edges": [0.0, 0.5, 1.0],
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows_a),
                    "col_count": len(rows_a[0]),
                    "rows": rows_a,
                    "cells": _structured_cells(rows_a),
                },
                {
                    "page_index": 1,
                    "table_id": "page1_table2",
                    "row_count": len(rows_b),
                    "col_count": len(rows_b[0]),
                    "rows": rows_b,
                    "cells": _structured_cells(rows_b),
                },
            ],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] != "apply_ready"
    assert workflow["apply_gate"]["can_apply"] is False
    assert workflow["candidate_resolution"]["resolutions"]["column_mapping"]["decision_source"] == "position_fallback"
    assert workflow["candidate_resolution"]["resolutions"]["column_mapping"]["requires_user_choice"] is True
    assert "column_mapping_choice_required" in (
        workflow["candidate_resolution"]["resolutions"]["column_mapping"]["blocked_reasons"] or []
    )


def test_refresh_workflow_state_suppresses_stale_draft_layout_blockers_when_position_fallback_is_ready(monkeypatch):
    order_service.clear_all()
    order = _seed_order(
        message_id="msg-workflow-state-position-fallback-stale-draft-blockers",
        facility_hint="FAC00002",
        week_hint="2026-03",
    )
    rows = [
        ["日 付", "区 分", "", "献立", "常食", "", "事故", "", "変更の", "変更の", "備考欄"],
        ["", "", "", "", "", "", "肉款", "魚炊", "", "", ""],
        ["3/22", "\"", "VF", "Menu A", "23", "", "", "", "", "", ""],
        ["", "", "48", "Menu B", "27", "", "", "", "", "", ""],
    ]
    evidence = order_service.persist_ocr_evidence_run(
        order["id"],
        {
            "input_reference": "gs://bucket/orders/order.pdf",
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                    "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
                }
            ],
        },
    )
    assert isinstance(evidence, dict)
    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu",
            "fields": [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_x",
                "qty.no_meat_x",
                "qty.no_fish_x",
                "qty.change_1_x",
                "qty.change_2_x",
                "remarks",
            ],
            "header": ["日付", "区分", "メニュー", "常食", "肉禁", "魚禁", "変更1", "変更2", "備考"],
            "rows": [
                ["03/22", "breakfast", "Menu A", "23", "", "", "", "", ""],
                ["03/22", "lunch", "Menu B", "27", "", "", "", "", ""],
            ],
            "row_ids": ["draft-row-1", "draft-row-2"],
        },
        draft_state="draft_ready",
        blockers=["template_unresolved"],
        warnings=[
            "template_unresolved",
            "sheet_quantity_column_unmapped",
            "ocr_evidence_recovery_required",
            "sheet_ocr_review_required",
        ],
    )
    assert saved is not None
    monkeypatch.setattr(order_service, "_maybe_refresh_semantic_sheet_draft", lambda _order_id, draft: draft)
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["candidate_resolution"]["resolutions"]["column_mapping"]["decision_source"] == "position_fallback"
    assert workflow["state"] == "apply_ready"
    assert workflow["apply_gate"]["can_apply"] is True
    assert "template_unresolved" not in (workflow["apply_gate"]["blockers"] or [])
    assert "sheet_quantity_column_unmapped" not in (workflow["apply_gate"]["blockers"] or [])
    assert "ocr_evidence_recovery_required" not in (workflow["apply_gate"]["blockers"] or [])


def test_refresh_workflow_state_keeps_active_evidence_layout_blockers_for_authoritative_draft(monkeypatch):
    order_service.clear_all()
    order = _seed_order(
        message_id="msg-workflow-state-position-fallback-current-layout-blockers",
        facility_hint="FAC00002",
        week_hint="2026-03",
    )
    rows = [
        ["日 付", "区 分", "", "献立", "常食", "", "事故", "", "変更の", "変更の", "備考欄"],
        ["", "", "", "", "", "", "肉款", "魚炊", "", "", ""],
        ["3/22", "\"", "VF", "Menu A", "23", "", "", "", "", "", ""],
        ["", "", "48", "Menu B", "27", "", "", "", "", "", ""],
    ]
    evidence = order_service.persist_ocr_evidence_run(
        order["id"],
        {
            "input_reference": "gs://bucket/orders/order.pdf",
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "row_count": len(rows),
                    "col_count": len(rows[0]),
                    "rows": rows,
                    "cells": _structured_cells(rows),
                }
            ],
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png",
                    "layout_overlay_uri": "gs://bucket/orders/page1-layout.png",
                }
            ],
        },
    )
    assert isinstance(evidence, dict)
    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "weekly_menu",
            "fields": [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.regular_x",
                "qty.no_meat_x",
                "qty.no_fish_x",
                "qty.change_1_x",
                "qty.change_2_x",
                "remarks",
            ],
            "header": ["日付", "区分", "メニュー", "常食", "肉禁", "魚禁", "変更1", "変更2", "備考"],
            "rows": [
                ["03/22", "breakfast", "Menu A", "23", "", "", "", "", ""],
                ["03/22", "lunch", "Menu B", "27", "", "", "", "", ""],
            ],
            "row_ids": ["draft-row-1", "draft-row-2"],
            "base_evidence_run_id": evidence["id"],
        },
        base_evidence_run_id=evidence["id"],
        draft_state="draft_ready",
        blockers=["template_unresolved"],
        warnings=[
            "template_unresolved",
            "sheet_quantity_column_unmapped",
            "ocr_evidence_recovery_required",
            "sheet_ocr_review_required",
        ],
    )
    assert saved is not None
    monkeypatch.setattr(order_service, "_maybe_refresh_semantic_sheet_draft", lambda _order_id, draft: draft)
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] in {"draft_blocked", "review_required"}
    assert "template_unresolved" in (workflow["apply_gate"]["blockers"] or [])
    assert "sheet_quantity_column_unmapped" in (workflow["apply_gate"]["blockers"] or [])


def test_refresh_workflow_state_returns_new_evidence_available_when_latest_evidence_differs_from_draft(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-new-evidence", facility_hint="FAC00001", week_hint="2026-03")
    first = _persist_evidence(order["id"], extra_payload={"table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|5|"})
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
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
    assert workflow["state"] == "apply_ready"
    assert workflow["primary_action"] == "apply_draft"
    assert workflow["evidence_run_id"] == first["id"]
    assert workflow["active_evidence_run_id"] == first["id"]
    assert workflow["candidate_prompt_visible"] is True
    assert workflow["candidate_evidence_run_id"] == second["id"]
    assert workflow["current_sheet_revision_id"] == saved["id"]
    assert workflow["apply_gate"]["can_apply"] is True


def test_refresh_workflow_state_new_evidence_available_retains_apply_readiness_for_clean_draft(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-new-evidence-clean-draft", facility_hint="FAC00001", week_hint="2026-03")
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
        blockers=[],
        warnings=[],
    )
    assert isinstance(saved, dict)

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
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
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
    assert workflow["candidate_prompt_visible"] is True
    assert workflow["candidate_evidence_run_id"] == second["id"]
    assert workflow["apply_gate"]["can_apply"] is True
    assert (workflow["apply_gate"].get("blockers") or []) == []


def test_refresh_workflow_state_uses_candidate_sheet_state_to_block_unpreviewable_candidate(monkeypatch):
    order_service.clear_all()
    order_id = "ORD-WORKFLOW-CAND-BLOCK-001"
    active_evidence_run = {
        "id": "OEV-ACTIVE-BLOCK-001",
        "payload_json": {},
        "created_at": datetime.utcnow().isoformat(),
    }
    candidate_evidence_run = {
        "id": "OEV-CAND-BLOCK-001",
        "payload_json": {},
        "created_at": datetime.utcnow().isoformat(),
    }
    draft_record = {
        "id": "ODR-BLOCK-001",
        "base_evidence_run_id": active_evidence_run["id"],
        "draft_sheet_json": {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
    }
    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "draft_record": draft_record,
            "draft_payload": draft_record["draft_sheet_json"],
            "fields": list(draft_record["draft_sheet_json"]["fields"]),
            "header": list(draft_record["draft_sheet_json"]["header"]),
            "base_evidence_run_id": active_evidence_run["id"],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_load_order_payload",
        lambda _order_id: {
            "id": order_id,
            "status": "要確認",
            "facility": "FAC00001",
            "week_value": "2026-03@2026-03-22~2026-03-28",
            "received_at": datetime(2026, 3, 22, 9, 0, 0).isoformat(),
        },
    )
    monkeypatch.setattr(
        workflow_state_service.ocr_evidence_service,
        "get_latest_evidence_run",
        lambda _order_id: active_evidence_run,
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_resolve_active_evidence_run",
        lambda *_args, **_kwargs: active_evidence_run,
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_augment_workflow_evidence_run",
        lambda evidence_run, **_kwargs: evidence_run,
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_resolve_candidate_evidence_run",
        lambda *_args, **_kwargs: candidate_evidence_run,
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    monkeypatch.setattr(
        workflow_state_service.candidate_resolution_service,
        "resolve_order_candidates",
        lambda **_kwargs: {"critical_choices": [], "resolutions": {}},
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_merge_selected_decisions_into_resolution",
        lambda resolution, decisions: (resolution, set()),
    )
    monkeypatch.setattr(
        workflow_state_service.apply_gate_service,
        "evaluate_apply_gate",
        lambda **_kwargs: {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []},
    )
    monkeypatch.setattr(workflow_state_service, "_latest_confirmed_snapshot_id", lambda _order_id: None)
    monkeypatch.setattr(order_service, "get_order_review_summary", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        order_service,
        "candidate_sheet_state",
        lambda *_args, **_kwargs: {
            "candidate_evidence_run_id": candidate_evidence_run["id"],
            "current_sheet_revision_id": "REV-BLOCK-001",
            "candidate_preview_available": False,
            "candidate_has_meaningful_diff": False,
            "candidate_preview_error": "template_unresolved",
            "candidate_preview_draft": None,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order_id)

    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["candidate_evidence_run_id"] is None
    assert workflow["candidate_sheet_state"]["candidate_evidence_run_id"] == candidate_evidence_run["id"]
    assert workflow["candidate_sheet_state"]["candidate_preview_available"] is False
    assert workflow["candidate_sheet_state"]["candidate_preview_error"] == "template_unresolved"


def test_refresh_workflow_state_hides_candidate_prompt_when_candidate_preview_revision_mismatches_current(monkeypatch):
    order_service.clear_all()
    order_id = "ORD-WORKFLOW-CAND-REV-MISMATCH-001"
    active_evidence_run = {
        "id": "OEV-ACTIVE-REV-MISMATCH-001",
        "payload_json": {},
        "created_at": datetime.utcnow().isoformat(),
    }
    candidate_evidence_run = {
        "id": "OEV-CAND-REV-MISMATCH-001",
        "payload_json": {},
        "created_at": datetime.utcnow().isoformat(),
    }
    draft_record = {
        "id": "ODR-REV-MISMATCH-001",
        "base_evidence_run_id": active_evidence_run["id"],
        "draft_sheet_json": {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
    }
    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "draft_record": draft_record,
            "draft_payload": draft_record["draft_sheet_json"],
            "fields": list(draft_record["draft_sheet_json"]["fields"]),
            "header": list(draft_record["draft_sheet_json"]["header"]),
            "current_sheet_revision_id": draft_record["id"],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_load_order_payload",
        lambda _order_id: {
            "id": order_id,
            "status": "要確認",
            "facility": "FAC00001",
            "week_value": "2026-03@2026-03-22~2026-03-28",
            "received_at": datetime(2026, 3, 22, 9, 0, 0).isoformat(),
        },
    )
    monkeypatch.setattr(
        workflow_state_service.ocr_evidence_service,
        "get_latest_evidence_run",
        lambda _order_id: active_evidence_run,
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_resolve_active_evidence_run",
        lambda *_args, **_kwargs: active_evidence_run,
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_augment_workflow_evidence_run",
        lambda evidence_run, **_kwargs: evidence_run,
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_resolve_candidate_evidence_run",
        lambda *_args, **_kwargs: candidate_evidence_run,
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    monkeypatch.setattr(
        workflow_state_service.candidate_resolution_service,
        "resolve_order_candidates",
        lambda **_kwargs: {"critical_choices": [], "resolutions": {}},
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_merge_selected_decisions_into_resolution",
        lambda resolution, decisions: (resolution, set()),
    )
    monkeypatch.setattr(
        workflow_state_service.apply_gate_service,
        "evaluate_apply_gate",
        lambda **_kwargs: {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []},
    )
    monkeypatch.setattr(workflow_state_service, "_latest_confirmed_snapshot_id", lambda _order_id: None)
    monkeypatch.setattr(order_service, "get_order_review_summary", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        order_service,
        "candidate_sheet_state",
        lambda *_args, **_kwargs: {
            "current_sheet_revision_id": "ODR-OTHER-REVISION",
            "candidate_evidence_run_id": candidate_evidence_run["id"],
            "candidate_preview_available": True,
            "candidate_has_meaningful_diff": True,
            "candidate_preview_error": None,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order_id)

    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["current_sheet_revision_id"] == draft_record["id"]
    assert workflow["candidate_prompt_visible"] is False
    assert workflow["candidate_evidence_run_id"] is None
    assert workflow["candidate_sheet_state"]["candidate_evidence_run_id"] == candidate_evidence_run["id"]


def test_refresh_workflow_state_hides_new_candidate_after_acknowledging_current_sheet(monkeypatch):
    order_service.clear_all()
    order_id = "ORD-WORKFLOW-ACK-001"
    active_evidence_run = {"id": "OEV-ACTIVE-001", "payload_json": {}, "created_at": datetime.utcnow().isoformat()}
    candidate_evidence_run = {"id": "OEV-CAND-001", "payload_json": {}, "created_at": datetime.utcnow().isoformat()}
    draft_record = {
        "id": "ODR-001",
        "base_evidence_run_id": active_evidence_run["id"],
        "draft_sheet_json": {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
    }
    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "draft_record": draft_record,
            "draft_payload": draft_record["draft_sheet_json"],
            "fields": list(draft_record["draft_sheet_json"]["fields"]),
            "header": list(draft_record["draft_sheet_json"]["header"]),
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_load_order_payload",
        lambda _order_id: {
            "id": order_id,
            "status": "要確認",
            "facility": "FAC00001",
            "week_value": "2026-03@2026-03-22~2026-03-28",
            "received_at": datetime(2026, 3, 22, 9, 0, 0).isoformat(),
        },
    )
    monkeypatch.setattr(workflow_state_service.ocr_evidence_service, "get_latest_evidence_run", lambda _order_id: active_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_resolve_active_evidence_run", lambda *_args, **_kwargs: active_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_augment_workflow_evidence_run", lambda evidence_run, **_kwargs: evidence_run)
    monkeypatch.setattr(workflow_state_service, "_resolve_candidate_evidence_run", lambda *_args, **_kwargs: candidate_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_build_menu_context_from_current_sheet_context", lambda **_kwargs: {"month_id": "2026-03", "weekly_menu_missing": False, "menu_entries_missing": False, "entries_count": 21})
    monkeypatch.setattr(workflow_state_service.candidate_resolution_service, "resolve_order_candidates", lambda **_kwargs: {"critical_choices": [], "resolutions": {}})
    monkeypatch.setattr(workflow_state_service, "_merge_selected_decisions_into_resolution", lambda resolution, decisions: (resolution, set()))
    monkeypatch.setattr(workflow_state_service.apply_gate_service, "evaluate_apply_gate", lambda **_kwargs: {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []})
    monkeypatch.setattr(workflow_state_service, "_latest_confirmed_snapshot_id", lambda _order_id: None)
    monkeypatch.setattr(order_service, "get_order_review_summary", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        order_service,
        "candidate_sheet_state",
        lambda *_args, **_kwargs: {
            "current_sheet_revision_id": "OCRREV-001",
            "candidate_preview_available": True,
            "candidate_has_meaningful_diff": True,
            "candidate_preview_error": None,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order_id)
    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["candidate_prompt_visible"] is True
    assert workflow["candidate_evidence_run_id"] == candidate_evidence_run["id"]

    decision = critical_decision_service.acknowledge_candidate_evidence(
        order_id,
        candidate_evidence_run["id"],
        selected_by="tester",
    )
    assert isinstance(decision, dict)

    refreshed = workflow_state_service.refresh_workflow_state(order_id)

    assert isinstance(refreshed, dict)
    assert refreshed["state"] == "apply_ready"
    assert refreshed["candidate_prompt_visible"] is False
    assert refreshed["candidate_evidence_run_id"] is None
    assert refreshed["acknowledged_candidate_evidence_run_id"] == candidate_evidence_run["id"]
    assert refreshed["active_evidence_run_id"] == active_evidence_run["id"]
    assert refreshed["apply_gate"]["can_apply"] is True


def test_refresh_workflow_state_reprompts_when_new_candidate_arrives_after_acknowledgement(monkeypatch):
    order_service.clear_all()
    order_id = "ORD-WORKFLOW-ACK-002"
    base_time = datetime.utcnow()
    active_evidence_run = {"id": "OEV-ACTIVE-002", "payload_json": {}, "created_at": (base_time - timedelta(hours=2)).isoformat()}
    current_candidate = {"id": "OEV-CAND-002", "payload_json": {}, "created_at": (base_time - timedelta(hours=1)).isoformat()}
    draft_record = {
        "id": "ODR-002",
        "base_evidence_run_id": active_evidence_run["id"],
        "draft_sheet_json": {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
    }
    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "draft_record": draft_record,
            "draft_payload": draft_record["draft_sheet_json"],
            "fields": list(draft_record["draft_sheet_json"]["fields"]),
            "header": list(draft_record["draft_sheet_json"]["header"]),
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_load_order_payload",
        lambda _order_id: {
            "id": order_id,
            "status": "要確認",
            "facility": "FAC00001",
            "week_value": "2026-03@2026-03-22~2026-03-28",
            "received_at": datetime(2026, 3, 22, 9, 0, 0).isoformat(),
        },
    )
    monkeypatch.setattr(workflow_state_service.ocr_evidence_service, "get_latest_evidence_run", lambda _order_id: active_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_resolve_active_evidence_run", lambda *_args, **_kwargs: active_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_augment_workflow_evidence_run", lambda evidence_run, **_kwargs: evidence_run)
    monkeypatch.setattr(
        workflow_state_service,
        "_resolve_candidate_evidence_run",
        lambda *_args, **_kwargs: current_candidate,
    )
    monkeypatch.setattr(workflow_state_service, "_build_menu_context_from_current_sheet_context", lambda **_kwargs: {"month_id": "2026-03", "weekly_menu_missing": False, "menu_entries_missing": False, "entries_count": 21})
    monkeypatch.setattr(workflow_state_service.candidate_resolution_service, "resolve_order_candidates", lambda **_kwargs: {"critical_choices": [], "resolutions": {}})
    monkeypatch.setattr(workflow_state_service, "_merge_selected_decisions_into_resolution", lambda resolution, decisions: (resolution, set()))
    monkeypatch.setattr(workflow_state_service.apply_gate_service, "evaluate_apply_gate", lambda **_kwargs: {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []})
    monkeypatch.setattr(workflow_state_service, "_latest_confirmed_snapshot_id", lambda _order_id: None)
    monkeypatch.setattr(order_service, "get_order_review_summary", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        order_service,
        "candidate_sheet_state",
        lambda *_args, **_kwargs: {
            "current_sheet_revision_id": "OCRREV-002",
            "candidate_preview_available": True,
            "candidate_has_meaningful_diff": True,
            "candidate_preview_error": None,
        },
    )

    critical_decision_service.acknowledge_candidate_evidence(
        order_id,
        current_candidate["id"],
        selected_by="tester",
    )
    current_candidate = {
        "id": "OEV-CAND-003",
        "payload_json": {},
        "created_at": (datetime.utcnow() + timedelta(seconds=1)).isoformat(),
    }

    refreshed = workflow_state_service.refresh_workflow_state(order_id)

    assert isinstance(refreshed, dict)
    assert refreshed["state"] == "apply_ready"
    assert refreshed["candidate_prompt_visible"] is True
    assert refreshed["candidate_evidence_run_id"] == "OEV-CAND-003"
    assert refreshed["acknowledged_candidate_evidence_run_id"] == "OEV-CAND-002"
    assert refreshed["active_evidence_run_id"] == active_evidence_run["id"]


def test_refresh_workflow_state_suppresses_older_pending_candidate_ids_after_acknowledgement(monkeypatch):
    order_service.clear_all()
    order_id = "ORD-WORKFLOW-ACK-003"
    base_time = datetime.utcnow()
    active_evidence_run = {"id": "OEV-ACTIVE-003", "payload_json": {}, "created_at": (base_time - timedelta(hours=3)).isoformat()}
    visible_candidate = {"id": "OEV-CAND-004", "payload_json": {}, "created_at": (base_time - timedelta(hours=2)).isoformat()}
    older_pending_candidate = {"id": "OEV-CAND-005", "payload_json": {}, "created_at": (base_time - timedelta(hours=1)).isoformat()}
    draft_record = {
        "id": "ODR-003",
        "base_evidence_run_id": active_evidence_run["id"],
        "draft_sheet_json": {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
    }
    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "draft_record": draft_record,
            "draft_payload": draft_record["draft_sheet_json"],
            "fields": list(draft_record["draft_sheet_json"]["fields"]),
            "header": list(draft_record["draft_sheet_json"]["header"]),
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_load_order_payload",
        lambda _order_id: {
            "id": order_id,
            "status": "要確認",
            "facility": "FAC00001",
            "week_value": "2026-03@2026-03-22~2026-03-28",
            "received_at": datetime(2026, 3, 22, 9, 0, 0).isoformat(),
        },
    )
    monkeypatch.setattr(workflow_state_service.ocr_evidence_service, "get_latest_evidence_run", lambda _order_id: active_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_resolve_active_evidence_run", lambda *_args, **_kwargs: active_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_augment_workflow_evidence_run", lambda evidence_run, **_kwargs: evidence_run)

    current_candidate = visible_candidate
    monkeypatch.setattr(
        workflow_state_service,
        "_resolve_candidate_evidence_run",
        lambda *_args, **_kwargs: current_candidate,
    )
    monkeypatch.setattr(workflow_state_service, "_build_menu_context_from_current_sheet_context", lambda **_kwargs: {"month_id": "2026-03", "weekly_menu_missing": False, "menu_entries_missing": False, "entries_count": 21})
    monkeypatch.setattr(workflow_state_service.candidate_resolution_service, "resolve_order_candidates", lambda **_kwargs: {"critical_choices": [], "resolutions": {}})
    monkeypatch.setattr(workflow_state_service, "_merge_selected_decisions_into_resolution", lambda resolution, decisions: (resolution, set()))
    monkeypatch.setattr(workflow_state_service.apply_gate_service, "evaluate_apply_gate", lambda **_kwargs: {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []})
    monkeypatch.setattr(workflow_state_service, "_latest_confirmed_snapshot_id", lambda _order_id: None)
    monkeypatch.setattr(order_service, "get_order_review_summary", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        order_service,
        "candidate_sheet_state",
        lambda *_args, **_kwargs: {
            "current_sheet_revision_id": "OCRREV-003",
            "candidate_preview_available": True,
            "candidate_has_meaningful_diff": True,
            "candidate_preview_error": None,
        },
    )

    initial = workflow_state_service.refresh_workflow_state(order_id)
    assert initial["state"] == "apply_ready"
    assert initial["candidate_prompt_visible"] is True
    assert initial["candidate_evidence_run_id"] == visible_candidate["id"]

    critical_decision_service.acknowledge_candidate_evidence(
        order_id,
        visible_candidate["id"],
        selected_by="tester",
    )
    current_candidate = older_pending_candidate

    refreshed = workflow_state_service.refresh_workflow_state(order_id)

    assert isinstance(refreshed, dict)
    assert refreshed["state"] == "apply_ready"
    assert refreshed["candidate_prompt_visible"] is False
    assert refreshed["candidate_evidence_run_id"] is None
    assert refreshed["acknowledged_candidate_evidence_run_id"] == visible_candidate["id"]


def test_refresh_workflow_state_does_not_prompt_when_candidate_preview_contract_is_unavailable(monkeypatch):
    order_service.clear_all()
    order_id = "ORD-WORKFLOW-CAND-CONTRACT-001"
    active_evidence_run = {"id": "OEV-ACTIVE-C-001", "payload_json": {}, "created_at": datetime.utcnow().isoformat()}
    candidate_evidence_run = {"id": "OEV-CAND-C-001", "payload_json": {}, "created_at": datetime.utcnow().isoformat()}
    draft_record = {
        "id": "ODR-C-001",
        "base_evidence_run_id": active_evidence_run["id"],
        "draft_sheet_json": {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
    }
    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "draft_record": draft_record,
            "draft_payload": draft_record["draft_sheet_json"],
            "fields": list(draft_record["draft_sheet_json"]["fields"]),
            "header": list(draft_record["draft_sheet_json"]["header"]),
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_load_order_payload",
        lambda _order_id: {
            "id": order_id,
            "status": "要確認",
            "facility": "FAC00001",
            "week_value": "2026-03@2026-03-22~2026-03-28",
            "received_at": datetime(2026, 3, 22, 9, 0, 0).isoformat(),
        },
    )
    monkeypatch.setattr(workflow_state_service.ocr_evidence_service, "get_latest_evidence_run", lambda _order_id: active_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_resolve_active_evidence_run", lambda *_args, **_kwargs: active_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_augment_workflow_evidence_run", lambda evidence_run, **_kwargs: evidence_run)
    monkeypatch.setattr(workflow_state_service, "_resolve_candidate_evidence_run", lambda *_args, **_kwargs: candidate_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_build_menu_context_from_current_sheet_context", lambda **_kwargs: {"month_id": "2026-03", "weekly_menu_missing": False, "menu_entries_missing": False, "entries_count": 21})
    monkeypatch.setattr(workflow_state_service.candidate_resolution_service, "resolve_order_candidates", lambda **_kwargs: {"critical_choices": [], "resolutions": {}})
    monkeypatch.setattr(workflow_state_service, "_merge_selected_decisions_into_resolution", lambda resolution, decisions: (resolution, set()))
    monkeypatch.setattr(workflow_state_service.apply_gate_service, "evaluate_apply_gate", lambda **_kwargs: {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []})
    monkeypatch.setattr(workflow_state_service, "_latest_confirmed_snapshot_id", lambda _order_id: None)
    monkeypatch.setattr(order_service, "get_order_review_summary", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        order_service,
        "candidate_sheet_state",
        lambda *_args, **_kwargs: {
            "current_sheet_revision_id": "OCRREV-C-001",
            "candidate_preview_available": False,
            "candidate_has_meaningful_diff": False,
            "candidate_preview_error": "candidate_preview_unavailable",
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order_id)

    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["candidate_evidence_run_id"] is None
    assert workflow["candidate_sheet_state"]["candidate_preview_available"] is False
    assert workflow["candidate_sheet_state"]["candidate_preview_error"] == "candidate_preview_unavailable"


def test_refresh_workflow_state_skips_candidate_preview_build_when_requested(monkeypatch):
    order_service.clear_all()
    order_id = "ORD-WORKFLOW-CAND-LIGHT-001"
    active_evidence_run = {"id": "OEV-ACTIVE-LIGHT-001", "payload_json": {}, "created_at": datetime.utcnow().isoformat()}
    candidate_evidence_run = {"id": "OEV-CAND-LIGHT-001", "payload_json": {}, "created_at": datetime.utcnow().isoformat()}
    draft_record = {
        "id": "ODR-LIGHT-001",
        "base_evidence_run_id": active_evidence_run["id"],
        "draft_sheet_json": {
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
        },
    }
    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "draft_record": draft_record,
            "draft_payload": draft_record["draft_sheet_json"],
            "fields": list(draft_record["draft_sheet_json"]["fields"]),
            "header": list(draft_record["draft_sheet_json"]["header"]),
            "base_evidence_run_id": active_evidence_run["id"],
            "current_sheet_revision_id": draft_record["id"],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_load_order_payload",
        lambda _order_id: {
            "id": order_id,
            "status": "要確認",
            "facility": "FAC00001",
            "week_value": "2026-03@2026-03-22~2026-03-28",
            "received_at": datetime(2026, 3, 22, 9, 0, 0).isoformat(),
        },
    )
    monkeypatch.setattr(workflow_state_service.ocr_evidence_service, "get_latest_evidence_run", lambda _order_id: active_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_resolve_active_evidence_run", lambda *_args, **_kwargs: active_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_augment_workflow_evidence_run", lambda evidence_run, **_kwargs: evidence_run)
    monkeypatch.setattr(workflow_state_service, "_resolve_candidate_evidence_run", lambda *_args, **_kwargs: candidate_evidence_run)
    monkeypatch.setattr(workflow_state_service, "_build_menu_context_from_current_sheet_context", lambda **_kwargs: {"month_id": "2026-03", "weekly_menu_missing": False, "menu_entries_missing": False, "entries_count": 21})
    monkeypatch.setattr(workflow_state_service.candidate_resolution_service, "resolve_order_candidates", lambda **_kwargs: {"critical_choices": [], "resolutions": {}})
    monkeypatch.setattr(workflow_state_service, "_merge_selected_decisions_into_resolution", lambda resolution, decisions: (resolution, set()))
    monkeypatch.setattr(workflow_state_service.apply_gate_service, "evaluate_apply_gate", lambda **_kwargs: {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []})
    monkeypatch.setattr(workflow_state_service, "_latest_confirmed_snapshot_id", lambda _order_id: None)
    monkeypatch.setattr(order_service, "get_order_review_summary", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        order_service,
        "candidate_sheet_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("candidate preview should be skipped")),
    )

    workflow = workflow_state_service.refresh_workflow_state(
        order_id,
        include_candidate_preview=False,
    )

    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["candidate_prompt_visible"] is False
    assert workflow["candidate_evidence_run_id"] is None
    assert workflow["current_sheet_revision_id"] == draft_record["id"]
    assert workflow["candidate_sheet_state"]["candidate_evidence_run_id"] == candidate_evidence_run["id"]
    assert workflow["candidate_sheet_state"]["candidate_preview_available"] is False
    assert workflow["candidate_sheet_state"]["candidate_has_meaningful_diff"] is False


def test_save_ocr_sheet_exact_invokes_authoritative_candidate_acknowledgement(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-save-ack", facility_hint="FAC00001", week_hint="2026-03")
    calls: list[tuple[str, str]] = []

    def _fake_acknowledge(order_id: str, *, selected_by: str):
        calls.append((order_id, selected_by))
        return {"state": "apply_ready"}

    monkeypatch.setattr(
        order_service,
        "_acknowledge_current_candidate_after_authoritative_action",
        _fake_acknowledge,
    )

    result, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F"],
        rows=[["03/22", "朝", "Menu A", "5"]],
        ui_mode="sheet",
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        row_ids=["row-1"],
    )

    assert error is None
    assert isinstance(result, dict)
    assert calls == [(order["id"], "save-ocr-sheet-exact")]


def test_acknowledge_current_candidate_evidence_uses_cached_candidate_and_lightweight_refresh(monkeypatch):
    order_service.clear_all()
    captured: dict[str, object] = {}

    def _fake_get_order_workflow_state(order_id: str, *, refresh: bool = False):
        captured["refresh"] = refresh
        assert order_id == "ORD-WORKFLOW-ACK-LIGHT-001"
        if refresh:
            raise AssertionError("candidate acknowledgement should not force a preview refresh before ack")
        return {
            "candidate_evidence_run_id": "OEV-CACHED-001",
            "current_sheet_revision_id": "ODR-CACHED-001",
        }

    def _fake_acknowledge(order_id: str, candidate_evidence_run_id: str, *, selected_by: str):
        captured["ack"] = (order_id, candidate_evidence_run_id, selected_by)
        return {"selected_value": candidate_evidence_run_id}

    def _fake_refresh(order_id: str, *, include_candidate_preview: bool = True, **_kwargs):
        captured["light_refresh"] = (order_id, include_candidate_preview)
        return {
            "state": "apply_ready",
            "candidate_prompt_visible": False,
            "current_sheet_revision_id": "ODR-CACHED-001",
        }

    monkeypatch.setattr(order_service, "get_order_workflow_state", _fake_get_order_workflow_state)
    monkeypatch.setattr(critical_decision_service, "acknowledge_candidate_evidence", _fake_acknowledge)
    monkeypatch.setattr(workflow_state_service, "refresh_workflow_state", _fake_refresh)

    workflow, error = order_service.acknowledge_current_candidate_evidence(
        "ORD-WORKFLOW-ACK-LIGHT-001",
        selected_by="tester",
    )

    assert error is None
    assert workflow == {
        "state": "apply_ready",
        "candidate_prompt_visible": False,
        "current_sheet_revision_id": "ODR-CACHED-001",
    }
    assert captured["refresh"] is False
    assert captured["ack"] == ("ORD-WORKFLOW-ACK-LIGHT-001", "OEV-CACHED-001", "tester")
    assert captured["light_refresh"] == ("ORD-WORKFLOW-ACK-LIGHT-001", False)


def test_apply_latest_draft_invokes_authoritative_candidate_acknowledgement(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-apply-ack", facility_hint="FAC00001", week_hint="2026-03")
    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F"],
        rows=[["03/22", "朝", "Menu A", "5"]],
        ui_mode="sheet",
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        row_ids=["row-1"],
    )
    assert error is None
    assert isinstance(saved, dict)

    calls: list[tuple[str, str]] = []

    def _fake_acknowledge(order_id: str, *, selected_by: str):
        calls.append((order_id, selected_by))
        return {"state": "apply_ready"}

    monkeypatch.setattr(
        order_service,
        "_acknowledge_current_candidate_after_authoritative_action",
        _fake_acknowledge,
    )

    applied, apply_error = order_service.apply_latest_draft(order["id"])

    assert apply_error is None
    assert isinstance(applied, dict)
    assert calls == [(order["id"], "apply-latest-draft")]


def test_authoritative_candidate_acknowledgement_falls_back_to_active_evidence_when_candidate_already_collapsed(monkeypatch):
    order_id = "ORD-WORKFLOW-ACTIVE-HORIZON-001"
    workflow_states = [
        {
            "state": "apply_ready",
            "candidate_evidence_run_id": None,
            "active_evidence_run_id": "OEV-ACTIVE-HORIZON-001",
        },
        {
            "state": "apply_ready",
            "candidate_evidence_run_id": None,
            "active_evidence_run_id": "OEV-ACTIVE-HORIZON-001",
        },
    ]
    calls: list[tuple[str, str, str]] = []

    def _fake_get_order_workflow_state(_order_id: str, *, refresh: bool = False):
        assert _order_id == order_id
        assert refresh is True
        return workflow_states.pop(0)

    def _fake_acknowledge_candidate_evidence(_order_id: str, evidence_run_id: str, *, selected_by: str | None = None):
        calls.append((_order_id, evidence_run_id, str(selected_by or "")))
        return {"selected_value": evidence_run_id}

    monkeypatch.setattr(order_service, "get_order_workflow_state", _fake_get_order_workflow_state)
    monkeypatch.setattr(
        critical_decision_service,
        "acknowledge_candidate_evidence",
        _fake_acknowledge_candidate_evidence,
    )

    refreshed = order_service._acknowledge_current_candidate_after_authoritative_action(
        order_id,
        selected_by="save-ocr-sheet-exact",
    )

    assert isinstance(refreshed, dict)
    assert refreshed["active_evidence_run_id"] == "OEV-ACTIVE-HORIZON-001"
    assert calls == [(order_id, "OEV-ACTIVE-HORIZON-001", "save-ocr-sheet-exact")]


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
        "_build_menu_context_from_current_sheet_context",
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
        "_build_menu_context_from_current_sheet_context",
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


def test_get_order_workflow_state_heals_failed_rerun_back_to_awaiting_output_when_current_output_is_pending(monkeypatch, tmp_path):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-rerun-pending-heal", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(order["id"])
    output_path = tmp_path / "ocr_rerun_pending.json"
    output_path.write_text(
        json.dumps(
            {
                "status": "running",
                "stage": "ocr",
                "input_reference": "file://dummy-workflow.pdf",
                "output_reference": f"file://{output_path}",
            }
        ),
        encoding="utf-8",
    )
    create_job(f"OCR-{order['id']}", input_reference="file://dummy-workflow.pdf", status="failed")
    update_job(
        f"OCR-{order['id']}",
        status="failed",
        output_reference=f"file://{output_path}",
        error_message="ocr_pipeline_failed:SystemExit(1)",
        metrics={
            "processing_stage": "ocr_pipeline",
            "request_mode": "ocr_rerun",
            "result_state": "hard_failed",
            "order_id": order["id"],
        },
    )

    workflow = order_service.get_order_workflow_state(order["id"], refresh=True)

    rerun_job = get_ocr_job(f"OCR-{order['id']}")
    assert isinstance(workflow, dict)
    assert workflow["state"] == "rerun_in_progress"
    reparse_state = workflow.get("reparse_state") or {}
    assert reparse_state.get("status") == "awaiting_output"
    assert isinstance(rerun_job, dict)
    assert rerun_job["status"] == "awaiting_output"
    assert rerun_job["output_reference"] == f"file://{output_path}"


def test_get_order_workflow_state_reconciles_completed_rerun_output_when_current_reference_is_still_pending(
    monkeypatch,
    tmp_path,
):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-rerun-pending-current-output", facility_hint="FAC00001", week_hint="2026-03")
    old_evidence = _persist_evidence(
        order["id"],
        extra_payload={
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|5|",
        },
    )
    assert isinstance(old_evidence, dict)
    pending_output_path = tmp_path / "ocr_rerun_pending_current_ref.json"
    pending_output_path.write_text(
        json.dumps(
            {
                "status": "running",
                "stage": "ocr",
                "input_reference": "file://dummy-workflow.pdf",
                "output_reference": f"file://{pending_output_path}",
            }
        ),
        encoding="utf-8",
    )
    create_job(f"OCR-{order['id']}", input_reference="file://dummy-workflow.pdf", status="awaiting_output")
    update_job(
        f"OCR-{order['id']}",
        status="awaiting_output",
        output_reference=f"file://{pending_output_path}",
        error_message="ocr_output_pending",
        metrics={
            "processing_stage": "ocr_pipeline",
            "request_mode": "ocr_rerun",
            "result_state": "awaiting_output",
            "order_id": order["id"],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    completed_payload = {
        "status": "done",
        "stage": "done",
        "input_reference": "gs://bucket/input/OCR-order-rerun-new.pdf",
        "output_reference": "gs://bucket/output/OCR-order-rerun-new.pdf.json",
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
        lambda *_args, **_kwargs: [("gs://bucket/output/OCR-order-rerun-new.pdf.json", completed_payload)],
    )

    workflow = order_service.get_order_workflow_state(order["id"], refresh=True)

    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    rerun_job = get_ocr_job(f"OCR-{order['id']}")

    assert isinstance(workflow, dict)
    assert workflow["state"] != "rerun_in_progress"
    assert isinstance(latest_evidence, dict)
    assert latest_evidence["source"] == "ocr-rerun-reconcile"
    assert latest_evidence["id"] != old_evidence["id"]
    assert isinstance(rerun_job, dict)
    assert rerun_job["status"] == "done"
    assert rerun_job["output_reference"] == "gs://bucket/output/OCR-order-rerun-new.pdf.json"


def test_get_order_workflow_state_keeps_rerun_awaiting_output_when_only_older_completed_output_exists(
    monkeypatch,
    tmp_path,
):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-rerun-older-output", facility_hint="FAC00001", week_hint="2026-03")
    old_evidence = _persist_evidence(
        order["id"],
        extra_payload={
            "table_raw": "|日付|区分|メニュー|常食|\n|---|---|---|---|\n|03/22|朝|Menu A|5|",
        },
    )
    assert isinstance(old_evidence, dict)
    pending_output_path = tmp_path / f"OCR-{order['id']}_20260322_120000_000001.pdf.json"
    pending_output_path.write_text(
        json.dumps(
            {
                "status": "running",
                "stage": "ocr",
                "input_reference": "file://dummy-workflow.pdf",
                "output_reference": f"file://{pending_output_path}",
            }
        ),
        encoding="utf-8",
    )
    create_job(f"OCR-{order['id']}", input_reference="file://dummy-workflow.pdf", status="awaiting_output")
    update_job(
        f"OCR-{order['id']}",
        status="awaiting_output",
        output_reference=f"file://{pending_output_path}",
        error_message="ocr_output_pending",
        metrics={
            "processing_stage": "ocr_pipeline",
            "request_mode": "ocr_rerun",
            "result_state": "awaiting_output",
            "order_id": order["id"],
            "awaiting_output_since": "2026-03-22T12:00:00",
            "stage_updated_at": "2026-03-22T12:00:00",
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )
    older_completed_payload = {
        "status": "done",
        "stage": "done",
        "input_reference": "gs://bucket/input/OCR-order-rerun-old.pdf",
        "output_reference": f"gs://bucket/output/OCR-{order['id']}_20260322_115959_999999.pdf.json",
        "pages": [{"page_index": 1, "ocr_overlay_uri": "gs://bucket/orders/page1-ocr.png"}],
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
        lambda *_args, **_kwargs: [(older_completed_payload["output_reference"], older_completed_payload)],
    )

    workflow = order_service.get_order_workflow_state(order["id"], refresh=True)

    latest_evidence = order_service.get_latest_ocr_evidence_run(order["id"], backfill_from_cache=False)
    rerun_job = get_ocr_job(f"OCR-{order['id']}")

    assert isinstance(workflow, dict)
    reparse_state = workflow.get("reparse_state") or {}
    assert reparse_state.get("status") in {"awaiting_output", "recovering"}
    assert isinstance(latest_evidence, dict)
    assert latest_evidence["id"] == old_evidence["id"]
    assert isinstance(rerun_job, dict)
    assert rerun_job["status"] in {"awaiting_output", "recovering"}
    assert rerun_job["output_reference"] == f"file://{pending_output_path}"


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
        "_build_menu_context_from_current_sheet_context",
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
        "_build_menu_context_from_current_sheet_context",
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
        "_build_menu_context_from_current_sheet_context",
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
        "_build_menu_context_from_current_sheet_context",
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
        "_build_menu_context_from_current_sheet_context",
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
        "_build_menu_context_from_current_sheet_context",
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
        lambda _order_id: _semantic_initial_draft(order["id"]),
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
    blockers = next_workflow.get("apply_gate", {}).get("blockers") or []
    assert "column_mapping_choice_required" not in blockers
    assert "draft_rows_empty" not in blockers
    assert next_workflow.get("can_apply") is True
    assert next_workflow.get("can_confirm") is True
    assert next_workflow.get("apply_gate", {}).get("can_apply") is True
    assert next_workflow.get("apply_gate", {}).get("can_confirm") is True
    assert "column_mapping" not in (
        next_workflow.get("candidate_resolution", {}).get("gate_summary", {}).get("choice_required_types") or []
    )


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
    assert workflow["can_apply"] is False
    assert workflow["can_confirm"] is False
    assert workflow["apply_gate"]["can_apply"] is False
    assert workflow["apply_gate"]["can_confirm"] is False
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
    assert workflow["apply_gate"]["can_apply"] is False
    assert workflow["apply_gate"]["can_confirm"] is False
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
        "_build_menu_context_from_current_sheet_context",
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
    assert workflow["apply_gate"]["can_apply"] is False
    assert workflow["apply_gate"]["can_confirm"] is False
    assert "numeric_trust_low" in (workflow["apply_gate"]["warnings"] or [])


def test_refresh_workflow_state_clears_stale_low_trust_review_when_current_sheet_is_semantic_ready(monkeypatch):
    order_service.clear_all()
    order = _seed_order(
        message_id="msg-workflow-state-current-sheet-ready",
        facility_hint="FAC00015",
        week_hint="2026-04",
    )
    _persist_evidence(
        order["id"],
        extra_payload={
            "facility_id": "FAC00015",
            "template_id": "fax_layout_regular_forbidden_v1",
            "template_resolution": {
                "resolved_template_id": "fax_layout_regular_forbidden_v1",
                "candidate_template_ids": ["fax_layout_regular_forbidden_v1"],
                "confidence": 0.96,
                "blocked": False,
                "blocked_reasons": [],
            },
            "cell_issues": [{"issue_code": "merged_numeric_cell"}],
            "failed_cells": [{"row_index": 0, "column_index": 3}],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-04",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 40,
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "order_id": order["id"],
            "source": "weekly_menu+payload_row",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "rows": [["04/26", "朝", "Menu A", "26", ""]],
            "row_ids": ["row-1"],
            "warnings": [],
            "blockers": [],
            "has_persisted_draft": False,
            "clean_saved_draft": False,
            "has_semantic_fields": True,
            "resolved_week_id": "2026-04@2026-04-26~2026-04-30",
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "evidence_ready"
    assert workflow["primary_action"] == "open_draft"
    warnings = workflow["apply_gate"]["warnings"] or []
    assert "quantity_review_required" not in warnings
    assert "numeric_trust_low" not in warnings


def test_refresh_workflow_state_keeps_low_trust_review_when_current_sheet_still_requests_ocr_review(monkeypatch):
    order_service.clear_all()
    order = _seed_order(
        message_id="msg-workflow-state-current-sheet-review",
        facility_hint="FAC00015",
        week_hint="2026-04",
    )
    _persist_evidence(
        order["id"],
        extra_payload={
            "facility_id": "FAC00015",
            "template_id": "fax_layout_regular_forbidden_v1",
            "template_resolution": {
                "resolved_template_id": "fax_layout_regular_forbidden_v1",
                "candidate_template_ids": ["fax_layout_regular_forbidden_v1"],
                "confidence": 0.96,
                "blocked": False,
                "blocked_reasons": [],
            },
            "cell_issues": [{"issue_code": "merged_numeric_cell"}],
            "failed_cells": [{"row_index": 0, "column_index": 3}],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-04",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 40,
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "order_id": order["id"],
            "source": "weekly_menu+payload_row",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "rows": [["04/26", "朝", "Menu A", "26", ""]],
            "row_ids": ["row-1"],
            "warnings": ["sheet_ocr_review_required"],
            "blockers": [],
            "has_persisted_draft": False,
            "clean_saved_draft": False,
            "has_semantic_fields": True,
            "resolved_week_id": "2026-04@2026-04-26~2026-04-30",
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "review_required"
    assert workflow["primary_action"] == "review_critical_cells"
    warnings = workflow["apply_gate"]["warnings"] or []
    assert "quantity_review_required" in warnings
    assert "numeric_trust_low" in warnings


def _assert_refresh_workflow_state_blocks_when_monthly_menu_object_is_missing():
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-missing-menu", facility_hint="FAC00001", week_hint="2199-11")
    _persist_evidence(order["id"])

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "draft_blocked"
    assert workflow["primary_action"] == "resolve_blockers"
    blockers = workflow["apply_gate"]["blockers"] or []
    warnings = workflow["apply_gate"]["warnings"] or []
    assert "monthly_menu_object_missing" in blockers
    assert "monthly_menu_object_missing" not in warnings
    assert workflow["apply_gate"]["can_apply"] is False
    assert workflow["apply_gate"]["can_confirm"] is False


def test_refresh_workflow_state_blocks_when_monthly_menu_object_is_missing():
    _assert_refresh_workflow_state_blocks_when_monthly_menu_object_is_missing()


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
    assert "monthly_menu_object_missing" not in (apply_gate.get("apply_blockers") or [])
    assert "monthly_menu_object_missing" not in (apply_gate.get("confirm_blockers") or [])
    assert "monthly_menu_object_missing" in (apply_gate.get("warnings") or [])
    assert apply_gate["can_apply"] is True
    assert apply_gate["can_confirm"] is True


def test_refresh_workflow_state_keeps_semantic_initial_draft_when_weekly_menu_warning_remains(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-semantic-initial", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(order["id"])

    monkeypatch.setattr(
        order_service,
        "get_latest_sheet_draft",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        order_service,
        "build_initial_sheet_draft",
        lambda _order_id: {
            "order_id": _order_id,
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", ""]],
            "row_ids": ["semantic-1"],
            "warnings": ["sheet_weekly_menu_missing", "quantity_review_required"],
            "base_evidence_run_id": "OEVtest",
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["draft_id"] is None
    assert workflow["state"] == "review_required"
    assert "monthly_menu_object_missing" not in (workflow["apply_gate"]["blockers"] or [])
    assert "monthly_menu_object_missing" in (workflow["apply_gate"]["warnings"] or [])


def test_refresh_workflow_state_blocks_on_menu_entries_missing_without_monthly_menu_object_missing(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-menu-entries-missing", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(order["id"])
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": True,
            "entries_count": 0,
            "order_codes": ["menu_entries_missing"],
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["state"] == "draft_blocked"
    blockers = workflow["apply_gate"]["blockers"] or []
    assert "menu_entries_missing" in blockers
    assert "monthly_menu_object_missing" not in blockers


def test_refresh_workflow_state_uses_current_sheet_context_menu_diagnostics_without_relookup(monkeypatch):
    order_service.clear_all()
    order = _seed_order(
        message_id="msg-workflow-state-current-sheet-menu-diagnostics-001",
        facility_hint="FAC00001",
        week_hint="2026-03",
    )
    _persist_evidence(order["id"])
    order_payload = order_service.get_order_by_id(order["id"])

    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "order_id": order["id"],
            "order_payload": order_payload,
            "draft_record": None,
            "draft_payload": {
                "source": "ocr_table",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "5"]],
                "row_ids": ["semantic-1"],
                "warnings": [],
                "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
                "menu_diagnostics": {
                    "order_codes": ["menu_entries_missing"],
                    "facility_entries_count": 0,
                    "global_entries_count": 0,
                },
            },
            "draft_id": None,
            "source": "ocr_table",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["semantic-1"],
            "warnings": [],
            "blockers": [],
            "has_persisted_draft": False,
            "clean_saved_draft": False,
            "base_evidence_run_id": None,
            "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
            "facility_id": "FAC00001",
            "menu_diagnostics": {
                "order_codes": ["menu_entries_missing"],
                "facility_entries_count": 0,
                "global_entries_count": 0,
            },
            "row_diagnostics": [],
            "seed_source": "ocr_table",
            "enrichment_source": None,
            "has_semantic_fields": True,
        },
    )

    def _unexpected_menu_context(**_kwargs):
        raise AssertionError("workflow menu context should come from current_sheet_context diagnostics")

    monkeypatch.setattr(workflow_state_service, "_build_menu_context", _unexpected_menu_context)

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    blockers = workflow["apply_gate"]["blockers"] or []
    assert "menu_entries_missing" in blockers


def test_refresh_workflow_state_exposes_review_summary_from_sheet_gate(monkeypatch):
    order_service.clear_all()
    order = _seed_order(
        message_id="msg-workflow-state-review-summary-parity-001",
        facility_hint="FAC00001",
        week_hint="2026-03",
    )
    _persist_evidence(order["id"])
    order_payload = order_service.get_order_by_id(order["id"])

    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "order_id": order["id"],
            "order_payload": order_payload,
            "draft_record": {
                "id": "ODRreview001",
                "edited_at": "2026-03-22T10:00:00",
                "warnings_json": ["sheet_ocr_review_required"],
                "blockers_json": [],
            },
            "draft_payload": {
                "source": "review_blocked",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [],
                "row_ids": [],
                "warnings": ["sheet_ocr_review_required"],
                "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
                "menu_diagnostics": {"order_codes": ["menu_entries_missing"]},
            },
            "draft_id": "ODRreview001",
            "source": "review_blocked",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [],
            "row_ids": [],
            "warnings": ["sheet_ocr_review_required"],
            "blockers": [],
            "has_persisted_draft": True,
            "clean_saved_draft": False,
            "base_evidence_run_id": None,
            "resolved_week_id": "2026-03@2026-03-22~2026-03-28",
            "facility_id": "FAC00001",
            "menu_diagnostics": {"order_codes": ["menu_entries_missing"]},
            "row_diagnostics": [],
            "seed_source": "review_blocked",
            "enrichment_source": None,
            "has_semantic_fields": True,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["ocr_review_state"] == "draft_ready"
    assert workflow["ocr_review_stage"] == "needs_human_review"
    assert workflow["ocr_has_saved_draft"] is True
    assert workflow["ocr_draft_row_count"] == 0
    assert "menu_entries_missing" in (workflow["ocr_apply_blockers"] or [])
    assert "rows_empty" in (workflow["ocr_apply_blockers"] or [])
    assert "menu_entries_missing" in (workflow["ocr_confirm_blockers"] or [])


def test_refresh_workflow_state_uses_canonical_order_week_over_stale_current_sheet_week(monkeypatch):
    order_service.clear_all()
    order = _seed_order(
        message_id="msg-workflow-state-week-parity-001",
        facility_hint="FAC00001",
        week_hint="2026-03",
    )
    _persist_evidence(order["id"])
    order_payload = order_service.get_order_by_id(order["id"])
    captured: dict[str, str | None] = {}
    original_resolve = workflow_state_service.candidate_resolution_service.resolve_order_candidates

    monkeypatch.setattr(
        workflow_state_service,
        "_load_workflow_current_sheet_context",
        lambda *_args, **_kwargs: {
            "order_id": order["id"],
            "order_payload": order_payload,
            "draft_record": None,
            "draft_payload": {
                "source": "manual_draft",
                "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
                "header": ["日付", "区分", "メニュー", "常食2F"],
                "rows": [["03/22", "朝", "Menu A", "5"]],
                "row_ids": ["row-1"],
                "warnings": [],
                "resolved_week_id": "2026-03@2026-03-01~2026-03-07",
                "menu_diagnostics": {"order_codes": [], "facility_entries_count": 21, "global_entries_count": 21},
            },
            "draft_id": None,
            "source": "manual_draft",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [["03/22", "朝", "Menu A", "5"]],
            "row_ids": ["row-1"],
            "warnings": [],
            "blockers": [],
            "has_persisted_draft": False,
            "clean_saved_draft": False,
            "base_evidence_run_id": None,
            "resolved_week_id": "2026-03@2026-03-01~2026-03-07",
            "facility_id": "FAC00001",
            "menu_diagnostics": {"order_codes": [], "facility_entries_count": 21, "global_entries_count": 21},
            "row_diagnostics": [],
            "seed_source": "manual_draft",
            "enrichment_source": None,
            "has_semantic_fields": True,
        },
    )

    def _capture_resolve_order_candidates(**kwargs):
        captured["week_code"] = kwargs.get("week_code")
        return original_resolve(**kwargs)

    monkeypatch.setattr(
        workflow_state_service.candidate_resolution_service,
        "resolve_order_candidates",
        _capture_resolve_order_candidates,
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert captured["week_code"] == "2026-03@2026-03-22~2026-03-28"


def test_refresh_workflow_state_prefers_clean_saved_draft_over_stale_layout_blockers(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-clean-draft-override", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(
        order["id"],
        extra_payload={
            "template_resolution": {
                "resolved_template_id": None,
                "candidate_template_ids": ["fax_layout_regular_soft_mixer_forbidden_v1"],
                "confidence": 0.42,
                "blocked": True,
                "blocked_reasons": ["template_resolution_missing"],
            },
            "quantity_subgrid_passes": [],
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
        },
    )
    order_service.set_status(order["id"], "確定")
    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食", "備考"],
            "rows": [["03/22", "朝", "Menu A", "5", ""]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
    )
    assert saved is not None
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
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
    assert workflow["apply_gate"]["can_apply"] is True


    assert "semantic_shell_only" not in (workflow["apply_gate"]["blockers"] or [])
    assert "template_unresolved" not in (workflow["apply_gate"]["blockers"] or [])
    assert "numeric_trust_low" not in (workflow["apply_gate"]["warnings"] or [])
    assert "draft_newer_than_lines" in (workflow["apply_gate"]["warnings"] or [])
    assert "draft_newer_than_lines" in (workflow["apply_gate"]["confirm_blockers"] or [])


def test_refresh_workflow_state_uses_persisted_draft_without_exact_revision(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-persisted-draft-truth", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(order["id"])
    saved = draft_sheet_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食", "備考"],
            "rows": [["03/22", "朝", "Menu A", "8", "manual"]],
            "row_ids": ["row-1"],
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        edited_by="tester",
    )
    assert saved is not None
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])

    assert isinstance(workflow, dict)
    assert workflow["ocr_has_saved_draft"] is True
    assert workflow["ocr_draft_row_count"] == 1
    assert workflow["ocr_draft_updated_at"] == saved["edited_at"]


def test_refresh_workflow_state_does_not_mutate_saved_sheet_blockers_on_read(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-workflow-state-refresh-clears-stale-blockers", facility_hint="FAC00001", week_hint="2026-03")
    _persist_evidence(order["id"])
    saved = order_service.persist_sheet_draft(
        order_id=order["id"],
        draft_sheet_json={
            "order_id": order["id"],
            "source": "draft_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食", "備考"],
            "rows": [
                ["03/22", "朝", "Menu A", "5", ""],
                ["01/01", "\"", "Ghost Menu", "23", ""],
            ],
            "row_ids": ["draft-2", "draft-1"],
            "warnings": ["stale-current-warning-should-drop"],
        },
        draft_state="auto_apply_blocked",
        blockers=["sheet_canonical_mismatch"],
        warnings=["sheet_ocr_review_required"],
    )
    assert saved is not None
    monkeypatch.setattr(
        order_service,
        "_build_best_available_semantic_draft",
        lambda _order_id, use_saved_draft=False: {
            "order_id": order["id"],
            "source": "weekly_menu+ocr_payload",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
            "header": ["日付", "区分", "メニュー", "常食", "備考"],
            "rows": [["03/22", "朝", "Menu A", "", ""]],
            "row_ids": ["fresh-1"],
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        workflow_state_service,
        "_build_menu_context_from_current_sheet_context",
        lambda **_kwargs: {
            "month_id": "2026-03",
            "weekly_menu_missing": False,
            "menu_entries_missing": False,
            "entries_count": 21,
        },
    )

    workflow = workflow_state_service.refresh_workflow_state(order["id"])
    refreshed_draft = draft_sheet_service.get_latest_sheet_draft(order["id"])

    assert refreshed_draft is not None
    assert refreshed_draft["blockers_json"] == ["sheet_canonical_mismatch"]
    assert refreshed_draft["warnings_json"] == ["sheet_ocr_review_required"]
    assert refreshed_draft["draft_sheet_json"]["rows"] == [
        ["03/22", "朝", "Menu A", "5", ""],
        ["01/01", "\"", "Ghost Menu", "23", ""],
    ]
    assert isinstance(workflow, dict)
    assert workflow["state"] == "draft_blocked"
    assert workflow["apply_gate"]["can_apply"] is False
    assert "sheet_canonical_mismatch" in (workflow["apply_gate"]["blockers"] or [])


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


def _assert_refresh_workflow_state_clears_review_required_after_quantity_choice(monkeypatch):
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
        "_build_menu_context_from_current_sheet_context",
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
        lambda _order_id: _semantic_initial_draft(order["id"]),
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


def test_refresh_workflow_state_clears_review_required_after_quantity_choice(monkeypatch):
    _assert_refresh_workflow_state_clears_review_required_after_quantity_choice(monkeypatch)
