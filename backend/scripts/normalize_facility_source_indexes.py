#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = (
    BACKEND_ROOT / "src" / "data" / "facility_master.template.json",
    BACKEND_ROOT / "src" / "data" / "facility_master.test.json",
)


def _role(column: dict[str, Any]) -> str:
    return str(column.get("role") or "").strip().lower()


def _column_index(column: dict[str, Any], fallback: int) -> int:
    try:
        return int(column.get("index"))
    except Exception:
        return fallback


def _infer_source_indexes(columns: list[dict[str, Any]]) -> list[int]:
    menu_index = next(
        (
            _column_index(column, idx)
            for idx, column in enumerate(columns)
            if _role(column) in {"menu", "menu_name"}
        ),
        2,
    )
    has_pre_menu_aux = any(
        _role(column) == "aux" and _column_index(column, idx) < menu_index
        for idx, column in enumerate(columns)
    )
    inferred: list[int] = []
    for idx, column in enumerate(columns):
        column_index = _column_index(column, idx)
        role = _role(column)
        if has_pre_menu_aux:
            inferred.append(column_index)
        elif role == "date":
            inferred.append(0)
        elif role == "daypart":
            inferred.append(1)
        elif role in {"menu", "menu_name"}:
            inferred.append(3)
        else:
            inferred.append(column_index + 1)
    return inferred


def _normalize_master(path: Path, *, write: bool) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    changed: list[str] = []
    facilities = payload.get("facilities")
    if not isinstance(facilities, list):
        raise RuntimeError(f"facilities missing: {path}")
    for facility in facilities:
        if not isinstance(facility, dict):
            continue
        facility_id = str(facility.get("facility_id") or "").strip()
        template = facility.get("fax_template_override")
        columns = template.get("columns") if isinstance(template, dict) else None
        if not isinstance(columns, list) or not all(isinstance(item, dict) for item in columns):
            continue
        if all(column.get("source_index") is not None for column in columns):
            continue
        inferred = _infer_source_indexes(columns)
        for column, source_index in zip(columns, inferred, strict=True):
            if column.get("source_index") is None:
                column["source_index"] = int(source_index)
        changed.append(facility_id or "<unknown>")
    if write and changed:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    paths = tuple(args.paths) if args.paths else DEFAULT_TARGETS
    for path in paths:
        changed = _normalize_master(path, write=args.write)
        mode = "updated" if args.write else "would_update"
        print(f"{path}: {mode} {len(changed)} facilities {','.join(changed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
