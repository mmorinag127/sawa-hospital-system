"""Zero-label quantity boundaries and real accepted-candidate CSV consumption.

The reused owned SQLite/JSON/projection/PDF fixture manually seeds OCR evidence;
this is not native OCR, HTTP/auth, formal PG, operator registration or live proof.
Float-fault objects and candidate-copy mutations are component fault inputs only.
No quantity reader, validator, config capture or business result is mocked.
"""

from copy import deepcopy
import csv
from datetime import date
from hashlib import sha256

import pytest

from test_modernization_real_bagging_confirmation import actual_bagging, owned_bagging
from test_modernization_workflow_surface_parity import database_snapshot, surface as surface
from src.services import output_builder as output


DAY = date(2026, 3, 1)  # Explicit component input, not a production date default.


def component_line(**quantities):
    return {
        "date": DAY, "daypart": "朝", "menu_name": "component-menu",
        "menu_category": "主", "diet_type": "regular", "area_id": "X",
        "source_diet_type": "regular", "menu_unit_type": "g",
        "menu_qty_per_serving": 100, "unknown": {"null": None, "empty": ""},
        **quantities,
    }


def component_context(lines):
    return {
        "order_for_outputs": {"id": "COMPONENT-ORDER", "facility": "F"},
        "order_lines": lines, "unknown": {"null": None, "empty": ""},
    }


@pytest.mark.parametrize("quantities,raw,number", [
    pytest.param({"quantity_original": 0}, 0, 0.0, id="integer-zero"),
    pytest.param({"quantity_original": 0.0}, 0.0, 0.0, id="float-zero"),
    pytest.param({"quantity_original": "0"}, "0", 0.0, id="string-zero"),
    pytest.param({"quantity_original": 7, "quantity_corrected": 0}, 0, 0.0, id="corrected-zero"),
    pytest.param({"quantity_original": 7, "quantity_corrected": "0"}, "0", 0.0, id="corrected-string-zero"),
    pytest.param({"quantity_original": 0, "quantity_corrected": 7}, 7, 7.0, id="corrected-seven"),
    pytest.param({"quantity_original": 7}, 7, 7.0, id="original-seven"),
    pytest.param({"quantity_original": None, "quantity_corrected": None}, None, None, id="both-none"),
    pytest.param({"quantity": 0}, None, None, id="legacy-quantity-not-authority"),
    pytest.param({"quantity_original": None, "unknown_quantity": 0}, None, None, id="unknown-zero-not-authority"),
])
def test_raw_authority_priority_and_zero_only_supplement(quantities, raw, number):
    line = component_line(**quantities)
    ctx = component_context([line])
    before = deepcopy(ctx)
    group = {"bags": [], "facility_config": {"daily_label_comparable_diet_types": []}}
    selected, numeric = output._daily_label_line_quantity(line)
    assert selected == raw and type(selected) is type(raw)
    assert numeric == number
    output._append_zero_quantity_label_bags(group, ctx, DAY)
    if number == 0:
        assert len(group["bags"]) == 1
        assert group["bags"][0]["quantity"] == raw
        assert type(group["bags"][0]["quantity"]) is type(raw)
    else:
        assert group["bags"] == []
    if raw is None:
        with pytest.raises(ValueError, match="^label_quantity_unresolved$"):
            output._line_to_label_bag("COMPONENT-ORDER", "F", line)
    else:
        bag = output._line_to_label_bag("COMPONENT-ORDER", "F", line)
        assert bag["quantity"] == raw and type(bag["quantity"]) is type(raw)
        assert bag["source_diet_type"] == line["source_diet_type"]
    assert ctx == before


class FloatFault:
    def __init__(self, error):
        self.error = error

    def __float__(self):
        raise self.error


@pytest.mark.parametrize("value", [
    pytest.param("", id="empty"), pytest.param(" ", id="whitespace"),
    pytest.param("garbage", id="invalid-string"),
    pytest.param(True, id="true"), pytest.param(False, id="false"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="infinity"),
    pytest.param(FloatFault(TypeError("component-float")), id="float-type-error"),
    pytest.param(FloatFault(OverflowError("component-float")), id="float-overflow"),
])
def test_invalid_values_stop_all_shared_quantity_entries(value):
    line = component_line(quantity_original=value)
    ctx = component_context([line])
    line_before = dict(line)
    group = {"bags": [], "unknown": {"null": None, "empty": ""}}
    before = deepcopy(group)
    for invoke in (
        lambda: output._daily_label_line_quantity(line),
        lambda: output._line_to_label_bag("COMPONENT-ORDER", "F", line),
        lambda: output._append_zero_quantity_label_bags(group, ctx, DAY),
    ):
        with pytest.raises(ValueError, match="^label_quantity_unresolved$"):
            invoke()
        assert group == before
        assert line.keys() == line_before.keys()
        assert all(line[key] is line_before[key] for key in line)
        assert ctx["order_lines"] == [line]
        assert ctx["unknown"] == {"null": None, "empty": ""}


def test_unknown_float_runtime_error_propagates_original_exception():
    error = RuntimeError("component-float-runtime")
    line = component_line(quantity_original=FloatFault(error))
    ctx = component_context([line])
    group = {"bags": [], "unknown": {"null": None, "empty": ""}}
    before = deepcopy(group)
    for invoke in (
        lambda: output._daily_label_line_quantity(line),
        lambda: output._line_to_label_bag("COMPONENT-ORDER", "F", line),
        lambda: output._append_zero_quantity_label_bags(group, ctx, DAY),
    ):
        with pytest.raises(RuntimeError) as caught:
            invoke()
        assert caught.value is error
        assert group == before
        assert line["quantity_original"].error is error


