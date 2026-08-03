from __future__ import annotations

import importlib.util
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection


SYSTEM_KEYS = ("hospital", "shift", "school-lunch")
MIGRATION_ID = "0026"
MIGRATION_PATH = Path(__file__).resolve().parents[2] / "migrations" / "0026_user_system_access.py"
CANONICAL_CHECK_NAME = "ck_user_system_access_system_key"
BOOTSTRAP_ACTOR = "system:prod-portal-db-bootstrap"


class PortalAccessBootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class PortalAccessBootstrapResult:
    migration_applied: bool
    bootstrap_performed: bool
    deploy_verification_access_granted: bool
    active_admin_count_before: int
    active_admin_count_after: int

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def run_portal_access_bootstrap_gate(
    connection: Connection,
    *,
    bootstrap_admin_email: str | None,
    deploy_verification_email: str | None = None,
    actor: str = BOOTSTRAP_ACTOR,
) -> PortalAccessBootstrapResult:
    migration_applied = ensure_user_system_access_schema(connection)
    active_admin_count_before = _count_active_admins(connection)
    bootstrap_performed = False
    if active_admin_count_before == 0:
        email = _normalize_email(bootstrap_admin_email)
        if not email:
            raise PortalAccessBootstrapError(
                "PORTAL_BOOTSTRAP_ADMIN_EMAIL is required when no active admin exists"
            )
        bootstrap_performed = _bootstrap_admin(connection, email=email, actor=actor)
    deploy_verification_access_granted = False
    deploy_email = _normalize_email(
        deploy_verification_email,
        label="PORTAL_DEPLOY_VERIFICATION_EMAIL",
    )
    if deploy_email:
        deploy_verification_access_granted = _grant_deploy_verification_access(
            connection,
            email=deploy_email,
            actor=actor,
        )
    active_admin_count_after = _count_active_admins(connection)
    if active_admin_count_after == 0:
        raise PortalAccessBootstrapError(
            "portal bootstrap gate requires at least one active admin before prod deploy"
        )
    return PortalAccessBootstrapResult(
        migration_applied=migration_applied,
        bootstrap_performed=bootstrap_performed,
        deploy_verification_access_granted=deploy_verification_access_granted,
        active_admin_count_before=active_admin_count_before,
        active_admin_count_after=active_admin_count_after,
    )


def ensure_user_system_access_schema(connection: Connection) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table("users"):
        raise PortalAccessBootstrapError("users table is required before portal bootstrap")
    if not inspector.has_table("audit_logs"):
        raise PortalAccessBootstrapError("audit_logs table is required before portal bootstrap")

    migration_applied = False
    if not inspector.has_table("user_system_access"):
        _apply_user_system_access_migration(connection)
        migration_applied = True
    _assert_canonical_user_system_access_schema(connection)
    return migration_applied


def _normalize_email(
    raw_email: str | None,
    *,
    label: str = "PORTAL_BOOTSTRAP_ADMIN_EMAIL",
) -> str:
    token = str(raw_email or "").strip().lower()
    if not token:
        return ""
    if token.count("@") != 1 or token.startswith("@") or token.endswith("@"):
        raise PortalAccessBootstrapError(f"{label} must be a valid email address")
    return token


def _load_user_system_access_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0026_user_system_access",
        MIGRATION_PATH,
    )
    if spec is None or spec.loader is None:
        raise PortalAccessBootstrapError(f"unable to load migration {MIGRATION_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply_user_system_access_migration(connection: Connection) -> None:
    module = _load_user_system_access_migration()
    context = MigrationContext.configure(connection)
    previous_op = getattr(module, "op", None)
    module.op = Operations(context)
    try:
        module.upgrade()
    finally:
        module.op = previous_op


def _assert_canonical_user_system_access_schema(connection: Connection) -> None:
    inspector = sa.inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("user_system_access")}
    if set(columns) != {"user_id", "system_key", "enabled"}:
        raise PortalAccessBootstrapError(
            f"user_system_access columns are not canonical: {sorted(columns)}"
        )
    for required_column in ("user_id", "system_key", "enabled"):
        if columns[required_column].get("nullable"):
            raise PortalAccessBootstrapError(
                f"user_system_access.{required_column} must be NOT NULL"
            )

    pk = inspector.get_pk_constraint("user_system_access") or {}
    pk_columns = tuple(pk.get("constrained_columns") or ())
    if set(pk_columns) != {"user_id", "system_key"}:
        raise PortalAccessBootstrapError(
            f"user_system_access primary key is not canonical: {pk_columns}"
        )

    foreign_keys = inspector.get_foreign_keys("user_system_access")
    canonical_fk = next(
        (
            foreign_key
            for foreign_key in foreign_keys
            if tuple(foreign_key.get("constrained_columns") or ()) == ("user_id",)
            and foreign_key.get("referred_table") == "users"
            and tuple(foreign_key.get("referred_columns") or ()) == ("id",)
        ),
        None,
    )
    if canonical_fk is None:
        raise PortalAccessBootstrapError("user_system_access.user_id must reference users.id")
    ondelete = str((canonical_fk.get("options") or {}).get("ondelete") or "").upper()
    if ondelete and ondelete != "CASCADE":
        raise PortalAccessBootstrapError("user_system_access.user_id foreign key must use ON DELETE CASCADE")

    checks = inspector.get_check_constraints("user_system_access")
    if not _has_canonical_system_check(connection, checks):
        raise PortalAccessBootstrapError(
            "user_system_access system_key constraint must allow only hospital, shift, and school-lunch"
        )


