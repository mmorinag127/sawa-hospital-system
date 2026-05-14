import json
from datetime import datetime, timedelta, date as dt_date
from urllib.error import HTTPError
from urllib.request import urlopen
from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks, Query, Request
from fastapi.responses import Response, JSONResponse
from loguru import logger
from pydantic import BaseModel

from src.services import order_service, config_service, candidate_resolution_service, workflow_state_service, uploaded_pdf_service
from src.services import order_workflow_v2_service
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
    update_job as update_ocr_job,
)
from src.services.ocr_execution_lock_service import OcrExecutionSlotTimeout, acquire_ocr_execution_slot
from src.api.auth import require_role
from src.services.storage_service import load_bytes_from_uri

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


class WorkflowV2ExpandedCellCopyModeBody(BaseModel):
    mode: str


class WorkflowV2FacilityTemplateColumnsBody(BaseModel):
    columns: list[dict]


class WorkflowV2QuadReviewBody(BaseModel):
    decision: str
    quad_px: list[list[float]]


class WorkflowV2HeaderAxisReviewBody(BaseModel):
    corrected_xs: list[float]
    coordinate_space: dict


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
    if not isinstance(job, dict):
        return job
    order_id = str(order.get("id") or "").strip()
    if not order_id or not _is_order_reparse_job(job, order_id):
        return job
    if _should_preserve_terminal_reparse_state(job, order_id):
        return job
    normalized_status = str(job.get("status") or "").strip().lower()
    if normalized_status not in {"running", "pending", "awaiting_output", "recovering"}:
        return job
    if not force and not _is_job_stale(job):
        return job
    stale_minutes = _get_ocr_stale_minutes()
    cached_payload = order_service.get_cached_ocr_payload(order_id)
    current_metrics = dict(job.get("metrics") or {})
    previous_stage = str(current_metrics.get("processing_stage") or "").strip().lower() or None
    review = order_service.get_order_review_summary(
        order_id,
        lines_updated_at=order.get("lines_updated_at"),
        ocr_status="failed",
        cached_payload=cached_payload,
        ocr_metrics=current_metrics,
        order_status=order.get("status"),
    )
    error_code = f"reparse_stale_timeout>{stale_minutes}m"
    current_metrics.update(
        {
            "processing_stage": "stale_timeout",
            "result_state": "draft_ready_blocked" if review.get("ocr_has_saved_draft") else "hard_failed",
            "confirmed_lines_retained": bool(order.get("lines_updated_at")),
            "error": error_code,
            "stale_recovered": True,
            "stale_timeout_minutes": stale_minutes,
            "previous_processing_stage": previous_stage,
            "stale_marked_at": datetime.utcnow().isoformat(),
        }
    )
    update_ocr_job(
        str(job.get("id") or ""),
        status="failed",
        error_message=error_code,
        metrics=current_metrics,
    )
    refreshed = get_ocr_job(str(job.get("id") or ""))
    return refreshed or job


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
        order["ocr_status"] = "stalled"
        order["ocr_error"] = order.get("ocr_error") or f"timeout>{stale_minutes}m"
        order["ocr_updated_at"] = updated_at
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
    # Read surfaces may project cache-derived legacy status, but cache is not
    # canonical and must not rewrite OCR job state.
    order["ocr_status"] = cached_status
    order.pop("ocr_error", None)
    if isinstance(cached_payload, dict) and cached_payload.get("error"):
        order["ocr_error"] = cached_payload.get("error")
    if isinstance(cached_payload, dict) and cached_payload.get("metrics"):
        order["ocr_metrics"] = cached_payload.get("metrics")
    if job and job.get("updated_at"):
        order["ocr_updated_at"] = job.get("updated_at")
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
    if job.get("status") == "failed" and _is_read_timeout_error(error_message):
        order["ocr_status"] = "running"
        return
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
    week_range_missing = bool(week_value) and "@" not in week_value
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
    if message_id and (not cached_payload or not effective_facility or not effective_week):
        uploaded_pdf = uploaded_pdf_service.get_uploaded_pdf_by_message_id(message_id)
        if isinstance(uploaded_pdf, dict):
            ingest_payload = uploaded_pdf_service.build_ingest_payload(uploaded_pdf)
            if not effective_facility:
                effective_facility = str(ingest_payload.get("facility_hint") or "").strip() or None
            if not effective_week:
                effective_week = str(ingest_payload.get("week_hint") or "").strip() or None
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
    if not _is_active_order_reparse_job(job, order_id):
        return job
    try:
        workflow = order_service.get_order_workflow_state(order_id, refresh=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Active reparse job workflow reconciliation failed",
            order_id=order_id,
            error=str(exc),
        )
        return job
    if not isinstance(workflow, dict):
        return get_latest_order_job(order_id) or job
    reparse_state = workflow.get("reparse_state")
    reparse_state = reparse_state if isinstance(reparse_state, dict) else {}
    reparse_status = str(
        reparse_state.get("status")
        or workflow.get("ocr_reparse_status")
        or ""
    ).strip().lower()
    if reparse_status not in {"hard_failed", "done"}:
        return get_latest_order_job(order_id) or job
    normalized_job_id = str((job or {}).get("id") or "").strip()
    if not normalized_job_id:
        return get_latest_order_job(order_id) or job
    metrics = (job or {}).get("metrics")
    metrics = dict(metrics) if isinstance(metrics, dict) else {}
    processing_stage = str(
        reparse_state.get("processing_stage")
        or workflow.get("ocr_processing_stage")
        or metrics.get("processing_stage")
        or "ocr_pipeline"
    ).strip() or "ocr_pipeline"
    if reparse_status == "hard_failed":
        error_message = str(
            workflow.get("ocr_last_reparse_error")
            or reparse_state.get("error_message")
            or (job or {}).get("error_message")
            or "ocr_rerun_failed"
        ).strip() or "ocr_rerun_failed"
        result_state = "hard_failed"
        terminal_status = "failed"
    else:
        error_message = None
        result_state = str(
            reparse_state.get("result_state")
            or workflow.get("ocr_result_state")
            or "done"
        ).strip() or "done"
        terminal_status = "done"
    metrics.update(
        {
            "request_mode": get_job_request_mode(job) or "ocr_rerun",
            "processing_stage": processing_stage,
            "result_state": result_state,
            "stage_updated_at": datetime.utcnow().isoformat(),
        }
    )
    if error_message:
        metrics["error"] = error_message
    logger.info(
        "Healed active reparse job from workflow terminal state",
        order_id=order_id,
        ocr_job_id=normalized_job_id,
        reparse_status=reparse_status,
        terminal_status=terminal_status,
        error=error_message,
    )
    update_ocr_job(
        normalized_job_id,
        status=terminal_status,
        error_message=error_message,
        metrics=metrics,
    )
    return get_ocr_job(normalized_job_id)


