import json
from types import SimpleNamespace

from src.services import order_service


def test_sheet_header_from_template_keeps_header_group_for_area_quantity_columns() -> None:
    template = {
        "columns": [
            {"index": 0, "role": "date", "header": "日付", "name": "date_mmdd"},
            {"index": 1, "role": "daypart", "header": "区分", "name": "daypart"},
            {"index": 2, "role": "menu", "header": "メニュー", "name": "menu"},
            {
                "index": 3,
                "role": "quantity",
                "header_group": "常食",
                "header": "月",
                "name": "qty.regular_3f",
                "diet_type": "regular",
                "area_id": "3F",
            },
            {
                "index": 4,
                "role": "quantity",
                "header_group": "軟菜",
                "header": "花",
                "name": "qty.soft_2f",
                "diet_type": "soft",
                "area_id": "2F",
            },
            {
                "index": 5,
                "role": "quantity",
                "header_group": "禁食",
                "header": "肉禁",
                "name": "qty.no_meat_x",
                "diet_type": "no_meat",
                "area_id": "X",
            },
        ],
    }

    header = order_service._sheet_header_from_template(  # noqa: SLF001
        ["date_mmdd", "daypart", "menu", "qty.regular_3f", "qty.soft_2f", "qty.no_meat_x"],
        template,
    )

    assert header == ["日付", "区分", "メニュー", "常食月", "軟菜花", "肉禁"]


