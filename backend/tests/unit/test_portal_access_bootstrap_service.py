import pathlib
import sys

import pytest
from sqlalchemy import create_engine, text

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.portal_access_bootstrap_service import (  # noqa: E402
    PortalAccessBootstrapError,
    run_portal_access_bootstrap_gate,
)


def _prepare_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'portal-bootstrap.sqlite3'}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """CREATE TABLE users (
                id VARCHAR PRIMARY KEY,
                role VARCHAR NOT NULL,
                account VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
                )"""
            )
        )
        connection.execute(
            text(
                """CREATE TABLE audit_logs (
                id VARCHAR PRIMARY KEY,
                actor VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                target VARCHAR NOT NULL,
                fac VARCHAR NULL,
                wek VARCHAR NULL,
                metadata JSON NULL,
                created_at TIMESTAMP NOT NULL
                )"""
            )
        )
    return engine


def test_gate_applies_migration_preserves_legacy_hospital_access_and_bootstraps_admin(tmp_path):
    engine = _prepare_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users(id, role, account, status, created_at) "
                "VALUES('legacy-active', 'operator', 'legacy@example.com', 'active', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users(id, role, account, status, created_at) "
                "VALUES('legacy-inactive', 'operator', 'inactive@example.com', 'inactive', CURRENT_TIMESTAMP)"
            )
        )
        result = run_portal_access_bootstrap_gate(
            connection,
            bootstrap_admin_email="bootstrap-admin@example.com",
        )

    assert result.migration_applied is True
    assert result.bootstrap_performed is True
    assert result.active_admin_count_before == 0
    assert result.active_admin_count_after == 1

    with engine.begin() as connection:
        access_rows = connection.execute(
            text(
                "SELECT u.account, usa.system_key "
                "FROM user_system_access usa "
                "JOIN users u ON u.id = usa.user_id "
                "ORDER BY lower(u.account), usa.system_key"
            )
        ).all()
        actions = connection.execute(
            text("SELECT action FROM audit_logs ORDER BY created_at, action")
        ).scalars().all()
        admin_row = connection.execute(
            text(
                "SELECT role, status, created_at FROM users "
                "WHERE lower(account) = 'bootstrap-admin@example.com'"
            )
        ).mappings().one()

    assert access_rows == [
        ("bootstrap-admin@example.com", "hospital"),
        ("bootstrap-admin@example.com", "school-lunch"),
        ("bootstrap-admin@example.com", "shift"),
        ("legacy@example.com", "hospital"),
    ]
    assert "portal_hospital_access_migrated" in actions
    assert "portal_bootstrap_admin_upserted" in actions
    assert "portal_bootstrap_admin_access_granted" in actions
    assert admin_row["role"] == "admin"
    assert admin_row["status"] == "active"
    assert admin_row["created_at"] is not None


def test_gate_is_idempotent_after_active_admin_exists(tmp_path):
    engine = _prepare_engine(tmp_path)
    with engine.begin() as connection:
        first = run_portal_access_bootstrap_gate(
            connection,
            bootstrap_admin_email="bootstrap-admin@example.com",
        )
        initial_audit_count = int(
            connection.execute(text("SELECT COUNT(*) FROM audit_logs")).scalar() or 0
        )
        second = run_portal_access_bootstrap_gate(
            connection,
            bootstrap_admin_email="bootstrap-admin@example.com",
        )
        final_audit_count = int(
            connection.execute(text("SELECT COUNT(*) FROM audit_logs")).scalar() or 0
        )
        access_count = int(
            connection.execute(text("SELECT COUNT(*) FROM user_system_access")).scalar() or 0
        )

    assert first.migration_applied is True
    assert first.bootstrap_performed is True
    assert second.migration_applied is False
    assert second.bootstrap_performed is False
    assert second.active_admin_count_before == 1
    assert second.active_admin_count_after == 1
    assert initial_audit_count == final_audit_count
    assert access_count == 3


def test_gate_grants_hospital_only_access_to_deploy_verification_identity(tmp_path):
    engine = _prepare_engine(tmp_path)
    with engine.begin() as connection:
        result = run_portal_access_bootstrap_gate(
            connection,
            bootstrap_admin_email="bootstrap-admin@example.com",
            deploy_verification_email="deploy-verifier@example.com",
        )
        deploy_row = connection.execute(
            text(
                "SELECT id, role, status FROM users "
                "WHERE lower(account) = 'deploy-verifier@example.com'"
            )
        ).mappings().one()
        deploy_access = connection.execute(
            text(
                "SELECT system_key FROM user_system_access "
                "WHERE user_id = :user_id AND enabled = TRUE "
                "ORDER BY system_key"
            ),
            {"user_id": deploy_row["id"]},
        ).scalars().all()
        actions = connection.execute(
            text("SELECT action FROM audit_logs ORDER BY created_at, action")
        ).scalars().all()

    assert result.bootstrap_performed is True
    assert result.deploy_verification_access_granted is True
    assert deploy_row["role"] == "operator"
    assert deploy_row["status"] == "active"
    assert deploy_access == ["hospital"]
    assert "portal_deploy_verification_user_upserted" in actions
    assert "portal_deploy_verification_hospital_access_granted" in actions


