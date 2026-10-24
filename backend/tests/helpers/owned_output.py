"""Authored output components using real registration, selection, save and confirm."""
import pytest

from datetime import date
from src.services import output_builder
from src.services import menu_service, order_service, order_workflow_v2_service as workflow
from src.services.sheet_week_service import parse_sheet_week_value
from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order


@pytest.fixture
def owned_outputs(monkeypatch, tmp_path, isolated_formal_schema):
    class Inputs:
        def __init__(self):
            self.created = []

        def assert_fresh(self):
            assert not self.created, "each test owns a fresh schema; never clear retained inputs"

        def create_order_from_ingest(self, payload, *, lines, recipe=None,
                                     declared_quantity_pairs=None, accessory_masters=None, physical_slot=None,
                                     physical_category=None, prepared_menu_sources=False,
                                     daily_label_comparable_diet_types=None):
            assert lines, "explicit authored lines/identity required"
            assert isinstance(payload.facility_hint, str) and payload.facility_hint.strip(), "explicit authored facility required"
            name, day, daypart = (lines[0][key] for key in ("menu_name", "date", "daypart"))
            assert name and day and daypart, "explicit authored date/menu/daypart required"
            assert all((line["menu_name"], line["date"], line["daypart"]) ==
                       (name, day, daypart) for line in lines), "one exact date/menu/daypart per recipe"
            pairs = [(line["diet_type"], line["area_id"]) for line in lines]
            assert len(set(pairs)) == len(pairs), "duplicate authored cell identity must not lose quantities"
            if declared_quantity_pairs is not None:
                assert len(set(declared_quantity_pairs)) == len(declared_quantity_pairs)
                assert set(pairs) <= set(declared_quantity_pairs), "authored quantity column missing"
            _, start, end = parse_sheet_week_value(payload.week_hint)
            assert start is not None and end is not None, "explicit authored period required"
            assert start <= date.fromisoformat(day) <= end, "authored line outside explicit period"
            slots = None if physical_slot is None else {name: physical_slot}
            if recipe is None:
                # Resolve the same facility-scoped monthly source used by outputs.
                month = day[:7]
                items = menu_service.get_menu_items_for_facility(month, payload.facility_hint)
                index = {}
                for item in items:
                    for alias in output_builder._build_menu_name_aliases(item["name"]):
                        index.setdefault(output_builder._normalize_menu_key(alias), []).append(item)
                selected = [output_builder._select_menu_item_for_line(line, index) for line in lines]
                assert all(item is not None for item in selected), "explicit monthly recipe missing for line context"
                recipes = {tuple((key, item.get(key)) for key in
                    ("qty_per_serving", "unit_type", "category", "temp_type")) for item in selected}
                assert len(recipes) == 1, "distinct diet recipes require separate authored input groups"
                (resolved,) = recipes
                recipe = dict(resolved)
                entries = menu_service.get_menu_entries_for_facility(month, payload.facility_hint)
                entries = [entry for entry in entries
                    if entry["name"] == name and entry["menu_date"] == day
                    and output_builder._normalize_delivery_daypart(entry["daypart"]) ==
                        output_builder._normalize_delivery_daypart(daypart)
                    and entry["diet_type"] == "regular"]
                if entries:
                    assert len(entries) == 1, "physical monthly menu identity is ambiguous"
                    (entry,) = entries
                    assert type(entry["slot_index"]) is int
                    if slots is not None:
                        assert slots[name] == entry["slot_index"]
                    slots = {name: entry["slot_index"]}
                    if physical_category is not None:
                        assert physical_category == entry["category"]
                    physical_category = entry["category"]
                else:
                    assert slots is not None, "explicit authored physical slot required; never borrow another scope"
            assert recipe["qty_per_serving"] is not None and recipe["unit_type"]
            assert slots is not None and type(slots[name]) is int and slots[name] >= 0
            registered = accepted_bagging_order(monkeypatch, tmp_path, lines,
                facility_id=payload.facility_hint, message_id=payload.message_id,
                menu_qty_per_serving=recipe["qty_per_serving"],
                menu_unit_type=recipe["unit_type"], menu_category=recipe["category"],
                menu_temp_type=recipe.get("temp_type"),
                physical_menu_category=physical_category,
                prepared_menu_sources=prepared_menu_sources,
                daily_label_comparable_diet_types=daily_label_comparable_diet_types,
                physical_slot_indexes=slots, week_start=start.isoformat(), week_end=end.isoformat(),
                accessory_masters=accessory_masters,
                declared_quantity_pairs=declared_quantity_pairs, confirm=True)
            self.created.append(registered["id"])
            return order_service.get_order_by_id(registered["id"])

        def assert_confirmed(self, order_id):
            assert order_service.get_order_by_id(order_id)["status"] == "確定"

        def save_ocr_sheet_exact(self, order_id, *, fields, rows, row_ids=None, **kwargs):
            order = order_service.get_order_by_id(order_id)
            sheet = {**kwargs, "fields": fields, "rows": rows,
                     "week_id": kwargs.get("week_id", order["week_value"])}
            if row_ids is not None:
                sheet["row_ids"] = row_ids
            for operation, arguments in (
                (workflow.run_sheet_anomaly_review, {"sheet": sheet, "use_llm": False}),
                (workflow.confirm_sheet_review, {"sheet": sheet}),
                (workflow.save_sheet, {"sheet": sheet}),
            ):
                current, error = workflow.get_workflow_readonly(order_id)
                assert error is None
                saved, error = operation(order_id=order_id,
                    expected_revision=current["revision"], **arguments)
                assert error is None, error
            return saved, None

        def persist_sheet_draft(self, *, order_id, draft_sheet_json, **kwargs):
            sheet = {key: value for key, value in draft_sheet_json.items() if key != "order_id"}
            return self.save_ocr_sheet_exact(order_id, **sheet)[0]

        def confirm_order(self, order_id):
            current, error = workflow.get_workflow_readonly(order_id)
            assert error is None
            bagging, error = workflow.run_bagging(order_id,
                expected_revision=current["revision"])
            assert error is None, error
            confirmed, error = workflow.final_confirm(order_id,
                expected_revision=bagging["workflow"]["revision"])
            assert error is None, error
            return confirmed

    return Inputs()
