"""Database-independent JSON snapshots shared by execution and artifact guards."""

from __future__ import annotations

import json
from typing import Any


def _json_snapshot(value: Any) -> Any:
    from datetime import date, datetime

    def encode(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError("execution_snapshot_non_json_value")

    return json.loads(json.dumps(value, default=encode, allow_nan=False))


def canonical_snapshot_json(value: Any) -> str:
    return json.dumps(_json_snapshot(value), sort_keys=True, ensure_ascii=False, allow_nan=False)


def snapshots_equal(left: Any, right: Any) -> bool:
    return canonical_snapshot_json(left) == canonical_snapshot_json(right)
