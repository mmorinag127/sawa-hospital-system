"""Add confirmed snapshot lineage to materialized output artifacts."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {str(column.get("name") or "") for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {str(index.get("name") or "") for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _fk_names(table_name: str) -> set[str]:
    return {str(fk.get("name") or "") for fk in sa.inspect(op.get_bind()).get_foreign_keys(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if index_name not in _index_names(table_name):
        op.create_index(index_name, table_name, columns, unique=False)


def _create_fk_if_missing(
    table_name: str,
    constraint_name: str,
    referred_table: str,
    local_cols: list[str],
    remote_cols: list[str],
) -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    if constraint_name not in _fk_names(table_name):
        op.create_foreign_key(constraint_name, table_name, referred_table, local_cols, remote_cols)


def upgrade() -> None:
    _add_column_if_missing("order_lines", sa.Column("confirmed_snapshot_id", sa.String(), nullable=True))
    _add_column_if_missing("order_lines", sa.Column("line_digest", sa.String(), nullable=True))
    _create_index_if_missing("order_lines", "ix_order_lines_confirmed_snapshot_id", ["confirmed_snapshot_id"])
    _create_index_if_missing("order_lines", "ix_order_lines_line_digest", ["line_digest"])
    _create_fk_if_missing(
        "order_lines",
        "fk_order_lines_confirmed_snapshot_id",
        "order_confirmed_snapshots",
        ["confirmed_snapshot_id"],
        ["id"],
    )

    for table_name in ("bags", "label_rows", "delivery_notes"):
        _add_column_if_missing(table_name, sa.Column("confirmed_snapshot_id", sa.String(), nullable=True))
        _add_column_if_missing(table_name, sa.Column("output_bundle_id", sa.String(), nullable=True))
        _add_column_if_missing(table_name, sa.Column("source_saved_sheet_id", sa.String(), nullable=True))
        _add_column_if_missing(table_name, sa.Column("template_version_id", sa.String(), nullable=True))
        _create_index_if_missing(table_name, f"ix_{table_name}_confirmed_snapshot_id", ["confirmed_snapshot_id"])
        _create_index_if_missing(table_name, f"ix_{table_name}_output_bundle_id", ["output_bundle_id"])
        _create_index_if_missing(table_name, f"ix_{table_name}_source_saved_sheet_id", ["source_saved_sheet_id"])
        _create_index_if_missing(table_name, f"ix_{table_name}_template_version_id", ["template_version_id"])
        _create_fk_if_missing(
            table_name,
            f"fk_{table_name}_confirmed_snapshot_id",
            "order_confirmed_snapshots",
            ["confirmed_snapshot_id"],
            ["id"],
        )
        _create_fk_if_missing(
            table_name,
            f"fk_{table_name}_source_saved_sheet_id",
            "order_sheet_drafts",
            ["source_saved_sheet_id"],
            ["id"],
        )
        _create_fk_if_missing(
            table_name,
            f"fk_{table_name}_template_version_id",
            "facility_template_versions",
            ["template_version_id"],
            ["id"],
        )

    _add_column_if_missing("manufacturing_aggregate_rows", sa.Column("confirmed_snapshot_id", sa.String(), nullable=True))
    _add_column_if_missing("manufacturing_aggregate_rows", sa.Column("output_bundle_id", sa.String(), nullable=True))
    _add_column_if_missing("manufacturing_aggregate_rows", sa.Column("template_version_id", sa.String(), nullable=True))
    _create_index_if_missing(
        "manufacturing_aggregate_rows",
        "ix_manufacturing_aggregate_rows_confirmed_snapshot_id",
        ["confirmed_snapshot_id"],
    )
    _create_index_if_missing(
        "manufacturing_aggregate_rows",
        "ix_manufacturing_aggregate_rows_output_bundle_id",
        ["output_bundle_id"],
    )
    _create_index_if_missing(
        "manufacturing_aggregate_rows",
        "ix_manufacturing_aggregate_rows_template_version_id",
        ["template_version_id"],
    )
    _create_fk_if_missing(
        "manufacturing_aggregate_rows",
        "fk_manufacturing_aggregate_rows_confirmed_snapshot_id",
        "order_confirmed_snapshots",
        ["confirmed_snapshot_id"],
        ["id"],
    )
    _create_fk_if_missing(
        "manufacturing_aggregate_rows",
        "fk_manufacturing_aggregate_rows_template_version_id",
        "facility_template_versions",
        ["template_version_id"],
        ["id"],
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        for table_name, fk_names in {
            "order_lines": ("fk_order_lines_confirmed_snapshot_id",),
            "bags": (
                "fk_bags_confirmed_snapshot_id",
                "fk_bags_source_saved_sheet_id",
                "fk_bags_template_version_id",
            ),
            "label_rows": (
                "fk_label_rows_confirmed_snapshot_id",
                "fk_label_rows_source_saved_sheet_id",
                "fk_label_rows_template_version_id",
            ),
            "delivery_notes": (
                "fk_delivery_notes_confirmed_snapshot_id",
                "fk_delivery_notes_source_saved_sheet_id",
                "fk_delivery_notes_template_version_id",
            ),
            "manufacturing_aggregate_rows": (
                "fk_manufacturing_aggregate_rows_confirmed_snapshot_id",
                "fk_manufacturing_aggregate_rows_template_version_id",
            ),
        }.items():
            existing_fks = _fk_names(table_name)
            for fk_name in fk_names:
                if fk_name in existing_fks:
                    op.drop_constraint(fk_name, table_name, type_="foreignkey")

    index_names_by_table = {
        "order_lines": (
            "ix_order_lines_confirmed_snapshot_id",
            "ix_order_lines_line_digest",
        ),
        "bags": (
            "ix_bags_confirmed_snapshot_id",
            "ix_bags_output_bundle_id",
            "ix_bags_source_saved_sheet_id",
            "ix_bags_template_version_id",
        ),
        "label_rows": (
            "ix_label_rows_confirmed_snapshot_id",
            "ix_label_rows_output_bundle_id",
            "ix_label_rows_source_saved_sheet_id",
            "ix_label_rows_template_version_id",
        ),
        "delivery_notes": (
            "ix_delivery_notes_confirmed_snapshot_id",
            "ix_delivery_notes_output_bundle_id",
            "ix_delivery_notes_source_saved_sheet_id",
            "ix_delivery_notes_template_version_id",
        ),
        "manufacturing_aggregate_rows": (
            "ix_manufacturing_aggregate_rows_confirmed_snapshot_id",
            "ix_manufacturing_aggregate_rows_output_bundle_id",
            "ix_manufacturing_aggregate_rows_template_version_id",
        ),
    }
    for table_name, index_names in index_names_by_table.items():
        existing_indexes = _index_names(table_name)
        for index_name in index_names:
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name=table_name)
        columns = _column_names(table_name)
        for column_name in ("template_version_id", "source_saved_sheet_id", "output_bundle_id", "line_digest", "confirmed_snapshot_id"):
            if column_name in columns:
                op.drop_column(table_name, column_name)
