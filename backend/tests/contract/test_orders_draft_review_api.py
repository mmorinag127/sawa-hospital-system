import pathlib
import sys
from datetime import date, datetime, timedelta

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.orders as orders_api  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.main import app  # noqa: E402
from src.models.menu import MonthlyMenu, MonthlyMenuEntry  # noqa: E402
from src.models.ocr_job import OcrJob  # noqa: E402
from src.models.order_ocr_cache import OrderOcrCache  # noqa: E402
from src.services import order_service  # noqa: E402
from src.services.ocr_job_service import create_job, get_job, update_job  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _create_seed_order(
    message_id: str,
    *,
    week_hint: str = "2026-02",
    received_at: datetime | None = None,
    line_date: str = "2026-02-15",
) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=received_at or datetime(2026, 2, 15, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=week_hint,
    )
    lines = [
        {
            "date": line_date,
            "daypart": "朝",
            "menu_name": "Menu A",
            "diet_type": "regular",
            "area_id": "2F",
            "bag_type": "standard",
            "quantity_original": 5,
        }
    ]
    return order_service.create_order_from_ingest(payload, lines=lines)


def _clear_month(month_id: str) -> None:
    with session_scope() as session:
        session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.monthly_menu_id == month_id).delete()
        session.query(MonthlyMenu).filter(MonthlyMenu.id == month_id).delete()


def _sheet_payload(
    *,
    quantity: str,
    note: str,
    row_id: str,
) -> dict:
    return {
        "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
        "rows": [["02/15", "朝", "Menu A", quantity, note]],
        "ui_mode": "sheet",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        "row_ids": [row_id],
    }


def test_get_ocr_sheet_returns_recoverable_payload_when_apply_is_blocked():
    order_service.clear_all()
    _clear_month("2199-11")
    client = TestClient(app)
    order = _create_seed_order(
        "msg-draft-review-missing-menu",
        week_hint="2199-11",
        received_at=datetime(2199, 11, 15, 9, 0, 0),
        line_date="2199-11-15",
    )

    res = client.get(f"/orders/{order['id']}/ocr-sheet")

    assert res.status_code == 200
    payload = res.json()
    assert payload["rows"] == []
    assert payload["review_state"] == "review_required"
    assert payload["review_stage"] == "needs_human_review"
    assert payload["can_apply"] is False
    assert payload["can_confirm"] is False
    assert payload["source"] in {
        "review_blocked",
        "weekly_menu",
        "weekly_menu+ocr_payload",
        "weekly_menu_blocked",
        "ocr_table",
        "ocr_table+payload_row",
        "ocr_table+identity",
    }
    blocker_codes = set(payload.get("apply_blockers") or []) | set(payload.get("confirm_blockers") or [])
    assert blocker_codes
    detail_codes = {
        item.get("code")
        for item in (payload.get("apply_blocker_details") or []) + (payload.get("confirm_blocker_details") or [])
        if isinstance(item, dict)
    }
    assert detail_codes
    assert payload["draft_line_count"] == 0
    assert payload["confirmed_line_count"] == 1
    assert payload["line_count_delta"] == -1
    assert payload["line_count_mismatch"] is True


