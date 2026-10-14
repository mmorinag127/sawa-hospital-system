from copy import deepcopy
from hashlib import sha256

import fitz
import numpy as np
import pytest

from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as runtime
from src.services import hakodate_ocr_checkpoint_service as checkpoints


def _item(tmp_path):
    pdf = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((30, 30), "checkpoint fixture")
        document.save(pdf)
    return {
        "order_id": "ORDER-1", "facility_code": "H001", "week_code": "2026-W42",
        "selected_pdf_receipt": {"storage_uri": str(pdf), "sha256": sha256(pdf.read_bytes()).hexdigest()},
        "configured_input": {"facility": {"fax_template": {"page": 1}}},
        "original_physical_draft": {"physical_menu_row_ids": ["row-1"]},
        "original_target_semantics": [{"sheet_cell": "E11", "physical_menu_row_id": "row-1"}],
        "original_expected_sheet_cells": ["E11"], "selected_page": 1,
        "quad_px": [[0, 0], [9, 0], [9, 8], [0, 8]], "quad_source": "operator",
        "quad_override": None, "header_axis_override": None, "row_axis_override": None,
    }


def _checkpoint(tmp_path, item):
    return checkpoints.persist_recognizer_checkpoint(
        item=item, raw_rectified_bgr=np.zeros((8, 9, 3), dtype=np.uint8),
        regions=[{"sheet_cell": "E11", "bbox": [1, 1, 5, 5], "worksheet_row": 11, "worksheet_col": 5,
                  "logical_targets": [{"semantic_field": "regular"}]}], expected_sheet_cells=["E11"],
        metadata={"axis_evidence": {"header_intersection_x_match": {}},
                  "rectified_quad_points": [[0, 0], [8, 0], [8, 7], [0, 7]], "snap_debug": {"applied": True}},
    )


def test_checkpoint_resume_rejects_changed_input_before_preprocess_or_recognition(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    item = _item(tmp_path)
    checkpoint = _checkpoint(tmp_path, item)
    changed = deepcopy(item)
    changed["week_code"] = "2026-W43"
    monkeypatch.setattr(runtime, "_build_preprocess_for_ocr", lambda **kw: pytest.fail("preprocess called"))
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kw: pytest.fail("recognizer called"))
    with pytest.raises(ValueError, match="hakodate_checkpoint_input_changed"):
        runtime.build_best_method_for_manifest_item(item=changed, page_index=1, draft_sheet={"rows": [["1"]], "fields": ["regular"]}, output_dir=tmp_path, recognition_checkpoint=checkpoint)


def test_checkpoint_load_exposes_safe_resume_state_and_rejects_quad_override_change(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    item = _item(tmp_path)
    checkpoint = _checkpoint(tmp_path, item)
    state = checkpoints.load_recognizer_checkpoint(checkpoint=checkpoint, item=item)
    assert set(("resume_item", "axis_evidence", "rectified_quad_points", "snap_debug")) <= set(state)
    assert "storage_uri" not in state["resume_item"]
    changed = deepcopy(item)
    changed["quad_override"] = {"points": [[1, 1], [8, 1], [8, 7], [1, 7]]}
    with pytest.raises(ValueError, match="hakodate_checkpoint_input_changed"):
        checkpoints.load_recognizer_checkpoint(checkpoint=checkpoint, item=changed)


def test_operator_values_are_preserved_without_preprocess_or_recognizer(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    item = _item(tmp_path)
    checkpoint = _checkpoint(tmp_path, item)
    monkeypatch.setattr(runtime, "_build_preprocess_for_ocr", lambda **kw: pytest.fail("preprocess called"))
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kw: pytest.fail("recognizer called"))
    monkeypatch.setattr(runtime, "build_recognizer_contact_sheet", lambda **kw: (runtime.Image.new("RGB", (9, 8)), kw["regions"], [], []))
    summary, _ = runtime.build_best_method_for_manifest_item(
        item=item, page_index=1, draft_sheet={"rows": [["1"]], "fields": ["regular"]}, output_dir=tmp_path,
        recognition_checkpoint=checkpoint, operator_values={"E11": "12"},
    )
    records = __import__("json").loads(open(summary["outputs"]["records"], encoding="utf-8").read())
    regions = __import__("json").loads(open(summary["outputs"]["ocr_regions"], encoding="utf-8").read())
    assert records[0]["pred_digits"] == "12"
    assert regions[0]["ocr_engine"] == "operator"
    assert summary["recognition_provenance"] == "operator"


def test_normal_callback_free_artifacts_keep_recognizer_provenance(tmp_path, monkeypatch):
    item = _item(tmp_path)
    region = {"sheet_cell": "E11", "bbox": [1, 1, 5, 5], "worksheet_row": 11, "worksheet_col": 5,
              "logical_targets": [{"semantic_field": "regular"}]}
    monkeypatch.setattr(runtime, "_build_preprocess_for_ocr", lambda **kw: {
        "raw_rectified": np.zeros((8, 9, 3), dtype=np.uint8), "expected_sheet_cells": ["E11"],
        "target_regions": [region], "axis_evidence": {}, "rectified_quad_points": [[0, 0], [8, 0], [8, 7], [0, 7]],
    })
    monkeypatch.setattr(runtime, "_select_template_owned_eval_regions", lambda regions: regions)
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kw: ([{
        **region, "truth": kw["truth"]["E11"], "expected_digits": "1", "raw_text": "1", "pred_digits": "1",
        "score": 0.9, "ocr_candidate": True, "ocr_engine": "recognizer",
    }], {"ocr_seconds": 0, "raw_prediction_count": 1, "digit_prediction_count": 1, "accepted_digit_count": 1,
          "candidate_digit_prediction_count": 1, "candidate_digit_accept_count": 1, "contact_sheet_seconds": 0,
          "contact_sheet_size": [9, 8], "contact_sheet_region_count": 1, "contact_sheet_usable_region_count": 1,
          "contact_sheet_skipped_region_count": 0}, runtime.Image.new("RGB", (9, 8))))
    summary, _ = runtime.build_best_method_for_manifest_item(
        item=item, page_index=1, draft_sheet={"rows": [["1"]], "fields": ["regular"]}, output_dir=tmp_path,
    )
    regions = __import__("json").loads(open(summary["outputs"]["ocr_regions"], encoding="utf-8").read())
    assert summary["recognition_provenance"] == "recognizer"
    assert regions[0]["source"] == "render_best_method_overlay_all_facilities.py"


