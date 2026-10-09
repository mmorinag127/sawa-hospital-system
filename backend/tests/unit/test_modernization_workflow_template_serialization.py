"""Pure serialization components, not DB, OCR, or end-to-end workflow proof."""

from copy import deepcopy
from datetime import datetime
import json

import pytest

from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_workflow_v2_service as workflow_service


@pytest.mark.parametrize(
    ("id_metadata", "expected_id"),
    [
        pytest.param({}, None, id="absent"),
        pytest.param({"template_id": None}, None, id="null"),
        pytest.param({"template_id": ""}, None, id="empty"),
        pytest.param({"template_id": " \t "}, None, id="blank"),
        pytest.param({"template_id": "selected-template"}, "selected-template", id="explicit"),
        pytest.param({"template_id": " \tselected-template\n"}, "selected-template", id="normalized"),
    ],
)
@pytest.mark.parametrize(
    ("source_metadata", "expected_source"),
    [
        pytest.param({}, None, id="source-absent"),
        pytest.param({"template_source": None}, None, id="source-null"),
        pytest.param({"template_source": ""}, None, id="source-empty"),
        pytest.param(
            {"template_source": "facility_resolved_template"},
            "facility_resolved_template",
            id="source-resolved",
        ),
        pytest.param(
            {"template_source": "facility_template_version"},
            "facility_template_version",
            id="source-version",
        ),
    ],
)
def test_explicit_metadata_only_serialization(
    monkeypatch, tmp_path, id_metadata, expected_id, source_metadata, expected_source
) -> None:
    root = tmp_path / "facilities.json"
    root.write_text(
        json.dumps({"facilities": [{"facility_id": "F", "fax_template_id": "root-template"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(root))

    def forbidden_getter(*args, **kwargs):
        pytest.fail("workflow serialization must not read current facility configuration")

    monkeypatch.setattr(workflow_service.config_service, "get_facility_config", forbidden_getter)
    meta = {"facility_id": "F", **id_metadata, **source_metadata}
    row = OrderWorkflowState(
        order_id="component-order",
        revision=4,
        state="context_confirmed",
        headline="component headline",
        primary_action="run_ocr",
        template_version_id="selected-version",
        secondary_actions_json={"workflow_v2": deepcopy(meta)},
        blockers_json=["template_version_required", "context_not_confirmed"],
        warnings_json=["component-warning"],
        last_transition_at=datetime(2026, 10, 10, 1, 2, 3),
    )
    original_meta = deepcopy(meta)
    original_row_meta = deepcopy(row.secondary_actions_json)
    original_blockers = deepcopy(row.blockers_json)

    assert workflow_service._effective_workflow_template_id(meta) == expected_id
    serialized = workflow_service._serialize_workflow(row)

    assert serialized["template_id"] == expected_id
    assert serialized["template_source"] == (
        expected_source or ("facility_resolved_template" if expected_id else None)
    )
    assert serialized["template_version_id"] == "selected-version"
    assert serialized["facility_id"] == "F"
    assert serialized["order_id"] == "component-order"
    assert serialized["revision"] == 4
    assert serialized["state"] == "context_confirmed"
    assert serialized["headline"] == "component headline"
    assert serialized["primary_action"] == "run_ocr"
    assert serialized["blockers"] == ["template_version_required", "context_not_confirmed"]
    assert serialized["warnings"] == ["component-warning"]
    assert serialized["source"] == "workflow_v2"
    assert meta == original_meta
    assert row.secondary_actions_json == original_row_meta
    assert row.blockers_json == original_blockers
    assert root.read_text(encoding="utf-8") == json.dumps(
        {"facilities": [{"facility_id": "F", "fax_template_id": "root-template"}]}
    )
