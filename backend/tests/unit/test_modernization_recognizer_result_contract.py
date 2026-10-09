"""Recording protocol doubles: component contracts, not native OCR acceptance."""

from copy import deepcopy

import numpy as np
from PIL import Image
import pytest
import torch

from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as direct
from src.services.yomitoku_text_recognizer_topk import (
    YomitokuTextRecognizerTopKWrapper,
    validate_recognizer_result,
)


POINTS = [[[x, 0], [x + 10, 0], [x + 10, 10], [x, 10]] for x in (0, 20, 40)]
TEXTS = ["", "0", "7"]
SCORES = [0.0, 0.8, 0.9]
REGIONS = [{"region_id": f"E{11 + i}", "sheet_cell": f"E{11 + i}",
            "field": "qty.regular_x", "source_note": f"owned-input-{i}"} for i in range(3)]


def _response():
    return {
        "contents": TEXTS.copy(), "scores": SCORES.copy(),
        "directions": ["horizontal", "vertical", "horizontal"],
        "candidates": [[], [{"text": "0", "score": 0.8, "token_ids": [1],
                              "token_probs": [0.8], "log_score": -0.223}],
                       [{"text": "7", "score": 0.9, "transport_note": "preserve"}]],
        "points": deepcopy(POINTS),
    }


def _direct_call(monkeypatch, response, *, regions=None, polygons=None):
    calls = []

    class RecordingTransport:
        def __init__(self, recognizer, **kwargs):
            calls.append(("init", recognizer, kwargs))

        def recognize(self, image_bgr, *, points):
            calls.append(("recognize", image_bgr.copy(), deepcopy(points)))
            return response

    # Only the transport response is substituted; the shared validator and
    # direct consumer's metadata/quantity processing remain actual production.
    monkeypatch.setattr(direct, "YomitokuTextRecognizerTopKWrapper", RecordingTransport)
    output = direct.run_text_recognizer_direct(
        recognizer="recording-component-transport", contact_sheet=Image.new("RGB", (60, 20), "white"),
        regions=deepcopy(REGIONS) if regions is None else regions,
        polygons=deepcopy(POINTS) if polygons is None else polygons,
        score_threshold=0.45, digit_score_threshold=0.05,
        candidate_digit_score_threshold=0.05, enable_context_repair=False,
    )
    return output, calls


def _check_boundary(boundary, monkeypatch, response):
    if boundary == "validator":
        validate_recognizer_result(response, expected_points=POINTS)
    else:
        _direct_call(monkeypatch, response)


@pytest.mark.parametrize("boundary", ["validator", "direct"])
@pytest.mark.parametrize("response", [None, [], "missing-response"])
def test_missing_or_malformed_response_stops(boundary, response, monkeypatch):
    with pytest.raises(ValueError, match="^recognizer_result_invalid$"):
        _check_boundary(boundary, monkeypatch, response)


@pytest.mark.parametrize("boundary", ["validator", "direct"])
@pytest.mark.parametrize("field", ["contents", "scores", "directions", "candidates"])
@pytest.mark.parametrize("change", ["missing", "none", "short", "extra", "scalar"])
def test_required_array_contract_stops_before_blank_substitution(boundary, field, change, monkeypatch):
    response = _response()
    if change == "missing":
        response.pop(field)
    elif change == "none":
        response[field] = None
    elif change == "short":
        response[field].pop()
    elif change == "extra":
        response[field].append(deepcopy(response[field][0]))
    else:
        response[field] = np.array(0)
    with pytest.raises(ValueError, match=f"^recognizer_result_cardinality_invalid: {field}$"):
        _check_boundary(boundary, monkeypatch, response)


@pytest.mark.parametrize("boundary", ["validator", "direct"])
@pytest.mark.parametrize("field,value,error", [
    ("contents", None, "recognizer_result_entry_invalid"),
    ("contents", 0, "recognizer_result_entry_invalid"),
    ("scores", None, "recognizer_result_entry_invalid"),
    ("scores", "0.8", "recognizer_result_entry_invalid"),
    ("scores", True, "recognizer_result_entry_invalid"),
    ("scores", float("nan"), "recognizer_result_entry_invalid"),
    ("scores", float("inf"), "recognizer_result_entry_invalid"),
    ("directions", None, "recognizer_result_entry_invalid"),
    ("candidates", None, "recognizer_result_entry_invalid"),
    ("candidates", [None], "recognizer_result_candidate_invalid"),
    ("candidates", [{"score": 0.8}], "recognizer_result_candidate_invalid"),
    ("candidates", [{"text": "7"}], "recognizer_result_candidate_invalid"),
    ("candidates", [{"text": "7", "score": float("nan")}], "recognizer_result_candidate_invalid"),
])
def test_malformed_entries_are_not_coerced_to_blank_or_zero(boundary, field, value, error, monkeypatch):
    response = _response()
    response[field][0] = value
    with pytest.raises(ValueError, match=f"^{error}$"):
        _check_boundary(boundary, monkeypatch, response)