def test_hakodate_canonical_payload_reads_digit_evidence_from_best_method_records(monkeypatch, tmp_path) -> None:
    regions_path = tmp_path / "best_method_ocr_regions.json"
    records_path = tmp_path / "best_method_records.json"
    overlay_path = tmp_path / "best_method_overlay.png"
    regions_path.write_text(
        json.dumps(
            [
                {
                    "region_id": "E11",
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "field": "qty.regular_x",
                    "bbox": [10, 20, 30, 40],
                    "ocr_text": "",
                    "ocr_normalized": "",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    records_path.write_text(
        json.dumps(
            [
                {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "field": "qty.regular_x",
                    "bbox": [10, 20, 30, 40],
                    "raw_text": "１２",
                    "pred_digits": "12",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    overlay_path.write_bytes(b"png")

    monkeypatch.setattr(order_service, "_hakodate_best_method_draft_sheet", lambda _order_id: {"rows": []})
    monkeypatch.setattr(order_service, "get_default_output_bucket", lambda: None)
    monkeypatch.setattr(
        order_service.hakodate_cell_ocr_batch_service,
        "build_hakodate_best_method_for_manifest_item",
        lambda **_kwargs: (
            SimpleNamespace(
                outputs={
                    "ocr_regions": str(regions_path),
                    "records": str(records_path),
                    "overlay": str(overlay_path),
                },
                ocr_engine="opencv_knn_leave_one_out_k5",
            ),
            None,
        ),
    )

    payload = order_service._hakodate_canonical_payload_from_manifest_item(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        item={
            "fax_pdf": "memory://fax.pdf",
            "template_pdf": "memory://template.pdf",
            "step2_png": "memory://template.png",
            "template_bbox": [0, 0, 100, 100],
            "week_sheet_name": "4月26日～4月30日",
        },
    )

    assert isinstance(payload, dict)
    assert payload["hakodate_canonical_pipeline"]["target_cell_count"] == 1
    assert payload["hakodate_canonical_pipeline"]["evidence_record_count"] == 1
    assert payload["hakodate_canonical_pipeline"]["assigned_target_count"] == 1
    evidence = payload["hakodate_ocr_evidence_records"][0]
    assert evidence["raw_text"] == "１２"
    assert evidence["normalized_value"] == "12"
    assert evidence["source_scope"] == "hakodate_cell_crop_batch"
    assert evidence["engine_metadata"]["source_artifact"] == "best_method_records"


def test_hakodate_canonical_payload_prefers_topk_accepted_digits_over_raw_nondigit(monkeypatch, tmp_path) -> None:
    regions_path = tmp_path / "best_method_ocr_regions.json"
    records_path = tmp_path / "best_method_records.json"
    overlay_path = tmp_path / "best_method_overlay.png"
    regions_path.write_text(
        json.dumps(
            [
                {
                    "region_id": "H13",
                    "sheet_cell": "H13",
                    "worksheet_row": 13,
                    "worksheet_col": 8,
                    "field": "qty.no_fish_x",
                    "bbox": [100, 200, 140, 240],
                    "ocr_text": "-",
                    "ocr_normalized": "1",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    records_path.write_text(
        json.dumps(
            [
                {
                    "sheet_cell": "H13",
                    "worksheet_row": 13,
                    "worksheet_col": 8,
                    "field": "qty.no_fish_x",
                    "bbox": [100, 200, 140, 240],
                    "raw_text": "-",
                    "pred_digits": "1",
                    "recognizer_decision_source": "topk_digits",
                    "recognizer_accepted_candidate": {"normalized_digits": "1", "score": 0.099},
                    "recognizer_score": 0.473,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    overlay_path.write_bytes(b"png")

    monkeypatch.setattr(order_service, "_hakodate_best_method_draft_sheet", lambda _order_id: {"rows": []})
    monkeypatch.setattr(order_service, "get_default_output_bucket", lambda: None)
    monkeypatch.setattr(
        order_service.hakodate_cell_ocr_batch_service,
        "build_hakodate_best_method_for_manifest_item",
        lambda **_kwargs: (
            SimpleNamespace(
                outputs={
                    "ocr_regions": str(regions_path),
                    "records": str(records_path),
                    "overlay": str(overlay_path),
                },
                ocr_engine="yomitoku_text_recognizer_corner_noise_trial",
            ),
            None,
        ),
    )

    payload = order_service._hakodate_canonical_payload_from_manifest_item(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        item={
            "fax_pdf": "memory://fax.pdf",
            "template_pdf": "memory://template.pdf",
            "step2_png": "memory://template.png",
            "template_bbox": [0, 0, 100, 100],
            "week_sheet_name": "4月26日～4月30日",
        },
    )

    assert isinstance(payload, dict)
    evidence = payload["hakodate_ocr_evidence_records"][0]
    assert evidence["raw_text"] == "1"
    assert evidence["normalized_value"] == "1"
    assert evidence["confidence"] == 0.099
    assignment = payload["hakodate_canonical_pipeline"]
    assert assignment["evidence_record_count"] == 1
    assert assignment["assigned_target_count"] == 1


def test_hakodate_evidence_assignment_blocks_without_new_payload_contract() -> None:
    assignment = order_service._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        payload={"tables": [{"rows": [["legacy", "ocr"]]}]},
    )

    assert assignment["assignment_mode"] == "ocr_evidence"
    assert assignment["status"] == "blocked"
    assert assignment["blockers"] == [
        "hakodate_ocr_evidence_missing",
        "hakodate_target_cell_map_missing",
    ]
    assert assignment["assignments"] == []


def test_hakodate_assignment_source_prefers_latest_evidence_over_legacy_cache(monkeypatch) -> None:
    latest_payload = {
        "hakodate_canonical_pipeline": {
            "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            "producer": "hakodate_cell_ocr_batch_service.build_hakodate_best_method_for_manifest_item",
        },
        "hakodate_preprocessing": {
            "target_cell_map": [
                {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "bbox": [10, 10, 30, 30],
                }
            ]
        },
        "hakodate_ocr_evidence_records": [
            {"text": "7", "center": [20, 20], "confidence": 0.9}
        ],
    }
    monkeypatch.setattr(
        order_service,
        "get_latest_ocr_evidence_run",
        lambda _order_id, backfill_from_cache=False: {"payload_json": latest_payload},
    )
    monkeypatch.setattr(
        order_service,
        "_load_order_ocr_cache",
        lambda _order_id: {"tables": [{"rows": [["legacy"]]}]},
    )

    payload = order_service._load_hakodate_assignment_source_payload("ORD_TEST")  # noqa: SLF001

    assert payload["hakodate_preprocessing"]["target_cell_map"][0]["sheet_cell"] == "E11"
    assert payload["hakodate_ocr_evidence_records"][0]["text"] == "7"


def test_hakodate_overlay_preview_prefers_latest_evidence_overlay_over_legacy_cache(monkeypatch) -> None:
    latest_payload = {
        "hakodate_canonical_pipeline": {
            "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            "producer": "hakodate_cell_ocr_batch_service.build_hakodate_best_method_for_manifest_item",
        },
        "hakodate_preprocessing": {
            "target_cell_map": [
                {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "bbox": [10, 10, 30, 30],
                }
            ]
        },
        "hakodate_ocr_evidence_records": [{"text": "7", "center": [20, 20], "confidence": 0.9}],
        "hakodate_overlay": {
            "uri": "gs://bucket/latest-overlay.png",
            "fingerprint": "latest-fingerprint",
            "producer": "hakodate_best_method_pipeline",
            "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
        },
    }
    legacy_payload = {
        "hakodate_overlay": {
            "uri": "gs://bucket/legacy-overlay.png",
            "fingerprint": "legacy-fingerprint",
            "producer": "hakodate_best_method_pipeline",
        }
    }
    monkeypatch.setattr(
        order_service,
        "get_latest_ocr_evidence_run",
        lambda _order_id, backfill_from_cache=False: {"id": "OEV_LATEST", "payload_json": latest_payload},
    )
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", lambda _order_id: legacy_payload)
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}")
    monkeypatch.setattr(
        order_service,
        "get_cached_hakodate_assignment_preview",
        lambda _order_id: {"status": "auto_assignable", "blockers": []},
    )

    preview = order_service.get_cached_hakodate_overlay_preview("ORD_TEST")

    assert preview["status"] == "ready"
    assert preview["overlay_uri"] == "gs://bucket/latest-overlay.png"
    assert preview["overlay_url"] == "signed:gs://bucket/latest-overlay.png"
    assert preview["source_evidence_run_id"] == "OEV_LATEST"
    assert preview["latest_hakodate_evidence"] is True


def test_hakodate_overlay_preview_prefers_legacy_versionless_latest_evidence_over_cache(monkeypatch) -> None:
    latest_payload = {
        "hakodate_canonical_pipeline": {
            "producer": "hakodate_cell_ocr_batch_service.build_hakodate_best_method_for_manifest_item",
            "source": "live_order_facility_source_workbook",
            "requested_order_id": "ORD_TEST",
        },
        "hakodate_preprocessing": {
            "target_cell_map": [
                {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "bbox": [10, 10, 30, 30],
                }
            ]
        },
        "hakodate_ocr_evidence_records": [{"text": "7", "center": [20, 20], "confidence": 0.9}],
        "hakodate_overlay": {
            "uri": "gs://bucket/latest-versionless-overlay.png",
            "fingerprint": "latest-versionless-fingerprint",
            "producer": "hakodate_best_method_pipeline",
        },
    }
    stale_cache_payload = {
        "hakodate_overlay": {
            "uri": "gs://bucket/stale-cache-overlay.png",
            "fingerprint": "stale-cache-fingerprint",
            "producer": "hakodate_best_method_pipeline",
            "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
        },
        "hakodate_assignment_preview": {
            "fingerprint": "stale-cache-fingerprint",
            "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            "assignment": {"status": "auto_assignable", "blockers": []},
        },
    }
    monkeypatch.setattr(
        order_service,
        "get_latest_ocr_evidence_run",
        lambda _order_id, backfill_from_cache=False: {"id": "OEV_VERSIONLESS", "payload_json": latest_payload},
    )
    monkeypatch.setattr(order_service, "_load_order_ocr_cache", lambda _order_id: stale_cache_payload)
    monkeypatch.setattr(order_service, "_signed_url_from_uri", lambda uri: f"signed:{uri}")
    monkeypatch.setattr(
        order_service,
        "get_cached_hakodate_assignment_preview",
        lambda _order_id: {"status": "auto_assignable", "blockers": []},
    )

    preview = order_service.get_cached_hakodate_overlay_preview("ORD_TEST")

    assert preview["status"] == "ready"
    assert preview["overlay_uri"] == "gs://bucket/latest-versionless-overlay.png"
    assert preview["overlay_url"] == "signed:gs://bucket/latest-versionless-overlay.png"
    assert preview["source_evidence_run_id"] == "OEV_VERSIONLESS"
    assert preview["latest_hakodate_evidence"] is True


def test_hakodate_current_draft_is_rebuilt_when_latest_evidence_exists(monkeypatch) -> None:
    stale_draft = {"draft_sheet_json": {"source": "weekly_menu", "rows": []}}
    rebuilt_draft = {
        "draft_sheet_json": {
            "source": "hakodate_ocr_evidence_sheet",
            "hakodate_evidence_projection": {"metrics": {"applied_count": 1}},
            "rows": [["04/26", "朝", "献立A", "7"]],
        }
    }
    calls: list[str] = []
    monkeypatch.setattr(order_service, "_latest_hakodate_evidence_available", lambda _order_id: True)
    monkeypatch.setattr(
        order_service,
        "get_latest_sheet_draft",
        lambda _order_id, **_kwargs: stale_draft,
    )

    def fake_switch(order_id: str, *, edited_by: str | None = None):
        calls.append(f"{order_id}:{edited_by}")
        return rebuilt_draft, None

    monkeypatch.setattr(order_service, "switch_draft_to_latest_evidence", fake_switch)

    draft, error = order_service.ensure_hakodate_evidence_draft_current(
        "ORD_TEST",
        edited_by="unit-test",
    )

    assert error is None
    assert draft is rebuilt_draft
    assert calls == ["ORD_TEST:unit-test"]


def test_hakodate_current_draft_is_reused_when_projection_exists(monkeypatch) -> None:
    current_draft = {
        "id": "ODR_CURRENT",
        "draft_sheet_json": {
            "source": "hakodate_ocr_evidence_sheet",
            "hakodate_evidence_projection": {"metrics": {"applied_count": 1}},
            "rows": [["04/26", "朝", "献立A", "7"]],
        },
    }
    monkeypatch.setattr(order_service, "_latest_hakodate_evidence_available", lambda _order_id: True)
    monkeypatch.setattr(
        order_service,
        "get_latest_sheet_draft",
        lambda _order_id, **_kwargs: current_draft,
    )

    def fail_switch(*_args, **_kwargs):
        raise AssertionError("existing Hakodate projection draft must not be rebuilt on read")

    monkeypatch.setattr(order_service, "switch_draft_to_latest_evidence", fail_switch)

    draft, error = order_service.ensure_hakodate_evidence_draft_current(
        "ORD_TEST",
        edited_by="unit-test",
    )

    assert error is None
    assert draft is current_draft


def test_hakodate_projection_draft_is_authoritative_and_not_semantic_rebased(monkeypatch) -> None:
    current_draft = {
        "id": "ODR_CURRENT",
        "draft_sheet_json": {
            "source": "hakodate_ocr_evidence_sheet",
            "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
            "rows": [["04/26", "朝", "献立A", "7"]],
            "hakodate_evidence_projection": {"metrics": {"applied_count": 1}},
        },
        "edited_by": "auto-hakodate-evidence-ocr-sheet",
    }

    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("Hakodate evidence sheet must not be semantically rebased")

    monkeypatch.setattr(order_service, "_build_fresh_semantic_sheet_for_draft_rebase", fail_rebuild)

    assert order_service._draft_record_is_authoritative_current_sheet(current_draft) is True  # noqa: SLF001
    rebase_required, rebuilt = order_service._draft_record_requires_current_sheet_semantic_rebase(  # noqa: SLF001
        "ORD_TEST",
        current_draft,
    )

    assert rebase_required is False
    assert rebuilt is None


def test_hakodate_payload_augmentation_uses_live_canonical_payload_not_legacy_ocr_sources(monkeypatch) -> None:
    payload = {
        "grid_column_edges": [9.0, 9.2, 9.4, 9.6, 9.8],
        "grid_row_edges": [9.0, 9.2, 9.4],
        "table_rows": [["4/26", "朝", "旧OCR献立", "99"]],
        "tables": [
            {
                "rows": [["4/26", "朝", "旧OCR献立", "99"]],
                "cells": [
                    {
                        "row_index": 1,
                        "col_index": 3,
                        "text": "９９",
                        "bbox": [9.6, 9.2, 9.8, 9.4],
                    }
                ],
            }
        ],
        "tokens": [{"text": "９９", "bbox": [9.6, 9.2, 9.8, 9.4], "x": 9.7, "y": 9.3}],
    }
    template = {
        "quantity_assignment_strategy": "hakodate",
        "hakodate_header_rows": 1,
        "main_ocr_row_fields": ["date", "daypart", "menu_name", "qty.regular_x"],
    }
    monkeypatch.setattr(
        order_service,
        "_hakodate_document_context",
        lambda order_id: {
            "document_uri": "memory://fax.pdf",
            "facility_id": "FAC_TEST",
            "week_id": "2026-04@2026-04-26_2026-04-30",
        },
    )
    monkeypatch.setattr(
        order_service,
        "_build_position_menu_entries_safe",
        lambda week_id, facility_id=None: [
            {"menu_date": "2026-04-26", "daypart_key": "朝", "menu_name": "献立A"}
        ],
    )
    monkeypatch.setattr(
        order_service,
        "_hakodate_canonical_payload_from_manifest_item",
        lambda **_kwargs: {
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "D11",
                        "sheet_cell": "D11",
                        "worksheet_row": 11,
                        "worksheet_col": 4,
                        "semantic_field": "qty.regular_x",
                        "date": "04/26",
                        "menu_name": "献立A",
                        "bbox": [0.55, 0.2, 0.95, 0.45],
                        "source": "hakodate_best_method_pipeline",
                    }
                ]
            },
            "hakodate_ocr_evidence_records": [
                {
                    "evidence_id": "hakodate-cell-1",
                    "text": "１２",
                    "normalized_value": "12",
                    "center": [0.75, 0.31],
                    "source_scope": "hakodate_cell_crop_batch",
                    "engine": "yomitoku_contact_sheet_batch",
                }
            ],
        },
    )

    augmented = order_service._augment_hakodate_ocr_payload_artifacts(  # noqa: SLF001
        order_id="ORD_TEST",
        payload=payload,
        template=template,
    )

    target_cells = augmented["hakodate_preprocessing"]["target_cell_map"]
    evidence_records = augmented["hakodate_ocr_evidence_records"]
    assert target_cells[0]["semantic_field"] == "qty.regular_x"
    assert target_cells[0]["date"] == "04/26"
    assert target_cells[0]["menu_name"] == "献立A"
    assert target_cells[0]["bbox"] == [0.55, 0.2, 0.95, 0.45]
    assert evidence_records[0]["normalized_value"] == "12"
    assert evidence_records[0]["center"] == [0.75, 0.31]
    assert evidence_records[0]["source_scope"] == "hakodate_cell_crop_batch"
    assert "99" not in str(target_cells)
    assert "99" not in str(evidence_records)


def test_hakodate_payload_augmentation_regenerates_stale_pipeline_payload(monkeypatch) -> None:
    payload = {
        "hakodate_canonical_pipeline": {
            "version": "old-hakodate-pipeline",
            "producer": "hakodate_cell_ocr_batch_service.build_hakodate_best_method_for_manifest_item",
        },
        "hakodate_preprocessing": {
            "target_cell_map": [{"target_cell_id": "old", "sheet_cell": "D11"}],
        },
        "hakodate_ocr_evidence_records": [
            {
                "evidence_id": "old-ev",
                "text": "9",
                "normalized_value": "9",
                "source_scope": "hakodate_cell_crop_batch",
            }
        ],
    }
    template = {"quantity_assignment_strategy": "hakodate"}
    calls = {"count": 0}

    monkeypatch.setattr(
        order_service,
        "_hakodate_document_context",
        lambda _order_id: {"facility_id": "FAC_TEST"},
    )

    def _canonical_payload(**_kwargs):
        calls["count"] += 1
        return {
            "hakodate_canonical_pipeline": {
                "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
                "producer": "hakodate_cell_ocr_batch_service.build_hakodate_best_method_for_manifest_item",
            },
            "hakodate_preprocessing": {
                "target_cell_map": [{"target_cell_id": "new", "sheet_cell": "D11"}],
            },
            "hakodate_ocr_evidence_records": [
                {
                    "evidence_id": "new-ev",
                    "text": "7",
                    "normalized_value": "7",
                    "source_scope": "hakodate_cell_crop_batch",
                }
            ],
        }

    monkeypatch.setattr(order_service, "_hakodate_canonical_payload_from_manifest_item", _canonical_payload)

    augmented = order_service._augment_hakodate_ocr_payload_artifacts(  # noqa: SLF001
        order_id="ORD_TEST",
        payload=payload,
        template=template,
    )

    assert calls["count"] == 1
    assert augmented["hakodate_canonical_pipeline"]["version"] == order_service.HAKODATE_CANONICAL_PIPELINE_VERSION
    assert augmented["hakodate_preprocessing"]["target_cell_map"][0]["target_cell_id"] == "new"
    assert augmented["hakodate_ocr_evidence_records"][0]["text"] == "7"


def test_hakodate_output_content_requires_real_artifacts_not_metadata_only() -> None:
    payload = {
        "_reparse_debug": {"provider": "gemini"},
        "hakodate_canonical_pipeline": {
            "producer": "hakodate_cell_ocr_batch_service.build_hakodate_best_method_for_manifest_item",
            "status": "blocked",
            "blockers": ["hakodate_canonical_pipeline_failed"],
        },
    }

    assert order_service._payload_has_hakodate_output_content(  # noqa: SLF001
        payload,
        order_id="ORD_TEST",
    ) is False


def test_hakodate_evidence_assignment_rejects_generic_legacy_payload_keys() -> None:
    assignment = order_service._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        payload={
            "target_cell_map": [
                {
                    "target_cell_id": "legacy-target",
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "semantic_field": "qty.regular_x",
                    "bbox": [10, 10, 30, 30],
                }
            ],
            "ocr_evidence_records": [{"text": "99", "center": [20, 20]}],
        },
    )

    assert assignment["status"] == "blocked"
    assert assignment["blockers"] == [
        "hakodate_ocr_evidence_missing",
        "hakodate_target_cell_map_missing",
    ]


def test_hakodate_evidence_projection_applies_assigned_cells_to_sheet_rows() -> None:
    assignment = order_service._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        payload={
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "E11",
                        "sheet_cell": "E11",
                        "worksheet_row": 11,
                        "worksheet_col": 5,
                        "semantic_field": "qty.regular_x",
                        "bbox": [10, 10, 30, 30],
                        "logical_targets": [
                            {
                                "sheet_cell": "E11",
                                "worksheet_row": 11,
                                "worksheet_col": 5,
                                "field": "qty.regular_x",
                                "date": "2026-04-26",
                                "daypart": "朝",
                                "menu_name": "献立A",
                            }
                        ],
                    }
                ]
            },
            "hakodate_ocr_evidence_records": [
                {"text": "１２", "center": [20, 20], "confidence": 0.9}
            ],
        },
    )
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", "99"]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )

    assert assignment["status"] == "auto_assignable"
    assert projected["source"] == "hakodate_ocr_evidence_sheet"
    assert projected["rows"] == [["4/26", "朝", "献立A", "12"]]
    assert projected["blockers"] == []
    assert projected["hakodate_evidence_projection"]["metrics"] == {
        "assignment_count": 1,
        "applied_count": 1,
        "skipped_count": 0,
        "deferred_count": 0,
        "ignored_count": 0,
        "cleared_legacy_cell_count": 1,
        "expanded_cell_same_daypart_filled_count": 0,
    }


def test_hakodate_projection_applies_expanded_cell_same_daypart_copy_when_enabled() -> None:
    assignment = {
        "status": "auto_assignable",
        "assignment_mode": "ocr_evidence",
        "warnings": [],
        "blockers": [],
        "target_cells": [
            {
                "sheet_cell": "E11",
                "worksheet_row": 11,
                "worksheet_col": 5,
                "semantic_field": "qty.regular_x",
                "metadata": {"truth": {"row_index": 0, "field": "qty.regular_x"}},
            }
        ],
        "sheet_output": {
            "cells": {
                "E11": {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "semantic_field": "qty.regular_x",
                    "value_normalized": "44",
                    "assignment_confidence": 0.9,
                    "metadata": {"truth": {"row_index": 0, "field": "qty.regular_x"}},
                }
            }
        },
    }
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [
            ["4/26", "朝", "献立A", ""],
            ["4/26", "朝", "献立B", ""],
            ["4/26", "昼", "献立C", ""],
        ],
        "row_ids": ["row-a", "row-b", "row-c"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config={"expanded_cell_same_daypart_copy_enabled": True},
    )

    assert projected["rows"] == [
        ["4/26", "朝", "献立A", "44"],
        ["4/26", "朝", "献立B", "44"],
        ["4/26", "昼", "献立C", ""],
    ]
    metrics = projected["hakodate_evidence_projection"]["metrics"]
    assert metrics["applied_count"] == 1
    assert metrics["expanded_cell_same_daypart_filled_count"] == 1
    assert projected["cell_provenance_rows"][1][3] == "expanded_cell_same_daypart_copy"


def test_hakodate_projection_limits_expanded_cell_copy_to_body_merge_policy_columns() -> None:
    assignment = {
        "status": "auto_assignable",
        "assignment_mode": "ocr_evidence",
        "warnings": [],
        "blockers": [],
        "target_cells": [
            {
                "sheet_cell": "D11",
                "worksheet_row": 11,
                "worksheet_col": 4,
                "semantic_field": "qty.regular_x",
                "metadata": {"truth": {"row_index": 0, "field": "qty.regular_x"}},
            },
            {
                "sheet_cell": "E11",
                "worksheet_row": 11,
                "worksheet_col": 5,
                "semantic_field": "qty.diabetes_x",
                "metadata": {"truth": {"row_index": 0, "field": "qty.diabetes_x"}},
            },
        ],
        "sheet_output": {
            "cells": {
                "D11": {
                    "sheet_cell": "D11",
                    "worksheet_row": 11,
                    "worksheet_col": 4,
                    "semantic_field": "qty.regular_x",
                    "value_normalized": "44",
                    "assignment_confidence": 0.9,
                    "metadata": {"truth": {"row_index": 0, "field": "qty.regular_x"}},
                },
                "E11": {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "semantic_field": "qty.diabetes_x",
                    "value_normalized": "5",
                    "assignment_confidence": 0.9,
                    "metadata": {"truth": {"row_index": 0, "field": "qty.diabetes_x"}},
                },
            }
        },
    }
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x", "qty.diabetes_x"],
        "rows": [
            ["4/26", "朝", "献立A", "", ""],
            ["4/26", "朝", "献立B", "", ""],
            ["4/26", "昼", "献立C", "", ""],
        ],
        "row_ids": ["row-a", "row-b", "row-c"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
        facility_config={
            "expanded_cell_same_daypart_copy_enabled": True,
            "fax_template": {
                "body_merge_policy": {
                    "mode": "daypart",
                    "columns": ["qty.regular_x"],
                    "required": True,
                },
                "columns": [
                    {"index": 0, "role": "date", "header": "日付"},
                    {"index": 1, "role": "daypart", "header": "区分"},
                    {"index": 2, "role": "menu_name", "header": "メニュー"},
                    {"index": 3, "role": "quantity", "header": "常食", "name": "常食"},
                    {"index": 4, "role": "quantity", "header": "糖尿", "name": "糖尿"},
                ],
            },
        },
    )

    assert projected["rows"] == [
        ["4/26", "朝", "献立A", "44", "5"],
        ["4/26", "朝", "献立B", "44", ""],
        ["4/26", "昼", "献立C", "", ""],
    ]
    metrics = projected["hakodate_evidence_projection"]["metrics"]
    assert metrics["expanded_cell_same_daypart_filled_count"] == 1
    assert projected["cell_provenance_rows"][1][3] == "expanded_cell_same_daypart_copy"
    assert projected["cell_provenance_rows"][1][4] == ""


def test_hakodate_projection_clears_legacy_quantities_when_evidence_missing() -> None:
    assignment = order_service._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        payload={
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "E11",
                        "sheet_cell": "E11",
                        "worksheet_row": 11,
                        "worksheet_col": 5,
                        "semantic_field": "qty.regular_x",
                        "date": "2026-04-26",
                        "daypart": "朝",
                        "menu_name": "献立A",
                        "bbox": [10, 10, 30, 30],
                    }
                ]
            }
        },
    )
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", "99"]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )

    assert assignment["status"] == "blocked"
    assert projected["rows"] == [["4/26", "朝", "献立A", ""]]
    assert "hakodate_ocr_evidence_missing" in projected["blockers"]
    assert projected["hakodate_evidence_projection"]["metrics"]["cleared_legacy_cell_count"] == 1


def test_hakodate_projection_uses_worksheet_grid_when_cell_identity_is_absent() -> None:
    assignment = {
        "status": "auto_assignable",
        "assignment_mode": "ocr_evidence",
        "warnings": [],
        "blockers": [],
        "target_cells": [
            {"sheet_cell": "E11", "worksheet_row": 11, "worksheet_col": 5, "semantic_field": "qty.regular_x"},
            {"sheet_cell": "F11", "worksheet_row": 11, "worksheet_col": 6, "semantic_field": "post_menu.F"},
            {"sheet_cell": "E12", "worksheet_row": 12, "worksheet_col": 5, "semantic_field": "qty.regular_x"},
            {"sheet_cell": "F12", "worksheet_row": 12, "worksheet_col": 6, "semantic_field": "post_menu.F"},
        ],
        "sheet_output": {
            "cells": {
                "E11": {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "semantic_field": "qty.regular_x",
                    "value_normalized": "12",
                },
                "F12": {
                    "sheet_cell": "F12",
                    "worksheet_row": 12,
                    "worksheet_col": 6,
                    "semantic_field": "post_menu.F",
                    "value_normalized": "7",
                },
            }
        },
    }
    base_sheet = {
        "fields": ["date_mmdd", "daypart", "menu", "qty.regular_x", "qty.unknown_x"],
        "rows": [["04/26", "朝", "献立A", "99", "88"], ["04/26", "朝", "献立B", "77", "66"]],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )

    assert projected["rows"] == [["04/26", "朝", "献立A", "12", ""], ["04/26", "朝", "献立B", "", "7"]]
    assert projected["blockers"] == []
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 2
    assert projected["hakodate_evidence_projection"]["metrics"]["skipped_count"] == 0


def test_hakodate_projection_defers_low_confidence_ocr_to_sheet_overlay() -> None:
    assignment = order_service._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        payload={
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "E11",
                        "sheet_cell": "E11",
                        "worksheet_row": 11,
                        "worksheet_col": 5,
                        "semantic_field": "qty.regular_x",
                        "bbox": [10, 10, 30, 30],
                        "logical_targets": [
                            {
                                "sheet_cell": "E11",
                                "worksheet_row": 11,
                                "worksheet_col": 5,
                                "field": "qty.regular_x",
                                "date": "2026-04-26",
                                "daypart": "朝",
                                "menu_name": "献立A",
                            }
                        ],
                    },
                    {
                        "target_cell_id": "F11",
                        "sheet_cell": "F11",
                        "worksheet_row": 11,
                        "worksheet_col": 6,
                        "semantic_field": "qty.soft_x",
                        "bbox": [40, 10, 60, 30],
                        "logical_targets": [
                            {
                                "sheet_cell": "F11",
                                "worksheet_row": 11,
                                "worksheet_col": 6,
                                "field": "qty.soft_x",
                                "date": "2026-04-26",
                                "daypart": "朝",
                                "menu_name": "献立A",
                            }
                        ],
                    },
                ]
            },
            "hakodate_ocr_evidence_records": [
                {"text": "5", "center": [20, 20], "confidence": 0.16},
                {"text": "7", "center": [50, 20], "confidence": 0.06},
            ],
        },
    )
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x", "qty.soft_x"],
        "rows": [["4/26", "朝", "献立A", "", ""]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )

    assert projected["rows"] == [["4/26", "朝", "献立A", "", ""]]
    assert projected["blockers"] == []
    assert projected["ocr_numeric_cell_summary"] == {
        "raw_ocr_numeric_count": 2,
        "accepted_count": 0,
        "deterministic_candidate_count": 1,
        "weak_candidate_count": 1,
        "unresolved_count": 0,
    }
    assert [
        (item["classification"], item["confidence_tier"], item["value"], item["target_col_index"])
        for item in projected["ocr_numeric_cell_items"]
    ] == [
        ("deterministic_candidate", "medium", "5", 3),
        ("weak_candidate", "low", "7", 4),
    ]
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 0
    assert projected["hakodate_evidence_projection"]["metrics"]["deferred_count"] == 2


