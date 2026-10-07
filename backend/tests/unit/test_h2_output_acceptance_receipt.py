from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services import output_builder
from src.services.order_workflow_v2_service import _canonical_payload_digest


def test_first_bagging_producer_then_consumer_uses_same_captured_output(isolated_formal_schema):
    from copy import deepcopy
    from datetime import datetime
    from src.models.order import Order
    from src.services import order_workflow_v2_service as workflow_service

    order = Order(id="own-first-bagging", facility_code="fixture", week_code="2026-10", received_at=datetime(2026, 10, 7))
    draft = SimpleNamespace(id="sheet-own", template_version_id="template-own", base_evidence_run_id="evidence-own")
    configured = {"facility": {"facility_id": "fixture", "packaging_policy": {}, "label_profile": {}}, "policy": {"quantity_rules": {"zero_as_empty": False}}}
    candidate = {"configured_input": deepcopy(configured), "lines": [{"date": "2026-10-07", "daypart": "lunch", "menu_name": "own", "diet_type": "regular", "quantity_original": 0, "quantity_corrected": 0, "menu_qty_per_serving": 1, "menu_unit_type": "g"}]}
    # No prior artifact exists. Actual common enrichment and bag-row allocation run.
    with patch.object(output_builder, "_accepted_output_candidate", side_effect=AssertionError("producer cannot consume a prior artifact")), patch("src.services.config_service.get_facility_config", side_effect=AssertionError("current master recapture")), patch("src.services.config_service.load_ingest_policy", side_effect=AssertionError("current policy recapture")):
        artifact = workflow_service._build_bagging_result_payload(order=order, saved_sheet=draft, materialization_candidate=candidate, processing_input={"evidence": {"payload_json": {"own": 0}}, "output_ocr_menu_entries": [], "output_ocr_menu_defaults": {}})
    assert artifact["materialization_digest"] == _canonical_payload_digest(candidate)
    assert candidate["configured_input"] == configured
    assert candidate["lines"][0]["quantity_original"] == 0
    state = SimpleNamespace(secondary_actions_json={"workflow_v2": {"bagging_result": artifact}})
    with patch.object(output_builder.order_output_artifact_service, "enrich_workflow_meta_with_artifacts", side_effect=lambda session, meta: meta):
        accepted = output_builder._workflow_v2_materialization_candidate(None, {}, state, draft)
    configured["facility"]["bagging_exceptions"] = [{"type": "quantity_multiplier", "multiplier": 99}]
    expected_bags = output_builder.build_bag_rows_for_outputs({"id": order.id, "facility": "fixture", "week": "2026-10"}, accepted_candidate=accepted)
    with patch.object(output_builder, "_accepted_output_candidate", return_value=accepted), patch("src.services.config_service.get_facility_config", side_effect=AssertionError("changed master read")):
        assert output_builder.build_order_lines_for_outputs({"id": order.id}) == candidate["output_lines"]
        with patch.object(output_builder, "_collect_cached_menu_items_for_week", side_effect=AssertionError("late monthly read")), patch.object(output_builder, "_resolve_cached_menu_defaults", side_effect=AssertionError("late master read")):
            assert output_builder.build_bag_rows_for_outputs({"id": order.id, "facility": "fixture", "week": "2026-10"}) == expected_bags
    foreign_lines = [{**candidate["output_lines"][0], "quantity_original": 99}]
    with pytest.raises(ValueError, match="accepted output lines mismatch"):
        output_builder.build_bag_rows_for_outputs({"id": order.id}, order_lines=foreign_lines, accepted_candidate=accepted)