def _enqueue_order_reparse_job(
    order_id: str,
    background_tasks: BackgroundTasks,
    *,
    ocr_prompt: str | None = None,
    prompt_preset: str | None = None,
    ocr_provider: str | None = None,
    ocr_model: str | None = None,
    llm_assist: bool = True,
    force: bool = False,
    stale_action: str = "retry",
    request_mode: str | None = None,
) -> dict:
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    if not order.get("facility"):
        raise HTTPException(status_code=400, detail="facility missing")
    if not order.get("document"):
        raise HTTPException(status_code=404, detail="document not found")
    if not config_service.get_facility_config(order.get("facility")):
        raise HTTPException(status_code=404, detail="facility not found")

    ocr_job_id = f"OCR-{order_id}"
    existing_job = _heal_active_order_reparse_job_from_workflow(order_id, get_ocr_job(ocr_job_id))
    is_stale_reparse = bool(_is_order_reparse_job(existing_job, order_id) and _is_job_stale(existing_job))
    existing_job_state = describe_ocr_job_state(existing_job if _is_order_reparse_job(existing_job, order_id) else None)
    if is_stale_reparse or existing_job_state.get("status") == "stalled":
        stale_at = get_ocr_job_stale_at(existing_job)
        if not force and stale_action == "wait":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "reparse_in_progress",
                    "message": "stale reparse requires retry or force",
                    "recoverable": True,
                    "ocr_job_id": ocr_job_id,
                    "stale_at": (
                        stale_at.isoformat()
                        if isinstance(stale_at, datetime)
                        else existing_job_state.get("stale_at")
                    ),
                    "stale_threshold_seconds": existing_job_state.get("stale_threshold_seconds"),
                },
            )
        existing_job = _mark_stale_order_reparse_job(order, existing_job)
    if _is_active_order_reparse_job(existing_job, order_id):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "reparse_in_progress",
                "message": "reparse already running",
                "recoverable": False,
                "ocr_job_id": ocr_job_id,
                "updated_at": (
                    existing_job.get("updated_at").isoformat()
                    if isinstance(existing_job.get("updated_at"), datetime)
                    else existing_job.get("updated_at")
                ),
            },
        )

    input_reference = str(order.get("document") or "")
    workflow_for_lineage, _workflow_error = order_workflow_v2_service.get_workflow(order_id)
    workflow_template_version_id = (
        str((workflow_for_lineage or {}).get("template_version_id") or "").strip()
        if isinstance(workflow_for_lineage, dict)
        else None
    )
    _, created = create_ocr_job(
        ocr_job_id,
        input_reference=input_reference,
        status="running",
        order_id=order_id,
        template_version_id=workflow_template_version_id,
    )
    if not created:
        existing_job = _heal_active_order_reparse_job_from_workflow(order_id, get_ocr_job(ocr_job_id))
        if _is_active_order_reparse_job(existing_job, order_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "reparse_in_progress",
                    "message": "reparse already running",
                    "recoverable": False,
                    "ocr_job_id": ocr_job_id,
                    "updated_at": (
                        existing_job.get("updated_at").isoformat()
                        if isinstance(existing_job.get("updated_at"), datetime)
                        else existing_job.get("updated_at")
                    ),
                },
            )
    update_ocr_job(
        ocr_job_id,
        status="running",
        error_message=None,
        template_id=None,
        output_reference=None,
        order_id=order_id,
        template_version_id=workflow_template_version_id,
        metrics={
            "job_id": ocr_job_id,
            "processing_stage": "queued",
            "result_state": "processing",
            "confirmed_lines_retained": bool(order.get("lines_updated_at")),
            "request_mode": str(
                request_mode or ("llm_reparse" if llm_assist else "ocr_reparse")
            ).strip()
            or ("llm_reparse" if llm_assist else "ocr_reparse"),
            "status": "running",
        },
        input_reference=input_reference,
    )
    background_tasks.add_task(
        _run_reparse_background,
        order_id,
        ocr_prompt,
        prompt_preset,
        ocr_provider,
        ocr_model,
        llm_assist,
    )
    return {"accepted": True, "ocr_job_id": ocr_job_id}


