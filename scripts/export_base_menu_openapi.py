#!/usr/bin/env python3
"""Offline registered base-menu response slice; imports only schemas/shared parser."""
import argparse
import json
from pathlib import Path
import sys

from pydantic.json_schema import models_json_schema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))
from src.api import base_menu_schemas  # noqa: E402
from scripts.registered_response_contract import registered_response_contract  # noqa: E402

EXPECTED = {("/base-menus", "get"), ("/base-menus", "post"), ("/base-menus/{item_id}", "put")}


def response_contract(*, main_source=None, route_source=None):
    return registered_response_contract(
        main_source=main_source if main_source is not None else (ROOT / "backend/src/main.py").read_text(),
        route_source=route_source if route_source is not None else (ROOT / "backend/src/api/base_menus.py").read_text(),
        module_name="base_menus", expected_prefix="", expected=EXPECTED,
        models={model.__name__: model for model in (
            base_menu_schemas.BaseMenuListResponse, base_menu_schemas.BaseMenuReplaceResponse,
            base_menu_schemas.BaseMenuUpdateResponse,
        )},
        title="Registered Base Menu Response Contract", label="base-menu",
        schema_builder=models_json_schema,
    )


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
