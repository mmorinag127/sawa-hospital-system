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
    assert "qty.soft_x" not in fields
    assert "qty.mixer_x" not in fields
    assert "qty.change_1_x" in fields
    assert "qty.change_2_x" in fields
    assert ((template.get("postprocess") or {}).get("qty_ocr_engine")) == "tesseract_digits"
    assert ((template.get("postprocess") or {}).get("qty_max_value")) == 50


def test_explicit_quantity_diet_type_wins_over_unrecognized_japanese_header():
    _clear_facilities()
    fac = facility_service.create_facility("Diet Override Facility", [])
    config = {
        "fax_template_override": {
            "columns": [
                {"index": 0, "role": "date", "header": "日付"},
                {"index": 1, "role": "daypart", "header": "区分"},
                {"index": 2, "role": "menu_name", "header": "メニュー"},
                {
                    "index": 3,
                    "role": "quantity",
                    "header": "糖尿",
                    "diet_type": "diabetes",
                    "area_id": "X",
                },
                {
                    "index": 4,
                    "role": "quantity",
                    "header": "ゴマアレルギー",
                    "diet_type": "sesame_allergy",
                    "area_id": "X",
                },
                {"index": 5, "role": "note", "header": "備考欄"},
            ]
        }
    }
    assert facility_service.update_config(fac["id"], config)

    resolved = config_service.get_facility_config(fac["id"])
    assert resolved is not None
    template = resolved.get("fax_template") or {}
    columns = template.get("columns") or []

    assert columns[3]["header"] == "糖尿"
    assert columns[3]["diet_type"] == "diabetes"
    assert columns[3]["name"] == "qty.diabetes_x"
    assert columns[4]["header"] == "ゴマアレルギー"
    assert columns[4]["diet_type"] == "sesame_allergy"
    assert columns[4]["name"] == "qty.sesame_allergy_x"
    assert template.get("main_ocr_row_fields") == [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.diabetes_x",
        "qty.sesame_allergy_x",
        "remarks",
    ]


def test_placeholder_and_custom_quantity_tokens_are_preserved():
    _clear_facilities()
    fac = facility_service.create_facility("Custom Quantity Facility", [])
    config = {
        "fax_template_override": {
            "columns": [
                {"index": 0, "role": "date", "header": "日付", "format": "MM/DD"},
                {"index": 1, "role": "daypart", "header": "区分"},
                {"index": 2, "role": "menu_name", "header": "メニュー"},
                {"index": 3, "role": "quantity", "header": "-", "diet_type": "placeholder", "area_id": "X"},
                {"index": 4, "role": "quantity", "header": "お茶", "diet_type": "tea", "area_id": "X"},
                {"index": 5, "role": "quantity", "header": "事業", "diet_type": "business", "area_id": "X"},
                {"index": 6, "role": "quantity", "header": "妊娠", "diet_type": "pregnancy", "area_id": "X"},
                {"index": 7, "role": "note", "header": "備考欄"},
            ]
        }
    }
    assert facility_service.update_config(fac["id"], config)

    resolved = config_service.get_facility_config(fac["id"])
    assert resolved is not None
    template = resolved.get("fax_template") or {}
    columns = template.get("columns") or []

    assert columns[3]["diet_type"] == "placeholder"
    assert columns[3]["name"] == "qty.placeholder_x"
    assert columns[4]["diet_type"] == "tea"
    assert columns[4]["name"] == "qty.tea_x"
    assert columns[5]["diet_type"] == "business"
    assert columns[5]["name"] == "qty.business_x"
    assert columns[6]["diet_type"] == "pregnancy"
    assert columns[6]["name"] == "qty.pregnancy_x"


def test_fac00006_exposes_layout_template_candidates():
    config_service.reload_configs()
    resolved = config_service.get_facility_config("FAC00006")
    assert resolved is not None
    assert resolved.get("fax_template_id") == "fax_layout_regular_soft_mixer_forbidden_v1"
    assert ((resolved.get("fax_template") or {}).get("postprocess") or {}).get("qty_ocr_engine") == "tesseract_digits"
    assert (resolved.get("fax_template") or {}).get("main_ocr_row_fields") == [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "qty.regular_bag_x",
        "qty.soft_x",
        "qty.mixer_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "remarks",
    ]
    assert resolved.get("fax_template_ids") == [
        "fax_layout_regular_soft_mixer_forbidden_v1",
        "fax_layout_floor_2f3f_v1",
        "fax_layout_regular_staff_daycare_v1",
    ]
    registry = config_service.load_fax_template_registry()
    assert registry["fax_layout_regular_staff_daycare_v1"]["postprocess"]["qty_ocr_engine"] == "tesseract_digits"
    assert registry["fax_layout_regular_soft_mixer_forbidden_v1"]["postprocess"]["qty_ocr_engine"] == "tesseract_digits"
    assert registry["fax_layout_regular_staff_daycare_v1"]["postprocess"]["qty_max_value"] == 50
    assert registry["fax_layout_regular_soft_mixer_forbidden_v1"]["postprocess"]["qty_max_value"] == 50


def test_fac00007_and_fac00012_use_regular_forbidden_plus_change_columns():
    config_service.reload_configs()
    for facility_id in ("FAC00007", "FAC00012"):
        resolved = config_service.get_facility_config(facility_id)
        assert resolved is not None
        assert resolved.get("fax_template_id") == "fax_layout_regular_forbidden_v1"
        template = resolved.get("fax_template") or {}
        assert template.get("main_ocr_row_fields") == [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_x",
            "qty.no_meat_x",
            "qty.no_fish_x",
            "qty.change_1_x",
            "qty.change_2_x",
            "remarks",
        ]


