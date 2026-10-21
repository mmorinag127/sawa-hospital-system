"""Revocable portal sessions and single-use Google login attempts."""
from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("portal_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("google_sub", sa.String(255), nullable=False),
        sa.Column("account", sa.String(), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("generation", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("idle_seconds", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True))
    op.create_index("ix_portal_sessions_user_id", "portal_sessions", ["user_id"])
    op.create_table("portal_login_attempts",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("previous_session_hash", sa.String(64), nullable=True),
        sa.Column("return_path", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table("portal_login_attempts")
    op.drop_index("ix_portal_sessions_user_id", table_name="portal_sessions")
    op.drop_table("portal_sessions")
