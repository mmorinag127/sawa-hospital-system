import json
import os
from datetime import datetime, timedelta, date as dt_date
from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks
from fastapi.responses import Response, JSONResponse
from loguru import logger

from src.services import order_service, config_service
from src.services import shipping_status_store
from src.services.output_builder import rebuild_bags
from src.services.ocr_job_service import (
    create_job as create_ocr_job,
    describe_job_state as describe_ocr_job_state,
    get_job as get_ocr_job,
    get_jobs as get_ocr_jobs,
    get_stale_minutes as get_ocr_job_stale_minutes,
    is_job_stale as is_ocr_job_stale,
    update_job as update_ocr_job,
)
from src.workers.output_worker import enqueue_outputs, OutputBuildError
from src.api.auth import require_role
from src.services.storage_service import load_bytes_from_uri

router = APIRouter()


RECOVERABLE_OCR_SHEET_ERRORS = {
    "week_unresolved",
    "menu_entries_missing",
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


def _flatten_draft_sheet_payload(order_id: str, draft_payload: dict) -> dict:
    workflow = order_service.get_order_workflow_state(order_id, refresh=True)
    evidence = order_service.get_latest_ocr_evidence_run(order_id, backfill_from_cache=True)
    draft_json = draft_payload.get("draft_sheet_json") if isinstance(draft_payload.get("draft_sheet_json"), dict) else {}
    warnings = [
        str(item).strip()
        for item in (
            list(draft_json.get("warnings") or [])
            + list(draft_payload.get("warnings_json") or [])
        )
        if str(item).strip()
    ]
    deduped_warnings: list[str] = []
    for item in warnings:
        if item not in deduped_warnings:
            deduped_warnings.append(item)
    apply_gate = workflow.get("apply_gate") if isinstance(workflow, dict) else None
    return {
        **draft_payload,
        "fields": list(draft_json.get("fields") or []),
        "header": list(draft_json.get("header") or []),
        "rows": list(draft_json.get("rows") or []),
        "row_ids": list(draft_json.get("row_ids") or []),
        "source": str(draft_json.get("source") or draft_payload.get("draft_state") or "draft").strip() or "draft",
        "warnings": deduped_warnings,
        "review_state": (
            str((workflow or {}).get("state") or draft_payload.get("draft_state") or "draft_ready").strip()
            if isinstance(workflow, dict) or draft_payload.get("draft_state")
            else "draft_ready"
        ),
        "workflow_state": workflow,
        "apply_gate": apply_gate,
        "critical_decisions": list((workflow or {}).get("critical_decisions") or []) if isinstance(workflow, dict) else [],
        "candidate_resolution": (workflow or {}).get("candidate_resolution") if isinstance(workflow, dict) else None,
        "evidence_run_id": (evidence or {}).get("id") if isinstance(evidence, dict) else None,
        "evidence_capabilities": (evidence or {}).get("capabilities_json") if isinstance(evidence, dict) else None,
        "evidence_degraded_reasons": list((evidence or {}).get("degraded_reasons_json") or []) if isinstance(evidence, dict) else [],
    }


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
    )


def _is_order_reparse_job(job_id: object, order_id: str) -> bool:
    if not isinstance(job_id, str):
        return False
    normalized = job_id.strip()
    if not normalized:
        return False
    return normalized == f"OCR-{order_id}"


