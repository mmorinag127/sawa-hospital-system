from __future__ import annotations

import os
import uuid

import sqlalchemy as sa

from src.services.portal_access_bootstrap_service import (
    PortalAccessBootstrapError,
    _assert_canonical_user_system_access_schema,
    _insert_audit_log,
)


def require_price_automation_context(*, environment: str, email: str) -> None:
    if (
        environment != "stg"
        or os.getenv("GITHUB_ACTIONS") != "true"
        or not os.getenv("GITHUB_RUN_ID", "").strip()
        or os.getenv("GITHUB_REF") != "refs/heads/develop"
        or os.getenv("GITHUB_REF_NAME") != "develop"
        or email != "sawa-price-ui-verify-stg@sawahospitalsystem.iam.gserviceaccount.com"
    ):
        raise PortalAccessBootstrapError("School lunch automation bootstrap requires staging develop and its dedicated identity")


def run_price_automation_bootstrap(connection, *, environment: str, email: str) -> dict:
    require_price_automation_context(environment=environment, email=email)
    _assert_canonical_user_system_access_schema(connection)
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE users, user_system_access IN SHARE ROW EXCLUSIVE MODE"))
    rows = connection.execute(sa.text(
        "SELECT id, account, role, status FROM users WHERE lower(trim(account)) = :email"
    ), {"email": email}).mappings().all()
    if rows:
        if len(rows) != 1:
            raise PortalAccessBootstrapError("Ambiguous school lunch automation identity")
        row = rows[0]
        access = connection.execute(sa.text(
            "SELECT system_key FROM user_system_access WHERE user_id = :id AND enabled = TRUE"
        ), {"id": row["id"]}).scalars().all()
        if row["account"] != email or row["role"] != "operator" or row["status"] != "active" or access != ["school-lunch"]:
            raise PortalAccessBootstrapError("Existing school lunch automation identity does not match")
        return {"user_id": row["id"], "created": False}
    user_id = str(uuid.uuid4())
    connection.execute(sa.text(
        "INSERT INTO users(id, account, role, status, created_at) "
        "VALUES(:id, :email, 'operator', 'active', CURRENT_TIMESTAMP)"
    ), {"id": user_id, "email": email})
    connection.execute(sa.text(
        "INSERT INTO user_system_access(user_id, system_key, enabled) VALUES(:id, 'school-lunch', TRUE)"
    ), {"id": user_id})
    _insert_audit_log(connection, actor="system:stg-price-automation-db-bootstrap",
                      action="portal_automation_school_lunch_user_created", target=user_id)
    return {"user_id": user_id, "created": True}