def test_hakodate_evidence_projection_ignores_stale_row_identity_and_uses_sheet_cell() -> None:
    assignment = order_service._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        payload={
            "hakodate_preprocessing": {
                "target_cell_map": [
                    {
                        "target_cell_id": "E11",
                        "sheet_cell": "E11",
                        "worksheet_row": 11,
                        "worksheet_col": 5,
                        "semantic_field": "qty.regular_x",
                        "date": "2026-04-26",
                        "daypart": "昼",
                        "menu_name": "献立B",
                        "bbox": [10, 10, 30, 30],
                    }
                ]
            },
            "hakodate_ocr_evidence_records": [{"text": "5", "center": [20, 20]}],
        },
    )
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", ""]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )

    assert projected["rows"] == [["4/26", "朝", "献立A", "5"]]
    assert "hakodate_sheet_projection_incomplete" not in projected["blockers"]
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 1
    assert projected["hakodate_evidence_projection"]["metrics"]["skipped_count"] == 0


def test_hakodate_evidence_projection_uses_sheet_cell_even_without_target_cells() -> None:
    assignment = {
        "status": "review_required",
        "blockers": [],
        "warnings": [],
        "sheet_output": {
            "cells": {
                "E11": {
                    "sheet_cell": "E11",
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "field": "qty.regular_x",
                    "date": "2026-04-26",
                    "daypart": "昼",
                    "menu_name": "献立B",
                    "value_text": "",
                    "value_normalized": "",
                }
            }
        },
    }
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", ""]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )

    assert projected["hakodate_evidence_projection"]["metrics"]["skipped_count"] == 0
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 1
    assert projected["hakodate_evidence_projection"]["metrics"]["deferred_count"] == 0
    assert "hakodate_sheet_projection_incomplete" not in projected["blockers"]


