import json
import os
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
from src.services.output_builder import rebuild_bags
from src.services.ocr_job_service import (
    create_job as create_ocr_job,
    describe_job_state as describe_ocr_job_state,
    get_job_request_mode,
    get_job as get_ocr_job,
    is_order_reparse_job as is_order_reparse_ocr_job,
    get_jobs as get_ocr_jobs,
    get_job_stale_at as get_ocr_job_stale_at,
    get_stale_minutes as get_ocr_job_stale_minutes,
    is_job_stale as is_ocr_job_stale,
    update_job as update_ocr_job,
)
from src.workers.output_worker import enqueue_outputs, OutputBuildError
from src.api.auth import require_role
from src.services.storage_service import load_bytes_from_uri

router = APIRouter()


async def _json_body_dict(request: Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


RECOVERABLE_OCR_SHEET_ERRORS = {
    "week_unresolved",
    "menu_entries_missing",
    "monthly_menu_object_missing",
    "monthly_menu_facility_scope_missing",
    "monthly_menu_lookup_failed",
    "sheet_fields_not_found",
    "sheet_fields_duplicate",
    "sheet_template_field_invalid",
    "sheet_quantity_columns_missing",
    "sheet_quantity_column_unmapped",
    "sheet_week_dates_incomplete",
    "week_menu_date_mismatch",
    "sheet_date_mismatch",
    "sheet_canonical_mismatch",
    "sheet_suspicious_blank_row",
    "ocr_evidence_recovery_required",
    "template_resolution_blocked",
}


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


class WorkflowV2FinalConfirmBody(BaseModel):
    confirmed_by: str | None = None


def _flatten_draft_sheet_payload(order_id: str, draft_payload: dict) -> dict:
    return order_service.flatten_current_sheet_payload(order_id, draft_payload)


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
    job = _mark_stale_order_reparse_job(order, job)
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
    if job:
        try:
            update_ocr_job(
                job["id"],
                status=cached_status,
                template_id=cached_payload.get("template_id") if isinstance(cached_payload, dict) else None,
                error_message=cached_payload.get("error") if isinstance(cached_payload, dict) else None,
                metrics=cached_payload.get("metrics") if isinstance(cached_payload, dict) else None,
            )
            job = get_ocr_job(job["id"]) or job
        except Exception:
            pass
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
    allow_refresh_fallback: bool = True,
) -> None:
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        return
    workflow = order_service.get_order_workflow_state(order_id, refresh=refresh)
    if not isinstance(workflow, dict):
        if refresh or not allow_refresh_fallback:
            return
        workflow = order_service.get_order_workflow_state(order_id, refresh=True)
        if not isinstance(workflow, dict):
            return
    elif not refresh and (
        not isinstance(workflow.get("candidate_resolution"), dict)
        or not isinstance(workflow.get("apply_gate"), dict)
        or not isinstance(workflow.get("critical_decisions"), list)
    ) and list(workflow.get("blockers_json") or []) and allow_refresh_fallback:
        refreshed = order_service.get_order_workflow_state(order_id, refresh=True)
        if isinstance(refreshed, dict):
            workflow = refreshed
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
        return get_ocr_job(f"OCR-{order_id}") or job
    reparse_state = workflow.get("reparse_state")
    reparse_state = reparse_state if isinstance(reparse_state, dict) else {}
    reparse_status = str(
        reparse_state.get("status")
        or workflow.get("ocr_reparse_status")
        or ""
    ).strip().lower()
    if reparse_status not in {"hard_failed", "done"}:
        return get_ocr_job(f"OCR-{order_id}") or job
    normalized_job_id = str((job or {}).get("id") or f"OCR-{order_id}").strip() or f"OCR-{order_id}"
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
    run_requested_at = datetime.utcnow().isoformat()
    _, created = create_ocr_job(ocr_job_id, input_reference=input_reference, status="running")
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
    started_at = datetime.utcnow()
    try:
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
    run_requested_at = datetime.utcnow().isoformat()
    _, created = create_ocr_job(ocr_job_id, input_reference=input_reference, status="running")
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
        fallback_ids: list[str] = []
        for order in orders:
            job_id = order.get("ocr_job_id")
            if job_id and job_id in jobs:
                continue
            message_id = order.get("message_id")
            if isinstance(message_id, str) and message_id:
                fallback_ids.append(f"OCR-{message_id}")
        fallback_jobs = get_ocr_jobs(fallback_ids) if fallback_ids else {}
        for order in orders:
            job_id = order.get("ocr_job_id")
            job = jobs.get(job_id) if job_id else None
            if not job:
                order_id = str(order.get("id") or "").strip()
                if order_id:
                    job = get_ocr_job(f"OCR-{order_id}")
            if not job:
                message_id = order.get("message_id")
                if isinstance(message_id, str) and message_id:
                    job = fallback_jobs.get(f"OCR-{message_id}")
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
    job = get_ocr_job(f"OCR-{order_id}")
    if not job and job_id:
        job = get_ocr_job(job_id)
    if not job:
        message_id = order.get("message_id")
        if isinstance(message_id, str) and message_id:
            job = get_ocr_job(f"OCR-{message_id}")
    if job:
        job_status = str(job.get("status") or "").strip().lower()
        job_metrics = job.get("metrics")
        job_metrics = job_metrics if isinstance(job_metrics, dict) else {}
        preserve_terminal_reparse_state = _should_preserve_terminal_reparse_state(job, order_id)
        job_evidence_run_id = str(job_metrics.get("evidence_run_id") or "").strip()
        job_evidence_run = (
            order_service.get_ocr_evidence_run(job_evidence_run_id)
            if job_evidence_run_id
            else None
        )
        latest_persisted_evidence = order_service.get_latest_ocr_evidence_run(order_id, backfill_from_cache=False)
        needs_done_reconcile = (
            not _is_order_reparse_job(job, order_id)
            and job_status in {"done", "success", "completed"}
            and not isinstance(job_evidence_run, dict)
            and not isinstance(latest_persisted_evidence, dict)
        )
        if job.get("output_reference") and (job_status in {"running", "failed"} or needs_done_reconcile):
            try:
                payload = load_bytes_from_uri(job["output_reference"])
                parsed = json.loads(payload.decode("utf-8"))
                output_status = parsed.get("status")
                output_template = parsed.get("template_id")
                job_error = job.get("error_message")
                should_update = bool(output_status and output_status != job.get("status"))
                should_update = should_update or bool(job_error)
                should_update = should_update or needs_done_reconcile
                if job.get("status") == "running" and _is_order_reparse_job(job, order_id):
                    # Reparse jobs keep running until post-processing/validation finishes.
                    # OCR output JSON "done" must not terminate the job early.
                    should_update = False
                if preserve_terminal_reparse_state:
                    should_update = False
                if should_update:
                    if _is_order_reparse_job(job, order_id):
                        update_ocr_job(
                            job["id"],
                            status=output_status or job.get("status"),
                            template_id=output_template or job.get("template_id"),
                            error_message=parsed.get("error"),
                            metrics=parsed.get("metrics"),
                        )
                    else:
                        order_service.reconcile_completed_ocr_job(str(job.get("id") or ""))
                    job = get_ocr_job(job["id"]) or job
            except Exception:
                pass
        _apply_job_status_to_order(order, job)
    job = _apply_stale_ocr_status(order, job)
    # Order detail is the first request that gates the page-level Loading state.
    # Keep it read-only/fast; explicit workflow endpoints and mutating actions
    # are responsible for refreshing persisted workflow/current-sheet state.
    _attach_order_workflow_context(order, refresh=False, allow_refresh_fallback=False)
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
    payload, error = order_service.get_ocr_output(order_id)
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
    _raise_legacy_order_workflow_gone("bags/rebuild")
    try:
        return rebuild_bags(order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="order not found")


