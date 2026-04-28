from types import SimpleNamespace

from src.services import order_service


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


def test_hakodate_payload_augmentation_ignores_legacy_ocr_payload_sources(monkeypatch) -> None:
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
    monkeypatch.setattr(order_service, "load_bytes_from_uri", lambda uri: b"%PDF-TEST")
    monkeypatch.setattr(
        order_service,
        "detect_table_grid",
        lambda pdf_bytes, template: SimpleNamespace(
            column_edges=[0.0, 0.1, 0.35, 0.55, 0.95],
            row_edges=[0.0, 0.2, 0.45],
            table_box=[0.0, 0.0, 1.0, 1.0],
            confidence=0.92,
        ),
    )
    monkeypatch.setattr(
        order_service.hakodate_assignment_service,
        "_extract_tesseract_tokens",
        lambda pdf_bytes, template: [
            SimpleNamespace(text="１２", x=0.75, y=0.31, bbox=[0.65, 0.25, 0.85, 0.37], confidence=0.8)
        ],
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
    assert evidence_records[0]["source_scope"] == "order_document_full_page"
    assert "99" not in str(target_cells)
    assert "99" not in str(evidence_records)


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
        "cleared_legacy_cell_count": 1,
    }


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


def test_hakodate_evidence_projection_blocks_unmatched_sheet_identity() -> None:
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

    assert projected["rows"] == [["4/26", "朝", "献立A", ""]]
    assert "hakodate_sheet_projection_incomplete" in projected["blockers"]
    assert projected["hakodate_evidence_projection"]["skipped"][0]["skip_reason"] == "row_identity_not_found"
