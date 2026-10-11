"""Synthetic area identity inputs; no production facility configuration."""
from copy import deepcopy

import pytest

from src.services import output_builder


@pytest.mark.parametrize("areas,error", [
    ([{"id": "2F"}], "label_area_name_required"),
    ([{"id": "3F", "name": "Synthetic area"}], "label_area_identity_unresolved"),
])
def test_synthetic_label_binding_stops_without_own_named_area(areas, error):
    bags = [{"area_id": "2F", "quantity": 13}]
    original = deepcopy(bags)
    with pytest.raises(ValueError, match=error):
        output_builder._bind_label_area_names(bags, {"areas": areas})
    assert bags == original


def test_synthetic_label_suffix_requires_explicit_name():
    with pytest.raises(ValueError, match="label_area_name_required"):
        output_builder._label_area_suffix("2F")


def test_synthetic_label_binding_preserves_identity_quantity_and_input():
    bags = [{"area_id": "2F", "quantity": 14}]
    original = deepcopy(bags)
    actual = output_builder._bind_label_area_names(
        bags, {"areas": [{"id": "2F", "name": "Synthetic area"}]})
    assert actual == [{"area_id": "2F", "quantity": 14, "area_name": "Synthetic area"}]
    assert bags == original