def _derive_status_from_payload(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if isinstance(status, str):
        normalized = status.strip().lower()
        if normalized and normalized not in {"running", "pending"}:
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
    return isinstance(value, str) and value.strip().lower() in {"running", "pending"}


def _get_ocr_stale_minutes() -> int:
    return get_ocr_job_stale_minutes()


def _is_job_stale(job: dict | None) -> bool:
    return is_ocr_job_stale(job)


def _mark_stale_order_reparse_job(order: dict, job: dict | None) -> dict | None:
    if not isinstance(job, dict):
        return job
    order_id = str(order.get("id") or "").strip()
    if not order_id or not _is_order_reparse_job(job.get("id"), order_id):
        return job
    if not _is_job_stale(job):
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
    order_id = str(order.get("id") or "").strip()
    cached_payload = (
        order_service.get_cached_ocr_payload(order_id)
        if order_id and _is_order_reparse_job(job.get("id"), order_id)
        else None
    )
    cached_status = _derive_status_from_payload(cached_payload) if _has_persisted_ocr_evidence(cached_payload) else None
    status = (job.get("status") or order.get("ocr_status") or "").lower()
    if status not in {"running", "pending"}:
        if (
            order_id
            and _is_order_reparse_job(job.get("id"), order_id)
            and _is_reparse_stale_timeout_error(job.get("error_message"))
            and cached_status
        ):
            order["ocr_status"] = cached_status
            order["ocr_error"] = None
            if job.get("metrics"):
                order["ocr_metrics"] = job.get("metrics")
            if job.get("updated_at"):
                order["ocr_updated_at"] = job.get("updated_at")
            return job
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
    if normalized in {"running", "pending", "stalled"}:
        return True
    if normalized == "failed" and job and not _is_order_reparse_job(job.get("id"), order_id):
        return True
    return False


def _apply_cached_status_override(order: dict, order_id: str, job: dict | None) -> dict | None:
    cached_payload = order_service.get_cached_ocr_payload(order_id)
    cached_status = _derive_status_from_payload(cached_payload)
    current_status = order.get("ocr_status")
    if not cached_status or not _should_prefer_cached_status(order_id, current_status, job):
        return job
    if job and _is_order_reparse_job(job.get("id"), order_id):
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
) -> None:
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        return
    effective_ocr_status = order.get("ocr_status")
    effective_ocr_metrics = order.get("ocr_metrics")
    if not effective_ocr_status and isinstance(ocr_job, dict) and _is_order_reparse_job(ocr_job.get("id"), order_id):
        effective_ocr_status = ocr_job.get("status")
        if not effective_ocr_metrics:
            effective_ocr_metrics = ocr_job.get("metrics")
    review = order_service.get_order_review_summary(
        order_id,
        lines_updated_at=order.get("lines_updated_at"),
        ocr_status=effective_ocr_status,
        cached_payload=cached_payload,
        ocr_metrics=effective_ocr_metrics,
        order_status=order.get("status"),
    )
    job_state = describe_ocr_job_state(ocr_job if isinstance(ocr_job, dict) else None)
    review["ocr_reparse_health"] = str(job_state.get("status") or "idle")
    review["ocr_reparse_stale_at"] = job_state.get("stale_at")
    review["ocr_reparse_stale_threshold_seconds"] = job_state.get("stale_threshold_seconds")
    review["ocr_reparse_last_job_id"] = (
        str(ocr_job.get("id") or "").strip()
        if isinstance(ocr_job, dict) and _is_order_reparse_job(ocr_job.get("id"), order_id)
        else None
    )
    if not review.get("ocr_reparse_last_error_code") and isinstance(ocr_job, dict):
        review["ocr_reparse_last_error_code"] = str(ocr_job.get("error_message") or "").strip() or None
    order.update(review)


def _attach_order_workflow_context(order: dict, *, refresh: bool = False) -> None:
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        return
    workflow = order_service.get_order_workflow_state(order_id, refresh=refresh)
    if not isinstance(workflow, dict):
        return
    if not refresh and (
        not isinstance(workflow.get("candidate_resolution"), dict)
        or not isinstance(workflow.get("apply_gate"), dict)
        or not isinstance(workflow.get("critical_decisions"), list)
    ):
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


