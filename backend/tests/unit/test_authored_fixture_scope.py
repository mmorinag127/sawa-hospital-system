"""Reject incompatible authored fixture reuse; never real FAX acceptance proof."""
from datetime import date

import pytest

from tests.helpers.ingest_original import authored_sources
from tests.helpers.newnarrowbaggingfixture import selected_bagging_order


@pytest.mark.parametrize('change', ['facility', 'date', 'daypart'])
def test_fixed_authored_menu_row_must_match_its_explicit_scope(
        monkeypatch, tmp_path, isolated_formal_schema, change):
    values = {'facility_id': 'authored-a', 'menu_date': date(2026, 4, 18), 'daypart': 'lunch'}
    authored_sources(monkeypatch, tmp_path, **values)
    values.update({'facility': {'facility_id': 'authored-b'},
                   'date': {'menu_date': date(2026, 4, 19)},
                   'daypart': {'daypart': 'breakfast'}}[change])
    with pytest.raises(AssertionError, match='existing authored menu row'):
        authored_sources(monkeypatch, tmp_path, **values)


@pytest.mark.parametrize('change', [
    {'menu_qty_per_serving': 2}, {'menu_unit_type': '個'},
    {'menu_category': '副菜'}, {'menu_temp_type': 'cold'},
])
def test_named_authored_master_cannot_supply_different_values(
        monkeypatch, tmp_path, isolated_formal_schema, change):
    lines = [{'date': '2026-04-18', 'daypart': '昼', 'menu_name': 'Synthetic menu',
              'diet_type': 'regular', 'area_id': '2F', 'bag_type': 'standard',
              'quantity_original': 9}]
    selected_bagging_order(monkeypatch, tmp_path, lines,
                          facility_id='authored-a', prepare_only=True)
    with pytest.raises(AssertionError, match='existing authored menu master'):
        selected_bagging_order(monkeypatch, tmp_path, lines,
                              facility_id='authored-b', prepare_only=True, **change)