def test_output_consumes_exact_accepted_candidate_without_config_recapture():
    candidate = {"configured_input": {"facility": {"unknown": None}, "policy": {"zero": 0}}, "lines": [{"quantity": 0}]}
    artifact = {"source_saved_sheet_id": "sheet-own", "template_version_id": "template-own", "materialization_candidate": candidate,
                "materialization_digest": _canonical_payload_digest(candidate)}
    workflow = SimpleNamespace(secondary_actions_json={"workflow_v2": {"bagging_result": artifact}})
    draft = SimpleNamespace(id="sheet-own", template_version_id="template-own")
    with patch.object(output_builder.order_output_artifact_service, "enrich_workflow_meta_with_artifacts", side_effect=lambda session, meta: meta), patch("src.services.config_service.capture_authored_processing_config", side_effect=AssertionError("late config read")):
        result = output_builder._workflow_v2_materialization_candidate(None, {}, workflow, draft)
    assert result == candidate
    assert result is not candidate
    assert result["lines"][0]["quantity"] == 0


@pytest.mark.parametrize("change", ["missing", "wrong_sheet", "wrong_digest", "missing_policy", "missing_template"])
def test_output_missing_or_inconsistent_acceptance_receipt_stops(change):
    candidate = {"configured_input": {"facility": {}, "policy": {}}, "lines": [{"quantity": 0}]}
    artifact = {"source_saved_sheet_id": "sheet-own", "template_version_id": "template-own", "materialization_candidate": candidate,
                "materialization_digest": _canonical_payload_digest(candidate)}
    if change == "missing":
        artifact = None
    elif change == "wrong_sheet":
        artifact["source_saved_sheet_id"] = "other"
    elif change == "wrong_digest":
        artifact["materialization_digest"] = "other"
    elif change == "missing_template":
        del artifact["template_version_id"]
    else:
        del candidate["configured_input"]["policy"]
        artifact["materialization_digest"] = _canonical_payload_digest(candidate)
    workflow = SimpleNamespace(secondary_actions_json={"workflow_v2": {"bagging_result": artifact}})
    draft = SimpleNamespace(id="sheet-own", template_version_id="template-own")
    with patch.object(output_builder.order_output_artifact_service, "enrich_workflow_meta_with_artifacts", side_effect=lambda session, meta: meta), pytest.raises(ValueError):
        output_builder._workflow_v2_materialization_candidate(None, {}, workflow, draft)


def test_actual_delivery_authoritative_empty_does_not_read_current(monkeypatch):
    monkeypatch.setattr(output_builder, "_build_ocr_menu_meta", lambda *args, **kwargs: pytest.fail("empty receipt replaced by current OCR"))
    monkeypatch.setattr(output_builder, "_resolve_cached_menu_defaults", lambda *args: pytest.fail("current menu defaults"))
    rows = output_builder._build_delivery_rows({"id": "own", "facility": "fixture", "lines": []}, {"columns": []}, {"zero_as_empty": False}, {}, {"entries": [], "by_menu": {}, "entry_defaults": {}})
    assert rows == []


@pytest.mark.parametrize("area", ["2F", None, "X"])
@pytest.mark.parametrize("quantity", [7, 0, None])
def test_actual_nonempty_delivery_columns_use_only_authored_mapping(area, quantity):
    column = {"source": "quantity", "name": "肉禁3F misleading display", "diet_type": "regular", "area_id": area}
    line = {"date": "2026-10-07", "daypart": "lunch", "menu_name": "own", "menu_category": "主菜", "diet_type": "regular", "area_id": area, "quantity_original": quantity}
    configured = {"areas": [{"area_id": area, "name": "explicit authored area"}]}
    rows = output_builder._build_delivery_rows({"facility": "fixture", "lines": [line]}, {"columns": [column]}, {"zero_as_empty": False}, configured, {"entries": [], "entry_defaults": {}})
    if quantity is None:
        assert rows == []
    else:
        assert len(rows) == 1
        assert rows[0][column["name"]] == quantity
    signature = output_builder._invoice_quantity_signature(column, {})
    assert signature == ("regular", None if area is None else str(area))
    invoice = output_builder._build_invoice_template_from_fax_columns({"fax_template": {"columns": [{**column, "role": "quantity"}]}})
    quantity_columns = [col for col in invoice["columns"] if col.get("source") == "quantity"]
    assert len(quantity_columns) == 1
    assert quantity_columns[0]["area_id"] == (None if area is None else str(area))


