from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _digits(text: Any) -> str:
    return "".join(ch for ch in str(text or "").translate(str.maketrans("０１２３４５６７８９", "0123456789")) if ch.isdigit())


def _field_group(field: str) -> str:
    if field == "qty.regular_x":
        return "regular"
    if field.startswith("qty."):
        return "quantity_other"
    if field == "remarks":
        return "remarks"
    return "other"


def _score_bucket(score: float) -> str:
    if score <= 0:
        return "0"
    if score < 0.15:
        return "0-0.15"
    if score < 0.30:
        return "0.15-0.30"
    if score < 0.45:
        return "0.30-0.45"
    if score < 0.70:
        return "0.45-0.70"
    return "0.70+"


def _failure_reason(row: dict[str, Any]) -> str:
    expected = str((row.get("truth") or {}).get("expected_digits") or "")
    pred = str(row.get("ocr_normalized") or "")
    raw = str(row.get("recognizer_raw_text") or "").strip()
    raw_digits = _digits(raw)
    score = float(row.get("recognizer_score") or 0.0)
    stats = row.get("recognizer_ink_stats") if isinstance(row.get("recognizer_ink_stats"), dict) else {}
    ink_area = int(stats.get("ink_area") or 0)
    kept = int(stats.get("kept_component_count") or 0)

    if not expected and pred:
        return "false_positive_blank_truth"
    if expected and not raw:
        return "no_raw_text"
    if expected and raw_digits == expected and not pred:
        return "raw_correct_rejected_by_score"
    if expected and raw_digits != expected and not pred and raw_digits:
        return "raw_has_wrong_digits_rejected"
    if expected and pred and pred != expected:
        if len(pred) > len(expected):
            return "extra_digit_or_correction_mark"
        if len(pred) < len(expected):
            return "missing_digit"
        return "wrong_digit"
    if expected and not pred and ink_area > 0 and kept > 0:
        if score < 0.45:
            return "ink_present_low_score_no_digit"
        return "ink_present_no_digit"
    if expected and not pred:
        return "expected_but_no_usable_ink"
    return "other"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_field = Counter()
    by_reason = Counter()
    by_score = Counter()
    by_expected = Counter()
    by_pred = Counter()
    examples_by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        field = str(row.get("field") or "")
        reason = _failure_reason(row)
        score = float(row.get("recognizer_score") or 0.0)
        expected = str((row.get("truth") or {}).get("expected_digits") or "")
        pred = str(row.get("ocr_normalized") or "")
        by_field[_field_group(field)] += 1
        by_reason[reason] += 1
        by_score[_score_bucket(score)] += 1
        by_expected[expected] += 1
        by_pred[pred] += 1
        if len(examples_by_reason[reason]) < 12:
            examples_by_reason[reason].append(
                {
                    "sheet_cell": row.get("sheet_cell"),
                    "field": row.get("field"),
                    "field_label": row.get("field_label"),
                    "expected": expected,
                    "pred": pred,
                    "raw": row.get("recognizer_raw_text"),
                    "score": score,
                    "ink": row.get("recognizer_ink_stats"),
                }
            )
    return {
        "count": len(rows),
        "by_field_group": dict(by_field.most_common()),
        "by_failure_reason": dict(by_reason.most_common()),
        "by_score_bucket": dict(by_score.most_common()),
        "by_expected_digits": dict(by_expected.most_common()),
        "by_predicted_digits": dict(by_pred.most_common()),
        "examples_by_failure_reason": dict(examples_by_reason),
    }


def analyze(regions_path: Path, output_dir: Path) -> dict[str, Any]:
    rows = json.loads(regions_path.read_text(encoding="utf-8"))
    target_rows = [
        row
        for row in rows
        if str(row.get("role") or "") == "quantity" and isinstance(row.get("truth"), dict)
    ]
    correct = [
        row
        for row in target_rows
        if str((row.get("truth") or {}).get("expected_digits") or "") == str(row.get("ocr_normalized") or "")
    ]
    incorrect = [
        row
        for row in target_rows
        if str((row.get("truth") or {}).get("expected_digits") or "") != str(row.get("ocr_normalized") or "")
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    correct_path = output_dir / "ocr_correct_cells.json"
    incorrect_path = output_dir / "ocr_incorrect_cells.json"
    correct_path.write_text(json.dumps(correct, ensure_ascii=False, indent=2), encoding="utf-8")
    incorrect_path.write_text(json.dumps(incorrect, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "source": str(regions_path),
        "target_quantity_count": len(target_rows),
        "correct_count": len(correct),
        "incorrect_count": len(incorrect),
        "correct": _summarize(correct),
        "incorrect": _summarize(incorrect),
        "outputs": {
            "correct_cells": str(correct_path),
            "incorrect_cells": str(incorrect_path),
        },
    }
    summary_path = output_dir / "ocr_correct_incorrect_analysis.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--regions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.regions, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
