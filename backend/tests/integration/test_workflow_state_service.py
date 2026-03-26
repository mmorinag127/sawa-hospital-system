import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import draft_sheet_service, order_service, template_resolution_service, workflow_state_service  # noqa: E402
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
    assert workflow["state"] in {"review_required", "semantic_shell_only"}
    assert "numeric_trust_low" in (workflow["apply_gate"]["warnings"] or [])
    assert workflow["candidate_resolution"]["resolutions"]["column_mapping"]["decision_source"] == "position_fallback"


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
            "source": "draft_sheet",
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
    assert workflow["candidate_resolution"]["resolutions"]["column_mapping"]["decision_source"] == "position_fallback"
    assert workflow["state"] == "apply_ready"
    assert workflow["apply_gate"]["can_apply"] is True
    assert "template_unresolved" not in (workflow["apply_gate"]["blockers"] or [])
    assert "sheet_quantity_column_unmapped" not in (workflow["apply_gate"]["blockers"] or [])
    assert "ocr_evidence_recovery_required" not in (workflow["apply_gate"]["blockers"] or [])


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
    assert workflow["apply_gate"]["can_apply"] is True
    assert "semantic_shell_only" not in (workflow["apply_gate"]["blockers"] or [])
    assert "template_unresolved" not in (workflow["apply_gate"]["blockers"] or [])
    assert "numeric_trust_low" not in (workflow["apply_gate"]["warnings"] or [])
    assert "draft_newer_than_lines" in (workflow["apply_gate"]["warnings"] or [])


def test_refresh_workflow_state_does_not_keep_stale_sheet_blockers_after_semantic_refresh(monkeypatch):
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
        "_build_menu_context",
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
    assert refreshed_draft["blockers_json"] == []
    assert refreshed_draft["warnings_json"] == []
    assert refreshed_draft["draft_sheet_json"]["rows"] == [["03/22", "朝", "Menu A", "5", ""]]
    assert isinstance(workflow, dict)
    assert workflow["state"] == "apply_ready"
    assert workflow["apply_gate"]["can_apply"] is True
    assert "sheet_canonical_mismatch" not in (workflow["apply_gate"]["blockers"] or [])


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
