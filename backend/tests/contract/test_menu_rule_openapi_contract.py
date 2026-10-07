"""Deferred contract checks; service doubles test serialization, not live adoption."""
import ast
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT))
from src.api import menu_rules  # noqa: E402
from scripts.export_menu_rule_openapi import response_contract  # noqa: E402

BASE = "9e94528ad972862bb5f3cf4f5c409cec360bf1e1"


def contract_app():
    app = FastAPI()
    app.include_router(menu_rules.router, prefix="/menu-rules")
    return app


def test_actual_registry_and_response_schema_matches_generated_slice():
    app = contract_app()
    generated = response_contract()
    actual = app.openapi()
    inventory = {(route.path, method) for route in menu_rules.router.routes for method in route.methods}
    assert inventory == {("", "GET"), ("", "POST"), ("/{rule_id}", "PUT"), ("/{rule_id}", "DELETE")}
    for path, operations in generated["paths"].items():
        for method, operation in operations.items():
            assert operation["responses"]["200"] == actual["paths"][path][method]["responses"]["200"]
    assert generated["components"]["schemas"] == {key: actual["components"]["schemas"][key] for key in generated["components"]["schemas"]}
    for route in menu_rules.router.routes:
        assert route.response_model is None
        assert len(route.dependencies) == 1
    assert json.loads((ROOT / "frontend/src/generated/menu-rule-openapi.json").read_text()) == generated


def test_route_functions_signatures_auth_and_errors_are_unchanged():
    path = "backend/src/api/menu_rules.py"
    old = ast.parse(subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT, text=True))
    new = ast.parse((ROOT / path).read_text())
    def normalized(tree):
        tree.body = [node for node in tree.body if not (isinstance(node, ast.ImportFrom) and node.module == "src.api.menu_rule_schemas")]
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call):
                        deco.keywords = [key for key in deco.keywords if key.arg not in {"response_model", "responses"}]
        return ast.dump(tree, include_attributes=False)
    assert normalized(new) == normalized(old)


@pytest.mark.parametrize("method,path,service,wrapper", [
    ("get", "/menu-rules", "list_rules", "list"),
    ("post", "/menu-rules", "create_rule", "save"),
    ("put", "/menu-rules/m1", "update_rule", "update"),
    ("delete", "/menu-rules/m1", "delete_rule", "delete"),
])
def test_docs_do_not_filter_or_coerce_runtime_response(monkeypatch, method, path, service, wrapper):
    item = {"id": "m1", "revision": 1, "rule_type": "menu", "qty_per_serving": 0,
            "priority": None, "active": False, "unknown": [None, 0, "0", {"date": datetime(2026, 10, 8)}]}
    response = {"rules": [item], "revision": None} if wrapper == "list" else {"rule": item, "revision": "opaque"}
    if wrapper == "delete":
        response = {"deleted": True, "revision": "opaque"}
    response["extra"] = {"raw": "0", "null": None, "zero": 0}
    monkeypatch.setattr(menu_rules.menu_rule_service, service, lambda *args: response)
    app = contract_app()
    for route in menu_rules.router.routes:
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = lambda: None
    client = TestClient(app)
    result = getattr(client, method)(path, **({"json": {"qty_per_serving": "0"}, "headers": {"If-Match": "opaque"}} if method in {"post", "put"} else {}))
    assert result.status_code == 200
    assert result.json() == jsonable_encoder({"updated": True, **response} if wrapper == "update" else response)


def test_schema_fields_match_serializer_and_nullable_zero_contract():
    schema = response_contract()["components"]["schemas"]
    tree = ast.parse((ROOT / "backend/src/services/menu_rule_service.py").read_text())
    serializer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_serialize_rule")
    fields = {ast.literal_eval(k) for k in serializer.body[0].value.keys}
    assert set(schema["MenuRuleResponse"]["properties"]) == fields
    assert set(schema["MenuRuleResponse"]["required"]) == fields
    assert schema["MenuRuleResponse"]["additionalProperties"] is True
    for name in ("qty_per_serving", "priority"):
        assert {item["type"] for item in schema["MenuRuleResponse"]["properties"][name]["anyOf"]} == ({"number", "null"} if name == "qty_per_serving" else {"integer", "null"})


