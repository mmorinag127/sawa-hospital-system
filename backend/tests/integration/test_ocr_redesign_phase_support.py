import sys
import pathlib
from datetime import date, datetime

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import candidate_resolution_service, config_service, fax_extractor, order_service, template_resolution_service  # noqa: E402
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


def _fac00004_aux_rows() -> list[list[str]]:
    return [
        ["", "", "", "", "", "", "", "", "", "", "", "山田菜", "備考欄"],
        ["", "", "", "献立", "合計", "#☆", "通所", "職員", "平森", "", "", "", ""],
        ["日 付", "区\n分", "", "", "", "", "", "", "肉蒸", "魚禁", "揚げ物", "", ""],
        ["", "", "", "", "70", "", "", "", "", "", "", "", ""],
        ["4/26\n(日)", "朝", "主", "鶏じゃが", "67", "66", "", "", "", "", "", "", ""],
        ["", "夕", "主", "麻婆豆腐", "59", "58", "", "", "", "", "6", "", ""],
    ]


def _fac00004_live_like_block_total_rows() -> list[list[str]]:
    return [
        ["", "", "", "", "", "", "", "", "", "", "", "山田菜", "備考欄"],
        ["", "", "", "献立", "合計", "#☆", "通所", "職員", "平森", "", "", "", ""],
        ["日 付", "区\n分", "", "", "", "", "", "", "肉蒸", "魚禁", "揚げ物", "", ""],
        ["", "", "", "", "70", "", "", "", "", "", "", "", ""],
        ["4/26\n(日)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "67", "", "", "", "", "", "", "", ""],
        ["4/27\n(月)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "朝", "", "", "70", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "105", "", "", "", "", "", "", "", ""],
        ["", "夕", "", "", "72", "", "", "", "", "", "", "", ""],
        ["4/28/\n(火)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "朝", "", "", "70", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "105", "", "", "", "", "", "", "", ""],
        ["", "夕", "", "", "72", "", "", "", "", "", "", "", ""],
        ["4/20\n(水)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "朝", "", "", "70", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "105", "", "", "", "", "", "", "", ""],
        ["", "夕", "", "", "72", "", "", "", "", "", "", "", ""],
        ["4/30\n(木)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "朝", "", "", "70", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "102\n102", "", "", "", "", "", "", "", ""],
    ]


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


def _seed_order_without_lines_for_facility(
    *,
    message_id: str,
    facility_id: str,
    received_at: datetime,
) -> dict:
    payload = IngestEmailPayload(
        message_id=message_id,
        pdf_uri="file://dummy.pdf",
        received_at=received_at,
        facility_hint=facility_id,
        week_hint=None,
    )
    return order_service.create_order_from_ingest(payload, lines=None)


def _resolved_position_fallback_candidate_resolution(
    *,
    order_id: str = "ORD-test",
    facility_id: str,
    week_id: str,
    template_id: str,
    resolved_value: str,
    mapped_fields: list[str],
    page_index: int = 1,
    table_id: str = "page1_table1",
) -> dict:
    return {
        "order_id": order_id,
        "resolutions": {
            "facility": {
                "decision_type": "facility",
                "resolved_value": facility_id,
                "resolved_label": facility_id,
                "confidence": "high",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "candidates": [{"value": facility_id, "label": facility_id, "score": 1.0, "reason": "current_order_value"}],
            },
            "week": {
                "decision_type": "week",
                "resolved_value": week_id,
                "resolved_label": week_id,
                "confidence": "high",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "candidates": [{"value": week_id, "label": week_id, "score": 1.0, "reason": "ocr_dates_cross_month"}],
            },
            "template": {
                "decision_type": "template",
                "resolved_value": template_id,
                "resolved_label": template_id,
                "confidence": "high",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "candidates": [{"value": template_id, "label": template_id, "score": 1.0, "reason": "effective_template_equivalent"}],
            },
            "column_mapping": {
                "decision_type": "column_mapping",
                "resolved_value": resolved_value,
                "resolved_label": resolved_value,
                "confidence": "high",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "candidates": [
                    {
                        "candidate_id": "pcm-test",
                        "candidate_type": "position_fallback_candidate",
                        "value": resolved_value,
                        "label": resolved_value,
                        "score": 0.99,
                        "confidence": "high",
                        "reason": "structured_cell_position_mapping",
                        "decision_source": "position_fallback",
                        "mapped_quantity_fields": mapped_fields,
                        "expected_quantity_fields": mapped_fields,
                        "partial_quantity_mapping": False,
                        "evidence_ref": {
                            "page_index": page_index,
                            "table_id": table_id,
                            "mapped_fields": mapped_fields,
                        },
                    }
                ],
                "attention_required": False,
                "attention_reasons": [],
                "decision_source": "position_fallback",
                "ambiguity_scope": None,
                "partial_quantity_mapping": False,
                "mapped_quantity_fields": mapped_fields,
                "expected_quantity_fields": mapped_fields,
                "evidence_ref": {
                    "page_index": page_index,
                    "table_id": table_id,
                    "mapped_fields": mapped_fields,
                },
            },
            "quantity": {
                "decision_type": "quantity",
                "resolved_value": None,
                "resolved_label": None,
                "confidence": "low",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "candidates": [],
                "attention_required": True,
                "attention_reasons": ["merged_numeric_cell"],
                "failed_cell_count": 0,
                "decision_source": "ocr_evidence",
                "ambiguity_scope": None,
                "evidence_ref": None,
            },
        },
        "requires_user_choice": False,
        "critical_choices": [],
        "attention_required": True,
        "confidence_band": "low",
    }


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
    assert sheet["rows"][0][:4] == ["03/21", "朝", "Menu A", "9"]


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


def test_get_ocr_output_recanonicalizes_stale_aux_position_fallback_for_columns_authoritative_template():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-output-fac00004-aux-fix-001",
        facility_id="FAC00004",
    )
    rows = _fac00004_aux_rows()
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "done",
            "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "rows": rows,
                    "cells": _structured_cells(rows),
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
                "evidence_ref": {
                    "page_index": 1,
                    "table_id": "p1_t1",
                    "source_col_indexes": [4, 5, 6, 7, 8, 9, 10],
                },
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

    payload, payload_error = order_service.get_ocr_output(order["id"])

    assert payload_error is None
    assert isinstance(payload, dict)
    resolution = payload.get("column_mapping_resolution")
    assert isinstance(resolution, dict)
    assert resolution.get("resolved_value") == (
        "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
        "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
    )
    evidence_ref = resolution.get("evidence_ref")
    assert isinstance(evidence_ref, dict)
    assert evidence_ref.get("source_col_indexes") == [5, 6, 7, 8, 9, 10, 11]
    persisted = order_service._load_order_ocr_cache(order["id"])
    assert isinstance(persisted, dict)
    persisted_resolution = persisted.get("column_mapping_resolution")
    assert isinstance(persisted_resolution, dict)
    assert persisted_resolution.get("resolved_value") == (
        "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
        "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
    )


def test_get_ocr_output_blocks_stale_aux_position_fallback_when_recompute_is_impossible():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-output-fac00004-aux-block-001",
        facility_id="FAC00004",
    )
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "status": "done",
            "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            "column_mapping_resolution": {
                "resolved_value": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                "resolved_column_mapping_id": "4:qty.regular_x|5:qty.daycare_x|6:qty.staff_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.no_fried_x|10:qty.change_1_x",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "decision_source": "position_fallback",
                "partial_quantity_mapping": False,
                "confidence": 0.91,
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

    payload, payload_error = order_service.get_ocr_output(order["id"])

    assert payload_error is None
    assert isinstance(payload, dict)
    resolution = payload.get("column_mapping_resolution")
    assert isinstance(resolution, dict)
    assert resolution.get("resolved_value") is None
    assert resolution.get("resolved_column_mapping_id") is None
    assert resolution.get("blocked") is True
    assert resolution.get("blocked_reasons") == ["column_mapping_contract_mismatch"]


def test_get_ocr_output_recanonicalizes_stale_edited_rows_to_authoritative_aux_schema():
    rows = _fac00004_aux_rows()
    template = (config_service.get_facility_config("FAC00004") or {}).get("fax_template") or {}
    payload = {
        "status": "done",
        "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        "tables": [
            {
                "table_id": "p1_t1",
                "page_index": 1,
                "rows": rows,
                "cells": _structured_cells(rows),
                "row_count": len(rows),
                "col_count": len(rows[0]),
            }
        ],
        "_edited_ocr": {
            "raw_output": {
                "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "page_index": 1,
                        "rows": rows,
                        "cells": _structured_cells(rows),
                        "row_count": len(rows),
                        "col_count": len(rows[0]),
                    }
                ],
            },
            "latest": {
                "revision_id": "rev-aux-001",
                "ui_mode": "sheet",
                "fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_x",
                    "qty.daycare_x",
                    "qty.staff_x",
                    "qty.no_meat_x",
                    "qty.no_fish_x",
                    "qty.no_fried_x",
                    "qty.change_1_x",
                    "remarks",
                ],
                "header": ["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
                "rows": [["04/26", "朝", "鶏じゃが", "9", "", "", "", "", "", "", ""]],
                "row_ids": ["ocr-row-1"],
                "edited_at": "2026-04-18T11:11:11Z",
            },
            "revisions": [
                {
                    "revision_id": "rev-aux-001",
                    "ui_mode": "sheet",
                    "fields": [
                        "date_mmdd",
                        "daypart",
                        "menu",
                        "qty.regular_x",
                        "qty.daycare_x",
                        "qty.staff_x",
                        "qty.no_meat_x",
                        "qty.no_fish_x",
                        "qty.no_fried_x",
                        "qty.change_1_x",
                        "remarks",
                    ],
                    "header": ["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
                    "rows": [["04/26", "朝", "鶏じゃが", "9", "", "", "", "", "", "", ""]],
                    "row_ids": ["ocr-row-1"],
                    "edited_at": "2026-04-18T11:11:11Z",
                }
            ],
        },
    }

    payload, changed = order_service._recanonicalize_edited_ocr_payload_for_template(payload, template)

    assert changed is True
    assert isinstance(payload, dict)
    edited_table = order_service._attach_edited_ocr_payload(payload).get("edited_table")
    assert isinstance(edited_table, dict)
    assert edited_table.get("header") == [
        "日付",
        "区分",
        "副区分",
        "メニュー",
        "合計",
        "常食",
        "通所",
        "職員",
        "肉禁",
        "魚禁",
        "揚げ物禁",
        "変更1",
        "備考欄",
    ]
    rows_payload = edited_table.get("rows") or []
    assert rows_payload[0][1:5] == ["朝", "主", "鶏じゃが", "67"]
    latest = ((payload.get("_edited_ocr") or {}).get("latest") or {})
    assert latest.get("fields") == [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "qty.daycare_x",
        "qty.staff_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.no_fried_x",
        "qty.change_1_x",
        "remarks",
    ]


def test_get_ocr_output_blocks_stale_edited_rows_when_recanonicalization_source_is_missing():
    template = (config_service.get_facility_config("FAC00004") or {}).get("fax_template") or {}
    payload = {
        "status": "done",
        "_edited_ocr": {
            "latest": {
                "revision_id": "rev-aux-block-001",
                "ui_mode": "sheet",
                "fields": [
                    "date_mmdd",
                    "daypart",
                    "menu",
                    "qty.regular_x",
                    "qty.daycare_x",
                    "qty.staff_x",
                    "qty.no_meat_x",
                    "qty.no_fish_x",
                    "qty.no_fried_x",
                    "qty.change_1_x",
                    "remarks",
                ],
                "header": ["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
                "rows": [["04/26", "朝", "鶏じゃが", "9", "", "", "", "", "", "", ""]],
                "row_ids": ["ocr-row-1"],
                "edited_at": "2026-04-18T11:11:11Z",
            },
        },
    }

    payload, changed = order_service._recanonicalize_edited_ocr_payload_for_template(payload, template)

    assert changed is True
    assert isinstance(payload, dict)
    assert order_service._attach_edited_ocr_payload(payload).get("edited_table") is None
    edited = payload.get("_edited_ocr") or {}
    assert edited.get("schema_recanonicalization_blocked") == "edited_schema_contract_mismatch"


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


def test_get_ocr_pages_prefers_active_evidence_over_stale_job_output(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-pages-active-over-job-001")
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
    assert sheet["rows"][0][0] == "03/21"
    assert sheet["rows"][0][2] == "Menu A"
    assert sheet["rows"][0][3] == ""
    assert "template_resolution_blocked" in (sheet.get("warnings") or [])
    assert "sheet_payload_mapping_blocked_unresolved_template" in (sheet.get("warnings") or [])


def test_get_ocr_sheet_uses_resolved_alternate_template_for_multi_template_facility():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-multi-template-resolved-alt",
        facility_id="FAC00016",
    )

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
                "table_raw": "|日付|区分|メニュー|軟菜|袋分け|肉禁|魚禁|変更1|変更2|備考欄|\n|---|---|---|---|---|---|---|---|---|---|\n|03/21|朝|Menu A|5|0|1|0|0|0||",
                "table_rows": [["03/21", "朝", "Menu A", "5", "0", "1", "0", "0", "0", ""]],
                "template_resolution": {
                    "requested_template_ids": [
                        "fax_layout_regular_diabetes_v1",
                        "fax_layout_regular_forbidden_v1",
                    ],
                    "resolved_template_id": "fax_layout_regular_forbidden_v1",
                    "blocked": False,
                    "blocked_reasons": [],
                },
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    facility_config = order_service._load_facility_config_with_master_fallback(
        "FAC00016",
        log_context="test_forbidden_template_resolution",
    )
    assert facility_config is not None
    forbidden_template = order_service._materialize_facility_template_for_template_id(
        facility_config,
        "fax_layout_regular_forbidden_v1",
    )
    assert forbidden_template is not None
    expected_fields, _ = order_service._build_sheet_fields_and_indexes(forbidden_template)
    assert sheet["fields"] == expected_fields
    assert "qty.no_meat_x" in sheet["fields"]


def test_current_sheet_context_uses_canonical_equivalent_template_when_multi_template_ids_collapse():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-multi-template-unresolved-block",
        facility_id="FAC00016",
    )

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "糖尿", "備考"],
        rows=[["03/21", "朝", "Menu A", "7", "0", ""]],
        fields=["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.diabetes_x", "remarks"],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )
    assert error is None
    assert saved is not None

    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "|日付|区分|メニュー|軟菜|袋分け|肉禁|魚禁|変更1|変更2|備考欄|\n|---|---|---|---|---|---|---|---|---|---|\n|03/21|朝|Menu A|5|0|1|0|0|0||",
            "table_rows": [["03/21", "朝", "Menu A", "5", "0", "1", "0", "0", "0", ""]],
            "template_resolution": {
                "requested_template_ids": [
                    "fax_layout_regular_diabetes_v1",
                    "fax_layout_regular_forbidden_v1",
                ],
                "resolved_template_id": None,
                "blocked": True,
                "blocked_reasons": ["template_resolution_missing"],
            },
        },
    )

    current = order_service.get_current_sheet_context(order["id"])

    assert isinstance(current, dict)
    facility_config = order_service._load_facility_config_with_master_fallback(
        "FAC00016",
        log_context="test_current_sheet_template_resolution",
    )
    assert facility_config is not None
    forbidden_template = order_service._materialize_facility_template_for_template_id(
        facility_config,
        "fax_layout_regular_diabetes_v1",
    )
    assert forbidden_template is not None
    expected_fields, _ = order_service._build_sheet_fields_and_indexes(forbidden_template)
    assert current["fields"] == expected_fields
    assert "template_unresolved" not in (current.get("warnings") or [])
    assert "template_unresolved" not in (current.get("blockers") or [])


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
    assert str(sheet["source"]).startswith("weekly_menu")
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