@router.get("/{order_id}/evidence", dependencies=[Depends(require_role("operator"))])
def get_ocr_evidence(order_id: str):
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    evidence = order_service.get_latest_ocr_evidence_run(order_id, backfill_from_cache=True)
    if not isinstance(evidence, dict):
        raise HTTPException(status_code=404, detail="ocr evidence not found")
    return evidence


def _attach_reparse_sheet_state(order_id: str, payload: dict) -> dict:
    if not (
        str(payload.get("source") or "").strip() == "hakodate_ocr_evidence_sheet"
        and isinstance(payload.get("hakodate_evidence_projection"), dict)
    ):
        order_service.reconcile_ocr_rerun_state(order_id)
    reparse_job = get_ocr_job(f"OCR-{order_id}")
    reparse_state = describe_ocr_job_state(
        reparse_job if isinstance(reparse_job, dict) and _is_order_reparse_job(reparse_job, order_id) else None
    )
    reparse_status = str(reparse_state.get("status") or "").strip().lower()
    payload["reparse_health"] = reparse_state.get("status")
    payload["reparse_stale_at"] = reparse_state.get("stale_at")
    payload["reparse_stale_threshold_seconds"] = reparse_state.get("stale_threshold_seconds")
    if isinstance(reparse_job, dict):
        metrics = reparse_job.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        live_error = (
            str(metrics.get("error") or "").strip()
            or str(reparse_job.get("error_message") or "").strip()
            or None
        )
        live_processing_stage = str(metrics.get("processing_stage") or "").strip() or None
        live_result_state = str(metrics.get("result_state") or "").strip() or None
        live_request_mode = get_job_request_mode(reparse_job) or None
        if reparse_status:
            payload["reparse_status"] = reparse_status
        if live_error:
            payload["reparse_error"] = live_error
            payload["reparse_last_error_code"] = live_error
        if live_processing_stage:
            payload["reparse_processing_stage"] = live_processing_stage
        if live_result_state:
            payload["reparse_result_state"] = live_result_state
        if live_request_mode:
            payload["reparse_request_mode"] = live_request_mode
    if reparse_status != "stalled":
        return payload
    if str(payload.get("review_state") or "").strip().lower() == "processing":
        payload["review_state"] = "processing_stalled"
    apply_blockers = list(payload.get("apply_blockers") or [])
    confirm_blockers = list(payload.get("confirm_blockers") or [])
    if "reparse_stale" not in apply_blockers:
        apply_blockers.append("reparse_stale")
    if "reparse_stale" not in confirm_blockers:
        confirm_blockers.append("reparse_stale")
    payload["apply_blockers"] = apply_blockers
    payload["confirm_blockers"] = confirm_blockers
    apply_details = list(payload.get("apply_blocker_details") or [])
    confirm_details = list(payload.get("confirm_blocker_details") or [])
    stale_detail = {
        "code": "reparse_stale",
        "message": "再解析ジョブが停止しているため、再実行が必要です。",
        "severity": "blocker",
    }
    if not any(str(item.get("code") or "").strip() == "reparse_stale" for item in apply_details if isinstance(item, dict)):
        apply_details.append(stale_detail)
    if not any(str(item.get("code") or "").strip() == "reparse_stale" for item in confirm_details if isinstance(item, dict)):
        confirm_details.append(stale_detail)
    payload["apply_blocker_details"] = apply_details
    payload["confirm_blocker_details"] = confirm_details
    payload["can_apply"] = False
    payload["can_confirm"] = False
    return payload


def _hakodate_blocked_draft_sheet_payload(order_id: str, error: str, data: dict | None = None) -> dict:
    assignment = data.get("assignment") if isinstance(data, dict) and isinstance(data.get("assignment"), dict) else {}
    blockers = ["monthly_menu_object_missing", "rows_empty"] if error == "menu_entries_missing" else [error, "rows_empty"]
    blockers = list(dict.fromkeys(str(item) for item in blockers if str(item).strip()))
    return {
        "order_id": order_id,
        "fields": [],
        "header": [],
        "rows": [],
        "row_ids": [],
        "cell_confidence_rows": [],
        "cell_provenance_rows": [],
        "source": "review_blocked",
        "quantity_assignment_strategy": "hakodate",
        "review_state": "blocked",
        "can_apply": False,
        "can_confirm": False,
        "blockers": blockers,
        "apply_blockers": blockers,
        "confirm_blockers": blockers,
        "warnings": blockers,
        "hakodate_assignment": assignment,
        "hakodate_projection_metrics": {},
    }


