import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))
sys.path.append(str(ROOT.parent / "ocr_pipeline"))

import app.postprocess as postprocess  # noqa: E402


def _digit_cell() -> np.ndarray:
    image = np.full((24, 24, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (8, 4), (15, 19), (0, 0, 0), thickness=-1)
    return image


def _wide_digit_cell() -> np.ndarray:
    image = np.full((48, 200, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (92, 14), (104, 34), (0, 0, 0), thickness=-1)
    return image


def _rois() -> dict:
    cell = _digit_cell()
    return {
        "qty_cells": [cell],
        "qty_cells_alt": [cell.copy()],
        "qty_schema": {
            "rows": 1,
            "cols": 1,
            "row_names": ["r0"],
            "col_names": ["c0"],
        },
    }


def _template(**post_overrides) -> dict:
    post = {
        "qty_regex": r"^\d{0,2}$",
        "qty_high_confidence": 0.67,
        "qty_min_confidence": 0.58,
        "retry": {
            "max_attempts": 2,
            "crop_inset_px": [1, 1, 1, 1],
            "alt_binarize": True,
        },
    }
    post.update(post_overrides)
    return {"id": "tpl-test", "postprocess": post}


def _wide_rois() -> dict:
    cell = _wide_digit_cell()
    return {
        "qty_cells": [cell],
        "qty_cells_alt": [cell.copy()],
        "qty_schema": {
            "rows": 1,
            "cols": 1,
            "row_names": ["r0"],
            "col_names": ["c0"],
        },
    }


def _blank_rois() -> dict:
    cell = np.full((48, 200, 3), 255, dtype=np.uint8)
    return {
        "qty_cells": [cell],
        "qty_cells_alt": [cell.copy()],
        "qty_schema": {
            "rows": 1,
            "cols": 1,
            "row_names": ["r0"],
            "col_names": ["c0"],
        },
    }


def _multiline_rois() -> dict:
    cell = np.full((80, 40, 3), 255, dtype=np.uint8)
    cv2.rectangle(cell, (10, 6), (22, 18), (0, 0, 0), thickness=-1)
    cv2.rectangle(cell, (10, 30), (22, 42), (0, 0, 0), thickness=-1)
    cv2.rectangle(cell, (10, 54), (22, 66), (0, 0, 0), thickness=-1)
    return {
        "qty_cells": [cell],
        "qty_cells_alt": [cell.copy()],
        "qty_schema": {
            "rows": 1,
            "cols": 1,
            "row_names": ["r0"],
            "col_names": ["c0"],
        },
    }


def _row_aligned_rois(rows: int = 4) -> dict:
    cell = _digit_cell()
    return {
        "qty_cells": [cell.copy() for _ in range(rows)],
        "qty_cells_alt": [cell.copy() for _ in range(rows)],
        "qty_schema": {
            "rows": rows,
            "cols": 1,
            "row_names": [f"r{i}" for i in range(rows)],
            "col_names": ["c0"],
        },
        "menu_band": np.full((64, 64, 3), 255, dtype=np.uint8),
    }


def test_postprocess_and_retry_continues_after_blank_raw_variant():
    responses = iter(["", "4"])

    def _ocr_fn(_image, _prompt: str, _max_tokens: int) -> str:
        return next(responses, "")

    result = postprocess.postprocess_and_retry(
        rois=_rois(),
        tpl_cfg=_template(),
        ocr_fn=_ocr_fn,
    )

    assert result["qty"]["r0"]["c0"] == 4
    assert result["failed_cells"] == []
    assert result["qty_cell_diagnostics"][0]["route"] == "high_conf_single"


def test_postprocess_and_retry_does_not_use_removed_qty_fallback():
    responses = iter([""])

    def _ocr_fn(_image, _prompt: str, _max_tokens: int) -> str:
        return next(responses, "")

    result = postprocess.postprocess_and_retry(
        rois=_rois(),
        tpl_cfg=_template(),
        ocr_fn=_ocr_fn,
    )

    assert result["qty"]["r0"]["c0"] is None
    assert "tesseract_qty_calls" not in result["metrics"]
    assert result["qty_cell_diagnostics"][0]["route"] == "reject_no_candidate"


def test_postprocess_and_retry_rejects_conflicting_single_vote_candidates():
    responses = iter(["4", "7"])

    def _ocr_fn(_image, _prompt: str, _max_tokens: int) -> str:
        return next(responses, "")

    result = postprocess.postprocess_and_retry(
        rois=_rois(),
        tpl_cfg=_template(),
        ocr_fn=_ocr_fn,
    )

    assert result["qty"]["r0"]["c0"] is None
    assert result["qty_cell_diagnostics"][0]["route"] == "reject_low_confidence"
    assert result["failed_cells"][0]["reason"] == "low_confidence"


def test_postprocess_and_retry_rejects_noisy_single_candidate():
    responses = iter(["4?"])

    def _ocr_fn(_image, _prompt: str, _max_tokens: int) -> str:
        return next(responses, "")

    result = postprocess.postprocess_and_retry(
        rois=_rois(),
        tpl_cfg=_template(retry={"max_attempts": 1}),
        ocr_fn=_ocr_fn,
    )

    assert result["qty"]["r0"]["c0"] is None
    assert result["qty_cell_diagnostics"][0]["route"] == "reject_low_confidence"


def test_postprocess_and_retry_tight_crops_wide_qty_cells():
    responses = iter(["", "4"])
    seen_shapes = []

    def _ocr_fn(image, _prompt: str, _max_tokens: int) -> str:
        seen_shapes.append(tuple(int(dim) for dim in image.shape[:2]))
        return next(responses, "")

    result = postprocess.postprocess_and_retry(
        rois=_wide_rois(),
        tpl_cfg=_template(),
        ocr_fn=_ocr_fn,
    )

    assert result["qty"]["r0"]["c0"] == 4
    assert seen_shapes[0] == (48, 200)
    assert seen_shapes[1][1] < seen_shapes[0][1]
    assert seen_shapes[1][0] >= seen_shapes[0][0]


def test_postprocess_and_retry_skips_removed_fallback_for_blank_cells():
    def _ocr_fn(_image, _prompt: str, _max_tokens: int) -> str:
        return ""

    result = postprocess.postprocess_and_retry(
        rois=_blank_rois(),
        tpl_cfg=_template(),
        ocr_fn=_ocr_fn,
    )

    assert result["qty"]["r0"]["c0"] is None
    assert "tesseract_qty_calls" not in result["metrics"]


def test_postprocess_and_retry_rejects_qty_above_sanity_limit():
    responses = iter(["66", "66"])

    def _ocr_fn(_image, _prompt: str, _max_tokens: int) -> str:
        return next(responses, "")

    result = postprocess.postprocess_and_retry(
        rois=_rois(),
        tpl_cfg=_template(qty_max_value=50),
        ocr_fn=_ocr_fn,
    )

    assert result["qty"]["r0"]["c0"] is None
    assert result["metrics"]["sanity_rejected_qty_cells"] == 1
    assert result["qty_cell_diagnostics"][0]["route"] == "reject_sanity_fail"
    assert result["qty_cell_diagnostics"][0]["max_allowed"] == 50
    assert result["failed_cells"][0]["reason"] == "sanity_fail"


def test_postprocess_and_retry_can_disable_qty_cell_ocr():
    calls = {"count": 0}

    def _ocr_fn(_image, _prompt: str, _max_tokens: int) -> str:
        calls["count"] += 1
        return "4"

    result = postprocess.postprocess_and_retry(
        rois=_rois(),
        tpl_cfg=_template(qty_strategy="disabled"),
        ocr_fn=_ocr_fn,
    )

    assert result["qty"] == {}
    assert result["qty_cell_diagnostics"] == []
    assert result["failed_cells"] == []
    assert result["disable_overlay_rows"] is True
    assert result["metrics"]["qty_strategy"] == "disabled"
    assert result["metrics"]["accepted_qty_cells"] == 0
    assert result["metrics"]["rejected_qty_cells"] == 0
    assert calls["count"] == 0


def test_postprocess_and_retry_rejects_multiline_qty_cells_before_ocr():
    calls = {"count": 0}

    def _ocr_fn(_image, _prompt: str, _max_tokens: int) -> str:
        calls["count"] += 1
        return "4"

    result = postprocess.postprocess_and_retry(
        rois=_multiline_rois(),
        tpl_cfg=_template(qty_reject_multiline_bands=2),
        ocr_fn=_ocr_fn,
    )

    assert result["qty"]["r0"]["c0"] is None
    assert calls["count"] == 0
    assert result["failed_cells"][0]["reason"] == "multi_line_cell"
    assert result["qty_cell_diagnostics"][0]["route"] == "reject_multiline"


def test_postprocess_and_retry_disables_overlay_when_menu_lines_do_not_match_rows():
    def _ocr_fn(_image, _prompt: str, max_tokens: int) -> str:
        return "4" if max_tokens <= 32 else "line1"

    result = postprocess.postprocess_and_retry(
        rois=_row_aligned_rois(rows=4),
        tpl_cfg=_template(
            qty_min_menu_line_ratio_for_overlay=1.0,
            qty_min_menu_line_rows_for_overlay=1,
        ),
        ocr_fn=_ocr_fn,
    )

    assert result["disable_overlay_rows"] is True
    assert result["metrics"]["overlay_disabled_reason"] == "menu_band_row_mismatch"
