"""Shared transactional SQL CAS for editable planning masters."""

import sqlite3
from contextlib import contextmanager

from fastapi import HTTPException
from collections.abc import Callable

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .models import EditorRevision


def require_revision(value: object | None, code: str) -> int:
    if value is None:
        raise HTTPException(428, detail={"code": f"{code}_required", "message": "保存前に再読み込みしてください。"})
    if type(value) is not int or value <= 0:
        raise HTTPException(422, detail={"code": f"{code}_invalid", "message": "更新番号は1以上の整数で指定してください。"})
    return value


def technical_revision(value: object, code: str) -> int:
    if type(value) is not int or value <= 0:
        raise HTTPException(422, detail={"code": f"{code}_invalid", "message": "保存済みの更新番号が不正です。"})
    return value


def _require_clean_snapshot_entry(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        raise conflict("editor_read_snapshot_pending_changes")


def begin(session: Session) -> None:
    # Refuse caller edits before driver access or SQL can autoflush them.
    _require_clean_snapshot_entry(session)
    if session.get_bind().dialect.name == "sqlite" and not _sqlite_driver_in_transaction(session):
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError as error:
            session.rollback()
            if _locked(session, error):
                raise conflict("editor_revision") from error
            raise
    _expire_clean_snapshot_sources(session)


def read_collection(session: Session, resource: str, items: Callable[[], list[dict]]) -> dict:
    """Lock the canonical collection row before reading any collection values."""
    tokens, started_here = begin_read_snapshot(session, (resource,))
    try:
        response = {"items": items(), "collection_revision": tokens[resource]}
    except Exception:
        end_read_snapshot(session, started_here, failed=True)
        raise
    end_read_snapshot(session, started_here)
    return response


def _expire_clean_snapshot_sources(session: Session) -> None:
    """Reload held read sources without discarding pending operator changes."""
    pending = set(session.dirty) | set(session.new) | set(session.deleted)
    for row in list(session.identity_map.values()):
        if row not in pending:
            session.expire(row)


def begin_read_snapshot(session: Session, resources: tuple[str, ...]) -> tuple[dict[str, int], bool]:
    """Acquire canonical shared locks before a multi-resource read snapshot."""
    # Refuse before any SQL or driver access can autoflush or take ownership of
    # a transaction containing operator edits. Leave those edits with the caller.
    _require_clean_snapshot_entry(session)
    ordered_resources = tuple(sorted(set(resources)))
    if len(ordered_resources) != len(resources):
        raise RuntimeError("editor collection snapshot resources must be unique")
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        started_here = not session.in_transaction()
        if started_here:
            session.begin()
    elif dialect == "sqlite":
        started_here = not _sqlite_driver_in_transaction(session)
        if started_here:
            session.connection().exec_driver_sql("BEGIN")
    else:
        raise RuntimeError(f"unsupported editor collection read dialect: {dialect}")
    try:
        tokens: dict[str, int] = {}
        # SQL ORDER BY does not define lock acquisition order. Lock one resource
        # at a time so every reader follows the same canonical sequence.
        for resource in ordered_resources:
            revision = session.execute(
                select(EditorRevision.revision)
                .where(EditorRevision.resource == resource)
                .with_for_update(read=True)
            ).scalar_one_or_none()
            if revision is None:
                raise HTTPException(422, detail={"code": "editor_revision_source_missing", "message": f"正本の更新番号がありません: {[resource]}"})
            tokens[resource] = technical_revision(revision, f"{resource}_collection_revision")
        _expire_clean_snapshot_sources(session)
        return tokens, started_here
    except Exception:
        end_read_snapshot(session, started_here, failed=True)
        raise


def end_read_snapshot(session: Session, started_here: bool, *, failed: bool = False) -> None:
    if not started_here:
        return
    if failed:
        session.rollback()
    else:
        session.commit()


def begin_multi_resource_write_snapshot(
    session: Session,
    expected_revisions: dict[str, object | None],
    changed_resources: set[str],
) -> dict[str, int]:
    """Take exclusive collection locks in name order and increment changed tokens."""
    resources = tuple(sorted(expected_revisions))
    if not resources or set(changed_resources) - set(resources):
        raise RuntimeError("invalid editor collection write snapshot resources")
    begin(session)
    resource = "editor_revision"
    try:
        tokens: dict[str, int] = {}
        for resource in resources:
            expected = require_revision(expected_revisions[resource], f"{resource}_collection_revision")
            revision = session.execute(
                select(EditorRevision.revision)
                .where(EditorRevision.resource == resource)
                .with_for_update()
            ).scalar_one_or_none()
            if revision is None:
                raise HTTPException(422, detail={"code": "editor_revision_source_missing", "message": f"正本の更新番号がありません: {[resource]}"})
            if technical_revision(revision, f"{resource}_collection_revision") != expected:
                raise conflict(f"{resource}_collection_revision")
            if resource in changed_resources:
                result = session.execute(update(EditorRevision).where(
                    EditorRevision.resource == resource,
                    EditorRevision.revision == expected,
                ).values(revision=EditorRevision.revision + 1))
                if result.rowcount != 1:
                    raise conflict(f"{resource}_collection_revision")
                tokens[resource] = expected + 1
            else:
                tokens[resource] = expected
        _expire_clean_snapshot_sources(session)
        return tokens
    except OperationalError as error:
        session.rollback()
        if _locked(session, error):
            raise conflict(f"{resource}_collection_revision") from error
        raise
    except Exception:
        session.rollback()
        raise


def command_payload(session: Session, resource: str, item: dict) -> dict:
    token = session.get(EditorRevision, resource)
    if token is None:
        raise RuntimeError(f"missing formal editor revision resource: {resource}")
    return {"item": item, "collection_revision": technical_revision(token.revision, f"{resource}_collection_revision")}


def lock_solver_demand_revision(session: Session) -> int:
    """Read the authoritative counter in the owned solve snapshot, before inputs."""
    if not session.in_transaction() or session.info.get("solver_input_snapshot_transaction") is not session.get_transaction():
        raise conflict("solver_input_snapshot")
    revision = session.execute(
        select(EditorRevision.revision)
        .where(EditorRevision.resource == "demands")
        .with_for_update()
    ).scalar_one_or_none()
    if revision is None:
        raise HTTPException(422, detail={"code": "editor_revision_source_missing",
                                       "message": "正本の更新番号がありません: ['demands']"})
    return technical_revision(revision, "demands_collection_revision")


def advance_dev_reset_revisions(session: Session) -> None:
    """Invalidate every formal collection token without recreating metadata."""
    begin(session)
    try:
        for resource in (
            "constraints", "demands", "exceptions", "pair_rules", "positions",
            "rotations", "shift_templates", "staff", "workdays", "position_assignment_rules",
        ):
            revision = session.execute(
                select(EditorRevision.revision)
                .where(EditorRevision.resource == resource)
                .with_for_update()
            ).scalar_one_or_none()
            if revision is None:
                raise HTTPException(422, detail={"code": "editor_revision_source_missing", "message": f"正本の更新番号がありません: {[resource]}"})
            claim_collection(session, resource, technical_revision(revision, f"{resource}_collection_revision"))
    except Exception:
        session.rollback()
        raise


@contextmanager
def dev_seed_write_snapshot(session: Session):
    """Own one revision/data transaction across nested seed helpers."""
    key = "editor_dev_seed_transaction"
    owner = session.info.get(key)
    if owner is not None:
        if session.get_transaction() is not owner or not owner.is_active:
            raise RuntimeError("dev seed transaction ownership was lost")
        yield
        return
    # Pending rejection is outside the rollback boundary.
    advance_dev_reset_revisions(session)
    owner = session.get_transaction()
    session.info[key] = owner
    try:
        yield
        if session.get_transaction() is not owner or not owner.is_active:
            raise RuntimeError("dev seed helper ended the owned transaction")
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.info.pop(key, None)


def finalize_command(session: Session, resource: str, item: Callable[[], dict]) -> dict:
    """Freeze a successful command response before commit; never refresh or reread latest."""
    session.flush()
    response = command_payload(session, resource, item())
    session.commit()
    return response


def conflict(code: str) -> HTTPException:
    return HTTPException(409, detail={"code": f"{code}_conflict", "message": "別の画面で更新されています。変更は保存していません。再読み込みしてからやり直してください。"})


def _locked(session: Session, error: OperationalError) -> bool:
    return session.get_bind().dialect.name == "sqlite" and getattr(error.orig, "sqlite_errorcode", None) in {
        sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, getattr(sqlite3, "SQLITE_BUSY_SNAPSHOT", None),
    }


def _sqlite_driver_in_transaction(session: Session) -> bool:
    connection = session.connection()
    driver_connection = connection.connection.driver_connection
    return bool(getattr(driver_connection, "in_transaction", False))


def claim_collection(session: Session, resource: str, revision: object | None) -> None:
    expected = require_revision(revision, f"{resource}_collection_revision")
    try:
        result = session.execute(update(EditorRevision).where(
            EditorRevision.resource == resource, EditorRevision.revision == expected
        ).values(revision=EditorRevision.revision + 1))
    except OperationalError as error:
        session.rollback()
        if _locked(session, error):
            raise conflict(f"{resource}_collection_revision") from error
        raise
    if result.rowcount != 1:
        session.rollback()
        raise conflict(f"{resource}_collection_revision")


def claim_item(session: Session, model, item_id: int, revision: object | None, code: str) -> None:
    expected = require_revision(revision, code)
    try:
        result = session.execute(update(model).where(model.id == item_id, model.revision == expected).values(revision=model.revision + 1))
    except OperationalError as error:
        session.rollback()
        if _locked(session, error):
            raise conflict(code) from error
        raise
    if result.rowcount == 1:
        return
    session.rollback()
    if session.get(model, item_id) is None:
        raise HTTPException(404, detail=f"{code} target not found")
    raise conflict(code)
