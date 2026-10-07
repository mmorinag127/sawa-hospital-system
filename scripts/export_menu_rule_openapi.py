#!/usr/bin/env python3
"""Registered response-only OpenAPI slice; imports no auth, services or DB."""
import argparse
import ast
import json
import sys
from pathlib import Path
from pydantic.json_schema import models_json_schema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from src.api import menu_rule_schemas  # noqa: E402

EXPECTED = {("", "get"), ("", "post"), ("/{rule_id}", "put"), ("/{rule_id}", "delete")}


def _keywords(call):
    result = {}
    for keyword in call.keywords:
        if keyword.arg is None or keyword.arg in result:
            raise ValueError("unresolved or duplicate registration metadata")
        result[keyword.arg] = keyword.value
    return result


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ValueError("unresolved route metadata") from exc


def _visible(keywords):
    if "include_in_schema" in keywords and _literal(keywords["include_in_schema"]) is not True:
        raise ValueError("hidden or unresolved menu-rule route")


def response_contract(*, main_source=None, route_source=None):
    main = ast.parse(main_source if main_source is not None else (ROOT / "backend/src/main.py").read_text())
    imports = [alias for node in main.body if isinstance(node, ast.ImportFrom) and node.module == "src.api" for alias in node.names if alias.name == "menu_rules"]
    if len(imports) != 1 or imports[0].asname is not None:
        raise ValueError("unresolved menu-rule module binding")
    registrations = []
    allowed_module_names = set()
    top_level_calls = {id(n.value) for n in main.body if isinstance(n, ast.Expr)}
    for call in ast.walk(main):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != "include_router":
            continue
        keywords = _keywords(call)
        if len(call.args) > 1 or (call.args and "router" in keywords):
            raise ValueError("unresolved router registration")
        router = call.args[0] if call.args else keywords.get("router")
        if not isinstance(router, ast.Attribute) or not isinstance(router.value, ast.Name) or router.attr != "router":
            raise ValueError("unresolved router registration")
        if router.value.id != "menu_rules":
            continue
        allowed_module_names.add(id(router.value))
        if id(call) not in top_level_calls or ast.unparse(call.func.value) != "app":
            raise ValueError("unresolved menu-rule registration context")
        if set(keywords) - {"router", "prefix", "include_in_schema"}:
            raise ValueError("unsupported menu-rule registration metadata")
        _visible(keywords)
        prefix = _literal(keywords["prefix"]) if "prefix" in keywords else ""
        if not isinstance(prefix, str) or prefix != "/menu-rules":
            raise ValueError("unexpected menu-rule prefix")
        registrations.append(prefix)
    if any(isinstance(n, ast.Name) and n.id == "menu_rules" and id(n) not in allowed_module_names for n in ast.walk(main)):
        raise ValueError("unresolved menu-rule registration reference")
    if len(registrations) != 1:
        raise ValueError("menu-rule registration must be unique")
    prefix = registrations[0]
    tree = ast.parse(route_source if route_source is not None else (ROOT / "backend/src/api/menu_rules.py").read_text())
    assignments = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "router" for t in n.targets)]
    if len(assignments) != 1 or len(assignments[0].targets) != 1:
        raise ValueError("unresolved router construction")
    constructor = assignments[0].value
    if not isinstance(constructor, ast.Call) or ast.unparse(constructor.func) != "APIRouter" or constructor.args or constructor.keywords:
        raise ValueError("unsupported router construction metadata")
    allowed_router_names = {id(assignments[0].targets[0])}
    operations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute) or ast.unparse(deco.func.value) != "router":
                continue
            allowed_router_names.add(id(deco.func.value))
            if node not in tree.body:
                raise ValueError("unresolved nested route registration")
            keywords = _keywords(deco)
            if set(keywords) - {"path", "methods", "dependencies", "response_model", "responses", "include_in_schema", "status_code"}:
                raise ValueError("unsupported route metadata")
            _visible(keywords)
            if len(deco.args) > 1 or (deco.args and "path" in keywords):
                raise ValueError("unresolved route path")
            path = _literal(deco.args[0] if deco.args else keywords.get("path"))
            if not isinstance(path, str):
                raise ValueError("unresolved route path")
            if deco.func.attr == "api_route":
                methods = _literal(keywords.get("methods"))
                if not isinstance(methods, (list, tuple)) or not methods or any(not isinstance(m, str) for m in methods):
                    raise ValueError("unresolved route methods")
                methods = [method.lower() for method in methods]
            elif deco.func.attr in {"get", "post", "put", "delete", "patch", "head", "options", "trace"}:
                if "methods" in keywords:
                    raise ValueError("unexpected method override")
                methods = [deco.func.attr]
            else:
                raise ValueError("unsupported router operation")
            if "status_code" in keywords and _literal(keywords["status_code"]) != 200:
                raise ValueError("unexpected response status")
            if "response_model" not in keywords or _literal(keywords["response_model"]) is not None:
                raise ValueError("documentation must not validate responses")
            response = keywords.get("responses")
            if not isinstance(response, ast.Dict) or len(response.keys) != 1 or _literal(response.keys[0]) != 200:
                raise ValueError("unexpected response documentation")
            model = response.values[0]
            if not isinstance(model, ast.Dict) or len(model.keys) != 1 or _literal(model.keys[0]) != "model" or not isinstance(model.values[0], ast.Name):
                raise ValueError("unexpected response model reference")
            schema_model = getattr(menu_rule_schemas, model.values[0].id, None)
            if schema_model not in {menu_rule_schemas.MenuRuleListResponse, menu_rule_schemas.MenuRuleSaveResponse, menu_rule_schemas.MenuRuleUpdateResponse, menu_rule_schemas.MenuRuleDeleteResponse}:
                raise ValueError("unresolved response model")
            operations.extend((path, method, node.name, schema_model) for method in methods)
    if any(isinstance(n, ast.Name) and n.id == "router" and id(n) not in allowed_router_names for n in ast.walk(tree)):
        raise ValueError("unresolved router reference or imperative registration")
    identities = [(p, m) for p, m, _, _ in operations]
    if len(identities) != 4 or set(identities) != EXPECTED:
        raise ValueError("missing, duplicate or extra menu-rule operations")
    _, schema = models_json_schema([(model, "serialization") for _, _, _, model in operations], ref_template="#/components/schemas/{model}")
    paths = {}
    for path, method, handler, model in operations:
        paths.setdefault(prefix + path, {})[method] = {"operationId": handler, "responses": {"200": {"description": "Successful Response", "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{model.__name__}"}}}}}}
    return {"openapi": "3.1.0", "info": {"title": "Registered Menu Rule Response Contract", "version": "1"}, "paths": paths, "components": {"schemas": schema["$defs"]}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(response_contract(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        return 0 if args.output.is_file() and args.output.read_text() == content else 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
