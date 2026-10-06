#!/usr/bin/env python3
"""Export only the offline menu-master OpenAPI contract without app startup."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import FastAPI  # noqa: E402
from src.api.menu_masters import router  # noqa: E402


def contract_app() -> FastAPI:
    app = FastAPI(title="Menu Master API Contract")
    app.include_router(router)
    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    content = json.dumps(contract_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != content:
            print(f"stale menu-master OpenAPI contract: {output}", file=sys.stderr)
            return 1
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
