from copy import deepcopy

import pytest

from src.services import hakodate_physical_menu_row_service as physical
from src.services import order_service


def own_entry():
    return {"menu_date": "2026-10-07", "daypart_key": "lunch", "slot_index": 0, "menu_name": "own"}


@pytest.mark.parametrize("slot", [None, False, 1.5, "1", -1])
def test_physical_slot_invalid_is_not_ordinal(slot):
    entry = {**own_entry(), "slot_index": slot}
    with pytest.raises(ValueError, match="physical_menu_slot_identity_required"):
        physical.physical_row_key_from_entry(entry)
    with pytest.raises(ValueError, match="physical_menu_slot_identity_required"):
        physical.physical_row_ids_from_entries([entry])


def test_physical_missing_duplicate_empty_and_valid_zero():
    missing = own_entry()
    del missing["slot_index"]
    with pytest.raises(ValueError, match="physical_menu_slot_identity_required"):
        physical.physical_row_id_from_entry(missing)
    with pytest.raises(ValueError, match="physical_menu_row_duplicate"):
        physical.physical_row_ids_from_entries([own_entry(), own_entry()])
    with pytest.raises(ValueError, match="physical_menu_rows_unresolved"):
        physical.physical_row_keys_from_entries([])
    assert physical.physical_row_ids_from_entries([own_entry()]) == ["2026-10-07__lunch__0"]
    assert physical.physical_row_count_from_sheet({"physical_menu_row_count": 1})[0] == 0
    assert physical.physical_row_count_from_sheet({"physical_menu_row_ids": ["physical-row-1"]})[0] == 0
    assert physical.physical_row_count_from_sheet({"physical_menu_row_ids": ["2026-10-07__lunch__0"], "physical_menu_row_count": 1})[0] == 1


@pytest.mark.parametrize("slot", [None, False, 0.5, "0"])
def test_actual_monthly_mapping_does_not_fill_slot(monkeypatch, slot):
    monkeypatch.setattr(order_service, "_sheet_week_month_ids", lambda week: ["2026-10"])
    monkeypatch.setattr(order_service.menu_service, "get_menu_entries_for_facility", lambda month, facility: [{"name": "own", "menu_date": "2026-10-07", "daypart": "lunch", "slot_index": slot}])
    with pytest.raises(ValueError, match="physical_menu_slot_identity_required"):
        order_service._build_position_menu_entries("2026-10", "fixture")


def test_actual_monthly_mapping_zero_and_duplicate(monkeypatch):
    raw = {"name": "own", "menu_date": "2026-10-07", "daypart": "lunch", "slot_index": 0, "diet_type": "regular"}
    entries = [raw]
    monkeypatch.setattr(order_service, "_sheet_week_month_ids", lambda week: ["2026-10"])
    monkeypatch.setattr(order_service.menu_service, "get_menu_entries_for_facility", lambda month, facility: deepcopy(entries))
    monkeypatch.setattr(order_service, "_clip_entries_to_sheet_week_range", lambda rows, week: rows)
    result = order_service._build_position_menu_entries("2026-10", "fixture")
    assert physical.physical_row_ids_from_entries(result) == ["2026-10-07__昼__0"]
    entries.append(deepcopy(raw))
    with pytest.raises(ValueError, match="physical_menu_row_duplicate"):
        order_service._build_position_menu_entries("2026-10", "fixture")


def test_same_slot_variants_survive_digest_and_json_roundtrip(monkeypatch):
    import json
    from src.services.order_workflow_v2_service import _canonical_payload_json, _canonical_payload_digest
    rows = [{"name": name, "category": "主A", "menu_date": "2026-10-07", "daypart": "lunch", "slot_index": 0, "diet_type": diet} for diet, name in [("regular", "normal own"), ("soft", "soft own"), ("mixer", "mixer own")]]
    monkeypatch.setattr(order_service, "_sheet_week_month_ids", lambda week: ["2026-10"])
    monkeypatch.setattr(order_service.menu_service, "get_menu_entries_for_facility", lambda month, facility: deepcopy(rows))
    monkeypatch.setattr(order_service, "_clip_entries_to_sheet_week_range", lambda entries, week: entries)
    entries = order_service._build_position_menu_entries("2026-10", "fixture")
    assert len(entries) == 1
    assert "menu_name" not in entries[0]
    serialized = json.loads(_canonical_payload_json(entries))
    assert _canonical_payload_digest(serialized) == _canonical_payload_digest(entries)
    assert physical.physical_row_ids_from_entries(serialized) == ["2026-10-07__昼__0"]
    from src.services import master_order_form_template_service as master
    with pytest.raises(ValueError, match="physical_menu_common_name_unresolved"):
        master._normalize_week_menu_entries(entries=serialized, week_value="2026-10@2026-10-07~2026-10-07")
    with pytest.raises(ValueError, match="physical_menu_common_name_unresolved"):
        order_service._validate_reparse_lines_against_weekly_menu(
            lines=[{"menu_name": "soft own", "diet_type": "soft"}],
            week_id="2026-10", facility_id="fixture", ocr_rows=None,
            template={}, entries_override=serialized,
        )
    for diet, name in [("regular", "normal own"), ("soft", "soft own"), ("mixer", "mixer own")]:
        assert physical.entry_for_explicit_diet(serialized[0], diet)["menu_name"] == name
    with pytest.raises(ValueError, match="physical_menu_diet_variant_missing"):
        physical.entry_for_explicit_diet(serialized[0], "soft_mixer")
    damaged = deepcopy(serialized[0])
    damaged["diet_variants"]["soft"]["slot_index"] = 1
    with pytest.raises(ValueError, match="physical_menu_variant_identity_mismatch"):
        physical.physical_row_id_from_entry(damaged)
    damaged = deepcopy(serialized[0])
    damaged["diet_variants"]["soft"]["diet_type"] = "regular"
    with pytest.raises(ValueError, match="physical_menu_variant_invalid"):
        physical.physical_row_id_from_entry(damaged)
    damaged = deepcopy(serialized[0])
    damaged["diet_variants"]["soft"].pop("menu_name")
    with pytest.raises(ValueError, match="physical_menu_variant_name_required"):
        physical.physical_row_id_from_entry(damaged)
    rows.append(deepcopy(rows[0]))
    with pytest.raises(ValueError, match="physical_menu_row_duplicate"):
        order_service._build_position_menu_entries("2026-10", "fixture")


