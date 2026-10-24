from __future__ import annotations

from datetime import datetime
from copy import deepcopy

import pytest
from uuid import uuid4

from src.db import engine, session_scope
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_output_artifact import OrderBaggingResult, OrderOutputBundle
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.user import AuditLog
from src.services.order_pipeline_lineage_repair_service import (
    APPLY_CONFIRMATION_TOKEN,
    WORKFLOW_V2_META_KEY,
    backfill_step4_output_artifacts,
    repair_confirmed_workflow_v2_lineage,
    repair_confirmed_snapshot_payloads,
)


@pytest.fixture
def confirmed_order(monkeypatch, tmp_path, isolated_formal_schema):
    from tests.helpers.newnarrowbaggingfixture import accepted_bagging_order

    return accepted_bagging_order(monkeypatch, tmp_path, [{
        "date": "2026-05-10", "daypart": "昼", "menu_name": "Repair component",
        "diet_type": "regular", "area_id": "X", "bag_type": "standard", "quantity_original": 5,
    }], facility_id="FAC00001", week_start="2026-05-10", week_end="2026-05-16",
        menu_qty_per_serving=1, menu_unit_type="g", confirm=True)


def _id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:12]}"


def _seed_repairable_confirmed_snapshot(order_id: str) -> tuple[str, str, str]:
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        snapshot = session.get(OrderConfirmedSnapshot, workflow.confirmed_snapshot_id)
        assert snapshot.order_id == order_id
        assert snapshot.saved_sheet_id == workflow.draft_id
        snapshot_id, bagging_id, output_id = snapshot.id, snapshot.bagging_result_id, snapshot.output_bundle_id
        assert session.get(OrderBaggingResult, bagging_id) is not None
        assert session.get(OrderOutputBundle, output_id) is not None
        secondary = deepcopy(workflow.secondary_actions_json)
        meta = secondary[WORKFLOW_V2_META_KEY]
        for key in ("bagging_result", "bagging_result_id", "output_bundle", "output_bundle_id", "confirmed_snapshot_id"):
            meta.pop(key, None)
        workflow.secondary_actions_json = secondary
    return snapshot_id, bagging_id, output_id


def test_repair_confirmed_snapshot_payloads_dry_run_does_not_write(confirmed_order) -> None:
    order_id = confirmed_order["id"]
    _seed_repairable_confirmed_snapshot(order_id)
    from tests.unit.test_disabled_facility_columns import _snapshot
    original = _snapshot(engine)

    result = repair_confirmed_snapshot_payloads(order_id=order_id)

    assert result["mode"] == "dry_run"
    assert result["applied"] is False
    assert result["plan"]["status"] == "repairable"
    action_types = {action["action_type"] for action in result["plan"]["actions"]}
    assert "restore_bagging_result_id" in action_types
    assert "restore_output_bundle_id" in action_types
    assert _snapshot(engine) == original
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        meta = workflow.secondary_actions_json[WORKFLOW_V2_META_KEY]
        assert "bagging_result" not in meta
        assert "output_bundle" not in meta


def test_repair_confirmed_snapshot_payloads_apply_restores_workflow_meta(confirmed_order) -> None:
    order_id = confirmed_order["id"]
    snapshot_id, bagging_result_id, output_bundle_id = _seed_repairable_confirmed_snapshot(order_id)
    with session_scope() as session:
        original_bagging = deepcopy(session.get(OrderBaggingResult, bagging_result_id).payload_json)
        original_output = deepcopy(session.get(OrderOutputBundle, output_bundle_id).payload_json)

    result = repair_confirmed_snapshot_payloads(
        order_id=order_id,
        apply=True,
        confirm=APPLY_CONFIRMATION_TOKEN,
        actor="tester",
        reason="unit repair",
        idempotency_key=f"repair-{order_id}",
    )

    assert result["mode"] == "apply"
    assert result["applied"] is True
    assert result["plan"]["status"] == "repairable"
    assert result["repair_record"]["actor"] == "tester"
    assert result["repair_record"]["reason"] == "unit repair"
    assert result["repair_record"]["idempotency_key"] == f"repair-{order_id}"
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        meta = workflow.secondary_actions_json[WORKFLOW_V2_META_KEY]
        assert meta["bagging_result_id"] == bagging_result_id
        assert meta.get("bagging_result") is None
        assert session.get(OrderBaggingResult, bagging_result_id).payload_json == original_bagging
        assert original_bagging["bagging_result_id"] == bagging_result_id
        assert meta["output_bundle_id"] == output_bundle_id
        assert meta.get("output_bundle") is None
        assert session.get(OrderOutputBundle, output_bundle_id).payload_json == original_output
        assert original_output["output_bundle_id"] == output_bundle_id
        assert meta["confirmed_snapshot_id"] == snapshot_id
        audit_log = (
            session.query(AuditLog)
            .filter(AuditLog.action == "order_pipeline_lineage_repair")
            .filter(AuditLog.target == order_id)
            .one()
        )
        assert audit_log.actor == "tester"
        assert audit_log.metadata_json["before_digest"] == result["plan"]["before_digest"]
        assert audit_log.metadata_json["after_digest"] == result["plan"]["after_digest"]
        assert audit_log.metadata_json["affected_artifact_ids"]["bagging_result_ids"] == [bagging_result_id]
        assert audit_log.metadata_json["affected_artifact_ids"]["output_bundle_ids"] == [output_bundle_id]
        assert audit_log.metadata_json["affected_artifact_ids"]["confirmed_snapshot_ids"] == [snapshot_id]
    from tests.unit.test_disabled_facility_columns import _snapshot
    repaired = _snapshot(engine)
    repeated = repair_confirmed_snapshot_payloads(order_id=order_id, apply=True,
        confirm=APPLY_CONFIRMATION_TOKEN, actor="tester", reason="unit repair",
        idempotency_key=f"repair-{order_id}")
    assert repeated["applied"] is False
    assert repeated["plan"]["status"] == "no_op"
    assert _snapshot(engine) == repaired


