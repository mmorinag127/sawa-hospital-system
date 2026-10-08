"""Source-only monthly import contract; no app/auth/service/DB imports."""
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
from src.api import menu_import_contract as contracts
from scripts.registered_response_contract import registered_response_contract

PREFIX = "/monthly-menus"
BINARY = ("/{month_id}/imports/{import_id}/download", "get")
EXPECTED = {
    ("", "post"), ("/{month_id}/imports", "get"),
    ("/{month_id}/imports/{import_id}", "get"),
    ("/{month_id}/imports/{import_id}/review", "post"),
    ("/{month_id}/imports/{import_id}/apply", "post"),
    ("/{month_id}/entries/{entry_id}/exceptions", "post"),
}
MODELS = {model.__name__: model for model in (
    contracts.SavedImportResponse, contracts.ImportListResponse,
    contracts.ImportApplyResponse, contracts.MonthlyCommandResponse,
)}


def response_contract():
    tree = ast.parse((ROOT / "backend/src/api/menus.py").read_text())
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "src.api.menu_import_contract"]
    if (len(imports) != 1 or any(alias.asname for alias in imports[0].names)
            or {alias.name for alias in imports[0].names} != set(MODELS)):
        raise ValueError("unresolved menu import documentation binding")
    selected = []
    binary = []
    identities = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute)
                    or ast.unparse(decorator.func.value) != "router" or len(decorator.args) != 1):
                continue
            identity = (ast.literal_eval(decorator.args[0]), decorator.func.attr)
            if identity in EXPECTED | {BINARY}:
                identities.append(identity)
                (binary if identity == BINARY else selected).append(node)
    if len(identities) != 7 or set(identities) != EXPECTED | {BINARY}:
        raise ValueError("missing or duplicate selected menu import operation")
    construction = [node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "router" for target in node.targets)]
    route_slice = ast.unparse(ast.Module(body=construction + selected, type_ignores=[]))
    result = registered_response_contract(
        main_source=(ROOT / "backend/src/main.py").read_text(), route_source=route_slice,
        module_name="menus", expected_prefix=PREFIX, expected=EXPECTED, models=MODELS,
        title="Registered Monthly Menu Import API Contract", label="menu-import",
        schema_builder=models_json_schema,
    )
    node = binary[0]
    decorator = node.decorator_list[0]
    keywords = {keyword.arg: keyword.value for keyword in decorator.keywords}
    if (len(keywords) != len(decorator.keywords)
            or set(keywords) != {"dependencies", "response_model", "responses"}
            or ast.literal_eval(keywords["response_model"]) is not None):
        raise ValueError("unresolved binary registration metadata")
    documentation = ast.literal_eval(keywords["responses"])
    if documentation != {200: {"content": {"*/*": {"schema": {"type": "string", "format": "binary"}}}}}:
        raise ValueError("unresolved binary response documentation")
    returns = [statement for statement in node.body if isinstance(statement, ast.Return)]
    if (len(returns) != 1 or not isinstance(returns[0].value, ast.Call)
            or ast.unparse(returns[0].value.func) != "Response" or returns[0].value.args):
        raise ValueError("unresolved binary response producer")
    response_keywords = {keyword.arg: keyword.value for keyword in returns[0].value.keywords}
    if (len(response_keywords) != len(returns[0].value.keywords)
            or set(response_keywords) != {"content", "media_type", "headers"}
            or ast.unparse(response_keywords["content"]) != "source['bytes']"
            or ast.unparse(response_keywords["media_type"]) != "source['media_type']"):
        raise ValueError("unresolved binary source media metadata")
    result["paths"][PREFIX + BINARY[0]] = {"get": {
        "operationId": node.name,
        "responses": {"200": {"description": "Saved source with recorded media type", **documentation[200]}},
    }}
    for node in selected + binary:
        decorator = node.decorator_list[0]
        path = PREFIX + ast.literal_eval(decorator.args[0])
        operation = result["paths"][path][decorator.func.attr]
        parameters = []
        properties = {}
        required = []
        defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + node.args.defaults
        for argument, default in zip(node.args.args, defaults):
            name = argument.arg
            annotation = ast.unparse(argument.annotation)
            if name == "body":
                if annotation != "dict" or default is not None:
                    raise ValueError("unresolved raw JSON body")
                operation["requestBody"] = {"required": True, "content": {"application/json": {
                    "schema": TypeAdapter(dict).json_schema(),
                }}}
                continue
            schema = TypeAdapter(str | None if annotation == "str | None" else str).json_schema()
            if name == "file":
                if annotation != "UploadFile" or ast.unparse(default) != "File(...)":
                    raise ValueError("unresolved original upload parameter")
                properties[name] = {"type": "string", "format": "binary"}
                required.append(name)
                continue
            if annotation not in {"str", "str | None"}:
                raise ValueError("unresolved menu import argument type")
            location = "path" if "{" + name + "}" in path else "query"
            mandatory = default is None
            if default is not None:
                if isinstance(default, ast.Call):
                    if (ast.unparse(default.func) not in {"Form", "Header"}
                            or len(default.args) != 1 or default.keywords
                            or ast.literal_eval(default.args[0]) is not None or annotation != "str | None"):
                        raise ValueError("unresolved optional form/header metadata")
                    if ast.unparse(default.func) == "Form":
                        properties[name] = schema
                        continue
                    location = "header"
                elif ast.literal_eval(default) is not None or annotation != "str | None":
                    raise ValueError("unresolved optional query metadata")
            parameters.append({"name": name.replace("_", "-") if location == "header" else name,
                               "in": location, "required": mandatory, "schema": schema})
        operation["parameters"] = parameters
        if properties:
            operation["requestBody"] = {"required": True, "content": {"multipart/form-data": {
                "schema": {"type": "object", "properties": properties, "required": required},
            }}}
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
