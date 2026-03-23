from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from src.db import Base, engine, session_scope
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_sheet_patch_candidate import OrderSheetPatchCandidate
from src.services import draft_sheet_service


Base.metadata.create_all(bind=engine)


def _ensure_order_sheet_patch_candidate_schema() -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(order_sheet_patch_candidates)")).fetchall()
        if not rows:
            return
        columns = {str(row[1]) for row in rows if len(row) > 1}
        desired_columns = {
            "draft_id": "VARCHAR",
            "source": "VARCHAR",
            "patch_scope": "VARCHAR",
            "status": "VARCHAR",
            "confidence_score": "FLOAT",
            "patch_json": "JSON",
            "apply_plan_json": "JSON",
            "apply_ready_metadata_json": "JSON",
            "blockers_json": "JSON",
            "warnings_json": "JSON",
            "created_by": "VARCHAR",
            "reviewed_by": "VARCHAR",
            "reviewed_at": "TIMESTAMP",
            "updated_at": "TIMESTAMP",
            "candidate_state": "VARCHAR",
            "base_draft_id": "VARCHAR",
            "base_evidence_run_id": "VARCHAR",
            "provider": "VARCHAR",
            "model": "VARCHAR",
            "prompt_preset": "VARCHAR",
            "baseline_source": "VARCHAR",
            "baseline_revision_id": "VARCHAR",
            "summary_json": "JSON",
            "issues_json": "JSON",
            "patches_json": "JSON",
            "proposed_draft_sheet_json": "JSON",
            "applied_by": "VARCHAR",
            "applied_at": "TIMESTAMP",
        }
        for column_name, column_type in desired_columns.items():
            if column_name in columns:
                continue
            conn.execute(
                text(
                    f"ALTER TABLE order_sheet_patch_candidates "
                    f"ADD COLUMN {column_name} {column_type}"
                )
            )
        if "candidate_state" not in columns:
            conn.execute(
                text(
                    "UPDATE order_sheet_patch_candidates "
                    "SET candidate_state = COALESCE(NULLIF(status, ''), 'ready') "
                    "WHERE candidate_state IS NULL"
                )
            )
        if "summary_json" not in columns:
            conn.execute(
                text("UPDATE order_sheet_patch_candidates SET summary_json = '{}' WHERE summary_json IS NULL")
            )
        if "issues_json" not in columns:
            conn.execute(
                text("UPDATE order_sheet_patch_candidates SET issues_json = '[]' WHERE issues_json IS NULL")
            )
        if "patches_json" not in columns:
            conn.execute(
                text(
                    "UPDATE order_sheet_patch_candidates "
                    "SET patches_json = COALESCE(patch_json, '{\"applied_overwrites\": [], \"rejected_overwrites\": []}') "
                    "WHERE patches_json IS NULL"
                )
            )
        if "proposed_draft_sheet_json" not in columns:
            conn.execute(
                text(
                    "UPDATE order_sheet_patch_candidates "
                    "SET proposed_draft_sheet_json = '{}' "
                    "WHERE proposed_draft_sheet_json IS NULL"
                )
            )
        if "source" not in columns:
            conn.execute(text("UPDATE order_sheet_patch_candidates SET source = 'ocr_review' WHERE source IS NULL"))
        if "patch_scope" not in columns:
            conn.execute(text("UPDATE order_sheet_patch_candidates SET patch_scope = 'sheet' WHERE patch_scope IS NULL"))
        if "status" not in columns:
            conn.execute(
                text(
                    "UPDATE order_sheet_patch_candidates "
                    "SET status = CASE WHEN candidate_state = 'applied' THEN 'applied' "
                    "WHEN candidate_state = 'ready' THEN 'ready' ELSE 'pending' END "
                    "WHERE status IS NULL"
                )
            )
        if "patch_json" not in columns:
            conn.execute(
                text(
                    "UPDATE order_sheet_patch_candidates "
                    "SET patch_json = COALESCE(patches_json, '{\"applied_overwrites\": [], \"rejected_overwrites\": []}') "
                    "WHERE patch_json IS NULL"
                )
            )
        if "updated_at" not in columns:
            conn.execute(
                text("UPDATE order_sheet_patch_candidates SET updated_at = created_at WHERE updated_at IS NULL")
            )


_ensure_order_sheet_patch_candidate_schema()


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


def _mark_latest_draft_with_patch_candidate(
    *,
    order_id: str,
    base_draft_id: str | None,
    patch_candidate_id: str,
) -> None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id or not str(patch_candidate_id or "").strip():
        return
    with session_scope() as session:
        draft = None
        if str(base_draft_id or "").strip():
            draft = session.get(OrderSheetDraft, str(base_draft_id).strip())
        if draft is None:
            draft = (
                session.query(OrderSheetDraft)
                .filter(OrderSheetDraft.order_id == normalized_order_id)
                .order_by(OrderSheetDraft.edited_at.desc(), OrderSheetDraft.created_at.desc(), OrderSheetDraft.id.desc())
                .first()
            )
        if draft is None:
            return
        draft.latest_patch_candidate_id = str(patch_candidate_id).strip()
        session.flush()


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
) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    now = datetime.utcnow()
    with session_scope() as session:
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
    _mark_latest_draft_with_patch_candidate(
        order_id=normalized_order_id,
        base_draft_id=str(base_draft_id or "").strip() or None,
        patch_candidate_id=serialized["id"],
    )
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
) -> dict[str, Any] | None:
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
        if row is None:
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
    candidate = (
        get_patch_candidate(normalized_order_id, patch_candidate_id)
        if str(patch_candidate_id or "").strip()
        else get_latest_patch_candidate(normalized_order_id)
    )
    if not isinstance(candidate, dict):
        return None, "patch_candidate_not_found"
    proposed_draft = candidate.get("proposed_draft_sheet_json")
    if not isinstance(proposed_draft, dict) or not isinstance(proposed_draft.get("rows"), list):
        return None, "patch_candidate_not_applicable"
    latest_draft = draft_sheet_service.get_latest_sheet_draft(normalized_order_id)
    latest_draft_id = str((latest_draft or {}).get("id") or "").strip() or None
    expected_id = str(expected_draft_id or "").strip() or None
    if expected_id and latest_draft_id and expected_id != latest_draft_id:
        return None, "stale_draft_conflict"
    base_draft_id = str(candidate.get("base_draft_id") or "").strip() or None
    if base_draft_id and latest_draft_id and base_draft_id != latest_draft_id:
        return None, "stale_patch_candidate"
    persisted = draft_sheet_service.persist_sheet_draft(
        order_id=normalized_order_id,
        draft_sheet_json=proposed_draft,
        base_evidence_run_id=str(candidate.get("base_evidence_run_id") or "").strip() or None,
        base_template_resolution_id=None,
        base_menu_snapshot_id=None,
        draft_state="draft_ready",
        blockers=[],
        warnings=["llm_patch_applied"],
        latest_patch_candidate_id=str(candidate.get("id") or "").strip() or None,
        edited_by=str(edited_by or "").strip() or "llm_patch_candidate",
    )
    if not isinstance(persisted, dict):
        return None, "draft_persist_failed"
    applied_candidate = mark_patch_candidate_applied(
        normalized_order_id,
        str(candidate.get("id") or ""),
        applied_by=edited_by or "llm_patch_candidate",
    )
    return {
        "candidate": applied_candidate or candidate,
        "draft": persisted,
    }, None