def test_gate_keeps_existing_deploy_admin_role_and_is_idempotent(tmp_path):
    engine = _prepare_engine(tmp_path)
    with engine.begin() as connection:
        first = run_portal_access_bootstrap_gate(
            connection,
            bootstrap_admin_email="bootstrap-admin@example.com",
            deploy_verification_email="deploy-verifier@example.com",
        )
        connection.execute(
            text(
                "UPDATE users SET role = 'admin' "
                "WHERE lower(account) = 'deploy-verifier@example.com'"
            )
        )
        audit_count_before = int(
            connection.execute(text("SELECT COUNT(*) FROM audit_logs")).scalar() or 0
        )
        second = run_portal_access_bootstrap_gate(
            connection,
            bootstrap_admin_email="",
            deploy_verification_email="deploy-verifier@example.com",
        )
        audit_count_after = int(
            connection.execute(text("SELECT COUNT(*) FROM audit_logs")).scalar() or 0
        )
        deploy_row = connection.execute(
            text(
                "SELECT role, status FROM users "
                "WHERE lower(account) = 'deploy-verifier@example.com'"
            )
        ).mappings().one()

    assert first.deploy_verification_access_granted is True
    assert second.bootstrap_performed is False
    assert second.deploy_verification_access_granted is False
    assert audit_count_before == audit_count_after
    assert deploy_row["role"] == "admin"
    assert deploy_row["status"] == "active"


def test_gate_removes_non_hospital_access_from_existing_deploy_verification_identity(tmp_path):
    engine = _prepare_engine(tmp_path)
    with engine.begin() as connection:
        run_portal_access_bootstrap_gate(
            connection,
            bootstrap_admin_email="bootstrap-admin@example.com",
            deploy_verification_email="deploy-verifier@example.com",
        )
        deploy_user_id = connection.execute(
            text(
                "SELECT id FROM users "
                "WHERE lower(account) = 'deploy-verifier@example.com'"
            )
        ).scalar_one()
        for system_key in ("shift", "school-lunch"):
            connection.execute(
                text(
                    "INSERT INTO user_system_access(user_id, system_key, enabled) "
                    "VALUES(:user_id, :system_key, TRUE)"
                ),
                {"user_id": deploy_user_id, "system_key": system_key},
            )

        repaired = run_portal_access_bootstrap_gate(
            connection,
            bootstrap_admin_email="",
            deploy_verification_email="deploy-verifier@example.com",
        )
        access_rows = connection.execute(
            text(
                "SELECT system_key, enabled FROM user_system_access "
                "WHERE user_id = :user_id ORDER BY system_key"
            ),
            {"user_id": deploy_user_id},
        ).all()

    assert repaired.deploy_verification_access_granted is True
    assert access_rows == [
        ("hospital", True),
        ("school-lunch", False),
        ("shift", False),
    ]


def test_gate_blocks_when_no_active_admin_and_bootstrap_email_is_missing(tmp_path):
    engine = _prepare_engine(tmp_path)
    with engine.begin() as connection:
        with pytest.raises(
            PortalAccessBootstrapError,
            match="PORTAL_BOOTSTRAP_ADMIN_EMAIL is required",
        ):
            run_portal_access_bootstrap_gate(connection, bootstrap_admin_email="")


def test_gate_requires_explicit_hospital_grant_for_existing_active_admin(tmp_path):
    engine = _prepare_engine(tmp_path)
    with engine.begin() as connection:
        run_portal_access_bootstrap_gate(
            connection,
            bootstrap_admin_email="bootstrap-admin@example.com",
        )
        connection.execute(
            text(
                "DELETE FROM user_system_access "
                "WHERE system_key = 'hospital'"
            )
        )

        with pytest.raises(
            PortalAccessBootstrapError,
            match="PORTAL_BOOTSTRAP_ADMIN_EMAIL is required",
        ):
            run_portal_access_bootstrap_gate(connection, bootstrap_admin_email="")

        repaired = run_portal_access_bootstrap_gate(
            connection,
            bootstrap_admin_email="bootstrap-admin@example.com",
        )
        hospital_grant_count = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM user_system_access "
                    "WHERE system_key = 'hospital' AND enabled = TRUE"
                )
            ).scalar()
            or 0
        )

    assert repaired.active_admin_count_before == 0
    assert repaired.bootstrap_performed is True
    assert repaired.active_admin_count_after == 1
    assert hospital_grant_count == 1


def test_gate_blocks_when_existing_user_system_access_schema_is_not_canonical(tmp_path):
    engine = _prepare_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                """CREATE TABLE user_system_access (
                user_id VARCHAR NOT NULL,
                system_key VARCHAR NOT NULL,
                enabled BOOLEAN NOT NULL,
                extra_column VARCHAR NULL,
                PRIMARY KEY(user_id, system_key)
                )"""
            )
        )
        with pytest.raises(
            PortalAccessBootstrapError,
            match="user_system_access columns are not canonical",
        ):
            run_portal_access_bootstrap_gate(
                connection,
                bootstrap_admin_email="bootstrap-admin@example.com",
            )
