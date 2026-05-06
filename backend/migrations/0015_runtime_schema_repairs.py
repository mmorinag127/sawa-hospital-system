"""Move historical runtime schema repairs into an explicit migration."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in set(_inspector().get_table_names())


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return column_name in {str(column.get("name") or "") for column in _inspector().get_columns(table_name)}


def _create_table_if_missing(table_name: str, *elements: sa.SchemaItem) -> None:
    if not _has_table(table_name):
        op.create_table(table_name, *elements)


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if _has_table(table_name) and not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def _exec_if_table_exists(table_name: str, statement: str) -> None:
    if _has_table(table_name):
        op.get_bind().execute(sa.text(statement))


def upgrade() -> None:
    _add_column_if_missing("orders", sa.Column("lines_updated_at", sa.DateTime(), nullable=True))
    _add_column_if_missing("orders", sa.Column("archived_at", sa.DateTime(), nullable=True))
    _add_column_if_missing("orders", sa.Column("archived_by", sa.String(), nullable=True))
    _add_column_if_missing("orders", sa.Column("template_version_id", sa.String(), nullable=True))

    _create_table_if_missing(
        "menu_masters",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False),
        sa.Column("unit_type", sa.String(), nullable=True),
        sa.Column("qty_per_serving", sa.Float(), nullable=True),
        sa.Column("bag_max_qty", sa.Float(), nullable=True),
        sa.Column("bag_max_unit", sa.String(), nullable=True),
        sa.Column("temp_type", sa.String(), nullable=True),
        sa.Column("daypart", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("condiments", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("normalized_name", name="uq_menu_masters_normalized_name"),
    )
    _add_column_if_missing("menu_masters", sa.Column("condiments", sa.JSON(), nullable=True))

    _create_table_if_missing(
        "menu_facility_overrides",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("menu_master_id", sa.String(), sa.ForeignKey("menu_masters.id"), nullable=False),
        sa.Column("facility_id", sa.String(), nullable=False),
        sa.Column("unit_type", sa.String(), nullable=True),
        sa.Column("qty_per_serving", sa.Float(), nullable=True),
        sa.Column("bag_max_qty", sa.Float(), nullable=True),
        sa.Column("bag_max_unit", sa.String(), nullable=True),
        sa.Column("temp_type", sa.String(), nullable=True),
        sa.Column("daypart", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.UniqueConstraint("menu_master_id", "facility_id", name="uq_menu_facility_override_scope"),
    )
    _add_column_if_missing("monthly_menu_items", sa.Column("menu_master_id", sa.String(), nullable=True))
    _add_column_if_missing("monthly_menu_items", sa.Column("master_resolution_mode", sa.String(), nullable=True))
    _add_column_if_missing("monthly_menu_entries", sa.Column("facility_override", sa.String(), nullable=True))

    _create_table_if_missing(
        "base_menu_cycle_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("cycle_day", sa.Integer(), nullable=False),
        sa.Column("daypart", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("diet_type", sa.String(), nullable=True),
        sa.Column("slot_index", sa.Integer(), nullable=True),
    )

    _create_table_if_missing(
        "order_menu_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False, unique=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    _create_table_if_missing(
        "order_ocr_cache",
        sa.Column("order_id", sa.String(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    _create_table_if_missing(
        "order_ocr_revisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), nullable=False),
        sa.Column("ui_mode", sa.String(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sheet_save_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sheet_save_mode", sa.String(), nullable=True),
        sa.Column("before_digest", sa.String(), nullable=True),
        sa.Column("after_digest", sa.String(), nullable=True),
        sa.Column("fields", sa.JSON(), nullable=True),
        sa.Column("header", sa.JSON(), nullable=True),
        sa.Column("row_ids", sa.JSON(), nullable=True),
        sa.Column("rows", sa.JSON(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    _create_table_if_missing(
        "shipping_tracking_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tracking_key", sa.String(), nullable=False),
        sa.Column("tracking_number", sa.String(), nullable=False),
        sa.Column("ship_date", sa.Date(), nullable=True),
        sa.Column("facility_name", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("arrival_text", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("looked_up_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    _add_column_if_missing("shipping_tracking_logs", sa.Column("ship_date", sa.Date(), nullable=True))

    _create_table_if_missing(
        "ocr_training_samples",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("facility_code", sa.String(), nullable=True),
        sa.Column("week_code", sa.String(), nullable=True),
        sa.Column("document_uri", sa.String(), nullable=False),
        sa.Column("ocr_job_id", sa.String(), nullable=True),
        sa.Column("ocr_provider", sa.String(), nullable=True),
        sa.Column("ocr_output", sa.JSON(), nullable=True),
        sa.Column("labeled_lines", sa.JSON(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_corrections", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    for table_name in (
        "ocr_jobs",
        "order_ocr_evidence_runs",
        "order_sheet_drafts",
        "order_confirmed_snapshots",
        "order_workflow_states",
        "order_current_states",
    ):
        _add_column_if_missing(table_name, sa.Column("template_version_id", sa.String(), nullable=True))

    _add_column_if_missing("order_ocr_evidence_runs", sa.Column("source", sa.String(), nullable=True))

    patch_candidate_columns = (
        sa.Column("draft_id", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("patch_scope", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("patch_json", sa.JSON(), nullable=True),
        sa.Column("apply_plan_json", sa.JSON(), nullable=True),
        sa.Column("apply_ready_metadata_json", sa.JSON(), nullable=True),
        sa.Column("blockers_json", sa.JSON(), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("candidate_state", sa.String(), nullable=True),
        sa.Column("base_draft_id", sa.String(), nullable=True),
        sa.Column("base_evidence_run_id", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("prompt_preset", sa.String(), nullable=True),
        sa.Column("baseline_source", sa.String(), nullable=True),
        sa.Column("baseline_revision_id", sa.String(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("issues_json", sa.JSON(), nullable=True),
        sa.Column("patches_json", sa.JSON(), nullable=True),
        sa.Column("proposed_draft_sheet_json", sa.JSON(), nullable=True),
        sa.Column("applied_by", sa.String(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
    )
    for column in patch_candidate_columns:
        _add_column_if_missing("order_sheet_patch_candidates", column)

    _exec_if_table_exists(
        "order_sheet_patch_candidates",
        """
        UPDATE order_sheet_patch_candidates
        SET candidate_state = COALESCE(NULLIF(candidate_state, ''), NULLIF(status, ''), 'ready')
        WHERE candidate_state IS NULL OR candidate_state = ''
        """,
    )
    _exec_if_table_exists(
        "order_sheet_patch_candidates",
        "UPDATE order_sheet_patch_candidates SET source = 'ocr_review' WHERE source IS NULL",
    )
    _exec_if_table_exists(
        "order_sheet_patch_candidates",
        "UPDATE order_sheet_patch_candidates SET patch_scope = 'sheet' WHERE patch_scope IS NULL",
    )
    _exec_if_table_exists(
        "order_sheet_patch_candidates",
        "UPDATE order_sheet_patch_candidates SET updated_at = created_at WHERE updated_at IS NULL",
    )

    _exec_if_table_exists(
        "menu_facility_overrides",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_menu_facility_overrides_master_facility
        ON menu_facility_overrides(menu_master_id, facility_id)
        """,
    )
    _exec_if_table_exists(
        "monthly_menu_items",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_monthly_menu_items_scope_name
        ON monthly_menu_items(monthly_menu_id, name, COALESCE(facility_override, ''))
        """,
    )
    _exec_if_table_exists(
        "monthly_menu_entries",
        """
        CREATE INDEX IF NOT EXISTS ix_monthly_menu_entries_scope_slot
        ON monthly_menu_entries(
          monthly_menu_id,
          menu_date,
          daypart,
          COALESCE(slot_index, -1),
          COALESCE(facility_override, '')
        )
        """,
    )
    _exec_if_table_exists(
        "shipping_tracking_logs",
        "CREATE INDEX IF NOT EXISTS ix_shipping_tracking_logs_ship_date ON shipping_tracking_logs (ship_date)",
    )
    _exec_if_table_exists(
        "shipping_tracking_events",
        """
        CREATE INDEX IF NOT EXISTS ix_shipping_tracking_events_tracking_key_order
        ON shipping_tracking_events (tracking_key, event_order)
        """,
    )

    inspector = _inspector()
    if op.get_bind().dialect.name != "sqlite" and _has_table("facility_areas"):
        pk = inspector.get_pk_constraint("facility_areas") or {}
        constrained = set(pk.get("constrained_columns") or [])
        if constrained != {"facility_id", "id"}:
            constraint_name = pk.get("name") or "facility_areas_pkey"
            op.get_bind().execute(sa.text(f'ALTER TABLE facility_areas DROP CONSTRAINT IF EXISTS "{constraint_name}"'))
            op.get_bind().execute(sa.text("ALTER TABLE facility_areas ADD PRIMARY KEY (facility_id, id)"))


def downgrade() -> None:
    # This migration formalizes schema that may already exist from historical
    # runtime repairs. Downgrade is intentionally non-destructive.
    pass
