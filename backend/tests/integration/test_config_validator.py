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