def _is_active_order_reparse_job(job: dict | None, order_id: str) -> bool:
    if not isinstance(job, dict):
        return False
    if not _is_order_reparse_job(job.get("id"), order_id):
        return False
    normalized_status = str(job.get("status") or "").strip().lower()
    if normalized_status not in {"running", "pending"}:
        return False
    if _is_job_stale(job):
        return False
    return True


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
    existing_job = get_ocr_job(ocr_job_id)
    existing_job_state = describe_ocr_job_state(existing_job if _is_order_reparse_job(ocr_job_id, order_id) else None)
    if existing_job_state.get("status") == "stalled":
        if not force and stale_action == "wait":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "reparse_in_progress",
                    "message": "stale reparse requires retry or force",
                    "recoverable": True,
                    "ocr_job_id": ocr_job_id,
                    "stale_at": existing_job_state.get("stale_at"),
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
    _, created = create_ocr_job(ocr_job_id, input_reference=input_reference, status="running")
    if not created:
        existing_job = get_ocr_job(ocr_job_id)
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
    except Exception as exc:  # noqa: BLE001
        try:
            update_ocr_job(
                f"OCR-{order_id}",
                status="failed",
                error_message=f"reparse_crashed:{exc}",
                metrics={
                    "error": str(exc),
                    "processing_stage": "crashed",
                    "result_state": "hard_failed",
                    "confirmed_lines_retained": True,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to update OCR job status after reparse crash", order_id=order_id)
        logger.exception("Reparse background crashed", order_id=order_id, error=str(exc))


def _run_ocr_rerun_background(order_id: str, ocr_job_id: str) -> None:
    try:
        _, error = order_service.rerun_ocr_evidence_only(order_id, job_id=ocr_job_id)
        if error:
            logger.warning("OCR evidence-only rerun failed", order_id=order_id, error=error)
    except Exception as exc:  # noqa: BLE001
        current_order = order_service.get_order_by_id(order_id)
        retained_lines = bool(current_order.get("lines_updated_at")) if isinstance(current_order, dict) else False
        try:
            update_ocr_job(
                ocr_job_id,
                status="failed",
                error_message=f"ocr_rerun_crashed:{exc}",
                metrics={
                    "error": str(exc),
                    "request_mode": "ocr_rerun",
                    "processing_stage": "crashed",
                    "result_state": "hard_failed",
                    "confirmed_lines_retained": retained_lines,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to update OCR rerun status after crash", order_id=order_id)
        logger.exception("OCR evidence-only rerun crashed", order_id=order_id, error=str(exc))


def _enqueue_order_evidence_rerun(
    order_id: str,
    background_tasks: BackgroundTasks,
    *,
    stale_action: str = "retry",
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
    existing_job = get_ocr_job(ocr_job_id)
    existing_job_state = describe_ocr_job_state(existing_job if _is_order_reparse_job(ocr_job_id, order_id) else None)
    if existing_job_state.get("status") == "stalled":
        if stale_action == "wait":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "reparse_in_progress",
                    "message": "stale rerun requires retry",
                    "recoverable": True,
                    "ocr_job_id": ocr_job_id,
                    "stale_at": existing_job_state.get("stale_at"),
                    "stale_threshold_seconds": existing_job_state.get("stale_threshold_seconds"),
                },
            )
        existing_job = _mark_stale_order_reparse_job(order, existing_job)
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
    _, created = create_ocr_job(ocr_job_id, input_reference=input_reference, status="running")
    if not created:
        existing_job = get_ocr_job(ocr_job_id)
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
    try:
        workflow_state = order_service.get_order_workflow_state(order_id, refresh=True)
    except Exception:
        workflow_state = None
    background_tasks.add_task(_run_ocr_rerun_background, order_id, ocr_job_id)
    return {"accepted": True, "ocr_job_id": ocr_job_id, "workflow_state": workflow_state}


@router.get("", dependencies=[Depends(require_role("operator"))])
def list_orders(status: str | None = None, include_ocr: bool = False):
    orders = order_service.list_orders(status=status)
    if include_ocr:
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
    cache_map = order_service._load_order_ocr_cache_map(
        [str(order.get("id") or "").strip() for order in orders if str(order.get("id") or "").strip()]
    )
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
        _attach_order_review_summary(
            order,
            cached_payload=cache_map.get(order_id),
            ocr_job=review_job,
        )
        _attach_order_workflow_context(order, refresh=False)
    return {"orders": orders}


@router.post("/cache-refresh", dependencies=[Depends(require_role("admin"))])
def refresh_orders_cache(status: str | None = None):
    count = order_service.refresh_orders_cache(status=status)
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
        if job.get("output_reference") and job.get("status") in {"running", "failed"}:
            try:
                payload = load_bytes_from_uri(job["output_reference"])
                parsed = json.loads(payload.decode("utf-8"))
                output_status = parsed.get("status")
                output_template = parsed.get("template_id")
                job_error = job.get("error_message")
                should_update = bool(output_status and output_status != job.get("status"))
                should_update = should_update or bool(job_error)
                if job.get("status") == "running" and _is_order_reparse_job(job.get("id"), order_id):
                    # Reparse jobs keep running until post-processing/validation finishes.
                    # OCR output JSON "done" must not terminate the job early.
                    should_update = False
                if job.get("status") == "failed" and _is_terminal_reparse_error(job_error):
                    should_update = False
                if should_update:
                    update_ocr_job(
                        job["id"],
                        status=output_status or job.get("status"),
                        template_id=output_template or job.get("template_id"),
                        error_message=parsed.get("error"),
                        metrics=parsed.get("metrics"),
                    )
                    job = get_ocr_job(job["id"]) or job
            except Exception:
                pass
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
    job = _apply_cached_status_override(order, order_id, job)
    job = _apply_stale_ocr_status(order, job)
    _attach_order_review_summary(order, ocr_job=job)
    _attach_order_workflow_context(order, refresh=True)
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
    data = load_bytes_from_uri(uri)
    return Response(content=data, media_type="application/pdf")


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
    data, error = order_service.get_ocr_output(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"ocr_job_not_found", "ocr_output_not_found"}:
        raise HTTPException(status_code=404, detail="ocr output not found")
    if error == "ocr_output_pending":
        return JSONResponse(status_code=202, content={"pending": True})
    if error == "ocr_output_invalid":
        raise HTTPException(status_code=500, detail="ocr output invalid")
    return data


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


@router.get("/{order_id}/draft-sheet", dependencies=[Depends(require_role("operator"))])
def get_draft_sheet(order_id: str):
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    draft = order_service.get_latest_sheet_draft(
        order_id,
        backfill_from_revision=False,
        upgrade_generic_from_sheet=True,
    )
    if isinstance(draft, dict):
        return _flatten_draft_sheet_payload(order_id, draft)
    initial = order_service.build_initial_sheet_draft(order_id)
    if not isinstance(initial, dict):
        raise HTTPException(status_code=404, detail="draft sheet not found")
    return _flatten_draft_sheet_payload(order_id, {
        "id": None,
        "order_id": order_id,
        "base_evidence_run_id": initial.get("base_evidence_run_id"),
        "base_template_resolution_id": None,
        "base_menu_snapshot_id": None,
        "draft_sheet_json": initial,
        "draft_state": "draft_ready",
        "blockers_json": [],
        "warnings_json": [],
        "latest_patch_candidate_id": None,
        "edited_by": None,
        "edited_at": None,
        "created_at": None,
    })


@router.get("/{order_id}/workflow-state", dependencies=[Depends(require_role("operator"))])
def get_order_workflow_state(order_id: str):
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    workflow = order_service.get_order_workflow_state(order_id, refresh=True)
    if not isinstance(workflow, dict):
        raise HTTPException(status_code=404, detail="workflow state not found")
    return workflow


@router.get("/{order_id}/critical-decisions", dependencies=[Depends(require_role("operator"))])
def get_order_critical_decisions(order_id: str):
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return {"decisions": order_service.list_order_critical_decisions(order_id, refresh_workflow=True)}


@router.post("/{order_id}/critical-decisions/{decision_type}", dependencies=[Depends(require_role("operator"))])
def choose_order_critical_decision(order_id: str, decision_type: str, body: dict | None = None):
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
def get_ocr_pages(order_id: str):
    data, error = order_service.get_ocr_pages(order_id)
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
    def _attach_reparse_sheet_state(payload: dict) -> dict:
        reparse_job = get_ocr_job(f"OCR-{order_id}")
        reparse_state = describe_ocr_job_state(
            reparse_job if isinstance(reparse_job, dict) and _is_order_reparse_job(reparse_job.get("id"), order_id) else None
        )
        payload["reparse_health"] = reparse_state.get("status")
        payload["reparse_stale_at"] = reparse_state.get("stale_at")
        payload["reparse_stale_threshold_seconds"] = reparse_state.get("stale_threshold_seconds")
        if reparse_state.get("status") != "stalled":
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

    data, error = order_service.get_ocr_sheet(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"facility_missing", "facility_not_found"}:
        raise HTTPException(status_code=400, detail=error)
    if error in RECOVERABLE_OCR_SHEET_ERRORS:
        recovered, recover_error = order_service.build_recoverable_ocr_sheet_payload(order_id, error)
        if recover_error is None and isinstance(recovered, dict):
            return _attach_reparse_sheet_state(recovered)
        raise HTTPException(status_code=400, detail=error)
    if error:
        raise HTTPException(status_code=500, detail="ocr sheet load failed")
    if isinstance(data, dict):
        return _attach_reparse_sheet_state(data)
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


@router.post("/{order_id}/ocr-apply", dependencies=[Depends(require_role("operator"))])
def apply_ocr_markdown(order_id: str, body: dict):
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
    return save_ocr_sheet(order_id, body)


@router.post("/{order_id}/draft-sheet/switch-evidence", dependencies=[Depends(require_role("operator"))])
def switch_draft_sheet_evidence(order_id: str):
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


@router.post("/{order_id}/draft-sheet/apply-patch-candidate", dependencies=[Depends(require_role("operator"))])
def apply_patch_candidate(order_id: str, body: dict | None = None):
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
    fac = body.get("facility")
    if not fac:
        raise HTTPException(status_code=400, detail="facility missing")
    result = order_service.set_facility(
        order_id,
        fac,
        expected_current_facility=str(body.get("expected_current_facility") or "").strip() or None,
        enforce_conflict_guard="expected_current_facility" in body,
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
    week = body.get("week")
    if not week:
        raise HTTPException(status_code=400, detail="week missing")
    try:
        result = order_service.set_week(
            order_id,
            str(week),
            expected_current_week=str(body.get("expected_current_week") or "").strip() or None,
            enforce_conflict_guard="expected_current_week" in body,
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


@router.post("/{order_id}/confirm", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("operator"))])
def confirm_order(order_id: str, body: dict | None = None):
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
            payload=order_service.get_cached_ocr_payload(order_id),
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
            "menu_entries_missing": "weekly menu entries are missing",
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
        order = order_service.confirm_order(order_id)
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
    try:
        enqueue_outputs(order_id)
    except OutputBuildError as exc:
        order_service.set_status(order_id, "エラー")
        raise HTTPException(
            status_code=409,
            detail={"error": "output_failed", "message": str(exc)},
        )
    return {"accepted": True}


@router.post(
    "/{order_id}/reparse",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("operator"))],
)
def reparse_order(order_id: str, background_tasks: BackgroundTasks, body: dict | None = None):
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
                "freeform",
            }:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "prompt_preset must be one of "
                        "numeric_verification|column_missing|row_alignment|special_diet_semantics|freeform"
                    ),
                )
            prompt_preset = normalized_prompt_preset
        raw_provider = body.get("ocr_provider")
        if isinstance(raw_provider, str) and raw_provider.strip():
            normalized_provider = raw_provider.strip().lower()
            if normalized_provider not in {"pipeline", "tesseract", "openai", "gemini"}:
                raise HTTPException(status_code=400, detail="ocr_provider must be one of pipeline|tesseract|openai|gemini")
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
def rerun_ocr_pipeline(order_id: str, background_tasks: BackgroundTasks, body: dict | None = None):
    stale_action = "retry"
    if isinstance(body, dict):
        raw_stale_action = body.get("stale_action")
        if isinstance(raw_stale_action, str) and raw_stale_action.strip():
            normalized_stale_action = raw_stale_action.strip().lower()
            if normalized_stale_action not in {"retry", "wait"}:
                raise HTTPException(status_code=400, detail="stale_action must be retry or wait")
            stale_action = normalized_stale_action
    result = _enqueue_order_evidence_rerun(
        order_id,
        background_tasks,
        stale_action=stale_action,
    )
    result["mode"] = "pipeline_rerun"
    return result


@router.post(
    "/{order_id}/ocr-recover",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("operator"))],
)
def recover_ocr(order_id: str, background_tasks: BackgroundTasks):
    result = _enqueue_order_evidence_rerun(
        order_id,
        background_tasks,
        stale_action="retry",
    )
    result["mode"] = "pipeline_recovery"
    return result