def _run_reparse_background(
    order_id: str,
    ocr_prompt: str | None,
    prompt_preset: str | None = None,
    ocr_provider: str | None = None,
    ocr_model: str | None = None,
    llm_assist: bool = False,
) -> None:
    try:
        _, error = order_service.reparse_order(
            order_id,
            ocr_prompt=ocr_prompt,
            prompt_preset=prompt_preset,
            ocr_provider=ocr_provider,
            ocr_model=ocr_model,
            llm_assist=llm_assist,
        )
        if error:
            logger.warning("Reparse background failed", order_id=order_id, error=error)
    except BaseException as exc:  # noqa: BLE001
        try:
            current_order = order_service.get_order_by_id(order_id)
            retained_lines = bool(current_order.get("lines_updated_at")) if isinstance(current_order, dict) else False
            order_service._update_reparse_job_progress(  # noqa: SLF001
                f"OCR-{order_id}",
                status="failed",
                processing_stage="crashed",
                result_state="hard_failed",
                error_message=f"reparse_crashed:{exc}",
                metrics_patch={
                    "error": "reparse_crashed",
                    "crash_detail": str(exc),
                    "confirmed_lines_retained": retained_lines,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to update OCR job status after reparse crash", order_id=order_id)
        logger.exception("Reparse background crashed", order_id=order_id, error=str(exc))


def _run_ocr_rerun_background(order_id: str, ocr_job_id: str) -> None:
    def _patch_job_metrics(patch: dict) -> None:
        existing_job = get_ocr_job(ocr_job_id) or {}
        metrics = dict(existing_job.get("metrics") or {})
        metrics.update(patch)
        metrics["stage_updated_at"] = datetime.utcnow().isoformat()
        update_ocr_job(ocr_job_id, metrics=metrics)

    started_at = datetime.utcnow()
    try:
        _patch_job_metrics(
            {
                "processing_stage": "waiting_for_ocr_slot",
                "result_state": "processing",
                "request_mode": "ocr_rerun",
                "ocr_started_at": started_at.isoformat(),
                "ocr_slot_wait_started_at": started_at.isoformat(),
            }
        )
        with acquire_ocr_execution_slot(order_id=order_id, job_id=ocr_job_id) as slot:
            slot_acquired_at = datetime.utcnow()
            _patch_job_metrics(
                {
                    "processing_stage": "ocr_slot_acquired",
                    "result_state": "processing",
                    "request_mode": "ocr_rerun",
                    "ocr_slot_acquired_at": slot_acquired_at.isoformat(),
                    "ocr_slot_wait_seconds": round((slot_acquired_at - started_at).total_seconds(), 3),
                    "ocr_execution_slot": slot,
                }
            )
            evidence, error = order_service.rerun_ocr_evidence_only(
                order_id,
                job_id=ocr_job_id,
                project_sheet=False,
                refresh_workflow=False,
            )
        if error:
            logger.warning("OCR evidence-only rerun failed", order_id=order_id, error=error)
            finished_at = datetime.utcnow()
            existing_job = get_ocr_job(ocr_job_id) or {}
            metrics = dict(existing_job.get("metrics") or {})
            metrics.update(
                {
                    "ocr_started_at": started_at.isoformat(),
                    "ocr_finished_at": finished_at.isoformat(),
                    "ocr_elapsed_seconds": round((finished_at - started_at).total_seconds(), 3),
                }
            )
            update_ocr_job(ocr_job_id, metrics=metrics)
            order_workflow_v2_service.mark_ocr_run_completed(
                order_id,
                job_id=ocr_job_id,
                error=error,
            )
            return
        finished_at = datetime.utcnow()
        existing_job = get_ocr_job(ocr_job_id) or {}
        metrics = dict(existing_job.get("metrics") or {})
        metrics.update(
            {
                "ocr_started_at": started_at.isoformat(),
                "ocr_finished_at": finished_at.isoformat(),
                "ocr_elapsed_seconds": round((finished_at - started_at).total_seconds(), 3),
            }
        )
        update_ocr_job(ocr_job_id, metrics=metrics)
        order_workflow_v2_service.mark_ocr_run_completed(
            order_id,
            job_id=ocr_job_id,
            evidence_run_id=str((evidence or {}).get("id") or "").strip() or None,
        )
    except OcrExecutionSlotTimeout as exc:
        finished_at = datetime.utcnow()
        existing_job = get_ocr_job(ocr_job_id) or {}
        metrics = dict(existing_job.get("metrics") or {})
        metrics.update(
            {
                "error": str(exc),
                "request_mode": "ocr_rerun",
                "processing_stage": "ocr_execution_slot_timeout",
                "result_state": "hard_failed",
                "ocr_started_at": started_at.isoformat(),
                "ocr_finished_at": finished_at.isoformat(),
                "ocr_elapsed_seconds": round((finished_at - started_at).total_seconds(), 3),
                "stage_updated_at": finished_at.isoformat(),
            }
        )
        update_ocr_job(
            ocr_job_id,
            status="failed",
            error_message="ocr_execution_slot_timeout",
            metrics=metrics,
        )
        order_workflow_v2_service.mark_ocr_run_completed(
            order_id,
            job_id=ocr_job_id,
            error="ocr_execution_slot_timeout",
        )
        logger.exception("OCR evidence-only rerun could not acquire execution slot", order_id=order_id)
    except BaseException as exc:  # noqa: BLE001
        finished_at = datetime.utcnow()
        current_order = order_service.get_order_by_id(order_id)
        retained_lines = bool(current_order.get("lines_updated_at")) if isinstance(current_order, dict) else False
        existing_job = get_ocr_job(ocr_job_id) or {}
        metrics = dict(existing_job.get("metrics") or {})
        metrics.update(
            {
                "error": str(exc),
                "request_mode": "ocr_rerun",
                "processing_stage": "crashed",
                "result_state": "hard_failed",
                "confirmed_lines_retained": retained_lines,
                "ocr_started_at": started_at.isoformat(),
                "ocr_finished_at": finished_at.isoformat(),
                "ocr_elapsed_seconds": round((finished_at - started_at).total_seconds(), 3),
            }
        )
        try:
            update_ocr_job(
                ocr_job_id,
                status="failed",
                error_message=f"ocr_rerun_crashed:{exc}",
                metrics=metrics,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to update OCR rerun status after crash", order_id=order_id)
        try:
            order_workflow_v2_service.mark_ocr_run_completed(
                order_id,
                job_id=ocr_job_id,
                error=f"ocr_rerun_crashed:{exc}",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to update workflow-v2 status after OCR rerun crash", order_id=order_id)
        logger.exception("OCR evidence-only rerun crashed", order_id=order_id, error=str(exc))


def _enqueue_order_evidence_rerun(
    order_id: str,
    background_tasks: BackgroundTasks,
    *,
    stale_action: str = "retry",
    force: bool = False,
) -> dict:
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    if not order.get("facility"):
        raise HTTPException(status_code=400, detail="facility missing")
    if not order.get("document"):
        raise HTTPException(status_code=404, detail="document not found")
    if not config_service.get_facility_config(order.get("facility")):
        raise HTTPException(status_code=404, detail="facility not found")

    ocr_job_id = f"OCR-{order_id}"
    existing_job = _heal_active_order_reparse_job_from_workflow(order_id, get_ocr_job(ocr_job_id))
    is_stale_reparse = bool(_is_order_reparse_job(existing_job, order_id) and _is_job_stale(existing_job))
    existing_job_state = describe_ocr_job_state(existing_job if _is_order_reparse_job(existing_job, order_id) else None)
    if is_stale_reparse or existing_job_state.get("status") == "stalled":
        stale_at = get_ocr_job_stale_at(existing_job)
        if stale_action == "wait":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "reparse_in_progress",
                    "message": "stale rerun requires retry",
                    "recoverable": True,
                    "ocr_job_id": ocr_job_id,
                    "stale_at": (
                        stale_at.isoformat()
                        if isinstance(stale_at, datetime)
                        else existing_job_state.get("stale_at")
                    ),
                    "stale_threshold_seconds": existing_job_state.get("stale_threshold_seconds"),
                },
            )
        existing_job = _mark_stale_order_reparse_job(order, existing_job)
    if force and _is_active_order_reparse_job(existing_job, order_id):
        existing_job = _mark_stale_order_reparse_job(order, existing_job, force=True)
    if _is_active_order_reparse_job(existing_job, order_id):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "reparse_in_progress",
                "message": "OCR rerun already running",
                "recoverable": False,
                "ocr_job_id": ocr_job_id,
            },
        )

    input_reference = str(order.get("document") or "")
    workflow_for_lineage, _workflow_error = order_workflow_v2_service.get_workflow(order_id)
    workflow_template_version_id = (
        str((workflow_for_lineage or {}).get("template_version_id") or "").strip()
        if isinstance(workflow_for_lineage, dict)
        else None
    )
    _, created = create_ocr_job(
        ocr_job_id,
        input_reference=input_reference,
        status="running",
        order_id=order_id,
        template_version_id=workflow_template_version_id,
    )
    if not created:
        existing_job = _heal_active_order_reparse_job_from_workflow(order_id, get_ocr_job(ocr_job_id))
        if _is_active_order_reparse_job(existing_job, order_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "reparse_in_progress",
                    "message": "OCR rerun already running",
                    "recoverable": False,
                    "ocr_job_id": ocr_job_id,
                },
            )
    update_ocr_job(
        ocr_job_id,
        status="running",
        error_message=None,
        template_id=None,
        output_reference=None,
        input_reference=input_reference,
        order_id=order_id,
        template_version_id=workflow_template_version_id,
        metrics={
            "job_id": ocr_job_id,
            "processing_stage": "queued",
            "result_state": "processing",
            "confirmed_lines_retained": bool(order.get("lines_updated_at")),
            "request_mode": "ocr_rerun",
            "status": "running",
        },
    )
    background_tasks.add_task(_run_ocr_rerun_background, order_id, ocr_job_id)
    try:
        workflow_state = order_service.get_order_workflow_state(order_id, refresh=True)
    except Exception:
        workflow_state = None
    return {"accepted": True, "ocr_job_id": ocr_job_id, "workflow_state": workflow_state}


