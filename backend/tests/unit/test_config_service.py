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