@pytest.mark.parametrize("change", ["missing_diet", "null_diet", "invalid_area", "duplicate", "duplicate_name"])
def test_actual_delivery_rejects_unresolved_or_ambiguous_nonempty_mapping(change):
    from copy import deepcopy
    columns = [{"source": "quantity", "name": "常食2F", "diet_type": "regular", "area_id": "2F"}]
    if change == "missing_diet":
        del columns[0]["diet_type"]
    elif change == "null_diet":
        columns[0]["diet_type"] = None
    elif change == "invalid_area":
        columns[0]["area_id"] = []
    else:
        columns.append(deepcopy(columns[0]))
        if change == "duplicate":
            columns[1]["name"] = "another name"
        else:
            columns[1]["area_id"] = "3F"
    line = {"date": "2026-10-07", "menu_name": "own", "diet_type": "regular", "area_id": "2F", "quantity": 7}
    with pytest.raises(ValueError, match="delivery_quantity"):
        output_builder._build_delivery_rows({"lines": [line]}, {"columns": columns}, {"zero_as_empty": False}, {}, {"entries": [], "entry_defaults": {}})
    if not change.startswith("duplicate"):
        with pytest.raises(ValueError, match="delivery_quantity"):
            output_builder._invoice_quantity_signature(columns[0], {})
        with pytest.raises(ValueError, match="delivery_quantity"):
            output_builder._fax_quantity_signature({**columns[0], "role": "quantity"}, {})


@pytest.mark.parametrize("area_definition", [{}, {"area_id": None}, {"area_id": ""}])
def test_authored_optional_area_aggregates_without_display_name_inference(area_definition):
    column = {"source": "quantity", "name": "軟菜3F misleading", "diet_type": "soft", **area_definition}
    lines = [{"date": "2026-10-07", "menu_name": "own", "diet_type": "soft", "area_id": area, "quantity": qty} for area, qty in [("2F", 3), ("3F", 5), (None, 2), ("X", 4)]]
    rows = output_builder._build_delivery_rows({"lines": lines}, {"columns": [column]}, {"zero_as_empty": False}, {}, {"entries": [], "entry_defaults": {}})
    assert len(rows) == 1
    assert rows[0][column["name"]] == 14
    assert output_builder._invoice_quantity_signature(column, {}) == ("soft", None)


def test_generated_invoice_aggregate_and_explicit_x_preserve_both_quantities(monkeypatch, tmp_path):
    facility = {"fax_template": {"columns": [{"role": "quantity", "diet_type": "soft"}, {"role": "quantity", "diet_type": "soft", "area_id": "X"}]}}
    invoice = output_builder._build_invoice_template_from_fax_columns(facility)
    columns = [col for col in invoice["columns"] if col.get("source") == "quantity"]
    assert len(columns) == 2 and columns[0]["name"] != columns[1]["name"]
    lines = [{"date": "2026-10-07", "menu_name": "own", "diet_type": "soft", "area_id": area, "quantity": qty} for area, qty in [("2F", 7), ("X", 3)]]
    rows = output_builder._build_delivery_rows({"lines": lines}, invoice, {"zero_as_empty": False}, facility, {"entries": [], "entry_defaults": {}})
    assert rows[0][columns[0]["name"]] == 10
    assert rows[0][columns[1]["name"]] == 3
    exported = []
    monkeypatch.setattr(output_builder.pd.DataFrame, "to_excel", lambda frame, *args, **kwargs: exported.append(frame.copy()))
    output_builder._write_delivery_note(tmp_path / "delivery.xlsx", rows, columns, None, False)
    assert list(exported[0].iloc[0]) == [10, 3]
    assert list(exported[0].columns) == [col["header"] for col in columns]


