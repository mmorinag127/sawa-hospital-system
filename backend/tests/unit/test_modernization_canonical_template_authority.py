"""Local resolver/owned JSON component contracts, not OCR, staging or caller audit."""

from copy import deepcopy

import pytest

from modernization_saved_sheet_export_fixtures import SavedSheetFixture
from test_modernization_explicit_ocr_quantity_contract import authored_template
from src.models.order_critical_decision import OrderCriticalDecision
from src.models.order_sheet_draft import OrderSheetDraft
from src.services import config_service, order_service


def publish(fixture):
    fixture.republish()
    # The getter reads the explicit facility entry; versioned lineage retains its snapshot.
    fixture.master["facilities"][0].update(deepcopy(fixture.authored))
    fixture.write_master()


@pytest.fixture
def owned(tmp_path, monkeypatch):
    fixture = SavedSheetFixture(tmp_path, monkeypatch)
    try:
        fixture.authored["fax_template"] = authored_template()
        publish(fixture)
        monkeypatch.setattr(order_service, "session_scope", fixture.session_scope)
        yield fixture
    finally:
        fixture.close()


def resolve(fixture, config, **kwargs):
    arguments = {
        "order_id": fixture.order_id,
        "facility_id": "F",
        "facility_config": config,
        "log_context": "canonical-template-component",
    }
    arguments.update(kwargs)
    return order_service._resolve_effective_sheet_template(**arguments)


@pytest.mark.parametrize("supplied", [True, False], ids=["supplied", "json-getter"])
def test_canonical_template_is_exact_independent_copy(owned, supplied):
    config = config_service.get_authored_facility_config("F")
    before = deepcopy(config)
    result_config, template, identifier, error = resolve(owned, config if supplied else None)
    assert error is None
    assert result_config == before
    assert template == before["fax_template"]
    assert identifier == "fax_layout_regular_forbidden_v1"
    assert template is not result_config["fax_template"]
    template["columns"][0]["role"] = "edited-return-value"
    assert config == before
    assert result_config == before
    assert config_service.get_authored_facility_config("F") == before


def test_supplied_snapshot_survives_real_json_republication(owned):
    snapshot = config_service.get_authored_facility_config("F")
    before = deepcopy(snapshot)
    owned.authored["fax_template"]["header_rows"] = 1
    publish(owned)
    current = config_service.get_authored_facility_config("F")
    assert current["fax_template"]["header_rows"] == 1
    assert before["fax_template"]["header_rows"] == 0
    assert current != before
    assert resolve(owned, snapshot) == (
        before, before["fax_template"], "fax_layout_regular_forbidden_v1", None,
    )
    materialized = order_service._materialize_facility_template_for_template_id(
        snapshot, "fax_layout_regular_forbidden_v1",
    )
    assert materialized == before["fax_template"]
    materialized["columns"][0]["role"] = "edited-return-value"
    assert snapshot == before
    assert config_service.get_authored_facility_config("F") == current


@pytest.mark.parametrize("allow_workflow", [True, False])
def test_payload_draft_and_persisted_choice_cannot_replace_authority(owned, allow_workflow):
    config = config_service.get_authored_facility_config("F")
    before = deepcopy(config)
    payload = {
        "template_id": "ocr-other",
        "template_resolution": {"resolved_template_id": "ocr-other"},
        "template_classification": {"template_id": "semantic-other"},
    }
    payload_before = deepcopy(payload)
    with owned.session_scope() as session:
        draft = session.get(OrderSheetDraft, owned.draft_id)
        draft.base_template_resolution_id = "draft-other"
        session.add(OrderCriticalDecision(
            id="COMPONENT-TEMPLATE-CHOICE", order_id=owned.order_id,
            decision_type="template", selected_value="workflow-other",
            candidate_set_json={"candidates": [{"value": "workflow-other"}]},
            selected_by="synthetic-component",
        ))
    assert resolve(
        owned, config, ocr_payload=payload, draft_template_id="draft-other",
        allow_workflow_resolution=allow_workflow,
    ) == (before, before["fax_template"], "fax_layout_regular_forbidden_v1", None)
    assert config == before
    assert payload == payload_before
    with owned.session_scope() as session:
        assert session.get(OrderCriticalDecision, "COMPONENT-TEMPLATE-CHOICE").selected_value == "workflow-other"
        assert session.get(OrderSheetDraft, owned.draft_id).base_template_resolution_id == "draft-other"


