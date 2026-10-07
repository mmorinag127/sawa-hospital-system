from __future__ import annotations

from datetime import datetime
from contextlib import nullcontext
from typing import Any
from uuid import uuid4
from sqlalchemy import select

from src.db import session_scope
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_sheet_patch_candidate import OrderSheetPatchCandidate
from src.services import draft_sheet_service

def _serialize_candidate(row: OrderSheetPatchCandidate) -> dict[str, Any]:
    return {
        "id": row.id,
        "order_id": row.order_id,
        "base_draft_id": row.base_draft_id,
        "base_evidence_run_id": row.base_evidence_run_id,
        "candidate_state": str(row.candidate_state or "ready").strip() or "ready",
        "provider": row.provider,
        "model": row.model,
        "prompt_preset": row.prompt_preset,
        "baseline_source": row.baseline_source,
        "baseline_revision_id": row.baseline_revision_id,
        "summary_json": row.summary_json if isinstance(row.summary_json, dict) else {},
        "issues_json": list(row.issues_json or []),
        "patches_json": row.patches_json if isinstance(row.patches_json, dict) else {},
        "proposed_draft_sheet_json": (
            row.proposed_draft_sheet_json if isinstance(row.proposed_draft_sheet_json, dict) else {}
        ),
        "applied_by": row.applied_by,
        "applied_at": row.applied_at.isoformat() if isinstance(row.applied_at, datetime) else None,
        "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
    }


def _normalize_patch_payload(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"applied_overwrites": [], "rejected_overwrites": []}
    return {
        "applied_overwrites": list(value.get("applied_overwrites") or []),
        "rejected_overwrites": list(value.get("rejected_overwrites") or []),
    }


