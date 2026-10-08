from copy import deepcopy
import math

import pytest

from src.services import config_validator as validator


@pytest.mark.parametrize("index", [float("nan"), float("inf"), True, False, -1, 0.5, 1.0, "0", "bad", None])
@pytest.mark.parametrize("prevalidated", [False, True])
def test_invalid_column_stops_before_derivation(monkeypatch, index, prevalidated):
    columns = [{"index": index, "role": "date"}]
    errors = ["existing error"]
    if prevalidated:
        validator._validate_columns(columns, "fax_template.columns", errors, [])

    def forbidden_derivation(_columns):
        pytest.fail("invalid columns reached field derivation")

    monkeypatch.setattr(validator, "_derive_main_ocr_row_fields_from_columns", forbidden_derivation)
    validator._validate_column_field_consistency(columns, ["date_mmdd"], "fax_template.main_ocr_row_fields", errors)
    assert errors == ["existing error", "fax_template.columns[0].index must be a nonnegative integer"]
    assert columns[0]["index"] is index


@pytest.mark.parametrize("fields", [["date_mmdd"], None, []])
def test_missing_index_stops_without_partial_fields(monkeypatch, fields):
    columns = [{"role": "date"}]
    errors = []

    def forbidden_derivation(_columns):
        pytest.fail("missing index reached field derivation")

    monkeypatch.setattr(validator, "_derive_main_ocr_row_fields_from_columns", forbidden_derivation)
    validator._validate_column_field_consistency(columns, fields, "fax_template.main_ocr_row_fields", errors)
    assert errors == ["fax_template.columns[0].index is required"]
    assert columns == [{"role": "date"}]


def test_master_direct_template_reports_invalid_index_before_derivation(monkeypatch):
    master = {"facilities": [{"facility_id": "F", "facility_name": "Synthetic", "fax_template": {
        "columns": [{"index": float("inf"), "role": "date"}],
        "main_ocr_row_fields": ["date_mmdd"],
    }}]}

    def forbidden_derivation(_columns):
        pytest.fail("master invalid columns reached field derivation")

    monkeypatch.setattr(validator, "_derive_main_ocr_row_fields_from_columns", forbidden_derivation)
    result = validator.validate_facility_master(master)
    assert result["errors"].count("facilities[0].fax_template.columns[0].index must be a nonnegative integer") == 1
    assert math.isinf(master["facilities"][0]["fax_template"]["columns"][0]["index"])


def test_valid_zero_and_distinct_columns_keep_derived_fields_and_raw_values():
    columns = [
        {"index": 4, "role": "quantity", "diet_type": "regular", "area_id": "2f", "quantity": 0},
        {"index": 0, "role": "date", "quantity": None},
        {"index": 2, "role": "menu", "quantity": 5},
    ]
    original = deepcopy(columns)
    fields = ["date_mmdd", "menu", "qty.regular_2f"]
    original_fields = list(fields)
    assert validator._derive_main_ocr_row_fields_from_columns(columns) == fields
    errors = []
    validator._validate_column_field_consistency(columns, fields, "fax_template.main_ocr_row_fields", errors)
    assert errors == []
    assert columns == original
    assert fields == original_fields
    validator._validate_column_field_consistency(columns, ["menu"], "fax_template.main_ocr_row_fields", errors)
    assert errors == ["fax_template.main_ocr_row_fields does not match fax_template.columns "
                      "(expected ['date_mmdd', 'menu', 'qty.regular_2f'], got ['menu'])"]
    assert columns == original