def test_requested_delivery_template_failure_never_exports_alternative(monkeypatch, tmp_path):
    def unavailable(uri):
        raise OSError("requested template unavailable")
    monkeypatch.setattr(output_builder, "load_bytes_from_uri", unavailable)
    monkeypatch.setattr(output_builder.pd.DataFrame, "to_excel", lambda *args, **kwargs: pytest.fail("alternative delivery write"))
    with pytest.raises(ValueError, match="delivery_template_load_failed"):
        output_builder._write_delivery_note(tmp_path / "delivery.xlsx", [{"own": 0}], [{"name": "own"}], "gs://own/template.xlsx", False)


@pytest.mark.parametrize("columns", [[], [{"source": "menu", "name": "menu"}]])
@pytest.mark.parametrize("quantity", [7, 0])
def test_nonnull_delivery_quantity_without_mapping_stops(columns, quantity):
    with pytest.raises(ValueError, match="mapping_unresolved"):
        output_builder._build_delivery_rows({"lines": [{"menu_name": "own", "diet_type": "regular", "quantity": quantity}]}, {"columns": columns}, {"zero_as_empty": False}, {}, {"entries": [], "entry_defaults": {}})


def test_existing_no_fried_forbidden_other_business_aggregate_is_retained():
    column = {"source": "quantity", "name": "authored combined", "diet_type": "forbidden_other"}
    lines = [{"menu_name": "own", "diet_type": diet, "area_id": "X", "quantity": qty} for diet, qty in [("no_fried", 7), ("forbidden_other", 3)]]
    rows = output_builder._build_delivery_rows({"lines": lines}, {"columns": [column]}, {"zero_as_empty": False}, {}, {"entries": [], "entry_defaults": {}})
    assert rows[0]["authored combined"] == 10


@pytest.mark.parametrize("consumer", ["bundle", "preview", "delivery_preview", "outputs"])
def test_actual_delivery_consumers_empty_quantity_mapping_stop_before_output_write(monkeypatch, consumer):
    line = {"date": "2026-10-07", "menu_name": "own", "diet_type": "regular", "quantity_original": 7, "quantity_corrected": 7}
    candidate = {"configured_input": {"facility": {"facility_id": "fixture", "fax_template": {"columns": []}, "invoice_template": {"columns": []}}, "policy": {"quantity_rules": {"zero_as_empty": False}}}, "lines": [line], "output_lines": [line], "output_menu_entries": [], "output_ocr_menu_entries": [], "output_ocr_menu_defaults": {}}
    monkeypatch.setattr(output_builder, "get_order_by_id", lambda oid: {"id": oid, "facility": "fixture", "week": "2026-10"})
    monkeypatch.setattr(output_builder, "_accepted_output_candidate", lambda order: candidate)
    monkeypatch.setattr(output_builder, "_write_label_csv", lambda *args: pytest.fail("output before quantity gate"))
    with pytest.raises(ValueError, match="mapping_unresolved"):
        if consumer == "bundle":
            ctx = output_builder._prepare_output_context("own", include_bags=False)
            output_builder._build_delivery_rows_for_bundle(
                ctx["order_for_outputs"],
                ctx["invoice_template"],
                ctx["quantity_rules"],
                ctx["facility_config"],
                ctx["ocr_menu_meta"],
                allow_ocr_menu_meta=False,
            )
        elif consumer == "preview":
            output_builder.build_output_preview("own", "delivery")
        elif consumer == "delivery_preview":
            output_builder.build_delivery_preview("own", include_diagnostics=False)
        else:
            output_builder.build_outputs("own")


