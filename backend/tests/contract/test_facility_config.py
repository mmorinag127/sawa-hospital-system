import sys
import pathlib
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.main import app  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models.facility import Facility, FacilityArea, FacilityConfig  # noqa: E402
from src.models.facility_template_version import FacilityTemplateVersion  # noqa: E402
from src.services import config_service, facility_service, facility_template_version_service  # noqa: E402


@pytest.fixture(autouse=True)
def _reload_config_cache_after_test():
    yield
    config_service.reload_configs()


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


def test_facility_list_exposes_master_facilities_without_db_sync():
    _clear_facilities()
    client = TestClient(app)

    fetched = client.get("/facilities")

    assert fetched.status_code == 200
    facilities = fetched.json()["facilities"]
    assert any(item["id"] == "FAC00016" and item["name"] == "いこいの森プラス" for item in facilities)
    with session_scope() as session:
        assert session.query(Facility).count() == 0


def test_user_added_master_facility_is_available_for_order_step1_options(monkeypatch, tmp_path):
    _clear_facilities()
    source = config_service.load_facility_master()
    next_master = {
        **source,
        "facilities": [
            *(source.get("facilities") or []),
            {
                "facility_id": "FAC99991",
                "facility_name": "ケアホーム長生苑",
                "aliases": ["長生苑"],
                "areas": [],
                "fax_template_id": "fax_layout_regular_forbidden_v1",
                "fax_template_ids": ["fax_layout_regular_forbidden_v1"],
            },
        ],
    }
    master_path = tmp_path / "facility_master.template.json"
    master_path.write_text(json.dumps(next_master, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config_service, "FACILITY_MASTER_PATH", master_path)
    config_service.reload_configs()
    client = TestClient(app)

    listed = client.get("/facilities")
    fetched = client.get("/facilities/FAC99991")

    assert listed.status_code == 200
    assert any(item["id"] == "FAC99991" and item["name"] == "ケアホーム長生苑" for item in listed.json()["facilities"])
    assert fetched.status_code == 200
    payload = fetched.json()
    assert payload["facility"]["id"] == "FAC99991"
    assert payload["facility"]["name"] == "ケアホーム長生苑"
    assert payload["resolved_config"]["facility_id"] == "FAC99991"


def test_facility_master_save_materializes_new_facility_rows(monkeypatch, tmp_path):
    _clear_facilities()
    source = config_service.load_facility_master()
    master_path = tmp_path / "facility_master.template.json"
    master_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config_service, "FACILITY_MASTER_PATH", master_path)
    config_service.reload_configs()
    client = TestClient(app)

    next_master = {
        **source,
        "facilities": [
            *(source.get("facilities") or []),
            {
                "facility_id": "FAC99992",
                "facility_name": "ケアホーム長生苑",
                "aliases": ["長生苑"],
                "areas": [{"id": "2F", "name": "2F"}],
            },
        ],
    }
    saved = client.put("/facility-master", json=next_master)
    listed = client.get("/facilities")
    fetched = client.get("/facilities/FAC99992")

    assert saved.status_code == 200
    assert listed.status_code == 200
    assert any(item["id"] == "FAC99992" and item["name"] == "ケアホーム長生苑" for item in listed.json()["facilities"])
    assert fetched.status_code == 200
    assert fetched.json()["facility"]["id"] == "FAC99992"
    with session_scope() as session:
        facility = session.get(Facility, "FAC99992")
        assert facility is not None
        assert facility.name == "ケアホーム長生苑"
        assert [area.name for area in facility.areas] == ["2F"]


def test_facility_master_save_materializes_facility_config(monkeypatch, tmp_path):
    _clear_facilities()
    source = config_service.load_facility_master()
    master_path = tmp_path / "facility_master.template.json"
    master_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config_service, "FACILITY_MASTER_PATH", master_path)
    config_service.reload_configs()
    client = TestClient(app)

    next_master = {
        **source,
        "facilities": [
            {
                "facility_id": "FAC99993",
                "facility_name": "ケアホーム長生庵",
                "aliases": ["長生庵"],
                "areas": [{"id": "ARE001", "name": "本館"}],
                "fax_template_id": "fax_fac00002_v1",
                "fax_template_ids": ["fax_fac00002_v1"],
            },
        ],
    }
    saved = client.put("/facility-master", json=next_master)
    fetched = client.get("/facilities/FAC99993")

    assert saved.status_code == 200
    assert saved.json()["source"] == "db"
    assert fetched.status_code == 200
    payload = fetched.json()
    assert payload["config"]["fax_template_id"] == "fax_fac00002_v1"
    assert payload["resolved_config"]["fax_template_id"] == "fax_fac00002_v1"
    with session_scope() as session:
        config = session.get(FacilityConfig, "FAC99993")
        assert config is not None
        assert "facility_template_source" not in config.config_json


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
    assert "fax_template_id" not in resolved
    assert resolved.get("facility_template_version") is None
    with session_scope() as session:
        versions = (
            session.query(FacilityTemplateVersion)
            .filter(FacilityTemplateVersion.facility_id == facility_id)
            .all()
        )
        assert versions == []


