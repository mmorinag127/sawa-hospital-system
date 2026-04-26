import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.config_validator import validate_facility_config  # noqa: E402


def test_validate_facility_config_accepts_main_ocr_row_fields():
    payload = {
        "fax_template_override": {
            "main_ocr_row_fields": [
                "date_mmdd",
                "daypart",
                "aux.col_2",
                "menu",
                "qty.regular_x",
                "qty.soft_x",
                "qty.mixer_x",
                "remarks",
            ]
        }
    }
    result = validate_facility_config(payload)
    assert result["errors"] == []


def test_validate_facility_config_rejects_invalid_main_ocr_row_fields():
    payload = {
        "fax_template_override": {
            "main_ocr_row_fields": [
                "date_mmdd",
                "menu",
                "invalid_field",
                "qty.regular_x",
                "qty.regular_x",
            ]
        }
    }
    result = validate_facility_config(payload)
    assert any("invalid field" in item for item in result["errors"])
    assert any("duplicated field" in item for item in result["errors"])


def test_validate_facility_config_rejects_mismatched_columns_and_main_ocr_row_fields():
    payload = {
        "fax_template_override": {
            "columns": [
                {"index": 0, "role": "date", "header": "日付"},
                {"index": 1, "role": "daypart", "header": "区分"},
                {"index": 2, "role": "menu_name", "header": "メニュー"},
                {"index": 3, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
                {"index": 4, "role": "quantity", "header": "職員", "diet_type": "staff", "area_id": "X"},
                {"index": 5, "role": "note", "header": "備考欄"},
            ],
            "main_ocr_row_fields": [
                "date_mmdd",
                "daypart",
                "menu",
                "qty.staff_x",
                "qty.regular_x",
                "remarks",
            ],
        }
    }
    result = validate_facility_config(payload)
    assert any("does not match fax_template_override.columns" in item for item in result["errors"])


def test_validate_facility_config_accepts_aux_fields_from_columns_authoritative_schema():
    payload = {
        "fax_template_override": {
            "columns": [
                {"index": 0, "role": "date", "header": "日付"},
                {"index": 1, "role": "daypart", "header": "区分"},
                {"index": 2, "role": "aux", "header": "副区分"},
                {"index": 3, "role": "menu_name", "header": "メニュー"},
                {"index": 4, "role": "aux", "header": "合計"},
                {"index": 5, "role": "quantity", "header": "常食", "diet_type": "regular", "area_id": "X"},
                {"index": 6, "role": "note", "header": "備考欄"},
            ],
            "main_ocr_row_fields": [
                "date_mmdd",
                "daypart",
                "aux.col_2",
                "menu",
                "aux.col_4",
                "qty.regular_x",
                "remarks",
            ],
        }
    }
    result = validate_facility_config(payload)
    assert result["errors"] == []


def test_validate_facility_config_accepts_llm_retry_settings():
    payload = {
        "openai_ocr_retry_on_truncation": True,
        "openai_ocr_retry_max_tokens": 9000,
        "gemini_ocr_retry_on_truncation": True,
        "gemini_ocr_retry_max_tokens": 18000,
    }
    result = validate_facility_config(payload)
    assert result["errors"] == []


def test_validate_facility_config_rejects_invalid_llm_retry_settings():
    payload = {
        "openai_ocr_retry_on_truncation": "true",
        "openai_ocr_retry_max_tokens": "9000",
        "gemini_ocr_retry_on_truncation": "true",
        "gemini_ocr_retry_max_tokens": "18000",
    }
    result = validate_facility_config(payload)
    assert any("openai_ocr_retry_on_truncation must be a boolean" in item for item in result["errors"])
    assert any("openai_ocr_retry_max_tokens must be an integer" in item for item in result["errors"])
    assert any("gemini_ocr_retry_on_truncation must be a boolean" in item for item in result["errors"])
    assert any("gemini_ocr_retry_max_tokens must be an integer" in item for item in result["errors"])