def test_actual_delivery_header_and_reference_mapper_never_pick_name_alias():
    from openpyxl import Workbook
    workbook = Workbook()
    ws = workbook.active
    ws.cell(1, 5, "常食2F")
    column = {"source": "quantity", "name": "own soft aggregate", "header": "常食2F", "diet_type": "soft"}
    assert output_builder._build_delivery_column_map(ws, 1, [column]) == {"own soft aggregate": 5}
    # Reference header text itself is deliberately misleading; authored diet
    # and absent-area aggregate semantics still win.
    ws.cell(9, 5, "常食2F")
    mapped = output_builder._daily_delivery_column_meta(ws, authored_columns=[column])
    quantity = [item for item in mapped if item.get("source") == "quantity"]
    assert len(quantity) == 1
    assert output_builder._invoice_quantity_signature(quantity[0], {}) == ("soft", None)
    ws.cell(1, 6, "常食2F extra")
    assert output_builder._build_delivery_column_map(ws, 1, [column]) == {"own soft aggregate": 5}
    ws.cell(1, 6, "常食2F")
    with pytest.raises(ValueError, match="header_mapping_unresolved"):
        output_builder._build_delivery_column_map(ws, 1, [column])
    with pytest.raises(ValueError, match="header_mapping_unresolved"):
        output_builder._daily_delivery_column_meta(ws, authored_columns=[])
    workbook.close()


def test_existing_authored_floor_invoice_and_aggregate_exact_coexistence():
    import json
    from pathlib import Path
    from copy import deepcopy
    from src.services import config_validator
    authored = json.loads((Path(__file__).parents[2] / "src/data/facility_master.template.json").read_text())
    facilities = [facility for facility in authored["facilities"] if facility["facility_id"] == "FAC00003"]
    assert len(facilities) == 1
    template = deepcopy(facilities[0]["invoice_template"])
    errors = []
    config_validator._validate_invoice_columns(template["columns"], "invoice_template.columns", errors)
    assert errors == []
    quantities = [("regular", "2F", 3), ("regular", "3F", 5), ("soft", "2F", 7), ("soft", "3F", 11), ("mixer", "2F", 0), ("mixer", "3F", 13), ("soft", None, 2), ("soft", "X", 4), ("soft", "2F", None)]
    lines = [{"date": "2026-10-07", "daypart": "lunch", "menu_name": "own", "diet_type": diet, "area_id": area, "quantity": qty} for diet, area, qty in quantities]
    rows = output_builder._build_delivery_rows({"lines": lines}, template, {"zero_as_empty": False}, {}, {"entries": [], "entry_defaults": {}})
    assert len(rows) == 1
    assert {name: rows[0][name] for name in ["常食2F", "常食3F", "軟菜", "ミキサー2F", "ミキサー3F"]} == {"常食2F": 3, "常食3F": 5, "軟菜": 24, "ミキサー2F": 0, "ミキサー3F": 13}
    template["columns"].append({"source": "quantity", "name": "soft exact2F", "diet_type": "soft", "area_id": "2F"})
    rows = output_builder._build_delivery_rows({"lines": lines}, template, {"zero_as_empty": False}, {}, {"entries": [], "entry_defaults": {}})
    assert rows[0]["軟菜"] == 24
    assert rows[0]["soft exact2F"] == 7
    del template["columns"][-1]["diet_type"]
    with pytest.raises(ValueError, match="delivery_quantity_diet_required"):
        output_builder._build_delivery_rows({"lines": lines}, template, {"zero_as_empty": False}, {}, {"entries": [], "entry_defaults": {}})


def test_label_missing_amount_does_not_substitute_servings_or_diet_category():
    bag = {"date": "2026-10-07", "menu_name": "own", "diet_type": "regular", "quantity": 7, "menu_qty_per_serving": None, "menu_unit_type": None, "menu_category": None}
    label = output_builder._label_payload_jp(bag)
    assert label["内容量"] == ""
    assert label["内容詳細"] == ""
    assert label["メニュー"] == ""
    legacy = output_builder._label_payload_legacy(bag, {}, "fixture")
    assert legacy["menu_category"] is None
    assert legacy["quantity"] == 7


