from copy import deepcopy

import pytest

from src.services import order_workflow_v2_service as workflow


@pytest.mark.parametrize("kind,geometry", [
    ("quad", {"quad_px": [[0, 0], [9, 0], [9, 8], [0, 8]]}),
    ("header_axis", {"corrected_xs": [0, 9]}),
    ("row_axis", {"corrected_ys": [0, 8]}),
])
@pytest.mark.parametrize("changed_field,new_value", [
    ("selected_pdf_receipt", {"document_id": "second", "sha256": "second-pdf", "storage_uri": "second.pdf"}),
    ("order_input_identity", {"facility_code": "second", "week_code": "2026-W42"}),
    ("geometry_source_digest", "second-monthly-input-digest"),
    ("configured_input_digest", "second-config"),
    ("physical_menu_entries", [{"id": "second-menu-row"}]),
    ("workflow_revision", 2),
    ("created_at", "new-time"),
])
def test_same_coordinates_compare_canonical_source(kind, geometry, changed_field, new_value):
    receipt = {
        "selected_pdf_receipt": {"document_id": "first", "sha256": "first-pdf", "storage_uri": "first.pdf"},
        "order_input_identity": {"facility_code": "first", "week_code": "2026-W42"},
        "geometry_source_digest": "first-monthly-input-digest",
        "configured_input_digest": "first-config",
        "physical_menu_entries": [{"id": "first-menu-row"}],
        "workflow_revision": 1, "created_at": "old-time",
    }
    original = {**geometry, "source_receipt": receipt, "created_at": "old-time"}
    key = f"{kind}_override"
    recognition = {"values": {"E11": "12"}, "edited_by": "operator",
                   "source_receipt": deepcopy(receipt), "checkpoint": {"id": "old"}}
    meta = {key: deepcopy(original), "recognition_override": deepcopy(recognition)}
    replacement = deepcopy(original)
    replacement["source_receipt"][changed_field] = new_value
    replacement["created_at"] = "new-time"
    workflow._save_geometry_override(meta, kind=kind, replacement=replacement)
    if changed_field in {"workflow_revision", "created_at"}:
        assert meta[key] == original
        assert meta["recognition_override"] == recognition
        assert "invalidated_recognition_overrides" not in meta
    else:
        assert meta[key] == replacement
        assert "recognition_override" not in meta
        assert meta["invalidated_recognition_overrides"][0]["recognition_override"] == recognition
        assert meta["recognition_override_invalidation"]["reason"] == "recognition_checkpoint_geometry_changed"