@router.get("", dependencies=[Depends(require_role("operator"))])
def list_orders(
    status: str | None = None,
    include_ocr: bool | None = None,
    include_archived: bool | None = None,
    include_runtime: bool | None = None,
    include_candidate_summary: bool = False,
    limit: int | None = Query(default=None, ge=1, le=1000),
):
    include_archived_flag = True if include_archived is None else include_archived
    orders = order_service.list_orders(status=status, include_archived=include_archived_flag)
    if limit is not None:
        orders = orders[:limit]
    include_runtime_flag = (include_ocr is not None) if include_runtime is None else include_runtime
    if not include_runtime_flag:
        return {"orders": orders}
    order_ids = [str(order.get("id") or "").strip() for order in orders if str(order.get("id") or "").strip()]
    cache_map = order_service._load_order_ocr_cache_map(order_ids)
    lightweight_mode = include_ocr is False
    if lightweight_mode:
        workflow_map = workflow_state_service.list_workflow_states(order_ids)
        for order in orders:
            order_id = str(order.get("id") or "").strip()
            cached_payload = cache_map.get(order_id)
            workflow = workflow_map.get(order_id)
            if isinstance(workflow, dict):
                order["workflow_state"] = workflow
            cached_status = _derive_status_from_payload(cached_payload)
            if cached_status and not order.get("ocr_status"):
                order["ocr_status"] = cached_status
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
            if not job:
                order_id = str(order.get("id") or "").strip()
                if order_id:
                    job = get_latest_order_job(order_id)
            if job:
                error_message = job.get("error_message")
                if job.get("status") == "failed" and _is_read_timeout_error(error_message):
                    order["ocr_status"] = "running"
                else:
                    order["ocr_status"] = job.get("status")
                    if error_message:
                        order["ocr_error"] = error_message
                if job.get("metrics"):
                    order["ocr_metrics"] = job.get("metrics")
                order["ocr_updated_at"] = job.get("updated_at")
            job = _apply_cached_status_override(order, str(order.get("id") or ""), job)
            job = _apply_stale_ocr_status(order, job)
    reparse_jobs = get_ocr_jobs(
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
    return {"orders": orders}


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
    return order_service.get_daily_bag_summary(target_date, facility_id=facility, status=status)


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
    deleted = order_service.delete_daily_output_portion_override(override_id)
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
    order = order_service.get_order_by_id(order_id)
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


def _load_document_bytes(uri: str) -> tuple[bytes, str, str]:
    try:
        return load_bytes_from_uri(uri), "original", "source_uri"
    except Exception as exc:  # noqa: BLE001
        signed_url = order_service._signed_url_from_uri(uri) if isinstance(uri, str) else None
        if signed_url and isinstance(signed_url, str) and signed_url.strip():
            try:
                logger.warning("Falling back to signed URL for order document", uri=uri, error=str(exc))
                with urlopen(signed_url, timeout=30) as response:  # noqa: S310
                    return response.read(), "original", "signed_url"
            except HTTPError as http_exc:
                if http_exc.code not in {403, 404}:
                    raise
            except Exception:
                pass
        if isinstance(exc, FileNotFoundError):
            raise
        logger.warning("Falling back to signed URL for order document", uri=uri, error=str(exc))
        raise FileNotFoundError(str(uri)) from exc


def _load_archived_original_document_bytes(order_id: str, current_uri: str | None) -> tuple[bytes, str, str] | None:
    payload, error = order_service.get_ocr_output(order_id, persist_cache=False)
    if error or not isinstance(payload, dict):
        return None
    input_reference = str(payload.get("input_reference") or "").strip()
    if not input_reference or input_reference == str(current_uri or "").strip():
        return None
    try:
        data = load_bytes_from_uri(input_reference)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to load archived original document from OCR input reference",
            order_id=order_id,
            input_reference=input_reference,
            error=str(exc),
        )
        return None
    return data, "original_archive", "ocr_input_reference"


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
def download_document(order_id: str):
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    uri = order.get("document")
    if not uri:
        raise HTTPException(status_code=404, detail="document not found")
    try:
        data, source_kind, source_variant = _load_document_bytes(uri)
    except FileNotFoundError:
        archived = _load_archived_original_document_bytes(order_id, str(uri or ""))
        if not archived:
            raise HTTPException(status_code=404, detail="document not found")
        data, source_kind, source_variant = archived
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "X-Sawa-Document-Source": source_kind,
            "X-Sawa-Document-Variant": source_variant,
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
    evidence = order_service.get_latest_ocr_evidence_run(order_id, backfill_from_cache=False)
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
    if error:
        raise HTTPException(status_code=400, detail=error)
    return payload or {}


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


