from copy import deepcopy
import ast
from hashlib import sha256
import fitz
from pathlib import Path
import subprocess

import numpy as np
import pytest

from src.hakodate_best_method_runtime import render_best_method_overlay_all_facilities as runtime
from src.services import hakodate_ocr_checkpoint_service as checkpoints


def _item(tmp_path):
    pdf = tmp_path / "source.pdf"
    if not pdf.exists():
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((30, 30), "Synthetic checkpoint storage fixture")
            document.save(pdf)
    return {
        "order_id": "ORDER-1", "facility_code": "H001", "week_code": "2026-W42",
        "selected_pdf_receipt": {"storage_uri": str(pdf), "sha256": sha256(pdf.read_bytes()).hexdigest()},
        "configured_input": {"facility": {"fax_template": {"page": 1}}},
        "original_physical_draft": {"physical_menu_row_ids": ["monthly-row-1"]},
        "original_target_semantics": [{"sheet_cell": "E11", "physical_menu_row_id": "monthly-row-1"}],
        "original_expected_sheet_cells": ["E11"], "selected_page": 1,
        "quad_px": [[0, 0], [9, 0], [9, 8], [0, 8]], "quad_source": "operator",
        "header_axis_override": None, "row_axis_override": None,
    }


def _regions():
    return [{"sheet_cell": "E11", "bbox": [1.0, 2.0, 3.0, 4.0]}]


@pytest.mark.parametrize("failure", ["uri_missing", "sha_missing", "changed", "mismatch", "missing_file"])
@pytest.mark.parametrize("operation", ["save", "resume"])
def test_pdf_verification_blocks_without_writes_or_recognition(tmp_path, monkeypatch, failure, operation):
    root = tmp_path / "canonical"
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(root))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    item = _item(tmp_path)
    def save():
        return checkpoints.persist_recognizer_checkpoint(item=item,
            raw_rectified_bgr=np.zeros((4, 4, 3), dtype=np.uint8),
            regions=_regions(), expected_sheet_cells=["E11"])
    packet = save() if operation == "resume" else None
    receipt = item["selected_pdf_receipt"]
    if failure == "uri_missing":
        del receipt["storage_uri"]
    elif failure == "sha_missing":
        del receipt["sha256"]
    elif failure == "missing_file":
        Path(receipt["storage_uri"]).unlink()
    elif failure == "changed":
        with fitz.open() as document:
            document.new_page().insert_text((30, 30), "Changed PDF")
            document.save(receipt["storage_uri"])
    else:
        receipt["sha256"] = sha256(b"different bytes").hexdigest()
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(checkpoints.storage_service, "save_canonical_artifact_bytes",
        lambda *args, **kw: pytest.fail("unexpected artifact write"))
    with pytest.raises(ValueError):
        if operation == "save":
            save()
        else:
            checkpoints.resume_recognizer_from_checkpoint(checkpoint=packet, item=item,
                recognize=lambda **kw: pytest.fail("unexpected recognition"))
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_checkpoint_persists_pre_recognition_state_and_resumes_without_preprocessing(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "canonical"))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    image = np.full((8, 9, 3), 127, dtype=np.uint8)
    checkpoint = checkpoints.persist_recognizer_checkpoint(
        item=_item(tmp_path), raw_rectified_bgr=image, regions=_regions(), expected_sheet_cells=["E11"],
    )
    calls = []
    result = checkpoints.resume_recognizer_from_checkpoint(
        checkpoint=checkpoint,
        item=_item(tmp_path),
        recognize=lambda **kwargs: calls.append(kwargs) or "recognizer-result",
    )
    assert result == "recognizer-result"
    assert calls[0]["expected_sheet_cells"] == ["E11"]
    assert calls[0]["regions"] == _regions()
    assert np.array_equal(calls[0]["raw_rectified_bgr"], image)


