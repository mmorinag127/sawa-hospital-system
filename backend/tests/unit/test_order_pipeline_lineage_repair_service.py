from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from src.db import Base, engine, session_scope
from src.models.order import Order
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


Base.metadata.create_all(bind=engine)


def _id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:12]}"


def _create_order() -> str:
    order_id = _id("ORDrepair")
    with session_scope() as session:
        session.add(
            Order(
                id=order_id,
                facility_code="FAC00001",
                week_code="2026-05@2026-05-10~2026-05-16",
                status="確定",
                document_uri=f"file:///{order_id}.pdf",
                message_id=f"msg-{order_id}",
                received_at=datetime.utcnow(),
            )
        )
    return order_id


def _seed_repairable_confirmed_snapshot(order_id: str) -> tuple[str, str, str]:
    snapshot_id = _id("OCS")
    bagging_result_id = _id("OBG")
    output_bundle_id = _id("OOB")
    with session_scope() as session:
        session.add(
            OrderConfirmedSnapshot(
                id=snapshot_id,
                order_id=order_id,
                template_version_id="FTV_REPAIR",
                draft_id="ODR_REPAIR",
                snapshot_digest="snapshot-digest",
                snapshot_json={
                    "source": "workflow_v2",
                    "bagging_result": {
                        "bagging_result_id": bagging_result_id,
                        "source_saved_sheet_id": "ODR_REPAIR",
                        "template_version_id": "FTV_REPAIR",
                    },
                    "output_bundle": {
                        "output_bundle_id": output_bundle_id,
                        "source_bagging_result_id": bagging_result_id,
                        "source_saved_sheet_id": "ODR_REPAIR",
                        "confirmed_snapshot_id": snapshot_id,
                        "template_version_id": "FTV_REPAIR",
                    },
                },
                confirmed_by="test",
                confirmed_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                template_version_id="FTV_REPAIR",
                evidence_run_id=None,
                draft_id="ODR_REPAIR",
                confirmed_snapshot_id=snapshot_id,
                state="confirmed",
                headline="confirmed",
                primary_action=None,
                secondary_actions_json={WORKFLOW_V2_META_KEY: {"template_version_id": "FTV_REPAIR"}},
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )
    return snapshot_id, bagging_result_id, output_bundle_id


def test_repair_confirmed_snapshot_payloads_dry_run_does_not_write() -> None:
    order_id = _create_order()
    _seed_repairable_confirmed_snapshot(order_id)

    result = repair_confirmed_snapshot_payloads(order_id=order_id)

    assert result["mode"] == "dry_run"
    assert result["applied"] is False
    assert result["plan"]["status"] == "repairable"
    action_types = {action["action_type"] for action in result["plan"]["actions"]}
    assert "restore_bagging_result" in action_types
    assert "restore_output_bundle" in action_types
    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, order_id)
        meta = workflow.secondary_actions_json[WORKFLOW_V2_META_KEY]
        assert "bagging_result" not in meta
        assert "output_bundle" not in meta


def test_repair_confirmed_snapshot_payloads_apply_restores_workflow_meta() -> None:
    order_id = _create_order()
    snapshot_id, bagging_result_id, output_bundle_id = _seed_repairable_confirmed_snapshot(order_id)

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
        assert meta["bagging_result"]["bagging_result_id"] == bagging_result_id
        assert meta["output_bundle_id"] == output_bundle_id
        assert meta["output_bundle"]["output_bundle_id"] == output_bundle_id
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