def test_order_detail_and_draft_sheet_expose_same_blocker_reason_for_missing_menu():
    order_service.clear_all()
    _clear_month("2199-11")
    client = TestClient(app)
    order = _create_seed_order(
        "msg-draft-review-parity-missing-menu",
        week_hint="2199-11",
        received_at=datetime(2199, 11, 15, 9, 0, 0),
        line_date="2199-11-15",
    )

    detail_res = client.get(f"/orders/{order['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["ocr_review_state"] in {"draft_ready", "review_required"}
    assert "monthly_menu_object_missing" in (detail["ocr_apply_blockers"] or [])
    assert "rows_empty" in (detail["ocr_apply_blockers"] or [])

    workflow_res = client.get(f"/orders/{order['id']}/workflow-state")
    assert workflow_res.status_code == 200
    workflow = workflow_res.json()
    assert "monthly_menu_object_missing" in (workflow["ocr_apply_blockers"] or [])
    assert "rows_empty" in (workflow["ocr_apply_blockers"] or [])

    draft_res = client.get(f"/orders/{order['id']}/draft-sheet")
    assert draft_res.status_code == 200
    draft = draft_res.json()
    assert "monthly_menu_object_missing" in (draft.get("warnings") or [])
    assert "monthly_menu_object_missing" in (draft.get("apply_blockers") or [])
    assert "rows_empty" in (draft.get("apply_blockers") or [])


def test_draft_sheet_compact_payload_keeps_blockers_but_skips_heavy_meta_when_menu_missing():
    order_service.clear_all()
    _clear_month("2199-11")
    client = TestClient(app)
    order = _create_seed_order(
        "msg-draft-review-compact-missing-menu",
        week_hint="2199-11",
        received_at=datetime(2199, 11, 15, 9, 0, 0),
        line_date="2199-11-15",
    )

    res = client.get(f"/orders/{order['id']}/draft-sheet", params={"compact": 1})

    assert res.status_code == 200
    payload = res.json()
    assert payload["source"] == "review_blocked"
    assert payload["rows"] == []
    assert "monthly_menu_object_missing" in (payload.get("warnings") or [])
    assert "monthly_menu_object_missing" in (payload.get("blockers") or [])
    assert "workflow_state" not in payload
    assert "candidate_resolution" not in payload
    assert "evidence_capabilities" not in payload


def test_ocr_sheet_api_prefers_current_sheet_context_without_persisted_draft(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-current-sheet-ocr-sheet")

    current_sheet_payload = {
        "order_id": order["id"],
        "source": "weekly_menu+ocr_payload",
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [["02/15", "朝", "Menu A", "7"]],
        "row_ids": ["row-current-1"],
        "warnings": ["quantity_review_required"],
        "menu_diagnostics": {"order_codes": []},
        "sheet_projection": {"status": "projected"},
        "resolved_week_id": "2026-02@2026-02-15~2026-02-21",
    }
    current_sheet_record = {
        "id": None,
        "order_id": order["id"],
        "base_evidence_run_id": "OEVcurrent123",
        "base_template_resolution_id": "tmpl-current",
        "draft_sheet_json": {
            "order_id": order["id"],
            "source": "ocr_evidence",
            "fields": ["col1", "col2", "col3", "col4"],
            "header": ["日付", "区分", "メニュー", "常食2F"],
            "rows": [],
            "row_ids": [],
        },
        "draft_state": "draft_ready",
        "blockers_json": [],
        "warnings_json": ["quantity_review_required"],
    }
    current_sheet_context = {
        "order_id": order["id"],
        "draft_record": current_sheet_record,
        "draft_payload": dict(current_sheet_payload),
        "draft_id": None,
        "source": "weekly_menu+ocr_payload",
        "fields": list(current_sheet_payload["fields"]),
        "header": list(current_sheet_payload["header"]),
        "rows": list(current_sheet_payload["rows"]),
        "row_ids": list(current_sheet_payload["row_ids"]),
        "warnings": ["quantity_review_required"],
        "blockers": [],
        "sheet_projection": {"status": "projected"},
        "has_persisted_draft": False,
        "clean_saved_draft": False,
        "base_evidence_run_id": "OEVcurrent123",
        "resolved_week_id": "2026-02@2026-02-15~2026-02-21",
        "facility_id": "FAC00001",
        "menu_diagnostics": {"order_codes": []},
        "row_diagnostics": [],
        "has_semantic_fields": True,
    }

    monkeypatch.setattr(order_service, "_evidence_only_step2_enabled", lambda: True)
    monkeypatch.setattr(
        order_service,
        "_get_ocr_output_without_legacy_edits",
        lambda _order_id, persist_cache=False: ({"metrics": {"status": "done"}}, None),
    )
    monkeypatch.setattr(order_service, "_augment_payload_with_candidate_resolution", lambda _order_id, payload: payload)
    monkeypatch.setattr(
        order_service,
        "_resolve_effective_sheet_template",
        lambda **_kwargs: (
            {"facility_id": "FAC00001"},
            None,
            None,
            "template_unresolved",
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_build_sheet_fields_and_indexes",
        lambda _template: (
            list(current_sheet_payload["fields"]),
            {field: idx for idx, field in enumerate(current_sheet_payload["fields"])},
        ),
    )
    monkeypatch.setattr(order_service, "_validate_sheet_template_fields", lambda _fields: None)
    monkeypatch.setattr(order_service, "_build_sheet_quantity_index", lambda _fields: {})
    monkeypatch.setattr(
        order_service.candidate_resolution_service,
        "position_fallback_allowed_for_facility",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(order_service, "get_current_sheet_context", lambda *_args, **_kwargs: current_sheet_context)

    res = client.get(f"/orders/{order['id']}/ocr-sheet")

    assert res.status_code == 200
    payload = res.json()
    assert payload["source"] == "weekly_menu+ocr_payload"
    assert payload["rows"] == [["02/15", "朝", "Menu A", "7"]]
    assert payload["row_ids"] == ["row-current-1"]
    assert payload["evidence_run_id"] == "OEVcurrent123"
    assert payload["base_evidence_run_id"] == "OEVcurrent123"
    assert payload["ocr_job_id"] == f"OCR-{order['id']}"
    assert payload["can_apply"] is False
    assert payload["can_confirm"] is False


def test_ocr_sheet_api_prefers_generic_current_sheet_context_before_recoverable_fallback(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-current-sheet-generic")

    current_sheet_payload = {
        "order_id": order["id"],
        "source": "ocr_evidence",
        "fields": ["col1", "col2", "col3", "col4"],
        "header": ["日付", "区分", "メニュー", "常食2F"],
        "rows": [["02/15", "朝", "Menu A", "7"]],
        "row_ids": ["row-current-1"],
        "warnings": ["monthly_menu_object_missing"],
        "menu_diagnostics": {"order_codes": ["monthly_menu_object_missing"]},
        "sheet_projection": {"status": "projected"},
        "resolved_week_id": "2026-02@2026-02-15~2026-02-21",
    }
    current_sheet_record = {
        "id": None,
        "order_id": order["id"],
        "base_evidence_run_id": "OEVcurrent456",
        "base_template_resolution_id": None,
        "draft_sheet_json": dict(current_sheet_payload),
        "draft_state": "draft_ready",
        "blockers_json": [],
        "warnings_json": ["monthly_menu_object_missing"],
    }
    current_sheet_context = {
        "order_id": order["id"],
        "draft_record": current_sheet_record,
        "draft_payload": dict(current_sheet_payload),
        "draft_id": None,
        "source": "ocr_evidence",
        "fields": list(current_sheet_payload["fields"]),
        "header": list(current_sheet_payload["header"]),
        "rows": list(current_sheet_payload["rows"]),
        "row_ids": list(current_sheet_payload["row_ids"]),
        "warnings": ["monthly_menu_object_missing"],
        "blockers": [],
        "sheet_projection": {"status": "projected"},
        "has_persisted_draft": False,
        "clean_saved_draft": False,
        "base_evidence_run_id": "OEVcurrent456",
        "resolved_week_id": "2026-02@2026-02-15~2026-02-21",
        "facility_id": "FAC00001",
        "menu_diagnostics": {"order_codes": ["monthly_menu_object_missing"]},
        "row_diagnostics": [],
        "has_semantic_fields": False,
    }

    monkeypatch.setattr(order_service, "_evidence_only_step2_enabled", lambda: True)
    monkeypatch.setattr(
        order_service,
        "_get_ocr_output_without_legacy_edits",
        lambda _order_id, persist_cache=False: ({"metrics": {"status": "done"}}, None),
    )
    monkeypatch.setattr(order_service, "_augment_payload_with_candidate_resolution", lambda _order_id, payload: payload)
    monkeypatch.setattr(
        order_service,
        "_resolve_effective_sheet_template",
        lambda **_kwargs: (
            {"facility_id": "FAC00001"},
            {"fields": list(current_sheet_payload["fields"]), "header": list(current_sheet_payload["header"])},
            "tmpl-current",
            None,
        ),
    )
    monkeypatch.setattr(
        order_service,
        "_build_sheet_fields_and_indexes",
        lambda _template: (
            list(current_sheet_payload["fields"]),
            {field: idx for idx, field in enumerate(current_sheet_payload["fields"])},
        ),
    )
    monkeypatch.setattr(order_service, "_validate_sheet_template_fields", lambda _fields: None)
    monkeypatch.setattr(order_service, "_build_sheet_quantity_index", lambda _fields: {})
    monkeypatch.setattr(
        order_service.candidate_resolution_service,
        "position_fallback_allowed_for_facility",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(order_service, "get_current_sheet_context", lambda *_args, **_kwargs: current_sheet_context)

    res = client.get(f"/orders/{order['id']}/ocr-sheet")

    assert res.status_code == 200
    payload = res.json()
    assert payload["source"] == "ocr_evidence"
    assert payload["rows"] == [["02/15", "朝", "Menu A", "7"]]
    assert payload["row_ids"] == ["row-current-1"]
    assert payload["evidence_run_id"] == "OEVcurrent456"
    assert payload["base_evidence_run_id"] == "OEVcurrent456"
    assert payload["ocr_job_id"] == f"OCR-{order['id']}"


def test_order_endpoints_expose_draft_ready_state_from_saved_sheet_and_reject_reason():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-state")

    save_res = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["02/15", "朝", "Menu A", "7", "draft"]],
            "ui_mode": "sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "row_ids": ["row-draft-1"],
        },
    )
    assert save_res.status_code == 200

    cached_payload = order_service.get_cached_ocr_payload(order["id"]) or {}
    cached_payload["_reparse_debug"] = {
        "error": "sheet_llm_audit_failed",
        "reject_reasons": ["sheet_llm_audit_failed"],
    }
    order_service._save_order_ocr_cache(order["id"], cached_payload)

    detail_res = client.get(f"/orders/{order['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["ocr_prompt_enabled"] is True
    assert detail["ocr_review_state"] == "draft_ready"
    assert detail["ocr_review_stage"] == "needs_human_review"
    assert detail["ocr_has_saved_draft"] is True
    assert detail["ocr_draft_row_count"] == 1
    assert detail["ocr_auto_apply_blocked"] is True
    assert detail["ocr_reparse_status"] == "blocked"
    assert detail["ocr_can_apply_draft"] is True
    assert detail["ocr_can_confirm"] is True
    assert "draft_newer_than_lines" not in (detail.get("ocr_confirm_blockers") or [])
    assert all(
        item.get("code") != "draft_newer_than_lines"
        for item in (detail.get("ocr_confirm_blocker_details") or [])
    )
    assert any(item.get("code") == "auto_apply_blocked" for item in (detail.get("ocr_confirm_warning_details") or []))
    assert detail["ocr_last_reparse_error"] == "sheet_llm_audit_failed"
    assert "sheet_llm_audit_failed" in (detail.get("ocr_reject_reasons") or [])

    list_res = client.get("/orders")
    assert list_res.status_code == 200
    row = next(item for item in list_res.json()["orders"] if item["id"] == order["id"])
    assert row["ocr_review_state"] == "draft_ready"
    assert row["ocr_review_stage"] == "needs_human_review"
    assert row["ocr_has_saved_draft"] is True
    assert row["ocr_can_apply_draft"] is True
    assert row["ocr_reparse_status"] == "blocked"


def test_confirm_endpoint_uses_latest_draft_without_draft_newer_block(monkeypatch):
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-confirm-block")
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda _order_id, refresh=False: {
            "state": "draft_ready",
            "apply_gate": {"can_apply": True, "can_confirm": True, "blockers": [], "warnings": []},
        },
    )
    save_res = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["02/15", "朝", "Menu A", "8", "draft"]],
            "ui_mode": "sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "row_ids": ["row-draft-2"],
        },
    )
    assert save_res.status_code == 200
    second_save_res = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["02/15", "朝", "Menu A", "9", "latest-draft"]],
            "ui_mode": "sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "row_ids": ["row-draft-3"],
        },
    )
    assert second_save_res.status_code == 200

    res = client.post(f"/orders/{order['id']}/confirm")

    assert res.status_code == 202
    confirm_detail = client.get(f"/orders/{order['id']}").json()
    assert confirm_detail["status"] == "確定"
    assert confirm_detail["lines"][0]["quantity_original"] == 9


