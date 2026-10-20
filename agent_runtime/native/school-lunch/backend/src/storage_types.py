"""Storage fidelity without changing physical SQL column types."""
from datetime import timezone

from sqlalchemy import DateTime, JSON
from sqlalchemy.types import TypeDecorator


def same_json(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same_json(a, b) for a, b in zip(left, right))
    return left == right


class UTCNaiveDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.utcoffset() is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value


class StrictJSON(TypeDecorator):
    impl = JSON
    cache_ok = True

    def coerce_compared_value(self, op, value):
        return self.impl.coerce_compared_value(op, value)

    def compare_values(self, left, right):
        return same_json(left, right)
