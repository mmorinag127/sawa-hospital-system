import base64
import importlib
import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

import src.api.auth as auth_module  # noqa: E402
import src.api.auth_config as auth_config_module  # noqa: E402
import src.api.base_menus as base_menus_api  # noqa: E402
import src.api.facility_master as facility_master_api  # noqa: E402
import src.api.facilities as facilities_api  # noqa: E402
import src.api.menu_masters as menu_masters_api  # noqa: E402
import src.api.menu_rules as menu_rules_api  # noqa: E402
from src.main import app  # noqa: E402


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _set_operator_auth(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("OPERATOR_USER", "operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "secret")
    importlib.reload(auth_module)
    importlib.reload(auth_config_module)
    return _basic_header("operator", "secret")


def test_user2_base_menus_allows_operator(monkeypatch):
    headers = _set_operator_auth(monkeypatch)
    monkeypatch.setattr(base_menus_api.base_menu_service, "list_items", lambda *_args: [{"id": "item-1"}])
    monkeypatch.setattr(base_menus_api.base_menu_service, "replace_items", lambda items: {"created": len(items)})
    monkeypatch.setattr(base_menus_api.base_menu_service, "update_item", lambda *_args, **_kwargs: {"id": "item-1"})

    client = TestClient(app)
    assert client.get("/base-menus", headers=headers).status_code == 200
    assert client.post("/base-menus", json={"items": [{"name": "A", "cycle_day": 1}]}, headers=headers).status_code == 200
    assert client.put("/base-menus/item-1", json={"name": "B"}, headers=headers).status_code == 200


def test_user2_menu_masters_allows_operator(monkeypatch):
    headers = _set_operator_auth(monkeypatch)
    monkeypatch.setattr(menu_masters_api.menu_service, "list_menu_masters", lambda **_kwargs: [{"id": "m1"}])
    monkeypatch.setattr(menu_masters_api.menu_service, "create_menu_master", lambda body: {"id": "m2", **body})
    monkeypatch.setattr(menu_masters_api.menu_service, "update_menu_master", lambda *_args, **_kwargs: {"id": "m1"})

    client = TestClient(app)
    assert client.get("/menu-masters", headers=headers).status_code == 200
    assert client.post("/menu-masters", json={"name": "Menu"}, headers=headers).status_code == 200
    assert client.put("/menu-masters/m1", json={"name": "Menu"}, headers=headers).status_code == 200


def test_user2_menu_rules_allows_operator(monkeypatch):
    headers = _set_operator_auth(monkeypatch)
    monkeypatch.setattr(menu_rules_api.menu_rule_service, "list_rules", lambda *_args: [{"id": "r1"}])
    monkeypatch.setattr(menu_rules_api.menu_rule_service, "create_rule", lambda body: {"id": "r2", **body})
    monkeypatch.setattr(menu_rules_api.menu_rule_service, "update_rule", lambda *_args, **_kwargs: {"id": "r1"})
    monkeypatch.setattr(menu_rules_api.menu_rule_service, "delete_rule", lambda *_args, **_kwargs: True)

    client = TestClient(app)
    assert client.get("/menu-rules", headers=headers).status_code == 200
    assert client.post("/menu-rules", json={"rule_type": "global"}, headers=headers).status_code == 200
    assert client.put("/menu-rules/r1", json={"rule_type": "global"}, headers=headers).status_code == 200
    assert client.delete("/menu-rules/r1", headers=headers).status_code == 200


def test_user2_facility_master_save_allows_operator(monkeypatch):
    headers = _set_operator_auth(monkeypatch)
    sample_master = {"schema_version": "1", "facilities": [{"facility_id": "FAC00001", "facility_name": "Test"}]}
    monkeypatch.setattr(facility_master_api.facility_master_service, "save_master", lambda master: master)

    client = TestClient(app)
    res = client.put("/facility-master", json=sample_master, headers=headers)
    assert res.status_code == 200
    assert res.json()["updated"] is True


def test_user2_facility_basic_update_allows_operator(monkeypatch):
    headers = _set_operator_auth(monkeypatch)
    monkeypatch.setattr(
        facilities_api.facility_service,
        "update_facility",
        lambda facility_id, name, areas: {"id": facility_id, "name": name, "areas": areas},
    )

    client = TestClient(app)
    res = client.put(
        "/facilities/FAC00008",
        json={"name": "佐古", "areas": [{"id": "2F", "name": "2F"}]},
        headers=headers,
    )

    assert res.status_code == 200
    assert res.json() == {"id": "FAC00008", "name": "佐古", "areas": [{"id": "2F", "name": "2F"}]}
