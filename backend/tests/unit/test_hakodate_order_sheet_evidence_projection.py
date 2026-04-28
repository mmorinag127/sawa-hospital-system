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
        "rows": [["4/26", "朝", "献立A", ""]],
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
    }


def test_hakodate_evidence_projection_blocks_unmatched_sheet_identity() -> None:
    assignment = order_service._build_hakodate_evidence_assignment_from_payload(  # noqa: SLF001
        order_id="ORD_TEST",
        facility_id="FAC_TEST",
        template_id="tpl-1",
        payload={
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
            ],
            "ocr_evidence_records": [{"text": "5", "center": [20, 20]}],
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
