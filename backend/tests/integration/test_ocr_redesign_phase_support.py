import sys
import pathlib
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import config_service, order_service, template_resolution_service  # noqa: E402
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


def _seed_order(*, message_id: str) -> dict:
    return _seed_order_for_facility(message_id=message_id, facility_id="FAC00001")


def _seed_order_for_facility(*, message_id: str, facility_id: str) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 3, 21, 9, 0, 0),
        facility_hint=facility_id,
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
                "area_id": "X" if facility_id == "FAC00002" else "2F",
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
    assert "draft_newer_than_lines" not in (summary.get("ocr_confirm_blockers") or [])
    assert all(
        item.get("code") != "draft_newer_than_lines"
        for item in (summary.get("ocr_confirm_blocker_details") or [])
    )
    assert any(
        item.get("code") == "auto_apply_blocked"
        for item in (summary.get("ocr_confirm_warning_details") or [])
    )
    assert summary["ocr_can_confirm"] is True


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


def test_get_ocr_output_and_sheet_prefer_active_evidence_over_stale_cache(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-active-evidence-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/21|朝|Menu A|3|",
            "table_rows": [["03/21", "朝", "Menu A", "3"]],
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/stale-overlay.png",
                    "layout_overlay_uri": "gs://bucket/stale-layout.png",
                    "figure_uris": [],
                }
            ],
            "template_resolution": {
                "requested_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "requested_template_ids": [
                    "fax_layout_regular_soft_mixer_forbidden_v1",
                    "fax_layout_floor_2f3f_v1",
                ],
                "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "matched_template_id": "fax_layout_floor_2f3f_v1",
                "blocked": True,
                "blocked_reasons": ["template_mismatch"],
            },
            "table_box": None,
            "grid_column_edges": [],
            "grid_row_edges": [],
        },
    )
    order_service.persist_ocr_evidence_run(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/21|朝|Menu A|9|",
            "table_rows": [["03/21", "朝", "Menu A", "9"]],
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/fresh-overlay.png",
                    "layout_overlay_uri": "gs://bucket/fresh-layout.png",
                    "figure_uris": [],
                }
            ],
            "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/21", "朝", "Menu A", "9"]]}],
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
        schema_version="v2_evidence_rerun",
        producer_version="test",
        source="ocr-rerun",
    )

    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda *_args, **_kwargs: [
            {
                "menu_name": "Menu A",
                "menu_date": date(2026, 3, 21),
                "daypart_key": "breakfast",
                "slot_index": 0,
                "order": 0,
            }
        ],
    )

    payload, payload_error = order_service.get_ocr_output(order["id"], persist_cache=False)
    assert payload_error is None
    assert isinstance(payload, dict)
    assert "|03/21|朝|Menu A|9|" in str(payload.get("table_raw") or "")

    sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet["rows"][0][:4] == ["03/21", "breakfast", "Menu A", "9"]


def test_get_ocr_output_prefers_active_evidence_over_stale_job_output(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-active-over-job-001")
    order_service.persist_ocr_evidence_run(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/21|朝|Menu A|9|",
            "table_rows": [["03/21", "朝", "Menu A", "9"]],
            "pages": [{"page_index": 1}],
            "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/21", "朝", "Menu A", "9"]]}],
        },
        schema_version="v2_evidence_rerun",
        producer_version="test",
        source="ocr-rerun",
    )
    monkeypatch.setattr(
        order_service,
        "get_ocr_job",
        lambda _job_id: {
            "id": f"OCR-{order['id']}",
            "status": "done",
            "output_reference": "gs://bucket/stale-output.json",
            "metrics": {},
        },
    )
    monkeypatch.setattr(
        order_service,
        "_load_job_output",
        lambda _job, _source: {
            "table_raw": "|日付|区分|メニュー|常食2F|\n|---|---|---|---|\n|03/21|朝|Menu A|3|",
            "table_rows": [["03/21", "朝", "Menu A", "3"]],
            "pages": [{"page_index": 1}],
        },
    )

    payload, payload_error = order_service.get_ocr_output(order["id"], persist_cache=False)

    assert payload_error is None
    assert isinstance(payload, dict)
    assert "|03/21|朝|Menu A|9|" in str(payload.get("table_raw") or "")


