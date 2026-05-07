import sys
import pathlib
from fastapi.testclient import TestClient
from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.facility import Facility, FacilityArea, FacilityConfig  # noqa: E402
from src.models.facility_template_version import FacilityTemplateVersion  # noqa: E402
from src.services import config_service, facility_service, facility_template_version_service  # noqa: E402


def _clear_facilities():
    with session_scope() as session:
        session.execute(delete(FacilityTemplateVersion))
        session.execute(delete(FacilityConfig))
        session.execute(delete(FacilityArea))
        session.execute(delete(Facility))


def _seed_facilities_from_master():
    with session_scope() as session:
        facility_service._sync_facilities_from_master(session)  # noqa: SLF001


def _ensure_active_template_version_for_facility(facility_id: str):
    with session_scope() as session:
        facility_template_version_service.ensure_active_template_version_from_resolved_config(
            session,
            facility_id=facility_id,
            facility_config=config_service.get_facility_config(facility_id),
            created_by="test-explicit-template-registration",
        )


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


def test_facility_list_does_not_sync_master_on_read():
    _clear_facilities()
    client = TestClient(app)

    fetched = client.get("/facilities")

    assert fetched.status_code == 200
    assert fetched.json()["facilities"] == []
    with session_scope() as session:
        assert session.query(Facility).count() == 0


def test_facility_get_does_not_create_template_version_from_resolved_config():
    _clear_facilities()
    facility_id = "FAC_READ_NO_CREATE"
    with session_scope() as session:
        session.add(Facility(id=facility_id, name="Read No Create Facility"))
        session.add(FacilityConfig(facility_id=facility_id, config_json={"fax_template_id": "fax_layout_regular_forbidden_v1"}))
    client = TestClient(app)

    fetched = client.get(f"/facilities/{facility_id}")

    assert fetched.status_code == 200
    resolved = fetched.json().get("resolved_config") or {}
    assert resolved.get("fax_template_id") == "fax_layout_regular_forbidden_v1"
    assert resolved.get("facility_template_version") is None
    with session_scope() as session:
        versions = (
            session.query(FacilityTemplateVersion)
            .filter(FacilityTemplateVersion.facility_id == facility_id)
            .all()
        )
        assert versions == []


def test_facility_template_registration_does_not_sync_master_for_missing_facility():
    _clear_facilities()
    client = TestClient(app)

    update = client.put(
        "/facilities/FAC00002/fax-template",
        json={"fax_template_id": "fax_layout_regular_forbidden_v1"},
    )

    assert update.status_code == 404
    with session_scope() as session:
        assert session.query(Facility).count() == 0
        assert session.query(FacilityTemplateVersion).count() == 0


def test_active_template_version_import_does_not_sync_master_for_missing_facility():
    _clear_facilities()

    with session_scope() as session:
        version = facility_template_version_service.ensure_active_template_version_from_resolved_config(
            session,
            facility_id="FAC00002",
            facility_config=config_service.get_facility_config("FAC00002"),
            created_by="test-read-boundary",
        )

        assert version is None
        assert session.query(Facility).count() == 0
        assert session.query(FacilityTemplateVersion).count() == 0


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
    _seed_facilities_from_master()
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
    _seed_facilities_from_master()
    _ensure_active_template_version_for_facility("FAC00002")
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
    assert placeholder.get("source_index") == 5
    version_columns = ((resolved.get("facility_template_version") or {}).get("columns")) or []
    version_placeholder = version_columns[4]
    assert version_placeholder.get("column_id") == "col_005_quantity"
    assert (version_placeholder.get("semantic") or {}).get("aggregation_role") == "exclude"


def test_fac00007_facility_contract_keeps_repo_canonical_placeholder_column():
    _clear_facilities()
    _seed_facilities_from_master()
    _ensure_active_template_version_for_facility("FAC00007")
    client = TestClient(app)
    assert client.get("/facilities").status_code == 200
    fetched = client.get("/facilities/FAC00007")
    assert fetched.status_code == 200
    payload = fetched.json()
    resolved = payload.get("resolved_config") or {}
    columns = ((resolved.get("fax_template") or {}).get("columns")) or []

    assert [column.get("header") for column in columns[:10]] == [
        "日付",
        "区分",
        "メニュー",
        "常食",
        "-",
        "肉禁",
        "魚禁",
        "変更1",
        "変更2",
        "備考欄",
    ]
    assert [column.get("source_index") for column in columns[:10]] == [0, 1, 3, 4, 5, 6, 7, 8, 9, 10]
    version_columns = ((resolved.get("facility_template_version") or {}).get("columns")) or []
    validation = facility_template_version_service.validate_template_columns(version_columns)
    assert validation["errors"] == []
    placeholder = version_columns[4]
    assert placeholder.get("diet_type") == "placeholder"
    assert (placeholder.get("semantic") or {}).get("aggregation_role") == "exclude"


