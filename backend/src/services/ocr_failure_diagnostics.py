"""Describe rejected recognition without accepting or changing its quantities."""

RECOGNITION_REJECTIONS = frozenset({"evidence_unusable", "hakodate_ocr_quantity_unresolved", "first_pass_output_rejected"})


def recognition_failure_details(payload: dict, *, evidence_run_id: str) -> dict:
    review = payload.get("hakodate_recognition_review")
    if not isinstance(review, dict) or not isinstance(review.get("cells"), list) or not review["cells"]:
        return {}
    cells = review["cells"]
    if review.get("count") != len(cells) or any(
        not isinstance(cell, dict) or not all(isinstance(cell.get(key), str) and cell[key]
                                             for key in ("sheet_cell", "reason"))
        or not isinstance(cell.get("raw_text"), str)
        for cell in cells
    ):
        return {}
    return {
        "error_user_message": f"文字認識は完了しましたが、{len(cells)}セルの数量を確定できないため、自動採用を停止しました。",
        "recovery_action": "review_recognition_cells",
        "recognition_review": {"count": len(cells), "cells": [dict(cell) for cell in cells]},
        "evidence_run_id": evidence_run_id,
    }