def _has_canonical_system_check(connection: Connection, checks: list[dict]) -> bool:
    for check in checks:
        sqltext = str(check.get("sqltext") or "")
        name = str(check.get("name") or "")
        if all(key in sqltext for key in SYSTEM_KEYS):
            if connection.dialect.name == "sqlite" or name in {"", CANONICAL_CHECK_NAME}:
                return True

    if connection.dialect.name != "sqlite":
        return False

    ddl = connection.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'user_system_access'")
    ).scalar()
    ddl_text = str(ddl or "")
    return "CHECK" in ddl_text and all(key in ddl_text for key in SYSTEM_KEYS)


def _count_active_admins(connection: Connection) -> int:
    value = connection.execute(
        sa.text(
            "SELECT COUNT(DISTINCT users.id) FROM users "
            "JOIN user_system_access "
            "ON user_system_access.user_id = users.id "
            "WHERE lower(users.role) = 'admin' "
            "AND lower(users.status) = 'active' "
            "AND user_system_access.system_key = 'hospital' "
            "AND user_system_access.enabled = TRUE"
        )
    ).scalar()
    return int(value or 0)


def _bootstrap_admin(connection: Connection, *, email: str, actor: str) -> bool:
    row = connection.execute(
        sa.text("SELECT id, role, status FROM users WHERE lower(account) = :account"),
        {"account": email},
    ).mappings().first()

    if row:
        user_id = str(row["id"])
        previous_role = str(row["role"] or "")
        previous_status = str(row["status"] or "")
        connection.execute(
            sa.text(
                "UPDATE users SET account = :account, role = 'admin', status = 'active' WHERE id = :id"
            ),
            {"id": user_id, "account": email},
        )
    else:
        user_id = str(uuid.uuid4())
        previous_role = ""
        previous_status = ""
        connection.execute(
            sa.text(
                "INSERT INTO users(id, account, role, status, created_at) "
                "VALUES(:id, :account, 'admin', 'active', CURRENT_TIMESTAMP)"
            ),
            {"id": user_id, "account": email},
        )

    for system_key in SYSTEM_KEYS:
        connection.execute(
            sa.text(
                "INSERT INTO user_system_access(user_id, system_key, enabled) "
                "VALUES(:user_id, :system_key, TRUE) "
                "ON CONFLICT (user_id, system_key) DO UPDATE SET enabled = EXCLUDED.enabled"
            ),
            {"user_id": user_id, "system_key": system_key},
        )

    _insert_audit_log(
        connection,
        actor=actor,
        action="portal_bootstrap_admin_upserted",
        target=user_id,
    )
    _insert_audit_log(
        connection,
        actor=actor,
        action="portal_bootstrap_admin_access_granted",
        target=user_id,
    )

    if previous_role or previous_status:
        _insert_audit_log(
            connection,
            actor=actor,
            action="portal_bootstrap_admin_replaced_legacy_state",
            target=user_id,
        )
    return True


def _grant_deploy_verification_access(
    connection: Connection,
    *,
    email: str,
    actor: str,
) -> bool:
    row = connection.execute(
        sa.text("SELECT id, role, status FROM users WHERE lower(account) = :account"),
        {"account": email},
    ).mappings().first()

    changed = False
    if row:
        user_id = str(row["id"])
        previous_role = str(row["role"] or "").strip().lower()
        target_role = "admin" if previous_role == "admin" else "operator"
        previous_status = str(row["status"] or "").strip().lower()
        connection.execute(
            sa.text(
                "UPDATE users SET account = :account, role = :role, status = 'active' WHERE id = :id"
            ),
            {"id": user_id, "account": email, "role": target_role},
        )
        changed = previous_role != target_role or previous_status != "active"
    else:
        user_id = str(uuid.uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO users(id, account, role, status, created_at) "
                "VALUES(:id, :account, 'operator', 'active', CURRENT_TIMESTAMP)"
            ),
            {"id": user_id, "account": email},
        )
        changed = True

    existing_enabled = connection.execute(
        sa.text(
            "SELECT enabled FROM user_system_access "
            "WHERE user_id = :user_id AND system_key = 'hospital'"
        ),
        {"user_id": user_id},
    ).scalar()
    previous_extra_access_count = int(
        connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM user_system_access "
                "WHERE user_id = :user_id AND system_key <> 'hospital' AND enabled = TRUE"
            ),
            {"user_id": user_id},
        ).scalar()
        or 0
    )
    connection.execute(
        sa.text(
            "INSERT INTO user_system_access(user_id, system_key, enabled) "
            "VALUES(:user_id, 'hospital', TRUE) "
            "ON CONFLICT (user_id, system_key) DO UPDATE SET enabled = EXCLUDED.enabled"
        ),
        {"user_id": user_id},
    )
    connection.execute(
        sa.text(
            "UPDATE user_system_access SET enabled = FALSE "
            "WHERE user_id = :user_id AND system_key <> 'hospital'"
        ),
        {"user_id": user_id},
    )
    changed = changed or not bool(existing_enabled)
    changed = changed or previous_extra_access_count > 0

    if changed:
        _insert_audit_log(
            connection,
            actor=actor,
            action="portal_deploy_verification_user_upserted",
            target=user_id,
        )
        _insert_audit_log(
            connection,
            actor=actor,
            action="portal_deploy_verification_hospital_access_granted",
            target=user_id,
        )
    return changed


def _insert_audit_log(connection: Connection, *, actor: str, action: str, target: str) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO audit_logs(id, actor, action, target, created_at) "
            "VALUES(:id, :actor, :action, :target, CURRENT_TIMESTAMP)"
        ),
        {
            "id": str(uuid.uuid4()),
            "actor": actor,
            "action": action,
            "target": target,
        },
    )
