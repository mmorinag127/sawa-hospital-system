from src.services import apply_gate_service


def test_draft_newer_than_lines_allows_apply_but_blocks_confirm() -> None:
    sheet_gate = apply_gate_service.evaluate_sheet_gate(
        rows=[["04/26", "朝", "Menu A", "1"]],
        source="hakodate_ocr_evidence_sheet",
        has_semantic_fields=True,
        blockers=[],
        warnings=[],
        draft_newer_than_lines=True,
    )
    apply_gate = apply_gate_service.evaluate_apply_gate(
        order_payload={"facility": "FAC00001", "week_value": "2026-04@2026-04-26~2026-04-30"},
        evidence_run={"capabilities_json": {"step2_view_ready": True, "step2_edit_ready": True}},
        draft_sheet={
            "source": "hakodate_ocr_evidence_sheet",
            "fields": ["date_mmdd", "daypart", "menu", "qty.regular"],
            "rows": [["04/26", "朝", "Menu A", "1"]],
        },
        candidate_resolution={"resolutions": {}},
        menu_context={},
        sheet_gate=sheet_gate,
    )

    assert apply_gate["can_apply"] is True
    assert apply_gate["can_confirm"] is False
    assert "draft_newer_than_lines" in apply_gate["confirm_blockers"]
    assert "draft_newer_than_lines" in apply_gate["warnings"]