def _enqueue_workflow_v2_evidence_rerun(
    order_id: str,
    background_tasks: BackgroundTasks,
    *,
    stale_action: str = "retry",
    force: bool = False,
) -> dict:
    workflow = _workflow_v2_or_404(order_workflow_v2_service.get_workflow(order_id))
    if not workflow.get("facility_id") or not workflow.get("week_start") or not workflow.get("week_end"):
        raise HTTPException(status_code=400, detail="context_not_confirmed")
    if not order_workflow_v2_service.workflow_has_confirmed_ocr_context(workflow):
        raise HTTPException(status_code=400, detail="facility_template_unresolved")
    if stale_action not in {"retry", "wait"}:
        raise HTTPException(status_code=400, detail="stale_action must be retry or wait")

    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    if not order.get("facility"):
        raise HTTPException(status_code=400, detail="facility missing")
    if not order.get("document"):
        raise HTTPException(status_code=404, detail="document not found")
    if not config_service.get_facility_config(order.get("facility")):
        raise HTTPException(status_code=404, detail="facility not found")

    ocr_job_id = f"OCR-{order_id}"
    existing_job = get_ocr_job(ocr_job_id)
    existing_job_state = describe_ocr_job_state(existing_job if _is_order_reparse_job(existing_job, order_id) else None)
    if existing_job_state.get("status") == "stalled":
        stale_at = get_ocr_job_stale_at(existing_job)
        if stale_action == "wait":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "ocr_rerun_in_progress",
                    "message": "stale OCR rerun requires retry",
                    "recoverable": True,
                    "ocr_job_id": ocr_job_id,
                    "stale_at": (
                        stale_at.isoformat()
                        if isinstance(stale_at, datetime)
                        else existing_job_state.get("stale_at")
                    ),
                    "stale_threshold_seconds": existing_job_state.get("stale_threshold_seconds"),
                },
            )
        existing_job = _mark_stale_order_reparse_job(order, existing_job)
    if force and _is_active_order_reparse_job(existing_job, order_id):
        existing_job = _mark_stale_order_reparse_job(order, existing_job, force=True)
    if _is_active_order_reparse_job(existing_job, order_id):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "ocr_rerun_in_progress",
                "message": "OCR rerun already running",
                "recoverable": False,
                "ocr_job_id": ocr_job_id,
            },
        )

    input_reference = str(order.get("document") or "")
    run_requested_at = datetime.utcnow().isoformat()
    workflow_template_version_id = str(workflow.get("template_version_id") or "").strip() or None
    _, created = create_ocr_job(
        ocr_job_id,
        input_reference=input_reference,
        status="running",
        order_id=order_id,
        template_version_id=workflow_template_version_id,
    )
    if not created:
        existing_job = get_ocr_job(ocr_job_id)
        if _is_active_order_reparse_job(existing_job, order_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "ocr_rerun_in_progress",
                    "message": "OCR rerun already running",
                    "recoverable": False,
                    "ocr_job_id": ocr_job_id,
                },
            )
    update_ocr_job(
        ocr_job_id,
        status="running",
        error_message=None,
        template_id=None,
        output_reference=None,
        input_reference=input_reference,
        order_id=order_id,
        template_version_id=workflow_template_version_id,
        metrics={
            "job_id": ocr_job_id,
            "workflow_version": "v2",
            "processing_stage": "queued",
            "result_state": "processing",
            "confirmed_lines_retained": bool(order.get("lines_updated_at")),
            "request_mode": "ocr_rerun",
            "status": "running",
            "ocr_started_at": run_requested_at,
            "ocr_finished_at": None,
            "ocr_elapsed_seconds": None,
        },
    )
    queued_workflow = _workflow_v2_or_404(order_workflow_v2_service.mark_ocr_run_queued(order_id, ocr_job_id))
    background_tasks.add_task(_run_ocr_rerun_background, order_id, ocr_job_id)
    return {"accepted": True, "ocr_job_id": ocr_job_id, "workflow": queued_workflow}


