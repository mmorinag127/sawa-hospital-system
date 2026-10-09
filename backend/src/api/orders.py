import json
from typing import Literal
from datetime import datetime, timedelta, date as dt_date
from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks, Header, Query, Request
from fastapi.responses import Response, JSONResponse
from loguru import logger
from pydantic import BaseModel

from src.services import (
    order_service,
    config_service,
    candidate_resolution_service,
    workflow_state_service,
    uploaded_pdf_service,
    total_service,
)
from src.services import order_workflow_v2_service
from src.services import workflow_ocr_delivery_dispatch_service
from src.services import ocr_job_service
from src.services import shipping_status_store
from src.services.ocr_job_service import (
    create_job as create_ocr_job,
    describe_job_state as describe_ocr_job_state,
    get_job_request_mode,
    get_job as get_ocr_job,
    get_latest_order_job,
    is_order_reparse_job as is_order_reparse_ocr_job,
    get_jobs as get_ocr_jobs,
    get_job_stale_at as get_ocr_job_stale_at,
    get_stale_minutes as get_ocr_job_stale_minutes,
    is_job_stale as is_ocr_job_stale,
)
from src.services.ocr_execution_lock_service import OcrExecutionSlotTimeout, acquire_ocr_execution_slot
from src.api.auth import require_role

router = APIRouter()


class DailyOutputOverrideUpsertBody(BaseModel):
    date: str
    facility_id: str
    menu_name: str
    diet_type: str | None = None
    daypart: str | None = None
    menu_category: str | None = None
    unit_type: str
    qty_per_serving: float
    note: str | None = None
    acknowledge_ambiguous: bool = False


class DailyOutputOverrideBulkUpsertBody(BaseModel):
    date: str
    menu_name: str
    daypart: str | None = None
    menu_category: str | None = None
    unit_type: str
    qty_per_serving: float
    note: str | None = None


class WeekArchiveBody(BaseModel):
    week_value: str
    order_ids: list[str] | None = None
    purge_runtime_state: bool = False


class WorkflowV2ContextConfirmBody(BaseModel):
    facility_id: str
    week_start: str
    week_end: str
    template_id: str | None = None


class WorkflowV2OcrRunBody(BaseModel):
    stale_action: str | None = None
    force: bool = False
    mode: str | None = None
    document_id: str | None = None
    ocr_prompt: str | None = None
    prompt_preset: str | None = None
    ocr_provider: str | None = None
    ocr_model: str | None = None
    llm_assist: bool | None = None


class WorkflowV2SheetSaveBody(BaseModel):
    sheet: dict
    edited_by: str | None = None


class WorkflowV2SheetAutoEditBody(BaseModel):
    sheet: dict | None = None
    model: str | None = None
    use_llm: bool = True


class WorkflowV2SheetAnomalyBody(BaseModel):
    sheet: dict | None = None
    model: str | None = None
    use_llm: bool | None = None


class WorkflowV2SheetAnomalyDismissBody(BaseModel):
    sheet: dict
    warning: dict


class WorkflowV2SheetReviewConfirmBody(BaseModel):
    sheet: dict


class WorkflowV2SheetPreSaveStatusBody(BaseModel):
    sheet: dict


class WorkflowV2ExpandedCellCopyModeBody(BaseModel):
    mode: str


class WorkflowV2FacilityTemplateColumnsBody(BaseModel):
    columns: list[dict]


class WorkflowV2QuadReviewBody(BaseModel):
    decision: str
    quad_px: list[list[float]]
    source_receipt: dict | None = None


class WorkflowV2HeaderAxisReviewBody(BaseModel):
    corrected_xs: list[float]
    coordinate_space: dict
    source_receipt: dict | None = None


class WorkflowV2RowAxisReviewBody(BaseModel):
    corrected_ys: list[float]
    coordinate_space: dict
    source_receipt: dict | None = None


class WorkflowV2FinalConfirmBody(BaseModel):
    confirmed_by: str | None = None


def _is_read_timeout_error(value: object) -> bool:
    return isinstance(value, str) and "read operation timed out" in value.lower()