def _workflow_v2_or_404(result: tuple[dict | None, str | None]) -> dict:
    payload, error = result
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail=error)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return payload or {}


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
    _, created = create_ocr_job(ocr_job_id, input_reference=input_reference, status="running")
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


@router.put("/{order_id}/workflow-v2/sheet", dependencies=[Depends(require_role("operator"))])
def save_order_workflow_v2_sheet(order_id: str, body: WorkflowV2SheetSaveBody):
    return _workflow_v2_or_404(
        order_workflow_v2_service.save_sheet(
            order_id=order_id,
            sheet=body.sheet,
            edited_by=body.edited_by,
        )
    )


@router.post("/{order_id}/workflow-v2/bagging", dependencies=[Depends(require_role("operator"))])
def run_order_workflow_v2_bagging(order_id: str):
    return _workflow_v2_or_404(order_workflow_v2_service.run_bagging(order_id))


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
    _raise_legacy_order_workflow_gone("draft-sheet")
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    _ = (quantity_assignment_strategy, sheet_mode)
    draft, draft_error = order_service.ensure_hakodate_evidence_draft_current(
        order_id,
        edited_by="auto-hakodate-evidence-draft-sheet",
    )
    if draft_error and draft_error != "already_current":
        payload = _hakodate_blocked_draft_sheet_payload(order_id, draft_error, None)
        return payload if compact else _attach_reparse_sheet_state(order_id, payload)
    if not isinstance(draft, dict) or not isinstance(draft.get("draft_sheet_json"), dict):
        payload = _hakodate_blocked_draft_sheet_payload(order_id, "hakodate_sheet_artifact_missing", None)
        return payload if compact else _attach_reparse_sheet_state(order_id, payload)
    projected = dict(draft.get("draft_sheet_json") or {})
    assignment = order_service.get_cached_hakodate_assignment_preview(order_id) or {}
    metrics = (
        assignment.get("metrics")
        if isinstance(assignment, dict) and isinstance(assignment.get("metrics"), dict)
        else {}
    )
    draft_blockers = [str(item).strip() for item in (draft.get("blockers_json") or []) if str(item).strip()]
    draft_warnings = [str(item).strip() for item in (draft.get("warnings_json") or []) if str(item).strip()]
    projected_blockers = [str(item).strip() for item in (projected.get("blockers") or []) if str(item).strip()]
    projected_warnings = [str(item).strip() for item in (projected.get("warnings") or []) if str(item).strip()]
    blockers = list(dict.fromkeys([*projected_blockers, *draft_blockers]))
    warnings = list(dict.fromkeys([*projected_warnings, *draft_warnings]))
    payload = dict(projected)
    payload.update(
        {
            "order_id": order_id,
            "source": str(projected.get("source") or "saved_hakodate_draft_sheet"),
            "quantity_assignment_strategy": "hakodate",
            "review_state": str(draft.get("draft_state") or projected.get("review_state") or "draft_ready"),
            "can_apply": False,
            "can_confirm": False,
            "apply_blockers": blockers,
            "confirm_blockers": blockers,
            "hakodate_assignment": (
                order_service._compact_hakodate_assignment_for_client(assignment)  # noqa: SLF001
                if compact
                else assignment
            ),
            "hakodate_projection_metrics": metrics,
            "warnings": warnings,
            "draft_id": draft.get("id"),
            "base_evidence_run_id": draft.get("base_evidence_run_id"),
        }
    )
    return payload if compact else _attach_reparse_sheet_state(order_id, payload)


@router.get("/{order_id}/workflow-state", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_state(order_id: str, refresh: bool = Query(default=True)):
    _raise_legacy_order_workflow_gone("workflow-state")
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    workflow = order_service.get_order_workflow_state(order_id, refresh=refresh)
    if not isinstance(workflow, dict):
        raise HTTPException(status_code=404, detail="workflow state not found")
    return workflow


@router.get("/{order_id}/critical-decisions", dependencies=[Depends(require_role("operator"))])
def get_order_critical_decisions(order_id: str):
    _raise_legacy_order_workflow_gone("critical-decisions")
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return {"decisions": order_service.list_order_critical_decisions(order_id, refresh_workflow=True)}


@router.post("/{order_id}/critical-decisions/{decision_type}", dependencies=[Depends(require_role("operator"))])
def choose_order_critical_decision(order_id: str, decision_type: str, body: dict | None = None):
    _raise_legacy_order_workflow_gone("critical-decisions")
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    selected_value = str((body or {}).get("selected_value") or (body or {}).get("value") or "").strip()
    if not selected_value:
        raise HTTPException(status_code=400, detail="selected_value missing")
    result, error = order_service.choose_critical_decision(
        order_id,
        decision_type,
        selected_value,
        selected_by="operator",
    )
    if error == "decision_not_found":
        raise HTTPException(status_code=404, detail="critical decision not found")
    if error == "decision_stale":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "decision_stale",
                "message": "新しいOCR候補があるため、候補選択をやり直してください。",
            },
        )
    if error in {"facility_update_failed", "week_update_failed", "week_invalid"}:
        raise HTTPException(status_code=400, detail=error)
    if error:
        raise HTTPException(status_code=500, detail="critical decision update failed")
    return result


@router.get("/{order_id}/ocr-pages", dependencies=[Depends(require_role("operator"))])
def get_ocr_pages(
    order_id: str,
    preview_only: bool = Query(default=False),
    quantity_assignment_strategy: str | None = Query(default=None),
):
    _raise_legacy_order_workflow_gone("ocr-pages")
    data, error = order_service.get_ocr_pages(
        order_id,
        preview_only=preview_only,
        quantity_assignment_strategy=quantity_assignment_strategy,
    )
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"ocr_job_not_found", "ocr_output_not_found", "ocr_pages_not_found"}:
        raise HTTPException(status_code=404, detail="ocr pages not found")
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