def test_fac00004_aux_columns_preserve_physical_source_indexes():
    _clear_facilities()
    _seed_facilities_from_master()
    _ensure_active_template_version_for_facility("FAC00004")
    client = TestClient(app)
    assert client.get("/facilities").status_code == 200
    fetched = client.get("/facilities/FAC00004")
    assert fetched.status_code == 200
    payload = fetched.json()
    resolved = payload.get("resolved_config") or {}
    columns = ((resolved.get("fax_template") or {}).get("columns")) or []

    assert [column.get("header") for column in columns[:13]] == [
        "日付",
        "区分",
        "副区分",
        "メニュー",
        "合計",
        "常食",
        "通所",
        "職員",
        "肉禁",
        "魚禁",
        "揚げ物禁",
        "変更1",
        "備考欄",
    ]
    assert [column.get("source_index") for column in columns[:13]] == list(range(13))
    version_columns = ((resolved.get("facility_template_version") or {}).get("columns")) or []
    validation = facility_template_version_service.validate_template_columns(version_columns)
    assert validation["errors"] == []
    assert version_columns[2].get("column_id") == "col_002_aux"
    assert version_columns[4].get("column_id") == "col_004_aux"
    assert version_columns[5].get("column_id") == "col_005_quantity"


def test_fac00003_stale_non_authoritative_override_uses_repo_canonical_columns():
    _clear_facilities()
    _seed_facilities_from_master()
    client = TestClient(app)
    assert client.get("/facilities").status_code == 200
    stale_override = {
        "fax_template_id": "fax_layout_floor_2f3f_v1",
        "fax_template_override": {
            "columns": [
                {"index": 0, "role": "date", "header": "日付"},
                {"index": 1, "role": "daypart", "header": "区分"},
                {"index": 2, "role": "menu_name", "header": "メニュー"},
                {"index": 3, "role": "quantity", "header": "常食花", "name": "qty.regular_2f", "diet_type": "regular", "area_id": "2F"},
                {"index": 4, "role": "quantity", "header": "常食月", "name": "qty.regular_3f", "diet_type": "regular", "area_id": "3F"},
                {"index": 5, "role": "quantity", "header": "軟菜花", "name": "qty.soft_2f", "diet_type": "soft", "area_id": "2F"},
                {"index": 6, "role": "quantity", "header": "軟菜月", "name": "qty.soft_3f", "diet_type": "soft", "area_id": "3F"},
                {"index": 7, "role": "quantity", "header": "ミキサー花", "name": "qty.mixer_2f", "diet_type": "mixer", "area_id": "2F"},
                {"index": 8, "role": "quantity", "header": "ミキサー月", "name": "qty.mixer_3f", "diet_type": "mixer", "area_id": "3F"},
                {"index": 9, "role": "quantity", "header": "魚禁(常食)", "name": "qty.regular_x", "diet_type": "regular", "area_id": "X"},
                {"index": 10, "role": "note", "header": "備考"},
            ]
        },
    }
    with session_scope() as session:
        session.execute(delete(FacilityConfig).where(FacilityConfig.facility_id == "FAC00003"))
        session.add(FacilityConfig(facility_id="FAC00003", config_json=stale_override))

    fetched = client.get("/facilities/FAC00003")

    assert fetched.status_code == 200
    columns = ((fetched.json().get("resolved_config") or {}).get("fax_template") or {}).get("columns") or []
    assert [column.get("header") for column in columns[:10]] == [
        "日付",
        "区分",
        "メニュー",
        "花",
        "月",
        "花",
        "月",
        "花",
        "月",
        "備考",
    ]
    assert [column.get("source_index") for column in columns[:10]] == [0, 1, 3, 4, 5, 6, 7, 8, 9, 10]
