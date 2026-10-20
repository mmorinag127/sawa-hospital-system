from datetime import date

import pytest

from src.services import master_order_form_template_service as template
from src.services import menu_service


@pytest.mark.parametrize('value', [None, '', '2026-10-20', '2026/10/20', 'invalid', date(2026, 10, 20)])
def test_template_uses_shared_date_parser(value):
    assert template._parse_menu_date(value) == menu_service.parse_menu_date(value)
