"""Share immutable source bytes across derived routes without deleting history."""
from alembic import op
import sqlalchemy as sa

revision = "0032_route_source_references"
down_revision = "0031_catalog_read_keys"
branch_labels = None
depends_on = None
TABLE = "school_lunch_route_source_sessions"


def upgrade():
    op.create_index("ix_school_documents_history", "school_lunch_documents", ["facility_id", "target_month", "created_at", "id"])
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("artifact_source_id", sa.Integer(), nullable=True))
        batch.alter_column("source_pdf", existing_type=sa.LargeBinary(), nullable=True)
        batch.alter_column("image", existing_type=sa.LargeBinary(), nullable=True)
        batch.create_foreign_key("fk_route_source_artifact_source", TABLE, ["artifact_source_id"], ["id"])
        batch.create_index("ix_school_lunch_route_source_sessions_artifact_source_id", ["artifact_source_id"])
        batch.create_index("ix_route_sources_diagram_id", ["diagram_id", "id"])
        batch.create_check_constraint("ck_route_source_artifact_storage",
            "(artifact_source_id IS NULL AND source_pdf IS NOT NULL AND image IS NOT NULL) OR "
            "(artifact_source_id IS NOT NULL AND artifact_source_id <> id AND source_pdf IS NULL AND image IS NULL)")


def downgrade():
    op.drop_index("ix_school_documents_history", table_name="school_lunch_documents")
    connection = op.get_bind()
    table = sa.table(TABLE, sa.column("id", sa.Integer()), sa.column("artifact_source_id", sa.Integer()),
                     sa.column("source_pdf", sa.LargeBinary()), sa.column("image", sa.LargeBinary()))
    # Removing the reference column must not discard a derived route's source.
    root = table.alias("root_source")
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint("ck_route_source_artifact_storage", type_="check")
    connection.execute(table.update().where(table.c.artifact_source_id.is_not(None)).values(
        source_pdf=sa.select(root.c.source_pdf).where(root.c.id == table.c.artifact_source_id).scalar_subquery(),
        image=sa.select(root.c.image).where(root.c.id == table.c.artifact_source_id).scalar_subquery(),
    ))
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_index("ix_school_lunch_route_source_sessions_artifact_source_id")
        batch.drop_index("ix_route_sources_diagram_id")
        batch.drop_constraint("fk_route_source_artifact_source", type_="foreignkey")
        batch.drop_column("artifact_source_id")
        batch.alter_column("source_pdf", existing_type=sa.LargeBinary(), nullable=False)
        batch.alter_column("image", existing_type=sa.LargeBinary(), nullable=False)
