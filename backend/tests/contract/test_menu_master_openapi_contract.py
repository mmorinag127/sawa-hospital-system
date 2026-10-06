import json
import pathlib
import subprocess
import sys
from unittest.mock import create_autospec

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))
sys.path.append(str(ROOT.parent))

import src.api.menu_masters as menu_masters_api  # noqa: E402
from scripts.export_menu_master_openapi import contract_app  # noqa: E402


def test_menu_master_openapi_exposes_complete_serializer_response_contract():
    schema = contract_app().openapi()
    components = schema["components"]["schemas"]
    item = components["MenuMasterResponse"]
    assert item["required"] == [
        "id", "revision", "name", "normalized_name", "unit_type", "qty_per_serving",
        "bag_max_qty", "bag_max_unit", "temp_type", "daypart", "category", "condiments",
    ]
    assert set(item["properties"]) == set(item["required"])
    assert schema["paths"]["/menu-masters"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/MenuMasterListResponse"
    }
    assert set(schema["paths"]) == {"/menu-masters", "/menu-masters/{item_id}"}


def test_offline_menu_master_openapi_export_is_current(tmp_path):
    output = tmp_path / "menu-master-openapi.json"
    command = [sys.executable, str(ROOT.parent / "scripts/export_menu_master_openapi.py"), "--output", str(output)]
    subprocess.run(command, cwd=ROOT.parent, check=True)
    exported = json.loads(output.read_text(encoding="utf-8"))
    assert exported == contract_app().openapi()
    subprocess.run([*command, "--check"], cwd=ROOT.parent, check=True)


@pytest.mark.parametrize(
    ("method", "path", "body", "service_name", "response"),
    [
        ("get", "/menu-masters", None, "list_menu_masters_page", "page"),
        ("get", "/menu-masters/m1", None, "get_menu_master", "item"),
        ("post", "/menu-masters", {"name": "Menu"}, "create_menu_master", "item"),
        ("put", "/menu-masters/m1", {"name": "Menu", "revision": 1}, "save_menu_master", "updated"),
    ],
)
def test_schema_only_responses_preserve_legacy_runtime_payload_without_filtering(
    monkeypatch, method, path, body, service_name, response,
):
    item = {
        "id": "m1", "revision": 1, "name": "Menu", "normalized_name": None,
        "unit_type": None, "qty_per_serving": 0, "bag_max_qty": 9,
        "bag_max_unit": None, "temp_type": None, "daypart": None,
        "category": None, "condiments": [1, {"legacy": True}, None],
        "legacy_extra": {"unchanged": True},
    }
    page = {"items": [item], "total": 1, "offset": 0, "limit": 50, "page_extra": 9}
    values = {
        "page": page,
        "item": item,
        "updated": item,
    }
    monkeypatch.setattr(menu_masters_api.menu_service, service_name, create_autospec(
        getattr(menu_masters_api.menu_service, service_name), return_value=values[response],
    ))
    app = contract_app()
    for dependency in menu_masters_api.router.dependencies:
        app.dependency_overrides[dependency.dependency] = lambda: None
    client = TestClient(app)
    actual = getattr(client, method)(path) if body is None else getattr(client, method)(path, json=body)
    expected = page if response == "page" else {"item": item}
    if response == "updated":
        expected = {"updated": True, "item": item}
    assert actual.status_code == 200
    assert actual.json() == expected