@pytest.mark.parametrize("boundary", ["validator", "direct"])
@pytest.mark.parametrize("change", ["missing", "short", "extra", "reordered", "coordinate", "nonfinite"])
def test_point_identity_and_order_are_not_guessed(boundary, change, monkeypatch):
    response = _response()
    if change == "missing":
        response.pop("points")
    elif change == "short":
        response["points"].pop()
    elif change == "extra":
        response["points"].append(deepcopy(POINTS[0]))
    elif change == "reordered":
        response["points"].reverse()
    else:
        response["points"][0][0][0] = float("nan") if change == "nonfinite" else 1
    with pytest.raises(ValueError, match="^recognizer_result_points_changed_or_invalid$"):
        _check_boundary(boundary, monkeypatch, response)


@pytest.mark.parametrize("region_count,polygon_count", [(1, 0), (0, 1), (2, 1), (1, 2)])
def test_input_count_mismatch_stops_before_transport(monkeypatch, region_count, polygon_count):
    class ForbiddenTransport:
        def __init__(self, *args, **kwargs):
            raise AssertionError("input mismatch reached model transport")

    monkeypatch.setattr(direct, "YomitokuTextRecognizerTopKWrapper", ForbiddenTransport)
    with pytest.raises(ValueError, match="^recognizer_input_cardinality_invalid$"):
        direct.run_text_recognizer_direct(
            recognizer=None, contact_sheet=Image.new("RGB", (60, 20)),
            regions=deepcopy(REGIONS[:region_count]), polygons=deepcopy(POINTS[:polygon_count]),
            score_threshold=0.45, digit_score_threshold=0.05,
            candidate_digit_score_threshold=0.05, enable_context_repair=False,
        )


def test_explicit_blank_zero_seven_preserve_exact_metadata(monkeypatch):
    response = _response()
    original = deepcopy(response)
    validate_recognizer_result(response, expected_points=POINTS)
    (assigned, metrics), calls = _direct_call(monkeypatch, response)
    assert response == original
    assert len(calls) == 2
    assert calls[1][0] == "recognize"
    assert calls[1][2] == POINTS
    assert calls[1][1].shape == (20, 60, 3)
    assert [item["recognizer_raw_text"] for item in assigned] == TEXTS
    assert [item["recognizer_score"] for item in assigned] == SCORES
    assert [item["recognizer_direction"] for item in assigned] == response["directions"]
    assert [item["recognizer_candidates"] for item in assigned] == response["candidates"]
    assert [item["ocr_normalized"] for item in assigned] == TEXTS
    assert [item["recognizer_accepted"] for item in assigned] == [False, True, True]
    assert [item["recognizer_decision_source"] for item in assigned] == ["", "raw_digits", "raw_digits"]
    for source, actual in zip(REGIONS, assigned):
        for key, value in source.items():
            assert actual[key] == value
    assert metrics["raw_prediction_count"] == metrics["candidate_count"] == 3
    assert metrics["context_repair_count"] == 0


class RecordingRecognizer:
    class Tokenizer:
        eos_id = 0
        _itos = ("[E]", "0", "7")

    tokenizer = Tokenizer()

    def __init__(self, *, preprocess_arity=2, point_change=None, batch_sizes=(2, 1),
                 inference_delta=0, result_change=None):
        self.preprocess_arity = preprocess_arity
        self.point_change = point_change
        self.batch_sizes = batch_sizes
        self.inference_delta = inference_delta
        self.result_change = result_change
        self.calls = []
        self.offset = 0

    def preprocess(self, image, points):
        self.calls.append(("preprocess", deepcopy(points)))
        if points is None:
            height, width = image.shape[:2]
            returned = [[[0, 0], [width, 0], [width, height], [0, height]]]
        else:
            returned = deepcopy(points)
        if self.point_change == "reordered":
            returned.reverse()
        elif self.point_change == "short":
            returned.pop()
        elif self.point_change == "coordinate":
            returned[0][0][0] += 1
        batches = [torch.zeros((size, 3, 8, 8)) for size in self.batch_sizes]
        result = (batches, returned)
        return (*result, object()) if self.preprocess_arity == 3 else result

    def _run_inference(self, data):
        self.calls.append(("inference", len(data)))
        return torch.tensor([[[0.05, 0.9, 0.05], [0.9, 0.05, 0.05]]]
                            * (len(data) + self.inference_delta), dtype=torch.float32)

    def postprocess(self, probs, points):
        self.calls.append(("postprocess", deepcopy(points)))
        count = len(points)
        result = [TEXTS[self.offset:self.offset + count], SCORES[self.offset:self.offset + count],
                  ["horizontal"] * count]
        self.offset += count
        if self.result_change is not None:
            field, change = self.result_change
            if change == "short":
                result[field].pop()
            elif change == "extra":
                result[field].append(result[field][0])
            elif change == "none":
                result[field] = None
            else:
                result[field][0] = None
        return tuple(result)


