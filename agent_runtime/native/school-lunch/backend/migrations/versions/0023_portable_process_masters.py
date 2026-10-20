"""Portable masters can be reused without importing daily menu rows."""

from alembic import op
import sqlalchemy as sa

revision = "0023_portable_process_masters"
down_revision = "0022_process_route_profiles"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("school_lunch_process_revisions") as batch:
        batch.alter_column(
            "menu_execution_id", existing_type=sa.Integer(), nullable=True
        )
    for table in (
        "school_lunch_process_recipes",
        "school_lunch_process_route_profiles",
    ):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("transfer_source_key", sa.String(300), nullable=True)
            )
            batch.add_column(
                sa.Column("transfer_source_hash", sa.String(64), nullable=True)
            )
            batch.create_unique_constraint(
                f"uq_{table}_transfer_source_key",
                ["facility_id", "transfer_source_key"],
            )


def downgrade():
    conn = op.get_bind()
    if conn.execute(
        sa.text(
            "SELECT 1 FROM school_lunch_process_revisions WHERE menu_execution_id IS NULL LIMIT 1"
        )
    ).first():
        raise RuntimeError("日付を持たない移行済み工程マスターを保持しています")
    for table in (
        "school_lunch_process_recipes",
        "school_lunch_process_route_profiles",
    ):
        if conn.execute(
            sa.text(
                f"SELECT 1 FROM {table} WHERE transfer_source_key IS NOT NULL LIMIT 1"
            )
        ).first():
            raise RuntimeError("移行済みマスターを保持しています")
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"uq_{table}_transfer_source_key", type_="unique")
            batch.drop_column("transfer_source_hash")
            batch.drop_column("transfer_source_key")
    with op.batch_alter_table("school_lunch_process_revisions") as batch:
        batch.alter_column(
            "menu_execution_id", existing_type=sa.Integer(), nullable=False
        )