def test_ocr_sheet_save_returns_stale_revision_conflict_for_outdated_revision():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-stale-sheet-save")

    first = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json=_sheet_payload(quantity="7", note="draft-1", row_id="row-stale-save-1"),
    )
    assert first.status_code == 200
    first_revision_id = str(first.json()["revision"]["revision_id"])

    second = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            **_sheet_payload(quantity="8", note="draft-2", row_id="row-stale-save-2"),
            "expected_revision_id": first_revision_id,
        },
    )
    assert second.status_code == 200
    second_revision_id = str(second.json()["revision"]["revision_id"])
    assert second_revision_id and second_revision_id != first_revision_id

    stale = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            **_sheet_payload(quantity="9", note="draft-stale", row_id="row-stale-save-3"),
            "expected_revision_id": first_revision_id,
        },
    )
    assert stale.status_code == 409
    detail = stale.json().get("detail") or {}
    assert detail.get("error") == "stale_revision_conflict"

    history = client.get(f"/orders/{order['id']}/ocr-history")
    assert history.status_code == 200
    latest = history.json().get("latest") or {}
    assert latest.get("revision_id") == second_revision_id
    assert latest.get("rows")[0][3] == "8"


def test_update_lines_returns_stale_lines_conflict_when_lines_changed_elsewhere():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-stale-lines")

    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    order_detail = detail.json()
    original_lines_updated_at = order_detail["lines_updated_at"]
    latest_lines = list(order_detail["lines"] or [])
    latest_lines[0] = {**latest_lines[0], "quantity_corrected": 6}

    first_update = client.put(
        f"/orders/{order['id']}/lines",
        json={
            "lines": latest_lines,
            "expected_lines_updated_at": original_lines_updated_at,
        },
    )
    assert first_update.status_code == 200

    stale_lines = list(order_detail["lines"] or [])
    stale_lines[0] = {**stale_lines[0], "quantity_corrected": 9}
    stale_update = client.put(
        f"/orders/{order['id']}/lines",
        json={
            "lines": stale_lines,
            "expected_lines_updated_at": original_lines_updated_at,
        },
    )
    assert stale_update.status_code == 409
    detail = stale_update.json().get("detail") or {}
    assert detail.get("error") == "stale_lines_conflict"