@pytest.mark.parametrize("existing", [False, True], ids=["missing-bags", "existing-positive-bag"])
def test_later_invalid_line_never_partially_changes_group(existing):
    group = {"unknown": {"null": None, "empty": ""}}
    if existing:
        group["bags"] = [{**component_line(quantity_original=7), "quantity": 7,
                          "menu_name": "existing-positive", "metadata": {"source": "preserve"}}]
    ctx = component_context([
        component_line(quantity_original=0),
        {**component_line(quantity_original=""), "menu_name": "later-invalid"},
    ])
    before, ctx_before = deepcopy(group), deepcopy(ctx)
    with pytest.raises(ValueError, match="^label_quantity_unresolved$"):
        output._append_zero_quantity_label_bags(group, ctx, DAY)
    assert group == before
    assert ctx == ctx_before


def test_date_filter_and_duplicate_keys_preserve_existing_bag_metadata():
    line = component_line(quantity_original=0)
    existing = output._line_to_label_bag("COMPONENT-ORDER", "F", line)
    existing["metadata"] = {"source": "preserve", "null": None, "empty": ""}
    group = {"bags": [existing]}
    next_line = {**line, "menu_name": "new-zero"}
    ctx = component_context([
        line, {**line, "date": date(2026, 3, 2), "quantity_original": "invalid-outside-date"},
        next_line, deepcopy(next_line), {**line, "menu_name": "negative", "quantity_original": -7},
    ])
    before, ctx_before = deepcopy(group), deepcopy(ctx)
    output._append_zero_quantity_label_bags(group, ctx, DAY)
    expected_new = output._line_to_label_bag("COMPONENT-ORDER", "F", next_line)
    expected_new["area_name"] = ""
    assert group["bags"] == [before["bags"][0], expected_new]
    assert group["bags"][1]["area_name"] == ""
    assert ctx == ctx_before


def test_real_accepted_csv_preserves_seven_zero_and_all_owned_inputs(owned_bagging, monkeypatch, capsys):
    fixture = owned_bagging
    bagged = actual_bagging(fixture)
    candidate = bagged["bagging_result"]["materialization_candidate"]
    candidate_before = deepcopy(candidate)
    assert [(line["menu_name"], line["quantity_original"]) for line in candidate["lines"]] == [
        ("選択献立0", 7), ("選択献立3", 0),
    ]
    days = {output._ensure_date(line["date"]) for line in candidate["lines"]}
    assert len(days) == 1 and None not in days
    target = days.pop()
    saved = fixture.saved()
    assert [row[4] for row in saved["rows"]] == ["7", "", "", "0"]
    originals = {path: path.read_bytes() for path in (fixture.pdf, fixture.source, fixture.master_path)}
    before = database_snapshot()
    monkeypatch.setattr(output, "OUTPUT_DIR", fixture.output)
    path, _filename = output.build_label_csv_for_order(fixture.order_id, target_date=target)
    with path.open(encoding="cp932", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert {row["商品名１"]: (row[""], row["内容量"], row["内容詳細"]) for row in rows} == {
        "選択献立0": ("7人前", "700g", "100g"),
        "選択献立3": ("0人前", "0g", "100g"),
    }
    assert database_snapshot() == before
    assert fixture.saved() == saved
    assert candidate == candidate_before
    assert all(path.read_bytes() == data for path, data in originals.items())
    with capsys.disabled():
        print(f"zero-label artifact={path} sha256={sha256(path.read_bytes()).hexdigest()}")


@pytest.mark.parametrize("fault", [None, ""], ids=["missing-quantity", "invalid-empty-quantity"])
def test_real_candidate_copy_fault_is_component_only_and_never_writes(owned_bagging, fault):
    fixture = owned_bagging
    bagged = actual_bagging(fixture)
    candidate = bagged["bagging_result"]["materialization_candidate"]
    baseline = deepcopy(candidate)
    before = database_snapshot()
    saved = fixture.saved()
    originals = {path: path.read_bytes() for path in (fixture.pdf, fixture.source, fixture.master_path)}
    changed = deepcopy(candidate)
    # Only the copy's zero row is corrupted; no persisted receipt is relabeled.
    assert changed["lines"][1]["quantity_original"] == 0
    changed["lines"][1]["quantity_original"] = fault
    changed["lines"][1]["quantity_corrected"] = None
    ctx = {"order_for_outputs": {"id": fixture.order_id, "facility": "F"},
           "order_lines": changed["lines"]}
    ctx_before = deepcopy(ctx)
    target = output._ensure_date(changed["lines"][1]["date"])
    assert target is not None
    group = {"bags": [], "facility_config": changed["configured_input"]["facility"]}
    group_before = deepcopy(group)
    if fault is None:
        output._append_zero_quantity_label_bags(group, ctx, target)
    else:
        with pytest.raises(ValueError, match="^label_quantity_unresolved$"):
            output._append_zero_quantity_label_bags(group, ctx, target)
    assert group == group_before
    assert ctx == ctx_before
    assert candidate == baseline
    assert database_snapshot() == before
    assert fixture.saved() == saved
    assert all(path.read_bytes() == data for path, data in originals.items())
