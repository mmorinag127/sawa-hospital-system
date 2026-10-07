"""Add explicit CAS generations for ordinary menu editor writes."""

from alembic import op
import sqlalchemy as sa


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


class RevisionSchemaError(RuntimeError):
    pass


_SCOPES = ("base-menu-cycle-items", "menu-rules:global", "menu-rules:menu", "menu-rules:facility")
_OWNERSHIP_TABLE = "menu_editor_revision_column_ownership"
_REVISION_COLUMNS = ("base_menu_cycle_items", "menu_rules")
_OWNERSHIP_ADDED = "0030"
_OWNERSHIP_PREEXISTING = "preexisting"


def _columns(inspector, table):
    if not inspector.has_table(table):
        raise RevisionSchemaError(f"0030 blocked: {table} is missing")
    return {column["name"]: column for column in inspector.get_columns(table)}


def _validate_existing_revision(bind, table, column):
    if column["type"].compile(dialect=bind.dialect).upper() != "INTEGER" or column["nullable"]:
        raise RevisionSchemaError(f"0030 blocked: {table}.revision must be INTEGER NOT NULL")
    invalid = bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE revision IS NULL OR revision < 1 LIMIT 1")).first()
    if invalid is not None:
        raise RevisionSchemaError(f"0030 blocked: {table}.revision contains NULL or non-positive values")


def _preflight(bind):
    inspector = sa.inspect(bind)
    columns_by_table = {table: _columns(inspector, table) for table in _REVISION_COLUMNS}
    if inspector.has_table("menu_edit_revisions"):
        raise RevisionSchemaError("0030 blocked: menu_edit_revisions already exists")
    if inspector.has_table(_OWNERSHIP_TABLE):
        raise RevisionSchemaError(f"0030 blocked: {_OWNERSHIP_TABLE} already exists")
    for table, columns in columns_by_table.items():
        column = columns.get("revision")
        if column is not None:
            _validate_existing_revision(bind, table, column)
    return columns_by_table


def _ownership_before_downgrade(bind):
    inspector = sa.inspect(bind)
    _columns(inspector, "menu_edit_revisions")
    ownership_columns = _columns(inspector, _OWNERSHIP_TABLE)
    if set(ownership_columns) != {"table_name", "column_name", "ownership"}:
        raise RevisionSchemaError("0030 downgrade blocked: ownership metadata schema is invalid")

    rows = bind.execute(sa.text(
        f"SELECT table_name, column_name, ownership FROM {_OWNERSHIP_TABLE} "
        "ORDER BY table_name, column_name"
    )).all()
    expected = {(table, "revision") for table in _REVISION_COLUMNS}
    observed = {(table, column) for table, column, _ in rows}
    if len(rows) != len(expected) or observed != expected:
        raise RevisionSchemaError("0030 downgrade blocked: ownership metadata is incomplete or unknown")
    ownership = {(table, column): owner for table, column, owner in rows}
    if any(owner not in {_OWNERSHIP_ADDED, _OWNERSHIP_PREEXISTING} for owner in ownership.values()):
        raise RevisionSchemaError("0030 downgrade blocked: ownership metadata is invalid")
    for table in _REVISION_COLUMNS:
        if "revision" not in _columns(inspector, table):
            raise RevisionSchemaError(f"0030 downgrade blocked: {table}.revision is missing")
    return ownership


def upgrade():
    bind = op.get_bind()
    columns_by_table = _preflight(bind)
    ownership = {
        table: _OWNERSHIP_PREEXISTING if "revision" in columns else _OWNERSHIP_ADDED
        for table, columns in columns_by_table.items()
    }
    for table, columns in columns_by_table.items():
        if "revision" not in columns:
            op.add_column(table, sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        _OWNERSHIP_TABLE,
        sa.Column("table_name", sa.String(), primary_key=True),
        sa.Column("column_name", sa.String(), primary_key=True),
        sa.Column("ownership", sa.String(), nullable=False),
        sa.CheckConstraint(
            f"ownership IN ('{_OWNERSHIP_ADDED}', '{_OWNERSHIP_PREEXISTING}')",
            name="ck_menu_editor_revision_column_ownership_value",
        ),
    )
    bind.execute(
        sa.text(
            f"INSERT INTO {_OWNERSHIP_TABLE}(table_name, column_name, ownership) "
            "VALUES (:table_name, 'revision', :ownership)"
        ),
        [{"table_name": table, "ownership": owner} for table, owner in ownership.items()],
    )
    op.create_table(
        "menu_edit_revisions",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    bind.execute(sa.text("INSERT INTO menu_edit_revisions(scope, revision) VALUES (:scope, 1)"), [{"scope": scope} for scope in _SCOPES])


def downgrade():
    bind = op.get_bind()
    ownership = _ownership_before_downgrade(bind)
    op.drop_table("menu_edit_revisions")
    for table in _REVISION_COLUMNS:
        if ownership[(table, "revision")] == _OWNERSHIP_ADDED:
            op.drop_column(table, "revision")
    op.drop_table(_OWNERSHIP_TABLE)