@pytest.mark.parametrize("arity", [2, 3])
@pytest.mark.parametrize("automatic_points", [False, True])
def test_actual_wrapper_batches_and_optional_auto_points_protocol(arity, automatic_points):
    expected_points = [[[0, 0], [60, 0], [60, 20], [0, 20]]] if automatic_points else POINTS
    count = len(expected_points)
    recorder = RecordingRecognizer(preprocess_arity=arity, batch_sizes=(1,) if automatic_points else (2, 1))
    result = YomitokuTextRecognizerTopKWrapper(recorder, token_top_k=2, sequence_top_k=2).recognize(
        np.full((20, 60, 3), 255, np.uint8), points=None if automatic_points else deepcopy(POINTS),
    )
    validate_recognizer_result(result, expected_points=expected_points)
    assert result["contents"] == TEXTS[:count]
    assert result["scores"] == SCORES[:count]
    assert result["directions"] == ["horizontal"] * count
    assert result["points"] == expected_points
    assert len(result["candidates"]) == count
    for candidates in result["candidates"]:
        assert candidates[0]["text"] == "0"
        assert candidates[0]["score"] == pytest.approx(0.81)
        assert candidates[0]["token_ids"] == [1, 0]
        assert candidates[0]["token_probs"] == pytest.approx([0.9, 0.9])
    assert [call[1] for call in recorder.calls if call[0] == "inference"] == ([1] if automatic_points else [2, 1])
    assert [call[1] for call in recorder.calls if call[0] == "postprocess"] == (
        [expected_points] if automatic_points else [POINTS[:2], POINTS[2:]])
    assert recorder.calls[0] == ("preprocess", None if automatic_points else POINTS)


@pytest.mark.parametrize("field", [0, 1, 2])
@pytest.mark.parametrize("change", ["short", "extra", "none", "invalid_entry"])
def test_wrapper_rejects_malformed_batch_before_return(field, change):
    recorder = RecordingRecognizer(result_change=(field, change))
    error = "recognizer_result_entry_invalid" if change == "invalid_entry" else (
        "recognizer_result_cardinality_invalid: " + ("contents", "scores", "directions")[field])
    with pytest.raises(ValueError, match=f"^{error}$"):
        YomitokuTextRecognizerTopKWrapper(recorder).recognize(np.zeros((20, 60, 3), np.uint8), points=deepcopy(POINTS))
    assert [call[1] for call in recorder.calls if call[0] == "inference"] == [2]


@pytest.mark.parametrize("change", ["reordered", "short", "coordinate"])
def test_wrapper_rejects_preprocess_identity_change_before_inference(change):
    recorder = RecordingRecognizer(point_change=change)
    with pytest.raises(ValueError, match="^recognizer_result_points_changed_or_invalid$"):
        YomitokuTextRecognizerTopKWrapper(recorder).recognize(np.zeros((20, 60, 3), np.uint8), points=deepcopy(POINTS))
    assert [call[0] for call in recorder.calls] == ["preprocess"]


@pytest.mark.parametrize("batch_sizes,inference_delta", [((2, 1), -1), ((2, 1), 1), ((2,), 0), ((2, 2), 0)])
def test_wrapper_batch_and_final_cardinality_never_pad_results(batch_sizes, inference_delta):
    recorder = RecordingRecognizer(batch_sizes=batch_sizes, inference_delta=inference_delta)
    with pytest.raises(ValueError, match="^recognizer_result_batch_cardinality_invalid$"):
        YomitokuTextRecognizerTopKWrapper(recorder).recognize(np.zeros((20, 60, 3), np.uint8), points=deepcopy(POINTS))


def test_genuine_empty_input_performs_no_inference(monkeypatch):
    recorder = RecordingRecognizer()
    result = YomitokuTextRecognizerTopKWrapper(recorder).recognize(np.zeros((20, 60, 3), np.uint8), points=[])
    assert result == {"contents": [], "scores": [], "directions": [], "candidates": [], "points": []}
    assert recorder.calls == []
    validate_recognizer_result(result, expected_points=[])
    (assigned, metrics), calls = _direct_call(monkeypatch, result, regions=[], polygons=[])
    assert assigned == []
    assert calls == []
    assert metrics["raw_prediction_count"] == metrics["candidate_count"] == 0
