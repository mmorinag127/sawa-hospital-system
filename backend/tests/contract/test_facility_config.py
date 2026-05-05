import sys
import pathlib
from fastapi.testclient import TestClient
from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.facility import Facility, FacilityArea, FacilityConfig  # noqa: E402
from src.services import facility_service  # noqa: E402


def _clear_facilities():
    with session_scope() as session:
        session.execute(delete(FacilityConfig))
        session.execute(delete(FacilityArea))
        session.execute(delete(Facility))
    facility_service._SYNC_DONE = False  # noqa: SLF001


def test_facility_config_update_contract():
    _clear_facilities()
    client = TestClient(app)
    res = client.post(
        "/facilities",
        json={"name": "Alpha Facility", "areas": [{"id": "ARE001", "name": "Unit A"}]},
    )
    assert res.status_code == 201
    facility_id = res.json()["id"]
    config = {"label_profile_override": {"storage_mode": "frozen"}}
    update = client.put(f"/facilities/{facility_id}/config", json=config)
    assert update.status_code == 200
    fetched = client.get(f"/facilities/{facility_id}")
    assert fetched.status_code == 200
    payload = fetched.json()
    assert payload["config"]["label_profile_override"]["storage_mode"] == "frozen"


def test_facility_scoped_fax_template_registration_contract():
    _clear_facilities()
    client = TestClient(app)
    res = client.post("/facilities", json={"name": "Template Missing Facility", "areas": []})
    assert res.status_code == 201
    facility_id = res.json()["id"]

    options = client.get("/facilities/fax-template-options")
    assert options.status_code == 200
    option_ids = {item["template_id"] for item in options.json().get("templates", [])}
    assert "fax_layout_regular_forbidden_v1" in option_ids

    update = client.put(
        f"/facilities/{facility_id}/fax-template",
        json={"fax_template_id": "fax_layout_regular_forbidden_v1"},
    )
    assert update.status_code == 200
    update_payload = update.json()
    assert update_payload["config"]["fax_template_id"] == "fax_layout_regular_forbidden_v1"
    assert update_payload["config"]["fax_template_ids"] == ["fax_layout_regular_forbidden_v1"]
    assert update_payload["resolved_config"]["fax_template_id"] == "fax_layout_regular_forbidden_v1"
    assert update_payload["template_version"]["id"]
    assert update_payload["resolved_config"]["facility_template_version_id"] == update_payload["template_version"]["id"]

    direct_template_update = client.put(
        f"/facilities/{facility_id}/config",
        json={"fax_template_id": "fax_layout_regular_forbidden_v1", "fax_template_override": {"columns": []}},
    )
    assert direct_template_update.status_code == 400
    assert (
        direct_template_update.json()["detail"]["error"]
        == "facility_template_definition_update_requires_versioned_template_endpoint"
    )

    fetched = client.get(f"/facilities/{facility_id}")
    assert fetched.status_code == 200
    payload = fetched.json()
    assert payload["config"]["fax_template_id"] == "fax_layout_regular_forbidden_v1"
    assert payload["resolved_config"]["fax_template_id"] == "fax_layout_regular_forbidden_v1"

    missing = client.put(
        f"/facilities/{facility_id}/fax-template",
        json={"fax_template_id": "not_registered_template"},
    )
    assert missing.status_code == 400
    assert missing.json()["detail"]["error"] == "fax_template_not_found"


def test_fac00005_facility_contract_exposes_official_current_sheet_schema():
    _clear_facilities()
    client = TestClient(app)
    assert client.get("/facilities").status_code == 200
    fetched = client.get("/facilities/FAC00005")
    assert fetched.status_code == 200
    payload = fetched.json()
    resolved = payload.get("resolved_config") or {}
    override = resolved.get("fax_template_override") or {}
    assert override.get("columns_authoritative") is True
    columns = ((resolved.get("fax_template") or {}).get("columns")) or []
    assert [column.get("header") for column in columns[:10]] == [
        "日付",
        "区分",
        "メニュー",
        "軟菜",
        "袋分け",
        "肉禁",
        "魚禁",
        "変更1",
        "変更2",
        "備考欄",
    ]


def test_fac00002_facility_contract_keeps_unknown_placeholder_quantity_column():
    _clear_facilities()
    client = TestClient(app)
    assert client.get("/facilities").status_code == 200
    fetched = client.get("/facilities/FAC00002")
    assert fetched.status_code == 200
    payload = fetched.json()
    resolved = payload.get("resolved_config") or {}
    columns = ((resolved.get("fax_template") or {}).get("columns")) or []

    assert [column.get("header") for column in columns[:10]] == [
        "日付",
        "区分",
        "メニュー",
        "常食",
        "不明(-)",
        "肉禁",
        "魚禁",
        "変更1",
        "変更2",
        "備考欄",
    ]
    placeholder = columns[4]
    assert placeholder.get("role") == "quantity"
    assert placeholder.get("diet_type") == "placeholder"
    assert placeholder.get("name") == "qty.placeholder_x"
    version_columns = ((resolved.get("facility_template_version") or {}).get("columns")) or []
    version_placeholder = version_columns[4]
    assert version_placeholder.get("column_id") == "col_004_quantity"
    assert (version_placeholder.get("semantic") or {}).get("aggregation_role") == "exclude"
