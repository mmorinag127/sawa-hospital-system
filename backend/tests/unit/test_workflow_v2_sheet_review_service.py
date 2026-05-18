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

    assert result["patches"] == []
    assert result["rule_patches"] == []


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

    assert result["patches"] == []
    assert result["rule_patches"] == []


def test_anomaly_report_flags_sheet_only_outlier() -> None:
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
    assert not any(item["type"] == "sheet_differs_from_ocr" for item in warnings)
    assert "ocr_sheet_comparison" not in result
    assert "ocr_sheet_comparison" not in result["summary"]
    assert result["summary"]["warning_count"] >= 1


def test_anomaly_report_ignores_ocr_evidence_and_uses_sheet_totals() -> None:
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
    assert not any(item["type"] == "sheet_differs_from_ocr" for item in warnings)
    assert any(item["type"] == "same_day_total_outlier" for item in warnings)
    assert any(item["type"] == "other_day_count_outlier" and item["row_index"] == 0 for item in warnings)
    context = result["computed_context"]
    assert context["day_totals"]
    assert context["same_menu_totals"]
    assert "ocr_sheet_comparison" not in result


def test_auto_edit_llm_receives_fax_image_and_ocr_context(monkeypatch) -> None:
    captured = {}

    def _fake_gemini_json_request(**kwargs):
        captured.update(kwargs)
        return {
            "patches": [
                {
                    "row_index": 0,
                    "col_index": 3,
                    "current_value": "",
                    "suggested_value": "10",
                    "reason": "visible_on_fax",
                    "confidence": "high",
                }
            ]
        }, {"status": "ok"}

    monkeypatch.setattr(workflow_v2_sheet_review_service, "_gemini_json_request", _fake_gemini_json_request)

    sheet = _sheet([["04/28", "朝", "A", "", "5"]])
    sheet["target_cell_map"] = [
        {"target_row_index": 0, "target_col_index": 3, "field": "qty.regular", "bbox": [0, 0, 10, 10]}
    ]

    result = workflow_v2_sheet_review_service.propose_auto_sheet_edits(
        sheet=sheet,
        evidence_payload=_hakodate_evidence_payload(value="10"),
        use_llm=True,
        fax_image_png_base64="png-base64",
        fax_image_meta={"status": "attached"},
    )

    assert captured["image_png_base64"] == "png-base64"
    assert captured["user_payload"]["ocr_numeric_cell_items"] == []
    assert captured["user_payload"]["target_cell_map"]
    assert result["llm"]["fax_image"]["status"] == "attached"
    assert any(patch["source"] == "llm" and patch["suggested_value"] == "10" for patch in result["patches"])


def test_auto_edit_llm_scales_target_bboxes_to_attached_image_pixels(monkeypatch) -> None:
    captured = {}

    def _fake_gemini_json_request(**kwargs):
        captured.update(kwargs)
        return {"patches": []}, {"status": "ok"}

    sheet = _sheet([["04/28", "朝", "A", "", "5"]])
    sheet["target_cell_map"] = [
        {
            "target_cell_id": "D11",
            "sheet_cell": "D11",
            "target_row_index": 0,
            "target_col_index": 3,
            "field": "qty.regular",
            "bbox": [100, 200, 200, 300],
            "center": [150, 250],
        }
    ]
    png_2x2 = "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFElEQVR4nGNkYPjPwMDAwMDAAAAMAQABxLUxZQAAAABJRU5ErkJggg=="
    monkeypatch.setattr(workflow_v2_sheet_review_service, "_gemini_json_request", _fake_gemini_json_request)

    workflow_v2_sheet_review_service.propose_auto_sheet_edits(
        sheet=sheet,
        use_llm=True,
        fax_image_png_base64=png_2x2,
        fax_image_meta={"status": "attached"},
    )

    target = captured["user_payload"]["target_cell_map"][0]
    transform = captured["user_payload"]["fax_image"]["coordinate_transform"]
    assert transform["status"] == "scaled_to_attached_image"
    assert transform["image_width"] == 2
    assert transform["image_height"] == 2
    assert target["bbox_coordinate_space"] == "attached_image_pixels"
    assert target["bbox"] == [1.0, 1.33, 2.0, 2.0]
    assert target["bbox_original"] == [100, 200, 200, 300]


def test_auto_edit_retries_failed_chunks_as_single_cells(monkeypatch) -> None:
    calls = []

    def _fake_gemini_json_request(**kwargs):
        target_count = len(kwargs["user_payload"]["target_cell_map"])
        calls.append(target_count)
        if target_count > 1:
            return None, {"status": "failed", "error": "not-json"}
        target = kwargs["user_payload"]["target_cell_map"][0]
        return {
            "patches": [
                {
                    "row_index": target["target_row_index"],
                    "col_index": target["target_col_index"],
                    "current_value": "",
                    "suggested_value": "5",
                    "reason": "visible_on_fax",
                    "confidence": "high",
                }
            ]
        }, {"status": "ok"}

    sheet = _sheet([["04/28", "朝", "A", "", ""]])
    sheet["target_cell_map"] = [
        {"target_row_index": 0, "target_col_index": 3, "field": "qty.regular", "bbox": [0, 0, 10, 10]},
        {"target_row_index": 0, "target_col_index": 4, "field": "qty.soft", "bbox": [10, 0, 20, 10]},
    ]
    monkeypatch.setenv("WORKFLOW_V2_AUTO_EDIT_TARGET_CHUNK_SIZE", "2")
    monkeypatch.setattr(workflow_v2_sheet_review_service, "_gemini_json_request", _fake_gemini_json_request)

    result = workflow_v2_sheet_review_service.propose_auto_sheet_edits(
        sheet=sheet,
        use_llm=True,
        fax_image_png_base64="",
        fax_image_meta={"status": "attached"},
    )

    assert calls == [2, 1, 1]
    assert len(result["patches"]) == 2
    assert any(item.get("retry_of_failed_chunk") for item in result["llm"]["chunks"])


def test_anomaly_llm_context_excludes_ocr_comparison_and_evidence(monkeypatch) -> None:
    captured = {}

    def _fake_gemini_json_request(**kwargs):
        captured.update(kwargs)
        return {"warnings": []}, {"status": "ok"}

    monkeypatch.setattr(workflow_v2_sheet_review_service, "_gemini_json_request", _fake_gemini_json_request)

    workflow_v2_sheet_review_service.build_sheet_anomaly_report(
        sheet=_sheet(
            [
                ["04/28", "朝", "A", "110", "5"],
                ["04/29", "朝", "B", "10", "5"],
                ["04/30", "朝", "C", "11", "5"],
            ]
        ),
        evidence_payload=_hakodate_evidence_payload(value="10"),
        use_llm=True,
    )

    payload = captured["user_payload"]
    assert "ocr_sheet_comparison" not in payload
    assert payload["ocr_numeric_cell_items"] == []
    assert payload["target_cell_map"] == []
    assert payload["computed_context"]["quantity_cell_count"] > 0


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