def test_hakodate_evidence_projection_rejects_worksheet_position_without_cell_id() -> None:
    assignment = {
        "status": "review_required",
        "blockers": [],
        "warnings": [],
        "sheet_output": {
            "cells": {
                "legacy-region-1": {
                    "worksheet_row": 11,
                    "worksheet_col": 5,
                    "field": "qty.regular_x",
                    "semantic_field": "qty.regular_x",
                    "date": "2026-04-26",
                    "daypart": "朝",
                    "menu_name": "献立A",
                    "value_text": "7",
                    "value_normalized": "7",
                }
            }
        },
    }
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", ""]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )

    assert projected["rows"] == [["4/26", "朝", "献立A", ""]]
    assert projected["hakodate_evidence_projection"]["metrics"]["applied_count"] == 0
    assert projected["hakodate_evidence_projection"]["metrics"]["skipped_count"] == 1
    assert projected["hakodate_evidence_projection"]["skipped"][0]["skip_reason"] == "field_not_found"
    assert "hakodate_sheet_projection_incomplete" in projected["blockers"]


def test_hakodate_evidence_projection_ignores_outside_active_sheet_rows() -> None:
    assignment = {
        "status": "review_required",
        "blockers": [],
        "warnings": [],
        "target_cells": [
            {"sheet_cell": "E11", "worksheet_row": 11, "worksheet_col": 5},
            {"sheet_cell": "E12", "worksheet_row": 12, "worksheet_col": 5},
        ],
        "sheet_output": {
            "cells": {
                "E12": {
                    "sheet_cell": "E12",
                    "worksheet_row": 12,
                    "worksheet_col": 5,
                    "field": "qty.regular_x",
                    "value_text": "9",
                    "value_normalized": "9",
                }
            }
        },
    }
    base_sheet = {
        "fields": ["date", "daypart", "menu_name", "qty.regular_x"],
        "rows": [["4/26", "朝", "献立A", ""]],
        "row_ids": ["row-a"],
        "warnings": [],
        "blockers": [],
    }

    projected = order_service._apply_hakodate_sheet_output_to_sheet_payload(  # noqa: SLF001
        base_sheet=base_sheet,
        assignment=assignment,
    )

    projection = projected["hakodate_evidence_projection"]
    assert projected["rows"] == [["4/26", "朝", "献立A", ""]]
    assert projection["metrics"]["applied_count"] == 0
    assert projection["metrics"]["skipped_count"] == 0
    assert projection["metrics"]["ignored_count"] == 1
    assert projection["ignored"][0]["sheet_cell"] == "E12"
    assert "hakodate_sheet_projection_incomplete" not in projected["blockers"]
