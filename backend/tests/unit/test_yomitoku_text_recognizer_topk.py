from __future__ import annotations

import numpy as np
import torch

from src.services.yomitoku_text_recognizer_topk import YomitokuTextRecognizerTopKWrapper


class _FakeTokenizer:
    eos_id = 0
    bos_id = 5
    pad_id = 6
    _itos = ("[E]", "1", "2", "引", "A", "[B]", "[P]")


class _FakeRecognizer:
    tokenizer = _FakeTokenizer()


class _FakeRecognizerTwoValuePreprocess(_FakeRecognizer):
    def preprocess(self, image_bgr, points):
        return [[0]], points

    def _run_inference(self, data):
        return torch.tensor(
            [
                [
                    [0.01, 0.80, 0.10, 0.05, 0.04, 0.0, 0.0],
                    [0.90, 0.03, 0.03, 0.02, 0.02, 0.0, 0.0],
                ]
            ],
            dtype=torch.float32,
        )

    def postprocess(self, probs, batch_points):
        return ["1"], [0.8], ["horizontal"]


class _FakeModel:
    def __call__(self, data):
        return torch.tensor(
            [
                [
                    [0.01, 0.80, 0.10, 0.05, 0.04, 0.0, 0.0],
                    [0.90, 0.03, 0.03, 0.02, 0.02, 0.0, 0.0],
                ]
            ],
            dtype=torch.float32,
        )


class _FakeRecognizerWithoutRunInference(_FakeRecognizer):
    model = _FakeModel()
    device = "cpu"
    infer_onnx = False

    def postprocess(self, probs, batch_points):
        return ["1"], [0.8], ["horizontal"]


def test_sequence_candidates_keep_non_digit_candidates_and_scores() -> None:
    wrapper = YomitokuTextRecognizerTopKWrapper(
        _FakeRecognizer(),
        token_top_k=3,
        sequence_top_k=4,
        max_decode_steps=3,
    )
    probs = torch.tensor(
        [
            [0.01, 0.20, 0.10, 0.60, 0.09, 0.0, 0.0],
            [0.70, 0.05, 0.15, 0.04, 0.06, 0.0, 0.0],
            [0.90, 0.04, 0.03, 0.02, 0.01, 0.0, 0.0],
        ],
        dtype=torch.float32,
    )

    candidates = wrapper._sequence_candidates(probs)

    assert candidates[0].text == "引"
    assert any(candidate.text == "1" for candidate in candidates)
    assert all(candidate.score > 0 for candidate in candidates)


def test_recognize_accepts_two_value_preprocess_result() -> None:
    wrapper = YomitokuTextRecognizerTopKWrapper(
        _FakeRecognizerTwoValuePreprocess(),
        token_top_k=2,
        sequence_top_k=2,
        max_decode_steps=2,
    )

    result = wrapper.recognize(
        np.zeros((20, 20, 3), dtype=np.uint8),
        points=[[[0, 0], [10, 0], [10, 10], [0, 10]]],
    )

    assert result["contents"] == ["1"]
    assert result["scores"] == [0.8]
    assert result["directions"] == ["horizontal"]
    assert result["candidates"][0][0]["text"] == "1"


def test_run_inference_falls_back_to_model_softmax() -> None:
    wrapper = YomitokuTextRecognizerTopKWrapper(
        _FakeRecognizerWithoutRunInference(),
        token_top_k=2,
        sequence_top_k=2,
        max_decode_steps=2,
    )

    probs = wrapper._run_inference(torch.zeros((1, 3, 8, 8), dtype=torch.float32))

    assert probs.shape == (1, 2, 7)
    assert torch.allclose(probs.sum(dim=-1), torch.ones((1, 2)))