def test_operator_override_columns_preserve_master_body_merge_policy():
    _clear_facilities()
    facility_id = "FAC00007"
    master = config_service.load_facility_master()
    master_facility = next(item for item in master.get("facilities", []) if item.get("facility_id") == facility_id)
    stale_override = {
        "columns_authoritative": True,
        "columns": [
            column
            for column in master_facility["fax_template_override"]["columns"]
            if column["role"] in {"date", "daypart", "menu_name", "quantity", "note"}
        ],
    }
    with session_scope() as session:
        session.add(Facility(id=facility_id, name=master_facility["facility_name"]))
        session.add(
            FacilityConfig(
                facility_id=facility_id,
                config_json={
                    "fax_template_id": master_facility["fax_template_id"],
                    "facility_template_source": "operator_override",
                    "fax_template_override": stale_override,
                },
            )
        )

    resolved = config_service.get_facility_config(facility_id)

    assert resolved is not None
    assert resolved["fax_template"]["body_merge_policy"] == master_facility["fax_template_override"]["body_merge_policy"]


def test_facility_template_registration_materializes_master_facility():
    _clear_facilities()
    client = TestClient(app)

    update = client.put(
        "/facilities/FAC00002/fax-template",
        json={"fax_template_id": "fax_layout_regular_forbidden_v1"},
    )

    assert update.status_code == 410
    assert update.json()["detail"]["error"] == "legacy_fax_template_registry_disabled"
    with session_scope() as session:
        facility = session.get(Facility, "FAC00002")
        assert facility is None
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
    assert options.json().get("templates") == []

    update = client.put(
        f"/facilities/{facility_id}/fax-template",
        json={"fax_template_id": "fax_layout_regular_forbidden_v1"},
    )
    assert update.status_code == 410
    assert update.json()["detail"]["error"] == "legacy_fax_template_registry_disabled"

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
    assert payload["config"] == {}

    missing = client.put(
        f"/facilities/{facility_id}/fax-template",
        json={"fax_template_id": "not_registered_template"},
    )
    assert missing.status_code == 410
    assert missing.json()["detail"]["error"] == "legacy_fax_template_registry_disabled"


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


def test_fac00004_current_columns_preserve_physical_source_indexes():
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

    assert [column.get("header") for column in columns[:11]] == [
        "日付",
        "区分",
        "メニュー",
        "常食",
        "通所",
        "職員",
        "肉禁",
        "魚禁",
        "揚げ物禁",
        "変更1",
        "備考欄",
    ]
    assert [column.get("source_index") for column in columns[:11]] == [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    version_columns = ((resolved.get("facility_template_version") or {}).get("columns")) or []
    validation = facility_template_version_service.validate_template_columns(version_columns)
    assert validation["errors"] == []
    assert version_columns[2].get("column_id") == "col_003_menu_name"
    assert version_columns[3].get("column_id") == "col_004_quantity"
    assert version_columns[4].get("column_id") == "col_005_quantity"


def test_facility_master_columns_define_source_indexes_without_runtime_enrichment():
    master = config_service.load_facility_master()
    missing = []
    for facility in master.get("facilities", []):
        facility_id = facility.get("facility_id")
        columns = ((facility.get("fax_template_override") or {}).get("columns")) or []
        for column in columns:
            if column.get("source_index") is None:
                missing.append((facility_id, column.get("index"), column.get("header")))
    assert missing == []


def test_facility_config_does_not_enrich_missing_source_indexes_from_source_workbook():
    _clear_facilities()
    facility_id = "FAC_SOURCE_INDEX_BLOCK"
    with session_scope() as session:
        session.add(Facility(id=facility_id, name="Source Index Block Facility"))
        session.add(
            FacilityConfig(
                facility_id=facility_id,
                config_json={
                    "facility_template_source": "operator_override",
                    "fax_template_id": "fax_layout_regular_forbidden_v1",
                    "fax_template_override": {
                        "columns_authoritative": True,
                        "columns": [
                            {"index": 0, "role": "date", "header": "日付"},
                            {"index": 1, "role": "daypart", "header": "区分"},
                            {"index": 2, "role": "menu_name", "header": "メニュー"},
                            {
                                "index": 3,
                                "role": "quantity",
                                "header": "常食",
                                "diet_type": "regular",
                                "area_id": "X",
                            },
                        ],
                    },
                },
            )
        )

    resolved = config_service.get_facility_config(facility_id)
    columns = ((resolved.get("fax_template") or {}).get("columns")) or []
    assert [column.get("source_index") for column in columns[:4]] == [None, None, None, None]
    validation = facility_template_version_service.validate_template_columns(columns)
    assert "template_source_index_missing" in validation["errors"]


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
    assert [column.get("header") for column in columns] == [
        "日付",
        "区分",
        "メニュー",
        "常食2F",
        "常食3F",
        "軟菜2F",
        "軟菜3F",
        "ミキサー2F",
        "ミキサー3F",
        "魚禁2F",
        "魚禁3F",
        "魚禁2F",
        "魚禁3F",
        "魚禁2F",
        "魚禁3F",
        "備考",
    ]
    assert [column.get("source_index") for column in columns] == [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    assert [column.get("name") for column in columns[3:15]] == [
        "qty.regular_2f",
        "qty.regular_3f",
        "qty.soft_2f",
        "qty.soft_3f",
        "qty.mixer_2f",
        "qty.mixer_3f",
        "qty.no_fish_regular_2f",
        "qty.no_fish_regular_3f",
        "qty.no_fish_soft_2f",
        "qty.no_fish_soft_3f",
        "qty.no_fish_mixer_2f",
        "qty.no_fish_mixer_3f",
    ]