def source_inputs():
    return ((ROOT / "backend/src/main.py").read_text(),
            (ROOT / "backend/src/api/menu_rules.py").read_text())


@pytest.mark.parametrize("include", ["False", "None", "visibility"])
def test_hidden_or_unresolved_route_stops_before_schema(monkeypatch, include):
    from scripts import export_menu_rule_openapi as exporter
    main_source, route_source = source_inputs()
    route_source = route_source.replace('@router.get("",', f'@router.get("", include_in_schema={include},', 1)
    def forbidden_schema(*args, **kwargs):
        pytest.fail("hidden route reached schema generation")
    monkeypatch.setattr(exporter, "models_json_schema", forbidden_schema)
    with pytest.raises(ValueError, match="hidden|unresolved"):
        response_contract(main_source=main_source, route_source=route_source)


def test_async_existing_operation_is_not_omitted():
    main_source, route_source = source_inputs()
    assert response_contract(main_source=main_source, route_source=route_source.replace("def list_menu_rules(", "async def list_menu_rules(", 1)) == response_contract()


@pytest.mark.parametrize("extra", [
    '\n@router.get("/extra", response_model=None, responses={200: {"model": MenuRuleListResponse}})\nasync def extra_route():\n    return {}\n',
    '\n@router.get("", response_model=None, responses={200: {"model": MenuRuleListResponse}})\nasync def duplicate_route():\n    return {}\n',
    '\nrouter.add_api_route("/extra", list_menu_rules, methods=["GET"])\n',
])
def test_extra_async_duplicate_or_imperative_registration_refused(extra):
    main_source, route_source = source_inputs()
    with pytest.raises(ValueError, match="extra|duplicate|unresolved"):
        response_contract(main_source=main_source, route_source=route_source + extra)


@pytest.mark.parametrize("replacement", [
    '@router.api_route("", methods=["GET", "PATCH"],',
    '@router.api_route("", methods=["GET", "GET"],',
    '@router.api_route("", methods=selected_methods,',
    '@router.get(selected_path,',
])
def test_multimethod_extra_duplicate_or_unresolved_metadata_refused(replacement):
    main_source, route_source = source_inputs()
    with pytest.raises(ValueError, match="extra|duplicate|unresolved"):
        response_contract(main_source=main_source, route_source=route_source.replace('@router.get("",', replacement, 1))


def test_literal_api_route_method_and_visible_metadata_preserve_schema():
    main_source, route_source = source_inputs()
    changed = route_source.replace('@router.get("",', '@router.api_route("", methods=["GET"], include_in_schema=True,', 1)
    assert response_contract(main_source=main_source, route_source=changed) == response_contract()


@pytest.mark.parametrize("replacement", [
    'app.include_router(menu_rules.router, prefix="/menu-rules", include_in_schema=False)',
    'app.include_router(menu_rules.router, prefix=selected_prefix)',
    'app.include_router(selected_router, prefix="/menu-rules")',
    'app.include_router(menu_rules.router, prefix="/menu-rules", **metadata)',
    'if enabled:\n    app.include_router(menu_rules.router, prefix="/menu-rules")',
])
def test_hidden_or_unresolved_source_registration_refused(replacement):
    main_source, route_source = source_inputs()
    changed = main_source.replace('app.include_router(menu_rules.router, prefix="/menu-rules")', replacement, 1)
    with pytest.raises(ValueError, match="hidden|unresolved"):
        response_contract(main_source=changed, route_source=route_source)


def test_missing_route_is_refused():
    main_source, route_source = source_inputs()
    route_source = route_source.replace('@router.get("", dependencies=[Depends(require_role("operator"))], response_model=None, responses={200: {"model": MenuRuleListResponse}})\n', '', 1)
    with pytest.raises(ValueError, match="missing"):
        response_contract(main_source=main_source, route_source=route_source)