def test_get_ocr_sheet_uses_candidate_resolution_to_seed_cross_month_payload_projection(monkeypatch):
    config_service.reload_configs()
    order_service.clear_all()
    order = _seed_order_without_lines_for_facility(
        message_id="msg-ocr-redesign-candidate-resolution-cross-month",
        facility_id="FAC00014",
        received_at=datetime(2026, 4, 26, 9, 0, 0),
    )
    rows = [
        ["4 日", "区 分", "", "献立", "常 食", "職 貝", "禁食", "", "", "変更1", "備考欄"],
        ["", "", "", "", "", "", "肉禁", "魚禁", "ゴマ禁アレルギー", "", ""],
        ["4/26\n(日)", "朝", "", "Menu A", "102", "2", "2", "", "", "", ""],
        ["", "", "", "Menu B", "104", "2", "", "", "", "", ""],
        ["5/1\n(金)", "朝", "", "Menu C", "107", "3", "", "", "", "", ""],
    ]

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    original_get_order_candidate_resolution = order_service.get_order_candidate_resolution
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": "Menu A",
            "menu_date": date(2026, 4, 26),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 0,
        },
        {
            "menu_name": "Menu B",
            "menu_date": date(2026, 4, 26),
            "daypart_key": "breakfast",
            "slot_index": 1,
            "order": 1,
        },
        {
            "menu_name": "Menu C",
            "menu_date": date(2026, 5, 1),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 2,
        },
    ]
    order_service.get_order_candidate_resolution = lambda _order_id: _resolved_position_fallback_candidate_resolution(
        order_id=order["id"],
        facility_id="FAC00014",
        week_id="2026-04@2026-04-26~2026-05-02",
        template_id="fax_layout_regular_staff_daycare_v1",
        resolved_value="4:qty.regular_x|5:qty.staff_x|6:qty.no_meat_x|7:qty.no_fish_x|8:qty.sesame_allergy_x|9:qty.change_1_x",
        mapped_fields=[
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
        ],
    )

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

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"], prefer_order_lines=False)
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe
        order_service.get_order_candidate_resolution = original_get_order_candidate_resolution

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["week_id"] == "2026-04@2026-04-26~2026-05-01"
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet["rows"][0][:10] == ["04/26", "breakfast", "Menu A", "102", "2", "2", "", "", "", ""]
    assert sheet["rows"][1][:10] == ["04/26", "breakfast", "Menu B", "104", "2", "", "", "", "", ""]
    assert sheet["rows"][2][:10] == ["05/01", "breakfast", "Menu C", "107", "3", "", "", "", "", ""]
    assert "sheet_payload_mapping_blocked_unresolved_template" not in (sheet.get("warnings") or [])