def test_ocr_apply_returns_stale_lines_conflict_after_remote_line_update():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-stale-apply")

    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    order_detail = detail.json()
    original_lines_updated_at = order_detail["lines_updated_at"]

    save_res = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json=_sheet_payload(quantity="7", note="draft-apply", row_id="row-stale-apply-1"),
    )
    assert save_res.status_code == 200
    current_revision_id = str(save_res.json()["revision"]["revision_id"])

    latest_lines = list(order_detail["lines"] or [])
    latest_lines[0] = {**latest_lines[0], "quantity_corrected": 4}
    lines_res = client.put(
        f"/orders/{order['id']}/lines",
        json={
            "lines": latest_lines,
            "expected_lines_updated_at": original_lines_updated_at,
        },
    )
    assert lines_res.status_code == 200

    stale_apply = client.post(
        f"/orders/{order['id']}/ocr-apply",
        json={
            **_sheet_payload(quantity="8", note="apply-stale", row_id="row-stale-apply-2"),
            "expected_revision_id": current_revision_id,
            "expected_lines_updated_at": original_lines_updated_at,
        },
    )
    assert stale_apply.status_code == 409
    detail = stale_apply.json().get("detail") or {}
    assert detail.get("error") == "stale_lines_conflict"


def test_ocr_apply_clears_saved_draft_state_after_successful_apply():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-apply-clears-draft")

    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    order_detail = detail.json()
    original_lines_updated_at = order_detail["lines_updated_at"]

    save_res = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json=_sheet_payload(quantity="7", note="draft-before-apply", row_id="row-apply-clears-draft-1"),
    )
    assert save_res.status_code == 200
    current_revision_id = str(save_res.json()["revision"]["revision_id"])

    apply_res = client.post(
        f"/orders/{order['id']}/ocr-apply",
        json={
            **_sheet_payload(quantity="7", note="draft-applied", row_id="row-apply-clears-draft-1"),
            "expected_revision_id": current_revision_id,
            "expected_lines_updated_at": original_lines_updated_at,
        },
    )
    assert apply_res.status_code == 200

    refreshed_detail = client.get(f"/orders/{order['id']}")
    assert refreshed_detail.status_code == 200
    refreshed_order = refreshed_detail.json()
    assert refreshed_order["ocr_has_saved_draft"] is False
    assert refreshed_order["ocr_draft_newer_than_lines"] is False
    assert refreshed_order["ocr_auto_apply_blocked"] is False
    assert "下書きあり" not in (refreshed_order.get("ocr_review_badges") or [])
    assert "draft_newer_than_lines" not in (refreshed_order.get("ocr_confirm_blockers") or [])

    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 200
    sheet_payload = sheet_res.json()
    assert sheet_payload["has_saved_draft"] is False
    assert sheet_payload["draft_newer_than_lines"] is False
    assert "draft_not_applied" not in (sheet_payload.get("confirm_blockers") or [])


