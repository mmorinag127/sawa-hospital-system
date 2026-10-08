"""Internal metadata validation, not upload/receipt or operator proof."""
import ast
from copy import deepcopy
import inspect

import pytest

from src.services import output_builder


@pytest.mark.parametrize("entries", ["missing", None, []])
@pytest.mark.parametrize("defaults", [None, [], "invalid"])
def test_supplied_metadata_requires_dictionary_defaults(entries, defaults):
    metadata = {"entry_defaults": defaults}
    if entries != "missing":
        metadata["entries"] = entries
    before = deepcopy(metadata)
    with pytest.raises(ValueError, match="^accepted OCR menu defaults receipt missing$"):
        output_builder._build_delivery_rows(
            {"lines": []}, {"columns": []}, {}, menu_meta=metadata,
            allow_ocr_menu_meta=False,
        )
    assert metadata == before


def _previous_condition_function():
    # Compare the exact previous condition without requiring Git history in CI.
    tree = ast.parse(inspect.getsource(output_builder._build_delivery_rows))
    matches = 0
    for node in ast.walk(tree):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.BoolOp)
                and ast.unparse(node.test) ==
                "isinstance(menu_meta, dict) and (not isinstance(entry_defaults, dict))"):
            node.test = ast.parse(
                "isinstance(entries, list) and not isinstance(entry_defaults, dict)",
                mode="eval",
            ).body
            matches += 1
    assert matches == 1
    ast.fix_missing_locations(tree)
    namespace = dict(vars(output_builder))
    exec(compile(tree, "<previous-metadata-condition>", "exec"), namespace)
    return namespace["_build_delivery_rows"]


@pytest.mark.parametrize("quantity", [0, None])
def test_valid_metadata_zero_and_null_preserve_previous_output(quantity):
    order = {"facility": "FAC00001", "lines": [{
        "date": "2026-03-22", "daypart": "breakfast", "menu_name": "MenuA",
        "diet_type": "regular", "area_id": "2F", "quantity_original": quantity,
    }]}
    template = {"columns": [{
        "name": "regular", "source": "quantity", "diet_type": "regular", "area_id": "2F",
    }]}
    metadata = {"entries": [], "by_menu": {}, "entry_defaults": {
        "MenuA": {"quantity": 0, "optional": None},
    }}
    inputs = (order, template, {"zero_as_empty": False})
    before = deepcopy((inputs, metadata))
    actual = output_builder._build_delivery_rows(*inputs, menu_meta=metadata)
    expected = _previous_condition_function()(*deepcopy(inputs), menu_meta=deepcopy(metadata))
    assert actual == expected
    assert (inputs, metadata) == before
    assert metadata["entry_defaults"]["MenuA"] == {"quantity": 0, "optional": None}
    if quantity == 0:
        assert len(actual) == 1
        assert actual[0]["regular"] == 0
    else:
        assert actual == []


def test_explicit_none_without_ocr_preserves_previous_output():
    args = ({"lines": []}, {"columns": []}, {})
    actual = output_builder._build_delivery_rows(*args, menu_meta=None, allow_ocr_menu_meta=False)
    assert actual == _previous_condition_function()(*args, menu_meta=None, allow_ocr_menu_meta=False)
    assert actual == []


def test_explicit_none_with_ocr_still_requires_receipt():
    with pytest.raises(ValueError, match="^accepted OCR menu receipt missing$"):
        output_builder._build_delivery_rows({"lines": []}, {"columns": []}, {}, menu_meta=None)
