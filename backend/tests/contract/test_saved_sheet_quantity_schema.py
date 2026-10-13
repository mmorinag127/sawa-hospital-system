"""Real captured/saved component contracts; external recognition is not native proof."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import date
from hashlib import sha256
import json

import pytest
from sqlalchemy import event

from src.db import session_scope
from src.models.order import Order
from src.models.menu import MenuMaster, MonthlyMenuEntry, MonthlyMenuItem
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_workflow_state import OrderWorkflowState
from src.services import apply_gate_service, order_service as orders
from src.services import output_builder
from src.services import order_workflow_v2_service as workflow, workflow_state_service
from src.services import template_field_schema_service as schema
from tests.contract.test_orders_ocr_sheet_history_api import history_case, hospital_operator, _line
from tests.unit.test_disabled_facility_columns import _snapshot


pytestmark = pytest.mark.usefixtures("isolated_formal_schema")


def captured_sheet(history_case, *, output_ready=False):
    f = history_case(lines=[_line(quantity=2), _line(quantity=1, area="3F"),
                            {**_line(quantity=3), "diet_type": "soft"}])
    authored = json.loads(f.source.read_text())
    envelope = authored["facilities"][0]
    for config in (envelope, envelope["versions"][0]["facility"]):
        columns = config["fax_template"]["columns"]
        columns[4:4] = [
            {"index": 4, "source_index": 4, "name": "qty.regular_3f", "header": "regular 3F",
             "role": "quantity", "diet_type": "regular", "area_id": "3F"},
            {"index": 5, "source_index": 5, "name": "qty.soft_2f", "header": "soft 2F",
             "role": "quantity", "diet_type": "soft", "area_id": "2F"},
        ]
        columns[-1].update(index=6, source_index=6)
        if output_ready:
            config.update(
                areas=[{"area_id": "2F", "name": "Second floor"},
                       {"area_id": "3F", "name": "Third floor"}],
                daily_label_comparable_diet_types=[],
                bag_types=[{"bag_type_id": "UNIT-1000G", "label": "Unit 1000g", "max_qty": 1000, "unit": "g"}],
                packaging_policy={"split_key": ["facility", "date", "daypart", "menu_name", "diet_type", "area_id", "bag_type"]},
            )
    f.source.write_text(json.dumps(authored), encoding="utf-8")
    if output_ready:
        # Explicit synthetic serving units, authored before the real capture.
        with session_scope() as session:
            session.get(MonthlyMenuEntry, "seed-entry-2026-01-08-lunch-menu-a").category = "\u4e3b"
            session.add(MonthlyMenuEntry(id="schema-soft-menu", monthly_menu_id="2026-01",
                menu_date=date(2026, 1, 8), daypart="lunch", name="Menu A", slot_index=0,
                diet_type="soft", category="\u4e3b"))
            session.add(MenuMaster(id="schema-menu", name="Menu A",
                normalized_name=orders.menu_service._normalize_menu_name("Menu A"),
                qty_per_serving=100, unit_type="g", category="\u4e3b"))
            session.flush()
            for diet in ("regular", "soft"):
                session.add(MonthlyMenuItem(id="schema-menu-" + diet, monthly_menu_id="2026-01",
                    menu_master_id="schema-menu", name="Menu A", diet_type=diet, daypart="lunch",
                    category="\u4e3b", qty_per_serving=100, unit_type="g", master_resolution_mode="manual"))
    original = f.produce()["sheet"]
    assert original["rows"][0][original["fields"].index("qty.regular_2f")] == "2"
    return f, original


def exact_rows(actual, expected):
    for key in ("fields", "header", "rows", "row_ids"):
        assert actual[key] == expected[key], key
    assert [[type(cell) for cell in row] for row in actual["rows"]] == [
        [type(cell) for cell in row] for row in expected["rows"]]


def source_files(f):
    return {str(p.relative_to(f.root)): sha256(p.read_bytes()).hexdigest()
            for p in f.root.rglob("*") if p.is_file() and "sqlite" not in p.name}


@contextmanager
def unchanged_after_refusal(f, revision):
    before, files = _snapshot(f.engine), source_files(f)
    writes = []
    def observe(_connection, _cursor, statement, parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER")):
            writes.append((statement, parameters))
    event.listen(f.engine, "before_cursor_execute", observe)
    try:
        yield
    finally:
        event.remove(f.engine, "before_cursor_execute", observe)
        assert _snapshot(f.engine) == before
        assert source_files(f) == files
        # Existing SQLite ownership/CAS locks are self-updates, not state adoption.
        locks = {
            "UPDATE orders SET status=orders.status WHERE orders.id = ?": (f.id,),
            "UPDATE order_workflow_states SET revision=order_workflow_states.revision WHERE order_workflow_states.order_id = ? AND order_workflow_states.revision = ?": (f.id, revision),
        }
        for sql, parameters in writes:
            assert sql in locks, sql
            assert tuple(parameters) == locks[sql]


@pytest.mark.parametrize("fault,code", [
    ("missing", "quantity_template_field_missing"),
    ("unknown", "quantity_template_field_unknown"),
    ("duplicate", "quantity_template_alias_collision"),
])
def test_saved_schema_failure_is_readable_and_blocks_every_materialization_path(history_case, fault, code):
    f, original = captured_sheet(history_case)
    sheet = deepcopy(original)
    fields = sheet["fields"]
    index = fields.index("qty.soft_2f")
    if fault == "missing":
        for key in ("fields", "header"):
            sheet[key].pop(index)
        for row in sheet["rows"]:
            row.pop(index)
    else:
        fields[index] = "qty.unknown_2f" if fault == "unknown" else "qty.regular_2f"
    sheet["rows"][0][fields.index("qty.regular_2f")] = "8"
    sheet["rows"][0][fields.index("remarks")] = "  operator note  "
    saved = f.save(sheet)["saved_sheet"]
    before_files = source_files(f)
    with f.readonly():
        context = orders.get_current_sheet_context(f.id)
        exact_rows(context, sheet)
        assert code in context["blockers"]
        assert context["clean_saved_draft"] is True
        assert context["draft_record"]["blockers_json"] == []
        assert context["draft_payload"] == sheet
        gate = apply_gate_service.evaluate_sheet_gate_from_context(context)
        state = workflow_state_service.project_workflow_state(f.id, current_sheet_context=context)
        current, error = orders.get_ocr_sheet(f.id)
        assert error is None
        exact_rows(current, sheet)
        flat = orders.flatten_current_sheet_payload(f.id, context, include_meta=False)
        assert code in flat["blockers"]
        for result in (gate, state["apply_gate"], current):
            assert code in result["apply_blockers"]
            assert code in result["confirm_blockers"]
        for result in (state["apply_gate"], current):
            assert result["can_apply"] is False and result["can_confirm"] is False
        candidate = orders.build_confirm_materialization_candidate(f.id)
        assert candidate == {"source": "draft_sheet", "draft_id": saved["saved_sheet_id"],
                             "error": code, "line_count": 0, "lines": []}
    assert f.get("workflow-v2/sheet")["sheet"] == sheet
    public = f.get("workflow-v2")
    assert code in public["blockers"]
    assert public["menu_amount_resolution"]["status"] == "unresolved"
    assert public["menu_amount_resolution"]["blocker"] == code
    revision = public["revision"]
    with f.readonly():
        compare = f.client.post(f"/orders/{f.id}/quantity-assignment-compare", json={},
            headers={**f.actor.headers, "If-Match": str(revision)})
        assert compare.status_code == 200, compare.text
        summary = compare.json()["legacy"]["summary"]
        assert summary["can_apply"] is False and summary["can_confirm"] is False
        assert code in summary["apply_blockers"] and code in summary["confirm_blockers"]
    with unchanged_after_refusal(f, revision):
        assert orders.apply_latest_draft(f.id) == (None, code)
        with pytest.raises(orders.ConfirmMaterializationError) as refused:
            orders.confirm_order_authoritatively(f.id)
        assert refused.value.code == code
        response = f.client.post(f"/orders/{f.id}/workflow-v2/bagging",
            headers={**f.actor.headers, "If-Match": f'"{revision}"'})
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == code
        # Failed bagging produced no output lineage for confirm or aggregation.
        confirmed = f.client.post(f"/orders/{f.id}/workflow-v2/confirm",
            headers={**f.actor.headers, "If-Match": f'"{revision}"'})
        assert confirmed.status_code == 400, confirmed.text
        assert confirmed.json()["detail"] == "output_review_required"
        for suffix in ("bagging/confirm", "outputs/review"):
            retired = f.client.post(f"/orders/{f.id}/workflow-v2/{suffix}", headers=f.actor.headers)
            assert retired.status_code == 410
    assert source_files(f) == before_files


@pytest.mark.parametrize("value", ["8", 0, None, "", "  "])
def test_complete_schema_preserves_typed_operator_values_and_real_candidate(history_case, value):
    f, original = captured_sheet(history_case)
    sheet = deepcopy(original)
    sheet["rows"][0][sheet["fields"].index("qty.regular_2f")] = value
    sheet["rows"][0][sheet["fields"].index("remarks")] = "  operator note  "
    saved = f.save(sheet)["saved_sheet"]
    before_files = source_files(f)
    with f.readonly():
        context = orders.get_current_sheet_context(f.id)
        exact_rows(context, sheet)
        assert not any(code.startswith("quantity_template_") for code in context["blockers"])
        current, error = orders.get_ocr_sheet(f.id)
        assert error is None
        exact_rows(current, sheet)
        assert current["can_apply"] is True
        candidate = orders.build_confirm_materialization_candidate(f.id)
        assert candidate["error"] is None
        assert candidate["draft_id"] == saved["saved_sheet_id"]
        amounts = {(line["diet_type"], line["area_id"]): line["quantity_original"] for line in candidate["lines"]}
        assert amounts == ({("regular", "2F"): float(value), ("regular", "3F"): 2.0, ("soft", "2F"): 2.0}
                           if value in ("8", 0) else {("regular", "3F"): 2.0, ("soft", "2F"): 2.0})
        assert candidate["synchronous_write_input"]["draft"]["draft_sheet_json"] == sheet
        assert candidate["configured_input"]["facility"]["fax_template"]["template_id"]
    assert f.get("workflow-v2/sheet")["sheet"] == sheet
    assert source_files(f) == before_files


def test_unknown_programming_value_error_is_not_converted_to_domain_blocker(history_case, monkeypatch):
    f, original = captured_sheet(history_case)
    f.save(original)
    def programming_error(*args, **kwargs):
        raise ValueError("quantity_template_field_missing")
    monkeypatch.setattr(orders, "quantity_field_identities_for_fields", programming_error)
    with f.readonly():
        with pytest.raises(ValueError, match="^quantity_template_field_missing$") as context_error:
            orders.get_current_sheet_context(f.id)
        with pytest.raises(ValueError, match="^quantity_template_field_missing$") as candidate_error:
            orders.build_confirm_materialization_candidate(f.id)
        assert type(context_error.value) is ValueError
        assert type(candidate_error.value) is ValueError


def test_invalid_schema_revision_cannot_reuse_real_accepted_outputs_or_aggregate(history_case):
    f, original = captured_sheet(history_case, output_ready=True)
    sheet = deepcopy(original)
    sheet["rows"][0][sheet["fields"].index("qty.regular_2f")] = "8"
    sheet["rows"][0][sheet["fields"].index("remarks")] = "  operator note  "
    valid = f.save(sheet)["saved_sheet"]
    bagging = f.command("POST", "bagging")
    bag_id = bagging["bagging_result"]["bagging_result_id"]
    output_id = bagging["output_bundle"]["output_bundle_id"]
    assert bagging["bagging_result"]["source_saved_sheet_id"] == valid["saved_sheet_id"]
    assert bagging["output_bundle"]["source_bagging_result_id"] == bag_id
    assert bagging["bagging_result"]["summary"]["total_quantity"] == 12
    with f.readonly(), session_scope() as session:
        assert session.get(OrderBaggingResult, bag_id).payload_json == bagging["bagging_result"]
        assert session.get(OrderOutputBundle, output_id).payload_json == bagging["output_bundle"]
    accepted = f.command("POST", "confirm")
    assert accepted["confirmed_snapshot_id"]
    with f.readonly():
        aggregate = output_builder._weekly_weight_collect_rows(date(2026, 1, 8))
        assert sum(row["regular_quantity"] for row in aggregate.values()) == 10
        assert sum(row["soft_mixer_quantity"] for row in aggregate.values()) == 2

    invalid = deepcopy(sheet)
    index = invalid["fields"].index("qty.soft_2f")
    for key in ("fields", "header"):
        invalid[key].pop(index)
    for row in invalid["rows"]:
        row.pop(index)
    revised = f.save(invalid)
    assert revised["saved_sheet"]["saved_sheet_id"] != valid["saved_sheet_id"]
    assert revised["workflow"]["revision"] > accepted["workflow"]["revision"]
    assert f.get("workflow-v2/sheet")["sheet"] == invalid
    state = f.get("workflow-v2")
    assert "quantity_template_field_missing" in state["blockers"]
    with f.readonly(), session_scope() as session:
        assert session.get(OrderBaggingResult, bag_id) is None
        assert session.get(OrderOutputBundle, output_id) is None
        assert session.get(OrderWorkflowState, f.id).confirmed_snapshot_id is None
        assert output_builder._weekly_weight_collect_rows(date(2026, 1, 8)) == {}
        with pytest.raises(ValueError, match="^saved-sheet acceptance receipt missing$"):
            output_builder._prepare_output_context_for_bundle(f.id, include_bags=False,
                include_ocr_menu_meta=False, include_expanded_copy=True, require_invoice=False)
    with unchanged_after_refusal(f, state["revision"]):
        for suffix, code in (("confirm", "output_review_required"),
                             ("bagging", "quantity_template_field_missing")):
            response = f.client.post(f"/orders/{f.id}/workflow-v2/{suffix}",
                headers={**f.actor.headers, "If-Match": f'"{state["revision"]}"'})
            assert response.status_code == 400, response.text
            assert response.json()["detail"] == code
        assert orders.apply_latest_draft(f.id) == (None, "quantity_template_field_missing")


def test_invalid_schema_candidate_cannot_be_adopted_even_by_direct_materializer(history_case):
    f, original = captured_sheet(history_case)
    sheet = deepcopy(original)
    sheet["fields"][3] = "qty.unknown_2f"
    f.save(sheet)
    with f.readonly():
        candidate = orders.build_confirm_materialization_candidate(f.id)
        with session_scope() as session:
            with pytest.raises(orders.ConfirmMaterializationError) as refused:
                orders._materialize_confirmed_lines_from_candidate(session, session.get(Order, f.id), candidate)
        assert refused.value.code == "quantity_template_field_unknown"


def test_context_revalidates_real_source_after_schema_validation(history_case, monkeypatch):
    f, original = captured_sheet(history_case)
    f.save(original)
    validate = orders.quantity_field_identities_for_fields
    raced = {}
    def change_revision(template, fields):
        result = validate(template, fields)
        if not raced:
            with session_scope() as session:
                session.get(OrderWorkflowState, f.id).revision += 1
            raced["snapshot"] = _snapshot(f.engine)
        return result
    monkeypatch.setattr(orders, "quantity_field_identities_for_fields", change_revision)
    with pytest.raises(ValueError, match="^current_sheet_input_changed$"):
        orders.get_current_sheet_context(f.id)
    assert _snapshot(f.engine) == raced["snapshot"]


@pytest.mark.parametrize("fault,code", [
    ("missing_source", "quantity_template_source_required"),
    ("missing_identity", "quantity_template_identity_required"),
    ("duplicate_identity", "quantity_template_identity_duplicate"),
])
def test_authored_schema_validation_retains_domain_codes(fault, code):
    template = {"columns": [{"role": "quantity", "diet_type": "regular", "area_id": "2F"}]}
    if fault == "missing_source":
        template = None
    elif fault == "missing_identity":
        del template["columns"][0]["diet_type"]
    else:
        template["columns"].append({"role": "quantity", "diet_type": "regular", "area_id": "2F",
                                    "name_locked": True, "name": "qty.other_2f"})
    with pytest.raises(schema.QuantitySchemaError, match=f"^{code}$") as error:
        schema.quantity_field_identities_for_fields(template, ["qty.regular_2f"])
    assert isinstance(error.value, ValueError)