def test_get_ocr_sheet_reads_cross_month_rows_from_page_markdown_uris(monkeypatch):
    config_service.reload_configs()
    order_service.clear_all()
    order = _seed_order_without_lines_for_facility(
        message_id="msg-ocr-redesign-page-markdown-cross-month",
        facility_id="FAC00014",
        received_at=datetime(2026, 4, 26, 9, 0, 0),
    )
    markdown_by_uri = {
        "gs://bucket/page-1.md": """
|日付|区分|献立|常食|職員|肉禁|魚禁|ゴマ禁アレルギー|変更1|備考欄|
|-|-|-|-|-|-|-|-|-|-|
|4/26|朝|Menu A|102|2|2|||||
|4/26|朝|Menu B|104|2||||||
""".strip(),
        "gs://bucket/page-2.md": """
|日付|区分|献立|常食|職員|肉禁|魚禁|ゴマ禁アレルギー|変更1|備考欄|
|-|-|-|-|-|-|-|-|-|-|
|5/1|朝|Menu C|107|3||||||
|5/2|朝|Menu D|109|4||||||
""".strip(),
    }

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    original_get_order_candidate_resolution = order_service.get_order_candidate_resolution
    original_load_bytes_from_uri = order_service.load_bytes_from_uri
    original_resolve_sheet_week_id = order_service._resolve_sheet_week_id
    original_build_position_menu_entries_from_ocr_payload = (
        order_service._build_position_menu_entries_from_ocr_payload
    )
    original_resolve_sheet_week_id = order_service._resolve_sheet_week_id
    original_resolve_sheet_week_id = order_service._resolve_sheet_week_id
    original_resolve_sheet_week_id = order_service._resolve_sheet_week_id
    original_resolve_sheet_week_id = order_service._resolve_sheet_week_id
    original_resolve_sheet_week_id = order_service._resolve_sheet_week_id
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": "Menu A",
            "menu_date": date(2026, 4, 26),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 0,
        },
        {
            "menu_name": "Menu B",
            "menu_date": date(2026, 4, 26),
            "daypart_key": "breakfast",
            "slot_index": 1,
            "order": 1,
        },
        {
            "menu_name": "Menu C",
            "menu_date": date(2026, 5, 1),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 2,
        },
        {
            "menu_name": "Menu D",
            "menu_date": date(2026, 5, 2),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 3,
        },
    ]
    order_service.get_order_candidate_resolution = lambda _order_id: _resolved_position_fallback_candidate_resolution(
        order_id=order["id"],
        facility_id="FAC00014",
        week_id="2026-04@2026-04-26~2026-05-02",
        template_id="fax_layout_regular_staff_daycare_v1",
        resolved_value="3:qty.regular_x|4:qty.staff_x|5:qty.no_meat_x|6:qty.no_fish_x|7:qty.sesame_allergy_x|8:qty.change_1_x",
        mapped_fields=[
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
        ],
    )
    order_service.load_bytes_from_uri = lambda uri: markdown_by_uri[str(uri)].encode("utf-8")
    order_service._resolve_sheet_week_id = lambda **_kwargs: "2026-04@2026-04-26~2026-05-02"
    order_service._build_position_menu_entries_from_ocr_payload = lambda **_kwargs: [
        {
            "menu_name": "Menu A",
            "menu_date": date(2026, 4, 26),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 0,
            "source_order": 0,
        },
        {
            "menu_name": "Menu B",
            "menu_date": date(2026, 4, 26),
            "daypart_key": "breakfast",
            "slot_index": 1,
            "order": 1,
            "source_order": 1,
        },
        {
            "menu_name": "Menu C",
            "menu_date": date(2026, 5, 1),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 2,
            "source_order": 2,
        },
        {
            "menu_name": "Menu D",
            "menu_date": date(2026, 5, 2),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 3,
            "source_order": 3,
        },
    ]
    order_service._resolve_sheet_week_id = lambda **_kwargs: "2026-04@2026-04-26~2026-05-02"
    order_service._resolve_sheet_week_id = lambda **_kwargs: "2026-04@2026-04-26~2026-05-02"
    order_service._resolve_sheet_week_id = lambda **_kwargs: "2026-04@2026-04-26~2026-05-02"
    order_service._resolve_sheet_week_id = lambda **_kwargs: "2026-04@2026-04-26~2026-05-02"
    order_service._resolve_sheet_week_id = lambda **_kwargs: "2026-04@2026-04-26~2026-05-02"

    try:
        order_service._save_order_ocr_cache(
            order["id"],
            {
                "pages": [
                    {
                        "page_index": 1,
                        "markdown_uri": "gs://bucket/page-1.md",
                        "ocr_overlay_uri": "gs://bucket/ocr-page-1.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-1.png",
                        "figure_uris": [],
                    },
                    {
                        "page_index": 2,
                        "markdown_uri": "gs://bucket/page-2.md",
                        "ocr_overlay_uri": "gs://bucket/ocr-page-2.png",
                        "layout_overlay_uri": "gs://bucket/layout-page-2.png",
                        "figure_uris": [],
                    },
                ],
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"], prefer_order_lines=False)
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe
        order_service.get_order_candidate_resolution = original_get_order_candidate_resolution
        order_service.load_bytes_from_uri = original_load_bytes_from_uri
        order_service._resolve_sheet_week_id = original_resolve_sheet_week_id
        order_service._build_position_menu_entries_from_ocr_payload = (
            original_build_position_menu_entries_from_ocr_payload
        )
        order_service._resolve_sheet_week_id = original_resolve_sheet_week_id
        order_service._resolve_sheet_week_id = original_resolve_sheet_week_id
        order_service._resolve_sheet_week_id = original_resolve_sheet_week_id
        order_service._resolve_sheet_week_id = original_resolve_sheet_week_id
        order_service._resolve_sheet_week_id = original_resolve_sheet_week_id

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert [row[:3] for row in sheet["rows"]] == [
        ["04/26", "breakfast", "Menu A"],
        ["04/26", "breakfast", "Menu B"],
        ["05/01", "breakfast", "Menu C"],
        ["05/02", "breakfast", "Menu D"],
    ]
    assert sheet["rows"][2][:10] == ["05/01", "breakfast", "Menu C", "107", "3", "", "", "", "", ""]
    assert sheet["rows"][3][:10] == ["05/02", "breakfast", "Menu D", "109", "4", "", "", "", "", ""]


def test_augment_payload_with_candidate_resolution_skips_choice_required_column_mapping():
    payload = {
        "template_id": "fax_layout_regular_staff_daycare_v1",
        "column_mapping_resolution": {
            "blocked": True,
            "blocked_reasons": ["existing_block"],
            "requires_user_choice": True,
        },
    }

    original_get_order_candidate_resolution = order_service.get_order_candidate_resolution
    order_service.get_order_candidate_resolution = lambda _order_id: {
        "order_id": "ORD-test",
        "resolutions": {
            "template": {
                "decision_type": "template",
                "resolved_value": "fax_layout_regular_staff_daycare_v1",
                "confidence": "high",
                "blocked": False,
                "blocked_reasons": [],
                "requires_user_choice": False,
                "candidates": [],
            },
            "column_mapping": {
                "decision_type": "column_mapping",
                "resolved_value": "4:qty.regular_x",
                "confidence": "high",
                "blocked": True,
                "blocked_reasons": ["column_mapping_choice_required"],
                "requires_user_choice": True,
                "candidates": [
                    {
                        "candidate_id": "pcm-a",
                        "value": "4:qty.regular_x",
                        "label": "candidate-a",
                        "score": 0.55,
                        "decision_source": "position_fallback",
                    }
                ],
            },
        },
    }
    try:
        augmented = order_service._augment_payload_with_candidate_resolution("ORD-test", payload)
    finally:
        order_service.get_order_candidate_resolution = original_get_order_candidate_resolution

    assert isinstance(augmented, dict)
    resolution = augmented.get("column_mapping_resolution")
    assert isinstance(resolution, dict)
    assert resolution.get("resolved_value") in (None, "")
    assert resolution.get("blocked") is True
    assert resolution.get("requires_user_choice") is True
    assert augmented.get("column_mapping_candidates") in (None, [])


def test_sheet_payload_mapping_block_reason_allows_resolved_multi_table_position_fallback():
    template = {
        "row_fields": [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ]
    }
    payload = {
        "tables": [
            {"table_id": "p1_t1", "rows": [["4/26", "朝", "Menu A", "102", "2", "2", "", "", "", ""]]},
            {"table_id": "p2_t1", "rows": [["5/01", "朝", "Menu B", "107", "3", "", "", "", "", ""]]},
        ],
        "template_resolution": {
            "resolved_template_id": "fax_layout_regular_staff_daycare_v1",
            "blocked": False,
            "blocked_reasons": [],
        },
        "column_mapping_resolution": {
            "resolved_value": "4:qty.regular_x|5:qty.staff_x|6:qty.no_meat_x|7:qty.no_fish_x|8:qty.sesame_allergy_x|9:qty.change_1_x",
            "decision_source": "position_fallback",
            "blocked": False,
            "blocked_reasons": [],
            "requires_user_choice": False,
            "mapped_quantity_fields": [
                "qty.regular_x",
                "qty.staff_x",
                "qty.no_meat_x",
                "qty.no_fish_x",
                "qty.sesame_allergy_x",
                "qty.change_1_x",
            ],
            "expected_quantity_fields": [
                "qty.regular_x",
                "qty.staff_x",
                "qty.no_meat_x",
                "qty.no_fish_x",
                "qty.sesame_allergy_x",
                "qty.change_1_x",
            ],
            "partial_quantity_mapping": False,
            "evidence_ref": {"page_index": 1, "table_id": "p1_t1"},
        },
    }

    reason = order_service._sheet_payload_mapping_block_reason(
        source="weekly_menu",
        ocr_payload=payload,
        template=template,
        evidence_missing=[],
        template_blockers=[],
    )

    assert reason is None


def test_resolved_multi_table_position_fallback_keeps_sheet_current_and_workflow_in_parity():
    config_service.reload_configs()
    order_service.clear_all()
    order = _seed_order_without_lines_for_facility(
        message_id="msg-ocr-redesign-multi-table-parity-resolved",
        facility_id="FAC00014",
        received_at=datetime(2026, 4, 26, 9, 0, 0),
    )
    rows_page_1 = [
        ["4 日", "区 分", "", "献立", "常 食", "職 貝", "禁食", "", "", "変更1", "備考欄"],
        ["", "", "", "", "", "", "肉禁", "魚禁", "ゴマ禁アレルギー", "", ""],
        ["4/26\n(日)", "朝", "", "Menu A", "102", "2", "2", "", "", "", ""],
        ["", "", "", "Menu B", "104", "2", "", "", "", "", ""],
    ]
    rows_page_2 = [
        ["5 日", "区 分", "", "献立", "常 食", "職 貝", "禁食", "", "", "変更1", "備考欄"],
        ["", "", "", "", "", "", "肉禁", "魚禁", "ゴマ禁アレルギー", "", ""],
        ["5/1\n(金)", "朝", "", "Menu C", "107", "3", "", "", "", "", ""],
    ]

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    original_get_order_candidate_resolution = order_service.get_order_candidate_resolution
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {"menu_name": "Menu A", "menu_date": date(2026, 4, 26), "daypart_key": "breakfast", "slot_index": 0, "order": 0},
        {"menu_name": "Menu B", "menu_date": date(2026, 4, 26), "daypart_key": "breakfast", "slot_index": 1, "order": 1},
        {"menu_name": "Menu C", "menu_date": date(2026, 5, 1), "daypart_key": "breakfast", "slot_index": 0, "order": 2},
    ]
    order_service.get_order_candidate_resolution = lambda _order_id: _resolved_position_fallback_candidate_resolution(
        order_id=order["id"],
        facility_id="FAC00014",
        week_id="2026-04@2026-04-26~2026-05-02",
        template_id="fax_layout_regular_staff_daycare_v1",
        resolved_value="4:qty.regular_x|5:qty.staff_x|6:qty.no_meat_x|7:qty.no_fish_x|8:qty.sesame_allergy_x|9:qty.change_1_x",
        mapped_fields=[
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
        ],
    )
    try:
        order_service._save_order_ocr_cache(
            order["id"],
            {
                "tables": [
                    {"page_index": 1, "table_id": "page1_table1", "row_count": len(rows_page_1), "col_count": len(rows_page_1[0]), "rows": rows_page_1, "cells": _structured_cells(rows_page_1)},
                    {"page_index": 2, "table_id": "page2_table1", "row_count": len(rows_page_2), "col_count": len(rows_page_2[0]), "rows": rows_page_2, "cells": _structured_cells(rows_page_2)},
                ],
            },
        )
        sheet, sheet_error = order_service.get_ocr_sheet(order["id"], prefer_order_lines=False)
        current = order_service.get_current_sheet_context(order["id"])
        workflow = order_service.get_order_workflow_state(order["id"], refresh=True)
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe
        order_service.get_order_candidate_resolution = original_get_order_candidate_resolution

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert isinstance(current, dict)
    assert isinstance(workflow, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet.get("sheet_projection", {}).get("status") == "applied"
    assert sheet["rows"][0][:5] == ["04/26", "breakfast", "Menu A", "102", "2"]
    assert current.get("rows", [])[0][:5] == ["04/26", "朝", "Menu A", "102", "2"]
    assert "template_unresolved" not in (workflow.get("apply_gate", {}).get("blockers") or [])
    assert "column_mapping_choice_required" not in (workflow.get("apply_gate", {}).get("blockers") or [])


def test_choice_required_multi_table_position_fallback_stays_blocked_in_sheet_and_workflow():
    config_service.reload_configs()
    order_service.clear_all()
    order = _seed_order_without_lines_for_facility(
        message_id="msg-ocr-redesign-multi-table-parity-choice-required",
        facility_id="FAC00014",
        received_at=datetime(2026, 4, 26, 9, 0, 0),
    )
    rows_page_1 = [
        ["4 日", "区 分", "", "献立", "常 食", "職 貝", "禁食", "", "", "変更1", "備考欄"],
        ["", "", "", "", "", "", "肉禁", "魚禁", "ゴマ禁アレルギー", "", ""],
        ["4/26\n(日)", "朝", "", "Menu A", "102", "2", "2", "", "", "", ""],
    ]
    rows_page_2 = [
        ["5 日", "区 分", "", "献立", "常 食", "職 貝", "禁食", "", "", "変更1", "備考欄"],
        ["", "", "", "", "", "", "肉禁", "魚禁", "ゴマ禁アレルギー", "", ""],
        ["5/1\n(金)", "朝", "", "Menu C", "107", "3", "", "", "", "", ""],
    ]

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    original_get_order_candidate_resolution = order_service.get_order_candidate_resolution
    original_resolve_order_candidates = candidate_resolution_service.resolve_order_candidates
    original_augment_payload_with_position_fallback = (
        order_service.position_column_mapping_service.augment_payload_with_position_fallback
    )
    mocked_candidate_resolution = {
        "order_id": order["id"],
        "resolutions": {
            "facility": {"decision_type": "facility", "resolved_value": "FAC00014", "blocked": False, "blocked_reasons": [], "requires_user_choice": False},
            "week": {"decision_type": "week", "resolved_value": "2026-04@2026-04-26~2026-05-02", "blocked": False, "blocked_reasons": [], "requires_user_choice": False},
            "template": {"decision_type": "template", "resolved_value": "fax_layout_regular_staff_daycare_v1", "blocked": False, "blocked_reasons": [], "requires_user_choice": False},
            "column_mapping": {
                "decision_type": "column_mapping",
                "resolved_value": None,
                "blocked": True,
                "blocked_reasons": ["column_mapping_choice_required"],
                "requires_user_choice": True,
                "decision_source": "position_fallback",
                "candidates": [
                    {"value": "4:qty.regular_x|5:qty.staff_x", "label": "a", "score": 0.6},
                    {"value": "4:qty.staff_x|5:qty.regular_x", "label": "b", "score": 0.59},
                ],
            },
        },
    }
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {"menu_name": "Menu A", "menu_date": date(2026, 4, 26), "daypart_key": "breakfast", "slot_index": 0, "order": 0},
        {"menu_name": "Menu C", "menu_date": date(2026, 5, 1), "daypart_key": "breakfast", "slot_index": 0, "order": 1},
    ]
    order_service.get_order_candidate_resolution = lambda _order_id: mocked_candidate_resolution
    candidate_resolution_service.resolve_order_candidates = lambda **_kwargs: mocked_candidate_resolution
    order_service.position_column_mapping_service.augment_payload_with_position_fallback = (
        lambda payload, *_args, **_kwargs: payload
    )
    try:
        order_service._save_order_ocr_cache(
            order["id"],
            {
                "tables": [
                    {"page_index": 1, "table_id": "page1_table1", "row_count": len(rows_page_1), "col_count": len(rows_page_1[0]), "rows": rows_page_1, "cells": _structured_cells(rows_page_1)},
                    {"page_index": 2, "table_id": "page2_table1", "row_count": len(rows_page_2), "col_count": len(rows_page_2[0]), "rows": rows_page_2, "cells": _structured_cells(rows_page_2)},
                ],
            },
        )
        sheet, sheet_error = order_service.get_ocr_sheet(order["id"], prefer_order_lines=False)
        current = order_service.get_current_sheet_context(order["id"])
        workflow = order_service.get_order_workflow_state(order["id"], refresh=True)
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe
        order_service.get_order_candidate_resolution = original_get_order_candidate_resolution
        candidate_resolution_service.resolve_order_candidates = original_resolve_order_candidates
        order_service.position_column_mapping_service.augment_payload_with_position_fallback = (
            original_augment_payload_with_position_fallback
        )

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert isinstance(current, dict)
    assert isinstance(workflow, dict)
    assert sheet["source"] == "weekly_menu"
    assert sheet.get("sheet_projection", {}).get("reason_code") == "sheet_payload_mapping_blocked_unresolved_template"
    assert "sheet_payload_mapping_blocked_unresolved_template" in (sheet.get("warnings") or [])
    assert current.get("sheet_projection", {}).get("reason_code") == "sheet_payload_mapping_blocked_unresolved_template"
    assert "column_mapping" in (
        workflow.get("candidate_resolution", {}).get("gate_summary", {}).get("choice_required_types") or []
    )


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


def test_get_ocr_sheet_position_fallback_skips_physical_spacer_after_regular_column():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-position-fallback-regular-spacer",
        facility_id="FAC00007",
    )
    rows = [
        ["4 日", "区 分", "", "献立", "茶食", "", "禁食", "", "変更1", "※用◎", "備考欄"],
        ["", "", "", "", "", "", "肉禁", "魚焼", "", "", ""],
        ["3/22\n(日)", "明", "WO", "Menu A", "", "", "", "", "", "", ""],
        ["", "", "(2)", "Menu B", "", "", "", "", "", "", ""],
        ["", "&", "¥¥", "Menu C", "44", "", "", "", "", "", ""],
        ["", "", "金属", "Menu D", "", "", "", "", "", "", ""],
        ["", "", "福", "Menu E", "", "", "", "", "", "", ""],
        ["", "$", "¥", "Menu F", "44", "", "", "", "", "", ""],
    ]

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": menu_name,
            "menu_date": date(2026, 3, 22),
            "daypart_key": "breakfast",
            "slot_index": idx,
            "order": idx,
        }
        for idx, menu_name in enumerate(["Menu A", "Menu B", "Menu C", "Menu D", "Menu E", "Menu F"])
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
    assert sheet["rows"][2][:9] == ["03/22", "朝", "Menu C", "44", "", "", "", "", ""]
    assert sheet["rows"][5][:9] == ["03/22", "朝", "Menu F", "44", "", "", "", "", ""]
    assert "sheet_payload_mapping_blocked_unresolved_template" not in (sheet.get("warnings") or [])