@router.get("/{order_id}/ocr-sheet", dependencies=[Depends(require_role("operator"))])
def get_ocr_sheet(order_id: str):
    _raise_legacy_order_workflow_gone("ocr-sheet")
    data, error = order_service.get_ocr_sheet(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"facility_missing", "facility_not_found"}:
        raise HTTPException(status_code=400, detail=error)
    if error in RECOVERABLE_OCR_SHEET_ERRORS:
        recovered, recover_error = order_service.build_recoverable_ocr_sheet_payload(order_id, error)
        if recover_error is None and isinstance(recovered, dict):
            return _attach_reparse_sheet_state(order_id, recovered)
        raise HTTPException(status_code=400, detail=error)
    if error:
        raise HTTPException(status_code=500, detail="ocr sheet load failed")
    if isinstance(data, dict):
        return _attach_reparse_sheet_state(order_id, data)
    return data


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
    data, error = order_service.approve_order_hakodate_template_candidate(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"facility_missing", "template_unresolved", "candidate_unavailable", "candidate_signature_missing"}:
        raise HTTPException(status_code=400, detail=error)
    if error == "facility_not_found":
        raise HTTPException(status_code=404, detail="facility not found")
    if error == "validation_error":
        raise HTTPException(
            status_code=400,
            detail=(data or {}).get("validation", {}).get("errors") or ["facility template invalid"],
        )
    if error:
        raise HTTPException(status_code=500, detail=error)
    return data


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
    _raise_legacy_order_workflow_gone("ocr-apply")
    markdown = body.get("markdown") if isinstance(body, dict) else None
    header = body.get("header") if isinstance(body, dict) else None
    rows = body.get("rows") if isinstance(body, dict) else None
    ui_mode = body.get("ui_mode") if isinstance(body, dict) else None
    fields = body.get("fields") if isinstance(body, dict) else None
    row_ids = body.get("row_ids") if isinstance(body, dict) else None
    expected_revision_id = body.get("expected_revision_id") if isinstance(body, dict) else None
    expected_lines_updated_at = body.get("expected_lines_updated_at") if isinstance(body, dict) else None
    has_expected_revision = isinstance(body, dict) and "expected_revision_id" in body
    has_expected_lines_updated_at = isinstance(body, dict) and "expected_lines_updated_at" in body
    has_markdown = isinstance(markdown, str) and bool(markdown.strip())
    has_rows = isinstance(rows, list) and len(rows) > 0
    if not has_markdown and not has_rows:
        raise HTTPException(status_code=400, detail="markdown or rows is required")
    workflow = order_service.get_order_workflow_state(order_id, refresh=True)
    apply_gate = workflow.get("apply_gate") if isinstance(workflow, dict) else {}
    enforce_choice_gate = bool(
        isinstance(workflow, dict)
        and (
            str(workflow.get("evidence_run_id") or "").strip()
            or str(workflow.get("draft_id") or "").strip()
        )
    )
    raw_gate_blockers = (
        [
            str(item or "").strip()
            for item in (apply_gate.get("apply_blockers") or apply_gate.get("blockers") or [])
            if str(item or "").strip()
        ]
        if isinstance(apply_gate, dict)
        else []
    )
    apply_gate_blockers = [
        item
        for item in raw_gate_blockers
        if item
        in {
            "facility_missing",
            "week_missing",
            "facility_choice_required",
            "week_choice_required",
            "template_choice_required",
            "column_mapping_choice_required",
            "quantity_choice_required",
            "facility_unresolved",
            "week_unresolved",
        }
    ]
    if enforce_choice_gate and apply_gate_blockers:
        primary = apply_gate_blockers[0]
        messages = {
            "facility_missing": "facility is missing",
            "week_missing": "week is missing",
            "facility_choice_required": "facility choice is required",
            "week_choice_required": "week choice is required",
            "template_choice_required": "template choice is required",
            "column_mapping_choice_required": "column mapping choice is required",
            "quantity_choice_required": "quantity choice is required",
            "facility_unresolved": "facility is unresolved",
            "week_unresolved": "week is unresolved",
        }
        raise HTTPException(
            status_code=409,
            detail={
                "error": primary,
                "message": messages.get(primary, primary),
                "blockers": apply_gate_blockers,
                "workflow_state": workflow,
            },
        )
    order, error = order_service.apply_submitted_ocr_sheet(
        order_id,
        markdown=markdown if has_markdown else None,
        header=header,
        rows=rows if has_rows else None,
        ui_mode=ui_mode if isinstance(ui_mode, str) else None,
        fields=fields,
        row_ids=row_ids,
        expected_revision_id=str(expected_revision_id or "").strip() or None,
        expected_lines_updated_at=str(expected_lines_updated_at or "").strip() or None,
        enforce_revision_guard=has_expected_revision,
        enforce_lines_guard=has_expected_lines_updated_at,
    )
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"facility_missing", "facility_not_found"}:
        raise HTTPException(status_code=400, detail="facility missing")
    if error in {
        "markdown_empty",
        "rows_empty",
        "lines_empty",
        "draft_missing",
        "draft_rows_empty",
        "draft_rows_unparseable",
        "draft_lines_empty",
    }:
        raise HTTPException(status_code=400, detail=error)
    if error in {"draft_semantic_materialization_failed", "draft_materialization_mismatch"}:
        raise HTTPException(
            status_code=409,
            detail={
                "error": error,
                "message": "下書きの数量と明細化結果が一致しません。再解析または確認が必要です。",
            },
        )
    if error in {"stale_revision_conflict", "stale_lines_conflict"}:
        raise HTTPException(
            status_code=409,
            detail={"error": error, "message": "最新の注文状態に更新してから再度実行してください。"},
        )
    if error:
        raise HTTPException(status_code=500, detail="ocr apply failed")
    return order


