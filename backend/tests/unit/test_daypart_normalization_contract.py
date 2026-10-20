import pytest

from src.services import order_service


@pytest.mark.parametrize("value,normalized,canonical", [
    (None, "", ""),
    (" morning ", "朝", "朝"),
    (" lunch ", "昼", "昼"),
    ("supper", "夕", "夕"),
    ("unknown meal", "unknownmeal", ""),
    (0, "0", ""),
])
def test_daypart_normalization_keeps_unknown_values_distinct(value, normalized, canonical):
    assert order_service._normalize_daypart_key(value) == normalized
    assert order_service._canonical_daypart_key(value) == canonical