def test_get_ocr_pages_prefers_active_evidence_over_stale_cache(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-pages-active-evidence-001")
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/stale-overlay.png",
                    "layout_overlay_uri": "gs://bucket/stale-layout.png",
                    "figure_uris": [],
                }
            ]
        },
    )
    order_service.persist_ocr_evidence_run(
        order["id"],
        {
            "pages": [
                {
                    "page_index": 1,
                    "ocr_overlay_uri": "gs://bucket/fresh-overlay.png",
                    "layout_overlay_uri": "gs://bucket/fresh-layout.png",
                    "figure_uris": [],
                }
            ],
            "template_resolution": {
                "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "blocked": False,
                "blocked_reasons": [],
            },
        },
        schema_version="v2_evidence_rerun",
        producer_version="test",
        source="ocr-rerun",
    )

    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}" if uri else None)

    pages, error = order_service.get_ocr_pages(order["id"])

    assert error is None
    assert isinstance(pages, dict)
    assert pages["pages"][0]["ocr_overlay_uri"] == "gs://bucket/fresh-overlay.png"
    assert pages["pages"][0]["ocr_overlay_url"] == "signed:gs://bucket/fresh-overlay.png"


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


def test_get_ocr_sheet_keeps_semantic_sheet_when_template_resolution_is_blocked():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-template-blocked-semantic")

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
                "template_resolution": {
                    "resolved_template_id": None,
                    "blocked": True,
                    "blocked_reasons": ["template_resolution_missing"],
                },
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
    assert sheet["fields"][:4] == ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    assert sheet["rows"][0][:4] == ["03/21", "breakfast", "Menu A", ""]
    assert "template_resolution_blocked" in (sheet.get("warnings") or [])
    assert "sheet_payload_mapping_blocked_unresolved_template" in (sheet.get("warnings") or [])


def test_get_ocr_sheet_does_not_project_payload_quantities_when_template_semantics_are_missing():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-template-missing-payload-blocked")

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
                "table_raw": "|日付|区分|メニュー|常食2F|備考|\n|---|---|---|---|---|\n|03/21|朝|Menu A|9||",
                "pages": [
                    {
                        "page_index": 1,
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu"
    assert sheet["fields"][:4] == ["date_mmdd", "daypart", "menu", "qty.regular_2f"]
    assert sheet["rows"][0][:4] == ["03/21", "breakfast", "Menu A", ""]
    assert "sheet_payload_mapping_blocked_unresolved_template" in (sheet.get("warnings") or [])


def test_get_ocr_sheet_projects_payload_quantities_from_raw_table_when_header_fully_covers_template():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-raw-header-strong-template")

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
                "table_raw": "\n".join(
                    [
                        "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|",
                        "|---|---|---|---|---|---|---|---|---|---|",
                        "|03/21|朝|Menu A|9|8|7|6|5|4||",
                    ]
                ),
                "pages": [
                    {
                        "page_index": 1,
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
                "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet["rows"][0][:9] == ["03/21", "breakfast", "Menu A", "9", "8", "7", "6", "5", "4"]
    assert "sheet_payload_mapping_blocked_unresolved_template" not in (sheet.get("warnings") or [])


def test_get_ocr_sheet_projects_payload_quantities_from_position_fallback_when_template_semantics_are_missing():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-position-fallback-grid")
    rows = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/21", "朝", "Menu A", "9", "8", "7", "6", "5", "4", ""],
    ]

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
                "table_raw": "\n".join(
                    [
                        "|日付|区分|メニュー|常食2F|常食3F|軟菜2F|軟菜3F|ミキサー2F|ミキサー3F|備考|",
                        "|---|---|---|---|---|---|---|---|---|---|",
                        "|03/21|朝|Menu A|9|8|7|6|5|4||",
                    ]
                ),
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
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet["rows"][0][0:9] == ["03/21", "breakfast", "Menu A", "9", "8", "7", "6", "5", "4"]
    assert "sheet_payload_mapping_blocked_unresolved_template" not in (sheet.get("warnings") or [])


def test_get_ocr_sheet_projects_payload_quantities_for_noisy_regular_forbidden_headers():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-position-fallback-regular-forbidden-noisy",
        facility_id="FAC00002",
    )
    rows = [
        ["日 付", "区 分", "", "献立", "常食", "", "事故", "", "変更の", "変更の", "備考欄"],
        ["", "", "", "", "", "", "肉款", "魚炊", "", "", ""],
        ["3/22", "\"", "VF", "Menu A", "23", "", "", "", "", "", ""],
        ["", "", "48", "Menu B", "27", "", "", "", "", "", ""],
    ]

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": "Menu A",
            "menu_date": date(2026, 3, 22),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 0,
        },
        {
            "menu_name": "Menu B",
            "menu_date": date(2026, 3, 22),
            "daypart_key": "lunch",
            "slot_index": 0,
            "order": 1,
        },
    ]

    try:
        order_service._save_order_ocr_cache(
            order["id"],
            {
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
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet["rows"][0][:9] == ["03/22", "breakfast", "Menu A", "23", "", "", "", "", ""]
    assert sheet["rows"][1][:9] == ["03/22", "lunch", "Menu B", "27", "", "", "", "", ""]
    assert "sheet_payload_mapping_blocked_unresolved_template" not in (sheet.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])


def test_get_ocr_sheet_suppresses_stale_saved_draft_layout_warnings_when_position_fallback_is_ready(monkeypatch):
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-position-fallback-stale-draft-warnings",
        facility_id="FAC00002",
    )
    rows = [
        ["日 付", "区 分", "", "献立", "常食", "", "事故", "", "変更の", "変更の", "備考欄"],
        ["", "", "", "", "", "", "肉款", "魚炊", "", "", ""],
        ["3/22", "\"", "VF", "Menu A", "23", "", "", "", "", "", ""],
        ["", "", "48", "Menu B", "27", "", "", "", "", "", ""],
    ]

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": "Menu A",
            "menu_date": date(2026, 3, 22),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 0,
        },
        {
            "menu_name": "Menu B",
            "menu_date": date(2026, 3, 22),
            "daypart_key": "lunch",
            "slot_index": 0,
            "order": 1,
        },
    ]
    monkeypatch.setattr(order_service, "_maybe_refresh_semantic_sheet_draft", lambda _order_id, draft: draft)

    try:
        order_service._save_order_ocr_cache(
            order["id"],
            {
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
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
            },
        )
        saved = order_service.persist_sheet_draft(
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

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "draft_sheet"
    assert "template_unresolved" not in (sheet.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])
    assert "ocr_evidence_recovery_required" not in (sheet.get("warnings") or [])
    assert "sheet_ocr_review_required" in (sheet.get("warnings") or [])
    assert sheet["apply_blockers"] == []
    assert sheet["can_apply"] is True