@pytest.mark.parametrize("invalid,error", [
    ("ambiguous", "authored_fax_template_ambiguous"),
    ("mismatched-id", "authored_fax_template_id_mismatch"),
    ("missing-template", "authored_fax_template_missing"),
    ("incomplete-columns", "authored_fax_template_incomplete"),
    ("foreign-facility", "configured_execution_facility_mismatch"),
])
def test_supplied_invalid_authority_stops_despite_matching_hints(owned, invalid, error):
    config = config_service.get_authored_facility_config("F")
    identifier = config["fax_template"]["template_id"]
    if invalid == "ambiguous":
        config["fax_template_ids"] = [identifier, "other"]
    elif invalid == "mismatched-id":
        config["fax_template_id"] = "other"
    elif invalid == "missing-template":
        del config["fax_template"]
    elif invalid == "incomplete-columns":
        config["fax_template"]["columns"] = []
    elif invalid == "foreign-facility":
        config["facility_id"] = "FOREIGN"
    before = deepcopy(config)
    assert resolve(
        owned, config, ocr_payload={"template_resolution": {"resolved_template_id": identifier}},
        draft_template_id=identifier,
    ) == (before, None, None, error)
    assert config == before
    if invalid != "foreign-facility":
        with pytest.raises(ValueError, match=f"^{error}$"):
            order_service._resolve_preferred_template_ids(config)


@pytest.mark.parametrize("facility_id,error", [
    (None, "facility_missing"), ("  ", "facility_missing"),
    ("ABSENT", "facility_not_found"),
])
def test_absent_facility_does_not_infer_from_order_or_hints(owned, facility_id, error):
    assert resolve(
        owned, None, facility_id=facility_id,
        ocr_payload={"facility_id": "F", "template_id": "fax_layout_regular_forbidden_v1"},
    ) == (None, None, None, error)


@pytest.mark.parametrize("declared", ["single", "duplicate-same", "no-id"])
def test_preferred_ids_preserve_canonical_single_or_optional_absence(owned, declared):
    config = config_service.get_authored_facility_config("F")
    identifier = "fax_layout_regular_forbidden_v1"
    if declared == "duplicate-same":
        config["fax_template_ids"] = [identifier, identifier]
    elif declared == "no-id":
        config.pop("fax_template_id", None)
        config.pop("fax_template_ids", None)
        config["fax_template"].pop("template_id")
        identifier = None
    before = deepcopy(config)
    assert config_service._validate_authored_facility_config(config, None) == before
    assert order_service._resolve_preferred_template_ids(config) == (
        identifier, [identifier] if identifier else [],
    )
    assert resolve(owned, config) == (before, before["fax_template"], identifier, None)
    materialized = order_service._materialize_facility_template_for_template_id(config, identifier)
    assert materialized == before["fax_template"]
    assert materialized is not config["fax_template"]
    assert config == before


@pytest.mark.parametrize("invalid", ["non-dict", "missing", "columns", "selected-id", "actual-id"])
def test_materialize_invalid_preserves_none_contract_without_reselection(owned, invalid):
    config = config_service.get_authored_facility_config("F")
    selected = "fax_layout_regular_forbidden_v1"
    if invalid == "non-dict":
        config = None
    elif invalid == "missing":
        config.pop("fax_template")
    elif invalid == "columns":
        config["fax_template"]["columns"] = []
    elif invalid == "selected-id":
        selected = "other"
    elif invalid == "actual-id":
        config["fax_template"]["template_id"] = "other"
    before = deepcopy(config)
    assert order_service._materialize_facility_template_for_template_id(config, selected) is None
    assert config == before
