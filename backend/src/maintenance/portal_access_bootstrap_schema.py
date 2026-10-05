"""Schema mechanics invoked only by the explicit portal bootstrap gate."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Type

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection


SYSTEM_KEYS = ("hospital", "shift", "school-lunch")
MIGRATION_PATH = Path(__file__).resolve().parents[2] / "migrations" / "0026_user_system_access.py"
CANONICAL_CHECK_NAME = "ck_user_system_access_system_key"


def ensure_user_system_access_schema(connection: Connection, *, error_type: Type[RuntimeError]) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table("users"):
        raise error_type("users table is required before portal bootstrap")
    if not inspector.has_table("audit_logs"):
        raise error_type("audit_logs table is required before portal bootstrap")
    migration_applied = False
    if not inspector.has_table("user_system_access"):
        _apply_user_system_access_migration(connection, error_type)
        migration_applied = True
    elif connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE user_system_access IN ACCESS EXCLUSIVE MODE"))
        if not sa.inspect(connection).get_check_constraints("user_system_access"):
            connection.execute(sa.text(
                "ALTER TABLE user_system_access ADD CONSTRAINT ck_user_system_access_system_key "
                "CHECK (system_key IN ('hospital', 'shift', 'school-lunch'))"
            ))
            migration_applied = True
    _assert_canonical_user_system_access_schema(connection, error_type)
    return migration_applied


def _apply_user_system_access_migration(connection: Connection, error_type: Type[RuntimeError]) -> None:
    spec = importlib.util.spec_from_file_location("migration_0026_user_system_access", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise error_type(f"unable to load migration {MIGRATION_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = MigrationContext.configure(connection)
    previous_op = getattr(module, "op", None)
    module.op = Operations(context)
    try:
        module.upgrade()
    finally:
        module.op = previous_op


def _assert_canonical_user_system_access_schema(connection: Connection, error_type: Type[RuntimeError]) -> None:
    inspector = sa.inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("user_system_access")}
    if set(columns) != {"user_id", "system_key", "enabled"}:
        raise error_type(f"user_system_access columns are not canonical: {sorted(columns)}")
    for required_column in ("user_id", "system_key", "enabled"):
        if columns[required_column].get("nullable"):
            raise error_type(f"user_system_access.{required_column} must be NOT NULL")
    pk_columns = tuple((inspector.get_pk_constraint("user_system_access") or {}).get("constrained_columns") or ())
    if set(pk_columns) != {"user_id", "system_key"}:
        raise error_type(f"user_system_access primary key is not canonical: {pk_columns}")
    canonical_fk = next((foreign_key for foreign_key in inspector.get_foreign_keys("user_system_access") if tuple(foreign_key.get("constrained_columns") or ()) == ("user_id",) and foreign_key.get("referred_table") == "users" and tuple(foreign_key.get("referred_columns") or ()) == ("id",)), None)
    if canonical_fk is None:
        raise error_type("user_system_access.user_id must reference users.id")
    ondelete = str((canonical_fk.get("options") or {}).get("ondelete") or "").upper()
    if ondelete and ondelete != "CASCADE":
        raise error_type("user_system_access.user_id foreign key must use ON DELETE CASCADE")
    if not _has_canonical_system_check(connection, inspector.get_check_constraints("user_system_access")):
        raise error_type("user_system_access system_key constraint must allow only hospital, shift, and school-lunch")


def _has_canonical_system_check(connection: Connection, checks: list[dict]) -> bool:
    if connection.dialect.name == "postgresql":
        if len(checks) != 1 or checks[0].get("name") != CANONICAL_CHECK_NAME:
            return False
        reference = f"bootstrap_check_{uuid.uuid4().hex}"
        connection.execute(sa.text(f'CREATE TEMP TABLE "{reference}" (LIKE user_system_access) ON COMMIT DROP'))
        try:
            connection.execute(sa.text(f'ALTER TABLE "{reference}" ADD CHECK (system_key IN (\'hospital\', \'shift\', \'school-lunch\'))'))
            expressions = connection.execute(sa.text("SELECT conrelid = 'user_system_access'::regclass AS actual, pg_get_expr(conbin, conrelid) AS expression, convalidated FROM pg_constraint WHERE contype = 'c' AND conrelid IN ('user_system_access'::regclass, to_regclass(:reference))"), {"reference": reference}).all()
            actual = [row for row in expressions if row.actual]
            expected = [row for row in expressions if not row.actual]
            return len(actual) == len(expected) == 1 and actual[0].convalidated and actual[0].expression == expected[0].expression
        finally:
            connection.execute(sa.text(f'DROP TABLE "{reference}"'))
    for check in checks:
        if all(key in str(check.get("sqltext") or "") for key in SYSTEM_KEYS):
            if connection.dialect.name == "sqlite" or str(check.get("name") or "") in {"", CANONICAL_CHECK_NAME}:
                return True
    if connection.dialect.name != "sqlite":
        return False
    ddl_text = str(connection.execute(sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'user_system_access'")).scalar() or "")
    return "CHECK" in ddl_text and all(key in ddl_text for key in SYSTEM_KEYS)