def test_get_ocr_sheet_does_not_project_position_fallback_when_facility_conflicts_with_evidence():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-position-fallback-facility-conflict")
    augment_called = {"value": False}
    rows = [
        ["日付", "区分", "メニュー", "常食", "常食", "軟菜", "軟菜", "ミキサー", "ミキサー", "備考"],
        ["03/21", "朝", "Menu A", "9", "8", "7", "6", "5", "4", ""],
    ]

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    original_augment_payload_with_position_fallback = order_service.position_column_mapping_service.augment_payload_with_position_fallback
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": "Menu A",
            "menu_date": date(2026, 3, 21),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 0,
        }
    ]
    def _tracked_augment(*args, **kwargs):
        augment_called["value"] = True
        return original_augment_payload_with_position_fallback(*args, **kwargs)

    order_service.position_column_mapping_service.augment_payload_with_position_fallback = _tracked_augment

    try:
        order_service._save_order_ocr_cache(
            order["id"],
            {
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
                "pages": [
                    {
                        "page_index": 1,
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe
        order_service.position_column_mapping_service.augment_payload_with_position_fallback = (
            original_augment_payload_with_position_fallback
        )

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert augment_called["value"] is False


def test_get_ocr_sheet_does_not_project_position_fallback_when_column_mapping_is_ambiguous():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-position-fallback-ambiguous")
    rows_a = [
        ["日付", "区分", "メニュー", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/21", "朝", "Menu A", "9", "8", "7", "6", "5", "4", ""],
    ]
    rows_b = [
        ["日付", "区分", "メニュー", "補助", "常食2F", "常食3F", "軟菜2F", "軟菜3F", "ミキサー2F", "ミキサー3F", "備考"],
        ["03/21", "朝", "Menu A", "", "9", "8", "7", "6", "5", "4", ""],
    ]

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
                "pages": [
                    {
                        "page_index": 1,
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["rows"][0][0:3] == ["03/21", "breakfast", "Menu A"]
    assert sheet["rows"][0][3:9] == ["", "", "", "", "", ""]
    assert "sheet_payload_mapping_blocked_unresolved_template" in (sheet.get("warnings") or [])


def test_get_ocr_sheet_projects_payload_quantities_when_template_registry_can_supply_column_semantics():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-template-grid-registry-rescue")

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
                "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "table_raw": "|日付|区分|メニュー|常食2F|備考|\n|---|---|---|---|---|\n|03/21|朝|Menu A|9||",
                "table_rows": [["03/21", "朝", "Menu A", "9", ""]],
                "pages": [
                    {
                        "page_index": 1,
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
                "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/21", "朝", "Menu A", "9"]]}],
                "template_resolution": {
                    "requested_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                    "requested_template_ids": [
                        "fax_layout_regular_soft_mixer_forbidden_v1",
                        "fax_layout_floor_2f3f_v1",
                    ],
                    "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                    "matched_template_id": "fax_layout_floor_2f3f_v1",
                    "blocked": True,
                    "blocked_reasons": ["template_mismatch"],
                },
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet["rows"][0][:4] == ["03/21", "breakfast", "Menu A", "9"]
    assert "sheet_payload_mapping_blocked_unresolved_template" not in (sheet.get("warnings") or [])


def test_get_ocr_sheet_blocks_payload_projection_when_numeric_trust_is_low_even_with_template_semantics():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-template-grid-registry-numeric-risk")

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
                "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "table_raw": "|日付|区分|メニュー|常食2F|備考|\n|---|---|---|---|---|\n|03/21|朝|Menu A|21||",
                "table_rows": [["03/21", "朝", "Menu A", "21", ""]],
                "pages": [
                    {
                        "page_index": 1,
                        "markdown_uri": None,
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    }
                ],
                "quantity_subgrid_passes": [{"page_index": 1, "normalized_rows": [["03/21", "朝", "Menu A", "21"]]}],
                "template_resolution": {
                    "requested_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                    "requested_template_ids": [
                        "fax_layout_regular_soft_mixer_forbidden_v1",
                        "fax_layout_floor_2f3f_v1",
                    ],
                    "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                    "matched_template_id": "fax_layout_floor_2f3f_v1",
                    "blocked": False,
                    "blocked_reasons": [],
                },
                "cell_issues": [{"issue_code": "merged_numeric_cell"}],
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet["rows"][0][:4] == ["03/21", "breakfast", "Menu A", "21"]
    assert "sheet_payload_mapping_low_confidence" in (sheet.get("warnings") or [])


def test_build_confirm_materialization_candidate_prefers_latest_draft_rows():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-confirm-candidate-001")

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F", "備考"],
        rows=[["03/21", "朝", "Menu A", "7", "draft"]],
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )
    assert saved is not None

    second_saved, second_error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F", "備考"],
        rows=[["03/21", "朝", "Menu A", "8", "latest"]],
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        row_ids=["draft-row-2"],
        ui_mode="sheet",
    )

    assert second_error is None
    assert second_saved is not None
    assert error is None
    latest_draft = order_service.get_latest_sheet_draft(order["id"])
    assert isinstance(latest_draft, dict)

    candidate = order_service.build_confirm_materialization_candidate(order["id"])

    assert isinstance(candidate, dict)
    assert candidate["source"] == "draft_sheet"
    assert candidate["draft_id"] == latest_draft["id"]
    assert candidate["line_count"] == 1
    assert candidate["error"] is None
    assert candidate["lines"][0]["menu_name"] == "Menu A"
    assert candidate["lines"][0]["quantity_original"] == 8


def test_confirmed_snapshot_includes_materialization_candidate_from_latest_draft():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-confirm-snapshot-001")

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
    latest_draft = order_service.get_latest_sheet_draft(order["id"])
    assert isinstance(latest_draft, dict)

    confirmed = order_service.confirm_order(order["id"])
    assert confirmed is not None

    snapshot = order_service.get_latest_confirmed_snapshot(order["id"])
    assert isinstance(snapshot, dict)
    snapshot_json = snapshot["snapshot_json"]
    materialization_candidate = snapshot_json.get("materialization_candidate")
    assert isinstance(materialization_candidate, dict)
    assert materialization_candidate["source"] == "draft_sheet"
    assert materialization_candidate["draft_id"] == latest_draft["id"]
    assert materialization_candidate["lines"][0]["quantity_original"] == 9
    assert snapshot["draft_id"] == latest_draft["id"]
    refreshed = order_service.get_order_by_id(order["id"])
    assert isinstance(refreshed, dict)
    assert refreshed["lines"][0]["quantity_original"] == 9
    from src.services import output_builder

    output_ctx = output_builder._prepare_output_context(order["id"])
    assert output_ctx["order_lines"][0]["quantity_original"] == 9


def test_confirm_order_fails_safely_when_latest_draft_is_unparseable():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-confirm-snapshot-unparseable")

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食2F", "備考"],
        rows=[["", "", "", "", "broken"]],
        fields=["date_mmdd", "daypart", "menu", "qty.regular_2f", "remarks"],
        row_ids=["draft-row-broken-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    try:
        order_service.confirm_order(order["id"])
        assert False, "confirm should have raised for an unparseable latest draft"
    except order_service.ConfirmMaterializationError as exc:
        assert exc.code in {"draft_rows_unparseable", "draft_lines_empty"}

    refreshed = order_service.get_order_by_id(order["id"])
    assert isinstance(refreshed, dict)
    assert refreshed["status"] == "要確認"
    assert refreshed["lines"][0]["quantity_original"] == 2


def test_confirm_order_fails_when_no_draft_or_evidence_exists():
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-confirm-no-draft")

    try:
        order_service.confirm_order(order["id"])
        assert False, "confirm should require a latest draft or evidence-backed initial draft"
    except order_service.ConfirmMaterializationError as exc:
        assert exc.code in {"draft_missing", "draft_lines_empty"}

    refreshed = order_service.get_order_by_id(order["id"])
    assert isinstance(refreshed, dict)
    assert refreshed["status"] == "要確認"
