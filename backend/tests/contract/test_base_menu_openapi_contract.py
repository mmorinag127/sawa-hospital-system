"""Unexecuted response/source checks; serialization doubles are not live proof."""
import ast
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT))
from src.api import base_menus  # noqa: E402
from scripts.export_base_menu_openapi import response_contract  # noqa: E402
from scripts.export_menu_rule_openapi import response_contract as rule_contract  # noqa: E402

BASE = "95d832efe803a0373249a8f6a53abcd185f64e88"


def sources():
    return ((ROOT / "backend/src/main.py").read_text(), (ROOT / "backend/src/api/base_menus.py").read_text())


def test_real_router_matches_response_slice_and_none_runtime_models():
    app = FastAPI()
    app.include_router(base_menus.router, prefix="")
    actual = app.openapi()
    generated = response_contract()
    assert {(r.path, m) for r in base_menus.router.routes for m in r.methods} == {
        ("/base-menus", "GET"), ("/base-menus", "POST"), ("/base-menus/{item_id}", "PUT"),
    }
    for route in base_menus.router.routes:
        assert route.response_model is None
        assert route.include_in_schema is True
        assert len(route.dependencies) == 1
    for path, methods in generated["paths"].items():
        for method, operation in methods.items():
            assert operation["responses"]["200"] == actual["paths"][path][method]["responses"]["200"]
    for name, schema in generated["components"]["schemas"].items():
        assert schema == actual["components"]["schemas"][name]
    assert json.loads((ROOT / "frontend/src/generated/base-menu-openapi.json").read_text()) == generated


def test_runtime_module_ast_and_service_bytes_unchanged():
    name = "backend/src/api/base_menus.py"
    before = ast.parse(subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=ROOT, text=True))
    after = ast.parse((ROOT / name).read_text())
    after.body = [n for n in after.body if not (isinstance(n, ast.ImportFrom) and n.module == "src.api.base_menu_schemas")]
    for node in after.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    decorator.keywords = [k for k in decorator.keywords if k.arg not in {"responses", "response_model"}]
    assert ast.dump(after, include_attributes=False) == ast.dump(before, include_attributes=False)
    service = "backend/src/services/base_menu_service.py"
    assert (ROOT / service).read_bytes() == subprocess.check_output(["git", "show", f"{BASE}:{service}"], cwd=ROOT)


@pytest.mark.parametrize("method,path,service", [
    ("get", "/base-menus", "list_items"),
    ("post", "/base-menus", "replace_items"),
    ("put", "/base-menus/b1", "update_item"),
])
def test_docs_preserve_null_zero_raw_condiments_extra_keys_dates_and_requests(monkeypatch, method, path, service):
    item = {"id": "b1", "revision": 1, "cycle_day": 1, "daypart": None, "category": None,
            "name": "Component", "diet_type": None, "slot_index": 0, "unit_type": None,
            "qty_per_serving": 0, "temp_type": None, "bag_max_qty": None, "bag_max_unit": None,
            "condiments": [0, None, False, {"raw": ["0"]}], "extra_date": datetime(2026, 10, 8)}
    response = {"items": [item], "revision": "opaque-source-revision", "extra": {"raw": "0"}}
    if method == "post":
        response["created"] = 0
    if method == "put":
        response["item"] = {**item, "qty_per_serving": None, "condiments": []}
    calls = []
    def producer(*args):
        calls.append(args)
        return response
    monkeypatch.setattr(base_menus.base_menu_service, service, producer)
    app = FastAPI()
    app.include_router(base_menus.router)
    for route in base_menus.router.routes:
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = lambda: None
    body = {"items": [{"name": "Component", "slot_index": "0"}]} if method == "post" else {"slot_index": "0", "daypart": None}
    result = getattr(TestClient(app), method)(path, **({"json": body, "headers": {"If-Match": "opaque-source-revision"}} if method != "get" else {}))
    assert result.status_code == 200
    assert result.json() == jsonable_encoder({"updated": True, **response} if method == "put" else response)
    assert calls == ([(None,)] if method == "get" else [(body["items"], "opaque-source-revision")] if method == "post" else [("b1", body, "opaque-source-revision")])