@router.get("/{order_id}/workflow-v2", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.get_workflow(order_id))


@router.post("/{order_id}/workflow-v2/context", dependencies=[Depends(require_role("operator"))])
def confirm_order_workflow_v2_context(order_id: str, body: WorkflowV2ContextConfirmBody):
    return _workflow_v2_or_404(
        order_workflow_v2_service.confirm_context(
            order_id=order_id,
            facility_id=body.facility_id,
            week_start=body.week_start,
            week_end=body.week_end,
            template_id=body.template_id,
        )
    )


@router.post(
    "/{order_id}/workflow-v2/ocr-runs",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("operator"))],
)
def run_order_workflow_v2_ocr(order_id: str, background_tasks: BackgroundTasks, body: WorkflowV2OcrRunBody | None = None):
    stale_action = str((body.stale_action if body else None) or "retry").strip().lower()
    force = bool(body.force) if body else False
    mode = str((body.mode if body else None) or "hakodate").strip().lower()
    prerequisite_workflow, prerequisite_error = order_workflow_v2_service.ensure_ocr_prerequisites(order_id)
    if prerequisite_error:
        _raise_workflow_v2_ocr_prerequisite_error(prerequisite_error, prerequisite_workflow)
    if mode == "llm":
        result = _enqueue_order_reparse_job(
            order_id,
            background_tasks,
            ocr_prompt=(body.ocr_prompt.strip() if body and isinstance(body.ocr_prompt, str) and body.ocr_prompt.strip() else None),
            prompt_preset=(body.prompt_preset.strip().lower() if body and isinstance(body.prompt_preset, str) and body.prompt_preset.strip() else None),
            ocr_provider=(body.ocr_provider.strip().lower() if body and isinstance(body.ocr_provider, str) and body.ocr_provider.strip() else None),
            ocr_model=(body.ocr_model.strip() if body and isinstance(body.ocr_model, str) and body.ocr_model.strip() else None),
            llm_assist=True if body is None or body.llm_assist is None else bool(body.llm_assist),
            force=force,
            stale_action=stale_action,
            request_mode="llm_reparse",
        )
        queued_workflow = _workflow_v2_or_404(order_workflow_v2_service.mark_ocr_run_queued(order_id, result.get("ocr_job_id")))
        result["workflow"] = queued_workflow
        result["mode"] = "llm_reparse"
        return result
    return _enqueue_workflow_v2_evidence_rerun(
        order_id,
        background_tasks,
        stale_action=stale_action,
        force=force,
    )


