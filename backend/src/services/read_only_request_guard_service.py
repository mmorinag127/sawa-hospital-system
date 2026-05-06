from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re
from typing import Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


CANONICAL_TABLES = {
    "facility_template_versions",
    "orders",
    "ocr_jobs",
    "order_ocr_evidence_runs",
    "order_sheet_drafts",
    "order_workflow_states",
    "order_current_states",
    "order_confirmed_snapshots",
    "bags",
    "label_rows",
    "delivery_notes",
    "manufacturing_aggregate_rows",
}


@dataclass(frozen=True)
class ReadGuardContext:
    method: str
    path: str


_READ_GUARD_CONTEXT: ContextVar[ReadGuardContext | None] = ContextVar(
    "sawa_read_guard_context",
    default=None,
)
_INSTALLED = False
_WRITE_SQL_RE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b", re.IGNORECASE)


def _table_name_for_object(value: object) -> str | None:
    table = getattr(value, "__table__", None)
    name = getattr(table, "name", None)
    return str(name or "").strip() or None


def _current_context() -> ReadGuardContext | None:
    return _READ_GUARD_CONTEXT.get()


def _guard_error(table_name: str, operation: str) -> RuntimeError:
    context = _current_context()
    location = f"{context.method} {context.path}" if context is not None else "read scope"
    return RuntimeError(f"canonical write blocked during {location}: {operation} on {table_name}")


def _check_session_flush(session: Session, _flush_context: object, _instances: object) -> None:
    if _current_context() is None:
        return
    for collection_name, operation in (("new", "insert"), ("dirty", "update"), ("deleted", "delete")):
        for obj in getattr(session, collection_name):
            table_name = _table_name_for_object(obj)
            if table_name in CANONICAL_TABLES:
                raise _guard_error(table_name, operation)


def _check_cursor_execute(
    _conn,
    _cursor,
    statement: str,
    _parameters,
    _context,
    _executemany,
) -> None:
    if _current_context() is None or not isinstance(statement, str):
        return
    match = _WRITE_SQL_RE.match(statement)
    if not match:
        return
    normalized = statement.lower()
    for table_name in CANONICAL_TABLES:
        if table_name in normalized:
            raise _guard_error(table_name, match.group(1).lower())


def install_read_only_request_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "before_flush", _check_session_flush)
    event.listen(Engine, "before_cursor_execute", _check_cursor_execute)
    _INSTALLED = True


@contextmanager
def read_only_request_guard(*, method: str, path: str) -> Iterator[None]:
    install_read_only_request_guard()
    token = _READ_GUARD_CONTEXT.set(
        ReadGuardContext(method=str(method or "").upper() or "GET", path=str(path or "")),
    )
    try:
        yield
    finally:
        _READ_GUARD_CONTEXT.reset(token)


install_read_only_request_guard()