def test_ocr_sheet_save_returns_stale_lines_conflict_after_apply_changes_lines():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-save-stale-lines")

    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    order_detail = detail.json()
    original_lines_updated_at = order_detail["lines_updated_at"]

    save_res = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            **_sheet_payload(quantity="7", note="draft-before-apply", row_id="row-save-stale-lines-1"),
            "expected_lines_updated_at": original_lines_updated_at,
        },
    )
    assert save_res.status_code == 200
    current_revision_id = str(save_res.json()["revision"]["revision_id"])

    apply_res = client.post(
        f"/orders/{order['id']}/ocr-apply",
        json={
            **_sheet_payload(quantity="7", note="draft-applied", row_id="row-save-stale-lines-1"),
            "expected_revision_id": current_revision_id,
            "expected_lines_updated_at": original_lines_updated_at,
        },
    )
    assert apply_res.status_code == 200

    stale_save = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            **_sheet_payload(quantity="7", note="late-save", row_id="row-save-stale-lines-1"),
            "expected_lines_updated_at": original_lines_updated_at,
        },
    )
    assert stale_save.status_code == 409
    detail = stale_save.json().get("detail") or {}
    assert detail.get("error") == "stale_lines_conflict"


def test_confirm_returns_stale_revision_conflict_when_draft_changed_elsewhere():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-stale-confirm")

    first = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json=_sheet_payload(quantity="7", note="confirm-1", row_id="row-stale-confirm-1"),
    )
    assert first.status_code == 200
    first_revision_id = str(order_service._current_sheet_revision_id(order_id=order["id"]))
    assert first_revision_id

    second = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            **_sheet_payload(quantity="8", note="confirm-2", row_id="row-stale-confirm-2"),
            "expected_revision_id": first_revision_id,
        },
    )
    assert second.status_code == 200

    current_order = order_service.get_order_by_id(order["id"])
    assert isinstance(current_order, dict)
    stale_confirm = client.post(
        f"/orders/{order['id']}/confirm",
        json={
            "expected_revision_id": first_revision_id,
            "expected_lines_updated_at": current_order["lines_updated_at"],
        },
    )
    assert stale_confirm.status_code == 409
    detail = stale_confirm.json().get("detail") or {}
    assert detail.get("error") == "stale_revision_conflict"


def test_confirm_route_defers_post_confirm_side_effects_to_background(monkeypatch):
    background_tasks = orders_api.BackgroundTasks()
    order_id = "ORD-CONFIRM-ACK-001"
    order_payload = {"id": order_id, "lines_updated_at": "2026-02-15T09:00:00Z"}
    finalize_called = False

    monkeypatch.setattr(orders_api.order_service, "get_order_by_id", lambda _order_id: dict(order_payload))
    monkeypatch.setattr(orders_api.order_service, "_sheet_revision_conflict_detail", lambda **_kwargs: None)
    monkeypatch.setattr(orders_api.order_service, "_lines_timestamp_conflict_detail", lambda **_kwargs: None)
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_workflow_state",
        lambda _order_id, refresh=False: {
            "state": "apply_ready",
            "apply_gate": {"can_confirm": True, "confirm_blockers": [], "confirm_warnings": []},
        },
    )
    monkeypatch.setattr(
        orders_api.order_service,
        "confirm_order_authoritatively",
        lambda _order_id: (
            {"id": _order_id, "status": "確定"},
            {"order_id": _order_id, "confirmed_facility": "FAC00001", "confirmed_week": "2026-02"},
        ),
    )

    def _mark_finalize_called(_payload):
        nonlocal finalize_called
        finalize_called = True

    monkeypatch.setattr(orders_api.order_service, "finalize_confirmed_order", _mark_finalize_called)

    res = orders_api.confirm_order(
        order_id,
        background_tasks,
        {
            "expected_revision_id": "ODR-confirm-1",
            "expected_lines_updated_at": order_payload["lines_updated_at"],
        },
    )

    assert res == {"accepted": True}
    assert finalize_called is False
    task_names = [getattr(task.func, "__name__", "") for task in background_tasks.tasks]
    assert "_mark_finalize_called" in task_names
    assert "_enqueue_outputs_after_confirm" in task_names


def test_confirm_uses_authoritative_current_sheet_revision_not_stale_cached_payload():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-confirm-authoritative-current")

    save_res = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json=_sheet_payload(quantity="7", note="confirm-current", row_id="row-confirm-current-1"),
    )
    assert save_res.status_code == 200
    current_order = order_service.get_order_by_id(order["id"])
    assert isinstance(current_order, dict)
    current_revision_id = str(order_service._current_sheet_revision_id(order_id=order["id"]))
    assert current_revision_id

    cached_payload = order_service.get_cached_ocr_payload(order["id"]) or {}
    cached_payload["current_sheet_revision_id"] = "stale-cache-revision"
    order_service._save_order_ocr_cache(order["id"], cached_payload)

    confirm_res = client.post(
        f"/orders/{order['id']}/confirm",
        json={
            "expected_revision_id": current_revision_id,
            "expected_lines_updated_at": current_order["lines_updated_at"],
        },
    )

    assert confirm_res.status_code == 202
    confirmed = order_service.get_order_by_id(order["id"])
    assert isinstance(confirmed, dict)
    assert confirmed["status"] == "確定"


