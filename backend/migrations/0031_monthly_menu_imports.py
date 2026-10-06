"""Saved monthly import receipts; narrow monthly CAS authorities."""
from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("menu_edit_revisions") or not inspector.has_table("monthly_menus"):
        raise RuntimeError("0031 requires formal 0030 and monthly menus")
    if inspector.has_table("monthly_menu_imports"):
        raise RuntimeError("0031 import table already exists; unknown ownership")
    if bind.execute(sa.text("SELECT 1 FROM menu_edit_revisions WHERE scope LIKE 'monthly-menu:%' LIMIT 1")).first():
        raise RuntimeError("0031 monthly revision scopes already exist")
    op.create_table("monthly_menu_imports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("month_id", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("review_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_monthly_menu_imports_month_id", "monthly_menu_imports", ["month_id"])
    for month_id in bind.execute(sa.text("SELECT id FROM monthly_menus ORDER BY id")).scalars():
        bind.execute(sa.text("INSERT INTO menu_edit_revisions(scope,revision) VALUES (:scope,1)"), {"scope": f"monthly-menu:{month_id}"})


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM monthly_menu_imports LIMIT 1")).first():
        raise RuntimeError("Cannot discard saved import receipts")
    if bind.execute(sa.text("SELECT 1 FROM menu_edit_revisions WHERE scope LIKE 'monthly-menu:%' AND revision != 1 LIMIT 1")).first():
        raise RuntimeError("Cannot rewind monthly editor revisions")
    bind.execute(sa.text("DELETE FROM menu_edit_revisions WHERE scope LIKE 'monthly-menu:%'"))
    op.drop_index("ix_monthly_menu_imports_month_id", table_name="monthly_menu_imports")
    op.drop_table("monthly_menu_imports")
