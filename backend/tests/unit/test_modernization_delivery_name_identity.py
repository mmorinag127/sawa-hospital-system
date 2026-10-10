"""Captured display-name components and actual accepted child-HTML consumption.

The real fixture uses owned SQLite/JSON/PDF, manual OCR selection,
AUTH_DISABLED and no app lifespan. Not native/auth/UI/formal PG/live proof.
No capture, producer, reader, renderer or stop guard is mocked.
"""

from copy import deepcopy
from datetime import date

import pytest

from test_modernization_real_bagging_confirmation import owned_bagging as owned_bagging
from test_modernization_workflow_surface_parity import surface as surface
from test_modernization_real_daily_delivery_html import (
    assert_preserved, confirm, preservation, received_html,
)
from src.models.document import OrderDocument
from src.models.order import Order
from src.services import order_service as orders
from src.services import output_builder as output


def component_config(names):
    return {
        "unknown": {"none": None, "empty": "", "raw_quantities": [7, 0, None, ""]},
        **deepcopy(names),
    }


@pytest.mark.parametrize("names,expected", [
    ({"facility_name": "Canonical facility"}, "Canonical facility"),
    ({"facility_name": " Canonical facility "}, "Canonical facility"),
    ({"facility_name": "Canonical facility", "delivery_note_facility_name": " Explicit delivery name "}, "Explicit delivery name"),
    ({"delivery_note_facility_name": "Explicit delivery name"}, "Explicit delivery name"),
    ({"facility_name": None, "delivery_note_facility_name": "Explicit delivery name"}, "Explicit delivery name"),
])
def test_captured_display_name_selects_only_explicit_authority_without_mutation(names, expected):
    config = component_config(names)
    before = deepcopy(config)
    assert output._captured_delivery_facility_name(config) == expected
    assert config == before


@pytest.mark.parametrize("invalid", [None, "", " \t", 7, [], {}, True])
def test_present_invalid_override_never_falls_back_to_valid_canonical_name(invalid):
    config = component_config({"facility_name": "Canonical facility", "delivery_note_facility_name": invalid})
    before = deepcopy(config)
    with pytest.raises(ValueError, match="^accepted_delivery_note_facility_name_invalid$"):
        output._captured_delivery_facility_name(config)
    assert config == before


@pytest.mark.parametrize("names", [
    {}, {"facility_name": None}, {"facility_name": ""}, {"facility_name": " \t"},
    {"facility_name": 7}, {"facility_name": []}, {"facility_name": {}}, {"facility_name": True},
])
def test_absent_override_requires_valid_canonical_name_without_mutation(names):
    config = component_config(names)
    before = deepcopy(config)
    with pytest.raises(ValueError, match="^accepted_facility_name_invalid$"):
        output._captured_delivery_facility_name(config)
    assert config == before


def test_real_captured_override_reaches_child_html_with_seven_and_unchanged_sources(
    owned_bagging, surface, capsys,
):
    fixture = owned_bagging
    explicit_name = "Synthetic authored delivery display name"
    fixture.authored["delivery_note_facility_name"] = explicit_name
    fixture.authored["invoice_template"] = {
        "include_menu_name": True,
        "columns": [
            {"name": "Date", "source": "date"},
            {"name": "Menu", "source": "menu_name"},
            {"name": "Quantity", "source": "quantity", "diet_type": "regular", "area_id": "X"},
        ],
    }
    fixture.republish()
    with fixture.session_scope() as session:
        orders._record_current_order_version(
            session, session.get(Order, fixture.order_id), session.get(OrderDocument, "UNIT-DOC"),
        )
    candidate = confirm(fixture)
    captured_facility = candidate["configured_input"]["facility"]
    assert captured_facility["delivery_note_facility_name"] == explicit_name
    assert captured_facility["facility_name"] == "保存済み施設"
    assert [(line["menu_name"], line["quantity_original"]) for line in candidate["lines"]] == [
        ("選択献立0", 7), ("選択献立3", 0),
    ]
    before = preservation(fixture)
    candidate_before = deepcopy(candidate)
    day = date.fromisoformat(str(candidate["lines"][0]["date"])[:10])
    parsed = received_html(surface[1].get("/outputs/delivery-notes/html", params={
        "order_id": fixture.order_id, "date": day.isoformat(),
    }), fixture, "child-explicit-delivery-name.html", capsys)
    assert parsed.cells["facility-name"] == explicit_name
    quantities = {
        value: parsed.cells[key.replace("-c-3", "-c-4")]
        for key, value in parsed.cells.items() if key.startswith("r-") and key.endswith("-c-3")
    }
    assert quantities["選択献立0"] == "7"
    assert quantities["選択献立3"] == ""
    assert candidate == candidate_before
    assert_preserved(fixture, before)
