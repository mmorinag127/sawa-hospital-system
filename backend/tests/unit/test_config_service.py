from src.services import candidate_resolution_service, position_column_mapping_service
from src.services import config_service
from src.services.config_service import normalize_fax_template_columns


def test_normalize_fax_template_columns_rewrites_internal_quantity_header() -> None:
    columns = normalize_fax_template_columns(
        [
            {
                "index": 0,
                "role": "quantity",
                "header": "diabetes",
                "diet_type": "diabetes",
                "area_id": "X",
                "name": "qty.diabetes_x",
            }
        ]
    )

    assert columns[0]["header"] == "糖尿"
    assert columns[0]["diet_type"] == "diabetes"
    assert columns[0]["area_id"] == "X"
    assert columns[0]["name"] == "qty.diabetes_x"


def test_normalize_fax_template_columns_rewrites_internal_field_header_with_area() -> None:
    columns = normalize_fax_template_columns(
        [
            {
                "index": 0,
                "role": "quantity",
                "header": "qty.regular_2f",
                "name": "qty.regular_2f",
            }
        ]
    )

    assert columns[0]["header"] == "常食2F"
    assert columns[0]["diet_type"] == "regular"
    assert columns[0]["area_id"] == "2F"
    assert columns[0]["name"] == "qty.regular_2f"


def test_normalize_fax_template_columns_preserves_operator_display_header() -> None:
    columns = normalize_fax_template_columns(
        [
            {
                "index": 0,
                "role": "quantity",
                "header": "常食花",
                "diet_type": "regular",
                "area_id": "2F",
                "name": "qty.regular_2f",
            }
        ]
    )

    assert columns[0]["header"] == "常食花"
    assert columns[0]["diet_type"] == "regular"
    assert columns[0]["area_id"] == "2F"
    assert columns[0]["name"] == "qty.regular_2f"


def test_normalize_fax_template_columns_rewrites_legacy_unknown_spacer() -> None:
    columns = normalize_fax_template_columns(
        [
            {
                "index": 0,
                "role": "quantity",
                "header": "不明",
                "diet_type": "unknown",
                "area_id": "X",
                "name": "qty.unknown_x",
            }
        ]
    )

    assert columns[0]["header"] == "-"
    assert columns[0]["diet_type"] == "placeholder"
    assert columns[0]["area_id"] == "X"
    assert columns[0]["name"] == "qty.placeholder_x"


def test_fax_template_registry_loader_is_removed() -> None:
    assert not hasattr(config_service, "load_fax_template_registry")


def test_position_fallback_is_not_allowed() -> None:
    assert (
        candidate_resolution_service.position_fallback_allowed_for_facility(
            current_facility="FAC00010",
            payload={"facility_candidates": [{"value": "FAC00010", "score": 1.0}]},
        )
        is False
    )


def test_position_fallback_augment_is_noop() -> None:
    payload = {"column_mapping_resolution": {"decision_source": "strict_template"}}
    assert (
        position_column_mapping_service.augment_payload_with_position_fallback(
            payload,
            {"columns": []},
            template_id="山城",
        )
        is payload
    )