@pytest.mark.parametrize("field,value", [("week_code", "2026-W43"), ("configured_input", {"facility": {"fax_template": {"page": 2}}})])
def test_checkpoint_rejects_changed_original_input(tmp_path, monkeypatch, field, value):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "canonical"))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    checkpoint = checkpoints.persist_recognizer_checkpoint(
        item=_item(tmp_path), raw_rectified_bgr=np.zeros((4, 4, 3), dtype=np.uint8), regions=_regions(), expected_sheet_cells=["E11"],
    )
    changed = deepcopy(_item(tmp_path))
    changed[field] = value
    with pytest.raises(ValueError, match="^hakodate_checkpoint_input_changed$"):
        checkpoints.load_recognizer_checkpoint(checkpoint=checkpoint, item=changed)


def test_checkpoint_rejects_changed_cell_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "canonical"))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    with pytest.raises(ValueError, match="^hakodate_checkpoint_cell_identity_changed$"):
        checkpoints.persist_recognizer_checkpoint(
            item=_item(tmp_path), raw_rectified_bgr=np.zeros((4, 4, 3), dtype=np.uint8),
            regions=_regions(), expected_sheet_cells=["E12"],
        )


def test_manual_quantity_is_not_part_of_recognizer_checkpoint_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "canonical"))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    checkpoint = checkpoints.persist_recognizer_checkpoint(
        item=_item(tmp_path), raw_rectified_bgr=np.zeros((4, 4, 3), dtype=np.uint8), regions=_regions(), expected_sheet_cells=["E11"],
    )
    assert "operator_quantities" not in checkpoint
    assert "recognizer_output" not in checkpoint


def test_runtime_returns_checkpoint_stage_and_preserves_recognizer_exception(monkeypatch, tmp_path):
    original = RuntimeError("recognizer transport failure")
    regions = _regions()
    monkeypatch.setattr(runtime, "_resolve_item_paths", lambda item: item)
    monkeypatch.setattr(runtime, "_build_preprocess_for_ocr", lambda **kwargs: {
        "expected_sheet_cells": ["E11"], "target_regions": regions,
        "axis_evidence": {}, "raw_rectified": np.zeros((4, 4, 3), dtype=np.uint8),
    })
    monkeypatch.setattr(runtime, "_select_template_owned_eval_regions", lambda value: value)
    monkeypatch.setattr(runtime, "_build_truth_for_facility", lambda *args: ({}, {}))
    monkeypatch.setattr(runtime, "_run_text_recognizer_records", lambda **kwargs: (_ for _ in ()).throw(original))
    checkpoint = {"checkpoint": "canonical-reference"}
    with pytest.raises(runtime.HakodateRecognizerCheckpointError) as raised:
        runtime.build_best_method_for_manifest_item(
            item=_item(tmp_path), page_index=1, draft_sheet={"rows": [["x"]]}, output_dir=tmp_path,
            checkpoint_callback=lambda **kwargs: checkpoint,
        )
    assert raised.value.stage == "recognition"
    assert raised.value.checkpoint_reference == checkpoint
    assert raised.value.__cause__ is original


@pytest.mark.parametrize("field", ["original_physical_draft", "original_target_semantics",
    "original_expected_sheet_cells", "selected_page", "quad_px", "quad_source",
    "header_axis_override", "row_axis_override", "week_code"])
def test_required_source_field_missing_is_rejected(field, tmp_path):
    item = _item(tmp_path)
    del item[field]
    with pytest.raises(ValueError):
        checkpoints._source(item)


@pytest.mark.parametrize("field,value", [
    ("original_physical_draft", {"physical_menu_row_ids": ["monthly-row-2"]}),
    ("original_target_semantics", [{"sheet_cell": "E11", "physical_menu_row_id": "monthly-row-2"}]),
    ("original_expected_sheet_cells", ["E12"]), ("selected_page", 2),
    ("quad_px", [[1, 0], [9, 0], [9, 8], [0, 8]]), ("quad_source", "corrected"),
    ("header_axis_override", {"x": [1, 2]}), ("row_axis_override", {"y": [1, 2]}),
])
def test_resume_rejects_every_changed_source_before_recognition(tmp_path, monkeypatch, field, value):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    packet = checkpoints.persist_recognizer_checkpoint(item=_item(tmp_path), raw_rectified_bgr=np.zeros((4, 4, 3), dtype=np.uint8), regions=_regions(), expected_sheet_cells=["E11"])
    changed = _item(tmp_path)
    changed[field] = value
    with pytest.raises(ValueError, match="hakodate_checkpoint_input_changed"):
        checkpoints.resume_recognizer_from_checkpoint(checkpoint=packet, item=changed, recognize=lambda **kw: pytest.fail("recognizer called"))