def test_required_numeric_label_amount_without_unit_stops():
    with pytest.raises(ValueError, match="accepted_label_unit_required"):
        output_builder._label_payload_jp({"quantity": 7, "menu_qty_per_serving": 0, "menu_unit_type": None})


def test_actual_delivery_context_duplicate_preserves_producer_first(monkeypatch):
    from copy import deepcopy
    entries = [{"date": "2026-10-07", "menu_name": "own", "quantity_map": {"regular": 0}}, {"date": "2026-10-07", "menu_name": "own", "quantity_map": {"regular": 99}}]
    candidate = {"configured_input": {"facility": {"fax_template": {"columns": []}}, "policy": {"quantity_rules": {"zero_as_empty": False}}}, "output_lines": [], "output_ocr_menu_entries": entries, "output_ocr_menu_defaults": {}, "lines": []}
    artifact = {"source_saved_sheet_id": "own-sheet", "template_version_id": "own-template", "materialization_candidate": candidate, "materialization_digest": _canonical_payload_digest(candidate)}
    state = SimpleNamespace(secondary_actions_json={"workflow_v2": {"bagging_result": artifact}})
    sheet = SimpleNamespace(id="own-sheet", template_version_id="own-template")
    monkeypatch.setattr(output_builder.order_output_artifact_service, "enrich_workflow_meta_with_artifacts", lambda session, meta: meta)
    def accepted(order):
        return output_builder._workflow_v2_materialization_candidate(None, order, state, sheet)
    monkeypatch.setattr(output_builder, "_accepted_output_candidate", accepted)
    monkeypatch.setattr(output_builder, "get_order_by_id", lambda oid: {"id": oid, "facility": "fixture", "week": "2026-10"})
    monkeypatch.setattr(output_builder, "_resolve_cached_menu_defaults", lambda *args: pytest.fail("consumer current master"))
    context = output_builder._prepare_output_context("own", include_bags=False)
    assert list(context["ocr_menu_meta"]["by_menu"].values())[0] == entries[0]
    delivery = output_builder._build_delivery_rows(context["order_for_outputs"], context["invoice_template"], context["quantity_rules"], context["facility_config"], context["ocr_menu_meta"])
    assert len(delivery) == 1
    assert delivery[0]["menu_name"] == "own"
    candidate["output_ocr_menu_entries"] = []
    artifact["materialization_digest"] = _canonical_payload_digest(candidate)
    empty_context = output_builder._prepare_output_context("own", include_bags=False)
    assert empty_context["ocr_menu_meta"]["entries"] == []
    assert output_builder._build_delivery_rows(empty_context["order_for_outputs"], empty_context["invoice_template"], empty_context["quantity_rules"], empty_context["facility_config"], empty_context["ocr_menu_meta"]) == []


@pytest.mark.parametrize("per_quantity", [0, 7, None])
def test_final_label_formatter_preserves_frozen_values_without_menu_defaults(monkeypatch, per_quantity):
    from src.services import menu_service
    monkeypatch.setattr(menu_service, "resolve_menu_defaults", lambda *args: pytest.fail("late menu master read"))
    bag = {"menu_name": "煮込みハンバーグ", "date": "2026-10-07", "quantity": 5, "menu_qty_per_serving": per_quantity, "menu_unit_type": "g", "menu_temp_type": None, "menu_category": None, "daypart": None, "bag_max_qty": 9}
    row = output_builder._label_payload_jp(bag)
    assert row["内容詳細"] == ("" if per_quantity is None else f"{per_quantity}g")
    assert row["温・冷"] == ""
    assert row["時間"] == ""
    assert row["メニュー"] == ""
    assert bag["quantity"] == 5
    assert bag["bag_max_qty"] == 9
    assert bag["menu_qty_per_serving"] is per_quantity
