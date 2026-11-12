from copy import deepcopy
import importlib.util
from pathlib import Path
import json

import pytest
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "prepare_invoice_column_correction", ROOT / "scripts/prepare_invoice_column_correction.py")
correction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(correction)


def test_reviewed_invoice_correction_preserves_identity_and_ocr():
    source = {"source_index": 5, "header": "通所", "diet_type": "daycare", "area_id": "X"}
    facility = {"facility_id": "FAC-test", "facility_name": "original name", "areas": [],
                "fax_template": {"columns": [{**source, "role": "quantity",
                                             "semantic": {"aggregation_role": "include"}}]},
                "invoice_template": {"template_uri": "original.xlsx", "columns": [
                    {"name": "old", "source": "quantity", "diet_type": "regular"}]}}
    before = deepcopy(facility)
    columns = [{"name": "通所", "source": "quantity", "diet_type": "daycare", "area_id": "X"}]
    result = correction.prepare_correction(facility, source_columns=[source], invoice_columns=columns)
    assert facility == before
    assert result["fax_template"] == before["fax_template"]
    assert result["facility_name"] == before["facility_name"]
    assert result["areas"] == before["areas"]
    assert result["invoice_template"]["template_uri"] == "original.xlsx"
    for bad in ({**source, "header": "other"}, {**source, "source_index": 6}):
        with pytest.raises(ValueError, match="reviewed_source_columns_changed"):
            correction.prepare_correction(facility, source_columns=[bad], invoice_columns=columns)
    with pytest.raises(ValueError, match="explicit_one_to_one"):
        correction.prepare_correction(facility, source_columns=[source],
                                      invoice_columns=[{**columns[0], "area_id": None}])


def test_slot_heading_replaces_stale_template_without_touching_body():
    from src.services.output_builder import _apply_delivery_configured_headers
    ws = Workbook().active
    ws.cell(4, 1, "facility title")
    ws.cell(8, 4, "常食2F")
    ws.cell(8, 7, "ミキサー")
    ws.merge_cells("G8:H10")
    ws.cell(11, 7, "2F")
    ws.cell(11, 8, "3F")
    ws.cell(12, 7, 123)
    columns = [{"name": "日付", "column_index": 1},
               {"name": "常食", "column_index": 4},
               {"name": "通所", "column_index": 5},
               {"name": "職員", "column_index": 6},
               {"name": "備考", "column_index": 9}]
    _apply_delivery_configured_headers(ws, 8, columns,
        {column["name"]: column["column_index"] for column in columns},
        data_start_row=12, replace_header_band=True)
    assert [ws.cell(8, index).value for index in (4, 5, 6)] == ["常食", "通所", "職員"]
    assert all(ws.cell(row, col).value in (None, "") for row in range(8, 12) for col in (7, 8))
    assert ws.cell(4, 1).value == "facility title"
    assert ws.cell(12, 7).value == 123


def test_heading_merge_into_saved_quantity_is_rejected():
    from src.services.output_builder import _apply_delivery_configured_headers
    ws = Workbook().active
    ws.merge_cells("D8:D12")
    with pytest.raises(ValueError, match="crosses_data_boundary"):
        _apply_delivery_configured_headers(ws, 8, [{"name": "通所", "column_index": 4}],
                                           {"通所": 4}, data_start_row=12, replace_header_band=True)
    assert "D8:D12" in ws.merged_cells


def test_future_original_labels_keep_history_and_existing_aggregation():
    review = json.loads((ROOT / "docs/kasuga-future-reviewed-columns-20261112.json").read_text())
    columns = [{"source_index": patch["source_index"], **patch["expected"]}
               for patch in review["source_label_patches"]]
    invoice = [{"column_index": patch["column_index"], "source": "quantity", **patch["expected"]}
               for patch in review["invoice_label_patches"]]
    soft = {"column_index": 6, "source": "quantity", "name": "軟菜", "diet_type": "soft"}
    invoice.append(soft)
    facility = {"facility_id": "FAC00003", "facility_name": "unchanged", "areas": [],
                "fax_template": {"columns": columns, "main_ocr_row_fields": [column["name"] for column in columns]},
                "fax_template_override": {"columns": deepcopy(columns)},
                "invoice_template": {"columns": invoice}}
    original = deepcopy(facility)
    result = correction.prepare_source_labels(facility, review)
    assert facility == original
    assert result["facility_name"] == "unchanged"
    assert result["invoice_template"]["columns"][-1] == soft
    assert result["areas"] == [{"id": "hana", "name": "花"}, {"id": "tsuki", "name": "月"}]
    assert all(column["area_id"] in {"hana", "tsuki"} for column in result["fax_template"]["columns"])
    assert result["fax_template_override"]["columns"] == result["fax_template"]["columns"]
    assert not any("_2f" in name or "_3f" in name for name in result["fax_template"]["main_ocr_row_fields"])
    assert all("aliases" not in area for area in result["areas"])


def test_fixed_slot_quantity_headings_keep_area_identity():
    from src.services.output_builder import _apply_delivery_configured_headers
    ws = Workbook().active
    columns = [{"name": "ミキサー 花", "header": "ミキサー", "source": "quantity", "column_index": 7},
               {"name": "ミキサー 月", "header": "ミキサー", "source": "quantity", "column_index": 8}]
    _apply_delivery_configured_headers(ws, 8, columns,
        {column["name"]: column["column_index"] for column in columns},
        data_start_row=12, replace_header_band=True)
    assert ws.cell(8, 7).value == "ミキサー 花"
    assert ws.cell(8, 8).value == "ミキサー 月"
