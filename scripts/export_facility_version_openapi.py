#!/usr/bin/env python3
"""Offline slice of actual registered facility routes and Pydantic metadata."""
import argparse
import ast
import json
from pathlib import Path
import sys

from pydantic import TypeAdapter
from pydantic.json_schema import models_json_schema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))
from src.api import facility_version_contract as contracts  # noqa: E402
from scripts.registered_response_contract import registered_response_contract  # noqa: E402

EXPECTED = {
    ("/{facility_id}/resolved", "get"),
    ("/{facility_id}/versions", "get"),
    ("/{facility_id}/versions", "post"),
    ("/{facility_id}/versions/{version_id}", "put"),
}
MODELS = {model.__name__: model for model in (
    contracts.DatedFacilityResponse,
    contracts.FacilityVersionsResponse,
    contracts.FacilityVersionSaveResponse,
)}


def response_contract():
    source = (ROOT / "backend/src/api/facilities.py").read_text()
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "src.api.facility_version_contract"]
    if len(imports) != 1 or any(alias.asname for alias in imports[0].names):
        raise ValueError("unresolved facility contract metadata import")
    if not set(MODELS).union({"FacilityVersionBody"}).issubset(
            {alias.name for alias in imports[0].names}):
        raise ValueError("missing registered facility contract metadata")
    selected = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (not isinstance(decorator, ast.Call)
                    or not isinstance(decorator.func, ast.Attribute)
                    or ast.unparse(decorator.func.value) != "router"
                    or not decorator.args):
                continue
            path = ast.literal_eval(decorator.args[0])
            if (path, decorator.func.attr) in EXPECTED:
                selected.append(node)
    construction = [node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "router"
                            for target in node.targets)]
    # Preserve the actual decorators/signatures/bodies; never register substitute routes.
    route_slice = ast.unparse(ast.Module(body=construction + selected, type_ignores=[]))
    result = registered_response_contract(
        main_source=(ROOT / "backend/src/main.py").read_text(),
        route_source=route_slice, module_name="facilities", expected_prefix="/facilities",
        expected=EXPECTED, models=MODELS,
        title="Registered Facility Version API Contract", label="facility-version",
        schema_builder=models_json_schema,
    )
    for node in selected:
        decorator = node.decorator_list[0]
        path = "/facilities" + ast.literal_eval(decorator.args[0])
        method = decorator.func.attr
        operation = result["paths"][path][method]
        parameters = []
        for argument in node.args.args:
            name = argument.arg
            annotation = ast.unparse(argument.annotation)
            if name == "body":
                if annotation != "FacilityVersionBody" or method not in {"post", "put"}:
                    raise ValueError("unresolved dictionary body metadata")
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {
                        "schema": TypeAdapter(contracts.FacilityVersionBody).json_schema(),
                    }},
                }
            elif name in {"facility_id", "version_id"}:
                if annotation != "str" or "{" + name + "}" not in path:
                    raise ValueError("unresolved facility path metadata")
                parameters.append({"name": name, "in": "path", "required": True,
                                   "schema": TypeAdapter(str).json_schema()})
            elif name == "effective_date":
                if annotation != "str | None" or method != "get" or not path.endswith("/resolved"):
                    raise ValueError("unresolved dated facility query metadata")
                if len(node.args.defaults) != 1 or ast.literal_eval(node.args.defaults[0]) is not None:
                    raise ValueError("changed dated facility query default")
                parameters.append({"name": name, "in": "query", "required": False,
                                   "schema": TypeAdapter(str | None).json_schema()})
            else:
                raise ValueError("unexpected facility contract argument")
        operation["parameters"] = parameters
        if method in {"post", "put"} and "requestBody" not in operation:
            raise ValueError("missing facility write body")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
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