def test_get_ocr_sheet_projects_partial_position_fallback_quantities_but_keeps_unmapped_warning():
    config_service.reload_configs()
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-position-fallback-partial-coverage",
        facility_id="FAC00005",
    )
    rows = [
        ["日付", "", "区 分", "", "献立", "軟菜", "* # は", "熱食 【 軟菜 】", "", "変更1", "変更2", "備考欄"],
        ["", "", "", "", "", "", "", "茶室", "魚袋", "", "", ""],
        ["", "3/22\n(日)", "体", "学歴\nEND", "Menu A", "57", "2", "", "", "", "", ""],
        ["", "", "", "▼¥", "Menu B", "58", "4", "", "", "", "", ""],
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
    assert "sheet_payload_mapping_blocked_unresolved_template" not in (sheet.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" in (sheet.get("warnings") or [])
    assert sheet["can_apply"] is False
    assert "sheet_quantity_column_unmapped" in (sheet.get("apply_blockers") or [])
    assert sheet["current_sheet_revision_id"] == order_service._current_sheet_revision_id(order_id=order["id"])
    assert sheet["rows"][0][:9] == ["03/22", "朝", "Menu A", "57", "2", "", "", "", ""]
    assert sheet["rows"][1][:9] == ["03/22", "昼", "Menu B", "58", "4", "", "", "", ""]


def test_get_ocr_sheet_projects_payload_quantities_for_strict_evidence_context_with_ready_position_fallback():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-position-fallback-strict-evidence-context",
        facility_id="FAC00002",
    )
    rows = [
        ["日 付", "区 分", "", "献立", "常食", "", "事故", "", "変更の", "変更の", "備考欄"],
        ["", "", "", "", "", "", "肉款", "魚炊", "", "", ""],
        ["3/22\n(日)", "IN", "HKD", "Menu A", "", "", "", "", "", "", ""],
        ["", "\"", "VF", "Menu B", "23", "", "", "", "", "", ""],
        ["", "", "48", "Menu C", "27", "", "", "", "", "", ""],
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
        {
            "menu_name": "Menu C",
            "menu_date": date(2026, 3, 22),
            "daypart_key": "lunch",
            "slot_index": 1,
            "order": 2,
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
                "page_correction": {
                    "applied": True,
                    "document_rotation_deg": 90,
                },
                "page_correction_artifacts": {
                    "template_warp_page_indexes": [1],
                    "position_normalized": True,
                },
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet["current_sheet_revision_id"] == order_service._current_sheet_revision_id(order_id=order["id"])
    assert sheet["rows"][0][:9] == ["03/22", "朝", "Menu A", "", "", "", "", "", ""]
    assert sheet["rows"][1][:9] == ["03/22", "昼", "Menu B", "23", "", "", "", "", ""]
    assert sheet["rows"][2][:9] == ["03/22", "昼", "Menu C", "27", "", "", "", "", ""]
    assert "sheet_payload_mapping_blocked_unresolved_template" not in (sheet.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])


def test_get_ocr_sheet_preserves_aux_total_column_for_columns_authoritative_template():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-aux-total-column",
        facility_id="FAC00004",
    )
    rows = [
        ["日付", "区分", "副区分", "メニュー", "合計", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
        ["4/26", "昼", "主", "Menu A", "12", "10", "1", "1", "", "", "", "", ""],
        ["", "昼", "副", "Menu B", "9", "8", "", "1", "", "", "", "", ""],
    ]

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": "Menu A",
            "menu_date": date(2026, 4, 26),
            "daypart_key": "lunch",
            "slot_index": 0,
            "order": 0,
        },
        {
            "menu_name": "Menu B",
            "menu_date": date(2026, 4, 26),
            "daypart_key": "lunch",
            "slot_index": 1,
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
    assert sheet["fields"] == [
        "date_mmdd",
        "daypart",
        "aux.col_2",
        "menu",
        "aux.col_4",
        "qty.regular_x",
        "qty.daycare_x",
        "qty.staff_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.no_fried_x",
        "qty.change_1_x",
        "remarks",
    ]
    assert sheet["header"] == [
        "日付",
        "区分",
        "副区分",
        "メニュー",
        "合計",
        "常食",
        "通所",
        "職員",
        "肉禁",
        "魚禁",
        "揚げ物禁",
        "変更1",
        "備考欄",
    ]
    assert sheet["rows"][0][0] == "04/26"
    assert sheet["rows"][0][2:8] == ["主", "Menu A", "12", "10", "1", "1"]
    assert sheet["rows"][1][0] == "04/26"
    assert sheet["rows"][1][2:8] == ["副", "Menu B", "9", "8", "", "1"]


def test_extract_sheet_rows_from_payload_preserves_authoritative_aux_columns_for_noisy_fac00004_table():
    template = (config_service.get_facility_config("FAC00004") or {}).get("fax_template") or {}
    rows = _fac00004_aux_rows()
    payload = {
        "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
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
        "column_mapping_resolution": {
            "resolved_value": (
                "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
                "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
            ),
            "resolved_column_mapping_id": (
                "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
                "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
            ),
            "blocked": False,
            "blocked_reasons": [],
            "requires_user_choice": False,
            "decision_source": "position_fallback",
            "partial_quantity_mapping": False,
            "confidence": 0.99,
        },
    }

    extracted = order_service._extract_sheet_rows_from_payload(payload, template)

    assert extracted[:2] == [
        ["4/26 (日)", "朝", "主", "鶏じゃが", "70", "66", "", "", "", "", "", "", ""],
        ["", "夕", "主", "麻婆豆腐", "67", "58", "", "", "", "", "6", "", ""],
    ]


def test_extract_sheet_rows_from_resolved_mapping_overlays_explicit_aux_columns_when_mapping_meta_omits_them(monkeypatch):
    template = (config_service.get_facility_config("FAC00004") or {}).get("fax_template") or {}
    rows = _fac00004_aux_rows()
    payload = {
        "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
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
        "column_mapping_resolution": {
            "resolved_value": (
                "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
                "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
            ),
            "resolved_column_mapping_id": (
                "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.no_meat_x|"
                "9:qty.no_fish_x|10:qty.no_fried_x|11:qty.change_1_x"
            ),
            "blocked": False,
            "blocked_reasons": [],
            "requires_user_choice": False,
            "decision_source": "position_fallback",
            "partial_quantity_mapping": False,
            "confidence": 0.99,
        },
    }

    original_resolve_structured_table_mapping = fax_extractor._resolve_structured_table_mapping

    def _fake_resolve_structured_table_mapping(_matrix, _template):
        return (
            {
                "fields": order_service._get_row_fields(template),
                "mapped_indexes": {
                    0: 0,
                    1: 1,
                    3: 3,
                    5: 5,
                    6: 6,
                    7: 7,
                    8: 8,
                    9: 9,
                    10: 10,
                    11: 11,
                },
                "row_map": {4: 0, 5: 1},
            },
            [],
        )

    monkeypatch.setattr(
        fax_extractor,
        "_resolve_structured_table_mapping",
        _fake_resolve_structured_table_mapping,
    )

    try:
        extracted = order_service._extract_sheet_rows_from_resolved_column_mapping(payload, template)
    finally:
        monkeypatch.setattr(
            fax_extractor,
            "_resolve_structured_table_mapping",
            original_resolve_structured_table_mapping,
        )

    assert extracted[0][:7] == ["4/26 (日)", "朝", "主", "鶏じゃが", "67", "66", ""]
    assert extracted[1][:7] == ["", "夕", "主", "麻婆豆腐", "59", "58", ""]


def test_overlay_structured_block_total_cells_projects_live_like_sequences_by_canonical_date_order():
    template = (config_service.get_facility_config("FAC00004") or {}).get("fax_template") or {}
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    rows = [
        ["04/26", "朝", "", "大豆のトマト煮", "", "70", "", "", "", "", "", "", ""],
        ["04/26", "朝", "", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "", "67", "", "", "", "", "", "", ""],
        ["04/26", "昼", "", "サワラの揚げ浸し", "", "67", "", "", "", "", "", "", ""],
        ["04/26", "夕", "", "豚肉のピリ辛炒め", "", "67", "", "", "", "", "", "", ""],
        ["04/27", "朝", "", "じゃが芋の煮物", "", "70", "", "", "", "", "", "", ""],
        ["04/27", "昼", "", "オムレツミートソース", "", "67", "", "", "", "", "", "", ""],
        ["04/27", "夕", "", "鶏肉の和風あんかけ", "", "67", "", "", "", "", "", "", ""],
        ["04/28", "朝", "", "野菜の卵とじ", "", "70", "", "", "", "", "", "", ""],
        ["04/28", "昼", "", "豆腐ﾊﾝﾊﾞｰｸﾞ", "", "67", "", "", "", "", "", "", ""],
        ["04/28", "夕", "", "豚肉とじゃが芋の醤油炒め", "", "67", "", "", "", "", "", "", ""],
        ["04/29", "朝", "", "厚揚げと里芋の煮物", "", "70", "", "", "", "", "", "", ""],
        ["04/29", "昼", "", "タラの野菜あんかけ", "", "58", "34", "", "", "", "", "", ""],
        ["04/29", "夕", "", "鶏肉と根菜の煮込み", "", "65", "", "", "2", "", "", "", ""],
        ["04/30", "朝", "", "ジャーマンポテト", "", "70", "", "", "", "", "", "", ""],
        ["04/30", "昼", "", "豚肉と大根の生姜煮", "", "67", "34", "", "", "", "", "", ""],
        ["04/30", "夕", "", "メンチカツ", "", "61", "", "5", "", "", "6", "", ""],
    ]
    payload = {
        "table_raw": "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|\n|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": len(_fac00004_live_like_block_total_rows()),
                "col_count": len(_fac00004_live_like_block_total_rows()[0]),
                "rows": _fac00004_live_like_block_total_rows(),
                "cells": _structured_cells(_fac00004_live_like_block_total_rows()),
            }
        ],
    }

    projected, stats = order_service._overlay_structured_block_total_cells_onto_sheet_row_lists(
        rows=rows,
        fields=fields,
        header=header,
        raw_payload=payload,
        template=template,
    )

    assert projected[0][4] == "70"
    assert projected[1][4] == "70"
    assert projected[2][4] == "67"
    assert projected[3][4] == "67"
    assert projected[4][4] == "70"

    assert projected[5][4] == "105"
    assert projected[6][4] == "72"
    assert projected[10][4] == "70"
    assert projected[11][4] == "105"
    assert projected[12][4] == "72"
    assert projected[13][4] == "70"
    assert projected[14][4] == "102"
    assert projected[15][4] == "66"
    assert int(stats.get("structured_block_total_assignment_count") or 0) == 14
    assert int(stats.get("derived_block_total_assignment_count") or 0) == 2
    assignments = stats.get("structured_block_total_assignments") or []
    assert any(item.get("raw_date") == "04/20" and item.get("canonical_date") == "04/29" for item in assignments)


