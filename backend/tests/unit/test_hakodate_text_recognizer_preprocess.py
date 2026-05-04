from __future__ import annotations

import sys
import types

import numpy as np

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
