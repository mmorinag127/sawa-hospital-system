"""Add first-class workflow-v2 bagging and output artifacts."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {str(column.get("name") or "") for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {str(index.get("name") or "") for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _fk_names(table_name: str) -> set[str]:
    return {str(fk.get("name") or "") for fk in sa.inspect(op.get_bind()).get_foreign_keys(table_name)}


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if index_name not in _index_names(table_name):
        op.create_index(index_name, table_name, columns, unique=False)


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


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
    tables = _table_names()
    if "order_bagging_results" not in tables:
        op.create_table(
            "order_bagging_results",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("order_id", sa.String(), nullable=False),
            sa.Column("source_saved_sheet_id", sa.String(), nullable=False),
            sa.Column("source_ocr_result_id", sa.String(), nullable=True),
            sa.Column("template_version_id", sa.String(), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("payload_digest", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if "order_output_bundles" not in tables:
        op.create_table(
            "order_output_bundles",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("order_id", sa.String(), nullable=False),
            sa.Column("source_bagging_result_id", sa.String(), nullable=False),
            sa.Column("source_saved_sheet_id", sa.String(), nullable=False),
            sa.Column("source_ocr_result_id", sa.String(), nullable=True),
            sa.Column("template_version_id", sa.String(), nullable=True),
            sa.Column("materialization_digest", sa.String(), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("payload_digest", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    for table_name, columns in {
        "order_bagging_results": [
            "order_id",
            "source_saved_sheet_id",
            "source_ocr_result_id",
            "template_version_id",
            "payload_digest",
        ],
        "order_output_bundles": [
            "order_id",
            "source_bagging_result_id",
            "source_saved_sheet_id",
            "source_ocr_result_id",
            "template_version_id",
            "materialization_digest",
            "payload_digest",
        ],
    }.items():
        for column_name in columns:
            _create_index_if_missing(table_name, f"ix_{table_name}_{column_name}", [column_name])

    _create_fk_if_missing("order_bagging_results", "fk_order_bagging_results_order_id", "orders", ["order_id"], ["id"])
    _create_fk_if_missing(
        "order_bagging_results",
        "fk_order_bagging_results_source_saved_sheet_id",
        "order_sheet_drafts",
        ["source_saved_sheet_id"],
        ["id"],
    )
    _create_fk_if_missing(
        "order_bagging_results",
        "fk_order_bagging_results_source_ocr_result_id",
        "order_ocr_evidence_runs",
        ["source_ocr_result_id"],
        ["id"],
    )
    _create_fk_if_missing(
        "order_bagging_results",
        "fk_order_bagging_results_template_version_id",
        "facility_template_versions",
        ["template_version_id"],
        ["id"],
    )
    _create_fk_if_missing(
        "order_output_bundles",
        "fk_order_output_bundles_order_id",
        "orders",
        ["order_id"],
        ["id"],
    )
    _create_fk_if_missing(
        "order_output_bundles",
        "fk_order_output_bundles_source_bagging_result_id",
        "order_bagging_results",
        ["source_bagging_result_id"],
        ["id"],
    )
    _create_fk_if_missing(
        "order_output_bundles",
        "fk_order_output_bundles_source_saved_sheet_id",
        "order_sheet_drafts",
        ["source_saved_sheet_id"],
        ["id"],
    )
    _create_fk_if_missing(
        "order_output_bundles",
        "fk_order_output_bundles_source_ocr_result_id",
        "order_ocr_evidence_runs",
        ["source_ocr_result_id"],
        ["id"],
    )
    _create_fk_if_missing(
        "order_output_bundles",
        "fk_order_output_bundles_template_version_id",
        "facility_template_versions",
        ["template_version_id"],
        ["id"],
    )

    _add_column_if_missing("order_confirmed_snapshots", sa.Column("saved_sheet_id", sa.String(), nullable=True))
    _add_column_if_missing("order_confirmed_snapshots", sa.Column("bagging_result_id", sa.String(), nullable=True))
    _add_column_if_missing("order_confirmed_snapshots", sa.Column("output_bundle_id", sa.String(), nullable=True))
    _create_index_if_missing("order_confirmed_snapshots", "ix_order_confirmed_snapshots_saved_sheet_id", ["saved_sheet_id"])
    _create_index_if_missing("order_confirmed_snapshots", "ix_order_confirmed_snapshots_bagging_result_id", ["bagging_result_id"])
    _create_index_if_missing("order_confirmed_snapshots", "ix_order_confirmed_snapshots_output_bundle_id", ["output_bundle_id"])
    _create_fk_if_missing(
        "order_confirmed_snapshots",
        "fk_order_confirmed_snapshots_saved_sheet_id",
        "order_sheet_drafts",
        ["saved_sheet_id"],
        ["id"],
    )
    _create_fk_if_missing(
        "order_confirmed_snapshots",
        "fk_order_confirmed_snapshots_bagging_result_id",
        "order_bagging_results",
        ["bagging_result_id"],
        ["id"],
    )
    _create_fk_if_missing(
        "order_confirmed_snapshots",
        "fk_order_confirmed_snapshots_output_bundle_id",
        "order_output_bundles",
        ["output_bundle_id"],
        ["id"],
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        for table_name, fk_names in {
            "order_output_bundles": (
                "fk_order_output_bundles_template_version_id",
                "fk_order_output_bundles_source_ocr_result_id",
                "fk_order_output_bundles_source_saved_sheet_id",
                "fk_order_output_bundles_source_bagging_result_id",
                "fk_order_output_bundles_order_id",
            ),
            "order_bagging_results": (
                "fk_order_bagging_results_template_version_id",
                "fk_order_bagging_results_source_ocr_result_id",
                "fk_order_bagging_results_source_saved_sheet_id",
                "fk_order_bagging_results_order_id",
            ),
            "order_confirmed_snapshots": (
                "fk_order_confirmed_snapshots_output_bundle_id",
                "fk_order_confirmed_snapshots_bagging_result_id",
                "fk_order_confirmed_snapshots_saved_sheet_id",
            ),
        }.items():
            existing_fks = _fk_names(table_name) if table_name in _table_names() else set()
            for fk_name in fk_names:
                if fk_name in existing_fks:
                    op.drop_constraint(fk_name, table_name, type_="foreignkey")
    tables = _table_names()
    if "order_confirmed_snapshots" in tables:
        existing_indexes = _index_names("order_confirmed_snapshots")
        for index_name in (
            "ix_order_confirmed_snapshots_output_bundle_id",
            "ix_order_confirmed_snapshots_bagging_result_id",
            "ix_order_confirmed_snapshots_saved_sheet_id",
        ):
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name="order_confirmed_snapshots")
        columns = _column_names("order_confirmed_snapshots")
        for column_name in ("output_bundle_id", "bagging_result_id", "saved_sheet_id"):
            if column_name in columns:
                op.drop_column("order_confirmed_snapshots", column_name)
    if "order_output_bundles" in tables:
        op.drop_table("order_output_bundles")
    if "order_bagging_results" in tables:
        op.drop_table("order_bagging_results")