def test_extract_sheet_rows_from_resolved_column_mapping_uses_template_columns_for_aux_markdown_tables():
    template = (config_service.get_facility_config("FAC00004") or {}).get("fax_template") or {}
    payload = {
        "table_raw": "\n".join(
            [
                "|日付|区分|副区分|献立|合計|#☆|通所|職員|平森|肉蒸|魚禁|揚げ物|備考|",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
                "|04/26|朝|主|大豆のトマト煮|70|70|70|||||||",
                "|04/26|朝|副①|胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ|70|67|3||||6||",
            ]
        ),
        "column_mapping_resolution": {
            "resolved_value": (
                "5:qty.regular_x|6:qty.daycare_x|7:qty.staff_x|8:qty.hiramori_x|"
                "9:qty.no_meat_x|10:qty.no_fish_x|11:qty.no_fried_x"
            ),
            "decision_source": "resolved_column_mapping",
        },
    }

    extracted = order_service._extract_sheet_rows_from_resolved_column_mapping(payload, template)

    assert extracted[0][:7] == ["04/26", "朝", "主", "大豆のトマト煮", "70", "70", "70"]
    assert extracted[1][:7] == ["04/26", "朝", "副①", "胡瓜のﾌﾚﾝﾁｻﾗﾀﾞ", "70", "67", "3"]


def test_project_payload_quantities_onto_weekly_menu_shell_maps_exact_date_group_with_menu_anchor():
    template = (config_service.get_facility_config("FAC00001") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)

    def _shell_row(*, row_id: str, menu_date: date, mmdd: str, daypart: str, menu_name: str) -> dict[str, object]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = mmdd
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu_name
        return {
            "row_id": row_id,
            "values": values,
            "identity": order_service._sheet_row_identity(menu_date, daypart, menu_name),
        }

    def _payload_row(*, date_text: str, daypart: str, menu_name: str, regular: str) -> list[str]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = date_text
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu_name
        values[field_index["qty.regular_2f"]] = regular
        return values

    rows = [
        _shell_row(row_id="r1", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="朝", menu_name="Menu A"),
        _shell_row(row_id="r2", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="朝", menu_name="Menu B"),
        _shell_row(row_id="r3", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="昼", menu_name="Menu C"),
        _shell_row(row_id="r4", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="昼", menu_name="Menu D"),
        _shell_row(row_id="r5", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="昼", menu_name="Menu E"),
    ]
    payload_rows = [
        _payload_row(date_text="04/29", daypart="朝", menu_name="", regular="70"),
        _payload_row(date_text="", daypart="朝", menu_name="Menu B", regular="70"),
        _payload_row(date_text="", daypart="昼", menu_name="Menu C", regular="58"),
        _payload_row(date_text="", daypart="昼", menu_name="Menu D", regular="59"),
        _payload_row(date_text="", daypart="昼", menu_name="Menu E", regular="60"),
    ]

    projected, stats = order_service._project_payload_quantities_onto_weekly_menu_shell(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        template=template,
    )

    qty_idx = field_index["qty.regular_2f"]
    assert [row["values"][qty_idx] for row in projected] == ["70", "70", "58", "59", "60"]
    assert stats["mapped_date_group_count"] == 1
    assert stats["exact_row_count"] == 4
    assert stats["ordered_row_count"] == 1
    assert stats["unresolved_date_group_count"] == 0
    assert stats["overflow_row_count"] == 0


def test_project_payload_quantities_onto_weekly_menu_shell_leaves_unresolved_date_group_blank():
    template = (config_service.get_facility_config("FAC00001") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)

    def _shell_row(*, row_id: str, menu_date: date, mmdd: str, daypart: str, menu_name: str) -> dict[str, object]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = mmdd
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu_name
        return {
            "row_id": row_id,
            "values": values,
            "identity": order_service._sheet_row_identity(menu_date, daypart, menu_name),
        }

    def _payload_row(*, date_text: str, daypart: str, menu_name: str, regular: str) -> list[str]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = date_text
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu_name
        values[field_index["qty.regular_2f"]] = regular
        return values

    rows = [
        _shell_row(row_id="r1", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="朝", menu_name="Menu A"),
        _shell_row(row_id="r2", menu_date=date(2026, 4, 29), mmdd="04/29", daypart="朝", menu_name="Menu B"),
    ]
    payload_rows = [
        _payload_row(date_text="04/20", daypart="朝", menu_name="Unknown A", regular="91"),
        _payload_row(date_text="", daypart="朝", menu_name="Unknown B", regular="92"),
    ]

    projected, stats = order_service._project_payload_quantities_onto_weekly_menu_shell(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        template=template,
    )

    qty_idx = field_index["qty.regular_2f"]
    assert [row["values"][qty_idx] for row in projected] == ["", ""]
    assert stats["mapped_date_group_count"] == 0
    assert stats["unresolved_date_group_count"] == 1
    assert int(stats.get("overflow_row_count") or 0) == 0
    assert stats["unresolved_payload_dates"] == ["04/20"]


def test_extract_first_pass_rows_from_payload_builds_fragmented_quantity_rows_from_header_columns():
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)
    payload = {
        "column_mapping_resolution": {
            "resolved_value": "4:qty.regular_bag_x|5:qty.no_meat_x|6:qty.soft_x|7:qty.no_fish_x|8:qty.change_1_x",
            "decision_source": "position_fallback",
        },
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": 8,
                "col_count": 11,
                "rows": [
                    ["月 付", "", "区 分", "献立", "秋葉", "優 #±", "", "", "", "", ""],
                    ["", "", "", "", "", "", "奈食 【 軟菜 】", "", "変更の", "空辺2", "備考欄"],
                    ["", "", "", "", "", "", "肉薬", "魚禁", "", "", ""],
                    ["4/26\n(日)", "", "", "", "0", "0", "", "", "", "", ""],
                    ["", "", "ない", "胡瓜のフレンチサラダ", "0", "0", "", "", "", "", ""],
                    ["", "", "V7", "", "58", "2", "", "", "", "", ""],
                    ["", "", "<</d", "里芋の味噌かけ", "58", "2", "", "", "", "", ""],
                    ["", "", "", "", "58", "2", "", "", "", "", ""],
                ],
                "cells": _structured_cells(
                    [
                        ["月 付", "", "区 分", "献立", "秋葉", "優 #±", "", "", "", "", ""],
                        ["", "", "", "", "", "", "奈食 【 軟菜 】", "", "変更の", "空辺2", "備考欄"],
                        ["", "", "", "", "", "", "肉薬", "魚禁", "", "", ""],
                        ["4/26\n(日)", "", "", "", "0", "0", "", "", "", "", ""],
                        ["", "", "ない", "胡瓜のフレンチサラダ", "0", "0", "", "", "", "", ""],
                        ["", "", "V7", "", "58", "2", "", "", "", "", ""],
                        ["", "", "<</d", "里芋の味噌かけ", "58", "2", "", "", "", "", ""],
                        ["", "", "", "", "58", "2", "", "", "", "", ""],
                    ]
                ),
            }
        ],
    }

    rows = order_service._extract_first_pass_rows_from_payload(payload, template)

    menu_idx = field_index["menu"]
    bag_idx = field_index["qty.regular_bag_x"]
    meat_idx = field_index["qty.no_meat_x"]
    assert rows[0][field_index["date_mmdd"]] == "04/26"
    assert rows[0][menu_idx] == "胡瓜のフレンチサラダ"
    assert rows[0][bag_idx] == "0"
    assert rows[1][menu_idx] == ""
    assert rows[1][bag_idx] == "58"
    assert rows[1][meat_idx] == "2"
    assert rows[2][menu_idx] == "里芋の味噌かけ"
    assert rows[3][menu_idx] == ""


