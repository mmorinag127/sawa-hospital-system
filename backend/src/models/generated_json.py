"""Keep generated JSON scalars as JSON text, including on SQLite."""
import json

from sqlalchemy import Text, TypeDecorator


class GeneratedJSON(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_result_value(self, value, dialect):
        return json.loads(value) if value is not None else None
