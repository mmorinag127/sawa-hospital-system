from src.services.hakodate_ocr_evidence_service import (
    assign_evidence_to_target_cells,
    evidence_from_records,
    normalize_ocr_value,
    sheet_output_from_assigned_results,
)


def test_full_page_evidence_assigns_without_cell_crop_requirement() -> None:
    evidence = evidence_from_records(
        [{"text": "１２", "center": [15, 15], "confidence": 0.9}],
        run_id="run-1",
        engine="test_full_page_engine",
        source_scope="full_page",
    )
    targets = [
        {
            "target_cell_id": "qty-E11",
            "sheet_cell": "E11",
            "worksheet_row": 11,
            "worksheet_col": 5,
            "semantic_field": "qty.regular",
            "bbox": [10, 10, 30, 30],
        }
    ]

    result = assign_evidence_to_target_cells(evidence_records=evidence, target_cells=targets)

    assert result["blockers"] == []
    assert result["unassigned_evidence"] == []
    assert result["assignments"][0]["assigned_value"] == "12"
    assert result["assignments"][0]["assignment_state"] == "assigned"
    assert evidence[0]["source_scope"] == "full_page"


def test_evidence_outside_target_cells_cannot_create_sheet_cell() -> None:
    evidence = evidence_from_records(
        [{"text": "7", "center": [100, 100]}],
        run_id="run-1",
        engine="test_engine",
        source_scope="table_area",
    )
    targets = [
        {
            "target_cell_id": "qty-E11",
            "sheet_cell": "E11",
            "worksheet_row": 11,
            "worksheet_col": 5,
            "semantic_field": "qty.regular",
            "bbox": [10, 10, 30, 30],
        }
    ]

    result = assign_evidence_to_target_cells(evidence_records=evidence, target_cells=targets)

    assert result["assignments"][0]["assignment_state"] == "blank"
    assert result["assignments"][0]["sheet_cell"] == "E11"
    assert result["unassigned_evidence"][0]["unassigned_reason"] == "outside_target_cells"
    assert result["summary"]["target_cell_count"] == 1


def test_conflicting_evidence_blocks_assignment() -> None:
    evidence = evidence_from_records(
        [
            {"text": "1", "center": [15, 15]},
            {"text": "2", "center": [16, 16]},
        ],
        run_id="run-1",
        engine="test_engine",
        source_scope="column_band",
    )
    targets = [
        {
            "target_cell_id": "qty-E11",
            "sheet_cell": "E11",
            "worksheet_row": 11,
            "worksheet_col": 5,
            "semantic_field": "qty.regular",
            "bbox": [10, 10, 30, 30],
        }
    ]

    result = assign_evidence_to_target_cells(evidence_records=evidence, target_cells=targets)

    assert result["assignments"][0]["assignment_state"] == "conflict"
    assert result["assignments"][0]["assigned_value"] == ""
    assert result["blockers"] == ["conflicting evidence for target: qty-E11"]


def test_missing_center_is_unassigned() -> None:
    evidence = evidence_from_records(
        [{"text": "5"}],
        run_id="run-1",
        engine="test_engine",
        source_scope="existing_payload",
    )
    targets = [
        {
            "target_cell_id": "qty-E11",
            "sheet_cell": "E11",
            "worksheet_row": 11,
            "worksheet_col": 5,
            "semantic_field": "qty.regular",
            "bbox": [10, 10, 30, 30],
        }
    ]

    result = assign_evidence_to_target_cells(evidence_records=evidence, target_cells=targets)

    assert result["unassigned_evidence"][0]["unassigned_reason"] == "missing_center"
    assert result["assignments"][0]["assignment_state"] == "blank"


def test_normalize_ocr_value_keeps_numeric_content_only() -> None:
    assert normalize_ocr_value("  数量：１２．０個 ") == "12.0"


def test_sheet_output_uses_assigned_evidence_not_legacy_ocr_rows() -> None:
    evidence = evidence_from_records(
        [{"text": "8", "center": [15, 15], "confidence": 0.8}],
        run_id="run-1",
        engine="new_engine",
        source_scope="full_page",
    )
    assignment_result = assign_evidence_to_target_cells(
        evidence_records=evidence,
        target_cells=[
            {
                "target_cell_id": "qty-E11",
                "sheet_cell": "E11",
                "worksheet_row": 11,
                "worksheet_col": 5,
                "semantic_field": "qty.regular",
                "bbox": [10, 10, 30, 30],
            },
            {
                "target_cell_id": "qty-F11",
                "sheet_cell": "F11",
                "worksheet_row": 11,
                "worksheet_col": 6,
                "semantic_field": "qty.soft",
                "bbox": [40, 10, 60, 30],
            },
        ],
    )

    sheet = sheet_output_from_assigned_results(
        assignments=assignment_result["assignments"],
        blockers=assignment_result["blockers"],
        unassigned_evidence=assignment_result["unassigned_evidence"],
    )

    assert sheet["blockers"] == []
    assert sheet["columns"] == ["E", "F"]
    assert sheet["cells"]["E11"]["value_text"] == "8"
    assert sheet["cells"]["E11"]["assignment_state"] == "assigned"
    assert sheet["cells"]["F11"]["value_text"] == ""
    assert sheet["cells"]["F11"]["assignment_state"] == "blank"
    assert sheet["rows"][0]["values_by_column"] == {"E": "8", "F": ""}


def test_sheet_output_blocks_unassigned_evidence_and_conflicts() -> None:
    evidence = evidence_from_records(
        [
            {"text": "1", "center": [15, 15]},
            {"text": "2", "center": [16, 16]},
            {"text": "9", "center": [90, 90]},
        ],
        run_id="run-1",
        engine="new_engine",
        source_scope="table_area",
    )
    assignment_result = assign_evidence_to_target_cells(
        evidence_records=evidence,
        target_cells=[
            {
                "target_cell_id": "qty-E11",
                "sheet_cell": "E11",
                "worksheet_row": 11,
                "worksheet_col": 5,
                "semantic_field": "qty.regular",
                "bbox": [10, 10, 30, 30],
            }
        ],
    )

    sheet = sheet_output_from_assigned_results(
        assignments=assignment_result["assignments"],
        blockers=assignment_result["blockers"],
        unassigned_evidence=assignment_result["unassigned_evidence"],
    )

    assert "conflicting evidence for target: qty-E11" in sheet["blockers"]
    assert "conflicting assignment cannot enter sheet: E11" in sheet["blockers"]
    assert "unassigned OCR evidence exists: 1" in sheet["blockers"]
    assert sheet["cells"]["E11"]["assignment_state"] == "conflict"
    assert sheet["summary"]["blocker_count"] == 3
