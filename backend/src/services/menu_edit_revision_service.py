"""Atomic editor-generation contracts for base menus and menu rules."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.models.menu import MenuEditRevision


BASE_MENU_SCOPE = "base-menu-cycle-items"


def rule_scope(rule_type: str) -> str:
    return f"menu-rules:{rule_type}"


class RevisionPreconditionRequired(ValueError):
    pass


class RevisionConflict(RuntimeError):
    pass


class RevisionSchemaNotMigrated(RuntimeError):
    pass


@dataclass(frozen=True)
class RevisionToken:
    scope: str
    revision: int
    sources: tuple[tuple[str, int], ...]
    query: dict[str, int | None] | None
    coverage: str | None


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_sources(sources: object) -> list[tuple[str, int]]:
    if not isinstance(sources, list) or any(
        not isinstance(item, (list, tuple))
        or len(item) != 2
        or not isinstance(item[0], str)
        or not item[0]
        or not _is_positive_int(item[1])
        for item in sources
    ):
        raise RevisionConflict("invalid editor source revisions")
    normalized = [(item[0], item[1]) for item in sources]
    if len({key for key, _ in normalized}) != len(normalized):
        raise RevisionConflict("duplicate editor source revision keys")
    return sorted(normalized)


def _validate_query_coverage(query: object, coverage: object) -> tuple[dict[str, int | None] | None, str | None]:
    if query is None and coverage is None:
        return None, None
    if not isinstance(query, dict) or set(query) != {"cycle_day"}:
        raise RevisionConflict("invalid editor revision coverage")
    cycle_day = query["cycle_day"]
    if cycle_day is not None and not _is_positive_int(cycle_day):
        raise RevisionConflict("invalid editor revision coverage")
    if (cycle_day is None and coverage != "full") or (cycle_day is not None and coverage != "partial"):
        raise RevisionConflict("invalid editor revision coverage")
    return {"cycle_day": cycle_day}, coverage


def encode(
    scope: str,
    revision: int,
    sources: Iterable[tuple[str, int]] = (),
    *,
    query: dict[str, int | None] | None = None,
    coverage: str | None = None,
) -> str:
    normalized_sources = _validate_sources(list(sources))
    if not _is_positive_int(revision):
        raise RevisionConflict("invalid editor revision")
    query, coverage = _validate_query_coverage(query, coverage)
    payload = {"scope": scope, "revision": revision, "sources": normalized_sources}
    if query is not None:
        payload["query"] = query
        payload["coverage"] = coverage
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_token(value: str | None, scope: str) -> RevisionToken:
    if not value or not value.strip():
        raise RevisionPreconditionRequired("If-Match is required")
    token = value.strip().removeprefix('W/').strip().strip('"')
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw.decode("utf-8"))
        revision = payload["revision"]
        sources = payload.get("sources", [])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RevisionConflict("invalid editor revision") from exc
    if payload.get("scope") != scope or not _is_positive_int(revision):
        raise RevisionConflict("editor revision scope mismatch")
    normalized_sources = _validate_sources(sources)
    query = payload.get("query")
    coverage = payload.get("coverage")
    query, coverage = _validate_query_coverage(query, coverage)
    return RevisionToken(
        scope=scope,
        revision=revision,
        sources=tuple(normalized_sources),
        query=query,
        coverage=coverage,
    )


def decode(value: str | None, scope: str) -> tuple[int, list[tuple[str, int]]]:
    token = decode_token(value, scope)
    return token.revision, list(token.sources)


def decode_full_base_menu_snapshot(value: str | None) -> RevisionToken:
    token = decode_token(value, BASE_MENU_SCOPE)
    if token.query != {"cycle_day": None} or token.coverage != "full":
        raise RevisionConflict("full base-menu snapshot is required before replacing all items")
    return token


def current(session: Session, scope: str) -> int:
    row = session.get(MenuEditRevision, scope)
    if row is None:
        raise RevisionSchemaNotMigrated(f"menu editor revision scope is missing: {scope}")
    if not _is_positive_int(row.revision):
        raise RevisionConflict(f"invalid stored editor revision for scope: {scope}")
    return row.revision


def read_locked_current(session: Session, scope: str) -> int:
    """Capture the collection generation before any dependent snapshot reads."""
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE and does not begin a DBAPI transaction for SELECT.
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    row = session.execute(
        select(MenuEditRevision).where(MenuEditRevision.scope == scope).with_for_update(read=True)
    ).scalar_one_or_none()
    if row is None:
        raise RevisionSchemaNotMigrated(f"menu editor revision scope is missing: {scope}")
    if not _is_positive_int(row.revision):
        raise RevisionConflict(f"invalid stored editor revision for scope: {scope}")
    return row.revision


def compare_and_advance(session: Session, scope: str, expected_revision: int) -> int:
    """CAS is the lock: it works on PostgreSQL and SQLite without a read-then-write race."""
    result = session.execute(
        update(MenuEditRevision)
        .where(MenuEditRevision.scope == scope, MenuEditRevision.revision == expected_revision)
        .values(revision=MenuEditRevision.revision + 1)
    )
    if result.rowcount == 1:
        return expected_revision + 1
    if session.get(MenuEditRevision, scope) is None:
        raise RevisionSchemaNotMigrated(f"menu editor revision scope is missing: {scope}")
    raise RevisionConflict("editor revision conflict; reload before saving")
