#!/usr/bin/env python3
"""Export actual registered history metadata without importing app or DB."""
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
from src.api import output_history_contract as contracts  # noqa: E402
from scripts.registered_response_contract import registered_response_contract  # noqa: E402


def response_contract():
    source = (ROOT / "backend/src/api/output_history.py").read_text()
    tree = ast.parse(source)
    result = registered_response_contract(
        main_source=(ROOT / "backend/src/main.py").read_text(), route_source=source,
        module_name="output_history", expected_prefix="/outputs", expected={("/history", "get")},
        models={"OutputHistoryResponse": contracts.OutputHistoryResponse},
        title="Registered Output Generation History", label="output-history",
        schema_builder=models_json_schema,
    )
    handlers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "get_output_history"]
    if len(handlers) != 1:
        raise ValueError("unresolved required history query")
    handler = handlers[0]
    expected = {"order_id": "OrderIdQuery", "expected_workflow_revision": "WorkflowRevisionQuery",
                "page": "PageQuery", "limit": "LimitQuery"}
    optional = {"before_id": ("str | None", str | None, None),
                "validate_records": ("bool", bool, True), "record_id": ("str | None", str | None, None)}
    if (len(handler.args.args) != len(expected) + len(optional)
            or len(handler.args.defaults) != len(optional)
            or [argument.arg for argument in handler.args.args] != [*expected, *optional]
            or [ast.literal_eval(value) for value in handler.args.defaults] != [item[2] for item in optional.values()]):
        raise ValueError("changed history query signature")
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "src.api.output_history_contract"]
    if len(imports) != 1 or any(alias.asname for alias in imports[0].names):
        raise ValueError("unresolved history contract binding")
    if not set(expected.values()).union({"OutputHistoryResponse"}).issubset({alias.name for alias in imports[0].names}):
        raise ValueError("missing history contract binding")
    decorator = handler.decorator_list[0]
    dependencies = next((keyword.value for keyword in decorator.keywords if keyword.arg == "dependencies"), None)
    if dependencies is None or ast.unparse(dependencies) != "[Depends(require_role('operator'))]":
        raise ValueError("history operator authorization required")
    parameters = []
    for argument in handler.args.args:
        annotation = expected.get(argument.arg, optional.get(argument.arg, (None,))[0])
        if ast.unparse(argument.annotation) != annotation:
            raise ValueError("unresolved history query contract")
        required = argument.arg in expected
        value_type = getattr(contracts, annotation) if required else optional[argument.arg][1]
        schema = TypeAdapter(value_type).json_schema()
        if not required:
            schema['default'] = optional[argument.arg][2]
        parameters.append({"name": argument.arg, "in": "query", "required": required, "schema": schema})
    result["paths"]["/outputs/history"]["get"]["parameters"] = parameters
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(response_contract(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
