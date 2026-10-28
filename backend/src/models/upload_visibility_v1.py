"""One visibility predicate for navigation, counters and the matching partial index."""
from sqlalchemy import func, or_

from src.sql_text_v1 import PythonStrip


def unresolved_upload_conditions(source):
    return (
        source.current_order_id.is_(None),
        or_(func.lower(PythonStrip(func.coalesce(source.status, ""))) != "completed",
            source.current_document_id.is_(None), source.current_document_id == ""),
    )
