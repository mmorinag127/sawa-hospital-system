"""Add versioned facility templates and template lineage columns."""

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "facility_template_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("facility_id", sa.String(), sa.ForeignKey("facilities.id"), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("template_id", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("columns_json", sa.JSON(), nullable=False),
        sa.Column("cells_json", sa.JSON(), nullable=True),
        sa.Column("template_digest", sa.String(), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_facility_template_versions_facility_id", "facility_template_versions", ["facility_id"])
    op.create_index("ix_facility_template_versions_status", "facility_template_versions", ["status"])
    op.create_index("ix_facility_template_versions_template_digest", "facility_template_versions", ["template_digest"])

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("orders", recreate="always") as batch:
            batch.add_column(sa.Column("template_version_id", sa.String(), nullable=True))
            batch.create_foreign_key(
                "fk_orders_template_version_id",
                "facility_template_versions",
                ["template_version_id"],
                ["id"],
            )
    else:
        op.add_column("orders", sa.Column("template_version_id", sa.String(), nullable=True))
        op.create_foreign_key(
            "fk_orders_template_version_id",
            "orders",
            "facility_template_versions",
            ["template_version_id"],
            ["id"],
        )
    op.create_index("ix_orders_template_version_id", "orders", ["template_version_id"])

    for table_name, column_name in (
        ("ocr_jobs", "template_version_id"),
        ("order_ocr_evidence_runs", "template_version_id"),
        ("order_sheet_drafts", "template_version_id"),
        ("order_confirmed_snapshots", "template_version_id"),
        ("order_workflow_states", "template_version_id"),
        ("order_current_states", "template_version_id"),
    ):
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(table_name, recreate="always") as batch:
                batch.add_column(sa.Column(column_name, sa.String(), nullable=True))
                batch.create_foreign_key(
                    f"fk_{table_name}_{column_name}",
                    "facility_template_versions",
                    [column_name],
                    ["id"],
                )
        else:
            op.add_column(table_name, sa.Column(column_name, sa.String(), nullable=True))
            op.create_foreign_key(
                f"fk_{table_name}_{column_name}",
                table_name,
                "facility_template_versions",
                [column_name],
                ["id"],
            )
        op.create_index(f"ix_{table_name}_{column_name}", table_name, [column_name])


def downgrade() -> None:
    for table_name, column_name in reversed(
        (
            ("ocr_jobs", "template_version_id"),
            ("order_ocr_evidence_runs", "template_version_id"),
            ("order_sheet_drafts", "template_version_id"),
            ("order_confirmed_snapshots", "template_version_id"),
            ("order_workflow_states", "template_version_id"),
            ("order_current_states", "template_version_id"),
        )
    ):
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(table_name, recreate="always") as batch:
                batch.drop_index(f"ix_{table_name}_{column_name}")
                batch.drop_constraint(f"fk_{table_name}_{column_name}", type_="foreignkey")
                batch.drop_column(column_name)
        else:
            op.drop_index(f"ix_{table_name}_{column_name}", table_name=table_name)
            op.drop_constraint(f"fk_{table_name}_{column_name}", table_name, type_="foreignkey")
            op.drop_column(table_name, column_name)

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("orders", recreate="always") as batch:
            batch.drop_index("ix_orders_template_version_id")
            batch.drop_constraint("fk_orders_template_version_id", type_="foreignkey")
            batch.drop_column("template_version_id")
    else:
        op.drop_index("ix_orders_template_version_id", table_name="orders")
        op.drop_constraint("fk_orders_template_version_id", "orders", type_="foreignkey")
        op.drop_column("orders", "template_version_id")

    op.drop_index("ix_facility_template_versions_template_digest", table_name="facility_template_versions")
    op.drop_index("ix_facility_template_versions_status", table_name="facility_template_versions")
    op.drop_index("ix_facility_template_versions_facility_id", table_name="facility_template_versions")
    op.drop_table("facility_template_versions")
