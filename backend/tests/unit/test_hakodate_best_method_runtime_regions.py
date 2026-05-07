from __future__ import annotations

import sys
import types

topk_stub = types.ModuleType("src.services.yomitoku_text_recognizer_topk")
topk_stub.YomitokuTextRecognizerTopKWrapper = object
sys.modules.setdefault("src.services.yomitoku_text_recognizer_topk", topk_stub)

from src.hakodate_best_method_runtime.render_best_method_overlay_all_facilities import (
    _select_template_owned_eval_regions,
)


def test_best_method_keeps_template_owned_regions_past_draft_rows() -> None:
    regions = [{"worksheet_row": row, "worksheet_col": 4} for row in range(11, 67)]

    selected = _select_template_owned_eval_regions(regions)

    assert selected == regions
    assert selected is not regions
    assert selected[0]["worksheet_row"] == 11
    assert selected[-1]["worksheet_row"] == 66