def test_extract_first_pass_rows_from_payload_prefers_structured_identity_over_fragmented_rows():
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)
    payload = {
        "column_mapping_resolution": {
            "resolved_value": "5:qty.soft_x|6:qty.regular_bag_x|7:qty.no_meat_x|8:qty.no_fish_x|9:qty.change_1_x",
            "decision_source": "position_fallback",
        },
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": 4,
                "col_count": 12,
                "rows": [
                    ["日付", "", "区 分", "", "献立", "軟菜", "* # は", "熱食 【 軟菜 】", "", "変更1", "変更2", "備考欄"],
                    ["", "", "", "", "", "", "", "茶室", "魚袋", "", "", ""],
                    ["", "3/22\n(日)", "体", "学歴\nEND", "Menu A", "57", "2", "", "", "", "", ""],
                    ["", "", "", "▼¥", "Menu B", "58", "4", "", "", "", "", ""],
                ],
                "cells": _structured_cells(
                    [
                        ["日付", "", "区 分", "", "献立", "軟菜", "* # は", "熱食 【 軟菜 】", "", "変更1", "変更2", "備考欄"],
                        ["", "", "", "", "", "", "", "茶室", "魚袋", "", "", ""],
                        ["", "3/22\n(日)", "体", "学歴\nEND", "Menu A", "57", "2", "", "", "", "", ""],
                        ["", "", "", "▼¥", "Menu B", "58", "4", "", "", "", "", ""],
                    ]
                ),
            }
        ],
    }

    rows = order_service._extract_first_pass_rows_from_payload(payload, template)

    assert rows[0][field_index["date_mmdd"]] == "03/22"
    assert rows[0][field_index["daypart"]] == "朝"
    assert rows[0][field_index["menu"]] == "Menu A"
    assert rows[0][field_index["qty.soft_x"]] == "57"
    assert rows[0][field_index["qty.regular_bag_x"]] == "2"
    assert rows[1][field_index["daypart"]] == "昼"
    assert rows[1][field_index["menu"]] == "Menu B"
    assert rows[1][field_index["qty.soft_x"]] == "58"
    assert rows[1][field_index["qty.regular_bag_x"]] == "4"


def test_extract_sheet_rows_from_payload_does_not_use_position_fallback_pseudo_menu_tokens():
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)
    payload = {
        "column_mapping_resolution": {
            "resolved_value": "4:qty.regular_bag_x|5:qty.no_meat_x|6:qty.soft_x|7:qty.no_fish_x|8:qty.change_1_x",
            "decision_source": "position_fallback",
        },
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": 6,
                "col_count": 11,
                "rows": [
                    ["月 付", "", "区 分", "献立", "秋葉", "優 #±", "", "", "", "", ""],
                    ["", "", "", "", "", "", "奈食 【 軟菜 】", "", "変更の", "空辺2", "備考欄"],
                    ["", "", "", "", "", "", "肉薬", "魚禁", "", "", ""],
                    ["4/29\n(水)", "", "", "", "0", "0", "", "", "", "", ""],
                    ["", "", "41 4", "鯖の塩焼き", "58", "2", "", "", "", "", ""],
                    ["", "", "MD", "", "89", "2", "", "", "", "", ""],
                ],
            }
        ],
    }

    rows = order_service._extract_sheet_rows_from_payload(payload, template)

    menu_idx = field_index["menu"]
    bag_idx = field_index["qty.regular_bag_x"]
    assert rows[0][menu_idx] == "鯖の塩焼き"
    assert rows[0][bag_idx] == "58"
    assert rows[1][menu_idx] == ""
    assert rows[1][bag_idx] == "89"
    assert all(row[menu_idx] not in {"41 4", "MD"} for row in rows)


def test_project_payload_quantities_onto_weekly_menu_shell_fills_global_anchor_gaps_without_daypart_tokens():
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)

    def _shell_row(*, row_id: str, menu_date: date, mmdd: str, daypart: str, menu_name: str) -> dict[str, object]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = mmdd
        values[field_index["daypart"]] = daypart
        values[field_index["menu"]] = menu_name
        return {
            "row_id": row_id,
            "values": values,
            "identity": order_service._sheet_row_identity(menu_date, daypart, menu_name),
        }

    def _payload_row(*, date_text: str, menu_name: str, bag: str, meat: str) -> list[str]:
        values = [""] * len(fields)
        values[field_index["date_mmdd"]] = date_text
        values[field_index["menu"]] = menu_name
        values[field_index["qty.regular_bag_x"]] = bag
        values[field_index["qty.no_meat_x"]] = meat
        return values

    rows = [
        _shell_row(row_id="r1", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="朝", menu_name="大豆のトマト煮"),
        _shell_row(row_id="r2", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="朝", menu_name="胡瓜のフレンチサラダ"),
        _shell_row(row_id="r3", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="昼", menu_name="サワラの揚げ浸し"),
        _shell_row(row_id="r4", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="昼", menu_name="里芋の味噌かけ"),
        _shell_row(row_id="r5", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="昼", menu_name="オクラのおかか和え"),
        _shell_row(row_id="r6", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="夕", menu_name="豚肉のピリ辛炒め"),
        _shell_row(row_id="r7", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="夕", menu_name="玉子焼き"),
        _shell_row(row_id="r8", menu_date=date(2026, 4, 26), mmdd="04/26", daypart="夕", menu_name="キャベツと竹輪の酢の物"),
    ]
    payload_rows = [
        _payload_row(date_text="04/26", menu_name="胡瓜のフレンチサラダ", bag="0", meat="0"),
        _payload_row(date_text="", menu_name="", bag="58", meat="2"),
        _payload_row(date_text="", menu_name="里芋の味噌かけ", bag="58", meat="2"),
        _payload_row(date_text="", menu_name="", bag="58", meat="2"),
        _payload_row(date_text="", menu_name="豚肉のピリ辛炒め", bag="0", meat="0"),
        _payload_row(date_text="", menu_name="玉子焼き", bag="0", meat="0"),
        _payload_row(date_text="", menu_name="キャベツと竹輪の酢の物", bag="0", meat="0"),
    ]

    projected, stats = order_service._project_payload_quantities_onto_weekly_menu_shell(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        template=template,
    )

    bag_idx = field_index["qty.regular_bag_x"]
    meat_idx = field_index["qty.no_meat_x"]
    assert [row["values"][bag_idx] for row in projected] == ["58", "0", "58", "58", "", "0", "0", "0"]
    assert [row["values"][meat_idx] for row in projected] == ["2", "0", "2", "2", "", "0", "0", "0"]
    assert stats["exact_row_count"] == 5
    assert stats["ordered_row_count"] == 2
    assert stats["overflow_row_count"] == 0


def test_project_payload_quantities_onto_weekly_menu_shell_blocks_blank_rows_without_any_exact_anchor():
    template = (config_service.get_facility_config("FAC00005") or {}).get("fax_template") or {}
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)

    rows = [
        {
            "row_id": "r1",
            "values": ["04/26", "朝", "Menu A", "", "", "", "", "", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 4, 26), "朝", "Menu A"),
        },
        {
            "row_id": "r2",
            "values": ["04/26", "朝", "Menu B", "", "", "", "", "", "", ""],
            "identity": order_service._sheet_row_identity(date(2026, 4, 26), "朝", "Menu B"),
        },
    ]
    payload_rows = [
        ["04/26", "", "", "", "58", "2", "", "", "", ""],
        ["", "", "", "", "59", "3", "", "", "", ""],
    ]

    projected, stats = order_service._project_payload_quantities_onto_weekly_menu_shell(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        template=template,
    )

    bag_idx = field_index["qty.regular_bag_x"]
    assert [row["values"][bag_idx] for row in projected] == ["", ""]
    assert stats["mapped_date_group_count"] == 0
    assert stats["unresolved_date_group_count"] == 1
    assert stats["unresolved_payload_dates"] == ["04/26"]


def test_normalize_menu_text_collapses_halfwidth_katakana_and_spacing():
    assert order_service._normalize_menu_text("豆腐ﾊﾝﾊﾞｰｸﾞ　添)おろしｿｰｽ") == order_service._normalize_menu_text(
        "豆腐ハンバーグ 添)おろしソース"
    )


def test_overlay_structured_block_total_cells_skips_extra_values_beyond_canonical_block_count():
    template = (config_service.get_facility_config("FAC00004") or {}).get("fax_template") or {}
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    rows = [
        ["04/26", "朝", "", "Menu A", "", "", "", "", "", "", "", "", ""],
        ["04/26", "昼", "", "Menu B", "", "", "", "", "", "", "", "", ""],
        ["04/26", "夕", "", "Menu C", "", "", "", "", "", "", "", "", ""],
    ]
    raw_rows = [
        ["", "", "", "", "70", "", "", "", "", "", "", "", ""],
        ["4/26\n(日)", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "朝", "", "", "70", "", "", "", "", "", "", "", ""],
        ["", "昼", "", "", "80", "", "", "", "", "", "", "", ""],
        ["", "夕", "", "", "90", "", "", "", "", "", "", "", ""],
        ["", "夜", "", "", "100", "", "", "", "", "", "", "", ""],
    ]
    payload = {
        "tables": [
            {
                "page_index": 1,
                "table_id": "page1_table1",
                "row_count": len(raw_rows),
                "col_count": len(raw_rows[0]),
                "rows": raw_rows,
                "cells": _structured_cells(raw_rows),
            }
        ]
    }

    projected, stats = order_service._overlay_structured_block_total_cells_onto_sheet_row_lists(
        rows=rows,
        fields=fields,
        header=header,
        raw_payload=payload,
        template=template,
    )

    assert [row[4] for row in projected] == ["70", "80", "90"]
    skipped = stats.get("structured_block_total_skipped") or []
    assert skipped
    assert skipped[0]["values"] == ["70", "80", "90", "100"]


def test_overlay_structured_block_total_cells_derives_missing_totals_when_structured_segments_absent():
    template = (config_service.get_facility_config("FAC00004") or {}).get("fax_template") or {}
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    rows = [
        ["04/26", "夕", "", "豚肉のピリ辛炒め", "", "67", "", "5", "", "", "", "", ""],
        ["", "", "", "春雨サラダ", "", "67", "", "5", "", "", "", "", ""],
        ["", "", "", "味噌汁", "", "67", "", "5", "", "", "", "", ""],
        ["04/30", "夕", "", "メンチカツ", "", "61", "", "5", "", "", "6", "", ""],
        ["", "", "", "蓮根と鶏肉の炒め煮", "", "65", "", "5", "2", "", "", "", ""],
        ["", "", "", "ほうれん草のお浸し", "", "67", "", "5", "", "", "", "", ""],
    ]

    projected, stats = order_service._overlay_structured_block_total_cells_onto_sheet_row_lists(
        rows=rows,
        fields=fields,
        header=header,
        raw_payload={"tables": []},
        template=template,
    )

    assert [projected[idx][4] for idx in range(3)] == ["72", "72", "72"]
    assert [projected[idx][4] for idx in range(3, 6)] == ["72", "72", "72"]
    assert int(stats.get("structured_block_total_assignment_count") or 0) == 0
    assert int(stats.get("derived_block_total_assignment_count") or 0) == 6


