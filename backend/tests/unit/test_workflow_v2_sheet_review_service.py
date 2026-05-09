from src.services import workflow_v2_sheet_review_service


def _sheet(rows):
    return {
        "fields": ["date", "daypart", "menu", "qty.regular", "qty.soft"],
        "header": ["日付", "区分", "献立", "常食", "軟菜"],
        "rows": rows,
        "ocr_numeric_cell_items": [
            {
                "target_row_index": 0,
                "target_col_index": 3,
                "value": "10",
                "classification": "accepted",
                "confidence_tier": "high",
            },
            {
                "target_row_index": 1,
                "target_col_index": 3,
                "value": "12",
                "classification": "accepted",
                "confidence_tier": "high",
            },
            {
                "target_row_index": 2,
                "target_col_index": 3,
                "value": "10",
                "classification": "accepted",
                "confidence_tier": "high",
            },
        ],
    }


def _hakodate_evidence_payload(*, value: str = "10", field: str = "qty.regular", menu: str = "A"):
    return {
        "hakodate_preprocessing": {
            "target_cell_map": [
                {
                    "target_cell_id": "target-1",
                    "sheet_cell": "D11",
                    "worksheet_row": 11,
                    "worksheet_col": 4,
                    "semantic_field": field,
                    "bbox": [0, 0, 10, 10],
                    "center": [5, 5],
                    "logical_targets": [
                        {
                            "field": field,
                            "date": "04/28",
                            "daypart": "朝",
                            "menu_name": menu,
                        }
                    ],
                }
            ]
        },
        "hakodate_ocr_evidence": {
            "records": [
                {
                    "evidence_id": "ev-1",
                    "raw_text": value,
                    "normalized_value": value,
                    "confidence": 0.96,
                    "source_scope": "hakodate_cell_crop_batch",
                    "engine": "hakodate_cell_crop_ocr",
                    "source_bbox": [2, 2, 8, 8],
                    "center": [5, 5],
                }
            ]
        },
    }


def test_auto_edit_proposes_ocr_mismatch_and_correction_alternatives() -> None:
    sheet = _sheet(
        [
            ["04/28", "朝", "A", "110", "5"],
            ["04/28", "朝", "B", "12", "4/5"],
            ["04/28", "朝", "C", "", "6"],
        ]
    )
    sheet["ocr_numeric_cell_items"].append(
        {
            "target_row_index": 1,
            "target_col_index": 4,
            "value": "5",
            "classification": "deterministic_candidate",
            "confidence_tier": "medium",
        }
    )

    result = workflow_v2_sheet_review_service.propose_auto_sheet_edits(
        sheet=sheet,
        use_llm=False,
    )

    patches = result["patches"]
    assert any(
        patch["row_index"] == 0
        and patch["col_index"] == 3
        and patch["suggested_value"] == "10"
        and patch["reason"] == "sheet_value_differs_from_ocr"
        for patch in patches
    )
    slash_patch = next(patch for patch in patches if patch["row_index"] == 1 and patch["col_index"] == 4)
    assert slash_patch["suggested_value"] == "5"
    assert "4" in slash_patch["alternatives"]
    assert "5" in slash_patch["alternatives"]


def test_auto_edit_uses_hakodate_evidence_payload_when_sheet_has_no_embedded_items() -> None:
    sheet = _sheet(
        [
            ["04/28", "朝", "A", "110", "5"],
            ["04/28", "朝", "B", "12", "6"],
            ["04/29", "朝", "C", "11", "7"],
        ]
    )
    sheet["ocr_numeric_cell_items"] = []

    result = workflow_v2_sheet_review_service.propose_auto_sheet_edits(
        sheet=sheet,
        evidence_payload=_hakodate_evidence_payload(value="10"),
        use_llm=False,
    )

    assert any(
        patch["row_index"] == 0
        and patch["col_index"] == 3
        and patch["suggested_value"] == "10"
        and patch["reason"] == "sheet_value_differs_from_ocr"
        for patch in result["patches"]
    )


def test_anomaly_report_flags_outlier_and_ocr_difference() -> None:
    sheet = _sheet(
        [
            ["04/28", "朝", "A", "110", "5"],
            ["04/28", "朝", "B", "12", "6"],
            ["04/28", "朝", "C", "10", "7"],
            ["04/29", "朝", "D", "11", "6"],
        ]
    )

    result = workflow_v2_sheet_review_service.build_sheet_anomaly_report(
        sheet=sheet,
        use_llm=False,
    )

    warnings = result["warnings"]
    assert any(item["type"] == "high_outlier" and item["row_index"] == 0 for item in warnings)
    assert any(item["type"] == "sheet_differs_from_ocr" and item["row_index"] == 0 for item in warnings)
    assert result["summary"]["warning_count"] >= 2


def test_anomaly_report_uses_evidence_payload_and_day_menu_totals() -> None:
    sheet = _sheet(
        [
            ["04/28", "朝", "A", "110", "5"],
            ["04/28", "昼", "B", "10", "4"],
            ["04/29", "昼", "C", "12", "4"],
            ["04/30", "昼", "D", "11", "5"],
        ]
    )
    sheet["ocr_numeric_cell_items"] = []

    result = workflow_v2_sheet_review_service.build_sheet_anomaly_report(
        sheet=sheet,
        evidence_payload=_hakodate_evidence_payload(value="10"),
        use_llm=False,
    )

    warnings = result["warnings"]
    assert any(item["type"] == "sheet_differs_from_ocr" and item["row_index"] == 0 for item in warnings)
    assert any(item["type"] == "same_day_total_outlier" for item in warnings)
    assert any(item["type"] == "other_day_count_outlier" and item["row_index"] == 0 for item in warnings)
    context = result["computed_context"]
    assert context["day_totals"]
    assert context["same_menu_totals"]
    comparison = result["ocr_sheet_comparison"]
    assert comparison["summary"]["mismatch_count"] >= 1
    assert any(
        item["row_index"] == 0
        and item["col_index"] == 3
        and item["sheet_value"] == "110"
        and item["ocr_values"] == ["10"]
        and item["status"] == "mismatch"
        for item in comparison["items"]
    )


def test_workflow_llm_json_payload_uses_first_json_object_with_trailing_text() -> None:
    payload = workflow_v2_sheet_review_service._extract_workflow_llm_json_payload(  # noqa: SLF001
        '{"patches": [{"row_index": 0}]}{"ignored": true}'
    )

    assert payload == {"patches": [{"row_index": 0}]}


def test_workflow_llm_json_payload_wraps_top_level_array_for_auto_edit() -> None:
    payload = workflow_v2_sheet_review_service._extract_workflow_llm_json_payload(  # noqa: SLF001
        '```json\n[{"row_index": 0, "col_index": 3, "suggested_value": "10"}]\n```',
        array_key="patches",
    )

    assert payload == {"patches": [{"row_index": 0, "col_index": 3, "suggested_value": "10"}]}


def test_workflow_llm_json_payload_accepts_preface_and_trailing_note() -> None:
    payload = workflow_v2_sheet_review_service._extract_workflow_llm_json_payload(  # noqa: SLF001
        'result:\n{"warnings": [{"type": "high_outlier"}]}\n確認しました。',
        array_key="warnings",
    )

    assert payload == {"warnings": [{"type": "high_outlier"}]}
