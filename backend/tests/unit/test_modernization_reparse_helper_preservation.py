from copy import deepcopy

import pytest

from src.services import order_service


def baseline(slot="主", total="12", unknown="old"):
    return {
        "baseline_source": "sheet",
        "fields": ["date_mmdd", "daypart", "aux.col_2", "menu", "aux.col_4", "aux.col_5", "qty.regular_x"],
        "header": ["日付", "区分", "副区分", "メニュー", "合計", "自由表示", "常食"],
        "rows": [["04/26", "昼", slot, "Menu A", total, unknown, "10"]],
        "row_ids": ["row-1"],
    }


@pytest.mark.parametrize("slot,total", [("主", "12"), ("", ""), ("主", ""), ("", "12")])
def test_known_helpers_preserve_nonempty_and_fill_empty(slot, total):
    saved = baseline(slot, total)
    candidate = [["04/26", "昼", "副", "Menu A", "13", "new", "11"]]
    before = deepcopy((saved, candidate))
    merged, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=saved, candidate_rows=candidate)
    assert result["error"] is None
    assert merged == [["04/26", "昼", slot or "副", "Menu A", total or "13", "new", "11"]]
    assert result["detail"]["ignored_shell_drift_count"] == int(bool(slot)) + int(bool(total))
    assert result["detail"]["display_change_count"] == 3 - int(bool(slot)) - int(bool(total))
    assert result["detail"]["quantity_change_count"] == 1
    assert (saved, candidate) == before


def test_selected_fields_only_keep_unknown_aux_behavior():
    saved = baseline()
    candidate = [["", "", "副", "", "13", "new", "11"]]
    before = deepcopy((saved, candidate))
    merged, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=saved, candidate_rows=candidate,
        candidate_returned_row_indexes=[0], candidate_returned_field_map={0: {"aux.col_5"}})
    assert result["error"] is None
    assert merged == [["04/26", "昼", "主", "Menu A", "12", "new", "10"]]
    assert result["detail"]["display_change_count"] == 1
    assert result["detail"]["ignored_shell_drift_count"] == 0
    assert (saved, candidate) == before


def test_unreturned_row_does_not_adopt_helpers_or_quantity():
    saved = baseline("", "")
    saved["rows"].append(["04/26", "夕", "", "Menu B", "", "old", "9"])
    saved["row_ids"].append("row-2")
    candidate = [deepcopy(saved["rows"][0]), ["04/26", "夕", "副", "Menu B", "13", "new", "11"]]
    merged, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=saved, candidate_rows=candidate, candidate_returned_row_indexes=[0])
    assert result["error"] is None
    assert merged == saved["rows"]
    assert result["detail"]["display_change_count"] == 0


def test_candidate_total_conflict_still_stops_before_preservation():
    saved = baseline()
    saved["rows"].append(deepcopy(saved["rows"][0]))
    saved["row_ids"].append("row-2")
    candidate = deepcopy(saved["rows"])
    candidate[0][4], candidate[1][4] = "70", "71"
    merged, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=saved, candidate_rows=candidate)
    assert merged == []
    assert result["error"] == "llm_full_table_helper_column_conflict"
    assert result["detail"]["helper_conflicts"][0]["values"] == ["70", "71"]


def test_structural_drift_still_stops():
    saved = baseline()
    candidate = deepcopy(saved["rows"])
    candidate[0][3] = "Wrong Menu"
    merged, result = order_service._validate_and_merge_reparse_full_table_candidate(
        baseline=saved, candidate_rows=candidate)
    assert merged == []
    assert result["error"] == "llm_full_table_structural_drift"
