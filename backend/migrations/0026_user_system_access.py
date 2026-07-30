"""Create explicit per-system grants and migrate existing hospital users."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_system_access",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("system_key", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("user_id", "system_key"),
        sa.CheckConstraint(
            "system_key IN ('hospital', 'shift', 'school-lunch')",
            name="ck_user_system_access_system_key",
        ),
    )
    op.execute(
        sa.text(
            """INSERT INTO user_system_access(user_id, system_key, enabled)
            SELECT id, 'hospital', TRUE
            FROM users
            WHERE lower(status) = 'active'
            ON CONFLICT (user_id, system_key) DO NOTHING"""
        )
    )
    op.execute(
        sa.text(
            """INSERT INTO audit_logs(id, actor, action, target, created_at)
            SELECT 'migration-0026-hospital-' || id,
                   'system:migration-0026',
                   'portal_hospital_access_migrated',
                   id,
                   CURRENT_TIMESTAMP
            FROM users
            WHERE lower(status) = 'active'
            ON CONFLICT (id) DO NOTHING"""
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM audit_logs "
            "WHERE actor = 'system:migration-0026' "
            "AND action = 'portal_hospital_access_migrated'"
        )
    )
    op.drop_table("user_system_access")