def test_fac00010_uses_floor_columns_from_source_master():
    config_service.reload_configs()
    resolved = config_service.get_facility_config("FAC00010")
    assert resolved is not None
    template = resolved.get("fax_template") or {}
    assert resolved.get("fax_template_override") == {"grid_line_scale_horizontal": 20}
    assert template.get("main_ocr_row_fields") == [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "remarks",
    ]


def test_fac00014_15_16_expose_custom_quantity_columns():
    config_service.reload_configs()

    fac14 = config_service.get_facility_config("FAC00014")
    assert fac14 is not None
    assert fac14.get("fax_template_id") == "fax_layout_regular_staff_daycare_v1"
    assert (fac14.get("fax_template") or {}).get("main_ocr_row_fields") == [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "qty.staff_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.sesame_allergy_x",
        "qty.change_1_x",
        "remarks",
    ]

    fac15 = config_service.get_facility_config("FAC00015")
    assert fac15 is not None
    assert fac15.get("fax_template_id") == "fax_layout_regular_forbidden_v1"
    assert (fac15.get("fax_template") or {}).get("main_ocr_row_fields") == [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "qty.placeholder_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.change_1_x",
        "qty.change_2_x",
        "remarks",
    ]

    fac16 = config_service.get_facility_config("FAC00016")
    assert fac16 is not None
    assert fac16.get("fax_template_id") == "fax_layout_regular_diabetes_v1"
    assert (fac16.get("fax_template") or {}).get("main_ocr_row_fields") == [
        "date_mmdd",
        "daypart",
        "menu",
        "qty.regular_x",
        "qty.diabetes_x",
        "qty.no_meat_x",
        "qty.no_fish_x",
        "qty.change_1_x",
        "qty.change_2_x",
        "remarks",
    ]


def test_layout_templates_qty_regex_match_digit_cells():
    config_service.reload_configs()
    registry = config_service.load_fax_template_registry()
    for facility_id in ("FAC00002", "FAC00003", "FAC00006", "FAC00007", "FAC00012", "FAC00013", "FAC00014", "FAC00015", "FAC00016"):
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

    fac00014 = config_service.get_facility_config("FAC00014")
    assert fac00014 is not None
    assert fac00014.get("fax_template_id") == "fax_layout_regular_staff_daycare_v1"
    assert fac00014.get("fax_template_ids") == [
        "fax_layout_regular_staff_daycare_v1",
        "fax_layout_regular_forbidden_v1",
    ]

    fac00016 = config_service.get_facility_config("FAC00016")
    assert fac00016 is not None
    assert fac00016.get("fax_template_id") == "fax_layout_regular_diabetes_v1"
    assert fac00016.get("fax_template_ids") == [
        "fax_layout_regular_diabetes_v1",
        "fax_layout_regular_forbidden_v1",
    ]


def test_facility_config_normalizes_hana_tsuki_columns_to_floor_fields():
    original = facility_service.get_facility_config("FAC00003") or {}
    next_config = dict(original)
    override = dict(next_config.get("fax_template_override") or {})
    override["columns"] = [
        {"index": 0, "role": "date", "header": "日付"},
        {"index": 1, "role": "daypart", "header": "区分"},
        {"index": 2, "role": "menu_name", "header": "メニュー"},
        {"index": 3, "role": "quantity", "header": "常食花", "diet_type": "regular", "area_id": "花"},
        {"index": 4, "role": "quantity", "header": "常食月", "diet_type": "regular", "area_id": "月"},
        {"index": 5, "role": "quantity", "header": "軟菜花", "diet_type": "soft", "area_id": "花"},
        {"index": 6, "role": "quantity", "header": "軟菜月", "diet_type": "soft", "area_id": "月"},
        {"index": 7, "role": "quantity", "header": "ミキサー花", "diet_type": "mixer", "area_id": "花"},
        {"index": 8, "role": "quantity", "header": "ミキサー月", "diet_type": "mixer", "area_id": "月"},
        {"index": 9, "role": "note", "header": "備考"},
    ]
    override.pop("main_ocr_row_fields", None)
    next_config["fax_template_override"] = override

    try:
        assert facility_service.update_config("FAC00003", next_config)
        resolved = config_service.get_facility_config("FAC00003")
        assert resolved is not None
        columns = (resolved.get("fax_template") or {}).get("columns") or []
        qty_columns = [col for col in columns if isinstance(col, dict) and str(col.get("role") or "") == "quantity"]
        assert [col.get("area_id") for col in qty_columns[:6]] == ["2F", "3F", "2F", "3F", "2F", "3F"]
        assert [col.get("name") for col in qty_columns[:6]] == [
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.soft_2f",
            "qty.soft_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
        ]
        assert (resolved.get("fax_template") or {}).get("main_ocr_row_fields") == [
            "date_mmdd",
            "daypart",
            "menu",
            "qty.regular_2f",
            "qty.regular_3f",
            "qty.soft_2f",
            "qty.soft_3f",
            "qty.mixer_2f",
            "qty.mixer_3f",
            "remarks",
        ]
    finally:
        facility_service.update_config("FAC00003", original)
