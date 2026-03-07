import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.workers import celery_app
from src.workers.ingest_mail_adapter import parse_ingest_payload
from src.services.order_service import create_order_from_ingest
from src.services import config_service
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
from src.services.ocr_job_service import create_job, update_job
from src.services.ocr_pipeline_service import run_ocr_pipeline
from src.services import ingest_job_service
from src.services.gmail_ingest_service import mark_message_read
from loguru import logger

_INGEST_MAX_WORKERS = int(os.getenv("INGEST_MAX_WORKERS", "4") or 4)
_INGEST_EXECUTOR = ThreadPoolExecutor(max_workers=_INGEST_MAX_WORKERS)


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


def enqueue_ingest(payload: dict, force: bool = False):
    """
    For now process inline to avoid external broker dependency during bring-up.
    """
    job_id, should_enqueue = ingest_job_service.create_ingest_job(payload, force=force)
    if not should_enqueue:
        logger.info("Ingest job already completed", job_id=job_id)
        return
    process_ingest_job(job_id)


def enqueue_ingest_async(payload: dict, force: bool = False) -> None:
    """
    Run ingest on a background thread so API handlers stay responsive.
    """
    if os.getenv("INGEST_RUN_INLINE", "").lower() == "true" or os.getenv("PYTEST_CURRENT_TEST"):
        enqueue_ingest(payload, force=force)
        return
    job_id, should_enqueue = ingest_job_service.create_ingest_job(payload, force=force)
    if not should_enqueue:
        logger.info("Ingest job already completed", job_id=job_id)
        return
    enqueue_ingest_job_async(job_id)


def enqueue_ingest_job_async(job_id: str) -> None:
    _INGEST_EXECUTOR.submit(process_ingest_job, job_id=job_id)


def _maybe_mark_gmail_read(payload: dict) -> None:
    if not payload.get("gmail_mark_read"):
        return
    message_id = payload.get("gmail_message_id")
    if not message_id:
        return
    try:
        mark_message_read(message_id)
        logger.info("Gmail message marked read", message_id=message_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to mark Gmail read", message_id=message_id, error=str(exc))


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
    _maybe_mark_gmail_read(payload)


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
    retry_limit = int(policy.get("ocr_retry_limit", 3) or 3)
    ocr_job_id = f"OCR-{payload.message_id}"
    create_job(ocr_job_id, input_reference=payload.pdf_uri)
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
                        )
                        pipeline_output = output
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
                            metrics={"failed_cells": len(output.get("failed_cells") or [])},
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("ROI OCR pipeline failed", job_id=ocr_job_id, error=str(exc))
                        update_job(ocr_job_id, status="failed", error_message=str(exc))
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
    create_order_from_ingest(
        payload,
        lines=lines,
        ocr_attempts=ocr_attempts or 1,
        document_status=ocr_status,
        error_message=ocr_error,
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