@router.post("/{order_id}/ocr-sheet-save", dependencies=[Depends(require_role("operator"))])
def save_ocr_sheet(order_id: str, body: dict):
    _raise_legacy_order_workflow_gone("ocr-sheet-save")
    header = body.get("header") if isinstance(body, dict) else None
    rows = body.get("rows") if isinstance(body, dict) else None
    fields = body.get("fields") if isinstance(body, dict) else None
    row_ids = body.get("row_ids") if isinstance(body, dict) else None
    ui_mode = body.get("ui_mode") if isinstance(body, dict) else None
    expected_revision_id = body.get("expected_revision_id") if isinstance(body, dict) else None
    expected_lines_updated_at = body.get("expected_lines_updated_at") if isinstance(body, dict) else None
    has_expected_revision = isinstance(body, dict) and "expected_revision_id" in body
    has_expected_lines_updated_at = isinstance(body, dict) and "expected_lines_updated_at" in body
    has_rows = isinstance(rows, list) and len(rows) > 0
    if not has_rows:
        raise HTTPException(status_code=400, detail="rows is required")
    data, error = order_service.save_ocr_sheet_exact(
        order_id,
        header=header,
        rows=rows,
        fields=fields,
        row_ids=row_ids,
        ui_mode=ui_mode if isinstance(ui_mode, str) else None,
        expected_revision_id=str(expected_revision_id or "").strip() or None,
        expected_lines_updated_at=str(expected_lines_updated_at or "").strip() or None,
        enforce_revision_guard=has_expected_revision,
        enforce_lines_guard=has_expected_lines_updated_at,
    )
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error == "rows_empty":
        raise HTTPException(status_code=400, detail="rows_empty")
    if error in {"stale_revision_conflict", "stale_lines_conflict"}:
        raise HTTPException(
            status_code=409,
            detail={"error": error, "message": "最新の注文状態に更新してから保存してください。"},
        )
    if error:
        raise HTTPException(status_code=500, detail="ocr sheet save failed")
    return data


@router.post("/{order_id}/draft-sheet", dependencies=[Depends(require_role("operator"))])
def save_draft_sheet(order_id: str, body: dict):
    _raise_legacy_order_workflow_gone("draft-sheet")
    return save_ocr_sheet(order_id, body)


@router.post("/{order_id}/draft-sheet/switch-evidence", dependencies=[Depends(require_role("operator"))])
def switch_draft_sheet_evidence(order_id: str):
    _raise_legacy_order_workflow_gone("draft-sheet/switch-evidence")
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    draft, error = order_service.switch_draft_to_latest_evidence(
        order_id,
        edited_by="switch-evidence",
    )
    if error == "evidence_not_found":
        raise HTTPException(status_code=404, detail="latest evidence not found")
    if error in {"already_current", "switch_draft_unavailable"}:
        raise HTTPException(
            status_code=409,
            detail={
                "error": error,
                "message": "新しいOCR候補へはまだ切り替えられません。",
            },
        )
    if error == "switch_draft_failed":
        raise HTTPException(status_code=500, detail="failed to switch draft evidence")
    if not isinstance(draft, dict):
        raise HTTPException(status_code=500, detail="failed to switch draft evidence")
    return _flatten_draft_sheet_payload(order_id, draft)


@router.post("/{order_id}/draft-sheet/keep-current", dependencies=[Depends(require_role("operator"))])
def keep_current_draft_sheet(order_id: str):
    _raise_legacy_order_workflow_gone("draft-sheet/keep-current")
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    workflow_state, error = order_service.acknowledge_current_candidate_evidence(
        order_id,
        selected_by="operator",
    )
    if error == "candidate_not_found":
        raise HTTPException(status_code=404, detail="candidate evidence not found")
    if error in {"candidate_ack_failed", "workflow_refresh_failed"}:
        raise HTTPException(status_code=500, detail="failed to keep current draft")
    if not isinstance(workflow_state, dict):
        raise HTTPException(status_code=500, detail="failed to keep current draft")
    return workflow_state


@router.get("/{order_id}/draft-sheet/candidate-preview", dependencies=[Depends(require_role("operator"))])
def get_candidate_draft_sheet_preview(order_id: str):
    _raise_legacy_order_workflow_gone("draft-sheet/candidate-preview")
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    preview, error = order_service.get_candidate_draft_preview(order_id)
    if error == "candidate_not_found":
        raise HTTPException(status_code=404, detail="candidate evidence not found")
    if error:
        raise HTTPException(
            status_code=409,
            detail={
                "error": error,
                "message": "候補シートのプレビューをまだ表示できません。",
            },
        )
    if not isinstance(preview, dict):
        raise HTTPException(status_code=500, detail="failed to load candidate preview")
    return preview


@router.post("/{order_id}/draft-sheet/force-weekly-menu", dependencies=[Depends(require_role("operator"))])
def force_draft_sheet_weekly_menu(order_id: str, body: dict | None = None):
    _raise_legacy_order_workflow_gone("draft-sheet/force-weekly-menu")
    blank_quantities = False
    if isinstance(body, dict) and "blank_quantities" in body:
        blank_quantities = bool(body.get("blank_quantities"))
    draft, error = order_service.force_overwrite_current_sheet_with_weekly_menu(
        order_id,
        blank_quantities=blank_quantities,
    )
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error == "facility_missing":
        raise HTTPException(status_code=400, detail="facility missing")
    if error == "week_missing":
        raise HTTPException(status_code=400, detail="week missing")
    if error == "weekly_menu_missing":
        raise HTTPException(status_code=400, detail="weekly_menu_missing")
    if error:
        raise HTTPException(status_code=500, detail="failed to force weekly menu overwrite")
    if not isinstance(draft, dict):
        raise HTTPException(status_code=500, detail="failed to force weekly menu overwrite")
    return {
        "updated": True,
        "draft_payload": _flatten_draft_sheet_payload(order_id, draft),
    }


@router.post("/{order_id}/draft-sheet/force-facility-schema", dependencies=[Depends(require_role("operator"))])
def force_draft_sheet_facility_schema(order_id: str, body: dict | None = None):
    _raise_legacy_order_workflow_gone("draft-sheet/force-facility-schema")
    blank_quantities = True
    if isinstance(body, dict) and "blank_quantities" in body:
        blank_quantities = bool(body.get("blank_quantities"))
    draft, error = order_service.force_overwrite_current_sheet_with_facility_schema(
        order_id,
        blank_quantities=blank_quantities,
    )
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error == "facility_missing":
        raise HTTPException(status_code=400, detail="facility missing")
    if error == "facility_not_found":
        raise HTTPException(status_code=404, detail="facility not found")
    if error:
        raise HTTPException(status_code=500, detail="failed to force facility schema overwrite")
    if not isinstance(draft, dict):
        raise HTTPException(status_code=500, detail="failed to force facility schema overwrite")
    return {
        "updated": True,
        "draft_payload": _flatten_draft_sheet_payload(order_id, draft),
    }


