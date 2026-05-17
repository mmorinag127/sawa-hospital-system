from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from src.db import session_scope
from src.models.order import Order
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.services import order_output_artifact_service


WORKFLOW_V2_META_KEY = "workflow_v2"


@dataclass(frozen=True)
class LineageIssue:
    order_id: str
    issue_type: str
    severity: str
    repair_safety: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "repair_safety": self.repair_safety,
            "details": self.details,
        }


def _normalize_id(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _workflow_meta(workflow: OrderWorkflowState | None) -> dict[str, Any]:
    if workflow is None or not isinstance(workflow.secondary_actions_json, dict):
        return {}
    meta = workflow.secondary_actions_json.get(WORKFLOW_V2_META_KEY)
    return dict(meta) if isinstance(meta, dict) else {}


def _payload(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _bagging_payload(session: Session, meta: dict[str, Any]) -> dict[str, Any] | None:
    return _payload(meta.get("bagging_result")) or order_output_artifact_service.load_bagging_result_payload(
        session,
        _normalize_id(meta.get("bagging_result_id")),
    )


def _output_payload(session: Session, meta: dict[str, Any]) -> dict[str, Any] | None:
    return _payload(meta.get("output_bundle")) or order_output_artifact_service.load_output_bundle_payload(
        session,
        _normalize_id(meta.get("output_bundle_id")),
    )


def _confirmed_snapshot_id(workflow: OrderWorkflowState | None, meta: dict[str, Any]) -> str | None:
    output_bundle = _payload(meta.get("output_bundle"))
    return (
        _normalize_id(getattr(workflow, "confirmed_snapshot_id", None))
        or _normalize_id(meta.get("confirmed_snapshot_id"))
        or (_normalize_id(output_bundle.get("confirmed_snapshot_id")) if output_bundle else None)
    )


def _id_from_payload_or_meta(meta: dict[str, Any], *, payload_key: str, id_key: str) -> str | None:
    payload = _payload(meta.get(payload_key))
    return _normalize_id(meta.get(id_key)) or (_normalize_id(payload.get(id_key)) if payload else None)


def _is_confirmed_like(order: Order, workflow: OrderWorkflowState | None, meta: dict[str, Any]) -> bool:
    if _normalize_id(order.status) == "確定":
        return True
    state = _normalize_id(getattr(workflow, "state", None))
    if state == "confirmed":
        return True
    if _confirmed_snapshot_id(workflow, meta):
        return True
    return bool(_id_from_payload_or_meta(meta, payload_key="output_bundle", id_key="output_bundle_id"))


def _issue(
    *,
    order_id: str,
    issue_type: str,
    severity: str = "error",
    repair_safety: str = "blocked",
    **details: Any,
) -> LineageIssue:
    return LineageIssue(
        order_id=order_id,
        issue_type=issue_type,
        severity=severity,
        repair_safety=repair_safety,
        details={key: value for key, value in details.items() if value is not None},
    )


def _audit_saved_sheet(
    *,
    session: Session,
    order: Order,
    workflow: OrderWorkflowState | None,
    workflow_template_version_id: str | None,
) -> list[LineageIssue]:
    issues: list[LineageIssue] = []
    draft_id = _normalize_id(getattr(workflow, "draft_id", None))
    if not draft_id:
        return issues
    draft = session.get(OrderSheetDraft, draft_id)
    if draft is None:
        return [
            _issue(
                order_id=order.id,
                issue_type="saved_sheet_missing",
                saved_sheet_id=draft_id,
                repair_safety="blocked",
            )
        ]
    if draft.order_id != order.id:
        issues.append(
            _issue(
                order_id=order.id,
                issue_type="saved_sheet_order_mismatch",
                saved_sheet_id=draft.id,
                saved_sheet_order_id=draft.order_id,
            )
        )
    draft_template_version_id = _normalize_id(draft.template_version_id)
    if not draft_template_version_id:
        issues.append(
            _issue(
                order_id=order.id,
                issue_type="saved_sheet_template_version_missing",
                severity="warning",
                repair_safety="repair_candidate",
                saved_sheet_id=draft.id,
                workflow_template_version_id=workflow_template_version_id,
            )
        )
    elif workflow_template_version_id and draft_template_version_id != workflow_template_version_id:
        issues.append(
            _issue(
                order_id=order.id,
                issue_type="saved_sheet_template_version_mismatch",
                saved_sheet_id=draft.id,
                saved_sheet_template_version_id=draft_template_version_id,
                workflow_template_version_id=workflow_template_version_id,
            )
        )
    return issues


def _audit_evidence(
    *,
    session: Session,
    order: Order,
    workflow: OrderWorkflowState | None,
    workflow_template_version_id: str | None,
) -> list[LineageIssue]:
    evidence_id = _normalize_id(getattr(workflow, "evidence_run_id", None))
    if not evidence_id:
        return []
    evidence = session.get(OrderOcrEvidenceRun, evidence_id)
    if evidence is None:
        return [
            _issue(
                order_id=order.id,
                issue_type="selected_ocr_missing",
                selected_ocr_result_id=evidence_id,
            )
        ]
    issues: list[LineageIssue] = []
    if evidence.order_id != order.id:
        issues.append(
            _issue(
                order_id=order.id,
                issue_type="selected_ocr_order_mismatch",
                selected_ocr_result_id=evidence.id,
                selected_ocr_order_id=evidence.order_id,
            )
        )
    evidence_template_version_id = _normalize_id(evidence.template_version_id)
    if workflow_template_version_id and evidence_template_version_id != workflow_template_version_id:
        issues.append(
            _issue(
                order_id=order.id,
                issue_type="selected_ocr_template_version_mismatch",
                selected_ocr_result_id=evidence.id,
                selected_ocr_template_version_id=evidence_template_version_id,
                workflow_template_version_id=workflow_template_version_id,
            )
        )
    return issues


def _audit_confirmed_snapshot(
    *,
    session: Session,
    order: Order,
    workflow: OrderWorkflowState | None,
    meta: dict[str, Any],
    workflow_template_version_id: str | None,
) -> list[LineageIssue]:
    confirmed_snapshot_id = _confirmed_snapshot_id(workflow, meta)
    if not confirmed_snapshot_id:
        return [
            _issue(
                order_id=order.id,
                issue_type="confirmed_snapshot_id_missing",
                repair_safety="blocked",
            )
        ]
    snapshot = session.get(OrderConfirmedSnapshot, confirmed_snapshot_id)
    if snapshot is None:
        return [
            _issue(
                order_id=order.id,
                issue_type="confirmed_snapshot_row_missing",
                repair_safety="repair_candidate" if _has_repair_payload(meta) else "blocked",
                confirmed_snapshot_id=confirmed_snapshot_id,
            )
        ]
    issues: list[LineageIssue] = []
    if snapshot.order_id != order.id:
        issues.append(
            _issue(
                order_id=order.id,
                issue_type="confirmed_snapshot_order_mismatch",
                confirmed_snapshot_id=snapshot.id,
                snapshot_order_id=snapshot.order_id,
            )
        )
    snapshot_template_version_id = _normalize_id(snapshot.template_version_id)
    if workflow_template_version_id and snapshot_template_version_id != workflow_template_version_id:
        issues.append(
            _issue(
                order_id=order.id,
                issue_type="confirmed_snapshot_template_version_mismatch",
                confirmed_snapshot_id=snapshot.id,
                snapshot_template_version_id=snapshot_template_version_id,
                workflow_template_version_id=workflow_template_version_id,
            )
        )
    workflow_draft_id = _normalize_id(getattr(workflow, "draft_id", None))
    snapshot_draft_id = _normalize_id(snapshot.draft_id)
    if workflow_draft_id and snapshot_draft_id and workflow_draft_id != snapshot_draft_id:
        issues.append(
            _issue(
                order_id=order.id,
                issue_type="confirmed_snapshot_draft_mismatch",
                severity="warning",
                repair_safety="repair_candidate" if _has_repair_payload(meta) else "blocked",
                confirmed_snapshot_id=snapshot.id,
                workflow_saved_sheet_id=workflow_draft_id,
                snapshot_saved_sheet_id=snapshot_draft_id,
            )
        )
    return issues


def _has_repair_payload(meta: dict[str, Any]) -> bool:
    return bool(_payload(meta.get("bagging_result")) or _payload(meta.get("output_bundle")))


def _audit_bagging_and_output(
    *,
    session: Session,
    order: Order,
    workflow: OrderWorkflowState | None,
    meta: dict[str, Any],
    workflow_template_version_id: str | None,
) -> list[LineageIssue]:
    issues: list[LineageIssue] = []
    saved_sheet_id = _normalize_id(getattr(workflow, "draft_id", None))
    bagging_result = _bagging_payload(session, meta)
    output_bundle = _output_payload(session, meta)
    bagging_result_id = _id_from_payload_or_meta(meta, payload_key="bagging_result", id_key="bagging_result_id")
    output_bundle_id = _id_from_payload_or_meta(meta, payload_key="output_bundle", id_key="output_bundle_id")

    if bagging_result_id and bagging_result is None:
        issues.append(
            _issue(
                order_id=order.id,
                issue_type="bagging_result_payload_missing",
                severity="warning",
                repair_safety="blocked",
                bagging_result_id=bagging_result_id,
            )
        )
    if output_bundle_id and output_bundle is None:
        issues.append(
            _issue(
                order_id=order.id,
                issue_type="output_bundle_payload_missing",
                severity="warning",
                repair_safety="blocked",
                output_bundle_id=output_bundle_id,
            )
        )
    if bagging_result:
        bagging_template_version_id = _normalize_id(bagging_result.get("template_version_id"))
        if workflow_template_version_id and bagging_template_version_id != workflow_template_version_id:
            issues.append(
                _issue(
                    order_id=order.id,
                    issue_type="bagging_result_template_version_mismatch",
                    bagging_result_id=bagging_result_id,
                    bagging_template_version_id=bagging_template_version_id,
                    workflow_template_version_id=workflow_template_version_id,
                )
            )
        bagging_source_saved_sheet_id = _normalize_id(bagging_result.get("source_saved_sheet_id"))
        if saved_sheet_id and bagging_source_saved_sheet_id and bagging_source_saved_sheet_id != saved_sheet_id:
            issues.append(
                _issue(
                    order_id=order.id,
                    issue_type="bagging_result_source_saved_sheet_mismatch",
                    bagging_result_id=bagging_result_id,
                    bagging_source_saved_sheet_id=bagging_source_saved_sheet_id,
                    workflow_saved_sheet_id=saved_sheet_id,
                )
            )
    if output_bundle:
        output_template_version_id = _normalize_id(output_bundle.get("template_version_id"))
        if workflow_template_version_id and output_template_version_id != workflow_template_version_id:
            issues.append(
                _issue(
                    order_id=order.id,
                    issue_type="output_bundle_template_version_mismatch",
                    output_bundle_id=output_bundle_id,
                    output_template_version_id=output_template_version_id,
                    workflow_template_version_id=workflow_template_version_id,
                )
            )
        output_source_bagging_result_id = _normalize_id(output_bundle.get("source_bagging_result_id"))
        if bagging_result_id and output_source_bagging_result_id and output_source_bagging_result_id != bagging_result_id:
            issues.append(
                _issue(
                    order_id=order.id,
                    issue_type="output_bundle_source_bagging_mismatch",
                    output_bundle_id=output_bundle_id,
                    output_source_bagging_result_id=output_source_bagging_result_id,
                    bagging_result_id=bagging_result_id,
                )
            )
        output_source_saved_sheet_id = _normalize_id(output_bundle.get("source_saved_sheet_id"))
        if saved_sheet_id and output_source_saved_sheet_id and output_source_saved_sheet_id != saved_sheet_id:
            issues.append(
                _issue(
                    order_id=order.id,
                    issue_type="output_bundle_source_saved_sheet_mismatch",
                    output_bundle_id=output_bundle_id,
                    output_source_saved_sheet_id=output_source_saved_sheet_id,
                    workflow_saved_sheet_id=saved_sheet_id,
                )
            )
    return issues


def _audit_order(session: Session, order: Order, workflow: OrderWorkflowState | None) -> list[LineageIssue]:
    meta = _workflow_meta(workflow)
    workflow_template_version_id = _normalize_id(getattr(workflow, "template_version_id", None)) or _normalize_id(
        meta.get("template_version_id")
    )
    issues: list[LineageIssue] = []
    issues.extend(
        _audit_evidence(
            session=session,
            order=order,
            workflow=workflow,
            workflow_template_version_id=workflow_template_version_id,
        )
    )
    issues.extend(
        _audit_saved_sheet(
            session=session,
            order=order,
            workflow=workflow,
            workflow_template_version_id=workflow_template_version_id,
        )
    )
    issues.extend(
        _audit_bagging_and_output(
            session=session,
            order=order,
            workflow=workflow,
            meta=meta,
            workflow_template_version_id=workflow_template_version_id,
        )
    )
    if _is_confirmed_like(order, workflow, meta):
        issues.extend(
            _audit_confirmed_snapshot(
                session=session,
                order=order,
                workflow=workflow,
                meta=meta,
                workflow_template_version_id=workflow_template_version_id,
            )
        )
    return issues


def audit_order_pipeline_lineage(*, order_id: str | None = None, limit: int | None = None) -> dict[str, Any]:
    with session_scope() as session:
        query = session.query(Order).order_by(Order.received_at.desc(), Order.id.desc())
        if order_id:
            query = query.filter(Order.id == order_id)
        if limit is not None and limit > 0:
            query = query.limit(limit)
        orders = query.all()
        workflow_by_order_id = {
            row.order_id: row
            for row in session.query(OrderWorkflowState)
            .filter(OrderWorkflowState.order_id.in_([order.id for order in orders]))
            .all()
        }
        issues: list[LineageIssue] = []
        for order in orders:
            issues.extend(_audit_order(session, order, workflow_by_order_id.get(order.id)))

    issue_dicts = [issue.to_dict() for issue in issues]
    counts_by_type: dict[str, int] = {}
    counts_by_repair_safety: dict[str, int] = {}
    for issue in issues:
        counts_by_type[issue.issue_type] = counts_by_type.get(issue.issue_type, 0) + 1
        counts_by_repair_safety[issue.repair_safety] = counts_by_repair_safety.get(issue.repair_safety, 0) + 1
    return {
        "mode": "read_only",
        "order_count": len(orders),
        "issue_count": len(issue_dicts),
        "counts_by_type": dict(sorted(counts_by_type.items())),
        "counts_by_repair_safety": dict(sorted(counts_by_repair_safety.items())),
        "issues": issue_dicts,
    }
