from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from src.db import session_scope
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.user import AuditLog
from src.services import order_output_artifact_service


WORKFLOW_V2_META_KEY = "workflow_v2"
APPLY_CONFIRMATION_TOKEN = "REPAIR_ORDER_PIPELINE_LINEAGE"


@dataclass(frozen=True)
class RepairAction:
    action_type: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "details": self.details,
        }


@dataclass(frozen=True)
class RepairPlan:
    order_id: str
    status: str
    reason: str | None
    before_digest: str | None
    after_digest: str | None
    actions: list[RepairAction]

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "status": self.status,
            "reason": self.reason,
            "before_digest": self.before_digest,
            "after_digest": self.after_digest,
            "actions": [action.to_dict() for action in self.actions],
        }


def repair_confirmed_snapshot_payloads(
    *,
    order_id: str,
    apply: bool = False,
    confirm: str | None = None,
    actor: str | None = None,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    normalized_order_id = _normalize_id(order_id)
    if not normalized_order_id:
        return _result(order_id=order_id, mode=apply, status="blocked", reason="order_id_required")
    if apply and confirm != APPLY_CONFIRMATION_TOKEN:
        return _result(
            order_id=normalized_order_id,
            mode=apply,
            status="blocked",
            reason="apply_confirmation_required",
        )

    with session_scope() as session:
        workflow = session.get(OrderWorkflowState, normalized_order_id)
        plan, next_secondary_actions = _plan_confirmed_snapshot_payload_repair(
            session=session,
            order_id=normalized_order_id,
            workflow=workflow,
        )
        if not apply or plan.status != "repairable":
            return _result_from_plan(plan, apply=apply, applied=False)
        if next_secondary_actions is None:
            return _result_from_plan(
                RepairPlan(
                    order_id=normalized_order_id,
                    status="blocked",
                    reason="repair_payload_missing",
                    before_digest=plan.before_digest,
                    after_digest=plan.after_digest,
                    actions=plan.actions,
                ),
                apply=apply,
                applied=False,
            )
        assert workflow is not None
        workflow.secondary_actions_json = next_secondary_actions
        repair_record = _repair_record(
            plan=plan,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        session.add(
            AuditLog(
                id=f"AUD{int(datetime.utcnow().timestamp())}{uuid4().hex[:6]}",
                actor=repair_record["actor"],
                action="order_pipeline_lineage_repair",
                target=normalized_order_id,
                fac=None,
                wek=None,
                metadata_json=repair_record,
                created_at=datetime.utcnow(),
            )
        )
        session.flush()
        return _result_from_plan(plan, apply=apply, applied=True, repair_record=repair_record)


def backfill_step4_output_artifacts(
    *,
    order_id: str | None = None,
    limit: int | None = None,
    apply: bool = False,
    confirm: str | None = None,
    actor: str | None = None,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if apply and confirm != APPLY_CONFIRMATION_TOKEN:
        return {
            "mode": "apply",
            "applied": False,
            "status": "blocked",
            "reason": "apply_confirmation_required",
            "plans": [],
            "summary": {},
        }
    normalized_order_id = _normalize_id(order_id)
    with session_scope() as session:
        query = session.query(OrderWorkflowState).order_by(OrderWorkflowState.order_id)
        if normalized_order_id:
            query = query.filter(OrderWorkflowState.order_id == normalized_order_id)
        if limit is not None:
            query = query.limit(max(int(limit), 0))
        plans = [_plan_step4_artifact_backfill(session=session, workflow=workflow) for workflow in query.all()]
        repairable_plans = [plan for plan in plans if plan.status == "repairable"]
        repair_records: list[dict[str, Any]] = []
        if apply:
            for plan in repairable_plans:
                workflow = session.get(OrderWorkflowState, plan.order_id)
                if workflow is None:
                    continue
                meta = _workflow_meta_from_secondary(_secondary_actions(workflow))
                bagging_result = _payload(meta.get("bagging_result"))
                output_bundle = _payload(meta.get("output_bundle"))
                if bagging_result is not None:
                    meta["bagging_result"] = None
                if output_bundle is not None:
                    meta["output_bundle"] = None
                workflow.secondary_actions_json = {
                    **_secondary_actions(workflow),
                    WORKFLOW_V2_META_KEY: meta,
                }
                record = _repair_record(
                    plan=plan,
                    actor=actor,
                    reason=reason or "step4_artifact_backfill",
                    idempotency_key=idempotency_key,
                )
                session.add(
                    AuditLog(
                        id=f"AUD{int(datetime.utcnow().timestamp())}{uuid4().hex[:6]}",
                        actor=record["actor"],
                        action="order_pipeline_lineage_repair",
                        target=plan.order_id,
                        fac=None,
                        wek=None,
                        metadata_json=record,
                        created_at=datetime.utcnow(),
                    )
                )
                repair_records.append(record)
            session.flush()
        return {
            "mode": "apply" if apply else "dry_run",
            "applied": bool(apply and repair_records),
            "status": "ok",
            "reason": None,
            "summary": _summarize_plans(plans),
            "plans": [plan.to_dict() for plan in plans],
            "repair_records": repair_records,
        }


def repair_confirmed_workflow_v2_lineage(
    *,
    order_id: str | None = None,
    limit: int | None = None,
    apply: bool = False,
    confirm: str | None = None,
    actor: str | None = None,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if apply and confirm != APPLY_CONFIRMATION_TOKEN:
        return {
            "mode": "apply",
            "applied": False,
            "status": "blocked",
            "reason": "apply_confirmation_required",
            "plans": [],
            "summary": {},
        }
    normalized_order_id = _normalize_id(order_id)
    with session_scope() as session:
        query = session.query(OrderWorkflowState).order_by(OrderWorkflowState.order_id)
        if normalized_order_id:
            query = query.filter(OrderWorkflowState.order_id == normalized_order_id)
        if limit is not None:
            query = query.limit(max(int(limit), 0))
        plans_and_updates = [
            _plan_confirmed_workflow_v2_lineage_repair(session=session, workflow=workflow)
            for workflow in query.all()
        ]
        plans = [item[0] for item in plans_and_updates]
        repair_records: list[dict[str, Any]] = []
        if apply:
            for plan, update_payload in plans_and_updates:
                if plan.status != "repairable" or update_payload is None:
                    continue
                workflow = session.get(OrderWorkflowState, plan.order_id)
                snapshot = session.get(OrderConfirmedSnapshot, update_payload["confirmed_snapshot_id"])
                draft = session.get(OrderSheetDraft, update_payload["saved_sheet_id"])
                if workflow is None or snapshot is None or draft is None:
                    continue
                workflow.draft_id = update_payload["saved_sheet_id"]
                workflow.template_version_id = update_payload["template_version_id"]
                workflow.confirmed_snapshot_id = update_payload["confirmed_snapshot_id"]
                workflow.state = "confirmed"
                workflow.headline = "注文が確定されました"
                workflow.primary_action = None
                workflow.secondary_actions_json = update_payload["secondary_actions_json"]
                workflow.blockers_json = []
                workflow.warnings_json = []
                workflow.last_transition_at = datetime.utcnow()
                draft.template_version_id = update_payload["template_version_id"]
                record = _repair_record(
                    plan=plan,
                    actor=actor,
                    reason=reason or "confirmed_workflow_v2_lineage_repair",
                    idempotency_key=idempotency_key,
                )
                session.add(
                    AuditLog(
                        id=f"AUD{int(datetime.utcnow().timestamp())}{uuid4().hex[:6]}",
                        actor=record["actor"],
                        action="order_pipeline_lineage_repair",
                        target=plan.order_id,
                        fac=None,
                        wek=None,
                        metadata_json=record,
                        created_at=datetime.utcnow(),
                    )
                )
                repair_records.append(record)
            session.flush()
        return {
            "mode": "apply" if apply else "dry_run",
            "applied": bool(apply and repair_records),
            "status": "ok",
            "reason": None,
            "summary": _summarize_plans(plans),
            "plans": [plan.to_dict() for plan in plans],
            "repair_records": repair_records,
        }


def _plan_confirmed_workflow_v2_lineage_repair(
    *,
    session: Session,
    workflow: OrderWorkflowState,
) -> tuple[RepairPlan, dict[str, Any] | None]:
    before = {
        "draft_id": workflow.draft_id,
        "template_version_id": workflow.template_version_id,
        "confirmed_snapshot_id": workflow.confirmed_snapshot_id,
        "secondary_actions_json": workflow.secondary_actions_json,
    }
    before_digest = _digest(before)
    confirmed_snapshot_id = _normalize_id(workflow.confirmed_snapshot_id)
    if not confirmed_snapshot_id:
        return RepairPlan(workflow.order_id, "no_op", None, before_digest, before_digest, []), None
    snapshot = session.get(OrderConfirmedSnapshot, confirmed_snapshot_id)
    if snapshot is None or snapshot.order_id != workflow.order_id:
        return RepairPlan(workflow.order_id, "blocked", "confirmed_snapshot_missing_or_mismatch", before_digest, None, []), None
    if not isinstance(snapshot.snapshot_json, dict):
        return RepairPlan(workflow.order_id, "blocked", "confirmed_snapshot_payload_invalid", before_digest, None, []), None
    snapshot_json = snapshot.snapshot_json
    if _normalize_id(snapshot_json.get("source")) != "workflow_v2":
        return RepairPlan(workflow.order_id, "no_op", None, before_digest, before_digest, []), None
    canonical = _snapshot_artifacts(session, snapshot, workflow.order_id)
    if canonical is None:
        return RepairPlan(workflow.order_id, "blocked", "confirmed_snapshot_artifact_mismatch", before_digest, None, []), None
    bagging_payload, output_payload = canonical
    saved_sheet_id = _normalize_id(snapshot.saved_sheet_id)
    template_version_id = _normalize_id(snapshot.template_version_id)
    if not saved_sheet_id or not template_version_id:
        return RepairPlan(workflow.order_id, "blocked", "snapshot_saved_sheet_or_template_missing", before_digest, None, []), None
    draft = session.get(OrderSheetDraft, saved_sheet_id)
    if draft is None or draft.order_id != workflow.order_id:
        return RepairPlan(workflow.order_id, "blocked", "snapshot_saved_sheet_missing_or_mismatch", before_digest, None, []), None
    bagging_result_id = snapshot.bagging_result_id
    output_bundle_id = snapshot.output_bundle_id
    current_meta = _workflow_meta_from_secondary(_secondary_actions(workflow))
    for key, artifact_id in (("bagging_result_id", bagging_result_id), ("output_bundle_id", output_bundle_id)):
        current_id = _normalize_id(current_meta.get(key))
        if current_id is not None and current_id != artifact_id:
            return RepairPlan(workflow.order_id, "blocked", "workflow_artifact_identity_mismatch", before_digest, None, []), None
    next_meta = {
        "template_version_id": template_version_id,
        "confirmed_snapshot_id": confirmed_snapshot_id,
        "saved_sheet_id": saved_sheet_id,
        "bagging_result_id": bagging_result_id,
        "output_bundle_id": output_bundle_id,
    }
    next_meta["bagging_result"] = None
    next_meta["output_bundle"] = None
    next_secondary = {WORKFLOW_V2_META_KEY: next_meta}
    update_payload = {
        "confirmed_snapshot_id": confirmed_snapshot_id,
        "saved_sheet_id": saved_sheet_id,
        "template_version_id": template_version_id,
        "bagging_result_id": bagging_result_id,
        "output_bundle_id": output_bundle_id,
        "secondary_actions_json": next_secondary,
    }
    after = {
        "draft_id": saved_sheet_id,
        "template_version_id": template_version_id,
        "confirmed_snapshot_id": confirmed_snapshot_id,
        "secondary_actions_json": next_secondary,
    }
    actions: list[RepairAction] = []
    if _normalize_id(workflow.draft_id) != saved_sheet_id:
        actions.append(RepairAction("restore_workflow_saved_sheet_id", {"saved_sheet_id": saved_sheet_id}))
    if _normalize_id(workflow.template_version_id) != template_version_id:
        actions.append(RepairAction("restore_workflow_template_version_id", {"template_version_id": template_version_id}))
    if _normalize_id(draft.template_version_id) != template_version_id:
        actions.append(RepairAction("restore_saved_sheet_template_version_id", {"saved_sheet_id": saved_sheet_id, "template_version_id": template_version_id}))
    if not isinstance(workflow.secondary_actions_json, dict) or WORKFLOW_V2_META_KEY not in workflow.secondary_actions_json:
        actions.append(RepairAction("restore_workflow_v2_meta", {"confirmed_snapshot_id": confirmed_snapshot_id}))
    if not actions:
        return RepairPlan(workflow.order_id, "no_op", None, before_digest, before_digest, []), None
    return RepairPlan(workflow.order_id, "repairable", None, before_digest, _digest(after), actions), update_payload


def _plan_step4_artifact_backfill(*, session: Session, workflow: OrderWorkflowState) -> RepairPlan:
    meta = _workflow_meta_from_secondary(_secondary_actions(workflow))
    before_digest = _digest(meta)
    actions: list[RepairAction] = []
    blocked_actions: list[RepairAction] = []
    for payload_key, id_key, reader in (
        ("bagging_result", "bagging_result_id", order_output_artifact_service.load_bagging_result_payload),
        ("output_bundle", "output_bundle_id", order_output_artifact_service.load_output_bundle_payload),
    ):
        if meta.get(payload_key) is not None and not isinstance(meta[payload_key], dict):
            blocked_actions.append(RepairAction("manual_review_required", {"reason": f"{payload_key}_payload_invalid"}))
        if meta.get(id_key) is not None or meta.get(payload_key) is not None:
            if reader(session, _normalize_id(meta.get(id_key)), expected_order_id=workflow.order_id) is None:
                blocked_actions.append(RepairAction("manual_review_required", {"reason": f"{payload_key}_artifact_missing_or_invalid"}))
    bagging_result = _payload(meta.get("bagging_result"))
    if bagging_result is not None:
        bagging_result_id = _normalize_id(meta.get("bagging_result_id"))
        if not bagging_result_id:
            blocked_actions.append(RepairAction("manual_review_required", {"reason": "bagging_result_id_missing"}))
        elif order_output_artifact_service.load_bagging_result_payload(session, bagging_result_id, expected_order_id=workflow.order_id) != bagging_result:
            blocked_actions.append(RepairAction("manual_review_required", {"reason": "bagging_result_artifact_missing_or_mismatch"}))
        actions.append(RepairAction("clear_workflow_bagging_payload", {"bagging_result_id": bagging_result_id}))
    output_bundle = _payload(meta.get("output_bundle"))
    if output_bundle is not None:
        output_bundle_id = _normalize_id(meta.get("output_bundle_id"))
        if not output_bundle_id:
            blocked_actions.append(RepairAction("manual_review_required", {"reason": "output_bundle_id_missing"}))
        elif order_output_artifact_service.load_output_bundle_payload(session, output_bundle_id, expected_order_id=workflow.order_id) != output_bundle:
            blocked_actions.append(RepairAction("manual_review_required", {"reason": "output_bundle_artifact_missing_or_mismatch"}))
        actions.append(RepairAction("clear_workflow_output_payload", {"output_bundle_id": output_bundle_id}))
    if blocked_actions:
        return RepairPlan(workflow.order_id, "blocked", "artifact_missing_or_mismatch", before_digest, None, blocked_actions)
    if not actions:
        return RepairPlan(workflow.order_id, "no_op", None, before_digest, before_digest, [])
    next_meta = deepcopy(meta)
    if bagging_result is not None:
        next_meta["bagging_result"] = None
    if output_bundle is not None:
        next_meta["output_bundle"] = None
    return RepairPlan(workflow.order_id, "repairable", None, before_digest, _digest(next_meta), actions)


def _plan_confirmed_snapshot_payload_repair(
    *,
    session: Session,
    order_id: str,
    workflow: OrderWorkflowState | None,
) -> tuple[RepairPlan, dict[str, Any] | None]:
    if workflow is None:
        return _blocked(order_id, "workflow_not_found"), None

    current_secondary = _secondary_actions(workflow)
    current_meta = _workflow_meta_from_secondary(current_secondary)
    before_digest = _digest(current_meta)
    confirmed_snapshot_id = _confirmed_snapshot_id(workflow, current_meta)
    if not confirmed_snapshot_id:
        return _blocked(order_id, "confirmed_snapshot_id_missing", before_digest=before_digest), None

    snapshot = session.get(OrderConfirmedSnapshot, confirmed_snapshot_id)
    if snapshot is None:
        return _blocked(
            order_id,
            "confirmed_snapshot_row_missing",
            before_digest=before_digest,
            actions=[
                RepairAction(
                    action_type="manual_review_required",
                    details={"confirmed_snapshot_id": confirmed_snapshot_id},
                )
            ],
        ), None
    if snapshot.order_id != order_id:
        return _blocked(
            order_id,
            "confirmed_snapshot_order_mismatch",
            before_digest=before_digest,
            actions=[
                RepairAction(
                    action_type="manual_review_required",
                    details={
                        "confirmed_snapshot_id": confirmed_snapshot_id,
                        "snapshot_order_id": snapshot.order_id,
                    },
                )
            ],
        ), None

    canonical = _snapshot_artifacts(session, snapshot, order_id)
    if canonical is None:
        return _blocked(
            order_id,
            "confirmed_snapshot_artifact_mismatch",
            before_digest=before_digest,
            actions=[
                RepairAction(
                    action_type="manual_review_required",
                    details={"confirmed_snapshot_id": confirmed_snapshot_id},
                )
            ],
        ), None

    snapshot_bagging, snapshot_output = canonical
    next_meta = deepcopy(current_meta)
    actions: list[RepairAction] = []
    for payload_key, id_key, payload in (("bagging_result", "bagging_result_id", snapshot_bagging), ("output_bundle", "output_bundle_id", snapshot_output)):
        current_id = _normalize_id(next_meta.get(id_key))
        if current_id is not None and current_id != payload[id_key]:
            return _blocked(order_id, "workflow_artifact_identity_mismatch", before_digest=before_digest), None
        if current_id is None:
            next_meta[id_key] = payload[id_key]
            actions.append(RepairAction(f"restore_{id_key}", {id_key: payload[id_key]}))
        if next_meta.get(payload_key) is not None:
            next_meta[payload_key] = None
            actions.append(RepairAction(f"clear_workflow_{payload_key}", {id_key: payload[id_key]}))
    if snapshot_output is not None:
        output_confirmed_snapshot_id = _normalize_id(snapshot_output.get("confirmed_snapshot_id"))
        if output_confirmed_snapshot_id and not _normalize_id(next_meta.get("confirmed_snapshot_id")):
            next_meta["confirmed_snapshot_id"] = output_confirmed_snapshot_id
            actions.append(
                RepairAction(
                    action_type="restore_confirmed_snapshot_id",
                    details={"confirmed_snapshot_id": output_confirmed_snapshot_id},
                )
            )
    if not actions:
        return RepairPlan(order_id, "no_op", None, before_digest, before_digest, []), None

    next_secondary = deepcopy(current_secondary)
    next_secondary[WORKFLOW_V2_META_KEY] = next_meta
    return (
        RepairPlan(
            order_id=order_id,
            status="repairable",
            reason=None,
            before_digest=before_digest,
            after_digest=_digest(next_meta),
            actions=actions,
        ),
        next_secondary,
    )


def _snapshot_artifacts(session: Session, snapshot: OrderConfirmedSnapshot, order_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    return order_output_artifact_service.confirmed_snapshot_artifact_payloads(session, snapshot, expected_order_id=order_id)


def _secondary_actions(workflow: OrderWorkflowState) -> dict[str, Any]:
    return deepcopy(workflow.secondary_actions_json) if isinstance(workflow.secondary_actions_json, dict) else {}


def _workflow_meta_from_secondary(secondary_actions: dict[str, Any]) -> dict[str, Any]:
    meta = secondary_actions.get(WORKFLOW_V2_META_KEY)
    return deepcopy(meta) if isinstance(meta, dict) else {}


def _confirmed_snapshot_id(workflow: OrderWorkflowState, meta: dict[str, Any]) -> str | None:
    return (
        _normalize_id(workflow.confirmed_snapshot_id)
        or _normalize_id(meta.get("confirmed_snapshot_id"))
    )


def _payload(value: Any) -> dict[str, Any] | None:
    return deepcopy(value) if isinstance(value, dict) else None


def _normalize_id(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _blocked(
    order_id: str,
    reason: str,
    *,
    before_digest: str | None = None,
    actions: list[RepairAction] | None = None,
) -> RepairPlan:
    return RepairPlan(
        order_id=order_id,
        status="blocked",
        reason=reason,
        before_digest=before_digest,
        after_digest=None,
        actions=actions or [],
    )


def _result(*, order_id: str, mode: bool, status: str, reason: str) -> dict[str, Any]:
    return {
        "mode": "apply" if mode else "dry_run",
        "applied": False,
        "plan": {
            "order_id": order_id,
            "status": status,
            "reason": reason,
            "before_digest": None,
            "after_digest": None,
            "actions": [],
        },
    }


def _repair_record(
    *,
    plan: RepairPlan,
    actor: str | None,
    reason: str | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    return {
        "actor": _normalize_id(actor) or "system",
        "reason": _normalize_id(reason) or "legacy_confirmed_lineage_repair",
        "idempotency_key": _normalize_id(idempotency_key) or _default_idempotency_key(plan),
        "before_digest": plan.before_digest,
        "after_digest": plan.after_digest,
        "affected_artifact_ids": _affected_artifact_ids(plan.actions),
        "skipped_rows": [],
        "blocked_rows": [],
        "actions": [action.to_dict() for action in plan.actions],
    }


def _default_idempotency_key(plan: RepairPlan) -> str:
    return f"{plan.order_id}:{plan.before_digest}:{plan.after_digest}"


def _affected_artifact_ids(actions: list[RepairAction]) -> dict[str, list[str]]:
    affected: dict[str, set[str]] = {
        "bagging_result_ids": set(),
        "output_bundle_ids": set(),
        "confirmed_snapshot_ids": set(),
    }
    for action in actions:
        bagging_result_id = _normalize_id(action.details.get("bagging_result_id"))
        if bagging_result_id:
            affected["bagging_result_ids"].add(bagging_result_id)
        output_bundle_id = _normalize_id(action.details.get("output_bundle_id"))
        if output_bundle_id:
            affected["output_bundle_ids"].add(output_bundle_id)
        confirmed_snapshot_id = _normalize_id(action.details.get("confirmed_snapshot_id"))
        if confirmed_snapshot_id:
            affected["confirmed_snapshot_ids"].add(confirmed_snapshot_id)
    return {key: sorted(values) for key, values in affected.items()}


def _summarize_plans(plans: list[RepairPlan]) -> dict[str, Any]:
    counts_by_status: dict[str, int] = {}
    counts_by_action: dict[str, int] = {}
    for plan in plans:
        counts_by_status[plan.status] = counts_by_status.get(plan.status, 0) + 1
        for action in plan.actions:
            counts_by_action[action.action_type] = counts_by_action.get(action.action_type, 0) + 1
    return {
        "order_count": len(plans),
        "counts_by_status": counts_by_status,
        "counts_by_action": counts_by_action,
    }


def _result_from_plan(
    plan: RepairPlan,
    *,
    apply: bool,
    applied: bool,
    repair_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "mode": "apply" if apply else "dry_run",
        "applied": applied,
        "plan": plan.to_dict(),
        "repair_record": repair_record,
    }
