import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import shipping_status_store  # noqa: E402


def test_get_latest_statuses_for_facility_returns_serialized_items():
    shipping_status_store.clear_status_history()
    inserted = shipping_status_store.record_tracking_statuses(
        [
            {
                "tracking_number": "1234-5678-9012",
                "tracking_key": "123456789012",
                "facility_name": "大和なでしこ",
                "status": "配達中",
                "delivered": False,
                "arrival_text": None,
            }
        ],
        source="test",
    )

    assert inserted == 1

    result = shipping_status_store.get_latest_statuses_for_facility(["大和なでしこ"], limit=5)

    assert result["summary"]["pending"] == 1
    assert result["items"][0]["tracking_number"] == "1234-5678-9012"
    assert result["items"][0]["status"] == "配達中"