def test_actual_serializer_and_snapshot_merge_keep_zero_and_raw_list(monkeypatch):
    service = base_menus.base_menu_service
    item = SimpleNamespace(id="b1", revision=1, cycle_day=1, daypart=None, category=None, name="Component", diet_type=None, slot_index=0)
    raw = service.serialize_item(item)
    schema = response_contract()["components"]["schemas"]["BaseMenuItemResponse"]
    assert set(raw) == set(schema["properties"]) == set(schema["required"])
    assert raw["qty_per_serving"] is None and raw["condiments"] == []
    defaults = {"Component": {"unit_type": "g", "qty_per_serving": 0, "bag_max_qty": 0, "condiments": [None, 0, {"raw": True}]}}
    # Explicit snapshot serialization component source double, not DB/source acceptance.
    monkeypatch.setattr(service, "_defaults_and_sources", lambda *args, **kwargs: (defaults, [("m1", 7)]))
    snapshot = service._snapshot(None, [item], 2, cycle_day=None)
    assert snapshot["items"][0]["qty_per_serving"] == 0
    assert snapshot["items"][0]["bag_max_qty"] == 0
    assert snapshot["items"][0]["condiments"] == defaults["Component"]["condiments"]
    assert snapshot["revision"] == service.menu_edit_revision_service.encode(service.menu_edit_revision_service.BASE_MENU_SCOPE, 2, [("m1", 7)], query={"cycle_day": None}, coverage="full")
    assert schema["properties"]["condiments"]["items"] == {}
    assert schema["additionalProperties"] is True


@pytest.mark.parametrize("replacement", [
    '@router.get("/base-menus", include_in_schema=False,',
    '@router.get("/base-menus", include_in_schema=visibility,',
    '@router.get(selected_path,',
    '@router.api_route("/base-menus", methods=["GET", "DELETE"],',
    '@router.api_route("/base-menus", methods=["GET", "GET"],',
    '@router.api_route("/base-menus", methods=selected_methods,',
])
def test_hidden_unknown_extra_and_duplicate_inventory_refused(replacement):
    main_source, route_source = sources()
    with pytest.raises(ValueError):
        response_contract(main_source=main_source, route_source=route_source.replace('@router.get("/base-menus",', replacement, 1))


def test_async_preserved_extra_async_and_imperative_refused():
    main_source, route_source = sources()
    assert response_contract(main_source=main_source, route_source=route_source.replace("def list_base_menus(", "async def list_base_menus(", 1)) == response_contract()
    for extra in ('\n@router.get("/extra", response_model=None, responses={200: {"model": BaseMenuListResponse}})\nasync def extra():\n    return {}\n', '\nrouter.add_api_route("/extra", list_base_menus)\n'):
        with pytest.raises(ValueError):
            response_contract(main_source=main_source, route_source=route_source + extra)


def test_missing_operation_and_hidden_before_schema_refused(monkeypatch):
    from scripts import export_base_menu_openapi as exporter
    main_source, route_source = sources()
    decorator = '@router.get("/base-menus", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": BaseMenuListResponse}})\n'
    assert decorator in route_source
    with pytest.raises(ValueError, match="missing"):
        response_contract(main_source=main_source, route_source=route_source.replace(decorator, '', 1))
    def forbidden_schema(*args, **kwargs):
        pytest.fail("hidden registration reached schema generation")
    monkeypatch.setattr(exporter, "models_json_schema", forbidden_schema)
    with pytest.raises(ValueError, match="hidden"):
        response_contract(main_source=main_source, route_source=route_source.replace('@router.get("/base-menus",', '@router.get("/base-menus", include_in_schema=False,', 1))


@pytest.mark.parametrize("replacement", [
    'app.include_router(base_menus.router, prefix="", include_in_schema=False)',
    'app.include_router(base_menus.router, prefix=prefix)',
    'app.include_router(dynamic_router, prefix="")',
    'if enabled:\n    app.include_router(base_menus.router, prefix="")',
    '',
    'app.include_router(base_menus.router, prefix="")\napp.include_router(base_menus.router, prefix="")',
])
def test_unknown_hidden_missing_duplicate_registration_refused(replacement):
    main_source, route_source = sources()
    with pytest.raises(ValueError):
        response_contract(main_source=main_source.replace('app.include_router(base_menus.router, prefix="")', replacement, 1), route_source=route_source)


def test_menu_rule_output_and_generated_files_match_integrated_base():
    expected = json.loads(subprocess.check_output(["git", "show", f"{BASE}:frontend/src/generated/menu-rule-openapi.json"], cwd=ROOT, text=True))
    assert rule_contract() == expected
    for name in ("menu-rule-openapi.json", "menu-rule-api.ts"):
        filename = f"frontend/src/generated/{name}"
        assert (ROOT / filename).read_bytes() == subprocess.check_output(["git", "show", f"{BASE}:{filename}"], cwd=ROOT)