@router.post("/{order_id}/draft-sheet/apply-patch-candidate", dependencies=[Depends(require_role("operator"))])
def apply_patch_candidate(order_id: str, body: dict | None = None):
    _raise_legacy_order_workflow_gone("draft-sheet/apply-patch-candidate")
    candidate_id = str((body or {}).get("candidate_id") or "").strip() or None
    result, error = order_service.apply_patch_candidate_to_draft(
        order_id,
        candidate_id=candidate_id,
        applied_by="operator",
    )
    if error == "patch_candidate_not_found":
        raise HTTPException(status_code=404, detail="patch candidate not found")
    if error in {"stale_draft_conflict", "stale_patch_candidate"}:
        raise HTTPException(status_code=409, detail=error)
    if error == "patch_candidate_not_applicable":
        raise HTTPException(status_code=400, detail=error)
    if error:
        raise HTTPException(status_code=500, detail=error)
    return result


@router.post("/{order_id}/ocr-review", dependencies=[Depends(require_role("operator"))])
def review_ocr_sheet(order_id: str, body: dict | None = None):
    _raise_legacy_order_workflow_gone("ocr-review")
    ocr_prompt = None
    ocr_provider = None
    pdf_variant = None
    if isinstance(body, dict):
        raw_prompt = body.get("ocr_prompt")
        if not isinstance(raw_prompt, str):
            raw_prompt = body.get("prompt")
        if isinstance(raw_prompt, str) and raw_prompt.strip():
            ocr_prompt = raw_prompt.strip()
        raw_provider = body.get("ocr_provider")
        if not isinstance(raw_provider, str):
            raw_provider = body.get("provider")
        if isinstance(raw_provider, str) and raw_provider.strip():
            normalized_provider = raw_provider.strip().lower()
            if normalized_provider not in {"openai", "gemini"}:
                raise HTTPException(status_code=400, detail="ocr_provider must be one of openai|gemini")
            ocr_provider = normalized_provider
        raw_pdf_variant = body.get("pdf_variant")
        if not isinstance(raw_pdf_variant, str):
            raw_pdf_variant = body.get("pdfVariant")
        if isinstance(raw_pdf_variant, str) and raw_pdf_variant.strip():
            normalized_pdf_variant = raw_pdf_variant.strip().lower()
            if normalized_pdf_variant not in {"raw", "corrected"}:
                raise HTTPException(status_code=400, detail="pdf_variant must be one of raw|corrected")
            pdf_variant = normalized_pdf_variant
    order, error = order_service.review_ocr_table_with_llm(
        order_id,
        provider=ocr_provider,
        prompt=ocr_prompt,
        pdf_variant=pdf_variant,
    )
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error == "facility_not_found":
        raise HTTPException(status_code=404, detail="facility not found")
    if error in {
        "facility_missing",
        "document_missing",
        "ocr_payload_missing",
        "rows_empty",
        "fields_empty",
    }:
        raise HTTPException(status_code=400, detail=error)
    if error == "llm_patch_candidate_persist_failed":
        raise HTTPException(status_code=500, detail="llm patch candidate persist failed")
    if error:
        raise HTTPException(status_code=500, detail="ocr review failed")
    return order


@router.delete(
    "/by-message-prefix/{prefix}",
    dependencies=[Depends(require_role("admin"))],
)
def delete_orders_by_message_prefix(prefix: str):
    removed = order_service.delete_orders_by_message_prefix(prefix)
    return {"removed": removed}


@router.post("/{order_id}/facility", status_code=status.HTTP_200_OK, dependencies=[Depends(require_role("operator"))])
def set_facility(order_id: str, body: dict):
    _raise_legacy_order_workflow_gone("facility")
    fac = body.get("facility")
    if not fac:
        raise HTTPException(status_code=400, detail="facility missing")
    result = order_service.set_facility(
        order_id,
        fac,
        expected_current_facility=str(body.get("expected_current_facility") or "").strip() or None,
        enforce_conflict_guard="expected_current_facility" in body,
        refresh_current_sheet=bool(body.get("refresh_current_sheet")),
    )
    if isinstance(result, tuple):
        updated, error = result
    else:
        updated, error = result, None
    if not updated:
        if error == "order_not_found":
            raise HTTPException(status_code=404, detail="order not found")
        if error == "stale_facility_conflict":
            raise HTTPException(
                status_code=409,
                detail={"error": error, "message": "施設設定が他の画面で更新されました。再読込してください。"},
            )
        raise HTTPException(status_code=500, detail="facility update failed")
    return {"updated": True}


@router.get("/{order_id}/week-options", dependencies=[Depends(require_role("operator"))])
def get_week_options(order_id: str):
    options, error = order_service.get_order_week_options(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    return {"options": options or []}


@router.post("/{order_id}/week", status_code=status.HTTP_200_OK, dependencies=[Depends(require_role("operator"))])
def set_week(order_id: str, body: dict):
    _raise_legacy_order_workflow_gone("week")
    week = body.get("week")
    if not week:
        raise HTTPException(status_code=400, detail="week missing")
    try:
        result = order_service.set_week(
            order_id,
            str(week),
            expected_current_week=str(body.get("expected_current_week") or "").strip() or None,
            enforce_conflict_guard="expected_current_week" in body,
            allow_non_calendar_exception=True,
        )
        if isinstance(result, tuple):
            updated, error = result
        else:
            updated, error = result, None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="week invalid") from exc
    if not updated:
        if error == "order_not_found":
            raise HTTPException(status_code=404, detail="order not found")
        if error == "stale_week_conflict":
            raise HTTPException(
                status_code=409,
                detail={"error": error, "message": "週設定が他の画面で更新されました。再読込してください。"},
            )
        raise HTTPException(status_code=500, detail="week update failed")
    return {"updated": True}


