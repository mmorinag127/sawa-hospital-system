from __future__ import annotations

from src.staging_data import order_runtime_setting as _runtime_setting

import json
from hashlib import sha256
from src.services.storage_uri import parse_gcs_uri
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select, update

from src.db import session_scope
from src.models.ocr_job import OcrJob
from src.services.workflow_attempt_guard_service import (
    lock_actual_write_ownership, lock_current_workflow_attempt,
    current_nonworkflow_execution, lock_nonworkflow_execution_job,
    current_workflow_execution,
    is_selected_pdf_failure,
)

_RECOVERABLE_STATUSES = {"awaiting_output", "recovering"}
_RECOVERABLE_RESULT_STATES = {"awaiting_output", "recovering"}
_ORDER_REPARSE_REQUEST_MODES = {"ocr_rerun", "ocr_reparse", "llm_reparse"}

REPARSE_REQUEST_FIELDS = {
    "ocr_prompt": "reparse_ocr_prompt", "prompt_preset": "reparse_prompt_preset",
    "ocr_provider": "reparse_requested_provider", "ocr_model": "reparse_ocr_model",
    "llm_assist": "reparse_llm_assist",
}


class OwnPipelineLineageError(ValueError):
    pass


def _lock_own_execution_job(session, job_id, *, source_failure=None):
    workflow = current_workflow_execution()
    execution = current_nonworkflow_execution()
    owner = workflow if workflow is not None and workflow.get("attempt") is not None else execution
    if not owner or owner.get("job_id") != job_id or not owner.get("order_id"):
        return None
    if not lock_actual_write_ownership(
        session, job_id=job_id, order_id=owner["order_id"],
        workflow_attempt_id=owner.get("attempt"),
        expected_workflow_revision=owner.get("revision"),
        source_failure=source_failure,
    ):
        return None
    job = session.get(OcrJob, job_id, populate_existing=True)
    if job is None or (job.metrics or {}).get("rerun_terminal_receipt") is not None:
        return None
    if owner.get("attempt") is not None and job.status not in {"running", "pending", "recovering", "awaiting_output"}:
        return None
    return job


def complete_own_selected_pdf_failure(job_id, error):
    """Failure-only receipt, never recognized lines, evidence, or adoption."""
    if not is_selected_pdf_failure(error):
        return None
    execution = current_nonworkflow_execution()
    if not isinstance(execution, dict) or execution.get("job_id") != job_id:
        return None
    with session_scope() as session:
        job = _lock_own_execution_job(session, job_id, source_failure=error)
        if job is None or job.status in {"done", "failed", "completed", "success"}:
            return None
        from src.models.document import OrderDocument
        captured = execution.get("orders", {}).get(execution["order_id"], {})
        document = session.get(OrderDocument, (captured.get("document") or {}).get("id"))
        if document is not None:
            document.status, document.error_message = "error", str(error)
        receipt = {"job_id": job_id, "order_id": execution["order_id"],
            "execution_id": execution["execution_id"], "status": "failed", "error": str(error),
            "evidence_run_id": None, "adopted": False}
        job.status, job.error_message = "failed", str(error)
        job.updated_at = datetime.utcnow()
        job.metrics = {**(job.metrics or {}), "result_state": "hard_failed",
            "processing_stage": "source_verification_failed", "status": "failed", "error": str(error),
            "stage_updated_at": job.updated_at.isoformat(),
            "source_failure_receipt": receipt, "non_ocr_terminal_receipt": receipt,
            "rerun_terminal_receipt": receipt, "next_recovery_at": None}
        session.flush()
        return receipt


def capture_own_reparse_request(job_id, request):
    if set(request) != set(REPARSE_REQUEST_FIELDS):
        return False
    with session_scope() as session:
        job = _lock_own_execution_job(session, job_id)
        if job is None:
            return False
        execution = current_nonworkflow_execution()
        if isinstance(execution, dict) and execution.get("ingest_successor_id"):
            from src.services.ingest_delivery_successor_service import validate_request
            if not validate_request(session, execution, request):
                return False
        metrics = dict(job.metrics or {})
        present = [key in metrics for key in REPARSE_REQUEST_FIELDS.values()]
        if any(present):
            return all(present) and all(metrics[key] == request[name] for name, key in REPARSE_REQUEST_FIELDS.items())
        job.metrics = {**metrics, **{key: request[name] for name, key in REPARSE_REQUEST_FIELDS.items()}}
        return True


def own_pipeline_source():
    workflow = current_workflow_execution() or {}
    execution = current_nonworkflow_execution() or {}
    owner = workflow if workflow.get("attempt") is not None else execution
    job_id = owner.get("job_id")
    if not job_id:
        raise ValueError("ocr_execution_input_required")
    with session_scope() as session:
        job = _lock_own_execution_job(session, job_id)
        if job is None or not job.input_reference:
            raise ValueError("ocr_pipeline_source_changed")
        return job_id, job.input_reference


