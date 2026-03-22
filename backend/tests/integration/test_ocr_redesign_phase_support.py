import sys
import pathlib
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import config_service, order_service  # noqa: E402
from src.workers.ingest_mail_adapter import IngestEmailPayload  # noqa: E402


def _seed_order(*, message_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 3, 21, 9, 0, 0),
        facility_hint="FAC00001",
        week_hint=None,
    )
    return order_service.create_order_from_ingest(
        payload,
        lines=[
            {
                "date": "2026-03-21",
                "daypart": "朝",
                "menu_name": "Menu A",
                "diet_type": "regular",
                "area_id": "2F",
                "bag_type": "standard",
                "quantity_original": 2,
            }
        ],
    )


def test_get_order_review_summary_maps_blocked_saved_draft_to_user_facing_states():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-phase-state-001")

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F", "備考"],
        rows=[["03/21", "朝", "Menu A", "7", "draft"]],
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    cached_payload = order_service._load_order_ocr_cache(order["id"]) or {}
    cached_payload["_reparse_debug"] = {
        "error": "sheet_structural_projection_requires_review",
        "reject_reasons": ["sheet_structural_projection_requires_review"],
    }
    order_service._save_order_ocr_cache(order["id"], cached_payload)

    summary = order_service.get_order_review_summary(
        order["id"],
        ocr_status="done",
    )

    assert summary["ocr_review_state"] == "draft_ready"
    assert summary["ocr_review_stage"] == "needs_human_review"
    assert summary["ocr_reparse_status"] == "blocked"
    assert summary["ocr_has_saved_draft"] is True
    assert summary["ocr_auto_apply_blocked"] is True
    assert summary["ocr_draft_row_count"] == 1
    assert "draft_newer_than_lines" in (summary.get("ocr_confirm_blockers") or [])
    assert any(
        item.get("code") == "draft_newer_than_lines"
        for item in (summary.get("ocr_confirm_blocker_details") or [])
    )
    assert any(
        item.get("code") == "auto_apply_blocked"
        for item in (summary.get("ocr_confirm_warning_details") or [])
    )


def test_prepare_llm_review_output_payload_preserves_evidence_for_applied_and_unresolved_changes():
    baseline = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
        "header": ["日付", "区分", "メニュー", "常食", "備考"],
        "rows": [
            ["03/21", "朝", "Menu A", "11", ""],
            ["03/21", "昼", "Menu B", "8", ""],
        ],
        "row_ids": ["row-1", "row-2"],
        "raw_output": {
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": [
                        ["日付", "区分", "メニュー", "常食", "備考"],
                        ["03/21", "朝", "Menu A", "11", ""],
                        ["03/21", "昼", "Menu B", "8", ""],
                    ],
                }
            ]
        },
    }
    payload = {
        "rows": [
            ["03/21", "朝", "Menu A", "7", ""],
            ["03/21", "昼", "Menu B", "8", ""],
        ],
        "cell_issues": [
            {
                "row_id": "row-1",
                "field": "qty.regular_x",
                "issue_code": "ocr_misread",
                "confidence": 0.91,
                "evidence": "single digit 7 visible",
                "reason": "correct visible digit",
                "page_index": 1,
                "table_id": "p1_t1",
            },
            {
                "row_id": "row-2",
                "field": "remarks",
                "issue_code": "note_unclear",
                "confidence": 0.82,
                "evidence": "remarks area still unclear",
                "reason": "leave blank until human review",
                "page_index": 1,
                "table_id": "p1_t1",
            },
        ],
        "llm_review": {
            "status": "verified",
            "notes": "Corrected the breakfast quantity only.",
        },
    }
    template = {
        "main_ocr_row_fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "remarks"],
    }

    prepared = order_service._prepare_llm_review_output_payload(
        payload=payload,
        baseline=baseline,
        template=template,
        pdf_variant_requested="corrected",
        pdf_variant_used="raw",
        pdf_variant_fallback_reason="corrected_pdf_unavailable_in_backend_cache",
    )

    assert prepared is not None
    assert len(prepared["applied_overwrites"]) == 1
    assert prepared["applied_overwrites"][0]["row_id"] == "row-1"
    assert prepared["applied_overwrites"][0]["field"] == "qty.regular_x"
    assert prepared["applied_overwrites"][0]["evidence"] == "single digit 7 visible"
    assert len(prepared["issues"]) == 1
    unresolved = prepared["issues"][0]
    assert unresolved["issue_id"] == "llm-review-2"
    assert unresolved["row_id"] == "row-2"
    assert unresolved["field"] == "remarks"
    assert unresolved["issue_code"] == "note_unclear"
    assert unresolved["current_text"] == ""
    assert unresolved["confidence"] == 0.82
    assert unresolved["evidence"] == "remarks area still unclear"
    assert unresolved["reason"] == "leave blank until human review"
    assert unresolved["table_id"] == "p1_t1"
    assert unresolved["page_index"] == 1
    assert unresolved["row_index"] == 1
    assert unresolved["source_row_index"] == 1
    assert unresolved["column_index"] == 4
    assert unresolved["col_index"] == 4
    assert prepared["needs_more_review"] is True
    assert prepared["output_payload"]["cell_issues"][0]["evidence"] == "remarks area still unclear"
    assert prepared["output_payload"]["llm_review"]["pdf_variant_requested"] == "corrected"
    assert prepared["output_payload"]["llm_review"]["pdf_variant_used"] == "raw"
    assert (
        prepared["output_payload"]["llm_review"]["pdf_variant_fallback_reason"]
        == "corrected_pdf_unavailable_in_backend_cache"
    )