@pytest.mark.parametrize("regions,cells", [( [None], ["E11"]), (_regions(), "E11"),
    (_regions(), [{}]), (_regions(), ["E11", "E11"]),
    ([{"sheet_cell": "E11", "bbox": [0, 0, float("nan"), 4]}], ["E11"]),
    ([{"sheet_cell": "E11", "bbox": [0, 0, float("inf"), 4]}], ["E11"]),
    ([{"sheet_cell": "E11", "bbox": [0, 0, 4]}], ["E11"])])
def test_invalid_region_and_cells_are_explicit_errors(regions, cells):
    with pytest.raises(ValueError):
        checkpoints._regions(regions, cells)


def test_same_image_changed_settings_preserves_previous_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    def save(item):
        return checkpoints.persist_recognizer_checkpoint(item=item, raw_rectified_bgr=image, regions=_regions(), expected_sheet_cells=["E11"])
    first = save(_item(tmp_path))
    changed = _item(tmp_path)
    changed["configured_input"]["revision"] = 2
    second = save(changed)
    assert first["checkpoint"]["uri"] != second["checkpoint"]["uri"]
    checkpoints.load_recognizer_checkpoint(checkpoint=first, item=_item(tmp_path))
    checkpoints.load_recognizer_checkpoint(checkpoint=second, item=changed)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_json_rejects_nonfinite(value):
    with pytest.raises(ValueError):
        checkpoints._json_bytes({"value": value})


def test_checkpoint_roundtrip_matches_base_normal_recognition_adapter(tmp_path, monkeypatch):
    """Adapter parity with fixed recognizer output, not live model accuracy proof."""
    from PIL import Image
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    image = np.full((8, 9, 3), 127, dtype=np.uint8)
    regions = _regions()
    packet = checkpoints.persist_recognizer_checkpoint(item=_item(tmp_path), raw_rectified_bgr=image,
        regions=regions, expected_sheet_cells=["E11"])
    monkeypatch.setattr(runtime.time, "perf_counter", lambda: 10.0)
    def contact(**kwargs):
        assert np.array_equal(kwargs["rectified_fax_bgr"], image)
        assert kwargs["regions"] == regions
        return Image.new("RGB", (9, 8)), deepcopy(regions), [], []
    monkeypatch.setattr(runtime, "build_recognizer_contact_sheet", contact)
    monkeypatch.setattr(runtime, "_get_text_recognizer", lambda: object())
    monkeypatch.setattr(runtime, "run_text_recognizer_direct", lambda **kw: ([{
        **kw["regions"][0], "recognizer_raw_text": "7", "ocr_normalized": "7",
        "recognizer_score": 0.95, "recognizer_candidate": True}], {"ocr_seconds": 1.0}))
    repo = Path(__file__).resolve().parents[3]
    source = subprocess.run(["git", "show", "2af63f0:backend/src/hakodate_best_method_runtime/render_best_method_overlay_all_facilities.py"],
        cwd=repo, check=True, capture_output=True, text=True).stdout
    function = next(node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_text_recognizer_records")
    namespace = dict(vars(runtime))
    exec(compile(ast.Module(body=[function], type_ignores=[]), "base-recognizer", "exec"), namespace)
    before = namespace[function.name](raw_rectified_bgr=image, regions=regions, truth={})
    after = checkpoints.resume_recognizer_from_checkpoint(checkpoint=packet, item=_item(tmp_path),
        recognize=lambda raw_rectified_bgr, regions, expected_sheet_cells: runtime._run_text_recognizer_records(
            raw_rectified_bgr=raw_rectified_bgr, regions=regions, truth={}))
    assert before[:2] == after[:2]
    assert before[2].tobytes() == after[2].tobytes()
