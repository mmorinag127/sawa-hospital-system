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
    if _string_keyed_json(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return json.dumps(_json_snapshot(value), sort_keys=True, ensure_ascii=False, allow_nan=False)


def _string_keyed_json(value: Any) -> bool:
    """Avoid a JSON round trip only when it cannot normalize keys or values."""
    pending = [value]
    seen: set[int] = set()
    while pending:
        item = pending.pop()
        kind = type(item)
        if kind in (dict, list, tuple):
            identity = id(item)
            if identity in seen:
                return False
            seen.add(identity)
            if kind is dict:
                if any(type(key) is not str for key in item):
                    return False
                pending.extend(item.values())
            else:
                pending.extend(item)
        elif kind not in (str, int, float, bool, type(None)):
            return False
    return True


def snapshots_equal(left: Any, right: Any) -> bool:
    if left is right and _string_keyed_json(left):
        # Validate the native value once; nonfinite numbers and deep/cyclic
        # inputs must retain the serializer's original rejection behavior.
        canonical_snapshot_json(left)
        return True
    return canonical_snapshot_json(left) == canonical_snapshot_json(right)
