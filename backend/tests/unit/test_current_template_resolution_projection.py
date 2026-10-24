"""Bound current-sheet resolution components, not recognition accuracy proof."""
from copy import deepcopy

import pytest

from tests.helpers.authored_sheet_case import authored_sheet_case  # noqa: F401
from src.services import candidate_resolution_service as candidates, order_service
from src.services.workflow_attempt_guard_service import CurrentSheetInputChanged


@pytest.mark.parametrize("legacy", [
    None,
    {"resolved_template_id": "unselected-old-template", "blocked": True},
    {"candidates": [{"id": "old-template", "score": "invalid-old-diagnostic"}]},
])
def test_recorded_current_template_is_not_replaced_or_blocked_by_legacy_candidate(authored_sheet_case, legacy):
    case = authored_sheet_case(quantity=26, area="X")
    context = order_service.get_current_sheet_context(case.id)
    expected = context.input_receipt["input"]["configured_input"]["facility"]["fax_template"]["template_id"]
    payload = {} if legacy is None else {"template_resolution": deepcopy(legacy)}
    before = deepcopy(payload)
    with case.readonly():
        result = candidates.resolve_order_candidates(
            order_id=case.id, facility_code=case.facility, week_code=case.period,
            received_at=None, evidence_payload=payload, current_sheet_context=context,
        )
    template = result["resolutions"]["template"]
    assert template["resolved_value"] == expected
    assert template["decision_source"] == "configured_current_sheet"
    assert template["blocked"] is False and template["requires_user_choice"] is False
    assert payload == before
    assert order_service.get_current_sheet_context(case.id)["rows"] == context["rows"]


@pytest.mark.parametrize("damage", ["missing_receipt", "changed_content"])
def test_current_template_requires_unchanged_bound_source(authored_sheet_case, damage):
    case = authored_sheet_case(quantity=26, area="X")
    current = order_service.get_current_sheet_context(case.id)
    if damage == "missing_receipt":
        supplied = dict(current)
        error = "current_sheet_input_receipt_missing"
    else:
        supplied = deepcopy(current)
        supplied["fields"] = [*current["fields"], "qty.unowned_x"]
        error = "current_sheet_context_receipt_mismatch"
    with case.readonly(), pytest.raises(CurrentSheetInputChanged, match=error):
        candidates.resolve_order_candidates(
            order_id=case.id, facility_code=case.facility, week_code=case.period,
            received_at=None, evidence_payload={}, current_sheet_context=supplied,
        )
