"""Synthetic canonical quantity components; not recognizer or upload proof."""

from copy import deepcopy
from datetime import datetime

import pytest

from src.services import order_service as orders
from src.services import output_builder as outputs
from src.services import template_field_schema_service as schema


def template_for(area, *, omitted=False, locked=False):
    column = {"role": "quantity", "diet_type": "regular", "name": "qty.regular_locked",
              "name_locked": locked}
    if not omitted:
        column["area_id"] = area
    return {"columns": [{"role": "date"}, {"role": "daypart"},
                        {"role": "menu"}, column]}


@pytest.mark.parametrize("area,omitted,locked", [
    ("2F", False, False), ("3F", False, False), ("X", False, False),
    ("common", False, False), ("opaque_area_one", False, False),
    (None, True, False), (None, False, False), ("", False, False),
    ("common", False, True),
])
def test_canonical_identity_reaches_index_selector_summary_cells_and_ocr(area, omitted, locked):
    template = template_for(area, omitted=omitted, locked=locked)
    original = deepcopy(template)
    fields = schema.derive_row_fields_from_template(template)
    identity = ("regular", area or None)
    field = fields[-1]
    assert schema.resolve_quantity_field_identities(template) == {field: identity}
    index = orders._build_sheet_quantity_index(fields, template=template)
    assert index == {identity: 3}
    assert orders._resolve_quantity_column_index(
        quantity_index=index, diet_key=identity[0], area_key=identity[1]) == 3
    rows = [["2026-03-22", "朝", "Synthetic menu", "0"],
            ["2026-03-22", "朝", "Synthetic menu", None],
            ["2026-03-22", "朝", "Synthetic menu", "13"]]
    assert orders._materialization_quantity_cell_issues(
        fields=fields, rows_payload=rows, template=template) == []
    expected = {(0, *identity): 0.0, (2, *identity): 13.0}
    assert orders._build_sheet_source_row_quantity_summary(
        fields=fields, rows_payload=rows, template=template) == expected
    lines = [{"source_row_index": row, "diet_type": identity[0], "area_id": identity[1],
              "quantity_original": qty, "quantity_corrected": None}
             for row, qty in [(0, 0), (1, None), (2, 13)]]
    assert orders._build_materialized_source_row_quantity_summary(lines, template=template) == expected
    materialized = orders._build_materialization_lines_from_sheet_rows(
        fields=fields, rows_payload=rows, template=template,
        existing_week_code="2026-03@2026-03-22~2026-03-22",
        received_at=datetime(2027, 1, 1), date_policy={})
    assert [(line["diet_type"], line["area_id"], line["quantity_original"],
             line["quantity_corrected"], line["source_field"]) for line in materialized] == [
        (*identity, 0, None, field), (*identity, 13, None, field)]
    entries = outputs._extract_ocr_entries_from_structured_payload(
        {"fields": fields, "rows": rows}, template, 2026)
    key = field.removeprefix("qty.")
    assert [entry["quantity_map"] for entry in entries] == [{key: 0.0}, {}, {key: 13.0}]
    bad_rows = [["2026-03-22", "朝", "Synthetic menu", "unresolved"]]
    issues = orders._materialization_quantity_cell_issues(
        fields=fields, rows_payload=bad_rows, template=template)
    assert len(issues) == 1
    assert issues[0]["raw"] == "unresolved"
    assert issues[0]["field"] == field
    assert template == original
    assert rows[1][-1] is None


def test_unknown_field_identity_and_missing_template_stop():
    template = template_for("common")
    fields = schema.derive_row_fields_from_template(template)
    with pytest.raises(ValueError, match="quantity_template_field_unknown"):
        orders._build_sheet_quantity_index(fields[:-1] + ["qty.regular_common"], template=template)
    index = orders._build_sheet_quantity_index(fields, template=template)
    with pytest.raises(ValueError, match="quantity_template_identity_unknown"):
        orders._resolve_quantity_column_index(quantity_index=index, diet_key="regular", area_key="X")
    with pytest.raises(ValueError, match="quantity_template_source_required"):
        orders._build_sheet_quantity_index(fields)
    with pytest.raises(ValueError, match="quantity_template_source_required"):
        outputs._extract_ocr_entries_from_structured_payload({"rows": []}, None, 2026)
    with pytest.raises(ValueError, match="quantity_template_field_missing"):
        orders._build_sheet_quantity_index(fields[:-1], template=template)


