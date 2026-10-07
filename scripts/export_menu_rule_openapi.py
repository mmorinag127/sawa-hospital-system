#!/usr/bin/env python3
"""Registered response-only OpenAPI slice; imports no auth, services or DB."""
import argparse
import json
import sys
from pathlib import Path
from pydantic.json_schema import models_json_schema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))
from src.api import menu_rule_schemas  # noqa: E402
from scripts.registered_response_contract import registered_response_contract  # noqa: E402

EXPECTED = {("", "get"), ("", "post"), ("/{rule_id}", "put"), ("/{rule_id}", "delete")}


def response_contract(*, main_source=None, route_source=None):
    return registered_response_contract(
        main_source=main_source if main_source is not None else (ROOT / "backend/src/main.py").read_text(),
        route_source=route_source if route_source is not None else (ROOT / "backend/src/api/menu_rules.py").read_text(),
        module_name="menu_rules", expected_prefix="/menu-rules", expected=EXPECTED,
        models={model.__name__: model for model in (
            menu_rule_schemas.MenuRuleListResponse, menu_rule_schemas.MenuRuleSaveResponse,
            menu_rule_schemas.MenuRuleUpdateResponse, menu_rule_schemas.MenuRuleDeleteResponse,
        )},
        title="Registered Menu Rule Response Contract", label="menu-rule",
        schema_builder=models_json_schema,
    )


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