def test_overlay_structured_block_total_cells_keeps_blank_when_no_explicit_or_derivable_total_exists():
    template = (config_service.get_facility_config("FAC00004") or {}).get("fax_template") or {}
    fields = order_service._get_row_fields(template)
    header = order_service._sheet_header_from_template(fields, template)
    rows = [
        ["04/26", "夕", "", "豚肉のピリ辛炒め", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "春雨サラダ", "", "", "", "", "", "", "", "", ""],
    ]

    projected, stats = order_service._overlay_structured_block_total_cells_onto_sheet_row_lists(
        rows=rows,
        fields=fields,
        header=header,
        raw_payload={"tables": []},
        template=template,
    )

    assert [row[4] for row in projected] == ["", ""]
    assert int(stats.get("structured_block_total_assignment_count") or 0) == 0
    assert int(stats.get("derived_block_total_assignment_count") or 0) == 0


def test_get_ocr_sheet_prefers_menu_priority_projection_for_weekly_menu_rows():
    order_service.clear_all()
    payload = IngestEmailPayload(
        message_id="msg-ocr-redesign-position-fallback-menu-priority",
        pdf_uri="file://dummy.pdf",
        received_at=datetime(2026, 3, 21, 9, 0, 0),
        facility_hint="FAC00002",
        week_hint=None,
    )
    order = order_service.create_order_from_ingest(payload, lines=None)
    rows = [
        ["日 付", "区 分", "", "献立", "常食", "", "事故", "", "変更の", "変更の", "備考欄"],
        ["", "", "", "", "", "", "肉款", "魚炊", "", "", ""],
        ["3/22", "ロ", "VF", "Menu A", "23", "", "", "", "", "", ""],
        ["", "", "48", "Menu B", "27", "", "", "", "", "", ""],
        ["", "", "PX", "Menu C", "23", "", "", "", "", "", ""],
    ]

    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": "Breakfast 1",
            "menu_date": date(2026, 3, 22),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 0,
        },
        {
            "menu_name": "Breakfast 2",
            "menu_date": date(2026, 3, 22),
            "daypart_key": "breakfast",
            "slot_index": 1,
            "order": 1,
        },
        {
            "menu_name": "Menu A",
            "menu_date": date(2026, 3, 22),
            "daypart_key": "lunch",
            "slot_index": 0,
            "order": 2,
        },
        {
            "menu_name": "Menu B",
            "menu_date": date(2026, 3, 22),
            "daypart_key": "lunch",
            "slot_index": 1,
            "order": 3,
        },
        {
            "menu_name": "Menu C",
            "menu_date": date(2026, 3, 22),
            "daypart_key": "lunch",
            "slot_index": 2,
            "order": 4,
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
    assert sheet["rows"][0][:4] == ["03/22", "breakfast", "Breakfast 1", ""]
    assert sheet["rows"][1][:4] == ["03/22", "breakfast", "Breakfast 2", ""]
    assert sheet["rows"][2][:4] == ["03/22", "lunch", "Menu A", "23"]
    assert sheet["rows"][3][:4] == ["03/22", "lunch", "Menu B", "27"]
    assert sheet["rows"][4][:4] == ["03/22", "lunch", "Menu C", "23"]
    assert (sheet.get("trace") or {}).get("mapped_mode") == "payload_row"
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
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert "template_unresolved" not in (sheet.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])
    assert "template_unresolved" not in (sheet.get("apply_blockers") or [])
    assert "sheet_quantity_column_unmapped" not in (sheet.get("apply_blockers") or [])
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


def test_get_ocr_sheet_uses_canonical_equivalent_template_when_raw_header_is_partial():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-multi-template-forbidden-header",
        facility_id="FAC00016",
    )

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
                        "|日付|区分|メニュー|常食|肉禁|魚禁|備考|",
                        "|---|---|---|---|---|---|---|",
                        "|03/21|朝|Menu A|9|4|2||",
                    ]
                ),
                "template_resolution": {
                    "resolved_template_id": "",
                    "candidate_template_ids": [
                        "fax_layout_regular_diabetes_v1",
                        "fax_layout_regular_forbidden_v1",
                    ],
                    "confidence": 0.41,
                    "blocked": False,
                    "blocked_reasons": [],
                },
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] != "review_blocked"
    assert "qty.diabetes_x" in (sheet.get("fields") or [])
    assert "qty.change_1_x" in (sheet.get("fields") or [])
    assert "template_unresolved" not in (sheet.get("warnings") or [])


def test_get_ocr_sheet_does_not_block_on_template_when_equivalent_multi_template_header_is_ambiguous():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-multi-template-ambiguous-header",
        facility_id="FAC00016",
    )
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
                        "|日付|区分|メニュー|数量A|数量B|備考|",
                        "|---|---|---|---|---|---|",
                        "|03/21|朝|Menu A|9|4||",
                    ]
                ),
                "template_resolution": {
                    "resolved_template_id": "",
                    "candidate_template_ids": [
                        "fax_layout_regular_diabetes_v1",
                        "fax_layout_regular_forbidden_v1",
                    ],
                    "confidence": 0.41,
                    "blocked": False,
                    "blocked_reasons": [],
                },
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert "qty.diabetes_x" in (sheet.get("fields") or [])
    assert "template_unresolved" not in (sheet.get("warnings") or [])


def test_fac00013_uses_forbidden_template_as_single_canonical_config():
    facility_config = config_service.get_facility_config("FAC00013")

    assert isinstance(facility_config, dict)
    assert facility_config["fax_template_id"] == "fax_layout_regular_forbidden_v1"
    assert facility_config["fax_template_ids"] == ["fax_layout_regular_forbidden_v1"]


def test_get_ocr_sheet_projects_fac00006_repeated_regular_round_columns_from_source_indexes():
    order_service.clear_all()
    order = _seed_order_without_lines_for_facility(
        message_id="msg-ocr-redesign-fac00006-repeated-rounds",
        facility_id="FAC00006",
        received_at=datetime(2026, 4, 25, 9, 0, 0),
    )
    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": "大豆のトマト煮",
            "menu_date": date(2026, 4, 26),
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
                "table_raw": "\n".join(
                    [
                        "|日付|区分|コード|メニュー|常食1回目|常食2回目|常食3回目|常食袋分け|軟菜|ミキサー|禁食肉禁|禁食魚禁|備考欄|",
                        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
                        "|04/26|朝|CHE|大豆のトマト煮|11||13|2|3|4|5|6||",
                    ]
                ),
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
        current = order_service.get_current_sheet_context(order["id"])
        workflow = order_service.get_order_workflow_state(order["id"], refresh=True)
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet["header"] == [
        "日付",
        "区分",
        "メニュー",
        "常食1回目",
        "常食2回目",
        "常食3回目",
        "常食袋分け",
        "軟菜",
        "ミキサー",
        "禁食肉禁",
        "禁食魚禁",
        "備考欄",
    ]
    assert sheet["rows"][0] == ["04/26", "朝", "大豆のトマト煮", "11", "", "13", "2", "3", "4", "5", "6", ""]
    assert "template_unresolved" not in (sheet.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])
    assert "template_unresolved" not in (current.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" not in (current.get("warnings") or [])
    assert "template_unresolved" not in (workflow.get("apply_gate", {}).get("blockers") or [])
    assert "sheet_quantity_column_unmapped" not in (workflow.get("apply_gate", {}).get("blockers") or [])


def test_get_ocr_sheet_projects_fac00006_repeated_regular_round_columns_even_when_position_fallback_is_partial():
    order_service.clear_all()
    order = _seed_order_without_lines_for_facility(
        message_id="msg-ocr-redesign-fac00006-partial-position-fallback",
        facility_id="FAC00006",
        received_at=datetime(2026, 4, 25, 9, 0, 0),
    )
    original_build_position_menu_entries_safe = order_service._build_position_menu_entries_safe
    order_service._build_position_menu_entries_safe = lambda *_args, **_kwargs: [
        {
            "menu_name": "大豆のトマト煮",
            "menu_date": date(2026, 4, 26),
            "daypart_key": "breakfast",
            "slot_index": 0,
            "order": 0,
        }
    ]

    rows = [
        ["日付", "区分", "コード", "メニュー", "常食1回目", "常食2回目", "常食3回目", "常食袋分け", "軟菜", "ミキサー", "禁食肉禁", "禁食魚禁", "備考欄"],
        ["04/26", "朝", "CHE", "大豆のトマト煮", "11", "", "13", "2", "3", "4", "5", "6", ""],
    ]

    try:
        order_service._save_order_ocr_cache(
            order["id"],
            {
                "template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "table_raw": "\n".join(
                    [
                        "|日付|区分|コード|メニュー|常食1回目|常食2回目|常食3回目|常食袋分け|軟菜|ミキサー|禁食肉禁|禁食魚禁|備考欄|",
                        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
                        "|04/26|朝|CHE|大豆のトマト煮|11||13|2|3|4|5|6||",
                    ]
                ),
                "tables": [
                    {
                        "table_id": "p1_t1",
                        "page_index": 1,
                        "rows": rows,
                        "cells": _structured_cells(rows),
                        "row_count": len(rows),
                        "col_count": len(rows[0]),
                    }
                ],
                "column_mapping_resolution": {
                    "resolved_value": "4:qty.regular_x|7:qty.regular_bag_x",
                    "resolved_column_mapping_id": "4:qty.regular_x|7:qty.regular_bag_x",
                    "blocked": False,
                    "blocked_reasons": [],
                    "requires_user_choice": False,
                    "decision_source": "position_fallback",
                    "partial_quantity_mapping": True,
                    "confidence": 0.86,
                    "evidence_ref": {
                        "page_index": 1,
                        "table_id": "p1_t1",
                        "source_col_indexes": [4, 7],
                        "mapped_fields": ["qty.regular_x", "qty.regular_bag_x"],
                    },
                    "mapped_quantity_fields": ["qty.regular_x", "qty.regular_bag_x"],
                    "expected_quantity_fields": [
                        "qty.regular_x",
                        "qty.change_1_x",
                        "qty.change_2_x",
                        "qty.regular_bag_x",
                        "qty.soft_x",
                        "qty.mixer_x",
                        "qty.no_meat_x",
                        "qty.no_fish_x",
                    ],
                },
            },
        )

        sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
        workflow = order_service.get_order_workflow_state(order["id"], refresh=True)
    finally:
        order_service._build_position_menu_entries_safe = original_build_position_menu_entries_safe

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert sheet["source"] == "weekly_menu+ocr_payload"
    assert sheet["rows"][0] == ["04/26", "朝", "大豆のトマト煮", "11", "", "13", "2", "3", "4", "5", "6", ""]
    assert "sheet_quantity_column_unmapped" not in (sheet.get("warnings") or [])
    assert "sheet_quantity_column_unmapped" not in (workflow.get("apply_gate", {}).get("blockers") or [])


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


