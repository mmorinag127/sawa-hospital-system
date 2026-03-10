import json
import os
from datetime import datetime, timedelta, date as dt_date
from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks
from fastapi.responses import Response, JSONResponse
from loguru import logger

from src.services import order_service, config_service
from src.services.output_builder import rebuild_bags
from src.services.ocr_job_service import (
    create_job as create_ocr_job,
    get_job as get_ocr_job,
    get_jobs as get_ocr_jobs,
    update_job as update_ocr_job,
)
from src.workers.output_worker import enqueue_outputs, OutputBuildError
from src.api.auth import require_role
from src.services.storage_service import load_bytes_from_uri

router = APIRouter()


def _is_read_timeout_error(value: object) -> bool:
    return isinstance(value, str) and "read operation timed out" in value.lower()


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


def _is_running_status(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"running", "pending"}


def _get_ocr_stale_minutes() -> int:
    raw = os.getenv("OCR_JOB_STALE_MINUTES", "30")
    try:
        return int(raw)
    except ValueError:
        return 30


def _apply_stale_ocr_status(order: dict, job: dict | None) -> None:
    if not job:
        return
    status = (order.get("ocr_status") or job.get("status") or "").lower()
    if status not in {"running", "pending"}:
        return
    updated_at = job.get("updated_at")
    if not isinstance(updated_at, datetime):
        return
    stale_minutes = _get_ocr_stale_minutes()
    if stale_minutes <= 0:
        return
    if updated_at < datetime.utcnow() - timedelta(minutes=stale_minutes):
        order["ocr_status"] = "stalled"
        order["ocr_error"] = order.get("ocr_error") or f"timeout>{stale_minutes}m"
        order["ocr_updated_at"] = updated_at


def _run_reparse_background(
    order_id: str,
    ocr_prompt: str | None,
    ocr_provider: str | None = None,
    llm_assist: bool = False,
) -> None:
    try:
        _, error = order_service.reparse_order(
            order_id,
            ocr_prompt=ocr_prompt,
            ocr_provider=ocr_provider,
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
                metrics={"error": str(exc)},
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to update OCR job status after reparse crash", order_id=order_id)
        logger.exception("Reparse background crashed", order_id=order_id, error=str(exc))


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
            _apply_stale_ocr_status(order, job)
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
    job = None
    if job_id:
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
    cached_payload = order_service.get_cached_ocr_payload(order_id)
    cached_status = _derive_status_from_payload(cached_payload)
    current_status = order.get("ocr_status")
    if cached_status and (not isinstance(current_status, str) or not current_status.strip()) and not _is_running_status(current_status):
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
        if isinstance(cached_payload, dict) and cached_payload.get("error"):
            order["ocr_error"] = cached_payload.get("error")
        if isinstance(cached_payload, dict) and cached_payload.get("metrics"):
            order["ocr_metrics"] = cached_payload.get("metrics")
        if job and job.get("updated_at"):
            order["ocr_updated_at"] = job.get("updated_at")
    _apply_stale_ocr_status(order, job)
    return order


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


@router.get("/{order_id}/ocr-pages", dependencies=[Depends(require_role("operator"))])
def get_ocr_pages(order_id: str):
    data, error = order_service.get_ocr_pages(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"ocr_job_not_found", "ocr_output_not_found", "ocr_pages_not_found"}:
        raise HTTPException(status_code=404, detail="ocr pages not found")
    if error == "ocr_output_pending":
        return JSONResponse(status_code=202, content={"pending": True})
    if error == "ocr_output_invalid":
        raise HTTPException(status_code=500, detail="ocr output invalid")
    return data


@router.get("/{order_id}/ocr-sheet", dependencies=[Depends(require_role("operator"))])
def get_ocr_sheet(order_id: str):
    data, error = order_service.get_ocr_sheet(order_id)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {
        "facility_missing",
        "facility_not_found",
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
    }:
        raise HTTPException(status_code=400, detail=error)
    if error:
        raise HTTPException(status_code=500, detail="ocr sheet load failed")
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
    has_markdown = isinstance(markdown, str) and bool(markdown.strip())
    has_rows = isinstance(rows, list) and len(rows) > 0
    if not has_markdown and not has_rows:
        raise HTTPException(status_code=400, detail="markdown or rows is required")
    order, error = order_service.apply_ocr_table(
        order_id,
        markdown=markdown if has_markdown else None,
        header=header,
        rows=rows if has_rows else None,
        ui_mode=ui_mode if isinstance(ui_mode, str) else None,
        fields=fields,
        row_ids=row_ids,
    )
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"facility_missing", "facility_not_found"}:
        raise HTTPException(status_code=400, detail="facility missing")
    if error in {"markdown_empty", "rows_empty", "lines_empty"}:
        raise HTTPException(status_code=400, detail=error)
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
    )
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error == "rows_empty":
        raise HTTPException(status_code=400, detail="rows_empty")
    if error:
        raise HTTPException(status_code=500, detail="ocr sheet save failed")
    return data


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
    updated = order_service.set_facility(order_id, fac)
    if not updated:
        raise HTTPException(status_code=404, detail="order not found")
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
        updated = order_service.set_week(order_id, str(week))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="week invalid") from exc
    if not updated:
        raise HTTPException(status_code=404, detail="order not found")
    return {"updated": True}


@router.put("/{order_id}/facility-template-columns", dependencies=[Depends(require_role("admin"))])
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
    return result


@router.put("/{order_id}/lines", dependencies=[Depends(require_role("operator"))])
def update_lines(order_id: str, body: dict):
    if "lines" not in body:
        raise HTTPException(status_code=400, detail="lines missing")
    updated = order_service.update_lines(order_id, body["lines"])
    if not updated:
        raise HTTPException(status_code=404, detail="order not found")
    return {"updated": True}


@router.post("/{order_id}/confirm", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_role("operator"))])
def confirm_order(order_id: str):
    order = order_service.confirm_order(order_id)
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
    ocr_provider = None
    # Explicit user-triggered reparse should follow the OCR reparse directive:
    # keep yomitoku as default baseline, then run evaluator-guided LLM inference.
    llm_assist = True
    if isinstance(body, dict):
        raw_prompt = body.get("ocr_prompt")
        if isinstance(raw_prompt, str) and raw_prompt.strip():
            ocr_prompt = raw_prompt.strip()
        raw_provider = body.get("ocr_provider")
        if isinstance(raw_provider, str) and raw_provider.strip():
            normalized_provider = raw_provider.strip().lower()
            if normalized_provider not in {"pipeline", "tesseract", "openai", "gemini"}:
                raise HTTPException(status_code=400, detail="ocr_provider must be one of pipeline|tesseract|openai|gemini")
            ocr_provider = normalized_provider
        raw_llm_assist = body.get("llm_assist")
        if isinstance(raw_llm_assist, bool):
            llm_assist = raw_llm_assist
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
    input_reference = str(order.get("document") or "")
    create_ocr_job(ocr_job_id, input_reference=input_reference, status="running")
    update_ocr_job(
        ocr_job_id,
        status="running",
        error_message=None,
        template_id=None,
        output_reference=None,
        metrics=None,
        input_reference=input_reference,
    )
    background_tasks.add_task(_run_reparse_background, order_id, ocr_prompt, ocr_provider, llm_assist)
    return {"accepted": True, "ocr_job_id": ocr_job_id}
