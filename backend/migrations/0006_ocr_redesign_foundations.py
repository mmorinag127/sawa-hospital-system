"""Add OCR redesign foundation tables."""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_ocr_evidence_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("producer_version", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="ready"),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("artifact_manifest_json", sa.JSON(), nullable=True),
        sa.Column("artifact_digest", sa.String(), nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=True),
        sa.Column("degraded_reasons_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_order_ocr_evidence_runs_order_id", "order_ocr_evidence_runs", ["order_id"])
    op.create_index("ix_order_ocr_evidence_runs_artifact_digest", "order_ocr_evidence_runs", ["artifact_digest"])

    op.create_table(
        "order_sheet_drafts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("base_evidence_run_id", sa.String(), sa.ForeignKey("order_ocr_evidence_runs.id"), nullable=True),
        sa.Column("base_template_resolution_id", sa.String(), nullable=True),
        sa.Column("base_menu_snapshot_id", sa.String(), nullable=True),
        sa.Column("draft_sheet_json", sa.JSON(), nullable=False),
        sa.Column("draft_state", sa.String(), nullable=False, server_default="draft"),
        sa.Column("blockers_json", sa.JSON(), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("latest_patch_candidate_id", sa.String(), nullable=True),
        sa.Column("edited_by", sa.String(), nullable=True),
        sa.Column("edited_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_order_sheet_drafts_order_id", "order_sheet_drafts", ["order_id"])
    op.create_index("ix_order_sheet_drafts_base_evidence_run_id", "order_sheet_drafts", ["base_evidence_run_id"])

    op.create_table(
        "order_confirmed_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("draft_id", sa.String(), sa.ForeignKey("order_sheet_drafts.id"), nullable=True),
        sa.Column("snapshot_digest", sa.String(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("confirmed_by", sa.String(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_order_confirmed_snapshots_order_id", "order_confirmed_snapshots", ["order_id"])
    op.create_index("ix_order_confirmed_snapshots_draft_id", "order_confirmed_snapshots", ["draft_id"])
    op.create_index("ix_order_confirmed_snapshots_snapshot_digest", "order_confirmed_snapshots", ["snapshot_digest"])

    op.create_table(
        "order_workflow_states",
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), primary_key=True),
        sa.Column("evidence_run_id", sa.String(), sa.ForeignKey("order_ocr_evidence_runs.id"), nullable=True),
        sa.Column("draft_id", sa.String(), sa.ForeignKey("order_sheet_drafts.id"), nullable=True),
        sa.Column("confirmed_snapshot_id", sa.String(), sa.ForeignKey("order_confirmed_snapshots.id"), nullable=True),
        sa.Column("state", sa.String(), nullable=False, server_default="uploaded"),
        sa.Column("headline", sa.String(), nullable=True),
        sa.Column("primary_action", sa.String(), nullable=True),
        sa.Column("secondary_actions_json", sa.JSON(), nullable=True),
        sa.Column("blockers_json", sa.JSON(), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("confidence_band", sa.String(), nullable=True),
        sa.Column("last_transition_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "order_critical_decisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("decision_type", sa.String(), nullable=False),
        sa.Column("candidate_set_json", sa.JSON(), nullable=False),
        sa.Column("selected_value", sa.String(), nullable=True),
        sa.Column("selected_by", sa.String(), nullable=True),
        sa.Column("selected_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_order_critical_decisions_order_id", "order_critical_decisions", ["order_id"])
    op.create_index("ix_order_critical_decisions_decision_type", "order_critical_decisions", ["decision_type"])


def downgrade() -> None:
    op.drop_index("ix_order_critical_decisions_decision_type", table_name="order_critical_decisions")
    op.drop_index("ix_order_critical_decisions_order_id", table_name="order_critical_decisions")
    op.drop_table("order_critical_decisions")

    op.drop_table("order_workflow_states")

    op.drop_index("ix_order_confirmed_snapshots_snapshot_digest", table_name="order_confirmed_snapshots")
    op.drop_index("ix_order_confirmed_snapshots_draft_id", table_name="order_confirmed_snapshots")
    op.drop_index("ix_order_confirmed_snapshots_order_id", table_name="order_confirmed_snapshots")
    op.drop_table("order_confirmed_snapshots")

    op.drop_index("ix_order_sheet_drafts_base_evidence_run_id", table_name="order_sheet_drafts")
    op.drop_index("ix_order_sheet_drafts_order_id", table_name="order_sheet_drafts")
    op.drop_table("order_sheet_drafts")

    op.drop_index("ix_order_ocr_evidence_runs_artifact_digest", table_name="order_ocr_evidence_runs")
    op.drop_index("ix_order_ocr_evidence_runs_order_id", table_name="order_ocr_evidence_runs")
    op.drop_table("order_ocr_evidence_runs")
