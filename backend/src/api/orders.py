import json
from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks
from fastapi.responses import Response, JSONResponse
from loguru import logger

from src.services import order_service, config_service
from src.services.ocr_job_service import get_job as get_ocr_job, get_jobs as get_ocr_jobs, update_job as update_ocr_job
from src.workers.output_worker import enqueue_outputs, OutputBuildError
from src.api.auth import require_role
from src.services.storage_service import load_bytes_from_uri

router = APIRouter()


def _is_read_timeout_error(value: object) -> bool:
    return isinstance(value, str) and "read operation timed out" in value.lower()


def _run_reparse_background(order_id: str, ocr_prompt: str | None) -> None:
    try:
        _, error = order_service.reparse_order(order_id, ocr_prompt=ocr_prompt)
        if error:
            logger.warning("Reparse background failed", order_id=order_id, error=error)
    except Exception as exc:  # noqa: BLE001
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
    return {"orders": orders}


@router.get("/{order_id}", dependencies=[Depends(require_role("operator"))])
def get_order(order_id: str):
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    job_id = order.get("ocr_job_id")
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
                    should_update = output_status and output_status != job.get("status")
                    should_update = should_update or bool(job.get("error_message"))
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


@router.post("/{order_id}/ocr-apply", dependencies=[Depends(require_role("operator"))])
def apply_ocr_markdown(order_id: str, body: dict):
    markdown = body.get("markdown")
    if not markdown:
        raise HTTPException(status_code=400, detail="markdown missing")
    order, error = order_service.apply_ocr_markdown(order_id, markdown)
    if error == "order_not_found":
        raise HTTPException(status_code=404, detail="order not found")
    if error in {"facility_missing", "facility_not_found"}:
        raise HTTPException(status_code=400, detail="facility missing")
    if error in {"markdown_empty", "rows_empty", "lines_empty"}:
        raise HTTPException(status_code=400, detail=error)
    if error:
        raise HTTPException(status_code=500, detail="ocr apply failed")
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
    if isinstance(body, dict):
        raw_prompt = body.get("ocr_prompt")
        if isinstance(raw_prompt, str) and raw_prompt.strip():
            ocr_prompt = raw_prompt.strip()
    order = order_service.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    if not order.get("facility"):
        raise HTTPException(status_code=400, detail="facility missing")
    if not order.get("document"):
        raise HTTPException(status_code=404, detail="document not found")
    if not config_service.get_facility_config(order.get("facility")):
        raise HTTPException(status_code=404, detail="facility not found")
    background_tasks.add_task(_run_reparse_background, order_id, ocr_prompt)
    return {"accepted": True, "ocr_job_id": f"OCR-{order_id}"}