def persist_patch_candidate(
    *,
    order_id: str,
    base_draft_id: str | None = None,
    base_evidence_run_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    prompt_preset: str | None = None,
    baseline_source: str | None = None,
    baseline_revision_id: str | None = None,
    candidate_state: str = "ready",
    summary_json: dict[str, Any] | None = None,
    issues_json: list[dict[str, Any]] | None = None,
    patches_json: dict[str, Any] | None = None,
    proposed_draft_sheet_json: dict[str, Any] | None = None,
    synchronous_write_input: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    now = datetime.utcnow()
    from src.services import order_service

    if not isinstance(synchronous_write_input, dict):
        return None
    if base_evidence_run_id != synchronous_write_input.get("evidence_run_id") or base_draft_id != synchronous_write_input.get("draft_id"):
        return None
    with session_scope() as session:
        if not order_service._synchronous_cache_write_fence(session, normalized_order_id, synchronous_write_input):
            return None
        row = OrderSheetPatchCandidate(
            id=f"OPC{uuid4().hex[:12]}",
            order_id=normalized_order_id,
            draft_id=str(base_draft_id or "").strip() or None,
            source="ocr_review",
            patch_scope="sheet",
            status="applied" if str(candidate_state or "").strip() == "applied" else "pending",
            confidence_score=None,
            patch_json=_normalize_patch_payload(patches_json),
            apply_plan_json=None,
            apply_ready_metadata_json=None,
            blockers_json=[],
            warnings_json=[],
            created_by=str(provider or "").strip() or None,
            reviewed_by=None,
            reviewed_at=None,
            updated_at=now,
            base_draft_id=str(base_draft_id or "").strip() or None,
            base_evidence_run_id=str(base_evidence_run_id or "").strip() or None,
            candidate_state=str(candidate_state or "ready").strip() or "ready",
            provider=str(provider or "").strip() or None,
            model=str(model or "").strip() or None,
            prompt_preset=str(prompt_preset or "").strip() or None,
            baseline_source=str(baseline_source or "").strip() or None,
            baseline_revision_id=str(baseline_revision_id or "").strip() or None,
            summary_json=summary_json if isinstance(summary_json, dict) else {},
            issues_json=list(issues_json or []),
            patches_json=_normalize_patch_payload(patches_json),
            proposed_draft_sheet_json=(
                proposed_draft_sheet_json if isinstance(proposed_draft_sheet_json, dict) else {}
            ),
            created_at=now,
        )
        session.add(row)
        session.flush()
        serialized = _serialize_candidate(row)
        if base_draft_id:
            draft = session.get(OrderSheetDraft, base_draft_id)
            if draft is None or draft.order_id != normalized_order_id or draft.base_evidence_run_id != base_evidence_run_id:
                raise ValueError("patch_candidate_input_lineage_mismatch")
            draft.latest_patch_candidate_id = row.id
            session.flush()
    return serialized


def get_patch_candidate(order_id: str, patch_candidate_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    normalized_candidate_id = str(patch_candidate_id or "").strip()
    if not normalized_order_id or not normalized_candidate_id:
        return None
    with session_scope() as session:
        row = (
            session.query(OrderSheetPatchCandidate)
            .filter(
                OrderSheetPatchCandidate.order_id == normalized_order_id,
                OrderSheetPatchCandidate.id == normalized_candidate_id,
            )
            .first()
        )
        return _serialize_candidate(row) if row else None


def get_latest_patch_candidate(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        row = (
            session.query(OrderSheetPatchCandidate)
            .filter(OrderSheetPatchCandidate.order_id == normalized_order_id)
            .order_by(OrderSheetPatchCandidate.created_at.desc(), OrderSheetPatchCandidate.id.desc())
            .first()
        )
        return _serialize_candidate(row) if row else None


def list_patch_candidates(order_id: str) -> list[dict[str, Any]]:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return []
    with session_scope() as session:
        rows = (
            session.query(OrderSheetPatchCandidate)
            .filter(OrderSheetPatchCandidate.order_id == normalized_order_id)
            .order_by(OrderSheetPatchCandidate.created_at.desc(), OrderSheetPatchCandidate.id.desc())
            .all()
        )
        return [_serialize_candidate(row) for row in rows]


def mark_patch_candidate_applied(
    order_id: str,
    patch_candidate_id: str,
    *,
    applied_by: str | None = None,
    write_session: Any = None,
    synchronous_write_input: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    normalized_candidate_id = str(patch_candidate_id or "").strip()
    if not normalized_order_id or not normalized_candidate_id:
        return None
    if write_session is None:
        return None
    from src.services import order_service

    if not isinstance(synchronous_write_input, dict):
        return None
    with nullcontext(write_session) as session:
        if not order_service._synchronous_cache_write_fence(session, normalized_order_id, synchronous_write_input):
            return None
        row = (
            session.query(OrderSheetPatchCandidate)
            .filter(
                OrderSheetPatchCandidate.order_id == normalized_order_id,
                OrderSheetPatchCandidate.id == normalized_candidate_id,
            )
            .first()
        )
        if row is None:
            return None
        if row.base_evidence_run_id != synchronous_write_input.get("evidence_run_id") or row.base_draft_id != synchronous_write_input.get("draft_id"):
            return None
        row.candidate_state = "applied"
        row.status = "applied"
        row.applied_by = str(applied_by or "").strip() or None
        row.applied_at = datetime.utcnow()
        row.updated_at = row.applied_at
        session.flush()
        return _serialize_candidate(row)


def apply_patch_candidate_to_draft(
    order_id: str,
    *,
    patch_candidate_id: str | None = None,
    expected_draft_id: str | None = None,
    edited_by: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None, "order_not_found"
    from src.services import order_service

    synchronous_write_input = order_service._capture_synchronous_cache_write_input(normalized_order_id)
    if synchronous_write_input is None:
        return None, "synchronous_input_missing"
    selected_candidate_id = str(patch_candidate_id or "").strip()
    if not selected_candidate_id:
        return None, "patch_candidate_id_required"
    candidate = get_patch_candidate(normalized_order_id, selected_candidate_id)
    if not isinstance(candidate, dict):
        return None, "patch_candidate_not_found"
    proposed_draft = candidate.get("proposed_draft_sheet_json")
    if not isinstance(proposed_draft, dict) or not isinstance(proposed_draft.get("rows"), list):
        return None, "patch_candidate_not_applicable"
    latest_draft = synchronous_write_input.get("draft")
    latest_draft_id = str((latest_draft or {}).get("id") or "").strip() or None
    expected_id = str(expected_draft_id or "").strip() or None
    if expected_id and latest_draft_id and expected_id != latest_draft_id:
        return None, "stale_draft_conflict"
    base_draft_id = str(candidate.get("base_draft_id") or "").strip() or None
    if base_draft_id and latest_draft_id and base_draft_id != latest_draft_id:
        return None, "stale_patch_candidate"
    if candidate.get("base_evidence_run_id") != synchronous_write_input["evidence_run_id"]:
        return None, "stale_patch_candidate"
    with session_scope() as write_session:
        if not order_service._synchronous_cache_write_fence(write_session, normalized_order_id, synchronous_write_input):
            return None, "synchronous_input_conflict"
        candidate_query = select(OrderSheetPatchCandidate).where(OrderSheetPatchCandidate.id == selected_candidate_id)
        if write_session.bind.dialect.name != "sqlite":
            candidate_query = candidate_query.with_for_update()
        actual_candidate = write_session.execute(candidate_query.execution_options(populate_existing=True)).scalar_one_or_none()
        if actual_candidate is None or _serialize_candidate(actual_candidate) != candidate:
            return None, "stale_patch_candidate"
        return _adopt_patch_candidate(
            normalized_order_id, candidate=candidate, proposed_draft=proposed_draft,
            synchronous_write_input=synchronous_write_input, edited_by=edited_by,
            write_session=write_session,
        )


def _adopt_patch_candidate(
    normalized_order_id: str, *, candidate: dict[str, Any], proposed_draft: dict[str, Any],
    synchronous_write_input: dict[str, Any], edited_by: str | None, write_session: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    persisted = draft_sheet_service.persist_sheet_draft(
        order_id=normalized_order_id,
        draft_sheet_json=proposed_draft,
        base_evidence_run_id=str(candidate.get("base_evidence_run_id") or "").strip() or None,
        template_version_id=synchronous_write_input["evidence"].get("template_version_id"),
        base_template_resolution_id=None,
        base_menu_snapshot_id=None,
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
        latest_patch_candidate_id=str(candidate.get("id") or "").strip() or None,
        edited_by=str(edited_by or "").strip() or "llm_patch_candidate",
        synchronous_write_input=synchronous_write_input,
        write_session=write_session,
    )
    if not isinstance(persisted, dict):
        write_session.rollback()
        return None, "draft_persist_failed"
    applied_candidate = mark_patch_candidate_applied(
        normalized_order_id,
        str(candidate.get("id") or ""),
        applied_by=edited_by or "llm_patch_candidate",
        write_session=write_session,
        synchronous_write_input=synchronous_write_input,
    )
    if applied_candidate is None:
        write_session.rollback()
        return None, "patch_candidate_adoption_conflict"
    return {
        "candidate": applied_candidate,
        "draft": persisted,
    }, None
