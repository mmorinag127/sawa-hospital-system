import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from src.workers import celery_app
from src.workers.ingest_mail_adapter import parse_ingest_payload
from src.services.order_service import (
    create_order_from_ingest,
    finalize_first_pass_side_effects,
    reconcile_completed_ocr_job,
    reparse_order as run_order_reparse,
)
from src.services import config_service, ocr_evidence_service
from src.services.ingest_policy import (
    parse_date_string,
    month_id_from_dates,
    should_skip_ocr,
    retry_backoff_seconds,
)
from src.services.storage_service import load_bytes_from_uri
from src.services.storage_service import (
    StorageService,
    save_output_json,
    save_output_bytes_to_gcs,
    get_default_output_bucket,
)
from src.services.fax_extractor import extract_fax_data, filter_tokens_by_box
from src.services.grid_detector import detect_table_grid
from src.services.fax_parser import parse_order_lines
from src.services.ocr_job_service import (
    create_job,
    describe_job_state,
    get_auto_recovery_interval_seconds,
    get_auto_recovery_max_attempts,
    get_job,
    get_latest_order_job,
    get_job_recovery_attempts,
    is_job_recovery_due,
    list_recoverable_jobs,
    update_job,
)
from src.services.ocr_pipeline_service import (
    OCRPipelineOutputPendingError,
    is_ocr_pipeline_output_pending,
    run_ocr_pipeline,
)
from src.services import ingest_job_service
from src.services.uploaded_pdf_service import (
    backfill_uploaded_pdfs_from_ingest_jobs,
    build_ingest_payload,
    claim_uploaded_pdf,
    is_uploaded_pdf_completion_ready,
    list_ready_uploaded_pdf_ids,
    mark_uploaded_pdf_completed,
    refresh_uploaded_pdf_links,
    schedule_uploaded_pdf_retry,
)
from loguru import logger

_INGEST_MAX_WORKERS = int(os.getenv("INGEST_MAX_WORKERS", "4") or 4)
_INGEST_EXECUTOR = ThreadPoolExecutor(max_workers=_INGEST_MAX_WORKERS)
_AUTO_REPARSE_MAX_WORKERS = int(os.getenv("OCR_AUTO_LLM_REPARSE_MAX_WORKERS", "2") or 2)
_AUTO_REPARSE_EXECUTOR = ThreadPoolExecutor(max_workers=_AUTO_REPARSE_MAX_WORKERS)
_UPLOADED_PDF_RECOVERY_THREAD: threading.Thread | None = None
_UPLOADED_PDF_RECOVERY_LOCK = threading.Lock()
_UPLOADED_PDF_RECOVERY_STOP = threading.Event()
_UPLOADED_PDF_INFLIGHT: set[str] = set()


def _uploaded_pdf_worker_instance() -> str:
    revision = str(os.getenv("K_REVISION", "local") or "local").strip() or "local"
    return f"{revision}:{os.getpid()}"


def _uploaded_pdf_recovery_interval_seconds() -> float:
    raw = str(os.getenv("UPLOADED_PDF_RECOVERY_INTERVAL_SECONDS", "15") or "").strip()
    try:
        return max(float(raw), 1.0)
    except ValueError:
        return 15.0


