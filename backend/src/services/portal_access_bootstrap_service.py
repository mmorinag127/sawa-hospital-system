from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
import sqlalchemy as sa
from sqlalchemy.engine import Connection


SYSTEM_KEYS = ("hospital", "shift", "school-lunch")
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
    from src.maintenance.portal_access_bootstrap_schema import ensure_user_system_access_schema as ensure_schema

    return ensure_schema(connection, error_type=PortalAccessBootstrapError)


def _assert_canonical_user_system_access_schema(connection: Connection) -> None:
    """Compatibility entry point for explicit bootstrap callers."""
    from src.maintenance.portal_access_bootstrap_schema import (
        _assert_canonical_user_system_access_schema as assert_schema,
    )

    assert_schema(connection, PortalAccessBootstrapError)


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
