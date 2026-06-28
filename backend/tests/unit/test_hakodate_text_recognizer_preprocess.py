from __future__ import annotations

import sys
import types

import numpy as np
from PIL import Image, ImageDraw

topk_stub = types.ModuleType("src.services.yomitoku_text_recognizer_topk")
topk_stub.YomitokuTextRecognizerTopKWrapper = object
sys.modules.setdefault("src.services.yomitoku_text_recognizer_topk", topk_stub)

from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial
from src.hakodate_best_method_runtime import run_text_recognizer_trial


def _wide_line_binary() -> np.ndarray:
    binary = np.full((20, 625), 255, dtype=np.uint8)
    binary[4:15, :] = 0
    return binary


def test_corner_noise_foreground_centered_fits_wide_components() -> None:
    image, stats = run_text_recognizer_corner_noise_trial._foreground_centered(
        _wide_line_binary(),
        out_width=122,
        out_height=74,
    )

    assert image.size == (122, 74)
    assert stats["bbox_width"] == 625


def test_trial_foreground_centered_fits_wide_components() -> None:
    image, stats = run_text_recognizer_trial._foreground_centered(
        _wide_line_binary(),
        out_width=122,
        out_height=74,
    )

    assert image.size == (122, 74)
    assert stats["bbox_width"] == 625


def test_corner_zero_shape_never_bypasses_recognizer() -> None:
    image = Image.new("RGB", (120, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((42, 32, 72, 78), outline="black", width=8)
    draw.rectangle((60, 58, 78, 84), fill="black")
    rectified = np.array(image)

    contact_sheet, usable_regions, polygons, skipped_regions = (
        run_text_recognizer_corner_noise_trial.build_recognizer_contact_sheet(
            rectified_fax_bgr=rectified,
            regions=[
                {
                    "region_id": "E11",
                    "bbox": [20, 20, 100, 100],
                    "logical_targets": [{"sheet_cell": "E11"}],
                }
            ],
            line_mask=None,
            mode="corner_cc",
        )
    )

    assert contact_sheet.size[0] > 0
    assert len(usable_regions) == 1
    assert len(polygons) == 1
    assert skipped_regions == []
    assert not usable_regions[0].get("recognizer_fast_path")
    assert "fast_digits" not in usable_regions[0].get("recognizer_ink_stats", {})