def test_facility_and_week_updates_return_stale_conflicts():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-stale-selections")

    detail = client.get(f"/orders/{order['id']}")
    assert detail.status_code == 200
    current_order = detail.json()
    original_facility = current_order["facility"]
    original_week = current_order["week"]

    facility_res = client.post(
        f"/orders/{order['id']}/facility",
        json={"facility": "FAC00002", "expected_current_facility": original_facility},
    )
    assert facility_res.status_code == 200
    stale_facility = client.post(
        f"/orders/{order['id']}/facility",
        json={"facility": "FAC00003", "expected_current_facility": original_facility},
    )
    assert stale_facility.status_code == 409
    assert (stale_facility.json().get("detail") or {}).get("error") == "stale_facility_conflict"

    week_res = client.post(
        f"/orders/{order['id']}/week",
        json={"week": "2026-03", "expected_current_week": original_week},
    )
    assert week_res.status_code == 200
    stale_week = client.post(
        f"/orders/{order['id']}/week",
        json={"week": "2026-04", "expected_current_week": original_week},
    )
    assert stale_week.status_code == 409
    assert (stale_week.json().get("detail") or {}).get("error") == "stale_week_conflict"


def test_week_save_conflict_guard_accepts_current_promoted_week_value():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order(
        "msg-draft-review-current-promoted-week",
        week_hint="2026-02",
        received_at=datetime(2026, 2, 15, 9, 0, 0),
        line_date="2026-02-15",
    )

    order_res = client.get(f"/orders/{order['id']}")
    assert order_res.status_code == 200
    current_order = order_res.json()
    assert current_order["week"] == "2026-02"
    assert current_order["week_value"] == "2026-02@2026-02-15~2026-02-21"
    assert current_order["persisted_week_value"] == "2026-02@2026-02-15~2026-02-21"

    save_res = client.post(
        f"/orders/{order['id']}/week",
        json={
            "week": "2026-02@2026-02-16~2026-02-22",
            "expected_current_week": current_order["persisted_week_value"],
        },
    )
    assert save_res.status_code == 200

    refreshed = client.get(f"/orders/{order['id']}")
    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["week_value"] == "2026-02@2026-02-16~2026-02-22"
    assert payload["persisted_week_value"] == "2026-02@2026-02-16~2026-02-22"


