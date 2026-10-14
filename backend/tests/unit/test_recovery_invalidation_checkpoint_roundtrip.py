from copy import deepcopy

import numpy as np
import pytest

from src.services import order_workflow_v2_service as workflow
from src.services import hakodate_ocr_checkpoint_service as checkpoints
from test_hakodate_ocr_checkpoint_service import _item, _regions


@pytest.mark.parametrize("kind,geometry,field", [
    ("quad", {"quad_px": [[0, 0], [9, 0], [9, 8], [0, 8]]}, "quad_px"),
    ("header_axis", {"corrected_xs": [0, 9]}, "corrected_xs"),
    ("row_axis", {"corrected_ys": [0, 8], "body_day_boundary_indexes": [0, 1], "day_labels": ["Monday"]}, "corrected_ys"),
])
def test_same_geometry_resume_and_changed_geometry_archive(tmp_path, monkeypatch, kind, geometry, field):
    monkeypatch.setenv("OCR_ARTIFACT_DIR", str(tmp_path / "canonical"))
    monkeypatch.delenv("OCR_OUTPUT_BUCKET", raising=False)
    key = f"{kind}_override"
    original = {**geometry, "coordinate_space": {"width": 9, "height": 8},
                "source_receipt": {"workflow_revision": 1}, "created_at": "old"}
    item = _item(tmp_path)
    item[key] = deepcopy(original)
    checkpoint = checkpoints.persist_recognizer_checkpoint(
        item=item, raw_rectified_bgr=np.zeros((8, 9, 3), dtype=np.uint8),
        regions=_regions(), expected_sheet_cells=["E11"])
    recognition = {"checkpoint": checkpoint, "values": {"E11": "12"},
                   "edited_by": "operator", "source_receipt": {"revision": 1}}
    meta = {key: deepcopy(original), "recognition_override": deepcopy(recognition)}
    replacement = {**deepcopy(original), "created_at": "new", "source_receipt": {"workflow_revision": 2}}
    workflow._save_geometry_override(meta, kind=kind, replacement=replacement)
    assert meta[key] == original
    assert meta["recognition_override"] == recognition
    item[key] = meta[key]
    loaded = checkpoints.load_recognizer_checkpoint(checkpoint=checkpoint, item=item)
    assert loaded["expected_sheet_cells"] == ["E11"]
    replacement[field][0] = [1, 1] if kind == "quad" else 1
    workflow._save_geometry_override(meta, kind=kind, replacement=replacement)
    assert "recognition_override" not in meta
    assert meta["invalidated_recognition_overrides"][0]["recognition_override"] == recognition
    assert meta["recognition_override_invalidation"]["geometry_kind"] == kind
    item[key] = meta[key]
    with pytest.raises(ValueError, match="hakodate_checkpoint_input_changed"):
        checkpoints.load_recognizer_checkpoint(checkpoint=checkpoint, item=item)


@pytest.mark.parametrize("kind", ["quad", "header_axis", "row_axis"])
@pytest.mark.parametrize("archive", [None, {}, "broken", [None], ["broken"], [{}],
    [{"recognition_override": None, "reason": "recognition_checkpoint_geometry_changed", "geometry_kind": "quad", "invalidated_at": "2026-10-15"}],
    [{"recognition_override": {}, "reason": "unknown", "geometry_kind": "quad", "invalidated_at": "2026-10-15"}],
    [{"recognition_override": {}, "reason": "recognition_checkpoint_geometry_changed", "geometry_kind": "unknown", "invalidated_at": "2026-10-15"}],
    [{"recognition_override": {}, "reason": "recognition_checkpoint_geometry_changed", "geometry_kind": "quad", "invalidated_at": ""}],
])
def test_corrupt_archive_blocks_without_mutation(kind, archive):
    meta = {"invalidated_recognition_overrides": archive, "recognition_override": {"values": {"E11": "12"}}}
    before = deepcopy(meta)
    with pytest.raises(ValueError, match="recognition_override_archive_invalid"):
        workflow._save_geometry_override(meta, kind=kind, replacement={"coordinate_space": {"width": 8}})
    assert meta == before


def test_database_roundtrip_preserves_evidence_and_exposes_invalidation():
    from src.db import session_scope
    from src.models.order_workflow_state import OrderWorkflowState
    from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
    from test_order_workflow_v2_service import _create_order_with_evidence

    order_id, evidence_id, _ = _create_order_with_evidence()
    recognition = {"values": {"E11": "12"}, "edited_by": "operator",
                   "source_receipt": {"revision": 1}, "checkpoint": {"id": "old"}}
    with session_scope() as session:
        state = OrderWorkflowState(order_id=order_id, state="ocr_failed", evidence_run_id=evidence_id)
        session.add(state)
        workflow._write_workflow_meta(state, {"recognition_override": recognition})
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        meta = workflow._workflow_meta(state)
        workflow._save_geometry_override(meta, kind="quad", replacement={"quad_px": [[1, 1], [9, 0], [9, 8], [0, 8]]})
        workflow._write_workflow_meta(state, meta)
    with session_scope() as session:
        state = session.get(OrderWorkflowState, order_id)
        meta = workflow._workflow_meta(state)
        assert "recognition_override" not in meta
        assert meta["invalidated_recognition_overrides"][0]["recognition_override"] == recognition
        assert state.evidence_run_id == evidence_id
        assert session.get(OrderOcrEvidenceRun, evidence_id).payload_json == {"pipeline_version": "test-pipeline"}
        serialized = workflow._serialize_workflow(state)
        assert serialized["recognition_override_invalidation"]["reason"] == "recognition_checkpoint_geometry_changed"
