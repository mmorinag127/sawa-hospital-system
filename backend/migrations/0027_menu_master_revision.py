"""Version menu masters without rewriting existing business fields."""

from alembic import op
import re
import sqlalchemy as sa


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


class RevisionSchemaError(RuntimeError):
    """A fixed, credential-free diagnostic safe for the explicit CLI."""


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("menu_masters"):
        raise RevisionSchemaError("0027 blocked: menu_masters is missing; apply prior migrations first")
    columns = {column["name"]: column for column in inspector.get_columns("menu_masters")}
    column = columns.get("revision")
    if column is None:
        op.add_column("menu_masters", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        return
    default = str(column.get("default") or "").strip()
    if (
        column["type"].compile(dialect=bind.dialect).upper() != "INTEGER"
        or column["nullable"]
        or not re.fullmatch(r"(?:1|'1')(?:::integer)?", default)
        or column.get("identity")
        or column.get("computed")
    ):
        raise RevisionSchemaError("0027 blocked: revision must be INTEGER NOT NULL DEFAULT 1 without generation")
    invalid = bind.execute(sa.text("SELECT 1 FROM menu_masters WHERE revision < 1 LIMIT 1")).first()
    if invalid is not None:
        raise RevisionSchemaError("0027 blocked: revision contains non-positive values")


def downgrade() -> None:
    op.drop_column("menu_masters", "revision")
