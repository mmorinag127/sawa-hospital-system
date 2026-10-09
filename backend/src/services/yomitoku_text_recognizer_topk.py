from __future__ import annotations

import heapq
import math
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from numbers import Real
from typing import Any

import numpy as np
import torch


def _validate_point_identity(points: Any, expected_points: Any) -> None:
    try:
        actual = np.asarray(points)
        expected = np.asarray(expected_points)
        valid = (
            (np.issubdtype(actual.dtype, np.integer) or np.issubdtype(actual.dtype, np.floating))
            and (np.issubdtype(expected.dtype, np.integer) or np.issubdtype(expected.dtype, np.floating))
            and np.isfinite(actual).all()
            and np.isfinite(expected).all()
            and (actual.shape == (0,) or (actual.ndim == 3 and actual.shape[1:] == (4, 2)))
            and np.array_equal(actual, expected)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("recognizer_result_points_changed_or_invalid")


def validate_recognizer_result(result: Any, *, expected_points: Any) -> None:
    if not isinstance(result, dict):
        raise ValueError("recognizer_result_invalid")
    _validate_point_identity(result.get("points"), expected_points)
    count = len(expected_points)
    for key in ("contents", "scores", "directions", "candidates"):
        values = result.get(key)
        if (
            not isinstance(values, (list, tuple, np.ndarray))
            or (isinstance(values, np.ndarray) and values.ndim == 0)
            or len(values) != count
        ):
            raise ValueError(f"recognizer_result_cardinality_invalid: {key}")
    for text, score, direction, candidates in zip(
        result["contents"], result["scores"], result["directions"], result["candidates"],
    ):
        if (
            not isinstance(text, str)
            or not isinstance(direction, str)
            or isinstance(score, bool)
            or not isinstance(score, Real)
            or not math.isfinite(score)
            or not isinstance(candidates, list)
        ):
            raise ValueError("recognizer_result_entry_invalid")
        for candidate in candidates:
            if (
                not isinstance(candidate, dict)
                or not isinstance(candidate.get("text"), str)
                or isinstance(candidate.get("score"), bool)
                or not isinstance(candidate.get("score"), Real)
                or not math.isfinite(candidate["score"])
            ):
                raise ValueError("recognizer_result_candidate_invalid")


@dataclass(frozen=True)
class TextRecognizerCandidate:
    text: str
    score: float
    log_score: float
    token_ids: list[int]
    token_probs: list[float]


class YomitokuTextRecognizerTopKWrapper:
    """Expose ranked TextRecognizer candidates without patching yomitoku itself.

    yomitoku's public TextRecognizer API decodes only the greedy sequence. This
    wrapper keeps the same preprocessing/model path, then reads the softmax
    tensor before that candidate information is discarded.
    """

    def __init__(
        self,
        recognizer: Any,
        *,
        token_top_k: int = 5,
        sequence_top_k: int = 5,
        max_decode_steps: int = 8,
    ) -> None:
        self.recognizer = recognizer
        self.token_top_k = max(1, int(token_top_k))
        self.sequence_top_k = max(1, int(sequence_top_k))
        self.max_decode_steps = max(1, int(max_decode_steps))

    def recognize(self, image_bgr: np.ndarray, *, points: list[list[list[int]]] | None) -> dict[str, Any]:
        requested_points = deepcopy(points)
        if requested_points is not None:
            _validate_point_identity(points, requested_points)
            if len(requested_points) == 0:
                return {"contents": [], "scores": [], "directions": [], "candidates": [], "points": []}
        preprocess_result = self.recognizer.preprocess(image_bgr, points)
        if not isinstance(preprocess_result, tuple):
            raise TypeError("TextRecognizer.preprocess returned non-tuple result")
        if len(preprocess_result) == 3:
            dataloader, points, _dataset = preprocess_result
        elif len(preprocess_result) == 2:
            dataloader, points = preprocess_result
        else:
            raise ValueError(f"TextRecognizer.preprocess returned {len(preprocess_result)} values")
        _validate_point_identity(points, points if requested_points is None else requested_points)
        contents: list[str] = []
        scores: list[float] = []
        directions: list[str] = []
        candidates: list[list[dict[str, Any]]] = []
        offset = 0
        for data in dataloader:
            batch_points = points[offset : offset + len(data)]
            if len(batch_points) != len(data):
                raise ValueError("recognizer_result_batch_cardinality_invalid")
            probs = self._run_inference(data)
            if len(probs) != len(batch_points):
                raise ValueError("recognizer_result_batch_cardinality_invalid")
            postprocessed = self.recognizer.postprocess(probs, batch_points)
            if not isinstance(postprocessed, (tuple, list)) or len(postprocessed) != 3:
                raise ValueError("recognizer_postprocess_result_invalid")
            batch_contents, batch_scores, batch_directions = postprocessed
            batch_candidates = []
            for row in probs.detach().cpu():
                batch_candidates.append(
                    [
                        candidate.__dict__
                        for candidate in self._sequence_candidates(row)
                    ]
                )
            validate_recognizer_result(
                {"contents": batch_contents, "scores": batch_scores, "directions": batch_directions,
                 "candidates": batch_candidates, "points": batch_points},
                expected_points=batch_points,
            )
            contents.extend(batch_contents)
            scores.extend(float(value) for value in batch_scores)
            directions.extend(batch_directions)
            candidates.extend(batch_candidates)
            offset += len(data)
        if offset != len(points):
            raise ValueError("recognizer_result_batch_cardinality_invalid")
        result = {
            "contents": contents,
            "scores": scores,
            "directions": directions,
            "candidates": candidates,
            "points": points,
        }
        validate_recognizer_result(result, expected_points=points if requested_points is None else requested_points)
        return result

    def _run_inference(self, data: Any) -> torch.Tensor:
        run_inference = getattr(self.recognizer, "_run_inference", None)
        if callable(run_inference):
            return run_inference(data)
        if bool(getattr(self.recognizer, "infer_onnx", False)):
            sess = getattr(self.recognizer, "sess", None)
            if sess is None:
                raise AttributeError("TextRecognizer infer_onnx=True but sess is missing")
            array = data.numpy() if hasattr(data, "numpy") else np.asarray(data)
            results = sess.run(["output"], {"input": array})
            return torch.tensor(results[0])
        model = getattr(self.recognizer, "model", None)
        if model is None:
            raise AttributeError("TextRecognizer has neither _run_inference nor model")
        device = getattr(self.recognizer, "device", "cpu")
        with torch.inference_mode():
            model_input = data.to(device) if hasattr(data, "to") else data
            return model(model_input).softmax(-1)

    def _sequence_candidates(self, token_dists: torch.Tensor) -> list[TextRecognizerCandidate]:
        tokenizer = self.recognizer.tokenizer
        greedy_ids = token_dists.argmax(-1).tolist()
        max_steps = min(len(greedy_ids), self.max_decode_steps)
        if hasattr(tokenizer, "eos_id") and tokenizer.eos_id in greedy_ids:
            max_steps = min(max_steps, int(greedy_ids.index(tokenizer.eos_id)) + 2)
        max_steps = max(1, max_steps)

        top_k = min(self.token_top_k, int(token_dists.shape[-1]))
        top_probs, top_ids = torch.topk(token_dists[:max_steps], k=top_k, dim=-1)
        top_probs_np = top_probs.numpy()
        top_ids_np = top_ids.numpy()

        seen_indexes: set[tuple[int, ...]] = set()
        seen_texts: set[str] = set()
        heap: list[tuple[float, tuple[int, ...]]] = []
        start = tuple(0 for _ in range(max_steps))
        heapq.heappush(heap, (-self._combo_log_score(top_probs_np, top_ids_np, start), start))
        seen_indexes.add(start)

        results: list[TextRecognizerCandidate] = []
        max_pops = self.sequence_top_k * max_steps * top_k * 8
        pops = 0
        while heap and len(results) < self.sequence_top_k and pops < max_pops:
            neg_log_score, indexes = heapq.heappop(heap)
            pops += 1
            candidate = self._candidate_from_indexes(top_probs_np, top_ids_np, indexes)
            if candidate.text not in seen_texts:
                seen_texts.add(candidate.text)
                results.append(candidate)
            for pos in range(max_steps):
                next_index = list(indexes)
                if next_index[pos] + 1 >= top_k:
                    continue
                next_index[pos] += 1
                next_tuple = tuple(next_index)
                if next_tuple in seen_indexes:
                    continue
                seen_indexes.add(next_tuple)
                heapq.heappush(heap, (-self._combo_log_score(top_probs_np, top_ids_np, next_tuple), next_tuple))
        return results

    def _combo_log_score(self, top_probs: np.ndarray, top_ids: np.ndarray, indexes: tuple[int, ...]) -> float:
        eos_id = getattr(self.recognizer.tokenizer, "eos_id", None)
        score = 0.0
        for pos, rank in enumerate(indexes):
            prob = max(float(top_probs[pos, rank]), 1e-12)
            score += math.log(prob)
            if eos_id is not None and int(top_ids[pos, rank]) == int(eos_id):
                break
        return score

    def _candidate_from_indexes(
        self,
        top_probs: np.ndarray,
        top_ids: np.ndarray,
        indexes: tuple[int, ...],
    ) -> TextRecognizerCandidate:
        tokenizer = self.recognizer.tokenizer
        eos_id = getattr(tokenizer, "eos_id", None)
        pad_id = getattr(tokenizer, "pad_id", None)
        bos_id = getattr(tokenizer, "bos_id", None)
        token_ids: list[int] = []
        token_probs: list[float] = []
        chars: list[str] = []
        log_score = 0.0
        for pos, rank in enumerate(indexes):
            token_id = int(top_ids[pos, rank])
            prob = max(float(top_probs[pos, rank]), 1e-12)
            token_ids.append(token_id)
            token_probs.append(prob)
            log_score += math.log(prob)
            if eos_id is not None and token_id == int(eos_id):
                break
            if pad_id is not None and token_id == int(pad_id):
                continue
            if bos_id is not None and token_id == int(bos_id):
                continue
            chars.append(str(tokenizer._itos[token_id]))
        text = unicodedata.normalize("NFKC", "".join(chars))
        return TextRecognizerCandidate(
            text=text,
            score=float(math.exp(max(log_score, -745.0))),
            log_score=float(log_score),
            token_ids=token_ids,
            token_probs=token_probs,
        )