def record_own_pipeline_delivery(job_id, *, selected_input_reference, input_reference, output_reference, generation, input_sha256=None, wait_timeout_seconds=None):
    if not input_reference or not output_reference or not selected_input_reference:
        return False
    with session_scope() as session:
        job = _lock_own_execution_job(session, job_id)
        if job is None or job.status not in {"running", "pending", "recovering", "awaiting_output"} or job.input_reference != selected_input_reference:
            return False
        workflow = current_workflow_execution() or {}
        execution = current_nonworkflow_execution() or {}
        owner = workflow if workflow.get("attempt") is not None else execution
        selected_pdf = (
            execution.get("selected_pdf_receipt")
            if execution else (job.metrics or {}).get("selected_pdf_receipt")
        )
        if not isinstance(selected_pdf, dict) or selected_pdf.get("sha256") != input_sha256:
            return False
        read_input = execution.get("orders", {}).get(owner["order_id"]) if execution else (job.metrics or {}).get("reparse_read_input")
        configured_input = read_input.get("configured_input") if isinstance(read_input, dict) else None
        if not isinstance(configured_input, dict):
            return False
        configured_digest = sha256(json.dumps(configured_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        input_source = parse_gcs_uri(input_reference)
        receipt = {
            "job_id": job_id, "order_id": owner["order_id"],
            "workflow_attempt_id": workflow.get("attempt"),
            "workflow_revision": workflow.get("revision"),
            "execution_id": execution.get("execution_id"),
            "selected_input_reference": selected_input_reference,
            "input_reference": input_reference, "output_reference": output_reference,
            "generation": generation,
            "input_sha256": input_sha256,
            "configured_input_sha256": configured_digest,
            "selected_pdf_receipt": selected_pdf,
            "wait_timeout_seconds": wait_timeout_seconds,
            "pipeline_job_id": f"{input_source.bucket}:{input_source.object_name}:{generation}",
        }
        job.metrics = {**(job.metrics or {}), "pipeline_delivery_receipt": receipt}
        job.output_reference = output_reference
        return True


def own_pipeline_delivery_matches(job, input_reference, output_reference):
    receipt = (job.metrics or {}).get("pipeline_delivery_receipt")
    workflow = current_workflow_execution() or {}
    execution = current_nonworkflow_execution() or {}
    owner = workflow if workflow.get("attempt") is not None else execution
    return isinstance(receipt, dict) and (
        receipt.get("job_id") == job.id and bool(owner.get("order_id"))
        and receipt.get("order_id") == owner["order_id"]
        and job.order_id in {None, owner["order_id"]}
        and receipt.get("workflow_attempt_id") == workflow.get("attempt")
        and receipt.get("workflow_revision") == workflow.get("revision")
        and receipt.get("execution_id") == execution.get("execution_id")
        and receipt.get("selected_input_reference") == job.input_reference
        and receipt.get("input_reference") == input_reference
        and receipt.get("output_reference") == output_reference
    )


def own_pipeline_delivery_for_output(output_reference, *, required=True, write_session=None):
    workflow = current_workflow_execution() or {}
    execution = current_nonworkflow_execution() or {}
    owner = workflow if workflow.get("attempt") is not None else execution
    with (nullcontext(write_session) if write_session is not None else session_scope()) as session:
        job = _lock_own_execution_job(session, owner.get("job_id"))
        receipt = (job.metrics or {}).get("pipeline_delivery_receipt") if job is not None else None
        if not isinstance(receipt, dict) and not required:
            return None
        if (
            job is None or not isinstance(receipt, dict)
            or not str(receipt.get("generation") or "").isdigit()
            or (output_reference is not None and receipt.get("output_reference") != output_reference)
            or not own_pipeline_delivery_matches(job, receipt.get("input_reference"), receipt.get("output_reference"))
        ):
            raise OwnPipelineLineageError("ocr_pipeline_delivery_mismatch")
        return dict(receipt)


def validate_own_pipeline_result(payload, output_reference, *, write_session=None, allow_terminal_error=False):
    receipt = own_pipeline_delivery_for_output(output_reference, write_session=write_session)
    if not isinstance(payload, dict) or (
        payload.get("input_reference") != receipt["input_reference"]
        or str(payload.get("input_generation") or "") != receipt["generation"]
        or payload.get("output_reference") != receipt["output_reference"]
        or payload.get("job_id") != receipt["pipeline_job_id"]
        or (payload.get("status") == "done" and (
            not receipt.get("input_sha256") or payload.get("input_sha256") != receipt["input_sha256"]
            or not receipt.get("configured_input_sha256") or payload.get("configured_input_sha256") != receipt["configured_input_sha256"]
        ))
    ):
        raise OwnPipelineLineageError("ocr_pipeline_result_generation_mismatch")
    status = payload.get("status")
    stage = payload.get("stage")
    if not isinstance(status, str) or status not in {"running", "pending", "queued", "done", "failed", "error"}:
        raise OwnPipelineLineageError("ocr_pipeline_result_status_invalid")
    if ((stage == "done" and status != "done")
        or (stage in ("error", "failed") and status in {"running", "pending", "queued", "done"})):
        raise OwnPipelineLineageError("ocr_pipeline_result_status_stage_mismatch")
    if status in {"done", "failed", "error"} and payload.get("execution_owner") != {
        "job_id": receipt["job_id"], "order_id": receipt["order_id"],
        "workflow_attempt_id": receipt["workflow_attempt_id"], "workflow_revision": receipt["workflow_revision"],
        "nonworkflow_execution_id": receipt["execution_id"],
    }:
        raise OwnPipelineLineageError("ocr_pipeline_terminal_owner_mismatch")
    if status == "failed" or (status == "error" and allow_terminal_error is not True):
        raise OwnPipelineLineageError("ocr_pipeline_required_stage_failed")
    if status == "done":
        stages = payload.get("canonical_stages")
        if payload.get("canonical_adoption_allowed") is not True or not isinstance(stages, dict) or set(stages) != {"classification", "roi", "correction"} or any(value not in {"passed", "disabled"} for value in stages.values()):
            raise OwnPipelineLineageError("ocr_pipeline_required_stage_failed")
    return payload


def validate_own_first_pass_recognizer(payload, *, write_session):
    execution = current_nonworkflow_execution()
    provenance = payload.get("_first_pass_recognizer")
    auxiliary = payload.get("auxiliary_pipeline_result")
    pipeline = auxiliary if isinstance(auxiliary, dict) else payload
    validate_own_pipeline_result(pipeline, pipeline.get("output_reference"), write_session=write_session)
    receipt = own_pipeline_delivery_for_output(pipeline.get("output_reference"), write_session=write_session)
    if (
        execution is None or not isinstance(provenance, dict)
        or provenance.get("job_id") != execution.get("job_id")
        or provenance.get("execution_id") != execution.get("execution_id")
        or provenance.get("workflow_attempt_id") is not None
        or provenance.get("workflow_revision") is not None
        or provenance.get("input_sha256") != receipt.get("input_sha256")
        or not provenance.get("selected_input_reference")
        or provenance.get("selected_input_reference") != receipt.get("selected_input_reference")
        or provenance.get("template_id") != pipeline.get("template_id")
    ):
        raise OwnPipelineLineageError("first_pass_recognizer_lineage_mismatch")
    from src.services.ingest_policy import require_explicit_ingest_week
    from src.models.order import Order

    week = require_explicit_ingest_week(provenance.get("week_hint"))
    order = write_session.get(Order, execution.get("order_id"))
    if order is None or order.week_code != week:
        raise OwnPipelineLineageError("first_pass_recognizer_week_mismatch")
    if isinstance(auxiliary, dict) and not payload.get("error"):
        if (
            not isinstance(payload.get("raw_recognizer_payload"), dict)
            or payload.get("table_rows") != provenance.get("recognized_rows")
            or payload.get("engine") != provenance.get("provider")
        ):
            raise OwnPipelineLineageError("first_pass_recognizer_result_missing")
    return payload


def mark_own_pipeline_waiting(job_id, *, input_reference, output_reference, error_message,
                              metrics_patch, recovery_count_increment=False):
    with session_scope() as session:
        job = _lock_own_execution_job(session, job_id)
        if job is None or job.status not in {"running", "pending", "recovering", "awaiting_output"} or not own_pipeline_delivery_matches(job, input_reference, output_reference):
            return None
        metrics = merge_own_job_metrics_patch(job.metrics, metrics_patch)
        if recovery_count_increment:
            metrics["auto_recovery_count"] = int((job.metrics or {}).get("auto_recovery_count") or 0) + 1
        job.metrics = metrics
        job.status = "awaiting_output"
        job.output_reference = output_reference
        job.error_message = error_message
        job.updated_at = datetime.utcnow()
        return _job_to_dict(job)


def _job_to_dict(job: OcrJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "order_id": job.order_id,
        "uploaded_pdf_id": job.uploaded_pdf_id,
        "order_document_id": job.order_document_id,
        "input_artifact_digest": job.input_artifact_digest,
        "status": job.status,
        "input_reference": job.input_reference,
        "template_id": job.template_id,
        "template_version_id": job.template_version_id,
        "output_reference": job.output_reference,
        "metrics": job.metrics,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def get_job_request_mode(job: dict[str, Any] | None) -> str:
    if not isinstance(job, dict):
        return ""
    metrics = job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    direct_mode = str(metrics.get("request_mode") or metrics.get("rerun_mode") or "").strip().lower()
    if direct_mode:
        return direct_mode
    quality_track = str(metrics.get("quality_track") or "").strip().lower()
    if quality_track == "llm_reparse":
        return "llm_reparse"
    if quality_track == "non_llm_reparse":
        return "ocr_reparse"
    return ""


def is_order_reparse_job(job: dict[str, Any] | None, order_id: str) -> bool:
    if not isinstance(job, dict):
        return False
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return False
    normalized_job_order_id = str(job.get("order_id") or "").strip()
    normalized_job_id = str(job.get("id") or "").strip()
    if normalized_job_order_id != normalized_order_id and normalized_job_id != f"OCR-{normalized_order_id}":
        return False
    return get_job_request_mode(job) in _ORDER_REPARSE_REQUEST_MODES


def get_stale_minutes() -> int:
    raw = str(_runtime_setting("OCR_JOB_STALE_MINUTES", "30") or "").strip()
    try:
        return int(raw)
    except ValueError:
        return 30


def get_auto_recovery_interval_seconds() -> int:
    raw = str(_runtime_setting("OCR_JOB_AUTO_RECOVERY_INTERVAL_SECONDS", "120") or "").strip()
    try:
        return max(int(raw), 5)
    except ValueError:
        return 120


def get_auto_recovery_max_attempts() -> int:
    raw = str(_runtime_setting("OCR_JOB_AUTO_RECOVERY_MAX_ATTEMPTS", "3") or "").strip()
    try:
        return max(int(raw), 0)
    except ValueError:
        return 3


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def get_job_progress_updated_at(job: dict[str, Any] | None) -> datetime | None:
    if not isinstance(job, dict):
        return None
    metrics = job.get("metrics")
    if isinstance(metrics, dict):
        stage_updated_at = _coerce_datetime(metrics.get("stage_updated_at"))
        if isinstance(stage_updated_at, datetime):
            return stage_updated_at
    return _coerce_datetime(job.get("updated_at"))


def get_job_recovery_attempts(job: dict[str, Any] | None) -> int:
    if not isinstance(job, dict):
        return 0
    metrics = job.get("metrics")
    if not isinstance(metrics, dict):
        return 0
    raw = metrics.get("auto_recovery_count")
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def get_job_next_recovery_at(job: dict[str, Any] | None) -> datetime | None:
    if not isinstance(job, dict):
        return None
    metrics = job.get("metrics")
    if isinstance(metrics, dict):
        next_retry_at = _coerce_datetime(metrics.get("next_recovery_at"))
        if isinstance(next_retry_at, datetime):
            return next_retry_at
    progress_updated_at = get_job_progress_updated_at(job)
    if not isinstance(progress_updated_at, datetime):
        return None
    return progress_updated_at + timedelta(seconds=get_auto_recovery_interval_seconds())


def is_job_recoverable(job: dict[str, Any] | None) -> bool:
    if not isinstance(job, dict):
        return False
    normalized_status = str(job.get("status") or "").strip().lower()
    metrics = job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    result_state = str(metrics.get("result_state") or "").strip().lower()
    processing_stage = str(metrics.get("processing_stage") or "").strip().lower()
    error_message = str(job.get("error_message") or "").strip().lower()
    output_reference = str(job.get("output_reference") or "").strip()
    if normalized_status in _RECOVERABLE_STATUSES:
        return True
    if (
        normalized_status in {"running", "pending"}
        and is_job_stale(job)
        and (
            processing_stage == "ocr_pipeline"
            or result_state in {"processing", "awaiting_output"}
            or bool(output_reference)
        )
    ):
        return True
    if normalized_status in {"failed", "error"} and (
        result_state in _RECOVERABLE_RESULT_STATES
        or "ocr pipeline output not found" in error_message
    ):
        return True
    return False


def is_job_recovery_due(job: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    if not is_job_recoverable(job):
        return False
    due_at = get_job_next_recovery_at(job)
    if not isinstance(due_at, datetime):
        return True
    now_value = now or datetime.utcnow()
    return due_at <= now_value


def get_job_stale_at(job: dict[str, Any] | None) -> datetime | None:
    progress_updated_at = get_job_progress_updated_at(job)
    stale_minutes = get_stale_minutes()
    if stale_minutes <= 0 or not isinstance(progress_updated_at, datetime):
        return None
    return progress_updated_at + timedelta(minutes=stale_minutes)


def is_job_stale(job: dict[str, Any] | None) -> bool:
    stale_at = get_job_stale_at(job)
    if not isinstance(stale_at, datetime):
        return False
    return stale_at <= datetime.utcnow()


def has_recorded_execution_input(job: dict[str, Any]) -> bool:
    """Check the saved execution contract without resolving or repairing input."""
    from src.services.workflow_attempt_guard_service import (
        nonworkflow_execution_context, processing_start_read_input,
    )
    metrics = job.get("metrics")
    captured = metrics.get("nonworkflow_execution") if isinstance(metrics, dict) else None
    if not isinstance(captured, dict):
        return False
    order_id = captured.get("order_id")
    orders = captured.get("orders")
    if (not isinstance(order_id, str) or not order_id
        or not isinstance(orders, dict) or set(orders) != {order_id}
        or not isinstance(orders[order_id], dict)
        or captured.get("job_id") != job.get("id")
        or not isinstance(captured.get("execution_id"), str) or not captured["execution_id"]
        or captured.get("job_order_id") != job.get("order_id")
        or captured.get("input_reference") != job.get("input_reference")):
        return False
    lease = captured.get("ingest_lease")
    if lease is not None and (not isinstance(lease, dict)
        or not all(isinstance(lease.get(key), str) and lease[key]
                   for key in ("id", "generation", "claim_id", "input_digest"))
        or type(lease.get("attempts")) is not int or lease["attempts"] < 1):
        return False
    if (metrics.get("ingest_ocr_mode") is not None
        and captured.get("ingest_ocr_mode") != metrics["ingest_ocr_mode"]):
        return False
    try:
        # This is the same hash/shape/selected-source check used by execution.
        with nonworkflow_execution_context(captured):
            processing_start_read_input()
        if captured.get("ingest_ocr_mode") == "hakodate":
            from src.workers.ingest_mail_adapter import parse_ingest_payload
            from src.services.ingest_policy import require_explicit_ingest_week
            from src.services.ingest_job_service import payload_digest
            if not isinstance(lease, dict) or not isinstance(lease.get("payload"), dict):
                return False
            payload = parse_ingest_payload(lease["payload"])
            recorded = orders[order_id]
            order, document = recorded.get("order"), recorded.get("document")
            selected = captured.get("selected_pdf_receipt")
            if (payload.intake_protocol != "native_ingest_v2"
                or captured.get("intake_protocol") != payload.intake_protocol or payload.skip_ocr
                or payload_digest(lease["payload"]) != lease["input_digest"]
                or not isinstance(order, dict) or order.get("id") != order_id
                or order.get("week_code") != require_explicit_ingest_week(payload.week_hint)
                or not isinstance(document, dict) or not document.get("id")
                or order.get("current_document_id") != document["id"]
                or document.get("storage_uri") != payload.pdf_uri
                or document.get("source_email_id") != payload.message_id
                or captured["input_reference"] != payload.pdf_uri
                or not isinstance(selected, dict) or selected.get("document_id") != document["id"]
                or selected.get("order_id") != order_id
                or selected.get("sha256") != payload.content_sha256):
                return False
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return True


def describe_job_state(job: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(job, dict):
        return {
            "status": "idle",
            "progress_updated_at": None,
            "stale_at": None,
            "stale_threshold_seconds": max(get_stale_minutes(), 0) * 60,
            "job_id": None,
        }
    normalized_status = str(job.get("status") or "").strip().lower() or "idle"
    effective_status = normalized_status
    metrics = job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    missing_execution_input = (
        (metrics.get("request_mode") in {"ingest_first_pass", "ocr_rerun", "ocr_reparse", "llm_reparse"}
         or metrics.get("ingest_ocr_mode") == "hakodate")
        and metrics.get("workflow_attempt_id") is None
        and not has_recorded_execution_input(job)
    )
    if is_job_recoverable(job):
        effective_status = "recovering" if is_job_recovery_due(job) else "awaiting_output"
    elif normalized_status in {"running", "pending"} and is_job_stale(job):
        effective_status = "stalled"
    elif normalized_status in {"failed", "error"}:
        effective_status = "hard_failed"
    elif normalized_status in {"done", "success", "completed"}:
        effective_status = "done"
    if missing_execution_input:
        effective_status = "hard_failed"
    progress_updated_at = get_job_progress_updated_at(job)
    stale_at = get_job_stale_at(job) if effective_status == "stalled" else None
    recovery_due_at = get_job_next_recovery_at(job) if is_job_recoverable(job) else None
    return {
        "status": effective_status,
        "progress_updated_at": progress_updated_at.isoformat() if isinstance(progress_updated_at, datetime) else None,
        "stale_at": stale_at.isoformat() if isinstance(stale_at, datetime) else None,
        "recovery_due_at": (
            recovery_due_at.isoformat() if isinstance(recovery_due_at, datetime) else None
        ),
        "recovery_attempts": get_job_recovery_attempts(job),
        "stale_threshold_seconds": max(get_stale_minutes(), 0) * 60,
        "job_id": str(job.get("id") or "").strip() or None,
        "blockers": ["ocr_execution_input_required"] if missing_execution_input else [],
    }


def get_job(job_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        job = session.get(OcrJob, job_id)
        if not job:
            return None
        return _job_to_dict(job)


def get_jobs(job_ids: list[str]) -> dict[str, dict[str, Any]]:
    unique_ids = [job_id for job_id in dict.fromkeys(job_ids) if job_id]
    if not unique_ids:
        return {}
    with session_scope() as session:
        jobs = session.execute(
            select(OcrJob).where(OcrJob.id.in_(unique_ids))
        ).scalars().all()
        return {job.id: _job_to_dict(job) for job in jobs}


def get_recorded_order_job_bindings(order_ids: list[str], *, read_session=None) -> dict[str, dict[str, Any]]:
    """Read the workflow's explicit execution, never a name or latest-row guess."""
    from src.models.order import Order
    from src.models.order_workflow_state import OrderWorkflowState
    from src.models.document import OrderDocument
    from src.models.uploaded_pdf import UploadedPdf
    from src.models.order_version import OrderVersion
    from src.models.workflow_ocr_delivery import WorkflowOcrDelivery
    from src.services.workflow_attempt_guard_service import selected_version_snapshot, snapshots_equal

    identities = list(dict.fromkeys(value for value in order_ids if value))
    if not identities:
        return {}
    context = nullcontext(read_session) if read_session is not None else session_scope()
    with context as session:
        rows = session.execute(select(
            Order.id, Order.current_document_id, OrderWorkflowState.state,
            OrderWorkflowState.secondary_actions_json,
        ).select_from(Order).outerjoin(
            OrderWorkflowState, OrderWorkflowState.order_id == Order.id,
        ).where(Order.id.in_(identities))).all()
        records = {}
        for order_id, document_id, state, secondary in rows:
            meta = secondary.get("workflow_v2") if isinstance(secondary, dict) else None
            meta = meta if isinstance(meta, dict) else {}
            identity = meta.get("ocr_job_id")
            identity = identity if isinstance(identity, str) and identity else None
            records[order_id] = (document_id, state, meta, identity)
        job_ids = {record[3] for record in records.values() if record[3]}
        jobs = {job.id: job for job in session.execute(
            select(OcrJob).where(OcrJob.id.in_(job_ids)),
        ).scalars()} if job_ids else {}
        inputs = {}
        for job_id, job in jobs.items():
            metrics = job.metrics if isinstance(job.metrics, dict) else {}
            capture = metrics.get("nonworkflow_execution")
            capture = capture if isinstance(capture, dict) else {}
            if metrics.get("workflow_attempt_id") is None:
                orders = capture.get("orders")
                captured = orders.get(job.order_id) if isinstance(orders, dict) else None
                selected, version = capture.get("selected_pdf_receipt"), capture.get("selected_version_input")
            else:
                captured = metrics.get("reparse_read_input")
                selected, version = metrics.get("selected_pdf_receipt"), metrics.get("selected_version_input")
            inputs[job_id] = (captured, selected, version, capture)
        receipts = [value[1] for value in inputs.values() if isinstance(value[1], dict)]
        def string_ids(values):
            return {value for value in values if isinstance(value, str) and value}
        documents = {row.id: row for row in session.execute(select(OrderDocument).where(
            OrderDocument.id.in_(string_ids(value.get("document_id") for value in receipts)),
        )).scalars()} if receipts else {}
        uploads = {row.id: row for row in session.execute(select(UploadedPdf).where(
            UploadedPdf.id.in_(string_ids(value.get("uploaded_pdf_id") for value in receipts)),
        )).scalars()} if receipts else {}
        version_ids = string_ids(value[2].get("id") for value in inputs.values() if isinstance(value[2], dict))
        versions = {row.id: row for row in session.execute(select(OrderVersion).where(
            OrderVersion.id.in_(version_ids),
        )).scalars()} if version_ids else {}
        attempts = string_ids(job.metrics.get("workflow_attempt_id") for job in jobs.values()
                              if isinstance(job.metrics, dict))
        deliveries = {row.workflow_attempt_id: row for row in session.execute(select(
            WorkflowOcrDelivery.workflow_attempt_id, WorkflowOcrDelivery.order_id,
            WorkflowOcrDelivery.job_id, WorkflowOcrDelivery.selected_pdf_receipt,
            WorkflowOcrDelivery.request_identity["selected_version_input"].label("selected_version_input"),
            WorkflowOcrDelivery.request_identity["reparse_read_input"].label("reparse_read_input"),
        ).where(WorkflowOcrDelivery.workflow_attempt_id.in_(attempts)))} if attempts else {}
        bindings = {}
        for order_id, (document_id, state, meta, identity) in records.items():
            job = jobs.get(identity)
            error = None
            if identity is None:
                if ("ocr_job_id" in meta or "ocr_job_attempt_id" in meta
                        or state in {"ocr_running", "ocr_completed", "ocr_failed"}):
                    error = "ocr_job_not_recorded"
            elif job is None:
                error = "ocr_recorded_job_missing"
            elif job.order_id != order_id:
                error = "ocr_recorded_job_order_mismatch"
            elif not isinstance(job.metrics, dict):
                error = "ocr_recorded_job_input_missing"
            else:
                metrics = job.metrics
                expected = meta.get("ocr_job_attempt_id")
                actual = metrics.get("workflow_attempt_id")
                if (type(expected) is not type(actual) or expected != actual
                        or (expected is not None and (not isinstance(expected, str) or not expected))):
                    error = "ocr_recorded_job_attempt_mismatch"
                else:
                    captured, selected, selected_version, capture = inputs[identity]
                    if expected is None:
                        if (capture.get("job_id") != identity or capture.get("order_id") != order_id
                                or not has_recorded_execution_input(_job_to_dict(job))):
                            error = "ocr_recorded_job_input_missing"
                    else:
                        delivery = deliveries.get(expected)
                        if (delivery is None or delivery.order_id != order_id or delivery.job_id != identity
                                or not snapshots_equal(delivery.selected_pdf_receipt, selected)
                                or not snapshots_equal(delivery.selected_version_input, selected_version)
                                or not snapshots_equal(delivery.reparse_read_input, captured)):
                            error = "ocr_recorded_job_input_changed"
                    order_input = captured.get("order") if isinstance(captured, dict) else None
                    if error is None and (not isinstance(order_input, dict) or order_input.get("id") != order_id):
                        error = "ocr_recorded_job_input_missing"
                    if error is None and order_input.get("current_document_id") != document_id:
                        error = "ocr_recorded_job_document_changed"
                    if error is None and (not isinstance(selected, dict)
                            or selected.get("order_id") != order_id
                            or selected.get("storage_uri") != job.input_reference
                            or not all(isinstance(selected.get(key), str) and selected[key]
                                       for key in ("document_id", "uploaded_pdf_id", "message_id", "sha256"))):
                        error = "ocr_recorded_job_pdf_mismatch"
                    if error is None:
                        document = documents.get(selected.get("document_id"))
                        uploaded = uploads.get(selected.get("uploaded_pdf_id"))
                        if (document is None or uploaded is None
                                or (document.storage_uri, document.source_email_id) != (selected.get("storage_uri"), selected.get("message_id"))
                                or (uploaded.storage_uri, uploaded.message_id, uploaded.content_sha256)
                                != (selected.get("storage_uri"), selected.get("message_id"), selected.get("sha256"))):
                            error = "ocr_recorded_job_pdf_mismatch"
                        elif selected_version is None:
                            if selected.get("document_id") != document_id:
                                error = "ocr_recorded_job_document_changed"
                        else:
                            version_id = selected_version.get("id") if isinstance(selected_version, dict) else None
                            version = versions.get(version_id) if isinstance(version_id, str) else None
                            version_identity = ("id", "order_id", "version_no", "document_id", "message_id", "storage_uri",
                                                "facility_code", "week_code", "received_at")
                            actual_version = selected_version_snapshot(version) if version is not None else {}
                            if (version is None or version.order_id != order_id
                                    or version.document_id != selected.get("document_id")
                                    or not snapshots_equal(
                                        {key: actual_version.get(key) for key in version_identity},
                                        {key: selected_version.get(key) for key in version_identity},
                                    )):
                                error = "ocr_recorded_job_version_changed"
            bindings[order_id] = {
                "job_id": identity,
                "job": _job_to_dict(job) if job is not None and error is None else None,
                "error": error,
            }
        return bindings


def get_recorded_order_job(order_id: str) -> dict[str, Any] | None:
    binding = get_recorded_order_job_bindings([order_id]).get(order_id)
    if not binding:
        return None
    if binding["error"]:
        raise ValueError(binding["error"])
    return binding["job"]


def recorded_order_job_fields(binding: dict[str, Any] | None) -> dict[str, Any]:
    binding = binding or {}
    error = binding.get("error")
    result = {"ocr_job_id": binding.get("job_id") if binding.get("job") is not None else None,
              "ocr_job_binding_error": error}
    if error:
        result.update(ocr_status="failed", ocr_error=error, ocr_job_binding_error=error)
    return result


def get_latest_order_job(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        job = (
            session.execute(
                select(OcrJob)
                .where(OcrJob.order_id == normalized_order_id)
                .order_by(OcrJob.updated_at.desc(), OcrJob.created_at.desc(), OcrJob.id.desc())
            )
            .scalars()
            .first()
        )
        return _job_to_dict(job) if job is not None else None


def list_recoverable_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as session:
        jobs = (
            session.execute(
                select(OcrJob)
                .where(
                    or_(
                        OcrJob.status.in_(["awaiting_output", "recovering", "running", "pending"]),
                        # Terminal hard-failed jobs stay in the DB for audit, but must not
                        # consume recovery slots before newer output-waiting jobs are seen.
                        and_(
                            OcrJob.status.in_(["failed", "error"]),
                            OcrJob.error_message.ilike("%ocr pipeline output not found%"),
                        ),
                    )
                )
                .order_by(OcrJob.updated_at.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [
            serialized
            for serialized in (_job_to_dict(job) for job in jobs)
            if is_job_recoverable(serialized)
        ]


def create_job(
    job_id: str,
    input_reference: str,
    status: str = "running",
    *,
    order_id: str | None = None,
    uploaded_pdf_id: str | None = None,
    order_document_id: str | None = None,
    input_artifact_digest: str | None = None,
    template_version_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    with session_scope() as session:
        existing = session.get(OcrJob, job_id)
        if existing:
            return _job_to_dict(existing), False
        now = datetime.utcnow()
        job = OcrJob(
            id=job_id,
            order_id=str(order_id or "").strip() or None,
            uploaded_pdf_id=str(uploaded_pdf_id or "").strip() or None,
            order_document_id=str(order_document_id or "").strip() or None,
            input_artifact_digest=str(input_artifact_digest or "").strip() or None,
            status=status,
            input_reference=input_reference,
            template_version_id=str(template_version_id or "").strip() or None,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        return _job_to_dict(job), True


def lock_workflow_reparse_job(
    session: Any,
    *,
    job_id: str,
    order_id: str,
) -> tuple[OcrJob | None, str | None]:
    """Lock an existing reservation without adopting or exposing another owner."""
    job = session.execute(
        select(OcrJob).where(OcrJob.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is not None and (not job.order_id or job.order_id != order_id):
        return None, "ocr_job_order_mismatch"
    return job, None


def reserve_workflow_reparse_job(
    session: Any,
    *,
    job_id: str,
    order_id: str,
    input_reference: str,
    template_version_id: str | None,
    metrics: dict[str, Any],
    stale_action: str,
    force: bool,
) -> tuple[OcrJob | None, str | None]:
    """Reserve the OCR job inside the workflow command transaction.

    No caller may create or mark a job running before the workflow revision CAS
    has accepted the same request.
    """
    if stale_action not in {"retry", "wait"}:
        return None, "stale_action_invalid"
    existing, owner_error = lock_workflow_reparse_job(session, job_id=job_id, order_id=order_id)
    if owner_error:
        return None, owner_error
    serialized = _job_to_dict(existing) if existing is not None else None
    active = bool(
        is_order_reparse_job(serialized, order_id)
        and str(existing.status or "").strip().lower() in {"running", "pending", "awaiting_output", "recovering"}
    ) if existing is not None else False
    stale = active and is_job_stale(serialized)
    if stale and stale_action == "wait" and not force:
        return None, "ocr_rerun_stale_wait"
    if active and not stale and not force:
        return None, "ocr_rerun_in_progress"

    now = datetime.utcnow()
    job = existing
    if job is None:
        job = OcrJob(
            id=job_id,
            order_id=order_id,
            input_reference=input_reference,
            template_version_id=template_version_id,
            status="running",
            created_at=now,
            updated_at=now,
        )
        session.add(job)
    else:
        job.order_id = order_id
        job.input_reference = input_reference
        job.template_version_id = template_version_id
        job.status = "running"
        job.error_message = None
        job.template_id = None
        job.output_reference = None
        job.updated_at = now
    job.metrics = dict(metrics)
    session.flush()
    return job, None


_JOB_STAGE_METRIC_FIELDS = frozenset({
    "processing_stage", "result_state", "stage_updated_at", "request_mode", "status",
    "ocr_slot_wait_started_at", "ocr_slot_acquired_at", "ocr_slot_wait_seconds", "ocr_execution_slot",
    "requested_provider", "effective_provider", "llm_assist", "quality_track",
    "reparse_origin", "feedback_retry_depth", "error", "error_detail",
    "error_user_message", "recovery_action", "recognition_checkpoint", "stage_timeout_seconds",
    "awaiting_output_since", "ocr_started_at", "next_recovery_at", "failed_cells",
    "facility_id", "preferred_template_id", "preferred_template_ids", "output_reference",
    "confirmed_lines_retained", "reused_first_pass", "pipeline_run_skipped",
    "first_pass_wait_seconds", "actual_provider", "upstream_error_detail", "context_conflict",
    "provider", "row_count", "line_count", "finish_reason", "truncated_output",
    "rows_replaced_with_pipeline", "llm_quantity_only_merge", "structural_row_projection",
    "primary_model", "repair_pass_applied", "repair_pass_reason", "repair_pass_error",
    "repair_pass_model", "quality_error", "quality_detail", "before_count", "after_count",
    "changed", "reject_reasons", "validation_detail", "llm_cost", "llm_audit",
    "llm_pre_inference_audit", "llm_second_pass_audit", "llm_feedback_second_pass_applied",
    "llm_feedback_second_pass_error", "blank_anchor_realign", "auto_fallback_applied",
    "auto_fallback_from_provider", "auto_fallback_reason", "pdf_variant_used",
    "pdf_variant_fallback_reason",
})


def merge_own_job_metrics_patch(
    current: dict | None, patch: dict | None, *, own_outcome: bool = False,
) -> dict:
    """Patch telemetry on an already fenced row; ownership is never caller data."""
    retained = dict(current or {})
    protected = {
        "job_id", "order_id", "input_reference", "first_pass_source", "reparse_read_input",
        "selected_version_input", "selected_pdf_receipt", "selected_pdf_error", "source_failure_receipt", "selected_document_id", "selected_document_version",
        "selected_document_is_current", "selected_document_storage_uri", "evidence_run_id",
        "pending_receipt", "first_pass_terminal_receipt", "non_ocr_terminal_receipt", "rerun_terminal_receipt",
        "auto_recovery_count",
        "pipeline_delivery_receipt", *REPARSE_REQUEST_FIELDS.values(),
    }
    for key, value in (patch or {}).items():
        if key in protected or key.startswith(("workflow_", "nonworkflow_")):
            continue
        if not own_outcome and key not in _JOB_STAGE_METRIC_FIELDS:
            continue
        if key in {"awaiting_output_since", "ocr_started_at"} and key in retained:
            continue
        retained[key] = value
    return retained


def update_job(job_id: str, *, actual_order_id: str | None = None, recovery_count_increment: bool = False, **updates: Any) -> dict[str, Any] | None:
    with session_scope() as session:
        execution_input = current_nonworkflow_execution()
        job = session.get(OcrJob, job_id)
        if job is None:
            return None
        if (job.metrics or {}).get("rerun_terminal_receipt") is not None:
            return None
        if execution_input is None and (job.metrics or {}).get("workflow_attempt_id") is not None:
            return None
        if actual_order_id is None:
            actual_order_id = execution_input.get("order_id") if execution_input is not None else job.order_id
        if actual_order_id is None and execution_input is not None and not lock_nonworkflow_execution_job(session, execution_input):
            return None
        if actual_order_id is not None and not lock_actual_write_ownership(
            session,
            job_id=job_id,
            order_id=actual_order_id,
            workflow_attempt_id=None,
            expected_workflow_revision=None,
        ):
            return None
        session.refresh(job)
        if execution_input is None and (job.metrics or {}).get("nonworkflow_execution") is not None:
            return None
        if (job.metrics or {}).get("rerun_terminal_receipt") is not None:
            return None
        if execution_input is not None:
            if "metrics" in updates and updates["metrics"] is None:
                updates.pop("metrics")
            if "input_reference" in updates and updates["input_reference"] != execution_input.get("input_reference"):
                return None
            if "order_id" in updates and updates["order_id"] != execution_input.get("job_order_id"):
                return None
            if isinstance(updates.get("metrics"), dict):
                if updates["metrics"].get("workflow_attempt_id") is not None:
                    return None
        if "metrics" in updates:
            if updates["metrics"] is None:
                updates.pop("metrics")
            elif not isinstance(updates["metrics"], dict):
                return None
            else:
                updates["metrics"] = merge_own_job_metrics_patch(job.metrics, updates["metrics"])
        if recovery_count_increment:
            metrics = updates.get("metrics", dict(job.metrics or {}))
            metrics["auto_recovery_count"] = int((job.metrics or {}).get("auto_recovery_count") or 0) + 1
            updates["metrics"] = metrics
        for key, value in updates.items():
            if hasattr(job, key):
                setattr(job, key, value)
        job.updated_at = datetime.utcnow()
        session.add(job)
        return _job_to_dict(job)


def update_job_for_workflow_attempt(
    job_id: str,
    workflow_attempt_id: str,
    *,
    order_id: str | None = None,
    expected_workflow_revision: int | None = None,
    allow_terminal_metadata: bool = False,
    **updates: Any,
) -> tuple[dict[str, Any] | None, bool]:
    """Atomically update only the claimed attempt of this reusable job id."""
    with session_scope() as session:
        if not lock_current_workflow_attempt(
            session,
            job_id=job_id,
            workflow_attempt_id=workflow_attempt_id,
            order_id=order_id,
            expected_workflow_revision=expected_workflow_revision,
        ):
            return None, False
        values = {
            key: value
            for key, value in updates.items()
            if key != "metrics" and hasattr(OcrJob, key)
        }
        job = session.get(OcrJob, job_id)
        if job is None:
            return None, False
        if updates.get("metrics") is not None and not isinstance(updates["metrics"], dict):
            return None, False
        if isinstance(updates.get("metrics"), dict):
            values["metrics"] = merge_own_job_metrics_patch(job.metrics, updates["metrics"])
        values["updated_at"] = datetime.utcnow()
        terminal_statuses = ["done", "failed", "error", "completed", "success"]
        conditions = [
            OcrJob.id == job_id,
            OcrJob.metrics["workflow_attempt_id"].as_string() == workflow_attempt_id,
        ]
        # Progress is never allowed to overwrite a terminal result. Only the
        # workflow completion transaction may opt in to terminal metadata.
        if not allow_terminal_metadata:
            conditions.append(OcrJob.status.notin_(terminal_statuses))
        statement = update(OcrJob).where(*conditions).values(**values)
        result = session.execute(statement)
        if result.rowcount != 1:
            return None, False
        session.refresh(job)
        return _job_to_dict(job), True
