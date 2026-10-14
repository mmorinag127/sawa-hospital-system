from copy import deepcopy

from src.services import order_workflow_v2_service as workflow


def _recognition_override():
    return {
        "checkpoint": {"checkpoint": {"uri": "checkpoint://old"}},
        "values": {"E11": "12"},
        "edited_by": "operator@example.test",
        "source_receipt": {"recognition_checkpoint_digest": "old-digest"},
        "source": "operator",
    }


def _header_override(*, xs, revision):
    return {
        "corrected_xs": xs,
        "coordinate_space": {"mode": "template_canvas", "width": 100, "height": 200},
        "source_receipt": {"workflow_revision": revision, "geometry_source_digest": "same-source"},
    }


def test_geometry_change_expires_checkpoint_but_preserves_operator_audit_data():
    recognition = _recognition_override()
    meta = {
        "recognition_override": deepcopy(recognition),
        "header_axis_override": _header_override(xs=[10.0, 90.0], revision=3),
        "draft_marker": "preserved",
        "evidence_marker": "preserved",
        "quantity_marker": "preserved",
    }

    changed = workflow._invalidate_recognition_checkpoint_for_geometry_change(
        meta, kind="header_axis", replacement=_header_override(xs=[12.0, 90.0], revision=4),
    )

    assert changed is True
    assert "recognition_override" not in meta
    assert meta["invalidated_recognition_overrides"][-1]["recognition_override"] == recognition
    assert meta["invalidated_recognition_overrides"][-1]["reason"] == "recognition_checkpoint_geometry_changed"
    assert meta["recognition_override_invalidation"]["geometry_kind"] == "header_axis"
    assert meta["draft_marker"] == "preserved"
    assert meta["evidence_marker"] == "preserved"
    assert meta["quantity_marker"] == "preserved"


def test_receipt_revision_only_change_keeps_checkpoint_when_geometry_is_identical():
    recognition = _recognition_override()
    meta = {
        "recognition_override": deepcopy(recognition),
        "header_axis_override": _header_override(xs=[10.0, 90.0], revision=3),
    }

    changed = workflow._invalidate_recognition_checkpoint_for_geometry_change(
        meta, kind="header_axis", replacement=_header_override(xs=[10.0, 90.0], revision=4),
    )

    assert changed is False
    assert meta["recognition_override"] == recognition
    assert "invalidated_recognition_overrides" not in meta
