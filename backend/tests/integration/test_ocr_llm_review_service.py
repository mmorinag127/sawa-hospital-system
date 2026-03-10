from src.services import ocr_llm_review_service


def test_build_llm_review_prompt_rows_maps_fields_and_row_ids():
    rows = ocr_llm_review_service.build_llm_review_prompt_rows(
        fields=["date", "qty.regular_x", "note"],
        rows=[["2/15", "12", ""], ["2/16"]],
        row_ids=["row-a"],
    )

    assert rows == [
        {"row_id": "row-a", "date": "2/15", "qty.regular_x": "12", "note": ""},
        {"row_id": "row-2", "date": "2/16", "qty.regular_x": "", "note": ""},
    ]


def test_build_llm_review_payload_rows_omits_blank_rows():
    rows = ocr_llm_review_service.build_llm_review_payload_rows(
        fields=["date", "qty.regular_x", "note"],
        rows=[["2/15", "12", ""], ["", "", ""], ["2/16", "", "memo"]],
    )

    assert rows == [
        {"date": "2/15", "qty.regular_x": "12", "note": ""},
        {"date": "2/16", "qty.regular_x": "", "note": "memo"},
    ]


def test_build_llm_review_response_schema_requires_baseline_fields():
    schema = ocr_llm_review_service.build_llm_review_response_schema(["date", "qty.regular_x"])

    row_schema = schema["properties"]["rows"]["items"]
    assert row_schema["required"] == ["date", "qty.regular_x"]
    assert "llm_review" in schema["required"]


def test_build_llm_review_prompts_include_baseline_and_structured_context():
    system_prompt, user_prompt = ocr_llm_review_service.build_llm_review_prompts(
        provider="gemini",
        template={
            "gemini_ocr_prompt": "SYSTEM_BASE",
            "gemini_ocr_user_prompt": "USER_BASE",
        },
        baseline={
            "fields": ["date", "qty.regular_x"],
            "rows": [["2/15", "12"]],
            "row_ids": ["row-a"],
            "baseline_revision_id": "OCRREVBASE1",
            "baseline_source": "edited",
            "raw_output": {"table_raw": "| 日付 | 常食 |\n| --- | --- |\n| 2/15 | 12 |"},
        },
        pdf_variant_requested="corrected",
        pdf_variant_used="raw",
        pdf_variant_fallback_reason="corrected_pdf_missing",
        truncate_assist_text=lambda text, max_chars: text[:max_chars],
        compact_prompt_tables=lambda raw_output: [{"table_id": "p1_t1", "rows": [["2/15", "12"]]}],
        compact_prompt_cell_issues=lambda raw_output, template: [{"issue_code": "merged_numeric_cell"}],
    )

    assert "SYSTEM_BASE" in system_prompt
    assert "yomitoku-compatible JSON" in system_prompt
    assert "Attached fax variant requested: corrected" in user_prompt
    assert "Attached fax variant used: raw" in user_prompt
    assert "Attached fax fallback reason: corrected_pdf_missing" in user_prompt
    assert "Current baseline revision_id: OCRREVBASE1" in user_prompt
    assert "Current baseline source: edited" in user_prompt
    assert "Previous yomitoku/LLM structured tables/cells" in user_prompt
    assert "Existing suspicious cells from yomitoku/LLM" in user_prompt
