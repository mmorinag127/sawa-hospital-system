from __future__ import annotations

from typing import Any
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
from urllib.parse import urlparse, parse_qs
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from src.models.ocr_job import OcrJob
from src.models.order import Order, OrderLine
from src.models.document import OrderDocument
from src.models.order_version import OrderVersion
from src.models.order_ocr_cache import OrderOcrCache
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_workflow_state import OrderWorkflowState
from src.models.order_critical_decision import OrderCriticalDecision
from src.models.uploaded_pdf import UploadedPdf
from src.db import session_scope


_nonworkflow_execution: ContextVar[dict[str, Any] | None] = ContextVar("nonworkflow_execution", default=None)
_workflow_execution: ContextVar[dict[str, Any] | None] = ContextVar("workflow_execution", default=None)


@event.listens_for(Session, "before_flush")
def _bind_workflow_delivery_pending_handoff(session, flush_context, instances):
    from src.services.workflow_ocr_delivery_service import bind_pending_handoff_in_session
    for row in list(session.dirty):
        if isinstance(row, OcrJob) and (row.metrics or {}).get("workflow_ocr_delivery_id"):
            bind_pending_handoff_in_session(session, row)


@event.listens_for(Session, "before_commit")
def _require_workflow_delivery_terminal_receipts(session):
    if not session.info.get("workflow_delivery_terminal_jobs") and not any(
        isinstance(row, OcrJob) and (row.metrics or {}).get("workflow_ocr_delivery_id")
        for row in session.dirty
    ):
        return
    from src.services.workflow_ocr_delivery_service import require_terminal_receipts_before_commit
    session.flush()
    require_terminal_receipts_before_commit(session)


@event.listens_for(Session, "after_commit")
def _commit_workflow_delivery_terminal_tracking(session):
    tracked = session.info.get("workflow_delivery_terminal_jobs")
    if not tracked:
        return
    transaction = session.get_nested_transaction() or session.get_transaction()
    jobs = tracked.pop(transaction, set())
    if transaction is not None and transaction.nested:
        tracked.setdefault(transaction.parent, set()).update(jobs)
    else:
        session.info.pop("workflow_delivery_terminal_jobs", None)


@event.listens_for(Session, "after_rollback")
def _rollback_workflow_delivery_terminal_tracking(session):
    transaction = session.get_nested_transaction() or session.get_transaction()
    tracked = session.info.get("workflow_delivery_terminal_jobs")
    if tracked is not None:
        tracked.pop(transaction, None)
    if transaction is None or not transaction.nested:
        session.info.pop("workflow_delivery_terminal_jobs", None)


@event.listens_for(Session, "after_transaction_end")
def _end_workflow_delivery_terminal_tracking(session, transaction):
    # Flush subtransactions are not tracking scopes; savepoints and roots are.
    tracked = session.info.get("workflow_delivery_terminal_jobs")
    if tracked is not None:
        tracked.pop(transaction, None)
    if transaction.parent is None or not tracked:
        session.info.pop("workflow_delivery_terminal_jobs", None)


def is_selected_pdf_failure(error: Any) -> bool:
    return any(code in str(error or "") for code in (
        "selected_pdf_digest_required", "selected_pdf_document_required", "selected_pdf_input_mismatch",
        "selected_pdf_generation_required", "selected_pdf_generation_mismatch", "selected_pdf_generation_changed",
        "selected_pdf_bytes_changed", "selected_pdf_verification_failed", "selected_pdf_source_unsupported",
        "selected_pdf_start_receipt_required", "corrected_pdf_receipt_required", "corrected_pdf_source_mismatch",
    ))


def _track_execution_input(session: Any, boundary: dict[str, Any]) -> None:
    session.info.setdefault("ocr_execution_input_before_write", (boundary, deepcopy(boundary)))


@event.listens_for(Session, "after_rollback")
def _restore_execution_input_after_rollback(session: Any) -> None:
    tracked = session.info.pop("ocr_execution_input_before_write", None)
    if tracked is not None:
        boundary, original = tracked
        boundary.clear()
        boundary.update(original)


@event.listens_for(Session, "after_commit")
def _release_execution_input_after_commit(session: Any) -> None:
    session.info.pop("ocr_execution_input_before_write", None)


@event.listens_for(Session, "after_transaction_end")
def _restore_uncommitted_execution_input(session: Any, transaction: Any) -> None:
    if transaction.parent is None:
        _restore_execution_input_after_rollback(session)