@router.put("/{order_id}/facility-template-columns", dependencies=[Depends(require_role("operator"))])
def save_facility_template_columns(order_id: str, body: dict):
    _raise_legacy_order_workflow_gone("facility-template-columns")
    columns = body.get("columns") if isinstance(body, dict) else None
    result, error = order_service.save_order_facility_template_columns(order_id, columns)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error == "facility_not_found":
        raise HTTPException(status_code=404, detail="facility not found")
    if error == "facility_missing":
        raise HTTPException(status_code=400, detail="facility missing")
    if error == "columns_invalid":
        raise HTTPException(status_code=400, detail="columns invalid")
    if error == "validation_error":
        raise HTTPException(
            status_code=400,
            detail=(result or {}).get("validation", {}).get("errors") or ["facility template invalid"],
        )
    if error:
        raise HTTPException(status_code=500, detail="facility template update failed")
    if isinstance(result, dict) and isinstance(result.get("draft"), dict):
        result = dict(result)
        result["draft_payload"] = _flatten_draft_sheet_payload(order_id, result["draft"])
    return result


@router.put("/{order_id}/lines", dependencies=[Depends(require_role("operator"))])
def update_lines(order_id: str, body: dict):
    _raise_legacy_order_workflow_gone("lines")
    if "lines" not in body:
        raise HTTPException(status_code=400, detail="lines missing")
    result = order_service.update_lines(
        order_id,
        body["lines"],
        expected_lines_updated_at=str(body.get("expected_lines_updated_at") or "").strip() or None,
        enforce_conflict_guard="expected_lines_updated_at" in body,
    )
    if isinstance(result, tuple):
        updated, error = result
    else:
        updated, error = result, None
    if not updated:
        if error == "order_not_found":
            raise HTTPException(status_code=404, detail="order not found")
        if error == "stale_lines_conflict":
            raise HTTPException(
                status_code=409,
                detail={"error": error, "message": "明細が他の画面で更新されました。再読込してください。"},
            )
        raise HTTPException(status_code=500, detail="line update failed")
    return {"updated": True}


def _enqueue_outputs_after_confirm(order_id: str) -> None:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    try:
        enqueue_outputs(order_id)
    except OutputBuildError as exc:
        order_service.set_status(order_id, "エラー")
        logger.exception("Output enqueue failed after confirm", order_id=order_id, error=str(exc))


@router.post("/{order_id}/confirm", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("operator"))])
def confirm_order(order_id: str, background_tasks: BackgroundTasks, body: dict | None = None):
    _raise_legacy_order_workflow_gone("confirm")
    expected_revision_id = str((body or {}).get("expected_revision_id") or "").strip() or None
    expected_lines_updated_at = str((body or {}).get("expected_lines_updated_at") or "").strip() or None
    has_expected_revision = isinstance(body, dict) and "expected_revision_id" in body
    has_expected_lines_updated_at = isinstance(body, dict) and "expected_lines_updated_at" in body
    order_snapshot = order_service.get_order_by_id(order_id)
    if not order_snapshot:
        raise HTTPException(status_code=404, detail="order not found")
    if has_expected_revision:
        revision_conflict = order_service._sheet_revision_conflict_detail(
            order_id=order_id,
            expected_revision_id=expected_revision_id,
        )
        if revision_conflict is not None:
            raise HTTPException(
                status_code=409,
                detail={"error": "stale_revision_conflict", "message": "別の画面で下書きが更新されました。再読込してください。"},
            )
    if has_expected_lines_updated_at:
        lines_conflict = order_service._lines_timestamp_conflict_detail(
            current_lines_updated_at=order_snapshot.get("lines_updated_at"),
            expected_lines_updated_at=expected_lines_updated_at,
        )
        if lines_conflict is not None:
            raise HTTPException(
                status_code=409,
                detail={"error": "stale_lines_conflict", "message": "明細が他の画面で更新されました。再読込してください。"},
            )
    workflow = order_service.get_order_workflow_state(order_id, refresh=True)
    apply_gate = workflow.get("apply_gate") if isinstance(workflow, dict) else {}
    gate_blockers = (
        [
            str(item or "").strip()
            for item in (apply_gate.get("confirm_blockers") or apply_gate.get("blockers") or [])
            if str(item or "").strip()
        ]
        if isinstance(apply_gate, dict)
        else []
    )
    gate_warnings = (
        [
            str(item or "").strip()
            for item in (apply_gate.get("confirm_warnings") or apply_gate.get("warnings") or [])
            if str(item or "").strip()
        ]
        if isinstance(apply_gate, dict)
        else []
    )
    if gate_blockers or (isinstance(apply_gate, dict) and not apply_gate.get("can_confirm", False)):
        primary = str((gate_blockers or gate_warnings or ["confirm_blocked"])[0] or "").strip() or "confirm_blocked"
        messages = {
            "facility_missing": "facility is missing",
            "week_missing": "week is missing",
            "weekly_menu_missing": "weekly menu is missing",
            "monthly_menu_object_missing": "monthly menu object is missing",
            "menu_entries_missing": "weekly menu entries are missing",
            "monthly_menu_facility_scope_missing": "facility-specific weekly menu entries are missing",
            "monthly_menu_lookup_failed": "monthly menu lookup failed",
            "facility_choice_required": "facility choice is required",
            "week_choice_required": "week choice is required",
            "template_choice_required": "template choice is required",
            "column_mapping_choice_required": "column mapping choice is required",
            "quantity_choice_required": "quantity choice is required",
            "facility_unresolved": "facility is unresolved",
            "week_unresolved": "week is unresolved",
            "template_unresolved": "template is unresolved",
            "evidence_view_unavailable": "ocr evidence view is unavailable",
            "evidence_edit_unavailable": "ocr evidence edit is unavailable",
            "draft_rows_empty": "draft rows are empty",
            "recovery_recommended": "ocr evidence recovery is required before confirm",
            "ocr_evidence_recovery_required": "ocr evidence recovery is required",
            "template_resolution_blocked": "template resolution is blocked",
            "reparse_stale": "reparse is stale",
            "auto_apply_blocked": "auto apply is blocked",
        }
        raise HTTPException(
            status_code=409,
            detail={
                "error": primary,
                "message": messages.get(primary, primary),
                "blockers": gate_blockers,
                "warnings": gate_warnings,
                "workflow_state": workflow,
            },
        )
    try:
        order, postprocess_payload = order_service.confirm_order_authoritatively(order_id)
    except order_service.ConfirmMaterializationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": exc.code,
                "message": exc.message,
            },
        ) from exc
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    background_tasks.add_task(order_service.finalize_confirmed_order, postprocess_payload)
    background_tasks.add_task(_enqueue_outputs_after_confirm, order_id)
    return {"accepted": True}


