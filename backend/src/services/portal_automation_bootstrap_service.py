from __future__ import annotations

import os
import uuid

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from src.services.portal_access_bootstrap_service import (
    PortalAccessBootstrapError,
    _assert_canonical_user_system_access_schema,
    _insert_audit_log,
)


class PortalAutomationBootstrapError(PortalAccessBootstrapError):
    pass


def require_automation_context(*, environment: str, email: str) -> None:
    if environment not in {"stg", "prod"}:
        raise PortalAutomationBootstrapError("environment must be stg or prod")
    if os.getenv("GITHUB_ACTIONS") != "true" or not os.getenv("GITHUB_RUN_ID", "").strip():
        raise PortalAutomationBootstrapError("automation bootstrap requires GitHub Actions")
    ref = os.getenv("GITHUB_REF", "")
    ref_name = os.getenv("GITHUB_REF_NAME", "")
    allowed = (
        ref_name == "develop"
        if environment == "stg"
        else ref_name.startswith("release/prod-") and len(ref_name) > len("release/prod-")
    )
    if not allowed or ref != f"refs/heads/{ref_name}":
        raise PortalAutomationBootstrapError("automation bootstrap branch does not match environment")
    expected = f"sawa-ui-verify-{environment}@sawahospitalsystem.iam.gserviceaccount.com"
    if email != expected:
        raise PortalAutomationBootstrapError("automation email does not match environment")


def run_portal_automation_bootstrap(
    connection: Connection, *, environment: str, email: str
) -> dict[str, str | bool]:
    """Register only the dedicated identity inside the caller's transaction."""
    require_automation_context(environment=environment, email=email)
    inspector = sa.inspect(connection)
    for table in ("users", "audit_logs", "user_system_access"):
        if not inspector.has_table(table):
            raise PortalAutomationBootstrapError(f"{table} table is required before automation bootstrap")
    _assert_canonical_user_system_access_schema(connection)
    # accounts have no unique constraint; serialize check/insert on PostgreSQL.
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE users, user_system_access IN SHARE ROW EXCLUSIVE MODE"))
    rows = connection.execute(
        sa.text("SELECT id, account, role, status FROM users WHERE lower(trim(account)) = :email"),
        {"email": email},
    ).mappings().all()
    if rows:
        if len(rows) != 1:
            raise PortalAutomationBootstrapError("existing automation users are ambiguous")
        row = rows[0]
        access = connection.execute(
            sa.text("SELECT system_key, enabled FROM user_system_access WHERE user_id = :id"),
            {"id": row["id"]},
        ).all()
        enabled = {key for key, value in access if value}
        if (
            row["account"] != email
            or row["role"] != "operator"
            or row["status"] != "active"
            or enabled != {"shift"}
        ):
            raise PortalAutomationBootstrapError("existing automation user or access does not match")
        return {"user_id": row["id"], "created": False}

    user_id = str(uuid.uuid4())
    connection.execute(
        sa.text(
            "INSERT INTO users(id, account, role, status, created_at) "
            "VALUES(:id, :email, 'operator', 'active', CURRENT_TIMESTAMP)"
        ),
        {"id": user_id, "email": email},
    )
    connection.execute(
        sa.text(
            "INSERT INTO user_system_access(user_id, system_key, enabled) "
            "VALUES(:id, 'shift', TRUE)"
        ),
        {"id": user_id},
    )
    _insert_audit_log(
        connection,
        actor=f"system:{environment}-portal-automation-db-bootstrap",
        action="portal_automation_shift_user_created",
        target=user_id,
    )
    return {"user_id": user_id, "created": True}
