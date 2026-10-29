"""Input normalization shared by order list read projections."""
from datetime import datetime, timezone


def normalize_limit(limit: int | None) -> int:
    if limit is None:
        return 100
    try:
        normalized = int(limit)
    except (TypeError, ValueError):
        raise ValueError("order_list_limit_invalid") from None
    if normalized <= 0:
        raise ValueError("order_list_limit_invalid")
    return min(normalized, 1000)


def parse_cursor_received_at(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def normalize_cursor_id(value: str | None) -> str:
    return str(value or "").strip()