@router.post(
    "/{order_id}/reparse",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("operator"))],
)
async def reparse_order(order_id: str, background_tasks: BackgroundTasks, request: Request):
    _raise_legacy_order_workflow_gone("reparse")
    body = await _json_body_dict(request)
    ocr_prompt = None
    prompt_preset = None
    ocr_provider = None
    ocr_model = None
    # Explicit user-triggered reparse should follow the OCR reparse directive:
    # keep yomitoku as default baseline, then run evaluator-guided LLM inference.
    llm_assist = True
    force = False
    stale_action = "retry"
    if isinstance(body, dict):
        raw_prompt = body.get("ocr_prompt")
        if isinstance(raw_prompt, str) and raw_prompt.strip():
            ocr_prompt = raw_prompt.strip()
        raw_prompt_preset = body.get("prompt_preset")
        if not isinstance(raw_prompt_preset, str):
            raw_prompt_preset = body.get("ocr_prompt_preset")
        if not isinstance(raw_prompt_preset, str):
            raw_prompt_preset = body.get("promptPreset")
        if isinstance(raw_prompt_preset, str) and raw_prompt_preset.strip():
            normalized_prompt_preset = raw_prompt_preset.strip().lower()
            if normalized_prompt_preset not in {
                "numeric_verification",
                "column_missing",
                "row_alignment",
                "special_diet_semantics",
                "merged_cell_quantity_spans",
                "freeform",
            }:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "prompt_preset must be one of "
                        "numeric_verification|column_missing|row_alignment|special_diet_semantics|merged_cell_quantity_spans|freeform"
                    ),
                )
            prompt_preset = normalized_prompt_preset
        raw_provider = body.get("ocr_provider")
        if isinstance(raw_provider, str) and raw_provider.strip():
            normalized_provider = raw_provider.strip().lower()
            if normalized_provider == "tesseract":
                raise HTTPException(status_code=400, detail="ocr_provider=tesseract has been removed")
            if normalized_provider not in {"pipeline", "openai", "gemini"}:
                raise HTTPException(status_code=400, detail="ocr_provider must be one of pipeline|openai|gemini")
            ocr_provider = normalized_provider
        raw_model = body.get("ocr_model")
        if not (isinstance(raw_model, str) and raw_model.strip()):
            raw_model = body.get("llm_model")
        if isinstance(raw_model, str) and raw_model.strip():
            ocr_model = raw_model.strip()
        raw_llm_assist = body.get("llm_assist")
        if isinstance(raw_llm_assist, bool):
            llm_assist = raw_llm_assist
        raw_force = body.get("force")
        if isinstance(raw_force, bool):
            force = raw_force
        raw_stale_action = body.get("stale_action")
        if isinstance(raw_stale_action, str) and raw_stale_action.strip():
            normalized_stale_action = raw_stale_action.strip().lower()
            if normalized_stale_action not in {"retry", "wait"}:
                raise HTTPException(status_code=400, detail="stale_action must be retry or wait")
            stale_action = normalized_stale_action
    return _enqueue_order_reparse_job(
        order_id,
        background_tasks,
        ocr_prompt=ocr_prompt,
        prompt_preset=prompt_preset,
        ocr_provider=ocr_provider,
        ocr_model=ocr_model,
        llm_assist=llm_assist,
        force=force,
        stale_action=stale_action,
        request_mode="llm_reparse" if llm_assist else "ocr_reparse",
    )


@router.post(
    "/{order_id}/ocr-rerun",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("operator"))],
)
async def rerun_ocr_pipeline(order_id: str, background_tasks: BackgroundTasks, request: Request):
    _raise_legacy_order_workflow_gone("ocr-rerun")
    body = await _json_body_dict(request)
    stale_action = "retry"
    force = False
    if isinstance(body, dict):
        raw_provider = body.get("ocr_provider")
        if not isinstance(raw_provider, str):
            raw_provider = body.get("provider")
        if isinstance(raw_provider, str) and raw_provider.strip():
            normalized_provider = raw_provider.strip().lower()
            if normalized_provider == "tesseract":
                raise HTTPException(status_code=400, detail="ocr_provider=tesseract has been removed")
            raise HTTPException(status_code=400, detail="ocr_provider is not supported for Hakodate OCR rerun")
        raw_stale_action = body.get("stale_action")
        if isinstance(raw_stale_action, str) and raw_stale_action.strip():
            normalized_stale_action = raw_stale_action.strip().lower()
            if normalized_stale_action not in {"retry", "wait"}:
                raise HTTPException(status_code=400, detail="stale_action must be retry or wait")
            stale_action = normalized_stale_action
        force = body.get("force") is True
    result = _enqueue_order_evidence_rerun(
        order_id,
        background_tasks,
        stale_action=stale_action,
        force=force,
    )
    result["mode"] = "pipeline_rerun"
    return result


@router.post(
    "/{order_id}/ocr-recover",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("operator"))],
)
def recover_ocr(order_id: str, background_tasks: BackgroundTasks):
    _raise_legacy_order_workflow_gone("ocr-recover")
    result = _enqueue_order_evidence_rerun(
        order_id,
        background_tasks,
        stale_action="retry",
    )
    result["mode"] = "pipeline_recovery"
    return result