def _is_reparse_stale_timeout_error(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized.startswith("reparse_stale_timeout>")


def _is_terminal_reparse_error(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    if not normalized:
        return False
    return (
        normalized.startswith("sheet_")
        or normalized.startswith("lines_empty")
        or normalized.startswith("llm_cost_")
        or normalized.startswith("main_ocr_failed:")
        or normalized.startswith("first_pass_ocr_missing")
    )


def _is_order_reparse_job(job: object, order_id: str) -> bool:
    return is_order_reparse_ocr_job(job if isinstance(job, dict) else None, order_id)


def _should_preserve_terminal_reparse_state(job: dict | None, order_id: str) -> bool:
    if not isinstance(job, dict) or not _is_order_reparse_job(job, order_id):
        return False
    normalized_status = str(job.get("status") or "").strip().lower()
    if normalized_status in {"running", "pending", "awaiting_output", "recovering"}:
        return False
    metrics = job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    result_state = str(metrics.get("result_state") or "").strip().lower()
    error_message = job.get("error_message")
    return _is_terminal_reparse_error(error_message) or result_state in {
        "hard_failed",
        "draft_ready_blocked",
    }


def _derive_status_from_payload(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if isinstance(status, str):
        normalized = status.strip().lower()
        if normalized and normalized not in {"running", "pending", "awaiting_output", "recovering"}:
            return status
    if payload.get("pages") or payload.get("table_raw") or payload.get("rows"):
        return "success"
    return None


def _has_persisted_ocr_evidence(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    table_raw = payload.get("table_raw")
    if isinstance(table_raw, str) and table_raw.strip():
        return True
    if isinstance(payload.get("pages"), list) and payload.get("pages"):
        return True
    if isinstance(payload.get("tables"), list) and payload.get("tables"):
        return True
    return False


def _is_running_status(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in {
        "running",
        "pending",
        "awaiting_output",
        "recovering",
    }


def _get_ocr_stale_minutes() -> int:
    return get_ocr_job_stale_minutes()


def _is_job_stale(job: dict | None) -> bool:
    return is_ocr_job_stale(job)


def _mark_stale_order_reparse_job(order: dict, job: dict | None, *, force: bool = False) -> dict | None:
    """Project a stale diagnostic without completing or replacing the job."""
    if not isinstance(job, dict):
        return job
    order_id = str(order.get("id") or "").strip()
    if not order_id or not _is_order_reparse_job(job, order_id):
        return job
    if not _is_running_status(job.get("status")):
        return job
    if force or _is_job_stale(job):
        order["ocr_stale_diagnostic"] = {
            "error": f"reparse_stale_timeout>{_get_ocr_stale_minutes()}m",
            "job_id": job.get("id"),
            "processing_stage": (job.get("metrics") or {}).get("processing_stage"),
        }
    return job


def _apply_stale_ocr_status(order: dict, job: dict | None) -> dict | None:
    if not job:
        return job
    status = (job.get("status") or order.get("ocr_status") or "").lower()
    if status not in {"running", "pending", "awaiting_output", "recovering"}:
        if job.get("status"):
            order["ocr_status"] = job.get("status")
        error_message = job.get("error_message")
        if error_message:
            order["ocr_error"] = error_message
        if job.get("metrics"):
            order["ocr_metrics"] = job.get("metrics")
        if job.get("updated_at"):
            order["ocr_updated_at"] = job.get("updated_at")
        return job
    updated_at = job.get("updated_at")
    if not isinstance(updated_at, datetime):
        return job
    stale_minutes = _get_ocr_stale_minutes()
    if stale_minutes > 0 and updated_at < datetime.utcnow() - timedelta(minutes=stale_minutes):
        order["ocr_stale_diagnostic"] = {
            "error": f"timeout>{stale_minutes}m",
            "job_id": job.get("id"),
            "updated_at": updated_at,
        }
    return job


def _should_prefer_cached_status(order_id: str, current_status: object, job: dict | None) -> bool:
    normalized = str(current_status or "").strip().lower()
    if not normalized:
        return True
    if normalized in {"running", "pending", "stalled", "awaiting_output", "recovering"}:
        return True
    if normalized == "failed" and job and not _is_order_reparse_job(job, order_id):
        return True
    return False


def _apply_cached_status_override(order: dict, order_id: str, job: dict | None) -> dict | None:
    cached_payload = order_service.get_cached_ocr_payload(order_id)
    cached_status = _derive_status_from_payload(cached_payload)
    current_status = order.get("ocr_status")
    if not cached_status or not _should_prefer_cached_status(order_id, current_status, job):
        return job
    if job and _is_order_reparse_job(job, order_id):
        return job
    order["ocr_cache_diagnostic"] = {
        "status": cached_status,
        "error": cached_payload.get("error") if isinstance(cached_payload, dict) else None,
    }
    return job


def _attach_order_review_summary(
    order: dict,
    *,
    cached_payload: dict | None = None,
    ocr_job: dict | None = None,
    lightweight: bool = False,
) -> None:
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        return
    effective_ocr_status = order.get("ocr_status")
    effective_ocr_metrics = order.get("ocr_metrics")
    if isinstance(ocr_job, dict) and _is_order_reparse_job(ocr_job, order_id):
        if not effective_ocr_status:
            effective_ocr_status = ocr_job.get("status")
        if not effective_ocr_metrics:
            effective_ocr_metrics = ocr_job.get("metrics")
    workflow = order.get("workflow_state") if isinstance(order.get("workflow_state"), dict) else None
    if lightweight:
        job_state = describe_ocr_job_state(ocr_job if isinstance(ocr_job, dict) else None)
        apply_gate = (workflow or {}).get("apply_gate") if isinstance(workflow, dict) else None
        apply_gate = apply_gate if isinstance(apply_gate, dict) else {}
        order.update(
            {
                "ocr_review_state": str((workflow or {}).get("state") or "").strip() or None,
                "ocr_review_stage": str((workflow or {}).get("primary_action") or "").strip() or None,
                "ocr_review_badges": [],
                "ocr_has_saved_draft": None,
                "ocr_draft_updated_at": None,
                "ocr_draft_revision_id": str((workflow or {}).get("current_sheet_revision_id") or "").strip() or None,
                "current_sheet_revision_id": str((workflow or {}).get("current_sheet_revision_id") or "").strip() or None,
                "ocr_draft_row_count": None,
                "ocr_draft_newer_than_lines": None,
                "ocr_auto_apply_blocked": None,
                "ocr_reject_reasons": [],
                "ocr_last_reparse_error": None,
                "ocr_reparse_status": str(job_state.get("status") or "idle"),
                "ocr_reparse_health": str(job_state.get("status") or "idle"),
                "ocr_reparse_stale_at": job_state.get("stale_at"),
                "ocr_reparse_stale_threshold_seconds": job_state.get("stale_threshold_seconds"),
                "ocr_reparse_last_job_id": (
                    str(ocr_job.get("id") or "").strip()
                    if isinstance(ocr_job, dict) and _is_order_reparse_job(ocr_job, order_id)
                    else None
                ),
                "ocr_reparse_last_error_code": (
                    str(ocr_job.get("error_message") or "").strip() or None if isinstance(ocr_job, dict) else None
                ),
                "ocr_can_apply_draft": False,
                "ocr_apply_blockers": list(apply_gate.get("apply_blockers") or []),
                "ocr_apply_blocker_details": list(apply_gate.get("apply_blocker_details") or []),
                "ocr_can_confirm": False,
                "ocr_confirm_blockers": list(apply_gate.get("confirm_blockers") or []),
                "ocr_confirm_warnings": list(apply_gate.get("confirm_warnings") or []),
                "ocr_confirm_blocker_details": list(apply_gate.get("confirm_blocker_details") or []),
                "ocr_confirm_warning_details": list(apply_gate.get("confirm_warning_details") or []),
                "ocr_processing_stage": (
                    effective_ocr_metrics.get("processing_stage") if isinstance(effective_ocr_metrics, dict) else None
                ),
                "ocr_result_state": (
                    effective_ocr_metrics.get("result_state") if isinstance(effective_ocr_metrics, dict) else None
                ),
                "ocr_confirmed_lines_retained": (
                    effective_ocr_metrics.get("confirmed_lines_retained")
                    if isinstance(effective_ocr_metrics, dict)
                    else None
                ),
                "ocr_revision_count": None,
                "ocr_revision_last_id": None,
            }
        )
        return
    review = order_service.get_order_review_summary(
        order_id,
        lines_updated_at=order.get("lines_updated_at"),
        ocr_status=effective_ocr_status,
        cached_payload=cached_payload,
        ocr_metrics=effective_ocr_metrics,
        order_status=order.get("status"),
        current_sheet_context=None,
        sheet_gate=(workflow or {}).get("apply_gate") if isinstance(workflow, dict) else None,
    )
    if (
        str(review.get("ocr_review_state") or "").strip() in {"", "none"}
        and not list(review.get("ocr_apply_blockers") or [])
        and not list(((workflow or {}).get("apply_gate") or {}).get("apply_blockers") or [])
    ):
        current_sheet_context = order_service.get_current_sheet_context(
            order_id,
            refresh_draft_from_semantic=True,
            upgrade_generic_from_sheet=True,
            backfill_from_revision=False,
        )
        review = order_service.get_order_review_summary(
            order_id,
            lines_updated_at=order.get("lines_updated_at"),
            ocr_status=effective_ocr_status,
            cached_payload=cached_payload,
            ocr_metrics=effective_ocr_metrics,
            order_status=order.get("status"),
            current_sheet_context=current_sheet_context,
            sheet_gate=(workflow or {}).get("apply_gate") if isinstance(workflow, dict) else None,
        )
    job_state = describe_ocr_job_state(ocr_job if isinstance(ocr_job, dict) else None)
    review["ocr_reparse_health"] = str(job_state.get("status") or "idle")
    review["ocr_reparse_stale_at"] = job_state.get("stale_at")
    review["ocr_reparse_stale_threshold_seconds"] = job_state.get("stale_threshold_seconds")
    review["ocr_reparse_last_job_id"] = (
        str(ocr_job.get("id") or "").strip()
        if isinstance(ocr_job, dict) and _is_order_reparse_job(ocr_job, order_id)
        else None
    )
    if not review.get("ocr_reparse_last_error_code") and isinstance(ocr_job, dict):
        review["ocr_reparse_last_error_code"] = str(ocr_job.get("error_message") or "").strip() or None
    order.update(review)


def _attach_order_workflow_context(
    order: dict,
    *,
    refresh: bool = False,
) -> None:
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        return
    workflow = order_service.get_order_workflow_state(order_id, refresh=refresh)
    if not isinstance(workflow, dict):
        return
    order["workflow_state"] = workflow
    if isinstance(workflow.get("candidate_resolution"), dict):
        order["candidate_resolution"] = workflow["candidate_resolution"]
    if isinstance(workflow.get("critical_decisions"), list):
        order["critical_decisions"] = workflow["critical_decisions"]
    if isinstance(workflow.get("apply_gate"), dict):
        order["apply_gate"] = workflow["apply_gate"]


def _apply_job_status_to_order(order: dict, job: dict | None) -> None:
    if not isinstance(job, dict):
        return
    error_message = job.get("error_message")
    metrics = job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    metrics_error = str(metrics.get("error") or "").strip() or None
    order["ocr_status"] = job.get("status")
    order.pop("ocr_error", None)
    if metrics_error and _is_running_status(job.get("status")):
        order["ocr_error"] = metrics_error
    elif error_message:
        order["ocr_error"] = error_message
    if metrics:
        order["ocr_metrics"] = metrics
    order["ocr_updated_at"] = job.get("updated_at")


def _align_order_ocr_readiness_with_workflow(order: dict) -> None:
    workflow = order.get("workflow_state") if isinstance(order.get("workflow_state"), dict) else None
    if not isinstance(workflow, dict):
        return
    blockers = [
        str(item or "").strip()
        for item in (workflow.get("blockers_json") or [])
        if str(item or "").strip()
    ]
    has_evidence_blocker = any(
        code in {"evidence_view_unavailable", "evidence_edit_unavailable"}
        for code in blockers
    )
    if not has_evidence_blocker:
        return
    current_status = str(order.get("ocr_status") or "").strip().lower()
    current_error = str(order.get("ocr_error") or "").strip().lower()
    if current_status in {"done", "success"} and not current_error:
        return
    order_id = str(order.get("id") or "").strip()
    if order_id:
        try:
            current_payload, current_error_code = order_service._get_ocr_output_without_legacy_edits(  # noqa: SLF001
                order_id,
                persist_cache=False,
            )
        except Exception:
            current_payload, current_error_code = None, "ocr_output_probe_failed"
        if isinstance(current_payload, dict) and current_error_code is None:
            return
    reparse_state = workflow.get("reparse_state") if isinstance(workflow.get("reparse_state"), dict) else {}
    reparse_status = str((reparse_state or {}).get("status") or "").strip().lower()
    if reparse_status in {"running", "pending", "awaiting_output", "recovering"}:
        return
    order["ocr_status"] = "blocked"
    order["ocr_error"] = "ocr_evidence_recovery_required"
    order["ocr_processing_stage"] = "evidence_unavailable"
    order["ocr_result_state"] = "blocked"
    order["ocr_updated_at"] = None
    order["ocr_metrics"] = {
        "error": "ocr_evidence_recovery_required",
        "processing_stage": "evidence_unavailable",
        "result_state": "blocked",
    }


def _needs_list_candidate_summary(order: dict) -> bool:
    if not isinstance(order, dict):
        return False
    facility_missing = not str(order.get("facility") or "").strip()
    week_value = str(order.get("week_value") or order.get("week") or "").strip()
    week_range_missing = not week_value or "@" not in week_value
    return facility_missing or week_range_missing


def _attach_order_list_candidate_summary(
    order: dict,
    *,
    cached_payload: dict | None,
) -> None:
    if not _needs_list_candidate_summary(order):
        return
    effective_facility = str(order.get("facility") or "").strip() or None
    effective_week = str(order.get("week_value") or order.get("week") or "").strip() or None
    if not isinstance(cached_payload, dict):
        cached_payload = None
    message_id = str(order.get("message_id") or "").strip()
    from src.services.canonical_facility_store import CanonicalStoreError
    receipt_error = None
    reference_hints = {}
    if message_id:
        uploaded_pdf = uploaded_pdf_service.get_uploaded_pdf_by_message_id(message_id)
        if isinstance(uploaded_pdf, dict):
            reference_hints = {key: uploaded_pdf.get(key) for key in ("facility_hint", "week_hint")}
            try:
                ingest_payload = uploaded_pdf_service.build_ingest_payload(uploaded_pdf)
            except (ValueError, CanonicalStoreError) as exc:
                receipt_error = str(exc)
            else:
                if not effective_facility:
                    effective_facility = str(ingest_payload.get("facility_hint") or "").strip() or None
                if not effective_week:
                    effective_week = str(ingest_payload.get("week_hint") or "").strip() or None
        else:
            receipt_error = "uploaded_pdf_original_selection_missing"
    else:
        receipt_error = "uploaded_pdf_original_selection_missing"
    if receipt_error:
        order["candidate_resolution"] = {
            "reference_hints": reference_hints,
            "resolutions": {key: {
                "decision_type": key, "resolved_value": None, "resolved_label": None,
                "confidence": "unknown", "blocked": True,
                "blocked_reasons": [receipt_error], "requires_user_choice": True,
                "candidates": [],
            } for key in ("facility", "week")},
        }
        summary = order["candidate_resolution"]
        summary["gate_summary"] = candidate_resolution_service.summarize_resolution_gate(summary["resolutions"])
        summary["requires_user_choice"] = True
        summary["confidence_band"] = "unknown"
        summary["critical_choices"] = []
        return
    if cached_payload is None and not effective_facility and not effective_week:
        return
    summary = candidate_resolution_service.resolve_order_list_candidates(
        facility_code=effective_facility,
        week_code=effective_week,
        received_at=order.get("received_at"),
        evidence_payload=cached_payload,
    )
    resolutions = summary.get("resolutions") if isinstance(summary, dict) else None
    if not isinstance(resolutions, dict):
        return
    week = resolutions.get("week")
    facility = resolutions.get("facility")
    if not isinstance(week, dict) and not isinstance(facility, dict):
        return
    order["candidate_resolution"] = summary


def _is_active_order_reparse_job(job: dict | None, order_id: str) -> bool:
    if not isinstance(job, dict):
        return False
    if not _is_order_reparse_job(job, order_id):
        return False
    normalized_status = str(job.get("status") or "").strip().lower()
    if normalized_status not in {"running", "pending", "awaiting_output", "recovering"}:
        return False
    if _is_job_stale(job):
        return False
    return True


def _heal_active_order_reparse_job_from_workflow(order_id: str, job: dict | None) -> dict | None:
    # Result owners commit workflow and job together. A read projection cannot
    # manufacture a terminal job from another workflow/evidence snapshot.
    return job


def _enqueue_order_reparse_job(
    order_id: str,
    *,
    ocr_prompt: str | None = None,
    prompt_preset: str | None = None,
    ocr_provider: str | None = None,
    ocr_model: str | None = None,
    llm_assist: bool = True,
    force: bool = False,
    stale_action: str = "retry",
    request_mode: str,
    selected_document_id: str | None = None,
    expected_revision: int,
) -> dict:
    order, selected_version = _resolve_order_document_version(order_id, selected_document_id)
    if not order.get("facility"):
        raise HTTPException(status_code=400, detail="facility missing")
    if not config_service.get_facility_config(order.get("facility")):
        raise HTTPException(status_code=404, detail="facility not found")

    ocr_job_id = f"OCR-{order_id}"
    input_reference = str(selected_version.get("storage_uri") or "").strip()
    selected_document_metrics = _document_selection_metrics(selected_version)
    queued = _workflow_v2_or_404(
        order_workflow_v2_service.mark_ocr_run_queued(
            order_id,
            ocr_job_id,
            input_reference=input_reference,
            delivery_selected_document_id=selected_document_id,
            request_mode=request_mode,
            job_metrics={
                "processing_stage": "queued",
                "result_state": "processing",
                "confirmed_lines_retained": bool(order.get("lines_updated_at")),
                **order_service._build_reparse_request_metadata(
                    ocr_prompt=ocr_prompt, prompt_preset=prompt_preset,
                    requested_provider=ocr_provider, ocr_model=ocr_model, llm_assist=llm_assist,
                ),
                **selected_document_metrics,
            },
            stale_action=stale_action,
            force=force,
            expected_revision=expected_revision,
        )
    )
    queued_workflow = queued["workflow"]
    _dispatch_workflow_ocr_delivery(queued.get("delivery"))
    return {"accepted": True, "ocr_job_id": ocr_job_id, "workflow": queued_workflow}


def _complete_background_ocr_failure(
    order_id: str,
    job_id: str | None,
    exc: Exception,
    *,
    expected_revision: int | None,
    workflow_attempt_id: str | None,
) -> dict:
    receipt, completion_error = _complete_workflow_ocr_run(
        order_id, job_id, error=f"{type(exc).__name__}: {exc}", expected_revision=expected_revision,
        workflow_attempt_id=workflow_attempt_id,
    )
    if receipt is None or receipt.get("status") != "failed":
        raise ValueError(completion_error or "ocr_rerun_terminal_persist_refused") from exc
    return {"terminal_receipt": receipt}


def _run_reparse_background(
    order_id: str,
    ocr_prompt: str | None,
    prompt_preset: str | None = None,
    ocr_provider: str | None = None,
    ocr_model: str | None = None,
    llm_assist: bool = False,
    selected_document_id: str | None = None,
    expected_revision: int | None = None,
    workflow_attempt_id: str | None = None,
    ocr_job_id: str | None = None,
) -> dict:
    try:
        result, error = order_service.reparse_order(
            order_id, ocr_prompt=ocr_prompt, prompt_preset=prompt_preset,
            ocr_provider=ocr_provider, ocr_model=ocr_model, llm_assist=llm_assist,
            selected_document_id=selected_document_id,
            workflow_attempt_id=workflow_attempt_id,
            expected_workflow_revision=expected_revision, ocr_job_id=ocr_job_id,
        )
    except Exception as exc:  # noqa: BLE001
        return _complete_background_ocr_failure(
            order_id, ocr_job_id, exc, expected_revision=expected_revision,
            workflow_attempt_id=workflow_attempt_id,
        )
    if error is None and order_service.is_own_pending_ocr_result(
        result, order_id=order_id, job_id=ocr_job_id,
        workflow_attempt_id=workflow_attempt_id, workflow_revision=expected_revision,
    ):
        return result
    receipt = (result or {}).get("terminal_receipt")
    if isinstance(receipt, dict):
        if (receipt.get("job_id") != ocr_job_id or receipt.get("order_id") != order_id
                or receipt.get("workflow_attempt_id") != workflow_attempt_id
                or receipt.get("workflow_revision") != expected_revision
                or receipt.get("status") not in {"done", "failed"}):
            raise ValueError("reparse_terminal_receipt_mismatch")
        return {"terminal_receipt": receipt}
    if not error:
        raise ValueError("reparse_persisted_result_missing")
    receipt, completion_error = _complete_workflow_ocr_run(
        order_id, ocr_job_id,
        evidence_run_id=(result or {}).get("reparse_evidence_run_id"),
        error=error, expected_revision=expected_revision,
        workflow_attempt_id=workflow_attempt_id,
    )
    if receipt is None:
        raise ValueError(completion_error or "reparse_terminal_persist_refused")
    return {"terminal_receipt": receipt}


def _run_ocr_rerun_background(
    order_id: str,
    ocr_job_id: str,
    selected_document_id: str | None = None,
    expected_revision: int | None = None,
    workflow_attempt_id: str | None = None,
) -> dict:
    started_at = datetime.utcnow()
    def patch_metrics(patch):
        _job, accepted = ocr_job_service.update_job_for_workflow_attempt(
            ocr_job_id, workflow_attempt_id, order_id=order_id,
            expected_workflow_revision=expected_revision,
            metrics={**patch, "stage_updated_at": datetime.utcnow().isoformat()},
        )
        if not accepted:
            raise ValueError("workflow_ocr_delivery_progress_write_refused")
    try:
        patch_metrics({"processing_stage": "waiting_for_ocr_slot", "result_state": "processing",
            "request_mode": "ocr_rerun", "ocr_started_at": started_at.isoformat(),
            "ocr_slot_wait_started_at": started_at.isoformat()})
        with acquire_ocr_execution_slot(order_id=order_id, job_id=ocr_job_id) as slot:
            acquired_at = datetime.utcnow()
            patch_metrics({"processing_stage": "ocr_slot_acquired", "result_state": "processing",
                "request_mode": "ocr_rerun", "ocr_slot_acquired_at": acquired_at.isoformat(),
                "ocr_slot_wait_seconds": round((acquired_at - started_at).total_seconds(), 3),
                "ocr_execution_slot": slot})
            with order_service.workflow_ocr_write_context(workflow_attempt_id, expected_revision, ocr_job_id, order_id):
                evidence, error = order_service.rerun_ocr_evidence_only(
                    order_id, job_id=ocr_job_id, project_sheet=False,
                    refresh_workflow=False, selected_document_id=selected_document_id,
                )
    except OcrExecutionSlotTimeout as exc:
        receipt, completion_error = _complete_workflow_ocr_run(
            order_id, ocr_job_id, error="ocr_execution_slot_timeout",
            expected_revision=expected_revision, workflow_attempt_id=workflow_attempt_id,
            metrics_patch={"error_detail": str(exc), "request_mode": "ocr_rerun",
                "processing_stage": "ocr_execution_slot_timeout",
                "ocr_slot_wait_started_at": started_at.isoformat()},
        )
        if receipt is None:
            raise ValueError(completion_error or "ocr_rerun_terminal_persist_refused") from exc
        return {"terminal_receipt": receipt}
    except Exception as exc:  # noqa: BLE001
        return _complete_background_ocr_failure(
            order_id, ocr_job_id, exc, expected_revision=expected_revision,
            workflow_attempt_id=workflow_attempt_id,
        )
    if error is None and order_service.is_own_pending_ocr_result(
        evidence, order_id=order_id, job_id=ocr_job_id,
        workflow_attempt_id=workflow_attempt_id, workflow_revision=expected_revision,
    ):
        return evidence
    receipt = (evidence or {}).get("terminal_receipt")
    if (not isinstance(receipt, dict) or receipt.get("job_id") != ocr_job_id
            or receipt.get("order_id") != order_id
            or receipt.get("workflow_attempt_id") != workflow_attempt_id
            or receipt.get("workflow_revision") != expected_revision
            or receipt.get("status") not in {"done", "failed"}):
        raise ValueError("ocr_rerun_terminal_receipt_missing")
    return {"terminal_receipt": receipt}


@router.get("", dependencies=[Depends(require_role("operator"))])
def list_orders(
    status: str | None = None,
    include_ocr: bool | None = None,
    include_archived: bool | None = None,
    include_runtime: bool | None = None,
    include_candidate_summary: bool = False,
    limit: int | None = Query(default=None, ge=1, le=1000),
    before_received_at: str | None = None,
    before_id: str | None = None,
):
    include_archived_flag = True if include_archived is None else include_archived
    page = order_service.list_orders_page(
        status=status,
        include_archived=include_archived_flag,
        limit=limit,
        before_received_at=before_received_at,
        before_id=before_id,
    )
    orders = page["orders"]
    include_runtime_flag = (include_ocr is not None) if include_runtime is None else include_runtime
    if not include_runtime_flag:
        return page
    order_ids = [str(order.get("id") or "").strip() for order in orders if str(order.get("id") or "").strip()]
    lightweight_mode = include_ocr is False
    cache_map = order_service._load_order_ocr_cache_map(order_ids) if (not lightweight_mode or include_candidate_summary) else {}
    if lightweight_mode:
        workflow_map = workflow_state_service.list_workflow_states(order_ids)
        reparse_jobs = get_ocr_jobs([f"OCR-{order_id}" for order_id in order_ids])
        for order in orders:
            order_id = str(order.get("id") or "").strip()
            cached_payload = cache_map.get(order_id)
            workflow = workflow_map.get(order_id)
            if isinstance(workflow, dict):
                order["workflow_state"] = workflow
            review_job = reparse_jobs.get(f"OCR-{order_id}")
            if review_job:
                _apply_job_status_to_order(order, review_job)
            elif cached_payload:
                cached_status = _derive_status_from_payload(cached_payload)
                if cached_status and not order.get("ocr_status"):
                    order["ocr_cache_diagnostic"] = {"status": cached_status}
            if isinstance(cached_payload, dict):
                pages = cached_payload.get("pages")
                if isinstance(pages, list) and pages:
                    order["ocr_pages_count"] = len(pages)
            if include_candidate_summary:
                _attach_order_list_candidate_summary(
                    order,
                    cached_payload=cached_payload,
                )
    else:
        job_ids = [order.get("ocr_job_id") for order in orders if order.get("ocr_job_id")]
        jobs = get_ocr_jobs(job_ids)
        for order in orders:
            job_id = order.get("ocr_job_id")
            job = jobs.get(job_id) if job_id else None
            if job:
                _apply_job_status_to_order(order, job)
            job = _apply_cached_status_override(order, str(order.get("id") or ""), job)
            job = _apply_stale_ocr_status(order, job)
    reparse_jobs = {} if lightweight_mode else get_ocr_jobs(
        list(
            {
                token
                for order in orders
                for token in (
                    str(order.get("ocr_job_id") or "").strip(),
                    f"OCR-{str(order.get('id') or '').strip()}",
                )
                if token
            }
        )
    )
    for order in orders:
        order_id = str(order.get("id") or "").strip()
        review_job = (
            reparse_jobs.get(f"OCR-{order_id}")
            or reparse_jobs.get(str(order.get("ocr_job_id") or "").strip())
        )
        if review_job:
            review_job = _mark_stale_order_reparse_job(order, review_job)
        if not lightweight_mode:
            _attach_order_review_summary(
                order,
                cached_payload=cache_map.get(order_id),
                ocr_job=review_job,
            )
            _attach_order_workflow_context(order, refresh=False)
    page["orders"] = orders
    return page


@router.post("/cache-refresh", dependencies=[Depends(require_role("admin"))])
def refresh_orders_cache(status: str | None = None, include_archived: bool | None = None):
    include_archived_flag = True if include_archived is None else include_archived
    count = order_service.refresh_orders_cache(status=status, include_archived=include_archived_flag)
    return {"refreshed": count}


def _parse_iso_date(value: str) -> dt_date:
    try:
        return dt_date.fromisoformat(value)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid date format") from exc


@router.get("/by-line-date", dependencies=[Depends(require_role("operator"))])
def list_orders_by_line_date(date: str, facility: str | None = None, status: str | None = None):
    target_date = _parse_iso_date(date)
    orders = order_service.list_orders_by_line_date(target_date, facility_id=facility, status=status)
    return {"date": target_date.isoformat(), "orders": orders}


@router.get("/daily-bags", dependencies=[Depends(require_role("operator"))])
def get_daily_bags(date: str, facility: str | None = None, status: str | None = None):
    target_date = _parse_iso_date(date)
    return order_service.get_daily_bag_summary(
        target_date,
        facility_id=facility,
        status=status,
        include_expanded_copy=False,
    )


@router.get("/daily-bags/audit", dependencies=[Depends(require_role("operator"))])
def get_daily_bags_audit(
    date: str,
    facility: str | None = None,
    status: str | None = None,
    use_ai: bool = False,
):
    target_date = _parse_iso_date(date)
    return order_service.get_daily_bag_audit(
        target_date,
        facility_id=facility,
        status=status,
        use_ai=use_ai,
    )


def _daily_output_section(value: object = None, error: Exception | None = None) -> dict:
    if error is None:
        return {"status": "fulfilled", "data": value}
    return {
        "status": "rejected",
        "error": {
            "type": error.__class__.__name__,
            "message": str(error),
        },
    }


@router.get("/daily-output-context", dependencies=[Depends(require_role("operator"))])
def get_daily_output_context(
    date: str,
    facility: str | None = None,
    status: str | None = None,
    section: Literal["all", "primary", "bags", "totals"] = "all",
):
    target_date = _parse_iso_date(date)
    result: dict[str, object] = {
        "date": target_date.isoformat(),
        "facility": facility,
        "status": status,
        "sections": {},
    }
    sections = result["sections"]
    orders = []
    if section in {"all", "primary"}:
        try:
            orders = order_service.list_orders_by_line_date(target_date, facility_id=facility, status=status)
            sections["orders"] = _daily_output_section(
                {"date": target_date.isoformat(), "orders": orders}
            )
        except Exception as exc:  # noqa: BLE001
            sections["orders"] = _daily_output_section(error=exc)

        try:
            sections["meal_counts"] = _daily_output_section(
                total_service.build_daily_meal_counts(target_date)
            )
        except Exception as exc:  # noqa: BLE001
            sections["meal_counts"] = _daily_output_section(error=exc)

    if section in {"all", "bags"}:
        try:
            summary = order_service.get_daily_bag_summary(
                target_date,
                facility_id=facility,
                status=status,
                include_expanded_copy=False,
            )
            sections["daily_bags"] = _daily_output_section(summary)
        except Exception as exc:  # noqa: BLE001
            sections["daily_bags"] = _daily_output_section(error=exc)
            sections["daily_bags_audit"] = _daily_output_section(error=exc)
        else:
            try:
                sections["daily_bags_audit"] = _daily_output_section(
                    order_service.get_daily_bag_audit(
                        target_date,
                        facility_id=facility,
                        status=status,
                        use_ai=False,
                        summary=summary,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                sections["daily_bags_audit"] = _daily_output_section(error=exc)

    if section in {"all", "totals"}:
        try:
            sections["totals"] = _daily_output_section(
                {
                    "rows": total_service.build_totals(target_date, target_date),
                    "date_from": target_date.isoformat(),
                    "date_to": target_date.isoformat(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            sections["totals"] = _daily_output_section(error=exc)

    result["ok"] = all(
        isinstance(section, dict) and section.get("status") == "fulfilled"
        for section in sections.values()
    )
    result["order_count"] = len(orders)
    return result


@router.get("/daily-output-overrides", dependencies=[Depends(require_role("operator"))])
def get_daily_output_overrides(
    date: str,
    daypart: str,
    menu_name: str,
    menu_category: str | None = None,
):
    target_date = _parse_iso_date(date)
    try:
        return order_service.list_daily_output_override_editor_rows(
            target_date,
            daypart=daypart,
            menu_name=menu_name,
            menu_category=menu_category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/daily-output-overrides/upsert", dependencies=[Depends(require_role("operator"))])
def upsert_daily_output_override(payload: DailyOutputOverrideUpsertBody):
    target_date = _parse_iso_date(payload.date)
    try:
        return order_service.upsert_daily_output_portion_override(
            output_date=target_date,
            facility_id=str(payload.facility_id or "").strip(),
            menu_name=str(payload.menu_name or "").strip(),
            diet_type=payload.diet_type,
            daypart=payload.daypart,
            menu_category=payload.menu_category,
            unit_type=str(payload.unit_type or "").strip(),
            qty_per_serving=payload.qty_per_serving,
            note=payload.note,
            updated_by="operator",
            acknowledge_ambiguous=bool(payload.acknowledge_ambiguous),
        )
    except ValueError as exc:
        detail = str(exc)
        try:
            parsed = json.loads(detail)
        except Exception:
            parsed = detail
        status_code = 409 if isinstance(parsed, dict) else 400
        raise HTTPException(status_code=status_code, detail=parsed) from exc


@router.post("/daily-output-overrides/upsert-bulk", dependencies=[Depends(require_role("operator"))])
def upsert_daily_output_override_bulk(payload: DailyOutputOverrideBulkUpsertBody):
    target_date = _parse_iso_date(payload.date)
    try:
        return order_service.upsert_daily_output_portion_override_bulk(
            output_date=target_date,
            menu_name=str(payload.menu_name or "").strip(),
            daypart=payload.daypart,
            menu_category=payload.menu_category,
            unit_type=str(payload.unit_type or "").strip(),
            qty_per_serving=payload.qty_per_serving,
            note=payload.note,
            updated_by="operator",
        )
    except ValueError as exc:
        detail = str(exc)
        try:
            parsed = json.loads(detail)
        except Exception:
            parsed = detail
        status_code = 409 if isinstance(parsed, dict) else 400
        raise HTTPException(status_code=status_code, detail=parsed) from exc


@router.delete("/daily-output-overrides/{override_id}", dependencies=[Depends(require_role("operator"))])
def delete_daily_output_override(override_id: str):
    try:
        deleted = order_service.delete_daily_output_portion_override(override_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=json.loads(str(exc))) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="override not found")
    return deleted


@router.post("/archive-week", dependencies=[Depends(require_role("operator"))])
def archive_orders_for_week(payload: WeekArchiveBody):
    result, error = order_service.archive_orders_for_week(
        str(payload.week_value or "").strip(),
        order_ids=[str(item or "").strip() for item in (payload.order_ids or []) if str(item or "").strip()],
        archived_by="operator",
        purge_runtime_state=bool(payload.purge_runtime_state),
    )
    if error == "invalid_week":
        raise HTTPException(status_code=400, detail={"error": error})
    if error == "week_not_found":
        raise HTTPException(status_code=404, detail={"error": error})
    if error:
        raise HTTPException(status_code=400, detail={"error": error})
    return result


@router.post("/{order_id}/archive", dependencies=[Depends(require_role("operator"))])
def archive_single_order(order_id: str):
    result, error = order_service.archive_order(order_id, archived_by="operator")
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail={"error": error})
    if error:
        raise HTTPException(status_code=400, detail={"error": error})
    return result


@router.post("/{order_id}/unarchive", dependencies=[Depends(require_role("operator"))])
def unarchive_single_order(order_id: str):
    result, error = order_service.unarchive_order(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail={"error": error})
    if error:
        raise HTTPException(status_code=400, detail={"error": error})
    return result


@router.post("/unarchive-week", dependencies=[Depends(require_role("operator"))])
def unarchive_orders_for_week(payload: WeekArchiveBody):
    result, error = order_service.unarchive_orders_for_week(
        str(payload.week_value or "").strip(),
        order_ids=[str(item or "").strip() for item in (payload.order_ids or []) if str(item or "").strip()],
    )
    if error == "invalid_week":
        raise HTTPException(status_code=400, detail={"error": error})
    if error == "week_not_found":
        raise HTTPException(status_code=404, detail={"error": error})
    if error:
        raise HTTPException(status_code=400, detail={"error": error})
    return result


@router.get("/{order_id}", dependencies=[Depends(require_role("operator"))])
def get_order(order_id: str):
    order = order_service.get_order_by_id(order_id, allow_unresolved_menu_amount=True)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    job_id = order.get("ocr_job_id")
    job = get_latest_order_job(order_id)
    if not job and job_id:
        job = get_ocr_job(job_id)
    if job:
        # GET /orders/{id} is a read projection. Finished OCR jobs are
        # reconciled by workers or explicit commands, not by page reads.
        _apply_job_status_to_order(order, job)
    job = _apply_stale_ocr_status(order, job)
    # Order detail is the first request that gates the page-level Loading state.
    # Keep it read-only/fast; explicit workflow endpoints and mutating actions
    # are responsible for refreshing persisted workflow/current-sheet state.
    _attach_order_workflow_context(order, refresh=False)
    if job:
        refreshed_job = get_ocr_job(str(job.get("id") or "")) or job
        if refreshed_job is not job:
            job = refreshed_job
        _apply_job_status_to_order(order, job)
        job = _apply_stale_ocr_status(order, job)
    _attach_order_review_summary(order, ocr_job=job, lightweight=True)
    return order


def _shipping_facility_names(order: dict) -> list[str]:
    facility_id = str(order.get("facility") or "").strip()
    if not facility_id:
        return []
    fac = config_service.get_facility_config(facility_id) or {}
    names: list[str] = []
    for value in (
        fac.get("facility_name"),
        fac.get("name"),
        *(fac.get("aliases") or [] if isinstance(fac.get("aliases"), list) else []),
    ):
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def _load_document_bytes(uri: str, receipt: dict) -> tuple[bytes, str, str]:
    from src.services.storage_service import load_selected_pdf_bytes
    return load_selected_pdf_bytes(uri, receipt), "original", "source_uri"


@router.get("/{order_id}/shipping-statuses", dependencies=[Depends(require_role("operator"))])
def get_order_shipping_statuses(order_id: str, limit: int = 10, max_age_days: int = 30):
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    facility_names = _shipping_facility_names(order)
    return shipping_status_store.get_latest_statuses_for_facility(
        facility_names,
        limit=max(1, min(limit, 50)),
        max_age_days=max(1, min(max_age_days, 90)),
    )


@router.get("/{order_id}/document", dependencies=[Depends(require_role("operator"))])
def download_document(order_id: str, document_id: str | None = None):
    _order, selected_version = _resolve_order_document_version(order_id, document_id)
    uri = selected_version.get("storage_uri")
    if not uri:
        raise HTTPException(status_code=404, detail="document not found")
    try:
        from src.db import session_scope
        from src.models.order import Order
        from src.services.workflow_attempt_guard_service import capture_selected_pdf_receipt
        with session_scope() as session:
            receipt = capture_selected_pdf_receipt(session, session.get(Order, order_id), selected_version)
        data, source_kind, source_variant = _load_document_bytes(uri, receipt)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "X-Sawa-Document-Source": source_kind,
            "X-Sawa-Document-Variant": source_variant,
            "X-Sawa-Document-Id": str(selected_version.get("document_id") or ""),
            "X-Sawa-Document-Version": str(selected_version.get("version_no") or ""),
        },
    )


@router.get("/{order_id}/ocr-raw", dependencies=[Depends(require_role("operator"))])
def get_ocr_raw(order_id: str):
    data, error = order_service.get_ocr_raw_text(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error == "ocr_raw_not_found":
        raise HTTPException(status_code=404, detail="ocr raw not found")
    return data


@router.get("/{order_id}/ocr-output", dependencies=[Depends(require_role("operator"))])
def get_ocr_output(order_id: str):
    data, error = order_service.get_ocr_output(order_id, persist_cache=False)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"ocr_job_not_found", "ocr_output_not_found"}:
        raise HTTPException(status_code=404, detail="ocr output not found")
    if error == "ocr_output_pending":
        return JSONResponse(status_code=202, content={"pending": True})
    if error == "ocr_evidence_recovery_required":
        return JSONResponse(
            status_code=409,
            content={"recovery_required": True, "detail": "ocr evidence recovery required"},
        )
    if error == "ocr_output_invalid":
        raise HTTPException(status_code=500, detail="ocr output invalid")
    return data


@router.get("/{order_id}/hakodate-overlay-preview", dependencies=[Depends(require_role("operator"))])
def get_hakodate_overlay_preview(order_id: str):
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return order_service.get_hakodate_overlay_preview(order_id)


@router.get("/{order_id}/hakodate-job-status", dependencies=[Depends(require_role("operator"))])
def get_hakodate_job_status(order_id: str):
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return order_service.get_hakodate_pipeline_job_status(order_id)


@router.get("/{order_id}/bags", dependencies=[Depends(require_role("operator"))])
def get_bags(order_id: str):
    data, error = order_service.get_bag_summary(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error:
        raise HTTPException(status_code=500, detail="bag summary failed")
    return data


@router.post("/{order_id}/bags/rebuild", dependencies=[Depends(require_role("operator"))])
def rebuild_bag_summary(order_id: str):
    _ = order_id
    _raise_legacy_order_workflow_gone("bags/rebuild")


@router.get("/{order_id}/evidence", dependencies=[Depends(require_role("operator"))])
def get_ocr_evidence(order_id: str):
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    evidence = order_service._resolve_active_ocr_evidence_run(order_id)
    if not isinstance(evidence, dict):
        raise HTTPException(status_code=404, detail="ocr evidence not found")
    return evidence


def _workflow_v2_or_404(result: tuple[dict | None, str | None]) -> dict:
    payload, error = result
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail=error)
    if error == "validation_error":
        detail = dict(payload or {})
        detail.setdefault("error", "validation_error")
        detail.setdefault("message", "施設テンプレート列の検証に失敗しました。エラー内容を確認して修正してください。")
        raise HTTPException(status_code=400, detail=detail)
    if error == order_workflow_v2_service.WORKFLOW_REVISION_REQUIRED:
        raise HTTPException(status_code=428, detail={"error": error, "workflow": payload or {}})
    if error == order_workflow_v2_service.WORKFLOW_REVISION_INVALID:
        raise HTTPException(status_code=422, detail={"error": error, "workflow": payload or {}})
    if error == order_workflow_v2_service.WORKFLOW_REVISION_CONFLICT:
        raise HTTPException(status_code=409, detail={"error": error, "workflow": payload or {}})
    if error in {"ocr_rerun_in_progress", "ocr_rerun_stale_wait"}:
        raise HTTPException(status_code=409, detail={"error": error, "workflow": payload or {}})
    if error:
        raise HTTPException(status_code=400, detail=error)
    return payload or {}


def _workflow_completion_revision(job: dict | None) -> int | None:
    metrics = job.get("metrics") if isinstance(job, dict) else None
    value = metrics.get("workflow_completion_revision") if isinstance(metrics, dict) else None
    return value if isinstance(value, int) and value >= 1 else None


def _complete_workflow_ocr_run(
    order_id: str,
    job_id: str,
    *,
    expected_revision: int | None,
    workflow_attempt_id: str | None,
    evidence_run_id: str | None = None,
    error: str | None = None,
    metrics_patch: dict | None = None,
) -> tuple[dict | None, str | None]:
    if type(expected_revision) is not int or expected_revision < 1 or not isinstance(workflow_attempt_id, str) or not workflow_attempt_id:
        return None, order_workflow_v2_service.WORKFLOW_REVISION_REQUIRED
    try:
        with order_service.workflow_ocr_write_context(workflow_attempt_id, expected_revision, job_id, order_id):
            with order_service._ocr_result_transaction() as session:
                receipt = order_service._complete_ocr_rerun_in_session(
                    session, order_id, job_id,
                    {"id": evidence_run_id} if evidence_run_id is not None else None,
                    error=error, failure_metrics_override=True, metrics_patch={
                        **({"processing_stage": "failed", "result_state": "hard_failed"} if error else {}),
                        **(metrics_patch or {}),
                    },
                )
                if (
                    receipt.get("job_id") != job_id or receipt.get("order_id") != order_id
                    or receipt.get("workflow_attempt_id") != workflow_attempt_id
                    or receipt.get("workflow_revision") != expected_revision
                    or receipt.get("evidence_run_id") != evidence_run_id
                    or receipt.get("status") not in {"done", "failed"}
                ):
                    raise ValueError("workflow_terminal_receipt_mismatch")
        return receipt, receipt.get("error")
    except ValueError as exc:
        return None, str(exc)


def _dispatch_workflow_ocr_delivery(delivery: dict | None) -> None:
    """Transport an already committed intent; transport acknowledgement is not OCR success."""
    delivery_id = str((delivery or {}).get("id") or "").strip()
    generation = (delivery or {}).get("generation")
    if not delivery_id or type(generation) is not int:
        raise HTTPException(status_code=500, detail={"error": "workflow_ocr_delivery_missing"})
    try:
        workflow_ocr_delivery_dispatch_service.dispatch(delivery_id, generation)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail={"error": "workflow_ocr_delivery_dispatch_failed", "delivery_id": delivery_id},
        ) from exc


def _require_workflow_if_match(if_match: str | None = Header(default=None, alias="If-Match")) -> int:
    value = str(if_match or "").strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if not value or not value.isdigit():
        raise HTTPException(status_code=428, detail={"error": order_workflow_v2_service.WORKFLOW_REVISION_REQUIRED})
    return int(value)


def _raise_workflow_v2_ocr_prerequisite_error(error: str, workflow: dict | None = None) -> None:
    normalized = str(error or "").strip()
    if normalized in {"context_not_confirmed", "facility_template_unresolved"}:
        raise HTTPException(status_code=400, detail=normalized)
    messages = {
        "menu_entries_missing": "対象週の月次メニューが未登録です。メニューを登録してからOCRを実行してください。",
        "monthly_menu_object_missing": "対象月の月次メニューが未登録です。メニューを登録してからOCRを実行してください。",
        "monthly_menu_lookup_failed": "対象週の月次メニューを解決できません。メニュー登録を確認してください。",
        "monthly_menu_facility_scope_missing": "対象施設の月次メニュー差分を解決できません。メニュー設定を確認してください。",
        "week_unresolved": "週次が未確定です。Step1で週次を確定してください。",
    }
    raise HTTPException(
        status_code=409,
        detail={
            "error": normalized or "ocr_prerequisite_unresolved",
            "message": messages.get(normalized, "OCR前提条件が未解決です。Step1の施設・週次・メニュー設定を確認してください。"),
            "workflow": workflow or {},
        },
    )


def _raise_legacy_order_workflow_gone(endpoint: str) -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "error": "legacy_order_workflow_disabled",
            "endpoint": endpoint,
            "message": "注文処理は workflow-v2 に移行しました。この旧 order workflow endpoint は使用できません。",
            "replacement": "workflow-v2",
        },
    )


def _resolve_order_document_version(order_id: str, document_id: str | None = None) -> tuple[dict, dict]:
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    selected_document_id = str(document_id or "").strip()
    versions = [item for item in (order.get("versions") or []) if isinstance(item, dict)]
    if selected_document_id:
        for version in versions:
            if str(version.get("document_id") or "").strip() == selected_document_id:
                if not str(version.get("storage_uri") or "").strip():
                    raise HTTPException(status_code=404, detail="document not found")
                return order, version
        raise HTTPException(status_code=404, detail="document version not found")
    current_version = order.get("current_version") if isinstance(order.get("current_version"), dict) else None
    if current_version and str(current_version.get("storage_uri") or "").strip():
        return order, current_version
    uri = str(order.get("document") or "").strip()
    if not uri:
        raise HTTPException(status_code=404, detail="document not found")
    return order, {
        "document_id": order.get("document_id"),
        "version_no": None,
        "storage_uri": uri,
        "is_current": True,
    }


def _document_selection_metrics(version: dict) -> dict:
    return {
        "selected_version_input": version.get("selected_version_input"),
        "selected_document_id": str(version.get("document_id") or "").strip() or None,
        "selected_document_version": version.get("version_no"),
        "selected_document_is_current": bool(version.get("is_current")),
    }


def _enqueue_workflow_v2_evidence_rerun(
    order_id: str,
    *,
    stale_action: str = "retry",
    force: bool = False,
    selected_document_id: str | None = None,
    expected_revision: int,
) -> dict:
    if stale_action not in {"retry", "wait"}:
        raise HTTPException(status_code=400, detail="stale_action must be retry or wait")

    order, selected_version = _resolve_order_document_version(order_id, selected_document_id)
    if not order.get("facility"):
        raise HTTPException(status_code=400, detail="facility missing")
    if not config_service.get_facility_config(order.get("facility")):
        raise HTTPException(status_code=404, detail="facility not found")

    ocr_job_id = f"OCR-{order_id}"
    input_reference = str(selected_version.get("storage_uri") or "").strip()
    selected_document_metrics = _document_selection_metrics(selected_version)
    run_requested_at = datetime.utcnow().isoformat()
    queued = _workflow_v2_or_404(
        order_workflow_v2_service.mark_ocr_run_queued(
            order_id,
            ocr_job_id,
            input_reference=input_reference,
            request_mode="ocr_rerun",
            delivery_selected_document_id=selected_document_id,
            job_metrics={
                "workflow_version": "v2",
                "processing_stage": "queued",
                "result_state": "processing",
                "confirmed_lines_retained": bool(order.get("lines_updated_at")),
                "ocr_started_at": run_requested_at,
                "ocr_finished_at": None,
                "ocr_elapsed_seconds": None,
                **selected_document_metrics,
            },
            stale_action=stale_action,
            force=force,
            expected_revision=expected_revision,
        )
    )
    queued_workflow = queued["workflow"]
    _dispatch_workflow_ocr_delivery(queued.get("delivery"))
    return {"accepted": True, "ocr_job_id": ocr_job_id, "workflow": queued_workflow}


@router.get("/{order_id}/workflow-v2", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.get_workflow(order_id))


@router.post("/{order_id}/workflow-v2/context", dependencies=[Depends(require_role("operator"))])
def confirm_order_workflow_v2_context(
    order_id: str, body: WorkflowV2ContextConfirmBody, expected_revision: int = Depends(_require_workflow_if_match)
):
    return _workflow_v2_or_404(
        order_workflow_v2_service.confirm_context(
            order_id=order_id,
            facility_id=body.facility_id,
            week_start=body.week_start,
            week_end=body.week_end,
            template_id=body.template_id,
            expected_revision=expected_revision,
        )
    )


@router.post(
    "/{order_id}/workflow-v2/ocr-runs",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("operator"))],
)
def run_order_workflow_v2_ocr(
    order_id: str,
    body: WorkflowV2OcrRunBody | None = None,
    expected_revision: int = Depends(_require_workflow_if_match),
):
    stale_action = str((body.stale_action if body else None) or "retry").strip().lower()
    force = bool(body.force) if body else False
    mode = str((body.mode if body else None) or "hakodate").strip().lower()
    prerequisite_workflow, prerequisite_error = order_workflow_v2_service.ensure_ocr_prerequisites(
        order_id, expected_revision=expected_revision
    )
    if prerequisite_error:
        _raise_workflow_v2_ocr_prerequisite_error(prerequisite_error, prerequisite_workflow)
    if mode == "llm":
        result = _enqueue_order_reparse_job(
            order_id,
            ocr_prompt=(body.ocr_prompt.strip() if body and isinstance(body.ocr_prompt, str) and body.ocr_prompt.strip() else None),
            prompt_preset=(body.prompt_preset.strip().lower() if body and isinstance(body.prompt_preset, str) and body.prompt_preset.strip() else None),
            ocr_provider=(body.ocr_provider.strip().lower() if body and isinstance(body.ocr_provider, str) and body.ocr_provider.strip() else None),
            ocr_model=(body.ocr_model.strip() if body and isinstance(body.ocr_model, str) and body.ocr_model.strip() else None),
            llm_assist=True if body is None or body.llm_assist is None else bool(body.llm_assist),
            force=force,
            stale_action=stale_action,
            request_mode="llm_reparse",
            selected_document_id=(body.document_id if body else None),
            expected_revision=prerequisite_workflow["revision"],
        )
        result["mode"] = "llm_reparse"
        return result
    return _enqueue_workflow_v2_evidence_rerun(
        order_id,
        stale_action=stale_action,
        force=force,
        selected_document_id=(body.document_id if body else None),
        expected_revision=prerequisite_workflow["revision"],
    )


@router.get("/{order_id}/workflow-v2/quad-review", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_quad_review(order_id: str, document_id: str | None = None):
    return _workflow_v2_or_404(order_workflow_v2_service.get_quad_review(order_id, document_id=document_id))


@router.put("/{order_id}/workflow-v2/quad-review", dependencies=[Depends(require_role("operator"))])
def save_order_workflow_v2_quad_review(order_id: str, body: WorkflowV2QuadReviewBody, expected_revision: int = Depends(_require_workflow_if_match)):
    return _workflow_v2_or_404(
        order_workflow_v2_service.save_quad_review_decision(
            order_id,
            decision=body.decision,
            quad_px=body.quad_px,
            source_receipt=body.source_receipt,
            expected_revision=expected_revision,
        )
    )


@router.get("/{order_id}/workflow-v2/header-axis-review", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_header_axis_review(order_id: str, document_id: str | None = None):
    return _workflow_v2_or_404(order_workflow_v2_service.get_header_axis_review(order_id, document_id=document_id))


@router.put("/{order_id}/workflow-v2/header-axis-review", dependencies=[Depends(require_role("operator"))])
def save_order_workflow_v2_header_axis_review(order_id: str, body: WorkflowV2HeaderAxisReviewBody, expected_revision: int = Depends(_require_workflow_if_match)):
    return _workflow_v2_or_404(
        order_workflow_v2_service.save_header_axis_review_decision(
            order_id,
            corrected_xs=body.corrected_xs,
            coordinate_space=body.coordinate_space,
            source_receipt=body.source_receipt,
            expected_revision=expected_revision,
        )
    )


@router.get("/{order_id}/workflow-v2/row-axis-review", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_row_axis_review(order_id: str, document_id: str | None = None):
    return _workflow_v2_or_404(order_workflow_v2_service.get_row_axis_review(order_id, document_id=document_id))


@router.put("/{order_id}/workflow-v2/row-axis-review", dependencies=[Depends(require_role("operator"))])
def save_order_workflow_v2_row_axis_review(order_id: str, body: WorkflowV2RowAxisReviewBody, expected_revision: int = Depends(_require_workflow_if_match)):
    return _workflow_v2_or_404(
        order_workflow_v2_service.save_row_axis_review_decision(
            order_id,
            corrected_ys=body.corrected_ys,
            coordinate_space=body.coordinate_space,
            source_receipt=body.source_receipt,
            expected_revision=expected_revision,
        )
    )


@router.get("/{order_id}/workflow-v2/ocr-results", dependencies=[Depends(require_role("operator"))])
def list_order_workflow_v2_ocr_results(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.list_ocr_results(order_id))


@router.post("/{order_id}/workflow-v2/ocr-results/{ocr_result_id}/select", dependencies=[Depends(require_role("operator"))])
def select_order_workflow_v2_ocr_result(
    order_id: str, ocr_result_id: str, expected_revision: int = Depends(_require_workflow_if_match)
):
    return _workflow_v2_or_404(
        order_workflow_v2_service.select_ocr_result(order_id, ocr_result_id, expected_revision=expected_revision)
    )


@router.delete("/{order_id}/workflow-v2/ocr-results/{ocr_result_id}", dependencies=[Depends(require_role("operator"))])
def delete_order_workflow_v2_ocr_result(order_id: str, ocr_result_id: str, expected_revision: int = Depends(_require_workflow_if_match)):
    return _workflow_v2_or_404(order_workflow_v2_service.delete_ocr_result(order_id, ocr_result_id, expected_revision=expected_revision))


@router.get("/{order_id}/workflow-v2/sheet", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_sheet(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.get_saved_sheet(order_id))


@router.get("/{order_id}/workflow-v2/sheet-source", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_sheet_source(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.build_sheet_from_selected_ocr(order_id))


@router.put("/{order_id}/workflow-v2/expanded-cell-copy-mode", dependencies=[Depends(require_role("operator"))])
def set_order_workflow_v2_expanded_cell_copy_mode(order_id: str, body: WorkflowV2ExpandedCellCopyModeBody, expected_revision: int = Depends(_require_workflow_if_match)):
    return _workflow_v2_or_404(order_workflow_v2_service.set_expanded_cell_copy_mode(order_id, body.mode, expected_revision=expected_revision))


@router.put("/{order_id}/workflow-v2/facility-template-columns", dependencies=[Depends(require_role("operator"))])
def save_order_workflow_v2_facility_template_columns(order_id: str, body: WorkflowV2FacilityTemplateColumnsBody, expected_revision: int = Depends(_require_workflow_if_match)):
    return _workflow_v2_or_404(
        order_workflow_v2_service.save_facility_template_columns(
            order_id,
            body.columns,
            expected_revision=expected_revision,
        )
    )


@router.put("/{order_id}/workflow-v2/sheet", dependencies=[Depends(require_role("operator"))])
def save_order_workflow_v2_sheet(
    order_id: str, body: WorkflowV2SheetSaveBody, expected_revision: int = Depends(_require_workflow_if_match)
):
    return _workflow_v2_or_404(
        order_workflow_v2_service.save_sheet(
            order_id=order_id,
            sheet=body.sheet,
            edited_by=body.edited_by,
            require_pre_save_checks=True,
            expected_revision=expected_revision,
        )
    )


@router.post("/{order_id}/workflow-v2/sheet/review-confirm", dependencies=[Depends(require_role("operator"))])
def confirm_order_workflow_v2_sheet_review(order_id: str, body: WorkflowV2SheetReviewConfirmBody, expected_revision: int = Depends(_require_workflow_if_match)):
    return _workflow_v2_or_404(
        order_workflow_v2_service.confirm_sheet_review(
            order_id=order_id,
            sheet=body.sheet,
            expected_revision=expected_revision,
        )
    )


@router.post("/{order_id}/workflow-v2/sheet/pre-save-status", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_sheet_pre_save_status(order_id: str, body: WorkflowV2SheetPreSaveStatusBody):
    return _workflow_v2_or_404(
        order_workflow_v2_service.get_sheet_pre_save_status(
            order_id=order_id,
            sheet=body.sheet,
        )
    )


@router.post("/{order_id}/workflow-v2/sheet/auto-edit", dependencies=[Depends(require_role("operator"))])
def propose_order_workflow_v2_sheet_auto_edit(
    order_id: str,
    body: WorkflowV2SheetAutoEditBody,
    background_tasks: BackgroundTasks,
    wait: bool = Query(default=False),
    expected_revision: int = Depends(_require_workflow_if_match),
):
    if wait:
        return _workflow_v2_or_404(
            order_workflow_v2_service.propose_sheet_auto_edit(
                order_id=order_id,
                sheet=body.sheet,
                model=body.model,
                use_llm=body.use_llm,
                expected_revision=expected_revision,
            )
        )
    payload, error = order_workflow_v2_service.start_sheet_auto_edit_job(
        order_id=order_id,
        sheet=body.sheet,
        model=body.model,
        use_llm=body.use_llm,
        expected_revision=expected_revision,
    )
    if error:
        return _workflow_v2_or_404((payload, error))
    job = payload.get("job") if isinstance(payload, dict) else {}
    background_tasks.add_task(
        order_workflow_v2_service.run_sheet_auto_edit_job,
        order_id=order_id,
        job_id=str(job.get("job_id") or ""),
        sheet=body.sheet,
        model=body.model,
        use_llm=body.use_llm,
        expected_revision=job.get("workflow_completion_revision"),
    )
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=payload)


@router.get("/{order_id}/workflow-v2/sheet/auto-edit/{job_id}", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_sheet_auto_edit_job(order_id: str, job_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.get_sheet_auto_edit_job(order_id=order_id, job_id=job_id))


@router.post("/{order_id}/workflow-v2/bagging", dependencies=[Depends(require_role("operator"))])
def run_order_workflow_v2_bagging(order_id: str, expected_revision: int = Depends(_require_workflow_if_match)):
    return _workflow_v2_or_404(order_workflow_v2_service.run_bagging(order_id, expected_revision=expected_revision))


@router.post("/{order_id}/workflow-v2/sheet/anomaly-review", dependencies=[Depends(require_role("operator"))])
def run_order_workflow_v2_sheet_anomaly_review(order_id: str, body: WorkflowV2SheetAnomalyBody | None = None, expected_revision: int = Depends(_require_workflow_if_match)):
    return _workflow_v2_or_404(
        order_workflow_v2_service.run_sheet_anomaly_review(
            order_id,
            sheet=body.sheet if body else None,
            model=body.model if body else None,
            use_llm=body.use_llm if body else None,
            expected_revision=expected_revision,
        )
    )


@router.post("/{order_id}/workflow-v2/sheet/anomaly-review/dismiss", dependencies=[Depends(require_role("operator"))])
def dismiss_order_workflow_v2_sheet_anomaly_warning(order_id: str, body: WorkflowV2SheetAnomalyDismissBody, expected_revision: int = Depends(_require_workflow_if_match)):
    return _workflow_v2_or_404(
        order_workflow_v2_service.dismiss_sheet_anomaly_warning(
            order_id=order_id,
            sheet=body.sheet,
            warning=body.warning,
            expected_revision=expected_revision,
        )
    )


@router.post("/{order_id}/workflow-v2/bagging/confirm", dependencies=[Depends(require_role("operator"))])
def confirm_order_workflow_v2_bagging(order_id: str):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="workflow_v2_step4_unified_use_bagging")


@router.post("/{order_id}/workflow-v2/outputs/review", dependencies=[Depends(require_role("operator"))])
def prepare_order_workflow_v2_output_review(order_id: str):
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="workflow_v2_step4_unified_use_bagging")


@router.post("/{order_id}/workflow-v2/confirm", dependencies=[Depends(require_role("operator"))])
def confirm_order_workflow_v2(
    order_id: str,
    body: WorkflowV2FinalConfirmBody | None = None,
    expected_revision: int = Depends(_require_workflow_if_match),
):
    return _workflow_v2_or_404(
        order_workflow_v2_service.final_confirm(
            order_id,
            confirmed_by=body.confirmed_by if body else None,
            expected_revision=expected_revision,
        )
    )


@router.get("/{order_id}/workflow-v2/inspection", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_inspection(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.get_inspection(order_id))


@router.get("/{order_id}/draft-sheet", dependencies=[Depends(require_role("operator"))])
def get_draft_sheet(
    order_id: str,
    compact: bool = Query(default=False),
    quantity_assignment_strategy: str | None = Query(default=None),
    sheet_mode: str | None = Query(default=None),
):
    _ = order_id, compact, quantity_assignment_strategy, sheet_mode
    _raise_legacy_order_workflow_gone("draft-sheet")


@router.get("/{order_id}/workflow-state", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_state(order_id: str, refresh: bool = Query(default=True)):
    _ = order_id, refresh
    _raise_legacy_order_workflow_gone("workflow-state")


@router.get("/{order_id}/critical-decisions", dependencies=[Depends(require_role("operator"))])
def get_order_critical_decisions(order_id: str):
    _ = order_id
    _raise_legacy_order_workflow_gone("critical-decisions")


@router.post("/{order_id}/critical-decisions/{decision_type}", dependencies=[Depends(require_role("operator"))])
def choose_order_critical_decision(order_id: str, decision_type: str, body: dict | None = None):
    _ = order_id, decision_type, body
    _raise_legacy_order_workflow_gone("critical-decisions")


@router.get("/{order_id}/ocr-pages", dependencies=[Depends(require_role("operator"))])
def get_ocr_pages(
    order_id: str,
    preview_only: bool = Query(default=False),
    quantity_assignment_strategy: str | None = Query(default=None),
):
    _ = order_id, preview_only, quantity_assignment_strategy
    _raise_legacy_order_workflow_gone("ocr-pages")


@router.get("/{order_id}/ocr-sheet", dependencies=[Depends(require_role("operator"))])
def get_ocr_sheet(order_id: str):
    _ = order_id
    _raise_legacy_order_workflow_gone("ocr-sheet")


@router.get("/{order_id}/ocr-history", dependencies=[Depends(require_role("operator"))])
def get_ocr_history(order_id: str):
    data, error = order_service.get_ocr_edit_history(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error:
        raise HTTPException(status_code=500, detail="ocr history load failed")
    return data


@router.get("/{order_id}/history", dependencies=[Depends(require_role("operator"))])
def get_order_history(order_id: str, limit: int = 100):
    data, error = order_service.get_order_history(order_id, limit=limit)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error:
        raise HTTPException(status_code=500, detail="order history load failed")
    return data


@router.post("/{order_id}/grid-detect", dependencies=[Depends(require_role("operator"))])
def detect_grid(order_id: str, body: dict | None = None):
    table_box = body.get("table_box") if isinstance(body, dict) else None
    grid_params = None
    if isinstance(body, dict):
        raw_params = body.get("grid_params")
        if isinstance(raw_params, dict):
            grid_params = raw_params
        else:
            grid_params = {key: value for key, value in body.items() if key.startswith("grid_")}
    data, error, detail = order_service.detect_order_grid(
        order_id,
        table_box=table_box,
        grid_params=grid_params,
    )
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error == "document_missing":
        raise HTTPException(status_code=400, detail="document missing")
    if error == "template_not_found":
        raise HTTPException(status_code=404, detail="template not found")
    if error == "document_load_failed":
        raise HTTPException(status_code=500, detail="document load failed")
    if error == "grid_not_found":
        raise HTTPException(status_code=404, detail=detail or {"error": "grid_not_found"})
    if error:
        raise HTTPException(status_code=400, detail=detail or {"error": error})
    return data


@router.post("/{order_id}/hakodate-assignment", dependencies=[Depends(require_role("operator"))])
def build_hakodate_assignment(order_id: str, body: dict | None = None, expected_revision: int = Depends(_require_workflow_if_match)):
    strategy = None
    grid_params = None
    if isinstance(body, dict):
        raw_strategy = body.get("strategy") or body.get("quantity_assignment_strategy")
        if isinstance(raw_strategy, str):
            strategy = raw_strategy
        raw_params = body.get("grid_params")
        if isinstance(raw_params, dict):
            grid_params = raw_params
        else:
            grid_params = {
                key: value
                for key, value in body.items()
                if isinstance(key, str) and (key.startswith("grid_") or key.startswith("hakodate_"))
            }
    data, error = order_service.build_order_hakodate_assignment(
        order_id,
        strategy=strategy,
        grid_params=grid_params,
        expected_revision=expected_revision,
    )
    if error in {"workflow_revision_required", "workflow_revision_invalid", "workflow_revision_conflict"}:
        return _workflow_v2_or_404((data, error))
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {
        "facility_missing",
        "document_missing",
        "template_unresolved",
        "week_unresolved",
        "menu_entries_missing",
        "sheet_fields_not_found",
        "sheet_fields_duplicate",
        "sheet_template_field_invalid",
        "sheet_quantity_columns_missing",
    }:
        raise HTTPException(status_code=400, detail=error)
    if error == "facility_not_found":
        raise HTTPException(status_code=404, detail="facility not found")
    if error == "document_load_failed":
        raise HTTPException(status_code=500, detail="document load failed")
    if error:
        raise HTTPException(status_code=500, detail=error)
    return data


@router.post("/{order_id}/quantity-assignment-compare", dependencies=[Depends(require_role("operator"))])
def compare_quantity_assignment(order_id: str, body: dict | None = None, expected_revision: int = Depends(_require_workflow_if_match)):
    strategy = None
    grid_params = None
    if isinstance(body, dict):
        raw_strategy = body.get("strategy") or body.get("quantity_assignment_strategy")
        if isinstance(raw_strategy, str):
            strategy = raw_strategy
        raw_params = body.get("grid_params")
        if isinstance(raw_params, dict):
            grid_params = raw_params
        else:
            grid_params = {
                key: value
                for key, value in body.items()
                if isinstance(key, str) and (key.startswith("grid_") or key.startswith("hakodate_"))
            }
    data, error = order_service.build_order_quantity_assignment_comparison(
        order_id,
        strategy=strategy,
        grid_params=grid_params,
        expected_revision=expected_revision,
    )
    if error in {"workflow_revision_required", "workflow_revision_invalid", "workflow_revision_conflict"}:
        return _workflow_v2_or_404((data, error))
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error:
        raise HTTPException(status_code=500, detail=error)
    return data


@router.post("/{order_id}/hakodate-audit-copy", dependencies=[Depends(require_role("operator"))])
def create_hakodate_audit_copy(order_id: str):
    data, error = order_service.clone_order_for_hakodate_audit(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error:
        raise HTTPException(status_code=500, detail=error)
    return data


@router.get("/{order_id}/hakodate-template-candidate", dependencies=[Depends(require_role("operator"))])
def get_hakodate_template_candidate(order_id: str):
    data, error = order_service.build_order_hakodate_template_candidate(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"facility_missing", "template_unresolved"}:
        raise HTTPException(status_code=400, detail=error)
    if error == "facility_not_found":
        raise HTTPException(status_code=404, detail="facility not found")
    if error:
        raise HTTPException(status_code=500, detail=error)
    return data


@router.post("/{order_id}/hakodate-template-candidate/approve", dependencies=[Depends(require_role("operator"))])
def approve_hakodate_template_candidate(order_id: str):
    _ = order_id
    _raise_legacy_order_workflow_gone("hakodate-template-candidate/approve")


@router.post("/{order_id}/hakodate-canonical-manifest-item", dependencies=[Depends(require_role("operator"))])
def save_hakodate_canonical_manifest_item(order_id: str, body: dict | None = None):
    item = body.get("item") if isinstance(body, dict) and isinstance(body.get("item"), dict) else body
    data, error = order_service.save_order_hakodate_canonical_manifest_item(order_id, item if isinstance(item, dict) else {})
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error == "hakodate_manifest_deprecated":
        raise HTTPException(status_code=410, detail="hakodate canonical manifest item is deprecated")
    if error == "validation_error":
        raise HTTPException(status_code=400, detail=(data or {}).get("errors") or ["manifest_item_invalid"])
    if error:
        raise HTTPException(status_code=500, detail=error)
    return data


@router.get("/{order_id}/hakodate-canonical-manifest-item", dependencies=[Depends(require_role("operator"))])
def get_hakodate_canonical_manifest_item(order_id: str):
    data, error = order_service.get_order_hakodate_canonical_manifest_item(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error:
        raise HTTPException(status_code=500, detail=error)
    return data


@router.post("/{order_id}/hakodate-projected-sheet", dependencies=[Depends(require_role("operator"))])
def build_hakodate_projected_sheet(order_id: str, body: dict | None = None, expected_revision: int = Depends(_require_workflow_if_match)):
    strategy = None
    grid_params = None
    if isinstance(body, dict):
        raw_strategy = body.get("strategy") or body.get("quantity_assignment_strategy")
        if isinstance(raw_strategy, str):
            strategy = raw_strategy
        raw_params = body.get("grid_params")
        if isinstance(raw_params, dict):
            grid_params = raw_params
        else:
            grid_params = {
                key: value
                for key, value in body.items()
                if isinstance(key, str) and (key.startswith("grid_") or key.startswith("hakodate_"))
            }
    data, error = order_service.build_order_hakodate_projected_sheet(
        order_id,
        strategy=strategy,
        grid_params=grid_params,
        expected_revision=expected_revision,
    )
    if error in {"workflow_revision_required", "workflow_revision_invalid", "workflow_revision_conflict"}:
        return _workflow_v2_or_404((data, error))
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"facility_missing", "document_missing", "template_unresolved"}:
        raise HTTPException(status_code=400, detail=error)
    if error == "facility_not_found":
        raise HTTPException(status_code=404, detail="facility not found")
    if error in {"sheet_unavailable", "assignment_unavailable"}:
        raise HTTPException(status_code=400, detail=error)
    if error:
        raise HTTPException(status_code=500, detail=error)
    return data


@router.post("/{order_id}/ocr-apply", dependencies=[Depends(require_role("operator"))])
def apply_ocr_markdown(order_id: str, body: dict):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("ocr-apply")


@router.post("/{order_id}/ocr-sheet-save", dependencies=[Depends(require_role("operator"))])
def save_ocr_sheet(order_id: str, body: dict):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("ocr-sheet-save")


@router.post("/{order_id}/draft-sheet", dependencies=[Depends(require_role("operator"))])
def save_draft_sheet(order_id: str, body: dict):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("draft-sheet")


@router.post("/{order_id}/draft-sheet/switch-evidence", dependencies=[Depends(require_role("operator"))])
def switch_draft_sheet_evidence(order_id: str):
    _ = order_id
    _raise_legacy_order_workflow_gone("draft-sheet/switch-evidence")


@router.post("/{order_id}/draft-sheet/keep-current", dependencies=[Depends(require_role("operator"))])
def keep_current_draft_sheet(order_id: str):
    _ = order_id
    _raise_legacy_order_workflow_gone("draft-sheet/keep-current")


@router.get("/{order_id}/draft-sheet/candidate-preview", dependencies=[Depends(require_role("operator"))])
def get_candidate_draft_sheet_preview(order_id: str):
    _ = order_id
    _raise_legacy_order_workflow_gone("draft-sheet/candidate-preview")


@router.post("/{order_id}/draft-sheet/force-weekly-menu", dependencies=[Depends(require_role("operator"))])
def force_draft_sheet_weekly_menu(order_id: str, body: dict | None = None):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("draft-sheet/force-weekly-menu")


@router.post("/{order_id}/draft-sheet/force-facility-schema", dependencies=[Depends(require_role("operator"))])
def force_draft_sheet_facility_schema(order_id: str, body: dict | None = None):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("draft-sheet/force-facility-schema")


@router.post("/{order_id}/draft-sheet/apply-patch-candidate", dependencies=[Depends(require_role("operator"))])
def apply_patch_candidate(order_id: str, body: dict | None = None):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("draft-sheet/apply-patch-candidate")


@router.post("/{order_id}/ocr-review", dependencies=[Depends(require_role("operator"))])
def review_ocr_sheet(order_id: str, body: dict | None = None):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("ocr-review")


@router.delete(
    "/by-message-prefix/{prefix}",
    dependencies=[Depends(require_role("admin"))],
)
def delete_orders_by_message_prefix(prefix: str):
    removed = order_service.delete_orders_by_message_prefix(prefix)
    return {"removed": removed}


@router.post("/{order_id}/facility", status_code=status.HTTP_200_OK, dependencies=[Depends(require_role("operator"))])
def set_facility(order_id: str, body: dict):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("facility")


@router.get("/{order_id}/week-options", dependencies=[Depends(require_role("operator"))])
def get_week_options(order_id: str):
    options, error = order_service.get_order_week_options(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    return {"options": options or []}


@router.post("/{order_id}/week", status_code=status.HTTP_200_OK, dependencies=[Depends(require_role("operator"))])
def set_week(order_id: str, body: dict):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("week")


@router.put("/{order_id}/facility-template-columns", dependencies=[Depends(require_role("operator"))])
def save_facility_template_columns(order_id: str, body: dict):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("facility-template-columns")


@router.put("/{order_id}/lines", dependencies=[Depends(require_role("operator"))])
def update_lines(order_id: str, body: dict):
    _ = order_id, body
    _raise_legacy_order_workflow_gone("lines")


@router.post("/{order_id}/confirm", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("operator"))])
def confirm_order(order_id: str, background_tasks: BackgroundTasks, body: dict | None = None):
    _ = order_id, background_tasks, body
    _raise_legacy_order_workflow_gone("confirm")


@router.post(
    "/{order_id}/reparse",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("operator"))],
)
async def reparse_order(order_id: str, background_tasks: BackgroundTasks, request: Request):
    _ = order_id, background_tasks, request
    _raise_legacy_order_workflow_gone("reparse")


@router.post(
    "/{order_id}/ocr-rerun",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("operator"))],
)
async def rerun_ocr_pipeline(order_id: str, background_tasks: BackgroundTasks, request: Request):
    _ = order_id, background_tasks, request
    _raise_legacy_order_workflow_gone("ocr-rerun")


@router.post(
    "/{order_id}/ocr-recover",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("operator"))],
)
def recover_ocr(order_id: str, background_tasks: BackgroundTasks):
    _ = order_id, background_tasks
    _raise_legacy_order_workflow_gone("ocr-recover")