def test_repair_confirmed_snapshot_payloads_blocks_when_snapshot_payloads_missing(confirmed_order) -> None:
    order_id = confirmed_order["id"]
    snapshot_id, _, _ = _seed_repairable_confirmed_snapshot(order_id)
    with session_scope() as session:
        snapshot = session.get(OrderConfirmedSnapshot, snapshot_id)
        # Deliberate archive damage: retain its original digest so it cannot become a new authority.
        snapshot.snapshot_json = {"source": "workflow_v2"}
    from tests.unit.test_disabled_facility_columns import _snapshot
    original = _snapshot(engine)

    result = repair_confirmed_snapshot_payloads(
        order_id=order_id,
        apply=True,
        confirm=APPLY_CONFIRMATION_TOKEN,
    )

    assert result["mode"] == "apply"
    assert result["applied"] is False
    assert result["plan"]["status"] == "blocked"
    assert result["plan"]["reason"] == "confirmed_snapshot_artifact_mismatch"
    assert _snapshot(engine) == original


def test_backfill_step4_output_artifacts_moves_legacy_workflow_payloads_to_artifact_tables(confirmed_order) -> None:
    order_id = confirmed_order["id"]
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        snapshot = session.get(OrderConfirmedSnapshot, workflow.confirmed_snapshot_id)
        bagging_result_id, output_bundle_id = snapshot.bagging_result_id, snapshot.output_bundle_id
        bagging_payload = deepcopy(session.get(OrderBaggingResult, bagging_result_id).payload_json)
        output_payload = deepcopy(session.get(OrderOutputBundle, output_bundle_id).payload_json)
        secondary = deepcopy(workflow.secondary_actions_json)
        secondary[WORKFLOW_V2_META_KEY].update(bagging_result=bagging_payload, output_bundle=output_payload)
        workflow.secondary_actions_json = secondary

    dry_run = backfill_step4_output_artifacts(order_id=order_id)

    assert dry_run["mode"] == "dry_run"
    assert dry_run["applied"] is False
    assert dry_run["summary"]["counts_by_status"]["repairable"] == 1
    with session_scope() as session:
        assert session.get(OrderBaggingResult, bagging_result_id).payload_json == bagging_payload
        assert session.get(OrderOutputBundle, output_bundle_id).payload_json == output_payload

    applied = backfill_step4_output_artifacts(
        order_id=order_id,
        apply=True,
        confirm=APPLY_CONFIRMATION_TOKEN,
        actor="tester",
        idempotency_key=f"backfill-{order_id}",
    )

    assert applied["mode"] == "apply"
    assert applied["applied"] is True
    with session_scope() as session:
        bagging_artifact = session.get(OrderBaggingResult, bagging_result_id)
        output_artifact = session.get(OrderOutputBundle, output_bundle_id)
        workflow = session.get(OrderWorkflowState, order_id)
        assert bagging_artifact is not None
        assert output_artifact is not None
        assert bagging_artifact.payload_json["bagging_result_id"] == bagging_result_id
        assert output_artifact.payload_json["output_bundle_id"] == output_bundle_id
        assert bagging_artifact.payload_json == bagging_payload
        assert output_artifact.payload_json == output_payload
        meta = workflow.secondary_actions_json[WORKFLOW_V2_META_KEY]
        assert meta["bagging_result"] is None
        assert meta["output_bundle"] is None