@pytest.mark.parametrize("operator_values", [{}, {"E11": "-1"}, {"E11": 1}, {"E11": "1", "E12": "2"}, {"E11": "１２"}, {"E11": "١"}])
def test_operator_values_require_exact_cells_and_nonnegative_integer_strings(operator_values):
    with pytest.raises(ValueError):
        runtime._validate_operator_values(operator_values, ["E11"])


def test_missing_metadata_allows_recognizer_only_but_blocks_artifact_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    item = _item(tmp_path)
    packet = checkpoints.persist_recognizer_checkpoint(
        item=item, raw_rectified_bgr=np.zeros((8, 9, 3), dtype=np.uint8),
        regions=[{"sheet_cell": "E11", "bbox": [1, 1, 5, 5]}], expected_sheet_cells=["E11"],
    )
    assert packet["metadata"] is None
    state = checkpoints.load_recognizer_checkpoint(checkpoint=packet, item=item)
    assert state["rectified_quad_points"] is None
    assert state["resume_item"]["selected_pdf_receipt"] == item["selected_pdf_receipt"]
    assert checkpoints.resume_recognizer_from_checkpoint(checkpoint=packet, item=item, recognize=lambda **kw: "ok") == "ok"
    monkeypatch.setattr(runtime, "_build_preprocess_for_ocr", lambda **kw: pytest.fail("preprocess"))
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kw: pytest.fail("recognizer"))
    with pytest.raises(ValueError, match="hakodate_checkpoint_metadata_required"):
        runtime.build_best_method_for_manifest_item(item=item, page_index=1, draft_sheet={"rows": [["1"]]}, output_dir=tmp_path, recognition_checkpoint=packet)


@pytest.mark.parametrize("value", ["", "0", "0012", "12"])
def test_manual_json_retains_value_and_provenance(tmp_path, monkeypatch, value):
    import json
    from pathlib import Path
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    item = _item(tmp_path)
    packet = _checkpoint(tmp_path, item)
    monkeypatch.setattr(runtime, "_build_preprocess_for_ocr", lambda **kw: pytest.fail("preprocess"))
    monkeypatch.setattr(runtime, "_snap_regions_x_to_fax_lines_all_targets", lambda *a: pytest.fail("snap"))
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kw: pytest.fail("recognizer"))
    monkeypatch.setattr(runtime, "build_recognizer_contact_sheet", lambda **kw: (runtime.Image.new("RGB", (9, 8)), kw["regions"], [], []))
    summary, _ = runtime.build_best_method_for_manifest_item(item=item, page_index=1, draft_sheet={"rows": [["1"]], "fields": ["regular"]}, output_dir=tmp_path, recognition_checkpoint=packet, operator_values={"E11": value})
    for kind in ("records", "ocr_regions"):
        record = json.loads(Path(summary["outputs"][kind]).read_text())[0]
        assert record["operator_value"] == value
        assert record["pred_digits"] == value
        assert record["recognition_source"] == record["ocr_engine"] == "operator"
        assert record["raw_text"] == ""
        assert not record.get("recognizer_raw_text")
    assert all(Path(path).is_file() for path in summary["outputs"].values())
