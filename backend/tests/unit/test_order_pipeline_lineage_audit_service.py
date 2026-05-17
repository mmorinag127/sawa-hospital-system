from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from src.db import Base, engine, session_scope
from src.models.order import Order
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services.order_pipeline_lineage_audit_service import audit_order_pipeline_lineage


Base.metadata.create_all(bind=engine)


def _id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:12]}"


def _create_order() -> str:
    order_id = _id("ORDaudit")
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


def test_audit_reports_confirmed_legacy_lineage_gaps() -> None:
    order_id = _create_order()
    evidence_id = _id("OEV")
    draft_id = _id("ODR")
    snapshot_id = _id("OCS")
    template_version_id = _id("FTV")
    bagging_result_id = _id("OBG")
    output_bundle_id = _id("OOB")

    with session_scope() as session:
        session.add(
            OrderOcrEvidenceRun(
                id=evidence_id,
                order_id=order_id,
                template_version_id=template_version_id,
                schema_version="test",
                producer_version="test",
                source="test",
                status="ready",
                payload_json={},
                artifact_digest=f"digest-{evidence_id}",
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderSheetDraft(
                id=draft_id,
                order_id=order_id,
                template_version_id=None,
                base_evidence_run_id=evidence_id,
                draft_sheet_json={"rows": []},
                draft_state="saved",
                edited_by="test",
                edited_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                template_version_id=template_version_id,
                evidence_run_id=evidence_id,
                draft_id=draft_id,
                confirmed_snapshot_id=snapshot_id,
                state="apply_ready",
                headline="legacy state",
                primary_action="apply_draft",
                secondary_actions_json={
                    "workflow_v2": {
                        "template_version_id": template_version_id,
                        "bagging_result_id": bagging_result_id,
                        "output_bundle_id": output_bundle_id,
                    }
                },
                blockers_json=["draft_newer_than_lines"],
                warnings_json=["draft_newer_than_lines"],
                last_transition_at=datetime.utcnow(),
            )
        )

    result = audit_order_pipeline_lineage(order_id=order_id)

    assert result["mode"] == "read_only"
    issue_types = {issue["issue_type"] for issue in result["issues"]}
    assert "saved_sheet_template_version_missing" in issue_types
    assert "bagging_result_payload_missing" in issue_types
    assert "output_bundle_payload_missing" in issue_types
    assert "confirmed_snapshot_row_missing" in issue_types
    assert result["counts_by_type"]["confirmed_snapshot_row_missing"] == 1


def test_audit_reports_snapshot_draft_and_output_source_mismatches() -> None:
    order_id = _create_order()
    evidence_id = _id("OEV")
    draft_id = _id("ODR")
    other_draft_id = _id("ODR")
    snapshot_id = _id("OCS")
    template_version_id = _id("FTV")
    bagging_result_id = _id("OBG")
    output_bundle_id = _id("OOB")

    with session_scope() as session:
        session.add(
            OrderOcrEvidenceRun(
                id=evidence_id,
                order_id=order_id,
                template_version_id=template_version_id,
                schema_version="test",
                producer_version="test",
                source="test",
                status="ready",
                payload_json={},
                artifact_digest=f"digest-{evidence_id}",
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderSheetDraft(
                id=draft_id,
                order_id=order_id,
                template_version_id=template_version_id,
                base_evidence_run_id=evidence_id,
                draft_sheet_json={"rows": []},
                draft_state="saved",
                edited_by="test",
                edited_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderConfirmedSnapshot(
                id=snapshot_id,
                order_id=order_id,
                template_version_id=template_version_id,
                draft_id=other_draft_id,
                snapshot_digest="digest",
                snapshot_json={},
                confirmed_by="test",
                confirmed_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            OrderWorkflowState(
                order_id=order_id,
                template_version_id=template_version_id,
                evidence_run_id=evidence_id,
                draft_id=draft_id,
                confirmed_snapshot_id=snapshot_id,
                state="confirmed",
                headline="confirmed",
                primary_action=None,
                secondary_actions_json={
                    "workflow_v2": {
                        "template_version_id": template_version_id,
                        "bagging_result_id": bagging_result_id,
                        "bagging_result": {
                            "bagging_result_id": bagging_result_id,
                            "source_saved_sheet_id": other_draft_id,
                            "template_version_id": template_version_id,
                        },
                        "output_bundle_id": output_bundle_id,
                        "output_bundle": {
                            "output_bundle_id": output_bundle_id,
                            "source_bagging_result_id": _id("OBGstale"),
                            "source_saved_sheet_id": other_draft_id,
                            "template_version_id": template_version_id,
                        },
                    }
                },
                blockers_json=[],
                warnings_json=[],
                last_transition_at=datetime.utcnow(),
            )
        )

    result = audit_order_pipeline_lineage(order_id=order_id)

    issue_types = {issue["issue_type"] for issue in result["issues"]}
    assert "confirmed_snapshot_draft_mismatch" in issue_types
    assert "bagging_result_source_saved_sheet_mismatch" in issue_types
    assert "output_bundle_source_bagging_mismatch" in issue_types
    assert "output_bundle_source_saved_sheet_mismatch" in issue_types
