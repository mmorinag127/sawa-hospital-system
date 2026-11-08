from copy import deepcopy

import pytest

from src.services.template_field_schema_service import quantity_field_identities_for_fields, resolve_quantity_field_identities


def grouped_template():
    return {"columns": [
        {"role": "quantity", "diet_type": "no_fish", "area_id": area,
         "name": f"qty.no_fish_{group}_{area.lower()}", "name_locked": True,
         "header_super_group": "禁食、魚", "header_group": label, "source_index": 10 + g * 2 + a}
        for g, (group, label) in enumerate((("regular", "常食"), ("soft", "軟菜"), ("mixer", "ミキサー")))
        for a, area in enumerate(("2F", "3F"))
    ]}


def test_explicit_separate_excluded_groups_retain_every_physical_field_and_semantic_identity():
    template = grouped_template()
    before = deepcopy(template)
    resolved = resolve_quantity_field_identities(template)
    assert len(resolved) == 6
    assert set(resolved.values()) == {("no_fish", "2F"), ("no_fish", "3F")}
    assert quantity_field_identities_for_fields(template, list(resolved)) == resolved
    assert template == before


def test_grouped_quantities_are_transcribed_by_column_without_last_group_overwrite():
    from src.services.order_service import _project_payload_quantities_onto_sheet_row_lists
    template = grouped_template()
    fields = ["date_mmdd", "daypart", "menu", *resolve_quantity_field_identities(template)]
    target = [["11/15", "朝", "魚料理", *([""] * 6)]]
    source = [["11/15", "朝", "魚料理", "1", "2", "3", "4", "5", "6"]]
    actual, _ = _project_payload_quantities_onto_sheet_row_lists(
        rows=target, fields=fields, payload_rows=source,
        explicit_quantity_pairs=[(i, i) for i in range(3, 9)], template=template)
    assert actual == source
    assert target[0][3:] == [""] * 6


@pytest.mark.parametrize("fault", ["same_group", "missing_group", "unlocked", "same_source", "aggregating", "same_field"])
def test_ambiguous_or_aggregating_duplicates_remain_rejected(fault):
    template = grouped_template()
    first, second = template["columns"][0], template["columns"][2]
    if fault == "same_group": second["header_group"] = first["header_group"]
    if fault == "missing_group": second.pop("header_group")
    if fault == "unlocked": second["name_locked"] = False
    if fault == "same_source": second["source_index"] = first["source_index"]
    if fault == "aggregating":
        for column in template["columns"]: column["diet_type"] = "regular"
    if fault == "same_field": second["name"] = first["name"]
    with pytest.raises(ValueError): resolve_quantity_field_identities(template)


def test_native_shift_changes_are_sealed_to_the_actual_runtime_source():
    from pathlib import Path
    from experiments.agent_assistant.runtime import verify_source
    root = Path(__file__).resolve().parents[3] / "agent_runtime"
    verify_source(root / "native/shift", root / "manifests/shift.json")