def test_quantity_resolver_preserves_nonquantity_metadata_boundary():
    template = template_for("common")
    fields = schema.derive_row_fields_from_template(template)
    metadata_fields = ["category", "aux.col_2", "post_menu.note"]
    assert schema.quantity_field_identities_for_fields(template, fields + metadata_fields) == {
        fields[-1]: ("regular", "common")}
    with pytest.raises(ValueError, match="quantity_template_identity_unknown"):
        orders._build_materialized_source_row_quantity_summary([
            {"source_row_index": 0, "diet_type": "regular", "area_id": "X",
             "quantity_original": 0, "quantity_corrected": None}], template=template)


@pytest.mark.parametrize("area", ["2F", "3F", "X", "common", "opaque_area_one", None])
def test_saved_field_case_parity_preserves_raw_field_and_quantity(area):
    template = template_for(area)
    canonical_fields = schema.derive_row_fields_from_template(template)
    fields = canonical_fields[:-1] + [canonical_fields[-1].upper()]
    before = deepcopy(fields)
    identity = ("regular", area)
    assert schema.quantity_field_identities_for_fields(template, fields) == {fields[-1]: identity}
    assert orders._build_sheet_quantity_index(fields, template=template) == {identity: 3}
    assert orders._validate_sheet_template_fields(fields, template=template) is None
    rows = [["2026-03-22", "朝", "Synthetic menu", "0"],
            ["2026-03-22", "朝", "Synthetic menu", None],
            ["2026-03-22", "朝", "Synthetic menu", "13"]]
    before_rows = deepcopy(rows)
    lines = orders._build_materialization_lines_from_sheet_rows(
        fields=fields, rows_payload=rows, template=template,
        existing_week_code="2026-03@2026-03-22~2026-03-22",
        received_at=datetime(2027, 1, 1), date_policy={})
    assert [(line["source_field"], line["area_id"], line["quantity_original"],
             line["quantity_corrected"]) for line in lines] == [
        (fields[-1], area, 0, None), (fields[-1], area, 13, None)]
    assert orders._build_sheet_source_row_quantity_summary(
        fields=fields, rows_payload=rows, template=template) == {
            (0, *identity): 0.0, (2, *identity): 13.0}
    assert fields == before and rows == before_rows
    entries = outputs._extract_ocr_entries_from_structured_payload(
        {"fields": fields, "rows": rows}, template, 2026)
    canonical_key = canonical_fields[-1].removeprefix("qty.").lower()
    assert [entry["quantity_map"] for entry in entries] == [
        {canonical_key: 0.0}, {}, {canonical_key: 13.0}]
    assert fields == before and rows == before_rows
    with pytest.raises(ValueError, match="quantity_template_alias_collision"):
        schema.quantity_field_identities_for_fields(template, fields + [canonical_fields[-1]])


def test_case_normalization_never_introduces_raw_name_alias_or_identity_collision():
    template = template_for("common")
    fields = schema.derive_row_fields_from_template(template)
    with pytest.raises(ValueError, match="quantity_template_field_unknown"):
        schema.quantity_field_identities_for_fields(template, fields[:-1] + ["QTY.REGULAR_COMMON"])
    template["columns"].append({"role": "quantity", "diet_type": "regular", "area_id": "X"})
    with pytest.raises(ValueError, match="quantity_template_alias_collision"):
        schema.resolve_quantity_field_identities(template)


@pytest.mark.parametrize("kind,code", [
    ("field", "alias_collision"), ("identity", "identity_duplicate"),
    ("collision", "alias_collision"), ("diet", "identity_required"),
])
def test_duplicate_and_missing_authored_source_stop(kind, code):
    template = template_for("common")
    column = deepcopy(template["columns"][-1])
    if kind == "identity":
        template["columns"][-1].update(name_locked=True, name="qty.regular_first")
        column.update(name_locked=True, name="qty.regular_second")
    elif kind == "collision":
        column["area_id"] = "X"
    elif kind == "diet":
        template["columns"][-1].pop("diet_type")
    if kind != "diet":
        template["columns"].append(column)
    with pytest.raises(ValueError, match=f"quantity_template_{code}"):
        schema.resolve_quantity_field_identities(template)