def test_step4_backfill_cannot_recreate_missing_artifact_identity_from_embedded_payload(confirmed_order):
    from tests.unit.test_disabled_facility_columns import _snapshot

    order_id = confirmed_order["id"]
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        snapshot = session.get(OrderConfirmedSnapshot, workflow.confirmed_snapshot_id)
        original_output = deepcopy(session.get(OrderOutputBundle, snapshot.output_bundle_id).payload_json)
        secondary = deepcopy(workflow.secondary_actions_json)
        secondary[WORKFLOW_V2_META_KEY].update(output_bundle_id="missing-component-artifact",
            output_bundle=original_output)
        workflow.secondary_actions_json = secondary
    before = _snapshot(engine)
    result = backfill_step4_output_artifacts(order_id=order_id, apply=True, confirm=APPLY_CONFIRMATION_TOKEN)
    assert result["applied"] is False
    assert result["plans"][0]["status"] == "blocked"
    assert result["plans"][0]["reason"] == "artifact_missing_or_mismatch"
    assert _snapshot(engine) == before


def test_repair_confirmed_workflow_v2_lineage_restores_snapshot_source_of_truth(confirmed_order) -> None:
    order_id = confirmed_order["id"]
    stale_latest_draft_id = _id("ODR")
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        snapshot = session.get(OrderConfirmedSnapshot, workflow.confirmed_snapshot_id)
        snapshot_id, saved_sheet_id = snapshot.id, snapshot.saved_sheet_id
        bagging_result_id, output_bundle_id = snapshot.bagging_result_id, snapshot.output_bundle_id
        template_version_id = snapshot.template_version_id
        original_snapshot = deepcopy(snapshot.snapshot_json)
        original_sheet = deepcopy(session.get(OrderSheetDraft, saved_sheet_id).draft_sheet_json)
        session.add(OrderSheetDraft(id=stale_latest_draft_id, order_id=order_id,
            template_version_id=None, draft_sheet_json={"rows": [["05/11"]]},
            draft_state="draft_ready", edited_by="test", edited_at=datetime.utcnow(), created_at=datetime.utcnow()))
        session.flush()
        session.get(OrderSheetDraft, saved_sheet_id).template_version_id = None
        workflow.draft_id = stale_latest_draft_id
        workflow.template_version_id = None
        workflow.secondary_actions_json = ["rerun_yomitoku", "save_draft"]
        workflow.blockers_json = ["draft_newer_than_lines"]
        workflow.warnings_json = ["draft_newer_than_lines"]

    dry_run = repair_confirmed_workflow_v2_lineage(order_id=order_id)

    assert dry_run["mode"] == "dry_run"
    assert dry_run["applied"] is False
    assert dry_run["summary"]["counts_by_status"]["repairable"] == 1

    applied = repair_confirmed_workflow_v2_lineage(
        order_id=order_id,
        apply=True,
        confirm=APPLY_CONFIRMATION_TOKEN,
        actor="tester",
        idempotency_key=f"confirmed-lineage-{order_id}",
    )

    assert applied["mode"] == "apply"
    assert applied["applied"] is True
    assert applied["summary"]["counts_by_status"]["repairable"] == 1
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        snapshot = session.get(OrderConfirmedSnapshot, snapshot_id)
        draft = session.get(OrderSheetDraft, saved_sheet_id)
        assert workflow.draft_id == saved_sheet_id
        assert workflow.blockers_json == []
        assert workflow.warnings_json == []
        assert workflow.secondary_actions_json[WORKFLOW_V2_META_KEY]["saved_sheet_id"] == saved_sheet_id
        assert workflow.secondary_actions_json[WORKFLOW_V2_META_KEY]["bagging_result_id"] == bagging_result_id
        assert workflow.secondary_actions_json[WORKFLOW_V2_META_KEY]["output_bundle_id"] == output_bundle_id
        assert snapshot.saved_sheet_id == saved_sheet_id
        assert snapshot.bagging_result_id == bagging_result_id
        assert snapshot.output_bundle_id == output_bundle_id
        assert draft.template_version_id == template_version_id
        assert draft.draft_sheet_json == original_sheet
        assert snapshot.snapshot_json == original_snapshot
        assert session.get(OrderSheetDraft, stale_latest_draft_id).draft_sheet_json == {"rows": [["05/11"]]}
        assert session.get(OrderBaggingResult, bagging_result_id) is not None
        assert session.get(OrderOutputBundle, output_bundle_id) is not None
