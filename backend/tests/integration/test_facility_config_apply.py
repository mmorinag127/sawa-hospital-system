import sys
import pathlib
import re

from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import facility_service, config_service  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.facility import Facility, FacilityArea, FacilityConfig  # noqa: E402


def _clear_facilities():
    with session_scope() as session:
        session.execute(delete(FacilityConfig))
        session.execute(delete(FacilityArea))
        session.execute(delete(Facility))


def test_facility_config_applies_to_resolved_config():
    _clear_facilities()
    fac = facility_service.create_facility(
        "Beta Facility",
        [{"id": "ARE100", "name": "Unit X"}],
    )
    config = {
        "packaging_policy_override": {"split_key": ["facility", "date"]},
        "label_profile_override": {"storage_mode": "frozen"},
        "invoice_template": {
            "template_uri": "gs://example/invoice.xlsx",
            "columns": [{"name": "date", "source": "date"}],
        },
    }
    assert facility_service.update_config(fac["id"], config)
    resolved = config_service.get_facility_config(fac["id"])
    assert resolved is not None
    assert resolved["packaging_policy"]["split_key"] == ["facility", "date"]
    assert resolved["label_profile"]["storage_mode"] == "frozen"
    assert resolved["invoice_template"]["columns"][0]["name"] == "date"
    assert config_service.resolve_facility_id("Beta Facility") == fac["id"]


def test_fac00002_template_columns_preserve_area_schema_without_duplicates():
    config_service.reload_configs()
    resolved = config_service.get_facility_config("FAC00002")
    assert resolved is not None
    assert resolved.get("fax_template_id") == "fax_layout_regular_forbidden_v1"
    template = resolved.get("fax_template") or {}
    fields = template.get("main_ocr_row_fields") or []
    assert isinstance(fields, list)
    assert len(fields) == len(set(fields))
    assert "qty.regular_x" in fields
    assert "qty.no_meat_x" in fields
    assert "qty.no_fish_x" in fields
    assert "qty.unused_x" not in fields
    assert ((template.get("postprocess") or {}).get("qty_ocr_engine")) == "tesseract_digits"
    assert ((template.get("postprocess") or {}).get("qty_max_value")) == 50


def test_fac00006_exposes_layout_template_candidates():
    config_service.reload_configs()
    resolved = config_service.get_facility_config("FAC00006")
    assert resolved is not None
    assert resolved.get("fax_template_id") == "fax_layout_floor_2f3f_v1"
    assert ((resolved.get("fax_template") or {}).get("postprocess") or {}).get("qty_strategy") == "disabled"
    assert resolved.get("fax_template_ids") == [
        "fax_layout_floor_2f3f_v1",
        "fax_layout_regular_staff_daycare_v1",
        "fax_layout_regular_soft_mixer_forbidden_v1",
    ]
    registry = config_service.load_fax_template_registry()
    assert registry["fax_layout_regular_staff_daycare_v1"]["postprocess"]["qty_ocr_engine"] == "tesseract_digits"
    assert registry["fax_layout_regular_soft_mixer_forbidden_v1"]["postprocess"]["qty_ocr_engine"] == "tesseract_digits"
    assert registry["fax_layout_regular_staff_daycare_v1"]["postprocess"]["qty_max_value"] == 50
    assert registry["fax_layout_regular_soft_mixer_forbidden_v1"]["postprocess"]["qty_max_value"] == 50


def test_layout_templates_qty_regex_match_digit_cells():
    config_service.reload_configs()
    registry = config_service.load_fax_template_registry()
    for facility_id in ("FAC00002", "FAC00003", "FAC00006", "FAC00012", "FAC00013"):
        resolved = config_service.get_facility_config(facility_id)
        assert resolved is not None
        template_ids = [resolved.get("fax_template_id")] + list(resolved.get("fax_template_ids") or [])
        for template_id in {template_id for template_id in template_ids if template_id}:
            template = registry.get(template_id) or {}
            pattern = ((template or {}).get("postprocess") or {}).get("qty_regex")
            assert pattern
            assert re.compile(pattern).match("23"), template_id


def test_fac00003_and_fac00013_use_explicit_layout_templates():
    config_service.reload_configs()

    fac00003 = config_service.get_facility_config("FAC00003")
    assert fac00003 is not None
    assert fac00003.get("fax_template_id") == "fax_layout_floor_2f3f_v1"
    assert fac00003.get("fax_template_ids") == [
        "fax_layout_floor_2f3f_v1",
        "fax_layout_regular_forbidden_v1",
    ]

    fac00013 = config_service.get_facility_config("FAC00013")
    assert fac00013 is not None
    assert fac00013.get("fax_template_id") == "fax_layout_regular_diabetes_v1"
    assert fac00013.get("fax_template_ids") == [
        "fax_layout_regular_diabetes_v1",
        "fax_layout_regular_forbidden_v1",
    ]