def test_repair_confirmed_snapshot_payloads_blocks_when_snapshot_payloads_missing() -> None:
    order_id = _create_order()
    snapshot_id = _id("OCS")
    with session_scope() as session:
        session.add(
            OrderConfirmedSnapshot(
                id=snapshot_id,
                order_id=order_id,
                template_version_id="FTV_REPAIR",
                draft_id="ODR_REPAIR",
                snapshot_digest="snapshot-digest",
                snapshot_json={"source": "workflow_v2"},
                confirmed_by="test",
                confirmed_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                template_version_id="FTV_REPAIR",
                evidence_run_id=None,
                draft_id="ODR_REPAIR",
                confirmed_snapshot_id=snapshot_id,
                state="confirmed",
                headline="confirmed",
                primary_action=None,
                secondary_actions_json={WORKFLOW_V2_META_KEY: {}},
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    result = repair_confirmed_snapshot_payloads(
        order_id=order_id,
        apply=True,
        confirm=APPLY_CONFIRMATION_TOKEN,
    )

    assert result["mode"] == "apply"
    assert result["applied"] is False
    assert result["plan"]["status"] == "blocked"
    assert result["plan"]["reason"] == "confirmed_snapshot_payloads_missing"


def test_backfill_step4_output_artifacts_moves_legacy_workflow_payloads_to_artifact_tables() -> None:
    order_id = _create_order()
    bagging_result_id = _id("OBG")
    output_bundle_id = _id("OOB")
    with session_scope() as session:
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                template_version_id="FTV_REPAIR",
                evidence_run_id="OEV_REPAIR",
                draft_id="ODR_REPAIR",
                confirmed_snapshot_id=None,
                state="output_review",
                headline="output review",
                primary_action="final_confirm",
                secondary_actions_json={
                    WORKFLOW_V2_META_KEY: {
                        "template_version_id": "FTV_REPAIR",
                        "bagging_result_id": bagging_result_id,
                        "bagging_result": {
                            "bagging_result_id": bagging_result_id,
                            "order_id": order_id,
                            "source_saved_sheet_id": "ODR_REPAIR",
                            "source_ocr_result_id": "OEV_REPAIR",
                            "template_version_id": "FTV_REPAIR",
                        },
                        "output_bundle_id": output_bundle_id,
                        "output_bundle": {
                            "output_bundle_id": output_bundle_id,
                            "order_id": order_id,
                            "source_bagging_result_id": bagging_result_id,
                            "source_saved_sheet_id": "ODR_REPAIR",
                            "source_ocr_result_id": "OEV_REPAIR",
                            "template_version_id": "FTV_REPAIR",
                        },
                    }
                },
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    dry_run = backfill_step4_output_artifacts(order_id=order_id)

    assert dry_run["mode"] == "dry_run"
    assert dry_run["applied"] is False
    assert dry_run["summary"]["counts_by_status"]["repairable"] == 1
    with session_scope() as session:
        assert session.get(OrderBaggingResult, bagging_result_id) is None
        assert session.get(OrderOutputBundle, output_bundle_id) is None

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
        meta = workflow.secondary_actions_json[WORKFLOW_V2_META_KEY]
        assert meta["bagging_result"] is None
        assert meta["output_bundle"] is None


def test_repair_confirmed_workflow_v2_lineage_restores_snapshot_source_of_truth() -> None:
    order_id = _create_order()
    saved_sheet_id = _id("ODS")
    stale_latest_draft_id = _id("ODR")
    snapshot_id = _id("OCS")
    bagging_result_id = _id("OBG")
    output_bundle_id = _id("OOB")
    with session_scope() as session:
        session.add(
            OrderSheetDraft(
                id=saved_sheet_id,
                order_id=order_id,
                template_version_id=None,
                draft_sheet_json={"rows": [["05/10"]]},
                draft_state="saved",
                edited_by="test",
                edited_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderSheetDraft(
                id=stale_latest_draft_id,
                order_id=order_id,
                template_version_id=None,
                draft_sheet_json={"rows": [["05/11"]]},
                draft_state="draft_ready",
                edited_by="test",
                edited_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderConfirmedSnapshot(
                id=snapshot_id,
                order_id=order_id,
                template_version_id="FTV_REPAIR",
                draft_id=saved_sheet_id,
                saved_sheet_id=None,
                snapshot_digest="snapshot-digest",
                snapshot_json={
                    "source": "workflow_v2",
                    "template_version_id": "FTV_REPAIR",
                    "saved_sheet_id": saved_sheet_id,
                    "bagging_result": {
                        "bagging_result_id": bagging_result_id,
                        "order_id": order_id,
                        "source_saved_sheet_id": saved_sheet_id,
                        "template_version_id": "FTV_REPAIR",
                    },
                    "output_bundle": {
                        "output_bundle_id": output_bundle_id,
                        "order_id": order_id,
                        "source_bagging_result_id": bagging_result_id,
                        "source_saved_sheet_id": saved_sheet_id,
                        "confirmed_snapshot_id": snapshot_id,
                        "template_version_id": "FTV_REPAIR",
                    },
                },
                confirmed_by="test",
                confirmed_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                template_version_id="FTV_REPAIR",
                evidence_run_id=None,
                draft_id=stale_latest_draft_id,
                confirmed_snapshot_id=snapshot_id,
                state="confirmed",
                headline="confirmed",
                primary_action=None,
                secondary_actions_json=["rerun_yomitoku", "save_draft"],
                blockers_json=["draft_newer_than_lines"],
                warnings_json=["draft_newer_than_lines"],
                last_transition_at=datetime.utcnow(),
            )
        )

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
        assert draft.template_version_id == "FTV_REPAIR"
        assert session.get(OrderBaggingResult, bagging_result_id) is not None
        assert session.get(OrderOutputBundle, output_bundle_id) is not None