def _json_snapshot(value: Any) -> Any:
    from datetime import date, datetime

    def encode(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError("execution_snapshot_non_json_value")

    return json.loads(json.dumps(value, default=encode, allow_nan=False))


def canonical_snapshot_json(value: Any) -> str:
    return json.dumps(_json_snapshot(value), sort_keys=True, ensure_ascii=False, allow_nan=False)


def snapshots_equal(left: Any, right: Any) -> bool:
    return canonical_snapshot_json(left) == canonical_snapshot_json(right)


def _order_execution_input(session: Any, order: Any, *, lock: bool = False, original_input: dict[str, Any] | None = None) -> dict[str, Any]:
    from src.services import canonical_facility_lineage as lineage
    from src.models.facility_template_version import FacilityTemplateVersion
    from src.services.config_service import capture_authored_processing_config, captured_processing_config_matches
    from src.services.order_service import _build_position_menu_entries_safe
    selected_id = str(order.template_version_id or "").strip()
    projection = lineage.read_projection(session, selected_id, lock=lock) if selected_id else None
    if projection is None:
        raise ValueError("original_template_projection_missing")
    receipt = lineage.verify_projection(projection)
    authored = receipt["authored_version"]
    if receipt["facility_id"] != order.facility_code:
        raise ValueError("original_template_configured_source_mismatch")
    if original_input is None:
        configured_input = capture_authored_processing_config(order.facility_code, selected_authored_version=authored)
    else:
        configured_input = deepcopy(original_input.get("configured_input"))
        if (not snapshots_equal(original_input.get("original_template_lineage"), receipt)
                or not snapshots_equal((configured_input or {}).get("selected_authored_version"), authored)
                or not captured_processing_config_matches(order.facility_code, configured_input)):
            raise ValueError("configured_execution_input_changed")
    original_projection = _json_snapshot({
        column.key: getattr(projection, column.key)
        for column in FacilityTemplateVersion.__table__.columns
    })
    if original_input is not None and (not snapshots_equal(original_input.get("original_template_projection"), original_projection)
            or not snapshots_equal(original_input.get("original_template_source_proof"), projection.validation_json["creation_source_proof"])):
        raise ValueError("original_template_projection_changed")
    physical_menu_entries = _build_position_menu_entries_safe(order.week_code, order.facility_code)
    workflow = session.get(OrderWorkflowState, order.id, populate_existing=True, with_for_update=lock)
    lines_query = select(OrderLine).where(OrderLine.order_id == order.id).order_by(OrderLine.id).execution_options(populate_existing=True)
    if lock:
        lines_query = lines_query.with_for_update()
    lines = session.execute(lines_query).scalars().all()
    decisions_query = select(OrderCriticalDecision).where(OrderCriticalDecision.order_id == order.id).order_by(OrderCriticalDecision.id).execution_options(populate_existing=True)
    if lock:
        decisions_query = decisions_query.with_for_update()
    decisions = session.execute(decisions_query).scalars().all()
    evidence = session.get(OrderOcrEvidenceRun, workflow.evidence_run_id, populate_existing=True, with_for_update=lock) if workflow and workflow.evidence_run_id else None
    draft = session.get(OrderSheetDraft, workflow.draft_id, populate_existing=True, with_for_update=lock) if workflow and workflow.draft_id else None
    cache = session.get(OrderOcrCache, order.id, populate_existing=True, with_for_update=lock)
    document = session.get(OrderDocument, order.current_document_id, populate_existing=True, with_for_update=lock) if order.current_document_id else None
    def row_snapshot(row: Any) -> Any:
        return {column.key: getattr(row, column.key) for column in row.__table__.columns} if row is not None else None
    return _json_snapshot({
        "order_exists": True,
        "configured_input": configured_input,
        "original_template_projection": original_projection,
        "original_template_lineage": receipt,
        "original_template_source_proof": projection.validation_json["creation_source_proof"],
        "physical_menu_entries": physical_menu_entries,
        "order": {column.key: getattr(order, column.key) for column in order.__table__.columns},
        "lines": [{column.key: getattr(line, column.key) for column in line.__table__.columns} for line in lines],
        "workflow_exists": workflow is not None,
        "workflow_revision": workflow.revision if workflow is not None else None,
        "workflow": row_snapshot(workflow), "evidence": row_snapshot(evidence),
        "draft": row_snapshot(draft), "cache": row_snapshot(cache),
        "document": row_snapshot(document),
        "decisions": [row_snapshot(decision) for decision in decisions],
    })


def lock_order_execution_input(session: Any, order_id: str) -> Any:
    from src.services.ingest_job_service import current_ingest_lease, lock_ingest_lease
    boundary = current_nonworkflow_execution()
    lease = boundary.get("ingest_lease") if isinstance(boundary, dict) else current_ingest_lease()
    if lease is not None and not lock_ingest_lease(session, lease, terminal=True):
        return None
    # The parent row serializes the explicit absent-workflow case with bootstrap.
    if session.bind.dialect.name == "sqlite":
        claim = session.execute(update(Order).where(Order.id == order_id).values(status=Order.status))
        if claim.rowcount != 1:
            return None
    query = select(Order).where(Order.id == order_id).execution_options(populate_existing=True)
    if session.bind.dialect.name != "sqlite":
        query = query.with_for_update()
    return session.execute(query).scalar_one_or_none()


def _document_belongs_to_order(session, document, order_id):
    order = session.get(Order, order_id)
    if order is None or document is None:
        return False
    if order.current_document_id == document.id:
        return order.document_uri == document.storage_uri and order.message_id == document.source_email_id
    versions = session.execute(select(OrderVersion).where(
        OrderVersion.order_id == order_id, OrderVersion.document_id == document.id,
        OrderVersion.storage_uri == document.storage_uri,
    )).scalars().all()
    return len(versions) == 1


def capture_selected_pdf_receipt(session, order, selected=None, input_reference=None):
    if order is None:
        raise ValueError("selected_pdf_document_required")
    if selected is not None:
        uri, message = selected.get("storage_uri"), selected.get("message_id")
        document_id = selected.get("document_id")
        document = session.get(OrderDocument, document_id) if document_id else None
        if not _document_belongs_to_order(session, document, order.id) or document.storage_uri != uri:
            raise ValueError("selected_pdf_document_required")
        if message is None:
            message = document.source_email_id
        elif message != document.source_email_id:
            raise ValueError("selected_pdf_input_mismatch")
    else:
        document = session.get(OrderDocument, order.current_document_id) if order.current_document_id else None
        if not _document_belongs_to_order(session, document, order.id):
            raise ValueError("selected_pdf_document_required")
        uri, message, document_id = document.storage_uri, document.source_email_id, document.id
    if input_reference is not None and uri != input_reference:
        raise ValueError("selected_pdf_input_mismatch")
    if not isinstance(uri, str) or not uri:
        raise ValueError("selected_pdf_input_mismatch")
    rows = session.execute(select(UploadedPdf).where(UploadedPdf.message_id == message, UploadedPdf.storage_uri == uri)).scalars().all()
    if len(rows) != 1 or not re.fullmatch(r"[0-9a-f]{64}", str(rows[0].content_sha256 or "")):
        raise ValueError("selected_pdf_digest_required")
    generation = None
    if urlparse(uri).scheme == "gs":
        values = parse_qs(urlparse(uri).query).get("generation", [])
        if values and (len(values) != 1 or not values[0].isdigit()):
            raise ValueError("selected_pdf_generation_required")
        generation = values[0] if values else None
    return {"uploaded_pdf_id": rows[0].id, "document_id": document_id, "order_id": order.id, "message_id": message,
            "storage_uri": uri, "sha256": rows[0].content_sha256, "generation": generation}


def verify_selected_pdf_receipt(session, receipt):
    if not isinstance(receipt, dict) or not receipt.get("uploaded_pdf_id"):
        return False
    document = session.get(OrderDocument, receipt.get("document_id"), populate_existing=True, with_for_update=True)
    if not _document_belongs_to_order(session, document, receipt.get("order_id")) or (document.storage_uri, document.source_email_id) != (receipt.get("storage_uri"), receipt.get("message_id")):
        return False
    row = session.get(UploadedPdf, receipt["uploaded_pdf_id"], populate_existing=True, with_for_update=True)
    if row is None or (row.message_id, row.storage_uri, row.content_sha256) != (receipt.get("message_id"), receipt.get("storage_uri"), receipt.get("sha256")):
        return False
    from src.services.storage_service import verify_selected_pdf_source

    verify_selected_pdf_source(receipt)
    return True


def processing_start_pdf_receipt():
    execution = current_nonworkflow_execution()
    if execution is not None:
        receipt = execution.get("selected_pdf_receipt")
    else:
        workflow = current_workflow_execution() or {}
        if isinstance(workflow.get("selected_pdf_receipt"), dict):
            return deepcopy(workflow["selected_pdf_receipt"])
        with session_scope() as session:
            if not lock_current_workflow_attempt(session, job_id=workflow.get("job_id"), order_id=workflow.get("order_id"), workflow_attempt_id=workflow.get("attempt"), expected_workflow_revision=workflow.get("revision")):
                raise ValueError("selected_pdf_execution_changed")
            job = session.get(OcrJob, workflow["job_id"])
            receipt = (job.metrics or {}).get("selected_pdf_receipt")
            if isinstance(receipt, dict):
                workflow["selected_pdf_receipt"] = deepcopy(receipt)
    if not isinstance(receipt, dict):
        raise ValueError("selected_pdf_start_receipt_required")
    return deepcopy(receipt)


def capture_reparse_read_input(session, order):
    return _order_execution_input(session, order, lock=True)


def processing_start_configured_input():
    execution = current_nonworkflow_execution()
    if execution is not None:
        captured = processing_start_read_input()
    else:
        owner = current_workflow_execution() or {}
        if isinstance(owner.get("configured_input"), dict):
            return deepcopy(owner["configured_input"])
        with session_scope() as session:
            if not lock_current_workflow_attempt(session, job_id=owner.get("job_id"), order_id=owner.get("order_id"), workflow_attempt_id=owner.get("attempt"), expected_workflow_revision=owner.get("revision")):
                raise ValueError("configured_execution_input_changed")
            job = session.get(OcrJob, owner["job_id"])
            captured = (job.metrics or {}).get("reparse_read_input")
    configured = captured.get("configured_input") if isinstance(captured, dict) else None
    if not isinstance(configured, dict):
        raise ValueError("configured_execution_input_missing")
    return deepcopy(configured)


def processing_start_read_input():
    execution = current_nonworkflow_execution()
    if execution is not None:
        start = execution.get("start_orders", {}).get(execution.get("order_id"))
        captured = start.get("processing_start_input") if isinstance(start, dict) else None
        if not isinstance(captured, dict) or sha256(json.dumps(captured, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() != start.get("input_sha256"):
            raise ValueError("reparse_start_input_missing")
        if not isinstance(captured.get("physical_menu_entries"), list) or not captured.get("expected_sheet_cells") or not captured.get("expected_target_semantics") or not snapshots_equal(captured.get("selected_pdf_receipt"), execution.get("selected_pdf_receipt")):
            raise ValueError("reparse_start_input_missing")
    else:
        owner = current_workflow_execution() or {}
        if isinstance(owner.get("reparse_read_input"), dict):
            return deepcopy(owner["reparse_read_input"])
        with session_scope() as session:
            if not lock_current_workflow_attempt(session, job_id=owner.get("job_id"), order_id=owner.get("order_id"), workflow_attempt_id=owner.get("attempt"), expected_workflow_revision=owner.get("revision")):
                raise ValueError("configured_execution_input_changed")
            job = session.get(OcrJob, owner["job_id"])
            captured = (job.metrics or {}).get("reparse_read_input")
    if not isinstance(captured, dict) or not isinstance(captured.get("order"), dict) or not isinstance(captured.get("configured_input"), dict):
        raise ValueError("reparse_start_input_missing")
    return deepcopy(captured)


def capture_nonworkflow_execution(
    job_id: str, *, order_id: str | None = None, input_reference: str | None = None,
    expected_order: dict[str, Any] | None = None,
    selected_version_input: dict[str, Any] | None = None,
    expected_document_id: str | None = None,
) -> dict[str, Any]:
    """Claim a real execution and snapshot inputs before any OCR computation.

    Registration must resolve the real target before recognition starts.
    """
    with session_scope() as session:
        from src.services.ingest_job_service import current_ingest_lease, lock_ingest_lease
        ingest_lease = current_ingest_lease()
        if ingest_lease is not None and not lock_ingest_lease(session, ingest_lease):
            raise ValueError("ingest_attempt_changed")
        if not order_id:
            raise ValueError("ocr_order_input_required")
        order = lock_order_execution_input(session, order_id)
        if order is None:
            raise ValueError("ocr_order_input_missing")
        inputs = {order.id: _order_execution_input(session, order, lock=True)}
        if ingest_lease is not None:
            document = inputs[order.id].get("document")
            if (not expected_document_id or not isinstance(document, dict) or document.get("id") != expected_document_id
                or document.get("storage_uri") != input_reference or document.get("source_email_id") != ingest_lease["id"]):
                raise ValueError("ingest_registered_source_changed")
        pdf_error = None
        try:
            pdf_receipt = capture_selected_pdf_receipt(session, order, selected_version_input, input_reference)
        except ValueError as exc:
            pdf_receipt, pdf_error = None, str(exc)
        if selected_version_input is not None and not lock_selected_version_input(session, order_id, selected_version_input):
            raise ValueError("selected_document_input_changed")
        if expected_order is not None and (
            order_id not in inputs or not snapshots_equal(inputs[order_id]["order"], expected_order)
        ):
            raise ValueError("ocr_order_input_changed")
        job = session.execute(select(OcrJob).where(OcrJob.id == job_id).with_for_update()).scalar_one_or_none()
        if job is None or (job.metrics or {}).get("workflow_attempt_id") is not None:
            raise ValueError("ocr_execution_owner_changed")
        if ingest_lease is not None and (job.metrics or {}).get("ingest_lease") != ingest_lease:
            raise ValueError("ingest_attempt_changed")
        if order_id is not None and job.order_id not in {None, order_id}:
            raise ValueError("ocr_execution_order_mismatch")
        selected_order_id = order_id if order_id is not None else job.order_id
        if selected_order_id is not None and selected_order_id not in inputs:
            raise ValueError("ocr_order_input_missing")
        if input_reference is not None:
            job.input_reference = input_reference
        from src.services.hakodate_cell_ocr_batch_service import capture_original_target_plan, expected_sheet_cells_from_target_regions
        from src.services.hakodate_step_review_pipeline_service import canonical_target_semantics
        original_inputs = {}
        for key, value in inputs.items():
            plan = capture_original_target_plan(
                configured_input=value["configured_input"],
                physical_menu_entries=value["physical_menu_entries"],
                week_code=value["order"]["week_code"],
            )
            original_inputs[key] = _json_snapshot({
                **value,
                "selected_pdf_receipt": pdf_receipt,
                "expected_sheet_cells": expected_sheet_cells_from_target_regions(plan),
                "expected_target_semantics": canonical_target_semantics(plan),
            })
        boundary = {
            "ingest_lease": ingest_lease,
            "job_id": job_id, "execution_id": uuid4().hex,
            "input_reference": job.input_reference,
            "order_id": selected_order_id, "orders": inputs,
            "start_orders": {
                key: {"order_id": key, "order_exists": True,
                      "workflow_exists": value["workflow_exists"],
                      "workflow_revision": value["workflow_revision"],
                      "processing_start_input": deepcopy(value),
                      "input_sha256": sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}
                for key, value in original_inputs.items()
            },
            "job_order_id": job.order_id, "created_order_ids": [],
            "selected_version_input": _json_snapshot(selected_version_input),
            "selected_pdf_receipt": pdf_receipt,
            "selected_pdf_error": pdf_error,
        }
        evidence_input = inputs[order_id].get("evidence")
        workflow_input = inputs[order_id].get("workflow")
        boundary["first_pass_source"] = (
            captured_evidence_source(order_id, evidence_input)
            if evidence_input and workflow_input and workflow_input.get("evidence_run_id") == evidence_input.get("id")
            else None
        )
        retained_metrics = {
            key: value for key, value in (job.metrics or {}).items()
            if key not in {"evidence_run_id", "reparse_evidence_run_id", "new_evidence_available", "draft_id", "patch_candidate_id",
                           "reparse_ocr_prompt", "reparse_prompt_preset", "reparse_requested_provider",
                           "reparse_ocr_model", "reparse_llm_assist", "pipeline_delivery_receipt", "pending_receipt"}
        }
        if ingest_lease is None:
            retained_metrics.pop("ingest_lease", None)
        job.metrics = {**retained_metrics, "nonworkflow_execution": deepcopy(boundary)}
        job.output_reference = None
        return boundary


def captured_evidence_source(order_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": order_id, "evidence_run_id": evidence["id"],
        "input_sha256": sha256(json.dumps(_json_snapshot(evidence), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
    }


def selected_version_snapshot(version):
    return _json_snapshot({column.key: getattr(version, column.key) for column in version.__table__.columns})


def lock_selected_version_input(session, order_id, captured):
    if not isinstance(captured, dict) or captured.get("order_id") != order_id:
        return False
    version = session.get(OrderVersion, captured.get("id"), populate_existing=True, with_for_update=True)
    return version is not None and snapshots_equal(selected_version_snapshot(version), captured)


@contextmanager
def nonworkflow_execution_context(boundary: dict[str, Any] | None):
    token = _nonworkflow_execution.set(boundary)
    try:
        yield
    finally:
        _nonworkflow_execution.reset(token)


def current_nonworkflow_execution() -> dict[str, Any] | None:
    return _nonworkflow_execution.get()


@contextmanager
def workflow_execution_context(job_id: str | None, order_id: str | None, attempt: str | None, revision: int | None):
    token = _workflow_execution.set({"job_id": job_id, "order_id": order_id, "attempt": attempt, "revision": revision})
    try:
        yield
    finally:
        _workflow_execution.reset(token)


def current_workflow_execution() -> dict[str, Any] | None:
    return _workflow_execution.get()


def lock_current_execution_write(session: Any, order_id: str) -> bool:
    """Fence derived writes in the already captured execution, not a new read."""
    workflow = _workflow_execution.get()
    if workflow is not None and (workflow["job_id"] is not None or workflow["attempt"] is not None or workflow["revision"] is not None):
        if workflow["order_id"] != order_id:
            return False
        return lock_actual_write_ownership(
            session, job_id=workflow["job_id"], order_id=order_id,
            workflow_attempt_id=workflow["attempt"], expected_workflow_revision=workflow["revision"],
        )
    execution_input = current_nonworkflow_execution()
    if execution_input is not None:
        return lock_actual_write_ownership(
            session, job_id=execution_input.get("job_id"), order_id=order_id,
            workflow_attempt_id=None, expected_workflow_revision=None,
        )
    return True


def lock_nonworkflow_execution_job(session: Any, boundary: dict[str, Any] | None) -> bool:
    if not isinstance(boundary, dict) or not boundary.get("job_id") or not boundary.get("execution_id"):
        return False
    if boundary.get("ingest_lease") is not None:
        from src.services.ingest_job_service import lock_ingest_lease
        if not lock_ingest_lease(session, boundary["ingest_lease"], terminal=True):
            return False
    _track_execution_input(session, boundary)
    if session.bind.dialect.name == "sqlite":
        claim = session.execute(update(OcrJob).where(OcrJob.id == boundary["job_id"]).values(status=OcrJob.status))
        if claim.rowcount != 1:
            return False
    job = session.execute(
        select(OcrJob).where(OcrJob.id == boundary["job_id"]).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    stored = (job.metrics or {}).get("nonworkflow_execution") if job is not None else None
    return bool(
        job is not None and (job.metrics or {}).get("workflow_attempt_id") is None
        and isinstance(stored, dict) and stored.get("execution_id") == boundary["execution_id"]
        and snapshots_equal(stored.get("start_orders"), boundary.get("start_orders"))
        and snapshots_equal(stored.get("selected_pdf_receipt"), boundary.get("selected_pdf_receipt"))
        and job.input_reference == boundary.get("input_reference")
        and job.order_id == boundary.get("job_order_id")
    )


def advance_nonworkflow_order_input(session: Any, boundary: dict[str, Any], order: Any) -> None:
    """Advance only inputs actually written by this execution under its lock."""
    _track_execution_input(session, boundary)
    session.flush()
    original_config = boundary["orders"][order.id]["configured_input"]
    advanced = _order_execution_input(session, order, original_input=boundary["orders"][order.id])
    for key in ("original_template_projection", "original_template_lineage", "original_template_source_proof"):
        if not snapshots_equal(advanced[key], boundary["orders"][order.id].get(key)):
            raise ValueError("original_template_projection_changed")
    if not snapshots_equal(advanced["configured_input"], original_config):
        raise ValueError("configured_execution_input_changed")
    if not snapshots_equal(advanced["physical_menu_entries"], boundary["orders"][order.id]["physical_menu_entries"]):
        raise ValueError("physical_execution_input_changed")
    boundary["orders"][order.id] = advanced
    boundary["order_id"] = order.id
    job = session.get(OcrJob, boundary["job_id"])
    job.order_id = order.id
    boundary["job_order_id"] = order.id
    job.metrics = {**(job.metrics or {}), "nonworkflow_execution": deepcopy(boundary)}
    # Strict ownership reads refresh from storage even when autoflush is disabled.
    session.flush([job])
    if boundary.get("ingest_lease") is not None and not boundary.get("ingest_successor_id"):
        from src.services.ingest_delivery_successor_service import advance_preparation
        advance_preparation(session, boundary)


def lock_current_workflow_revision(
    session: Any,
    *,
    order_id: str | None,
    expected_workflow_revision: int | None,
) -> bool:
    """Claim the workflow revision before any reusable OCR job lock."""
    if not order_id or type(expected_workflow_revision) is not int:
        return False
    if lock_order_execution_input(session, order_id) is None:
        return False
    workflow_conditions = (
        OrderWorkflowState.order_id == order_id,
        OrderWorkflowState.revision == expected_workflow_revision,
    )
    if session.bind.dialect.name == "sqlite":
        claim = session.execute(
            update(OrderWorkflowState)
            .where(*workflow_conditions)
            .values(revision=OrderWorkflowState.revision)
        )
        return claim.rowcount == 1
    return session.execute(
        select(OrderWorkflowState.order_id).where(*workflow_conditions).with_for_update()
    ).scalar_one_or_none() is not None


def lock_current_workflow_attempt(
    session: Any,
    *,
    job_id: str | None,
    workflow_attempt_id: str | None,
    order_id: str | None,
    expected_workflow_revision: int | None,
    source_failure: str | None = None,
) -> bool:
    """Fence workflow-owned writes with the reusable OCR job row lock.

    SQLAlchemy renders the JSON scalar comparison for both SQLite and
    PostgreSQL; ``FOR UPDATE`` supplies the PostgreSQL serialization lock while
    SQLite retains the same conditional predicate inside its write transaction.
    """
    if workflow_attempt_id is None or type(expected_workflow_revision) is not int:
        return False
    if not job_id or not order_id:
        return False
    # Match queue ordering: workflow state first, then reusable job.
    job_conditions = (
        OcrJob.id == job_id,
        OcrJob.order_id == order_id,
        OcrJob.metrics["workflow_attempt_id"].as_string() == workflow_attempt_id,
    )
    if not lock_current_workflow_revision(
        session,
        order_id=order_id,
        expected_workflow_revision=expected_workflow_revision,
    ):
        return False
    if session.bind.dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE. Conditional no-op UPDATEs take its writer
        # lock within the existing transaction without changing business values.
        job_claim = session.execute(
            update(OcrJob)
            .where(*job_conditions)
            .values(status=OcrJob.status)
        )
        if job_claim.rowcount != 1:
            return False
    elif session.execute(select(OcrJob.id).where(*job_conditions).with_for_update()).scalar_one_or_none() is None:
        return False
    job = session.get(OcrJob, job_id, populate_existing=True)
    from src.services.workflow_ocr_delivery_service import lock_claim_write
    if not lock_claim_write(
        session, job_id=job_id, order_id=order_id,
        attempt=workflow_attempt_id, revision=expected_workflow_revision,
    ):
        return False
    captured = (job.metrics or {}).get("reparse_read_input")
    from src.services.config_service import captured_processing_config_matches
    order = session.get(Order, order_id, populate_existing=True)
    if not isinstance(captured, dict) or order is None or not captured_processing_config_matches(order.facility_code, captured.get("configured_input")):
        return False
    from src.services.order_service import _build_position_menu_entries_safe
    if not snapshots_equal(captured.get("physical_menu_entries"), _build_position_menu_entries_safe(order.week_code, order.facility_code)):
        return False
    owner = current_workflow_execution()
    if isinstance(owner, dict) and owner.get("job_id") == job_id:
        owner["configured_input"] = deepcopy(captured["configured_input"])
        owner["reparse_read_input"] = deepcopy(captured)
        owner["selected_pdf_receipt"] = deepcopy((job.metrics or {}).get("selected_pdf_receipt"))
    selected = (job.metrics or {}).get("selected_version_input")
    return (
        (selected is None or lock_selected_version_input(session, order_id, selected))
        and (is_selected_pdf_failure(source_failure) or verify_selected_pdf_receipt(session, (job.metrics or {}).get("selected_pdf_receipt")))
    )


def lock_actual_write_ownership(
    session: Any,
    *,
    job_id: str | None,
    order_id: str | None,
    workflow_attempt_id: str | None,
    expected_workflow_revision: int | None,
    source_failure: str | None = None,
) -> bool:
    """Lock the current owner before every workflow-adjacent actual write.

    A non-workflow execution may continue only while the reusable job remains
    non-workflow-owned. It must never infer a replacement attempt from the job.
    """
    if job_id is None:
        workflow_owner = _workflow_execution.get()
        return (
            current_nonworkflow_execution() is None
            and (workflow_owner is None or not any(workflow_owner.get(key) is not None for key in ("job_id", "attempt", "revision")))
            and workflow_attempt_id is None and expected_workflow_revision is None
        )
    if not job_id or not order_id:
        return False
    if workflow_attempt_id is not None or expected_workflow_revision is not None:
        return lock_current_workflow_attempt(
            session,
            job_id=job_id,
            workflow_attempt_id=workflow_attempt_id,
            order_id=order_id,
            expected_workflow_revision=expected_workflow_revision,
            source_failure=source_failure,
        )

    boundary = current_nonworkflow_execution()
    if not isinstance(boundary, dict) or boundary.get("job_id") != job_id:
        return False
    if boundary.get("ingest_lease") is not None:
        from src.services.ingest_job_service import lock_ingest_lease
        if not lock_ingest_lease(session, boundary["ingest_lease"], terminal=True):
            return False
    if set(boundary.get("orders", {})) != {order_id}:
        return False
    start_input = boundary.get("start_orders", {}).get(order_id)
    if not isinstance(start_input, dict) or not start_input.get("input_sha256"):
        return False
    original = start_input.get("processing_start_input")
    if not isinstance(original, dict) or not isinstance(original.get("configured_input"), dict) or not isinstance(original.get("physical_menu_entries"), list):
        return False
    if sha256(json.dumps(original, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() != start_input["input_sha256"]:
        return False
    if not original.get("expected_sheet_cells") or not original.get("expected_target_semantics") or not snapshots_equal(original.get("selected_pdf_receipt"), boundary.get("selected_pdf_receipt")):
        return False
    _track_execution_input(session, boundary)
    if boundary.get("order_id") is not None and boundary["order_id"] != order_id:
        return False
    captured = boundary.get("orders", {}).get(order_id)
    if not isinstance(captured, dict):
        return False
    if any(not snapshots_equal(original[key], captured.get(key)) for key in ("configured_input", "physical_menu_entries")):
        return False
    secondary = (captured.get("workflow") or {}).get("secondary_actions_json")
    workflow_meta = secondary.get("workflow_v2") if isinstance(secondary, dict) else None
    if isinstance(workflow_meta, dict) and workflow_meta.get("ocr_job_attempt_id") is not None:
        return False
    order = lock_order_execution_input(session, order_id)
    if order is None:
        return False
    if boundary.get("selected_version_input") is not None and not lock_selected_version_input(session, order_id, boundary["selected_version_input"]):
        return False
    if captured.get("workflow_exists") is True:
        if not lock_current_workflow_revision(
            session, order_id=order_id,
            expected_workflow_revision=captured.get("workflow_revision"),
        ):
            return False
    elif captured.get("workflow_exists") is not False or session.get(OrderWorkflowState, order_id) is not None:
        return False
    if not snapshots_equal(_order_execution_input(session, order, lock=True, original_input=captured), captured):
        return False
    job_conditions = (OcrJob.id == job_id,)
    if session.bind.dialect.name == "sqlite":
        claim = session.execute(
            update(OcrJob)
            .where(*job_conditions)
            .values(status=OcrJob.status)
        )
        if claim.rowcount != 1:
            return False
        current_attempt = session.execute(
            select(OcrJob.metrics["workflow_attempt_id"].as_string()).where(*job_conditions)
        ).scalar_one_or_none()
    else:
        current_owner = session.execute(
            select(OcrJob.id, OcrJob.metrics["workflow_attempt_id"].as_string())
            .where(*job_conditions)
            .with_for_update()
        ).one_or_none()
        if current_owner is None:
            return False
        current_attempt = current_owner[1]
    job = session.get(OcrJob, job_id, populate_existing=True)
    stored_boundary = (job.metrics or {}).get("nonworkflow_execution") if job is not None else None
    return (
        not bool(str(current_attempt or "").strip())
        and isinstance(stored_boundary, dict)
        and stored_boundary.get("execution_id") == boundary.get("execution_id")
        and snapshots_equal(stored_boundary.get("start_orders"), boundary.get("start_orders"))
        and snapshots_equal(stored_boundary.get("selected_pdf_receipt"), boundary.get("selected_pdf_receipt"))
        and job.input_reference == boundary.get("input_reference")
        and job.order_id == boundary.get("job_order_id")
        and stored_boundary.get("order_id") == boundary.get("order_id")
        and (is_selected_pdf_failure(source_failure) or verify_selected_pdf_receipt(session, boundary.get("selected_pdf_receipt")))
    )


def synchronous_input_versions(
    order: Any, evidence: Any, draft: Any, cache: Any, order_lines: Any = None,
) -> dict[str, Any]:
    """Keep the exact read inputs, including in-place edits, for the write CAS."""
    versions = {
        name: deepcopy({column.key: getattr(row, column.key) for column in row.__table__.columns})
        if row is not None else None
        for name, row in (("order", order), ("evidence", evidence), ("draft", draft), ("cache", cache))
    }
    versions["order_lines"] = [
        deepcopy({column.key: getattr(row, column.key) for column in row.__table__.columns})
        for row in sorted(order.lines if order_lines is None else order_lines, key=lambda row: row.id)
    ]
    return versions


def lock_current_synchronous_cache_write(
    session: Any,
    *,
    order_id: str | None,
    expected_order_lines_updated_at: Any,
    expected_workflow_revision: int | None,
    expected_evidence_run_id: str | None,
    expected_draft_id: str | None,
    expected_draft_base_evidence_run_id: str | None,
    expected_cache_exists: bool,
    expected_cache_updated_at: Any,
    expected_input_versions: dict[str, Any] | None = None,
    source_failure: str | None = None,
) -> bool:
    """CAS a synchronous cache repair from the operator-read input snapshot."""
    if not order_id or expected_workflow_revision is None or not expected_evidence_run_id:
        return False
    if not isinstance(expected_input_versions, dict):
        return False
    if not lock_current_workflow_revision(
        session,
        order_id=order_id,
        expected_workflow_revision=expected_workflow_revision,
    ):
        return False

    order_query = select(Order).where(Order.id == order_id)
    evidence_query = select(OrderOcrEvidenceRun).where(OrderOcrEvidenceRun.id == expected_evidence_run_id)
    cache_query = select(OrderOcrCache).where(OrderOcrCache.order_id == order_id)
    draft_query = select(OrderSheetDraft).where(OrderSheetDraft.id == expected_draft_id) if expected_draft_id else None
    if session.bind.dialect.name != "sqlite":
        order_query = order_query.with_for_update()
        evidence_query = evidence_query.with_for_update()
        cache_query = cache_query.with_for_update()
        if draft_query is not None:
            draft_query = draft_query.with_for_update()

    order = session.execute(order_query.execution_options(populate_existing=True)).scalar_one_or_none()
    if order is None or order.lines_updated_at != expected_order_lines_updated_at:
        return False
    evidence = session.execute(evidence_query.execution_options(populate_existing=True)).scalar_one_or_none()
    if evidence is None or evidence.order_id != order_id:
        return False
    from src.services.ocr_evidence_service import evidence_run_is_selectable

    if not evidence_run_is_selectable(evidence):
        return False
    workflow_evidence = session.execute(
        select(OrderWorkflowState).where(
            OrderWorkflowState.order_id == order_id,
            OrderWorkflowState.revision == expected_workflow_revision,
        ).execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if workflow_evidence is not None:
        if workflow_evidence.evidence_run_id != expected_evidence_run_id:
            return False
        if workflow_evidence.draft_id != expected_draft_id:
            return False
        if workflow_evidence.template_version_id != evidence.template_version_id:
            return False
    else:
        return False
    draft = None
    if draft_query is not None:
        draft = session.execute(draft_query.execution_options(populate_existing=True)).scalar_one_or_none()
        if (
            draft is None
            or draft.order_id != order_id
            or draft.base_evidence_run_id != expected_draft_base_evidence_run_id
            or draft.base_evidence_run_id != expected_evidence_run_id
            or draft.template_version_id != evidence.template_version_id
        ):
            return False
        if not isinstance(draft.draft_sheet_json, dict) or any(
            draft.draft_sheet_json.get(key) and draft.draft_sheet_json[key] != expected_evidence_run_id
            for key in ("base_evidence_run_id", "evidence_run_id")
        ):
            return False
    cache = session.execute(cache_query.execution_options(populate_existing=True)).scalar_one_or_none()
    if (cache is not None) != expected_cache_exists:
        return False
    lines_query = select(OrderLine).where(OrderLine.order_id == order_id).order_by(OrderLine.id)
    if session.bind.dialect.name != "sqlite":
        lines_query = lines_query.with_for_update()
    lines = session.execute(lines_query.execution_options(populate_existing=True)).scalars().all()
    pdf_receipt = expected_input_versions.get("selected_pdf_receipt")
    if not is_selected_pdf_failure(source_failure) and not verify_selected_pdf_receipt(session, pdf_receipt):
        return False
    current_versions = synchronous_input_versions(order, evidence, draft, cache, lines)
    current_versions["selected_pdf_receipt"] = pdf_receipt
    if not snapshots_equal(current_versions, expected_input_versions):
        return False
    return cache is None or cache.updated_at == expected_cache_updated_at
