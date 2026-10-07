import json
import os
import threading
from hashlib import sha256
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from src.workers import celery_app
from src.workers.ingest_mail_adapter import parse_ingest_payload, to_job_kwargs
from src.services.order_service import (
    create_order_from_ingest,
    preview_existing_order_for_ingest,
    finalize_first_pass_side_effects,
    reconcile_completed_ocr_job,
    reparse_order as run_order_reparse,
)
from src.services import config_service, ocr_evidence_service
from src.services.workflow_attempt_guard_service import (
    capture_nonworkflow_execution, nonworkflow_execution_context,
    current_nonworkflow_execution,
    is_selected_pdf_failure,
)
from src.services.ingest_policy import (
    parse_date_string,
    require_explicit_ingest_week,
    should_skip_ocr,
    retry_backoff_seconds,
)
from src.services.storage_service import load_bytes_from_uri, load_selected_pdf_bytes
from src.services.storage_service import (
    StorageService,
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
    get_job_recovery_attempts,
    is_job_recovery_due,
    list_recoverable_jobs,
    update_job,
    complete_own_selected_pdf_failure,
    mark_own_pipeline_waiting,
    validate_own_pipeline_result,
    OwnPipelineLineageError,
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
    return False


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
    execution_input = current_nonworkflow_execution()
    if not isinstance(execution_input, dict) or execution_input.get("job_id") != job_id:
        raise ValueError("ocr_execution_input_required")
    metrics = {}
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
    metrics = {}
    metrics.update(
        {
            "request_mode": request_mode,
            "processing_stage": "ocr_pipeline",
            "result_state": "awaiting_output",
            "error": "ocr_output_pending",
            "awaiting_output_since": datetime.utcnow().isoformat(),
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
    result = mark_own_pipeline_waiting(
        job_id,
        recovery_count_increment=increment_recovery_count,
        input_reference=input_reference,
        output_reference=output_reference,
        error_message=error_message,
        metrics_patch=metrics,
    )
    if result is None:
        raise ValueError("ocr_pending_delivery_conflict")
    return result


def _mark_ocr_job_final_failed(job_id: str, *, error_message: str) -> dict | None:
    if is_selected_pdf_failure(error_message):
        return complete_own_selected_pdf_failure(job_id, error_message)
    current = get_job(job_id) or {}
    boundary = current_nonworkflow_execution()
    if get_job_request_mode(current) == "ingest_first_pass":
        if not isinstance(boundary, dict) or boundary.get("job_id") != job_id:
            return None
        captured = boundary["orders"].get(boundary.get("order_id"))
        document = captured.get("document") if isinstance(captured, dict) else None
        if not isinstance(document, dict):
            return None
        payload = parse_ingest_payload({
            "message_id": document["source_email_id"], "pdf_uri": document["storage_uri"],
            "received_at": document["received_at"], "order_id": boundary["order_id"],
            "order_document_id": document["id"],
            "facility_hint": captured["order"]["facility_code"],
            "week_hint": captured["order"]["week_code"],
        })
        failed = create_order_from_ingest(
            payload, lines=None, document_status="error", error_message=error_message,
            execution_input=boundary,
        )
        return failed.get("non_ocr_terminal_receipt")
    metrics = {}
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
        validate_own_pipeline_result(parsed, output_reference)
    except OwnPipelineLineageError:
        raise
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
        from src.services.ocr_job_service import own_pipeline_delivery_for_output

        delivery = own_pipeline_delivery_for_output(job.get("output_reference"))
        from src.services.workflow_attempt_guard_service import processing_start_pdf_receipt

        start_pdf = processing_start_pdf_receipt()
        pdf_bytes = load_selected_pdf_bytes(start_pdf["storage_uri"], start_pdf)
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
    except OwnPipelineLineageError as exc:
        _mark_ocr_job_final_failed(job_id, error_message=str(exc))
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR job recovery resubmit failed", job_id=job_id, error=str(exc))
        if get_job_recovery_attempts(job) + 1 >= get_auto_recovery_max_attempts():
            _mark_ocr_job_final_failed(job_id, error_message=f"ocr_recovery_exhausted:{exc}")
        else:
            _mark_ocr_job_awaiting_output(
                job_id,
                error_message=f"ocr_recovery_retry_failed:{exc}",
                input_reference=(metrics.get("pipeline_delivery_receipt") or {}).get("input_reference"),
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
        input_reference=output.get("input_reference"),
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
        pending_receipt = (job.get("metrics") or {}).get("pending_receipt")
        if isinstance(pending_receipt, dict) and pending_receipt.get("workflow_attempt_id") is not None:
            from src.services import order_service

            recovered += int(order_service.reconcile_pending_workflow_reparse(job, deepcopy(pending_receipt)))
            continue
        execution_input = (job.get("metrics") or {}).get("nonworkflow_execution")
        if not isinstance(execution_input, dict):
            logger.warning("OCR recovery stopped: processing-start input missing", job_id=job.get("id"))
            continue
        with nonworkflow_execution_context(deepcopy(execution_input)):
            try:
                recovered += int(_recover_captured_ocr_job(job))
            except ValueError as exc:
                if not is_selected_pdf_failure(exc):
                    raise
                recovered += int(complete_own_selected_pdf_failure(job.get("id"), str(exc)) is not None)
    return recovered


def _recover_captured_ocr_job(job: dict) -> bool:
    job_id = str(job.get("id") or "").strip()
    if not job_id:
        return False
    output_reference = str(job.get("output_reference") or "").strip() or None
    if output_reference:
        try:
            payload = _load_completed_ocr_output(output_reference)
        except OwnPipelineLineageError as exc:
            return _mark_ocr_job_final_failed(job_id, error_message=str(exc)) is not None
        if isinstance(payload, dict):
            return reconcile_completed_ocr_job(job_id, execution_input=current_nonworkflow_execution())
    if not is_job_recovery_due(job):
        return False
    if get_job_recovery_attempts(job) >= get_auto_recovery_max_attempts():
        return _mark_ocr_job_final_failed(job_id, error_message="ocr_recovery_exhausted") is not None
    return _resubmit_ocr_job(job)


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


def _run_auto_llm_reparse(order_id: str, *, job_id: str, execution_input: dict, own_payload: dict, provider: str | None = None) -> None:
    from src.services import order_service

    updated = None
    try:
        with nonworkflow_execution_context(execution_input):
            updated, error = run_order_reparse(
                order_id, ocr_provider=provider, llm_assist=True,
                ocr_job_id=job_id, resume_first_pass_payload=own_payload,
            )
            if error is None and order_service.is_own_pending_ocr_result(
                updated, order_id=order_id, job_id=job_id,
                execution_id=execution_input.get("execution_id"),
            ):
                return
            receipt = (updated or {}).get("terminal_receipt")
            if not isinstance(receipt, dict):
                updated, error = order_service._persist_ocr_rerun_completion(
                    order_id, job_id, own_payload,
                    error=error or "llm_reparse_terminal_receipt_missing",
                    metrics_patch={"request_mode": "llm_reparse"},
                )
                receipt = updated["terminal_receipt"]
            if (
                receipt.get("job_id") != job_id or receipt.get("order_id") != order_id
                or receipt.get("execution_id") != execution_input.get("execution_id")
                or receipt.get("evidence_run_id") != (
                    (updated or {}).get("reparse_evidence_run_id") or (updated or {}).get("id")
                )
                or receipt.get("status") not in {"done", "failed"}
            ):
                raise ValueError("llm_reparse_terminal_receipt_mismatch")
        logger.info(
            "Auto LLM reparse finished",
            order_id=order_id,
            provider=provider or "default",
            updated=bool(updated),
            error=error,
        )
    except Exception as exc:  # noqa: BLE001
        if not isinstance((updated or {}).get("terminal_receipt"), dict):
            with nonworkflow_execution_context(execution_input):
                order_service._persist_ocr_rerun_completion(
                    order_id, job_id, own_payload, error=f"llm_reparse_failed:{exc}",
                    metrics_patch={"request_mode": "llm_reparse"},
                )
        logger.exception("Auto LLM reparse failed", order_id=order_id, error=str(exc))


def _enqueue_auto_llm_reparse(
    order: dict | None,
    *,
    ocr_status: str,
    pipeline_output: dict | None,
    execution_input: dict,
    own_evidence: dict | None,
    own_job: dict | None,
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
    if not isinstance(own_evidence, dict) or own_evidence.get("selectable") is not True:
        return
    if not isinstance(order, dict):
        return
    order_id = str(order.get("id") or "").strip()
    facility_id = str(order.get("facility") or "").strip()
    document_uri = str(order.get("document") or "").strip()
    if not order_id or not facility_id or not document_uri:
        return
    existing_job = own_job
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
    _AUTO_REPARSE_EXECUTOR.submit(
        _run_auto_llm_reparse, order_id, provider=provider,
        job_id=execution_input["job_id"], execution_input=deepcopy(execution_input),
        own_payload=deepcopy(own_evidence["payload_json"]),
    )


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
    try:
        payload = build_ingest_payload(claimed)
    except ValueError as exc:
        schedule_uploaded_pdf_retry(
            uploaded_pdf_id, error_code=str(exc), error_message=str(exc), worker_instance=worker_instance,
        )
        return
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
    payload.week_hint = require_explicit_ingest_week(payload.week_hint)
    ocr_job_id = str(payload.ocr_job_id or f"OCR-{payload.message_id}").strip()
    create_job(
        ocr_job_id, input_reference=payload.pdf_uri, order_id=payload.order_id,
        uploaded_pdf_id=payload.uploaded_pdf_id,
        order_document_id=payload.order_document_id,
        input_artifact_digest=payload.content_sha256,
    )
    if not payload.order_id:
        existing = preview_existing_order_for_ingest(payload)
        registered = {"id": existing["order_id"]} if existing is not None else create_order_from_ingest(
            payload, lines=None, ocr_attempts=0,
            document_status="processing", error_message="ocr_pending",
            registration_job_id=ocr_job_id,
        )
        if not isinstance(registered, dict) or not registered.get("id"):
            raise ValueError("ocr_order_input_required")
        payload.order_id = registered["id"]
    execution_input = capture_nonworkflow_execution(ocr_job_id, order_id=payload.order_id, input_reference=payload.pdf_uri)
    with nonworkflow_execution_context(execution_input):
        try:
            if execution_input.get("selected_pdf_error"):
                raise ValueError(execution_input["selected_pdf_error"])
            return _process_ingest_captured(payload, ocr_job_id, execution_input)
        except BaseException as exc:
            if is_selected_pdf_failure(exc):
                receipt = complete_own_selected_pdf_failure(ocr_job_id, str(exc))
                if receipt is not None:
                    return {"id": payload.order_id, "document_status": "error", "non_ocr_terminal_receipt": receipt}
                raise
            try:
                failed = create_order_from_ingest(
                    payload, lines=None, ocr_attempts=0,
                    document_status="error", error_message=str(exc),
                    execution_input=execution_input,
                )
                if isinstance(exc, OwnPipelineLineageError):
                    return failed
            except Exception as terminal_error:
                logger.warning("Own OCR failure receipt refused", job_id=ocr_job_id, error=str(terminal_error))
            raise


def map_own_ingest_result(payload, pdf_bytes, pipeline_result, mapping_input):
    require_explicit_ingest_week(payload.week_hint)
    if not isinstance(mapping_input, dict) or not isinstance(mapping_input.get("policy"), dict):
        raise ValueError("first_pass_mapping_input_missing")
    validate_own_pipeline_result(pipeline_result, pipeline_result.get("output_reference"))
    template = deepcopy(mapping_input["template"])
    if not template.get("template_id") or pipeline_result.get("template_id") != template["template_id"]:
        raise ValueError("first_pass_template_input_mismatch")
    extracted = extract_fax_data(
        pdf_bytes, template, facility_id=payload.facility_hint,
        preferred_template_id=mapping_input["preferred_template_id"], own_pipeline_payload=pipeline_result,
    )
    if extracted.facility_name and not payload.facility_name:
        payload.facility_name = extracted.facility_name
    if extracted.date_strings and not payload.date_hints:
        payload.date_hints = extracted.date_strings
    tokens = filter_tokens_by_box(extracted.tokens, template.get("table_box"))
    grid = detect_table_grid(pdf_bytes, template)
    extracted.tokens = tokens
    extracted.grid = grid
    _dump_ocr_debug(payload, extracted)
    parsed_dates = [parse_date_string(raw, payload.received_at, policy=mapping_input["policy"]) for raw in extracted.date_strings]
    parsed_dates = [date for date in parsed_dates if date]
    lines = parse_order_lines(
        extracted.table_rows, template, payload.received_at, mapping_input["policy"].get("quantity_rules", {}),
        default_date=min(parsed_dates) if parsed_dates else None,
        tokens=tokens, grid=grid.__dict__ if grid else None, pdf_bytes=pdf_bytes,
        date_policy=mapping_input["policy"],
    )
    raw = extracted.recognizer_payload
    result = deepcopy(raw) if isinstance(raw, dict) else {}
    if extracted.own_pipeline_payload is None:
        from src.services.storage_service import verify_selected_pdf_bytes
        from src.services.workflow_attempt_guard_service import processing_start_pdf_receipt
        input_receipt = processing_start_pdf_receipt()
        verify_selected_pdf_bytes(pdf_bytes, input_receipt)
        result = {
            **(deepcopy(raw) if isinstance(raw, dict) else {}),
            "engine": extracted.ocr_provider, "raw_recognizer_payload": deepcopy(raw),
            "table_raw": extracted.raw_text, "table_rows": deepcopy(extracted.table_rows),
            "date_strings": deepcopy(extracted.date_strings), "provider_debug": deepcopy(extracted.provider_debug),
            "auxiliary_pipeline_result": deepcopy(pipeline_result),
            "page_correction_artifacts": {"recognition_input": {
                "representation": "original_identity", "receipt": input_receipt,
            }},
        }
    result["raw_recognizer_payload"] = deepcopy(raw)
    if not isinstance(raw, dict):
        result.update(status="error", error="first_pass_own_recognizer_result_missing")
        lines = None
    execution = current_nonworkflow_execution()
    if execution is None or not execution.get("input_reference"):
        raise ValueError("ocr_execution_input_required")
    if extracted.own_pipeline_payload is None:
        result["input_reference"] = execution["input_reference"]
    result["_first_pass_recognizer"] = {
        "selected_pdf_receipt": deepcopy((current_nonworkflow_execution() or {}).get("selected_pdf_receipt")),
        "job_id": execution["job_id"], "execution_id": execution["execution_id"],
        "workflow_attempt_id": None, "workflow_revision": None,
        "template_id": template["template_id"], "week_hint": payload.week_hint,
        "input_sha256": sha256(pdf_bytes).hexdigest(), "provider": extracted.ocr_provider,
        "selected_input_reference": execution["input_reference"],
        "recognized_rows": deepcopy(extracted.table_rows),
        "mapped_lines": [
            {key: value.isoformat() if isinstance(value, (datetime, date)) else deepcopy(value) for key, value in line.items()}
            for line in lines
        ] if lines is not None else None,
    }
    return {"lines": lines, "recognizer_result": result}


def _process_ingest_captured(payload, ocr_job_id: str, execution_input: dict):
    week = require_explicit_ingest_week(payload.week_hint)
    captured_order = execution_input.get("orders", {}).get(payload.order_id, {}).get("order")
    if not isinstance(captured_order, dict) or captured_order.get("week_code") != week:
        raise ValueError("first_pass_week_input_mismatch")
    configured_input = execution_input.get("orders", {}).get(payload.order_id, {}).get("configured_input")
    if not isinstance(configured_input, dict):
        raise ValueError("configured_execution_input_missing")
    policy = deepcopy(configured_input["policy"])
    lines = None
    ocr_attempts = 0
    ocr_status = "success"
    ocr_error = None
    pipeline_output = None
    pipeline_pending = False
    facility_candidates: list[dict[str, object]] = []
    retry_limit = int(policy.get("ocr_retry_limit", 3) or 3)
    update_job(
        ocr_job_id,
        status="running",
        input_reference=payload.pdf_uri,
        order_id=execution_input.get("job_order_id"),
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
    mapping_input = None
    if retry_limit:
        if not payload.facility_hint:
            raise ValueError("facility_unresolved")
        fac_config = deepcopy(configured_input["facility"])
        preferred_template_id = fac_config.get("fax_template_id")
        preferred_template_ids = list(fac_config.get("fax_template_ids") or [])
        if preferred_template_id and preferred_template_id not in preferred_template_ids:
            preferred_template_ids.insert(0, preferred_template_id)
        mapping_input = {"template": deepcopy(fac_config["fax_template"]), "policy": deepcopy(policy),
                         "preferred_template_id": preferred_template_id}
    try:
        for attempt in range(1, retry_limit + 1):
            ocr_attempts = attempt
            try:
                pdf_bytes = load_selected_pdf_bytes(payload.pdf_uri)
                if attempt == 1:
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
                        if is_ocr_pipeline_output_pending(output):
                            pipeline_pending = True
                            _mark_ocr_job_awaiting_output(
                                ocr_job_id,
                                error_message="ocr_output_pending",
                                input_reference=output.get("input_reference"),
                                output_reference=str(output.get("output_reference") or "").strip() or None,
                                request_mode="ingest_first_pass",
                                facility_id=payload.facility_hint,
                                order_id=payload.order_id,
                                preferred_template_id=preferred_template_id,
                                preferred_template_ids=preferred_template_ids,
                            )
                        else:
                            pipeline_output = deepcopy(output) if isinstance(output, dict) else None
                            output_ref = output.get("output_reference")
                            validate_own_pipeline_result(output, output_ref)
                            update_job(
                                ocr_job_id,
                                status="running",
                                template_id=output.get("template_id"),
                                output_reference=output_ref,
                                error_message=None,
                                metrics=_first_pass_job_metrics(
                                    ocr_job_id,
                                    processing_stage="ocr_pipeline",
                                    result_state="persist_pending",
                                    failed_cells=len(output.get("failed_cells") or []),
                                ),
                            )
                    except OCRPipelineOutputPendingError as exc:
                        pipeline_pending = True
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
                            order_id=payload.order_id,
                            preferred_template_id=preferred_template_id,
                            preferred_template_ids=preferred_template_ids,
                        )
                    except OwnPipelineLineageError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("ROI OCR pipeline failed", job_id=ocr_job_id, error=str(exc))
                        update_job(
                            ocr_job_id,
                            status="running",
                            error_message=str(exc),
                            metrics=_first_pass_job_metrics(
                                ocr_job_id,
                                processing_stage="ocr_pipeline",
                                result_state="persist_failure_pending",
                                error=str(exc),
                            ),
                        )
                if pipeline_pending:
                    break
                if not isinstance(pipeline_output, dict):
                    raise ValueError("first_pass_pipeline_result_missing")
                mapped = map_own_ingest_result(payload, pdf_bytes, pipeline_output, mapping_input)
                lines = mapped["lines"]
                pipeline_output = mapped["recognizer_result"]
                ocr_status = "success" if lines is not None else "error"
                ocr_error = None if lines is not None else pipeline_output.get("error")
                break
            except OCRPipelineOutputPendingError as exc:
                pipeline_pending = True
                pipeline_output = None
                _mark_ocr_job_awaiting_output(
                    ocr_job_id, error_message="ocr_output_pending",
                    input_reference=exc.input_reference, output_reference=exc.output_reference,
                    request_mode="ingest_first_pass", facility_id=payload.facility_hint,
                    order_id=payload.order_id, preferred_template_id=preferred_template_id,
                    preferred_template_ids=preferred_template_ids,
                )
                break
            except OwnPipelineLineageError:
                raise
            except Exception as exc:  # noqa: BLE001
                ocr_status = "error"
                ocr_error = str(exc)
                logger.exception("OCR extraction failed", attempt=attempt)
                if attempt >= retry_limit:
                    logger.warning("OCR retries exhausted", attempts=attempt)
    except OwnPipelineLineageError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("OCR pipeline failed; continuing without lines")
    if pipeline_pending:
        execution_input["first_pass_application"] = {
            "payload": to_job_kwargs(payload), "mapping_input": deepcopy(mapping_input),
            "ocr_attempts": ocr_attempts or 1,
        }
    if not isinstance(pipeline_output, dict):
        if not pipeline_pending and ocr_status != "skipped":
            raise ValueError("first_pass_pipeline_result_missing")
        lines = None
    elif not order_service._classify_owned_evidence_payload(pipeline_output).get("persistable") or ocr_status != "success":
        lines = None
    order = create_order_from_ingest(
        payload,
        lines=lines,
        ocr_attempts=0 if ocr_status == "skipped" else ocr_attempts or 1,
        document_status="processing" if pipeline_pending else ocr_status,
        error_message="ocr_output_pending" if pipeline_pending else ocr_error,
        execution_input=execution_input,
        pipeline_result=pipeline_output,
        mapping_policy=mapping_input["policy"] if mapping_input is not None else None,
    )
    own_evidence = None
    saved_job = None
    if isinstance(order, dict) and isinstance(pipeline_output, dict):
        own_evidence = order.pop("first_pass_evidence", None)
        if not isinstance(own_evidence, dict) or not own_evidence.get("id"):
            raise ValueError("first_pass_own_evidence_persist_failed")
        if own_evidence.get("selectable") is True and order.get("first_pass_result_applied") is not True:
            raise ValueError("first_pass_result_not_applied")
        receipt = order.get("first_pass_terminal_receipt")
        if (
            not isinstance(receipt, dict) or receipt.get("job_id") != ocr_job_id
            or receipt.get("execution_id") != execution_input["execution_id"]
            or receipt.get("evidence_run_id") != own_evidence["id"]
        ):
            raise ValueError("first_pass_terminal_receipt_missing")
        saved_job = {"id": receipt["job_id"], "status": receipt["status"]}
        finalize_first_pass_side_effects(
            job_id=ocr_job_id, order_id=order["id"], message_id=payload.message_id,
        )
    elif pipeline_pending:
        # The registration is not an OCR result. Recovery replays the start input
        # persisted on this exact real job; it may not recapture current tokens.
        return
    elif ocr_status == "skipped":
        receipt = order.get("non_ocr_terminal_receipt") if isinstance(order, dict) else None
        if not isinstance(receipt, dict) or receipt.get("job_id") != ocr_job_id or receipt.get("result_state") != "skipped":
            raise ValueError("non_ocr_terminal_receipt_missing")
        finalize_first_pass_side_effects(
            job_id=ocr_job_id, order_id=order["id"], message_id=payload.message_id,
        )
        return
    elif ocr_status != "skipped":
        raise ValueError("first_pass_pipeline_result_missing")
    if isinstance(order, dict):
        try:
            from src.services import order_workflow_v2_service  # noqa: PLC0415

            order_workflow_v2_service.record_context_suggestion(
                order_id=str(order.get("id") or "").strip(),
                expected_revision=(execution_input["orders"][order["id"]]["workflow_revision"]
                                   if execution_input["orders"][order["id"]]["workflow_exists"] else 0),
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
    _enqueue_auto_llm_reparse(
        order,
        ocr_status=ocr_status,
        pipeline_output=pipeline_output if isinstance(pipeline_output, dict) else None,
        execution_input=execution_input, own_evidence=own_evidence, own_job=saved_job,
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
