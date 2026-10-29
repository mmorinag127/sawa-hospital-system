from datetime import datetime

import pytest

from src.services.order_list_contract import normalize_cursor_id, normalize_limit, parse_cursor_received_at


def test_limits_preserve_default_and_ceiling():
    assert [normalize_limit(value) for value in (None, 1, 100, 1001)] == [100, 1, 100, 1000]
    for value in (0, -1, 'invalid'):
        with pytest.raises(ValueError, match='order_list_limit_invalid'):
            normalize_limit(value)


def test_cursor_converts_timezone_without_inventing_missing_date():
    assert parse_cursor_received_at('2026-10-29T09:00:00+09:00') == datetime(2026, 10, 29)
    assert parse_cursor_received_at('2026-10-29T00:00:00Z') == datetime(2026, 10, 29)
    assert parse_cursor_received_at('invalid') is None
    assert parse_cursor_received_at(None) is None
    assert normalize_cursor_id(' order-1 ') == 'order-1'