def test_get_ocr_pages_defers_grid_recovery_when_template_metadata_is_partial(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-phase-pages-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "markdown_uri": None,
                    "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                    "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                    "figure_uris": [],
                }
            ]
        },
    )

    monkeypatch.setattr(
        config_service,
        "get_facility_config",
        lambda facility_id: {
            "fax_template": {
                "table_box": [0.1, 0.2, 0.9, 0.8],
                "grid_column_edges": [0.1, 0.5, 0.9],
            }
        },
    )
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)
    monkeypatch.setattr(
        order_service,
        "load_bytes_from_uri",
        lambda uri: (_ for _ in ()).throw(AssertionError("request path should not fetch overlay bytes")),
    )
    monkeypatch.setattr(
        order_service,
        "detect_table_grid_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("request path should not recompute grid image")),
    )
    monkeypatch.setattr(
        order_service,
        "detect_table_grid",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("request path should not recompute pdf grid")),
    )

    pages, error = order_service.get_ocr_pages(order["id"])

    assert error is None
    assert isinstance(pages, dict)
    assert len(pages["pages"]) == 1
    assert pages["table_box"] == [0.1, 0.2, 0.9, 0.8]
    assert pages["grid_column_edges"] == [0.1, 0.5, 0.9]
    assert pages["grid_row_edges"] is None
    assert pages["grid_detection_status"] == "deferred"
    assert pages["grid_detection_deferred_reason"] == "missing_template_grid_metadata:grid_row_edges"


def test_get_ocr_sheet_prefers_saved_revision_over_confirmed_lines_by_default():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-phase-step2-evidence-only")

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F", "備考"],
        rows=[["03/21", "朝", "Menu A", "9", "draft"]],
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )
    assert error is None
    assert saved is not None

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": "Menu A",
            "menu_date": date(2026, 3, 21),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 0,
        }
    ]

    try:
        order_service._save_order_ocr_cache(
            order["id"],
            {
                "table_raw": "|日付|区分|メニュー|常食2F|備考|\n|---|---|---|---|---|\n|03/21|朝|Menu A|2||",
                "pages": [
                    {
                        "page_index": 1,
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
                "quantity_subgrid_passes": [],
                "template_resolution": {"resolved_template_id": "tpl-1", "blocked": False, "blocked_reasons": []},
                "table_box": [0.1, 0.2, 0.9, 0.8],
                "grid_column_edges": [0.1, 0.5, 0.9],
                "grid_row_edges": [0.2, 0.4, 0.8],
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["rows"][0][3] == "9"
    assert sheet["source"].startswith("draft_sheet")


def test_build_recoverable_ocr_sheet_payload_never_uses_confirmed_lines_fallback():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-phase-recoverable-no-confirmed-lines")

    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/21|朝|Menu A|4|",
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                    "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                    "figure_uris": [],
                }
            ],
            "template_resolution": {"resolved_template_id": "tpl-1", "blocked": False, "blocked_reasons": []},
            "table_box": [0.1, 0.2, 0.9, 0.8],
            "grid_column_edges": [0.1, 0.5, 0.9],
            "grid_row_edges": [0.2, 0.4, 0.8],
        },
    )

    sheet, error = order_service.build_recoverable_ocr_sheet_payload(
        order["id"],
        "ocr_evidence_recovery_required",
    )

    assert error is None
    assert isinstance(sheet, dict)
    assert sheet.get("recovery_source") != "confirmed_lines"
    assert (sheet.get("trace") or {}).get("mapped_mode") != "confirmed_lines"