def _uploaded_pdf_recovery_enabled() -> bool:
    raw = str(os.getenv("UPLOADED_PDF_RECOVERY_ENABLED", "1") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return True


def _ingest_runs_inline() -> bool:
    return os.getenv("INGEST_RUN_INLINE", "").lower() == "true" or bool(os.getenv("PYTEST_CURRENT_TEST"))


def _wait_for_pipeline_output_on_ingest() -> bool:
    raw = str(os.getenv("OCR_PIPELINE_WAIT_FOR_OUTPUT_ON_INGEST", "") or "").strip().lower()
    if raw:
        return raw not in {"0", "false", "no", "off"}
    return bool(str(os.getenv("OCR_PIPELINE_URL", "") or "").strip())


def _next_ocr_job_recovery_at() -> str:
    return (
        datetime.utcnow() + timedelta(seconds=get_auto_recovery_interval_seconds())
    ).isoformat()


def _first_pass_job_metrics(
    job_id: str,
    *,
    processing_stage: str,
    result_state: str,
    failed_cells: int | None = None,
    error: str | None = None,
) -> dict[str, object]:
    current = get_job(job_id) or {}
    metrics = dict(current.get("metrics") or {})
    metrics.update(
        {
            "request_mode": "ingest_first_pass",
            "processing_stage": processing_stage,
            "result_state": result_state,
            "stage_updated_at": datetime.utcnow().isoformat(),
        }
    )
    if failed_cells is not None:
        metrics["failed_cells"] = failed_cells
    if error:
        metrics["error"] = error
    return metrics


def _mark_ocr_job_awaiting_output(
    job_id: str,
    *,
    error_message: str,
    input_reference: str | None,
    output_reference: str | None,
    request_mode: str,
    facility_id: str | None = None,
    order_id: str | None = None,
    preferred_template_id: str | None = None,
    preferred_template_ids: list[str] | None = None,
    increment_recovery_count: bool = False,
) -> dict | None:
    current = get_job(job_id) or {}
    metrics = dict(current.get("metrics") or {})
    recovery_count = get_job_recovery_attempts(current)
    if increment_recovery_count:
        recovery_count += 1
    metrics.update(
        {
            "request_mode": request_mode,
            "processing_stage": "ocr_pipeline",
            "result_state": "awaiting_output",
            "error": "ocr_output_pending",
            "auto_recovery_count": recovery_count,
            "awaiting_output_since": str(metrics.get("awaiting_output_since") or datetime.utcnow().isoformat()),
            "next_recovery_at": _next_ocr_job_recovery_at(),
            "stage_updated_at": datetime.utcnow().isoformat(),
            "facility_id": facility_id,
            "order_id": order_id,
            "preferred_template_id": preferred_template_id,
            "preferred_template_ids": [item for item in (preferred_template_ids or []) if item],
            "output_reference": output_reference,
            "input_reference": input_reference,
        }
    )
    return update_job(
        job_id,
        status="awaiting_output",
        input_reference=input_reference,
        output_reference=output_reference,
        error_message=error_message,
        metrics=metrics,
    )


def _mark_ocr_job_final_failed(job_id: str, *, error_message: str) -> dict | None:
    current = get_job(job_id) or {}
    metrics = dict(current.get("metrics") or {})
    metrics.update(
        {
            "processing_stage": "ocr_pipeline",
            "result_state": "hard_failed",
            "stage_updated_at": datetime.utcnow().isoformat(),
            "next_recovery_at": None,
        }
    )
    return update_job(
        job_id,
        status="failed",
        error_message=error_message,
        metrics=metrics,
    )


def _load_completed_ocr_output(output_reference: str | None) -> dict | None:
    if not output_reference:
        return None
    try:
        payload = load_bytes_from_uri(output_reference)
        parsed = json.loads(payload.decode("utf-8"))
    except Exception:
        return None
    if is_ocr_pipeline_output_pending(parsed):
        return None
    return parsed if isinstance(parsed, dict) else None


def _resubmit_ocr_job(job: dict[str, object]) -> bool:
    job_id = str(job.get("id") or "").strip()
    if not job_id:
        return False
    input_reference = str(job.get("input_reference") or "").strip() or None
    if not input_reference:
        _mark_ocr_job_final_failed(job_id, error_message="ocr_recovery_input_missing")
        return False
    metrics = job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    try:
        pdf_bytes = load_bytes_from_uri(input_reference)
        output = run_ocr_pipeline(
            pdf_bytes=pdf_bytes,
            job_id=job_id,
            facility_id=str(metrics.get("facility_id") or "").strip() or None,
            input_reference=input_reference,
            preferred_template_id=str(metrics.get("preferred_template_id") or "").strip() or None,
            preferred_template_ids=[
                str(item).strip()
                for item in (metrics.get("preferred_template_ids") or [])
                if str(item).strip()
            ],
            force_upload=True,
            wait_for_output=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR job recovery resubmit failed", job_id=job_id, error=str(exc))
        if get_job_recovery_attempts(job) + 1 >= get_auto_recovery_max_attempts():
            _mark_ocr_job_final_failed(job_id, error_message=f"ocr_recovery_exhausted:{exc}")
        else:
            _mark_ocr_job_awaiting_output(
                job_id,
                error_message=f"ocr_recovery_retry_failed:{exc}",
                input_reference=input_reference,
                output_reference=str(job.get("output_reference") or "").strip() or None,
                request_mode=str(metrics.get("request_mode") or "ingest_first_pass").strip() or "ingest_first_pass",
                facility_id=str(metrics.get("facility_id") or "").strip() or None,
                order_id=str(metrics.get("order_id") or "").strip() or None,
                preferred_template_id=str(metrics.get("preferred_template_id") or "").strip() or None,
                preferred_template_ids=[
                    str(item).strip()
                    for item in (metrics.get("preferred_template_ids") or [])
                    if str(item).strip()
                ],
                increment_recovery_count=True,
            )
        return False
    _mark_ocr_job_awaiting_output(
        job_id,
        error_message="ocr_output_pending",
        input_reference=str(output.get("input_reference") or input_reference or "").strip() or input_reference,
        output_reference=str(output.get("output_reference") or "").strip() or None,
        request_mode=str(metrics.get("request_mode") or "ingest_first_pass").strip() or "ingest_first_pass",
        facility_id=str(metrics.get("facility_id") or "").strip() or None,
        order_id=str(metrics.get("order_id") or "").strip() or None,
        preferred_template_id=str(metrics.get("preferred_template_id") or "").strip() or None,
        preferred_template_ids=[
            str(item).strip()
            for item in (metrics.get("preferred_template_ids") or [])
            if str(item).strip()
        ],
        increment_recovery_count=True,
    )
    return True


def run_ocr_job_recovery_once(limit: int | None = None) -> int:
    recovery_limit = limit or _INGEST_MAX_WORKERS
    recovered = 0
    for job in list_recoverable_jobs(limit=recovery_limit):
        job_id = str(job.get("id") or "").strip()
        if not job_id:
            continue
        output_reference = str(job.get("output_reference") or "").strip() or None
        if output_reference:
            payload = _load_completed_ocr_output(output_reference)
            if isinstance(payload, dict):
                if reconcile_completed_ocr_job(job_id):
                    recovered += 1
                continue
        if not is_job_recovery_due(job):
            continue
        if get_job_recovery_attempts(job) >= get_auto_recovery_max_attempts():
            _mark_ocr_job_final_failed(job_id, error_message="ocr_recovery_exhausted")
            recovered += 1
            continue
        if _resubmit_ocr_job(job):
            recovered += 1
    return recovered


def _submit_uploaded_pdf_job(uploaded_pdf_id: str) -> bool:
    token = str(uploaded_pdf_id or "").strip()
    if not token:
        return False
    with _UPLOADED_PDF_RECOVERY_LOCK:
        if token in _UPLOADED_PDF_INFLIGHT:
            return False
        _UPLOADED_PDF_INFLIGHT.add(token)

    def _runner() -> None:
        try:
            process_uploaded_pdf_job(token)
        finally:
            with _UPLOADED_PDF_RECOVERY_LOCK:
                _UPLOADED_PDF_INFLIGHT.discard(token)

    _INGEST_EXECUTOR.submit(_runner)
    return True


def _auto_llm_reparse_enabled() -> bool:
    raw = str(os.getenv("OCR_AUTO_LLM_REPARSE_ON_INGEST", "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _looks_like_first_pass_ocr_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("table_raw"), str) and payload.get("table_raw", "").strip():
        return True
    pages = payload.get("pages")
    if isinstance(pages, list) and pages:
        return True
    tables = payload.get("tables")
    if isinstance(tables, list) and tables:
        return True
    return False


def _run_auto_llm_reparse(order_id: str, *, provider: str | None = None) -> None:
    try:
        updated, error = run_order_reparse(
            order_id,
            ocr_provider=provider,
            llm_assist=True,
        )
        logger.info(
            "Auto LLM reparse finished",
            order_id=order_id,
            provider=provider or "default",
            updated=bool(updated),
            error=error,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto LLM reparse failed", order_id=order_id, error=str(exc))


def _enqueue_auto_llm_reparse(
    order: dict | None,
    *,
    ocr_status: str,
    pipeline_output: dict | None,
) -> None:
    if not _auto_llm_reparse_enabled():
        return
    if os.getenv("PYTEST_CURRENT_TEST") and isinstance(_AUTO_REPARSE_EXECUTOR, ThreadPoolExecutor):
        logger.info("Skipping auto LLM reparse background submit during pytest")
        return
    if str(ocr_status or "").strip().lower() != "success":
        return
    if not _looks_like_first_pass_ocr_payload(pipeline_output):
        return
    if not isinstance(order, dict):
        return
    order_id = str(order.get("id") or "").strip()
    facility_id = str(order.get("facility") or "").strip()
    document_uri = str(order.get("document") or "").strip()
    if not order_id or not facility_id or not document_uri:
        return
    existing_job = get_latest_order_job(order_id)
    if isinstance(existing_job, dict):
        job_state = describe_job_state(existing_job)
        if str(job_state.get("status") or "").strip().lower() in {"running", "pending", "stalled"}:
            logger.info(
                "Skipping auto LLM reparse because reparse job is already active",
                order_id=order_id,
                job_status=job_state.get("status"),
            )
            return
    provider = str(os.getenv("OCR_AUTO_LLM_REPARSE_PROVIDER", "") or "").strip().lower() or None
    _AUTO_REPARSE_EXECUTOR.submit(_run_auto_llm_reparse, order_id, provider=provider)


def _build_pipeline_match_text(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    seen: set[str] = set()

    def _push(value: object) -> None:
        if value is None:
            return
        text = str(value).strip()
        if not text or text in seen:
            return
        seen.add(text)
        parts.append(text)

    def _push_row(row: object) -> None:
        if isinstance(row, list):
            values = [str(cell).strip() for cell in row if str(cell).strip()]
            if values:
                _push(" ".join(values))
            return
        if isinstance(row, dict):
            values = [
                str(value).strip()
                for value in row.values()
                if not isinstance(value, (dict, list)) and str(value).strip()
            ]
            if values:
                _push(" ".join(values))

    _push(payload.get("facility_name"))
    for value in payload.get("date_strings") or []:
        _push(value)
    roi_extraction = payload.get("roi_extraction")
    if isinstance(roi_extraction, dict):
        _push(roi_extraction.get("facility_name"))
        _push(roi_extraction.get("menu_band"))
        _push(roi_extraction.get("notes"))
    table_raw = payload.get("table_raw")
    if isinstance(table_raw, str) and table_raw.strip():
        _push(table_raw)
    for key in ("table_rows", "rows", "roi_overlay_rows"):
        raw_rows = payload.get(key)
        if not isinstance(raw_rows, list):
            continue
        for row in raw_rows[:200]:
            _push_row(row)
    if isinstance(roi_extraction, dict):
        raw_rows = roi_extraction.get("overlay_rows")
        if isinstance(raw_rows, list):
            for row in raw_rows[:200]:
                _push_row(row)

    def _push_tables(tables: object) -> None:
        if not isinstance(tables, list):
            return
        for table in tables[:40]:
            if not isinstance(table, dict):
                continue
            raw_rows = table.get("rows")
            if isinstance(raw_rows, list):
                for row in raw_rows[:120]:
                    _push_row(row)
            raw_cells = table.get("cells")
            if isinstance(raw_cells, list):
                for cell in raw_cells[:400]:
                    if not isinstance(cell, dict):
                        continue
                    text = cell.get("text")
                    if text is None:
                        text = cell.get("contents")
                    _push(text)

    _push_tables(payload.get("tables"))
    pages = payload.get("pages")
    if isinstance(pages, list):
        for page in pages[:10]:
            if not isinstance(page, dict):
                continue
            _push_tables(page.get("tables"))

    return "\n".join(parts)


def _dump_ocr_debug(payload, extracted) -> None:
    dump_dir = os.getenv("OCR_DEBUG_DUMP_DIR")
    if not dump_dir:
        return
    try:
        Path(dump_dir).mkdir(parents=True, exist_ok=True)
        path = Path(dump_dir) / f"ocr_{payload.message_id}.json"
        data = {
            "message_id": payload.message_id,
            "facility_name": extracted.facility_name,
            "date_strings": extracted.date_strings,
            "table_rows": extracted.table_rows,
            "tokens": extracted.tokens,
            "ocr_provider": extracted.ocr_provider,
            "grid": (
                {
                    "table_box": extracted.grid.table_box,
                    "column_edges": extracted.grid.column_edges,
                    "row_edges": extracted.grid.row_edges,
                    "confidence": extracted.grid.confidence,
                }
                if extracted.grid
                else None
            ),
        }
        path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
        logger.info("OCR debug dumped", path=str(path))
    except Exception:  # noqa: BLE001
        logger.exception("Failed to dump OCR debug data")


def _get_ocr_storage() -> StorageService:
    base_dir = os.getenv("OCR_ARTIFACT_DIR")
    if not base_dir:
        base_dir = str(Path(__file__).resolve().parents[1] / "tmp" / "ocr-artifacts")
    return StorageService(base_dir)


def enqueue_ingest(payload: dict, force: bool = False) -> tuple[str, bool]:
    """
    For now process inline to avoid external broker dependency during bring-up.
    """
    job_id, should_enqueue = ingest_job_service.create_ingest_job(payload, force=force)
    if not should_enqueue:
        logger.info("Ingest job already completed", job_id=job_id)
        return job_id, False
    process_ingest_job(job_id)
    return job_id, True


def enqueue_ingest_async(payload: dict, force: bool = False) -> tuple[str, bool]:
    """
    Run ingest on a background thread so API handlers stay responsive.
    """
    if _ingest_runs_inline():
        return enqueue_ingest(payload, force=force)
    job_id, should_enqueue = ingest_job_service.create_ingest_job(payload, force=force)
    if not should_enqueue:
        logger.info("Ingest job already completed", job_id=job_id)
        return job_id, False
    enqueue_ingest_job_async(job_id)
    return job_id, True


def enqueue_ingest_job_async(job_id: str) -> None:
    _INGEST_EXECUTOR.submit(process_ingest_job, job_id=job_id)


def enqueue_uploaded_pdf_async(uploaded_pdf_id: str) -> None:
    if _ingest_runs_inline():
        process_uploaded_pdf_job(uploaded_pdf_id)
        return
    _submit_uploaded_pdf_job(uploaded_pdf_id)


def process_ingest_job(job_id: str) -> None:
    payload = ingest_job_service.get_ingest_payload(job_id)
    if payload is None:
        logger.warning("Ingest job missing", job_id=job_id)
        return
    if not ingest_job_service.claim_ingest_job(job_id):
        logger.info("Ingest job already claimed", job_id=job_id)
        return
    try:
        # Run the ingest logic directly. Calling the Celery task wrapper
        # (process_ingest(**payload)) can fail in multi-threaded Cloud Run
        # because the task may not be bound yet (request_stack is None).
        _process_ingest_inline(**payload)
    except Exception as exc:  # noqa: BLE001
        ingest_job_service.fail_ingest_job(job_id, str(exc))
        logger.exception("Ingest job failed", job_id=job_id)
        return
    ingest_job_service.complete_ingest_job(job_id)


def process_uploaded_pdf_job(uploaded_pdf_id: str) -> None:
    worker_instance = _uploaded_pdf_worker_instance()
    claimed = claim_uploaded_pdf(uploaded_pdf_id, worker_instance=worker_instance)
    if claimed is None:
        logger.info("Uploaded PDF not ready or already claimed", uploaded_pdf_id=uploaded_pdf_id)
        return
    payload = build_ingest_payload(claimed)
    parsed_payload = parse_ingest_payload(payload)
    job_id = str(payload.get("message_id") or "").strip()
    if not job_id:
        schedule_uploaded_pdf_retry(
            uploaded_pdf_id,
            error_code="payload_invalid",
            error_message="uploaded pdf payload missing message_id",
            worker_instance=worker_instance,
        )
        return
    linked_row = refresh_uploaded_pdf_links(uploaded_pdf_id) or claimed
    current_order_id = str(linked_row.get("current_order_id") or "").strip()
    if not current_order_id:
        try:
            create_order_from_ingest(
                parsed_payload,
                lines=None,
                ocr_attempts=0,
                document_status="processing",
                error_message="ocr_pending",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Uploaded PDF placeholder order creation failed",
                uploaded_pdf_id=uploaded_pdf_id,
                message_id=job_id,
                error=str(exc),
            )
        linked_row = refresh_uploaded_pdf_links(uploaded_pdf_id) or linked_row
        current_order_id = str(linked_row.get("current_order_id") or "").strip()
    if current_order_id:
        payload["ocr_job_id"] = f"OCR-{current_order_id}"
        payload["order_id"] = current_order_id
    current_document_id = str(linked_row.get("current_document_id") or "").strip()
    if current_document_id:
        payload["order_document_id"] = current_document_id
    existing_job = ingest_job_service.get_ingest_job_snapshot(job_id)
    existing_status = str((existing_job or {}).get("status") or "").strip().lower()
    should_force_restart = False
    if existing_status == "done":
        completed = mark_uploaded_pdf_completed(uploaded_pdf_id)
        if completed and is_uploaded_pdf_completion_ready(uploaded_pdf_id):
            return
        should_force_restart = True
    elif existing_status == "processing":
        # Uploaded PDF retry/recovery explicitly chose this row for reprocessing.
        # If the linked ingest job is still marked processing here, keep progress
        # only when the job is clearly current; otherwise restart it from the top.
        should_force_restart = (
            ingest_job_service.is_processing_snapshot_stale(existing_job)
            or int(claimed.get("attempt_count") or 0) > 1
        )
    ingest_job_service.create_ingest_job(payload, force=should_force_restart)
    process_ingest_job(job_id)
    job = ingest_job_service.get_ingest_job_snapshot(job_id)
    if job is None:
        schedule_uploaded_pdf_retry(
            uploaded_pdf_id,
            error_code="ingest_job_missing",
            error_message=f"ingest job missing after processing: {job_id}",
            worker_instance=worker_instance,
        )
        return
    job_status = str(job.get("status") or "").strip().lower()
    if job_status == "done":
        completed = mark_uploaded_pdf_completed(uploaded_pdf_id)
        if completed and is_uploaded_pdf_completion_ready(uploaded_pdf_id):
            return
        schedule_uploaded_pdf_retry(
            uploaded_pdf_id,
            error_code="order_attach_missing",
            error_message="ingest completed without linked order/document",
            worker_instance=worker_instance,
        )
        return
    error_message = str(job.get("last_error") or f"ingest job ended with status={job.get('status')}").strip()
    schedule_uploaded_pdf_retry(
        uploaded_pdf_id,
        error_code=f"ingest_{job_status or 'unknown'}",
        error_message=error_message,
        worker_instance=worker_instance,
    )


def run_uploaded_pdf_recovery_once(limit: int | None = None) -> int:
    recovery_limit = limit or _INGEST_MAX_WORKERS
    reset_ids = ingest_job_service.reset_stale_processing(limit=recovery_limit)
    if reset_ids:
        logger.warning("Reset stale ingest jobs before uploaded PDF recovery", count=len(reset_ids))
    recovered_jobs = run_ocr_job_recovery_once(limit=recovery_limit)
    if recovered_jobs:
        logger.info("Recovered OCR jobs before uploaded PDF recovery", count=recovered_jobs)
    backfill_uploaded_pdfs_from_ingest_jobs()
    ready_ids = list_ready_uploaded_pdf_ids(limit=recovery_limit)
    accepted = 0
    for uploaded_pdf_id in ready_ids:
        if _submit_uploaded_pdf_job(uploaded_pdf_id):
            accepted += 1
    return accepted


def _run_uploaded_pdf_recovery_loop() -> None:
    interval = _uploaded_pdf_recovery_interval_seconds()
    while not _UPLOADED_PDF_RECOVERY_STOP.is_set():
        try:
            run_uploaded_pdf_recovery_once(limit=_INGEST_MAX_WORKERS)
        except Exception:  # noqa: BLE001
            logger.exception("Uploaded PDF recovery loop failed")
        _UPLOADED_PDF_RECOVERY_STOP.wait(interval)


def start_uploaded_pdf_recovery_loop() -> None:
    global _UPLOADED_PDF_RECOVERY_THREAD
    if not _uploaded_pdf_recovery_enabled():
        logger.info("Uploaded PDF recovery loop disabled")
        return
    with _UPLOADED_PDF_RECOVERY_LOCK:
        if _UPLOADED_PDF_RECOVERY_THREAD is not None and _UPLOADED_PDF_RECOVERY_THREAD.is_alive():
            return
        _UPLOADED_PDF_RECOVERY_STOP.clear()
        _UPLOADED_PDF_RECOVERY_THREAD = threading.Thread(
            target=_run_uploaded_pdf_recovery_loop,
            name="uploaded-pdf-recovery",
            daemon=True,
        )
        _UPLOADED_PDF_RECOVERY_THREAD.start()
        logger.info("Uploaded PDF recovery loop started")


def _process_ingest_inline(**kwargs):
    """
    In Cloud Run we execute ingest in-process (ThreadPoolExecutor).
    Keep this path independent from Celery task plumbing to avoid request_stack issues.
    """
    payload = parse_ingest_payload(kwargs)
    policy = config_service.load_ingest_policy()
    master = config_service.load_facility_master()
    lines = None
    ocr_attempts = 0
    ocr_status = "success"
    ocr_error = None
    pipeline_output = None
    facility_candidates: list[dict[str, object]] = []
    retry_limit = int(policy.get("ocr_retry_limit", 3) or 3)
    ocr_job_id = str(payload.ocr_job_id or f"OCR-{payload.message_id}").strip() or f"OCR-{payload.message_id}"
    create_job(
        ocr_job_id,
        input_reference=payload.pdf_uri,
        order_id=payload.order_id,
        uploaded_pdf_id=payload.uploaded_pdf_id,
        order_document_id=payload.order_document_id,
        input_artifact_digest=payload.content_sha256,
    )
    update_job(
        ocr_job_id,
        status="running",
        input_reference=payload.pdf_uri,
        order_id=payload.order_id,
        uploaded_pdf_id=payload.uploaded_pdf_id,
        order_document_id=payload.order_document_id,
        input_artifact_digest=payload.content_sha256,
        error_message=None,
        metrics=_first_pass_job_metrics(
            ocr_job_id,
            processing_stage="ocr_pipeline",
            result_state="processing",
        ),
    )
    if payload.skip_ocr:
        ocr_status = "skipped"
        ocr_error = "skipped_by_request"
        retry_limit = 0
    if should_skip_ocr(payload.received_at, policy):
        logger.warning("Skipping OCR due to stale backlog", message_id=payload.message_id)
        ocr_status = "skipped"
        ocr_error = "backlog_skipped"
        retry_limit = 0
    preferred_template_id = None
    preferred_template_ids: list[str] = []
    try:
        for attempt in range(1, retry_limit + 1):
            ocr_attempts = attempt
            try:
                pdf_bytes = load_bytes_from_uri(payload.pdf_uri)
                if attempt == 1:
                    if payload.facility_hint:
                        fac_config = config_service.get_facility_config(payload.facility_hint)
                        if fac_config:
                            preferred_template_id = fac_config.get("fax_template_id")
                            raw_template_ids = fac_config.get("fax_template_ids")
                            if isinstance(raw_template_ids, list):
                                preferred_template_ids = [
                                    str(item).strip()
                                    for item in raw_template_ids
                                    if str(item).strip()
                                ]
                            if (
                                isinstance(preferred_template_id, str)
                                and preferred_template_id
                                and preferred_template_id not in preferred_template_ids
                            ):
                                preferred_template_ids.insert(0, preferred_template_id)
                    try:
                        output = run_ocr_pipeline(
                            pdf_bytes=pdf_bytes,
                            job_id=ocr_job_id,
                            facility_id=payload.facility_hint,
                            input_reference=payload.pdf_uri,
                            preferred_template_id=preferred_template_id,
                            preferred_template_ids=preferred_template_ids,
                            wait_for_output=_wait_for_pipeline_output_on_ingest(),
                        )
                        pipeline_output = output if _looks_like_first_pass_ocr_payload(output) else None
                        output_ref = None
                        bucket = get_default_output_bucket()
                        if bucket:
                            output_bytes = json.dumps(output, ensure_ascii=False).encode("utf-8")
                            output_ref = save_output_bytes_to_gcs(
                                bucket,
                                ocr_job_id,
                                "ocr_output.json",
                                output_bytes,
                                content_type="application/json; charset=utf-8",
                            )
                        else:
                            storage = _get_ocr_storage()
                            output_ref = save_output_json(storage, ocr_job_id, "ocr_output.json", output)
                        update_job(
                            ocr_job_id,
                            status=output.get("status") or "done",
                            template_id=output.get("template_id"),
                            output_reference=output_ref,
                            error_message=None,
                            metrics=_first_pass_job_metrics(
                                ocr_job_id,
                                processing_stage="ocr_pipeline",
                                result_state="done",
                                failed_cells=len(output.get("failed_cells") or []),
                            ),
                        )
                    except OCRPipelineOutputPendingError as exc:
                        logger.info(
                            "ROI OCR pipeline awaiting output",
                            job_id=ocr_job_id,
                            output_reference=exc.output_reference,
                            error=str(exc),
                        )
                        _mark_ocr_job_awaiting_output(
                            ocr_job_id,
                            error_message=str(exc),
                            input_reference=exc.input_reference,
                            output_reference=exc.output_reference,
                            request_mode="ingest_first_pass",
                            facility_id=payload.facility_hint,
                            preferred_template_id=preferred_template_id,
                            preferred_template_ids=preferred_template_ids,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("ROI OCR pipeline failed", job_id=ocr_job_id, error=str(exc))
                        update_job(
                            ocr_job_id,
                            status="failed",
                            error_message=str(exc),
                            metrics=_first_pass_job_metrics(
                                ocr_job_id,
                                processing_stage="ocr_pipeline",
                                result_state="hard_failed",
                                error=str(exc),
                            ),
                        )
                base_template = master.get("fax_template_base", {})
                extracted = extract_fax_data(
                    pdf_bytes,
                    base_template,
                    facility_id=payload.facility_hint,
                    preferred_template_id=preferred_template_id,
                )
                if extracted.facility_name and not payload.facility_name:
                    payload.facility_name = extracted.facility_name
                if extracted.date_strings and not payload.date_hints:
                    payload.date_hints = extracted.date_strings
                if not payload.facility_hint and payload.facility_name:
                    payload.facility_hint = config_service.resolve_facility_id(payload.facility_name)
                if not payload.facility_hint and isinstance(pipeline_output, dict):
                    match_text = _build_pipeline_match_text(pipeline_output)
                    candidates = config_service.match_facility_candidates(match_text)
                    facility_candidates = [
                        dict(item)
                        for item in candidates
                        if isinstance(item, dict)
                    ][:5]
                    auto_match = next((item for item in candidates if item.get("auto")), None)
                    if auto_match:
                        payload.facility_hint = auto_match.get("facility_id")
                        if not payload.facility_name:
                            payload.facility_name = auto_match.get("facility_name")
                        logger.info(
                            "Facility auto matched from OCR",
                            facility_id=payload.facility_hint,
                            reason=auto_match.get("reason"),
                            score=auto_match.get("score"),
                        )
                if not payload.facility_hint:
                    facilities = master.get("facilities", [])
                    if len(facilities) == 1:
                        fallback = facilities[0]
                        payload.facility_hint = fallback.get("facility_id")
                        if not payload.facility_name:
                            payload.facility_name = fallback.get("facility_name")
                        logger.info("Facility fallback applied", facility_id=payload.facility_hint)
                if not payload.week_hint and payload.date_hints:
                    payload.week_hint = month_id_from_dates(payload.date_hints, payload.received_at, policy)
                if payload.facility_hint:
                    facility_config = config_service.get_facility_config(payload.facility_hint)
                    if facility_config:
                        template = facility_config.get("fax_template", base_template)
                        tokens = filter_tokens_by_box(extracted.tokens, template.get("table_box"))
                        grid = detect_table_grid(pdf_bytes, template)
                        extracted.tokens = tokens
                        extracted.grid = grid
                        _dump_ocr_debug(payload, extracted)
                        default_date = None
                        if extracted.date_strings:
                            parsed_dates = []
                            for raw in extracted.date_strings:
                                parsed = parse_date_string(raw, payload.received_at)
                                if parsed:
                                    parsed_dates.append(parsed)
                            if parsed_dates:
                                default_date = min(parsed_dates)
                        lines = parse_order_lines(
                            extracted.table_rows,
                            template,
                            payload.received_at,
                            policy.get("quantity_rules", {}),
                            default_date=default_date,
                            tokens=tokens,
                            grid=grid.__dict__ if grid else None,
                            pdf_bytes=pdf_bytes,
                        )
                        if not payload.week_hint and lines:
                            line_dates = [line.get("date") for line in lines if line.get("date")]
                            if line_dates:
                                payload.week_hint = month_id_from_dates(line_dates, payload.received_at, policy)
                ocr_status = "success"
                ocr_error = None
                break
            except Exception as exc:  # noqa: BLE001
                ocr_status = "error"
                ocr_error = str(exc)
                logger.exception("OCR extraction failed", attempt=attempt)
                if attempt >= retry_limit:
                    logger.warning("OCR retries exhausted", attempts=attempt)
    except Exception:  # noqa: BLE001
        logger.exception("OCR pipeline failed; continuing without lines")
    order = create_order_from_ingest(
        payload,
        lines=lines,
        ocr_attempts=ocr_attempts or 1,
        document_status=ocr_status,
        error_message=ocr_error,
    )
    if isinstance(order, dict):
        try:
            from src.services import order_workflow_v2_service  # noqa: PLC0415

            order_workflow_v2_service.record_context_suggestion(
                order_id=str(order.get("id") or "").strip(),
                suggestion={
                    "source": "ingest_first_pass_ocr",
                    "facility_id": payload.facility_hint,
                    "facility_name": payload.facility_name,
                    "facility_candidates": facility_candidates,
                    "week_code": payload.week_hint,
                    "date_hints": payload.date_hints,
                    "confidence": "high" if payload.facility_hint and payload.week_hint else "medium",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Workflow v2 context suggestion persistence failed",
                order_id=str(order.get("id") or "").strip() or None,
                message_id=payload.message_id,
                error=str(exc),
            )
    if isinstance(order, dict) and isinstance(pipeline_output, dict):
        try:
            ocr_evidence_service.persist_evidence_run(
                order_id=str(order.get("id") or "").strip(),
                payload=pipeline_output,
                schema_version="v1_legacy",
                producer_version="ocr_pipeline_ingest",
                status="ready",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "OCR evidence persistence failed",
                order_id=str(order.get("id") or "").strip() or None,
                message_id=payload.message_id,
                error=str(exc),
            )
        finalize_first_pass_side_effects(
            job_id=ocr_job_id,
            order_id=str(order.get("id") or "").strip() or None,
            message_id=payload.message_id,
        )
    _enqueue_auto_llm_reparse(
        order,
        ocr_status=ocr_status,
        pipeline_output=pipeline_output if isinstance(pipeline_output, dict) else None,
    )


@celery_app.task(name="backend.src.workers.ingest_worker.process_ingest", bind=True, max_retries=3)
def process_ingest(self=None, **kwargs):
    try:
        return _process_ingest_inline(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ingest failed; retrying")
        if self:
            policy = config_service.load_ingest_policy()
            attempt = getattr(self.request, "retries", 0) + 1
            delay = retry_backoff_seconds(attempt, policy)
            raise self.retry(exc=exc, countdown=delay)
        raise