def test_get_ocr_sheet_never_uses_confirmed_order_lines_as_quantity_source_for_weekly_menu(monkeypatch):
    order_service.clear_all()
    order = _seed_order(message_id="msg-ocr-redesign-no-confirmed-order-lines-weekly-menu")

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
    monkeypatch.setattr(
        order_service,
        "_build_monthly_menu_diagnostics",
        lambda *, week_id, facility_id: {
            "month_id": "2026-03",
            "resolved_week_id": week_id,
            "order_codes": [],
            "row_codes": [],
            "global_entries_count": 1,
            "facility_entries_count": 1,
        },
    )

    order_service._save_order_ocr_cache(
        order["id"],
        {
            "tables": [
                {
                    "page_index": 1,
                    "table_id": "page1_table1",
                    "rows": [
                        ["日付", "区分", "メニュー", "備考欄"],
                        ["03/21", "朝", "Menu A", ""],
                    ],
                    "cells": _structured_cells(
                        [
                            ["日付", "区分", "メニュー", "備考欄"],
                            ["03/21", "朝", "Menu A", ""],
                        ]
                    ),
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
            "template_resolution": {
                "requested_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "resolved_template_id": "fax_layout_regular_soft_mixer_forbidden_v1",
                "blocked": False,
                "blocked_reasons": [],
            },
            "table_box": [0.1, 0.2, 0.9, 0.8],
            "grid_column_edges": [0.1, 0.5, 0.9],
            "grid_row_edges": [0.2, 0.4, 0.8],
        },
    )

    sheet, sheet_error = order_service.get_ocr_sheet(order["id"])

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert str(sheet["source"]).startswith("weekly_menu")
    qty_indexes = [idx for idx, field in enumerate(sheet.get("fields") or []) if str(field).startswith("qty.")]
    assert qty_indexes
    assert all(str(sheet["rows"][0][idx] or "").strip() == "" for idx in qty_indexes)
    trace_rows = (sheet.get("trace") or {}).get("rows") or []
    assert trace_rows
    assert all(trace_rows[0][idx] != "order_lines" for idx in qty_indexes)
    assert sheet.get("confirmed_lines_retained") is False


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
    refreshed = order_service.get_order_by_id(order["id"]) or confirmed
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
        assert exc.code in {
            "draft_rows_unparseable",
            "draft_lines_empty",
            "draft_semantic_materialization_failed",
        }

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


def test_confirm_materialization_uses_sheet_fields_for_fac00004_multi_quantity_rows():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-confirm-fac00004-sheet-fields",
        facility_id="FAC00004",
    )

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "65", "45", "", "2", "", "", "", "豚肉2"]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    confirmed = order_service.confirm_order(order["id"])
    assert confirmed is not None

    refreshed = order_service.get_order_by_id(order["id"])
    assert isinstance(refreshed, dict)
    hits = [
        line
        for line in (refreshed.get("lines") or [])
        if line.get("date") == "2026-03-24"
        and line.get("daypart") == "昼"
        and line.get("menu_name") == "ホイコーロー"
    ]
    assert {(line.get("diet_type"), line.get("quantity_original")) for line in hits} == {
        ("regular", 65),
        ("daycare", 45),
        ("no_meat", 2),
    }


def test_confirm_materialization_uses_sheet_fields_for_fac00014_multi_quantity_rows():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-confirm-fac00014-sheet-fields",
        facility_id="FAC00014",
    )

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "職員", "肉禁", "魚禁", "ゴマアレルギー", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "102", "2", "2", "1", "", "", ""]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    confirmed = order_service.confirm_order(order["id"])
    assert confirmed is not None

    refreshed = order_service.get_order_by_id(order["id"])
    assert isinstance(refreshed, dict)
    hits = [
        line
        for line in (refreshed.get("lines") or [])
        if line.get("date") == "2026-03-24"
        and line.get("daypart") == "昼"
        and line.get("menu_name") == "ホイコーロー"
    ]
    assert {(line.get("diet_type"), line.get("quantity_original")) for line in hits} == {
        ("regular", 102),
        ("staff", 2),
        ("no_meat", 2),
        ("no_fish", 1),
    }


def test_equivalent_fac00014_templates_do_not_block_current_sheet_or_workflow():
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-fac00014-equivalent-template-collapse",
        facility_id="FAC00014",
    )

    rows = [
        ["日付", "区分", "メニュー", "常食", "職員", "禁食", "", "", "", "備考欄"],
        ["", "", "", "", "", "肉禁", "魚禁", "ゴマアレルギー", "変更1", ""],
        ["04/06", "朝", "Menu A", "44", "2", "1", "", "", "", ""],
    ]
    order_service._save_order_ocr_cache(
        order["id"],
        {
            "table_raw": "\n".join(
                [
                    "|日付|区分|メニュー|常食|職員|禁食||||備考欄|",
                    "|---|---|---|---|---|---|---|---|---|---|",
                    "||||||肉禁|魚禁|ゴマアレルギー|変更1||",
                    "|04/06|朝|Menu A|44|2|1|||||",
                ]
            ),
            "table_rows": rows,
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
            "template_resolution": {
                "resolved_template_id": "",
                "candidate_template_ids": [],
                "confidence": 0.2,
                "blocked": True,
                "blocked_reasons": ["template_resolution_missing"],
            },
        },
    )

    sheet, sheet_error = order_service.get_ocr_sheet(order["id"])
    current = order_service.get_current_sheet_context(order["id"])
    workflow = order_service.get_order_workflow_state(order["id"], refresh=True)

    assert sheet_error is None
    assert isinstance(sheet, dict)
    assert isinstance(current, dict)
    assert isinstance(workflow, dict)
    assert "template_unresolved" not in (sheet.get("warnings") or [])
    assert "template_unresolved" not in (sheet.get("blockers") or [])
    assert "template_unresolved" not in (current.get("warnings") or [])
    assert "template_unresolved" not in (current.get("blockers") or [])
    assert "template_unresolved" not in (workflow.get("apply_gate", {}).get("blockers") or [])
    assert "qty.staff_x" in (sheet.get("fields") or [])
    assert "qty.no_fish_x" in (sheet.get("fields") or [])
    assert "qty.sesame_allergy_x" in (sheet.get("fields") or [])
    assert "qty.change_1_x" in (sheet.get("fields") or [])


def test_apply_latest_draft_rejects_materialization_mismatch_for_semantic_sheet(monkeypatch):
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-apply-mismatch-guard-fac00014",
        facility_id="FAC00014",
    )

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "職員", "肉禁", "魚禁", "ゴマアレルギー", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "102", "2", "2", "1", "", "", ""]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    def _corrupt_position_mapping(lines, week_id, *, facility_id=None, entries_override=None):
        mutated = [dict(line) for line in lines]
        if mutated:
            mutated[0].pop("source_row_index", None)
            mutated[0]["quantity_original"] = 2
        return mutated, 1

    monkeypatch.setattr(order_service, "_apply_menu_position_mapping_safe", _corrupt_position_mapping)

    applied, apply_error = order_service.apply_latest_draft(order["id"])

    assert applied is None
    assert apply_error == "draft_materialization_mismatch"


def test_confirm_order_rejects_materialization_mismatch_for_semantic_sheet(monkeypatch):
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-confirm-mismatch-guard-fac00004",
        facility_id="FAC00004",
    )

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "65", "45", "", "2", "", "", "", "豚肉2"]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    def _corrupt_position_mapping(lines, week_id, *, facility_id=None, entries_override=None):
        mutated = [dict(line) for line in lines]
        if mutated:
            mutated[0].pop("source_row_index", None)
            mutated[0]["quantity_original"] = 2
        return mutated, 1

    monkeypatch.setattr(order_service, "_apply_menu_position_mapping_safe", _corrupt_position_mapping)

    try:
        order_service.confirm_order(order["id"])
        assert False, "confirm should have raised for a semantic draft materialization mismatch"
    except order_service.ConfirmMaterializationError as exc:
        assert exc.code == "draft_materialization_mismatch"


def test_confirm_materialization_rejects_semantic_draft_fallback(monkeypatch):
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-confirm-semantic-fallback-guard",
        facility_id="FAC00004",
    )

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "65", "45", "", "2", "", "", "", "豚肉2"]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    monkeypatch.setattr(order_service, "_build_materialization_lines_from_sheet_rows", lambda **_kwargs: [])

    try:
        order_service.confirm_order(order["id"])
        assert False, "confirm should fail instead of falling back to raw template parsing"
    except order_service.ConfirmMaterializationError as exc:
        assert exc.code == "draft_semantic_materialization_failed"


def test_confirm_materialization_guard_rejects_source_row_mismatch(monkeypatch):
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-confirm-source-row-guard",
        facility_id="FAC00014",
    )

    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "職員", "肉禁", "魚禁", "ゴマアレルギー", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "102", "2", "2", "1", "", "", ""]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )

    assert error is None
    assert saved is not None

    def _drop_source_row_indexes(lines, *_args, **_kwargs):
        mutated = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            copied = dict(line)
            copied.pop("source_row_index", None)
            mutated.append(copied)
        return mutated, len(mutated)

    monkeypatch.setattr(order_service, "_apply_menu_position_mapping_safe", _drop_source_row_indexes)

    try:
        order_service.confirm_order(order["id"])
        assert False, "confirm should fail when materialized lines no longer match draft source rows"
    except order_service.ConfirmMaterializationError as exc:
        assert exc.code == "draft_materialization_mismatch"


def test_apply_latest_draft_blocks_when_sheet_quantities_diverge_for_fac00004(monkeypatch):
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-guard-fac00004-apply",
        facility_id="FAC00004",
    )
    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "通所", "職員", "肉禁", "魚禁", "揚げ物禁", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "65", "45", "", "2", "", "", "", "豚肉2"]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.daycare_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.no_fried_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )
    assert error is None
    assert saved is not None

    original_builder = order_service._build_materialization_lines_from_sheet_rows

    def _corrupt_builder(**kwargs):
        lines = original_builder(**kwargs)
        for line in lines:
            if line.get("diet_type") == "regular":
                line["quantity_original"] = 2
                break
        return lines

    monkeypatch.setattr(order_service, "_build_materialization_lines_from_sheet_rows", _corrupt_builder)

    applied, apply_error = order_service.apply_latest_draft(order["id"])

    assert applied is None
    assert apply_error == "draft_materialization_mismatch"


def test_confirm_order_blocks_when_sheet_quantities_diverge_for_fac00014(monkeypatch):
    order_service.clear_all()
    order = _seed_order_for_facility(
        message_id="msg-ocr-redesign-guard-fac00014-confirm",
        facility_id="FAC00014",
    )
    saved, error = order_service.save_ocr_sheet_exact(
        order["id"],
        header=["日付", "区分", "メニュー", "常食", "職員", "肉禁", "魚禁", "ゴマアレルギー", "変更1", "備考欄"],
        rows=[["03/24", "昼", "ホイコーロー", "102", "2", "2", "1", "", "", ""]],
        fields=[
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.staff_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.sesame_allergy_x",
            "qty.change_1_x",
            "remarks",
        ],
        row_ids=["draft-row-1"],
        ui_mode="sheet",
    )
    assert error is None
    assert saved is not None

    original_builder = order_service._build_materialization_lines_from_sheet_rows

    def _corrupt_builder(**kwargs):
        lines = original_builder(**kwargs)
        for line in lines:
            if line.get("diet_type") == "regular":
                line["quantity_original"] = 2
                break
        return lines

    monkeypatch.setattr(order_service, "_build_materialization_lines_from_sheet_rows", _corrupt_builder)

    with pytest.raises(order_service.ConfirmMaterializationError) as exc_info:
        order_service.confirm_order(order["id"])

    assert exc_info.value.code == "draft_materialization_mismatch"
