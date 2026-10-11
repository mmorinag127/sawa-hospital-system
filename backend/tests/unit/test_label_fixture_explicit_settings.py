"""Synthetic configuration transport, not native OCR or production policy proof."""
from copy import deepcopy
import json

import pytest

from tests.helpers.monthly_override_fixture import publish_configuration
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order


@pytest.mark.parametrize("values", [None, [], [" staff ", "synthetic-unknown"]])
def test_monthly_fixture_preserves_explicit_setting_or_absence(monkeypatch, tmp_path, isolated_formal_schema, values):
    original = deepcopy(values)
    publish_configuration(monkeypatch, tmp_path, day="2026-07-12",
                          daily_label_comparable_diet_types=values)
    facility = json.loads((tmp_path / "authored-facilities.json").read_text())["facilities"][0]
    selected = facility["versions"][0]["facility"]
    for authored in (facility, selected):
        if values is None:
            assert "daily_label_comparable_diet_types" not in authored
        else:
            assert authored["daily_label_comparable_diet_types"] == values
    assert values == original


@pytest.mark.parametrize("values", [None, [], [" staff ", "synthetic-unknown"]])
def test_bagging_fixture_authors_setting_before_upload(monkeypatch, tmp_path, isolated_formal_schema, values):
    from tests.helpers import newnarrowbaggingfixture as helper

    reserve = helper.reserve_uploaded_original
    observed = []

    def observe_authored_json(*args, **kwargs):
        facility = json.loads((tmp_path / "authored-facilities.json").read_text())["facilities"][0]
        for authored in (facility, facility["versions"][0]["facility"]):
            if values is None:
                assert "daily_label_comparable_diet_types" not in authored
            else:
                assert authored["daily_label_comparable_diet_types"] == values
        observed.append(True)
        return reserve(*args, **kwargs)

    monkeypatch.setattr(helper, "reserve_uploaded_original", observe_authored_json)
    original = deepcopy(values)
    registered = accepted_bagging_order(monkeypatch, tmp_path, [{
        "date": "2026-05-10", "daypart": "朝", "menu_name": "Synthetic regular menu",
        "diet_type": "regular", "area_id": None, "quantity_original": 2,
        "bag_type": "normal",
    }], daily_label_comparable_diet_types=values)
    assert registered["id"]
    assert observed == [True]
    assert values == original