def test_order_endpoints_expose_reparse_stage_and_retained_lines_for_recoverable_result():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-stage")

    save_res = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["02/15", "朝", "Menu A", "9", "draft"]],
            "ui_mode": "sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "row_ids": ["row-draft-stage-1"],
        },
    )
    assert save_res.status_code == 200

    create_job(f"OCR-{order['id']}", input_reference="file://dummy.pdf", status="done")
    update_job(
        f"OCR-{order['id']}",
        status="done",
        error_message=None,
        metrics={
            "provider": "gemini",
            "processing_stage": "draft_saved",
            "result_state": "draft_ready_blocked",
            "confirmed_lines_retained": True,
            "error": "sheet_llm_audit_failed",
        },
    )

    detail_res = client.get(f"/orders/{order['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["ocr_status"] == "done"
    assert detail["ocr_review_state"] == "draft_ready"
    assert detail["ocr_processing_stage"] == "draft_saved"
    assert detail["ocr_result_state"] == "draft_ready_blocked"
    assert detail["ocr_confirmed_lines_retained"] is True

    list_res = client.get("/orders")
    assert list_res.status_code == 200
    row = next(item for item in list_res.json()["orders"] if item["id"] == order["id"])
    assert row["ocr_processing_stage"] == "draft_saved"
    assert row["ocr_result_state"] == "draft_ready_blocked"
    assert row["ocr_confirmed_lines_retained"] is True


def test_reparse_endpoint_blocks_when_reparse_job_is_already_running():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-reparse-running")

    create_job(f"OCR-{order['id']}", input_reference="file://dummy.pdf", status="running")
    update_job(
        f"OCR-{order['id']}",
        status="running",
        metrics={"request_mode": "ocr_reparse", "processing_stage": "llm_reparse", "result_state": "processing"},
    )

    res = client.post(f"/orders/{order['id']}/reparse", json={"ocr_provider": "gemini"})

    assert res.status_code == 409
    detail = res.json().get("detail") or {}
    assert detail.get("error") == "reparse_in_progress"
    assert detail.get("ocr_job_id") == f"OCR-{order['id']}"


def test_stale_reparse_job_syncs_workflow_v2_wait_state(monkeypatch):
    order_id = "ORD-stale-workflow-sync"
    job_id = f"OCR-{order_id}"
    job = {
        "id": job_id,
        "order_id": order_id,
        "status": "running",
        "updated_at": datetime.utcnow() - timedelta(minutes=45),
        "metrics": {
            "request_mode": "ocr_reparse",
            "processing_stage": "hakodate_live_pipeline",
            "result_state": "processing",
        },
    }
    updates: list[dict] = []
    marked: list[dict] = []
    monkeypatch.setenv("OCR_JOB_STALE_MINUTES", "1")
    monkeypatch.setattr(orders_api.order_service, "get_cached_ocr_payload", lambda _order_id: None)
    monkeypatch.setattr(
        orders_api.order_service,
        "get_order_review_summary",
        lambda *_args, **_kwargs: {"ocr_has_saved_draft": False},
    )
    monkeypatch.setattr(
        orders_api,
        "update_ocr_job",
        lambda job_id_arg, **kwargs: updates.append({"job_id": job_id_arg, **kwargs}),
    )
    monkeypatch.setattr(orders_api, "get_ocr_job", lambda _job_id: {**job, "status": "failed"})
    monkeypatch.setattr(
        orders_api.order_workflow_v2_service,
        "get_workflow",
        lambda _order_id: (
            {
                "state": "ocr_running",
                "ocr_job": {"id": job_id},
            },
            None,
        ),
    )
    monkeypatch.setattr(
        orders_api.order_workflow_v2_service,
        "mark_ocr_run_completed",
        lambda order_id_arg, *, job_id, error: marked.append(
            {"order_id": order_id_arg, "job_id": job_id, "error": error}
        )
        or ({"state": "ocr_failed"}, None),
    )

    refreshed = orders_api._mark_stale_order_reparse_job({"id": order_id}, job)

    assert refreshed["status"] == "failed"
    assert updates[0]["status"] == "failed"
    assert updates[0]["error_message"].startswith("reparse_stale_timeout>")
    assert marked == [
        {
            "order_id": order_id,
            "job_id": job_id,
            "error": updates[0]["error_message"],
        }
    ]


def test_workflow_v2_ocr_rerun_retries_recovering_stale_job(monkeypatch):
    order_id = "ORD-stale-workflow-v2-rerun"
    job_id = f"OCR-{order_id}"
    stale_job = {
        "id": job_id,
        "order_id": order_id,
        "status": "running",
        "updated_at": datetime.utcnow() - timedelta(minutes=45),
        "input_reference": "file:///old.pdf",
        "metrics": {
            "request_mode": "ocr_rerun",
            "workflow_version": "v2",
            "processing_stage": "ocr_pipeline",
            "result_state": "processing",
        },
    }
    marked: list[str] = []
    updated: list[dict] = []
    queued: list[str] = []
    monkeypatch.setenv("OCR_JOB_STALE_MINUTES", "1")
    monkeypatch.setattr(
        orders_api.order_workflow_v2_service,
        "get_workflow",
        lambda _order_id: (
            {
                "state": "ocr_running",
                "facility_id": "FAC00001",
                "week_start": "2026-06-07",
                "week_end": "2026-06-13",
                "template_id": "template-fac00001",
                "template_version_id": "FTV-1",
                "ocr_job": {"id": job_id},
            },
            None,
        ),
    )
    monkeypatch.setattr(
        orders_api.order_workflow_v2_service,
        "workflow_has_confirmed_ocr_context",
        lambda _workflow: True,
    )
    monkeypatch.setattr(
        orders_api.order_workflow_v2_service,
        "mark_ocr_run_queued",
        lambda _order_id, _job_id: queued.append(_job_id)
        or ({"state": "ocr_running", "ocr_job": {"id": _job_id}}, None),
    )
    monkeypatch.setattr(orders_api, "_resolve_order_document_version", lambda _order_id, _selected_document_id: (
        {"id": order_id, "facility": "FAC00001", "lines_updated_at": None},
        {"storage_uri": "gs://bucket/current.pdf", "document_id": "DOC-1", "version_no": 1, "is_current": True},
    ))
    monkeypatch.setattr(orders_api.config_service, "get_facility_config", lambda _facility_id: {"enabled": True})
    monkeypatch.setattr(orders_api, "get_ocr_job", lambda _job_id: stale_job if not marked else {**stale_job, "status": "failed"})
    monkeypatch.setattr(
        orders_api,
        "_mark_stale_order_reparse_job",
        lambda _order, job, **_kwargs: marked.append(job["id"]) or {**job, "status": "failed"},
    )
    monkeypatch.setattr(orders_api, "create_ocr_job", lambda *_args, **_kwargs: ({}, False))
    monkeypatch.setattr(
        orders_api,
        "update_ocr_job",
        lambda job_id_arg, **kwargs: updated.append({"job_id": job_id_arg, **kwargs}),
    )

    result = orders_api._enqueue_workflow_v2_evidence_rerun(order_id, BackgroundTasks(), stale_action="retry")

    assert result["accepted"] is True
    assert marked == [job_id]
    assert updated[0]["status"] == "running"
    assert queued == [job_id]


def test_stale_reparse_job_is_marked_failed_and_allows_retry(monkeypatch):
    order_service.clear_all()
    monkeypatch.setenv("OCR_JOB_STALE_MINUTES", "1")
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-stale-reparse")

    save_res = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["02/15", "朝", "Menu A", "9", "draft"]],
            "ui_mode": "sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "row_ids": ["row-draft-stale-1"],
        },
    )
    assert save_res.status_code == 200

    create_job(f"OCR-{order['id']}", input_reference="file://dummy.pdf", status="running")
    update_job(
        f"OCR-{order['id']}",
        status="running",
        error_message=None,
        metrics={
            "request_mode": "ocr_reparse",
            "processing_stage": "llm_review",
            "result_state": "running",
        },
    )
    with session_scope() as session:
        job = session.get(OcrJob, f"OCR-{order['id']}")
        assert job is not None
        job.updated_at = datetime.utcnow() - timedelta(minutes=45)
        session.add(job)

    list_res = client.get("/orders")
    assert list_res.status_code == 200
    row = next(item for item in list_res.json()["orders"] if item["id"] == order["id"])
    assert row["ocr_status"] == "failed"
    assert row["ocr_error"].startswith("reparse_stale_timeout>")
    assert row["ocr_review_state"] == "draft_ready"
    assert row["ocr_processing_stage"] == "stale_timeout"
    assert row["ocr_result_state"] == "draft_ready_blocked"
    assert row["ocr_confirmed_lines_retained"] is False
    assert row["ocr_reparse_health"] == "hard_failed"
    assert row["ocr_revision_count"] >= 1
    assert row["ocr_revision_last_id"]

    detail_res = client.get(f"/orders/{order['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["ocr_status"] == "failed"
    assert detail["ocr_error"].startswith("reparse_stale_timeout>")
    assert detail["ocr_processing_stage"] == "stale_timeout"
    assert detail["ocr_result_state"] == "draft_ready_blocked"
    assert detail["ocr_has_saved_draft"] is True
    assert detail["ocr_reparse_health"] == "hard_failed"
    assert detail["ocr_reparse_last_job_id"] == f"OCR-{order['id']}"
    assert detail["ocr_reparse_stale_threshold_seconds"] == 60

    sheet_res = client.get(f"/orders/{order['id']}/ocr-sheet")
    assert sheet_res.status_code == 200
    sheet_payload = sheet_res.json()
    assert sheet_payload["reparse_health"] == "hard_failed"

    stale_job = get_job(f"OCR-{order['id']}")
    assert stale_job is not None
    assert stale_job["status"] == "failed"
    assert stale_job["error_message"].startswith("reparse_stale_timeout>")
    assert stale_job["metrics"]["stale_recovered"] is True

    wait_res = client.post(
        f"/orders/{order['id']}/reparse",
        json={"ocr_provider": "gemini", "stale_action": "wait"},
    )
    assert wait_res.status_code == 202

    monkeypatch.setattr(orders_api, "_run_reparse_background", lambda *args, **kwargs: None)
    rerun_res = client.post(f"/orders/{order['id']}/reparse", json={"ocr_provider": "gemini"})
    assert rerun_res.status_code == 202
    rerun_job = get_job(f"OCR-{order['id']}")
    assert rerun_job is not None
    assert rerun_job["status"] == "running"
    assert rerun_job["metrics"]["processing_stage"] == "queued"


def test_persisted_ocr_revision_history_survives_missing_cache_payload():
    order_service.clear_all()
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-persisted-revision")

    save_res = client.post(
        f"/orders/{order['id']}/ocr-sheet-save",
        json={
            "header": ["日付", "区分", "メニュー", "常食2F", "備考"],
            "rows": [["02/15", "朝", "Menu A", "8", "persisted"]],
            "ui_mode": "sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
            "row_ids": ["row-draft-persisted-1"],
        },
    )
    assert save_res.status_code == 200

    with session_scope() as session:
        cache = session.get(OrderOcrCache, order["id"])
        assert cache is not None
        session.delete(cache)

    detail_res = client.get(f"/orders/{order['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["ocr_has_saved_draft"] is True
    assert detail["ocr_draft_row_count"] == 1
    assert detail["ocr_review_state"] == "draft_ready"

    history_res = client.get(f"/orders/{order['id']}/ocr-history")
    assert history_res.status_code == 200
    payload = history_res.json()
    assert payload["latest"]["row_count"] == 1
    assert payload["latest"]["rows"][0][3] == "8"
    assert len(payload["revisions"]) >= 1

    order_history_res = client.get(f"/orders/{order['id']}/history")
    assert order_history_res.status_code == 200
    history_items = order_history_res.json().get("items") or []
    assert any(item.get("action") == "ocr_table_apply" for item in history_items)


def test_reparse_endpoint_returns_recoverable_conflict_when_stale_action_wait(monkeypatch):
    order_service.clear_all()
    monkeypatch.setenv("OCR_JOB_STALE_MINUTES", "1")
    client = TestClient(app)
    order = _create_seed_order("msg-draft-review-stale-wait")

    create_job(f"OCR-{order['id']}", input_reference="file://dummy.pdf", status="running")
    update_job(
        f"OCR-{order['id']}",
        status="running",
        metrics={
            "request_mode": "ocr_reparse",
            "processing_stage": "llm_reparse",
            "result_state": "processing",
            "stage_updated_at": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        },
    )

    res = client.post(
        f"/orders/{order['id']}/reparse",
        json={"ocr_provider": "gemini", "stale_action": "wait"},
    )

    assert res.status_code == 409
    detail = res.json().get("detail") or {}
    assert detail.get("error") == "reparse_in_progress"
    assert detail.get("recoverable") is True
    assert detail.get("stale_at")
    assert detail.get("stale_threshold_seconds") == 60


def test_stale_reparse_keeps_success_status_when_cached_ocr_evidence_exists(monkeypatch):
    order_service.clear_all()
    monkeypatch.setenv("OCR_JOB_STALE_MINUTES", "1")
    client = TestClient(app)
    order = _create_seed_order("msg-stale-success-status")

    create_job(f"OCR-{order['id']}", input_reference="file://dummy.pdf", status="running")
    update_job(
        f"OCR-{order['id']}",
        status="running",
        metrics={
            "request_mode": "ocr_reparse",
            "processing_stage": "inference",
            "result_state": "processing",
        },
    )
    with session_scope() as session:
        job = session.get(OcrJob, f"OCR-{order['id']}")
        assert job is not None
        job.updated_at = datetime.utcnow() - timedelta(minutes=5)
        session.add(job)

    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "done",
            "template_id": "fax_layout_regular_2f3f_v1",
            "pages": [{"page_index": 0, "ocr_overlay_url": "https://example.com/ocr.pdf"}],
            "table_raw": "|日付|区分|メニュー|常食2F|\n|03/22|朝|Menu A|5|",
        },
    )

    detail_res = client.get(f"/orders/{order['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["ocr_status"] == "failed"
    assert detail["ocr_error"].startswith("reparse_stale_timeout>")
    assert detail["ocr_reparse_health"] == "hard_failed"

    stale_job = get_job(f"OCR-{order['id']}")
    assert stale_job is not None
    assert stale_job["status"] == "failed"
    assert stale_job["error_message"].startswith("reparse_stale_timeout>")
