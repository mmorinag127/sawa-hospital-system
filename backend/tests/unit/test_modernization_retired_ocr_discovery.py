"""Direct retirement component contracts, not active OCR or provider proof."""

from datetime import datetime

import pytest

from src.services import order_service


@pytest.mark.parametrize("order_id,arguments", [
    ("COMPONENT-ORDER", {}),
    ("COMPONENT-ORDER", {"not_before": datetime(2026, 3, 1), "limit": 2}),
    ("", {"not_before": "invalid-time", "limit": -1}),
], ids=["defaults", "explicit-valid-time", "blank-invalid-arguments"])
def test_retired_discovery_always_stops_before_discovery(order_id, arguments):
    with pytest.raises(order_service.LegacyOcrEvidenceFallbackDisabledError) as caught:
        order_service._list_latest_completed_ocr_outputs(order_id, **arguments)
    assert str(caught.value) == (
        "legacy OCR output discovery is disabled; use the owned output reference"
    )
