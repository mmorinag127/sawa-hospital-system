"""Create the explicitly registered authentication support tables."""
from alembic import op
import sqlalchemy as sa

revision = "0026_auth_support_tables"
down_revision = "0025_school_lunch_documents"
branch_labels = None
depends_on = None


def upgrade():
    from src.migration_catalog import require_auth_table_definitions
    require_auth_table_definitions(op.get_bind())
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    def create(name, *columns):
        if name not in existing:
            op.create_table(name, *columns)
    create("users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("account", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True))
    create("audit_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("fac", sa.String(), nullable=True),
        sa.Column("wek", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True))
    create("notifications",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("target_role", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("related_entity", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True))


def downgrade():
    for table in ("notifications", "audit_logs", "users"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Authentication support rows prevent downgrade")
    for table in ("notifications", "audit_logs", "users"):
        op.drop_table(table)