def test_normal_only_dated_entry_keeps_own_values(monkeypatch):
    raw = {"name": "own", "category": "主A", "menu_date": "2026-10-07", "daypart": "lunch", "slot_index": 0, "diet_type": "regular", "author_metadata": {"zero": 0, "null": None}}
    monkeypatch.setattr(order_service, "_sheet_week_month_ids", lambda week: ["2026-10"])
    monkeypatch.setattr(order_service.menu_service, "get_menu_entries_for_facility", lambda month, facility: [deepcopy(raw)])
    monkeypatch.setattr(order_service, "_clip_entries_to_sheet_week_range", lambda entries, week: entries)
    entry = order_service._build_position_menu_entries("2026-10", "fixture")[0]
    assert "diet_variants" not in entry
    assert physical.entry_for_explicit_diet(entry, "regular")["author_metadata"] == raw["author_metadata"]
    with pytest.raises(ValueError, match="physical_menu_diet_variant_missing"):
        physical.entry_for_explicit_diet(entry, "soft")
    raw.pop("diet_type")
    with pytest.raises(ValueError, match="physical_menu_diet_identity_required"):
        order_service._build_position_menu_entries("2026-10", "fixture")


@pytest.mark.parametrize("page", [None, 0, -1, False, 1.5, "1"])
def test_geometry_page_requires_authored_positive_integer(monkeypatch, page):
    configured = {"facility": {"fax_template": {"page": page}}, "policy": {}}
    monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda *args: [own_entry()])
    with pytest.raises(ValueError, match="geometry_template_page_required"):
        order_service.geometry_source_digest({"sha256": "0" * 64}, configured, {"facility_code": "fixture", "week_code": "2026-10"})


def test_geometry_actual_page_changes_digest_and_keeps_zero(monkeypatch):
    configured = {"facility": {"fax_template": {"page": 2}}, "policy": {"zero": 0}}
    identity = {"facility_code": "fixture", "week_code": "2026-10"}
    pdf = {"sha256": "0" * 64}
    monkeypatch.setattr(order_service, "_build_position_menu_entries_safe", lambda *args: [own_entry()])
    digest = order_service.geometry_source_digest(pdf, configured, identity)
    source = {"geometry_source_digest": digest, "physical_menu_entries": [own_entry()]}
    order_service._validate_geometry_source(source, pdf, configured, identity)
    assert order_service._authored_geometry_page(configured) == 2
    configured["facility"]["fax_template"]["page"] = 1
    with pytest.raises(ValueError, match="geometry_source_input_changed"):
        order_service._validate_geometry_source(source, pdf, configured, identity)


def test_owned_blank_and_zero_records_are_not_invented_quantities():
    from src.services import hakodate_ocr_evidence_service as evidence
    raw = [{"evidence_id": "zero", "text": 0, "normalized_value": 0, "engine_metadata": {"sheet_cell": "D1", "recognizer_output": {"value": 0}}}, {"evidence_id": "null", "text": "", "engine_metadata": {"sheet_cell": "D2", "recognizer_output": {"value": None}}}]
    records = evidence.evidence_from_records(raw, run_id="own", engine="own", source_scope="hakodate_cell_crop_batch", retain_empty_records=True)
    assert len(records) == 2
    assert records[0]["raw_text"] == "0"
    assert records[0]["normalized_value"] == "0"
    assert records[1]["raw_text"] == ""
    assert records[1]["engine_metadata"]["recognizer_output"]["value"] is None
    assert records[1]["normalized_value"] != "0"
