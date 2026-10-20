from datetime import date, datetime

import pytest

from src.services import master_order_form_template_service, menu_service, order_form_service, output_builder


@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("", None),
    ("invalid", None),
    ("2026-09-21", date(2026, 9, 21)),
    ("2026-09-21T12:00:00", date(2026, 9, 21)),
    (date(2026, 9, 21), date(2026, 9, 21)),
    (datetime(2026, 9, 21, 12), date(2026, 9, 21)),
])
def test_order_form_menu_date_parsing_is_shared(value, expected):
    for parser in [menu_service.parse_menu_date,
                   master_order_form_template_service._parse_menu_date,
                   order_form_service._parse_menu_date]:
        assert parser(value) == expected


@pytest.mark.parametrize("value,expected", [(" 朝食 ", "朝"), ("昼食", "昼"), ("夕食", "夕"), ("unknown", "unknown"), (None, "")])
def test_output_daypart_normalization_keeps_unknown_values(value, expected):
    assert output_builder._normalize_output_daypart(value) == expected