@router.get("/{order_id}/workflow-v2/quad-review", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_quad_review(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.get_quad_review(order_id))


@router.put("/{order_id}/workflow-v2/quad-review", dependencies=[Depends(require_role("operator"))])
def save_order_workflow_v2_quad_review(order_id: str, body: WorkflowV2QuadReviewBody):
    return _workflow_v2_or_404(
        order_workflow_v2_service.save_quad_review_decision(
            order_id,
            decision=body.decision,
            quad_px=body.quad_px,
        )
    )


@router.get("/{order_id}/workflow-v2/header-axis-review", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_header_axis_review(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.get_header_axis_review(order_id))


@router.put("/{order_id}/workflow-v2/header-axis-review", dependencies=[Depends(require_role("operator"))])
def save_order_workflow_v2_header_axis_review(order_id: str, body: WorkflowV2HeaderAxisReviewBody):
    return _workflow_v2_or_404(
        order_workflow_v2_service.save_header_axis_review_decision(
            order_id,
            corrected_xs=body.corrected_xs,
            coordinate_space=body.coordinate_space,
        )
    )


@router.get("/{order_id}/workflow-v2/ocr-results", dependencies=[Depends(require_role("operator"))])
def list_order_workflow_v2_ocr_results(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.list_ocr_results(order_id))


@router.post("/{order_id}/workflow-v2/ocr-results/{ocr_result_id}/select", dependencies=[Depends(require_role("operator"))])
def select_order_workflow_v2_ocr_result(order_id: str, ocr_result_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.select_ocr_result(order_id, ocr_result_id))


@router.delete("/{order_id}/workflow-v2/ocr-results/{ocr_result_id}", dependencies=[Depends(require_role("operator"))])
def delete_order_workflow_v2_ocr_result(order_id: str, ocr_result_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.delete_ocr_result(order_id, ocr_result_id))


@router.get("/{order_id}/workflow-v2/sheet", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_sheet(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.get_saved_sheet(order_id))


@router.get("/{order_id}/workflow-v2/sheet-source", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_sheet_source(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.build_sheet_from_selected_ocr(order_id))


@router.put("/{order_id}/workflow-v2/expanded-cell-copy-mode", dependencies=[Depends(require_role("operator"))])
def set_order_workflow_v2_expanded_cell_copy_mode(order_id: str, body: WorkflowV2ExpandedCellCopyModeBody):
    return _workflow_v2_or_404(order_workflow_v2_service.set_expanded_cell_copy_mode(order_id, body.mode))


@router.put("/{order_id}/workflow-v2/facility-template-columns", dependencies=[Depends(require_role("operator"))])
def save_order_workflow_v2_facility_template_columns(order_id: str, body: WorkflowV2FacilityTemplateColumnsBody):
    return _workflow_v2_or_404(
        order_workflow_v2_service.save_facility_template_columns(
            order_id,
            body.columns,
        )
    )


@router.put("/{order_id}/workflow-v2/sheet", dependencies=[Depends(require_role("operator"))])
def save_order_workflow_v2_sheet(order_id: str, body: WorkflowV2SheetSaveBody):
    return _workflow_v2_or_404(
        order_workflow_v2_service.save_sheet(
            order_id=order_id,
            sheet=body.sheet,
            edited_by=body.edited_by,
        )
    )


@router.post("/{order_id}/workflow-v2/sheet/auto-edit", dependencies=[Depends(require_role("operator"))])
def propose_order_workflow_v2_sheet_auto_edit(
    order_id: str,
    body: WorkflowV2SheetAutoEditBody,
    background_tasks: BackgroundTasks,
    wait: bool = Query(default=False),
):
    if wait:
        return _workflow_v2_or_404(
            order_workflow_v2_service.propose_sheet_auto_edit(
                order_id=order_id,
                sheet=body.sheet,
                model=body.model,
                use_llm=body.use_llm,
            )
        )
    payload, error = order_workflow_v2_service.start_sheet_auto_edit_job(
        order_id=order_id,
        sheet=body.sheet,
        model=body.model,
        use_llm=body.use_llm,
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
    )
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=payload)


@router.get("/{order_id}/workflow-v2/sheet/auto-edit/{job_id}", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_v2_sheet_auto_edit_job(order_id: str, job_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.get_sheet_auto_edit_job(order_id=order_id, job_id=job_id))


@router.post("/{order_id}/workflow-v2/bagging", dependencies=[Depends(require_role("operator"))])
def run_order_workflow_v2_bagging(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.run_bagging(order_id))


@router.post("/{order_id}/workflow-v2/sheet/anomaly-review", dependencies=[Depends(require_role("operator"))])
def run_order_workflow_v2_sheet_anomaly_review(order_id: str, body: WorkflowV2SheetAnomalyBody | None = None):
    return _workflow_v2_or_404(
        order_workflow_v2_service.run_sheet_anomaly_review(
            order_id,
            sheet=body.sheet if body else None,
            model=body.model if body else None,
            use_llm=body.use_llm if body else None,
        )
    )


@router.post("/{order_id}/workflow-v2/bagging/confirm", dependencies=[Depends(require_role("operator"))])
def confirm_order_workflow_v2_bagging(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.confirm_bagging(order_id))


@router.post("/{order_id}/workflow-v2/outputs/review", dependencies=[Depends(require_role("operator"))])
def prepare_order_workflow_v2_output_review(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.prepare_output_review(order_id))


@router.post("/{order_id}/workflow-v2/confirm", dependencies=[Depends(require_role("operator"))])
def confirm_order_workflow_v2(order_id: str, body: WorkflowV2FinalConfirmBody | None = None):
    return _workflow_v2_or_404(
        order_workflow_v2_service.final_confirm(
            order_id,
            confirmed_by=body.confirmed_by if body else None,
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
    return data


@router.post("/{order_id}/hakodate-assignment", dependencies=[Depends(require_role("operator"))])
def build_hakodate_assignment(order_id: str, body: dict | None = None):
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
    )
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
def compare_quantity_assignment(order_id: str, body: dict | None = None):
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
    )
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
def build_hakodate_projected_sheet(order_id: str, body: dict | None = None):
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
    )
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
