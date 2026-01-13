import sys
import pathlib

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
