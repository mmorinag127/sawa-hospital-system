from typing import Any, Optional
from pathlib import Path
import base64
import json
import os
import hashlib
import math
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from urllib.parse import urlparse
from difflib import SequenceMatcher
from src.workers.ingest_mail_adapter import IngestEmailPayload
from loguru import logger
from uuid import uuid4
from datetime import date, datetime, timedelta, timezone
import pandas as pd
from sqlalchemy import select, delete, inspect, text, func

from src.db import Base, engine, session_scope
from src.models.order import Order, OrderLine, OrderMenuSnapshot
from src.models.document import OrderDocument
from src.models.order_ocr_cache import OrderOcrCache
from src.models.order_ocr_revision import OrderOcrRevision  # noqa: F401
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun  # noqa: F401
from src.models.order_sheet_draft import OrderSheetDraft  # noqa: F401
from src.models.order_sheet_patch_candidate import OrderSheetPatchCandidate  # noqa: F401
from src.models.order_workflow_state import OrderWorkflowState  # noqa: F401
from src.models.order_critical_decision import OrderCriticalDecision  # noqa: F401
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot  # noqa: F401
from src.models.output import Bag, LabelRow, DeliveryNote, ManufacturingAggregateRow
from src.models.ingest_job import IngestJob  # noqa: F401
from src.models.user import AuditLog
from src.models.facility import FacilityConfig
from src.services.notification_service import record_event
from src.services import config_service, menu_service, facility_service
from src.services.config_validator import validate_facility_config
from src.services import ocr_llm_review_service, ocr_sheet_revision_service
from src.services import ocr_revision_store
from src.services import (
    apply_gate_service,
    evidence_manifest_service,
    template_resolution_service,
    ocr_evidence_service,
    draft_sheet_service,
    ocr_patch_candidate_service as patch_candidate_service,
)
from src.services import workflow_state_service, candidate_resolution_service, critical_decision_service
from src.services.fax_extractor import (
    extract_fax_data,
    filter_tokens_by_box,
    rows_from_markdown,
    rows_from_pipeline_payload,
    rows_from_structured_payload,
    structured_cell_issues_from_payload,
)
from src.services.fax_parser import parse_order_lines
from src.services.ingest_policy import parse_date_string, month_id_from_dates
from src.services.storage_service import load_bytes_from_uri, get_default_output_bucket
from src.services.storage_service import generate_signed_url
from src.services.grid_detector import GridDetectionResult, detect_table_grid, detect_table_grid_image
from src.services.pdf_render import render_pdf_to_png_bytes
from src.services.ocr_job_service import create_job, update_job, get_job as get_ocr_job, describe_job_state as describe_ocr_job_state
from src.services.ocr_pipeline_service import run_ocr_pipeline

Base.metadata.create_all(bind=engine)


def _ensure_orders_lines_updated_at() -> None:
    inspector = inspect(engine)
    if "orders" not in inspector.get_table_names():
        return
    columns = {col.get("name") for col in inspector.get_columns("orders")}
    if "lines_updated_at" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE orders ADD COLUMN lines_updated_at TIMESTAMP"))


_ensure_orders_lines_updated_at()


def _parse_sheet_week_value(value: object) -> tuple[str | None, date | None, date | None]:
    if not value:
        return None, None, None
    text = str(value).strip()
    if not text:
        return None, None, None
    if re.match(r"^\d{4}-\d{2}$", text):
        return text, None, None
    match = re.match(r"^(\d{4}-\d{2})@(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})$", text)
    if not match:
        return None, None, None
    month_id = match.group(1)
    try:
        start_date = date.fromisoformat(match.group(2))
        end_date = date.fromisoformat(match.group(3))
    except Exception:
        return None, None, None
    if end_date < start_date:
        return None, None, None
    if start_date.strftime("%Y-%m") != month_id or end_date.strftime("%Y-%m") != month_id:
        return None, None, None
    return month_id, start_date, end_date


def _format_sheet_week_value(
    month_id: str | None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> str | None:
    month = _to_sheet_month_id(month_id)
    if not month:
        return None
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        return month
    if end_date < start_date:
        return month
    if start_date.strftime("%Y-%m") != month or end_date.strftime("%Y-%m") != month:
        return month
    return f"{month}@{start_date.isoformat()}~{end_date.isoformat()}"


def _normalize_sheet_week_value(value: object) -> str | None:
    month_id, start_date, end_date = _parse_sheet_week_value(value)
    if not month_id:
        return None
    return _format_sheet_week_value(month_id, start_date, end_date)


def _normalize_sheet_week_candidate(value: object) -> str | None:
    normalized_week = _normalize_sheet_week_value(value)
    if normalized_week and "@" in normalized_week:
        return normalized_week
    return _to_sheet_month_id(value)


def _format_sheet_week_label(value: object) -> str:
    month_id, start_date, end_date = _parse_sheet_week_value(value)
    if not month_id:
        return ""
    if isinstance(start_date, date) and isinstance(end_date, date):
        return f"{month_id} ({start_date.strftime('%m/%d')}-{end_date.strftime('%m/%d')})"
    return month_id


def _calendar_week_ranges_for_month(month_id: str) -> list[tuple[date, date]]:
    normalized_month = _to_sheet_month_id(month_id)
    if not normalized_month:
        return []
    try:
        year = int(normalized_month[:4])
        month = int(normalized_month[5:7])
        month_start = date(year, month, 1)
    except Exception:
        return []
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)

    # Week picker follows the existing Sunday-Saturday convention used in the menus.
    first_week_start = month_start - timedelta(days=(month_start.weekday() + 1) % 7)
    ranges: list[tuple[date, date]] = []
    cursor = first_week_start
    while cursor <= month_end:
        week_start = max(cursor, month_start)
        week_end = min(cursor + timedelta(days=6), month_end)
        if week_end >= week_start:
            ranges.append((week_start, week_end))
        cursor += timedelta(days=7)
    return ranges


def _clip_entries_to_sheet_week_range(entries: list[dict[str, Any]], week_value: object) -> list[dict[str, Any]]:
    month_id, start_date, end_date = _parse_sheet_week_value(week_value)
    if not month_id or not isinstance(start_date, date) or not isinstance(end_date, date):
        return entries
    clipped: list[dict[str, Any]] = []
    for entry in entries:
        menu_date = _normalize_entry_date(entry.get("menu_date"))
        if not isinstance(menu_date, date):
            continue
        if start_date <= menu_date <= end_date:
            clipped.append(entry)
    return clipped


def _run_roi_ocr_pipeline(
    *,
    job_id: str,
    pdf_bytes: bytes,
    facility_id: str | None,
    input_reference: str | None,
    preferred_template_id: str | None,
    preferred_template_ids: list[str] | None = None,
) -> str | None:
    try:
        logger.info(
            "ROI OCR pipeline start job_id={} facility_id={} template_id={} template_ids={} input_reference={}",
            job_id,
            facility_id,
            preferred_template_id,
            preferred_template_ids,
            input_reference,
        )
        output = run_ocr_pipeline(
            pdf_bytes=pdf_bytes,
            job_id=job_id,
            facility_id=facility_id,
            input_reference=input_reference,
            preferred_template_id=preferred_template_id,
            preferred_template_ids=preferred_template_ids,
            force_upload=True,
            wait_for_output=False,
        )
        output_ref = output.get("output_reference")
        logger.info(
            "ROI OCR pipeline queued job_id={} output_ref={}",
            job_id,
            output_ref,
        )
        update_job(
            job_id,
            status="running",
            template_id=output.get("template_id"),
            output_reference=output_ref,
            error_message=None,
            metrics=None,
        )
        event_type = "ocr_job_started"
        record_event(
            event_type,
            actor="system",
            target=job_id,
            fac=facility_id,
            metadata={"template_id": output.get("template_id"), "output_ref": output_ref},
        )
        return output_ref
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"ROI OCR pipeline failed (job_id={job_id}): {exc}")
        update_job(job_id, status="failed", error_message=str(exc))
        record_event(
            "ocr_job_failed",
            actor="system",
            target=job_id,
            fac=facility_id,
            metadata={"error": str(exc)},
        )
        return None


def _resolve_preferred_template_ids(facility_config: dict[str, Any] | None) -> tuple[str | None, list[str]]:
    if not isinstance(facility_config, dict):
        return None, []
    template_id = facility_config.get("fax_template_id")
    if isinstance(template_id, str):
        template_id = template_id.strip() or None
    template_ids_raw = facility_config.get("fax_template_ids")
    template_ids: list[str] = []
    if isinstance(template_ids_raw, list):
        for item in template_ids_raw:
            token = str(item or "").strip()
            if token and token not in template_ids:
                template_ids.append(token)
    if template_id and template_id not in template_ids:
        template_ids.insert(0, template_id)
    if not template_id and template_ids:
        template_id = template_ids[0]
    return template_id, template_ids


def clear_all():
    with session_scope() as session:
        session.execute(delete(LabelRow))
        session.execute(delete(Bag))
        session.execute(delete(DeliveryNote))
        session.execute(delete(ManufacturingAggregateRow))
        session.execute(delete(FacilityConfig))
        session.execute(delete(OrderCriticalDecision))
        session.execute(delete(OrderWorkflowState))
        session.execute(delete(OrderConfirmedSnapshot))
        session.execute(delete(OrderSheetDraft))
        session.execute(delete(OrderSheetPatchCandidate))
        session.execute(delete(OrderOcrEvidenceRun))
        session.execute(delete(OrderOcrRevision))
        session.execute(delete(OrderOcrCache))
        session.execute(delete(OrderDocument))
        session.execute(delete(OrderLine))
        session.execute(delete(OrderMenuSnapshot))
        session.execute(delete(Order))
        session.execute(delete(IngestJob))
    _invalidate_orders_cache()


def delete_orders_by_message_prefix(prefix: str) -> int:
    if not prefix:
        return 0
    removed = 0
    with session_scope() as session:
        orders = (
            session.execute(select(Order).where(Order.message_id.like(f"{prefix}%")))
            .scalars()
            .all()
        )
        for order in orders:
            session.execute(delete(LabelRow).where(LabelRow.order_id == order.id))
            session.execute(delete(Bag).where(Bag.order_id == order.id))
            session.execute(delete(DeliveryNote).where(DeliveryNote.order_id == order.id))
            session.execute(delete(OrderCriticalDecision).where(OrderCriticalDecision.order_id == order.id))
            session.execute(delete(OrderWorkflowState).where(OrderWorkflowState.order_id == order.id))
            session.execute(delete(OrderConfirmedSnapshot).where(OrderConfirmedSnapshot.order_id == order.id))
            session.execute(delete(OrderSheetDraft).where(OrderSheetDraft.order_id == order.id))
            session.execute(delete(OrderSheetPatchCandidate).where(OrderSheetPatchCandidate.order_id == order.id))
            session.execute(delete(OrderOcrEvidenceRun).where(OrderOcrEvidenceRun.order_id == order.id))
            session.execute(delete(OrderOcrRevision).where(OrderOcrRevision.order_id == order.id))
            session.execute(delete(OrderOcrCache).where(OrderOcrCache.order_id == order.id))
            session.execute(delete(OrderDocument).where(OrderDocument.order_id == order.id))
            session.execute(delete(OrderMenuSnapshot).where(OrderMenuSnapshot.order_id == order.id))
            session.execute(delete(OrderLine).where(OrderLine.order_id == order.id))
            session.execute(delete(Order).where(Order.id == order.id))
            removed += 1
        if removed:
            session.execute(delete(IngestJob).where(IngestJob.id.like(f"{prefix}%")))
    if removed:
        _invalidate_orders_cache()
    return removed


def find_order_by_message_id(message_id: str) -> dict | None:
    token = str(message_id or "").strip()
    if not token:
        return None
    with session_scope() as session:
        order = (
            session.execute(
                select(Order)
                .where(Order.message_id == token)
                .order_by(Order.received_at.desc(), Order.id.desc())
            )
            .scalars()
            .first()
        )
        if not order:
            return None
        return {
            "id": order.id,
            "message_id": order.message_id,
            "facility_id": order.facility_code,
            "week_id": order.week_code,
            "status": order.status,
            "received_at": order.received_at.isoformat() if order.received_at else None,
        }


def _make_order_id() -> str:
    return f"ORD{uuid4().hex[:8]}"


def _make_document_id() -> str:
    return f"DOC{uuid4().hex[:8]}"


def _make_line_id() -> str:
    return f"OLN{uuid4().hex[:10]}"


def _existing_line_ids(candidate_ids: set[str], *, exclude_order_id: str | None = None) -> set[str]:
    normalized = {str(item or "").strip() for item in candidate_ids if str(item or "").strip()}
    if not normalized:
        return set()
    with session_scope() as session:
        query = select(OrderLine.id).where(OrderLine.id.in_(normalized))
        if exclude_order_id:
            query = query.where(OrderLine.order_id != exclude_order_id)
        rows = session.execute(query).scalars().all()
    return {str(item or "").strip() for item in rows if str(item or "").strip()}


def _ensure_unique_line_ids(
    lines: list[dict] | None,
    *,
    exclude_order_id: str | None = None,
) -> list[dict]:
    if not isinstance(lines, list):
        return []
    initial_candidates = {
        str(raw.get("id") or "").strip()
        for raw in lines
        if isinstance(raw, dict) and str(raw.get("id") or "").strip()
    }
    existing_ids = _existing_line_ids(initial_candidates, exclude_order_id=exclude_order_id)
    normalized: list[dict] = []
    used_ids: set[str] = set()
    for raw in lines:
        if not isinstance(raw, dict):
            continue
        line = dict(raw)
        candidate = str(line.get("id") or "").strip()
        if not candidate or candidate in used_ids or candidate in existing_ids:
            candidate = _make_line_id()
            while candidate in used_ids or candidate in existing_ids:
                candidate = _make_line_id()
            line["id"] = candidate
        else:
            line["id"] = candidate
        used_ids.add(candidate)
        normalized.append(line)
    return normalized


def _line_digest(rows: list[dict]) -> str:
    normalized = []
    for row in rows:
        normalized.append(
            {
                "date": str(row.get("date") or ""),
                "daypart": row.get("daypart") or "",
                "menu_name": row.get("menu_name") or "",
                "diet_type": row.get("diet_type") or "",
                "area_id": row.get("area_id") or "",
                "bag_type": row.get("bag_type") or "",
                "quantity_original": row.get("quantity_original"),
                "quantity_corrected": row.get("quantity_corrected"),
                "change_note": row.get("change_note") or "",
            }
        )
    normalized.sort(
        key=lambda item: (
            item["date"],
            item["menu_name"],
            item["diet_type"],
            item["area_id"],
            item["bag_type"],
            str(item["quantity_original"]),
            str(item["quantity_corrected"]),
            item["change_note"],
        )
    )
    payload = json.dumps(normalized, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_MENU_TRANSLATION = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)


def _normalize_menu_text(text: str) -> str:
    normalized = text.translate(_MENU_TRANSLATION)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.replace("・", "").replace("／", "/")
    return normalized.strip().lower()


def _match_menu_name(
    raw: str, candidates: list[str], normalized_candidates: dict[str, str], min_ratio: float
) -> tuple[str, float]:
    if not raw or not candidates:
        return raw, 0.0
    raw_norm = _normalize_menu_text(raw)
    if not raw_norm:
        return raw, 0.0
    best_name = raw
    best_ratio = 0.0
    for candidate in candidates:
        candidate_norm = normalized_candidates.get(candidate, "")
        if not candidate_norm:
            continue
        if raw_norm == candidate_norm:
            return candidate, 1.0
        ratio = SequenceMatcher(None, raw_norm, candidate_norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = candidate
    if best_ratio >= min_ratio:
        return best_name, best_ratio
    return raw, best_ratio


def _apply_menu_matching(
    lines: list[dict],
    week_id: Optional[str],
    facility_id: Optional[str],
    min_ratio: float,
) -> list[dict]:
    month_id = _to_sheet_month_id(week_id)
    if not month_id:
        return lines
    items = menu_service.get_menu_items_for_facility(month_id, facility_id)
    if not items:
        return lines
    candidates = [item.get("name") for item in items if item.get("name")]
    if not candidates:
        return lines
    normalized_candidates = {name: _normalize_menu_text(name) for name in candidates}
    for line in lines:
        raw = line.get("menu_name")
        if not raw:
            continue
        matched, ratio = _match_menu_name(raw, candidates, normalized_candidates, min_ratio)
        if matched != raw:
            line["menu_name"] = matched
            line["menu_match_ratio"] = ratio
    return lines


def _normalize_daypart_key(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if "朝" in text:
        return "朝"
    if "昼" in text:
        return "昼"
    if "夕" in text or "夜" in text:
        return "夕"
    return text


_DAYPART_SORT_ORDER = {"朝": 0, "昼": 1, "夕": 2}


def _daypart_sort_components(value: object) -> tuple[int, str]:
    normalized = _normalize_daypart_key(value)
    if not normalized:
        return 99, ""
    return _DAYPART_SORT_ORDER.get(normalized, 50), normalized


def _is_supported_daypart(value: object) -> bool:
    return _normalize_daypart_key(value) in {"朝", "昼", "夕"}


def _normalize_entry_date(value: object) -> date | None:
    if isinstance(value, date):
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        return value
    if not value:
        return None
    try:
        parsed = date.fromisoformat(str(value))
        try:
            if pd.isna(parsed):
                return None
        except Exception:
            pass
        return parsed
    except Exception:
        try:
            parsed = pd.to_datetime(value)
            if pd.isna(parsed):
                return None
            parsed_date = parsed.date()
            if pd.isna(parsed_date):
                return None
            return parsed_date
        except Exception:
            return None


def _build_position_menu_entries(week_id: str, facility_id: str | None = None) -> list[dict]:
    month_id = _to_sheet_month_id(week_id)
    if not month_id:
        return []
    menu = (
        menu_service.get_menu_for_facility(month_id, facility_id)
        if facility_id
        else menu_service.get_menu(month_id)
    )
    if not isinstance(menu, dict):
        return []
    raw_entries = menu.get("entries")
    if not isinstance(raw_entries, list):
        return []
    entries: list[dict] = []
    for idx, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            continue
        menu_name = str(raw.get("name") or "").strip()
        if not menu_name:
            continue
        menu_date = _normalize_entry_date(raw.get("menu_date"))
        daypart_key = _normalize_daypart_key(raw.get("daypart"))
        slot_index_raw = raw.get("slot_index")
        try:
            slot_index = int(slot_index_raw) if slot_index_raw is not None else idx
        except Exception:
            slot_index = idx
        entries.append(
            {
                "menu_name": menu_name,
                "menu_date": menu_date,
                "daypart_key": daypart_key,
                "slot_index": slot_index,
                "order": idx,
            }
        )
    entries.sort(
        key=lambda item: (
            item.get("menu_date") or date.min,
            _daypart_sort_components(item.get("daypart_key"))[0],
            _daypart_sort_components(item.get("daypart_key"))[1],
            int(item.get("slot_index") or 0),
            int(item.get("order") or 0),
        )
    )
    return _clip_entries_to_sheet_week_range(entries, week_id)


def _build_position_menu_entries_safe(week_id: str, facility_id: str | None = None) -> list[dict]:
    try:
        return _build_position_menu_entries(week_id, facility_id)
    except TypeError as exc:
        if "positional argument" not in str(exc) and "were given" not in str(exc):
            raise
        return _build_position_menu_entries(week_id)


def _build_position_menu_entries_from_orders(week_id: str, facility_id: str | None) -> list[dict]:
    def _serialize_rows(rows: list[tuple[Any, Any, Any]]) -> list[dict]:
        entries: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for idx, (menu_date_raw, daypart_raw, menu_name_raw) in enumerate(rows):
            menu_name = str(menu_name_raw or "").strip()
            if not menu_name:
                continue
            menu_date = _normalize_entry_date(menu_date_raw)
            daypart_key = _normalize_daypart_key(daypart_raw)
            key = (
                menu_date.isoformat() if isinstance(menu_date, date) else "",
                daypart_key,
                _normalize_menu_text(menu_name),
            )
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "menu_name": menu_name,
                    "menu_date": menu_date,
                    "daypart_key": daypart_key,
                    "slot_index": idx,
                    "order": idx,
                }
            )
        entries.sort(
            key=lambda item: (
                item.get("menu_date") or date.min,
                _daypart_sort_components(item.get("daypart_key"))[0],
                _daypart_sort_components(item.get("daypart_key"))[1],
                int(item.get("slot_index") or 0),
                int(item.get("order") or 0),
            )
        )
        return entries

    month_id = _to_sheet_month_id(week_id)
    if not month_id:
        return []

    with session_scope() as session:
        q = (
            select(OrderLine.date, OrderLine.daypart, OrderLine.menu_name)
            .join(Order, Order.id == OrderLine.order_id)
            .where(Order.week_code.like(f"{month_id}%"), OrderLine.menu_name.is_not(None))
            .order_by(OrderLine.date, OrderLine.daypart, OrderLine.menu_name)
        )
        rows_all = session.execute(q).all()
        if facility_id:
            q_fac = q.where(Order.facility_code == facility_id)
            rows_fac = session.execute(q_fac).all()
            entries_fac = _serialize_rows(rows_fac)
            if entries_fac:
                return _clip_entries_to_sheet_week_range(entries_fac, week_id)
        return _clip_entries_to_sheet_week_range(_serialize_rows(rows_all), week_id)


def _resolve_sheet_payload_for_menu_entries(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    edited = payload.get("_edited_ocr")
    if isinstance(edited, dict):
        latest = edited.get("latest")
        llm_review = latest.get("llm_review") if isinstance(latest, dict) else None
        output_payload = llm_review.get("output_payload") if isinstance(llm_review, dict) else None
        if isinstance(output_payload, dict):
            return output_payload
        raw_output = edited.get("raw_output")
        if isinstance(raw_output, dict):
            return raw_output
    return payload


def _extract_entry_date_from_sheet_cell(value: object, received_at: datetime) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = parse_date_string(text, received_at)
    if parsed:
        return parsed
    return _normalize_entry_date(text)


def _build_position_menu_entries_from_ocr_payload(
    *,
    payload: dict[str, Any] | None,
    template: dict[str, Any],
    received_at: datetime,
) -> list[dict]:
    source_payload = _resolve_sheet_payload_for_menu_entries(payload)
    if not isinstance(source_payload, dict):
        return []
    sheet_rows = _extract_sheet_rows_from_payload(source_payload, template)
    if not sheet_rows:
        return []
    fields = _row_fields_from_template(template)
    if not fields:
        return []

    date_idx = next(
        (idx for idx, field in enumerate(fields) if _normalize_sheet_text(field).lower().startswith("date")),
        None,
    )
    daypart_idx = next(
        (idx for idx, field in enumerate(fields) if _normalize_sheet_text(field).lower() in {"daypart", "meal", "time"}),
        None,
    )
    menu_idx = next(
        (
            idx
            for idx, field in enumerate(fields)
            if _normalize_sheet_text(field).lower() in {"menu", "menuname"}
        ),
        None,
    )
    if menu_idx is None:
        return []

    large_cell_mode = bool(template.get("large_cell_mode", False))
    fill_forward_roles = set(template.get("fill_forward_roles") or [])
    if large_cell_mode:
        fill_forward_roles.update({"date", "daypart", "menu_name"})
    fill_missing_date_with_hint = bool(template.get("fill_missing_date_with_hint", False))
    if "fill_missing_date_with_first_seen" in template:
        fill_missing_date_with_first_seen = bool(template.get("fill_missing_date_with_first_seen"))
    else:
        fill_missing_date_with_first_seen = large_cell_mode

    date_candidates = _collect_sheet_dates_from_payload(source_payload, received_at)
    default_date = min(date_candidates) if date_candidates else None
    first_date_in_table: date | None = None
    carry_forward: dict[str, Any] = {role: None for role in fill_forward_roles}
    entries: list[dict] = []

    for row_idx, row in enumerate(sheet_rows):
        row_values = list(row) if isinstance(row, list) else []
        raw_date = row_values[date_idx] if date_idx is not None and date_idx < len(row_values) else ""
        raw_daypart = (
            row_values[daypart_idx] if daypart_idx is not None and daypart_idx < len(row_values) else ""
        )
        raw_menu_name = row_values[menu_idx] if menu_idx < len(row_values) else ""
        menu_name = str(raw_menu_name or "").strip()
        menu_date = _extract_entry_date_from_sheet_cell(raw_date, received_at)
        daypart = str(raw_daypart or "").strip()

        base = {
            "date": menu_date,
            "daypart": daypart or None,
            "menu_name": menu_name or None,
        }
        for role in fill_forward_roles:
            if role not in base:
                continue
            value = base.get(role)
            if value:
                carry_forward[role] = value
            else:
                base[role] = carry_forward.get(role)

        if isinstance(base.get("date"), date) and first_date_in_table is None:
            first_date_in_table = base.get("date")
        if base.get("date") is None and fill_missing_date_with_hint and default_date is not None:
            base["date"] = default_date
        if base.get("date") is None and first_date_in_table is not None and fill_missing_date_with_first_seen:
            base["date"] = first_date_in_table

        if not base.get("menu_name"):
            continue
        entries.append(
            {
                "menu_name": str(base.get("menu_name") or "").strip(),
                "menu_date": base.get("date") if isinstance(base.get("date"), date) else None,
                "daypart_key": _normalize_daypart_key(base.get("daypart")),
                "slot_index": row_idx,
                "order": row_idx,
                "source_order": row_idx,
            }
        )
    return entries


def _build_sheet_menu_entries(
    *,
    week_id: str,
    facility_id: str | None,
    ocr_payload: dict[str, Any] | None,
    template: dict[str, Any],
    received_at: datetime,
) -> tuple[list[dict], str]:
    entries = _build_position_menu_entries_safe(week_id, facility_id)
    if entries:
        return entries, "weekly_menu"
    ocr_entries = _build_position_menu_entries_from_ocr_payload(
        payload=ocr_payload,
        template=template,
        received_at=received_at,
    )
    if ocr_entries:
        return ocr_entries, "ocr_table"
    return [], "menu_missing"


def _filter_position_menu_entries_by_dates(
    entries: list[dict],
    target_dates: set[date] | None,
    *,
    min_anchor_dates: int = 2,
) -> list[dict]:
    if not entries or not target_dates:
        return list(entries)
    normalized_dates = {item for item in target_dates if isinstance(item, date)}
    if not normalized_dates:
        return list(entries)

    entry_dates = {
        item.get("menu_date")
        for item in entries
        if isinstance(item, dict) and isinstance(item.get("menu_date"), date)
    }
    matched_dates = sorted(item for item in normalized_dates if item in entry_dates)
    if len(matched_dates) < max(1, int(min_anchor_dates)):
        # Single-date anchors are often OCR noise; keep original monthly scope.
        return list(entries)

    filtered = [
        item
        for item in entries
        if isinstance(item, dict)
        and isinstance(item.get("menu_date"), date)
        and item.get("menu_date") in set(matched_dates)
    ]
    if not filtered:
        return list(entries)

    min_date = matched_dates[0]
    max_date = matched_dates[-1]
    span_days = (max_date - min_date).days
    if 1 < span_days <= 10:
        range_filtered = [
            item
            for item in entries
            if isinstance(item, dict)
            and isinstance(item.get("menu_date"), date)
            and min_date <= item.get("menu_date") <= max_date
        ]
        if len(range_filtered) >= len(filtered):
            filtered = range_filtered
    return filtered


def _select_dominant_date_cluster(
    dates: set[date] | None,
    *,
    max_gap_days: int = 2,
    min_cluster_size: int = 3,
    min_cluster_share: float = 0.6,
) -> set[date]:
    normalized = sorted(item for item in (dates or set()) if isinstance(item, date))
    if len(normalized) <= 1:
        return set(normalized)

    clusters: list[list[date]] = []
    current: list[date] = []
    for item in normalized:
        if not current:
            current = [item]
            continue
        prev = current[-1]
        if (item - prev).days <= max(1, int(max_gap_days)):
            current.append(item)
        else:
            clusters.append(current)
            current = [item]
    if current:
        clusters.append(current)
    if len(clusters) <= 1:
        return set(normalized)

    best = max(clusters, key=lambda cluster: (len(cluster), cluster[-1]))
    if (
        len(best) >= max(1, int(min_cluster_size))
        and (len(best) / len(normalized)) >= float(min_cluster_share)
    ):
        return set(best)
    return set(normalized)


def _build_reparse_position_menu_entries(
    *,
    week_id: str | None,
    facility_id: str | None = None,
    lines: list[dict[str, Any]] | None,
    rows: list[list[Any]] | None,
    parsed_output: dict[str, Any] | None,
    existing_lines: list[dict[str, Any]] | None = None,
    extra_payload_dates: set[date] | None = None,
    received_at: datetime,
) -> list[dict]:
    lines_for_scope = list(lines or [])
    payload_dates: set[date] = {
        item for item in (extra_payload_dates or set()) if isinstance(item, date)
    }
    existing_line_dates = _collect_line_dates_for_position_scope(existing_lines)
    line_dates = _collect_line_dates_for_position_scope(lines_for_scope)
    if isinstance(parsed_output, dict):
        payload_dates |= {
            item
            for item in _collect_sheet_dates_from_payload(parsed_output, received_at)
            if isinstance(item, date)
        }
    payload_dates |= _collect_sheet_dates_from_rows(rows, received_at=received_at)
    observed_payload_dates = {
        item for item in payload_dates if isinstance(item, date)
    }
    if len(existing_line_dates) >= 2:
        lower = min(existing_line_dates) - timedelta(days=1)
        upper = max(existing_line_dates) + timedelta(days=1)
        existing_min = min(existing_line_dates)
        existing_max = max(existing_line_dates)
        existing_span_days = (existing_max - existing_min).days
        entry_dates_for_scope: set[date] = set()
        if week_id:
            entry_dates_for_scope = {
                item.get("menu_date")
                for item in _build_position_menu_entries_safe(week_id, facility_id)
                if isinstance(item, dict) and isinstance(item.get("menu_date"), date)
            }
        payload_dates_in_scope = {
            item for item in observed_payload_dates if item in entry_dates_for_scope
        }
        payload_dates_inside_existing = {
            item for item in payload_dates_in_scope if lower <= item <= upper
        }
        payload_dates_outside_existing = {
            item for item in payload_dates_in_scope if item < lower or item > upper
        }
        dominant_payload_dates = _select_dominant_date_cluster(payload_dates_in_scope)
        dominant_payload_dates_inside_existing = {
            item for item in dominant_payload_dates if lower <= item <= upper
        }
        dominant_payload_dates_outside_existing = {
            item for item in dominant_payload_dates if item < lower or item > upper
        }
        payload_scope_span_days = 0
        if dominant_payload_dates:
            payload_scope_span_days = (
                max(dominant_payload_dates) - min(dominant_payload_dates)
            ).days
        payload_suggests_week_scope = (
            len(dominant_payload_dates) >= 5
            and len(dominant_payload_dates_outside_existing) >= 3
            and 5 <= payload_scope_span_days <= 10
        )
        existing_scope_is_partial = (
            existing_span_days <= 2 or len(existing_line_dates) <= 3
        )
        allow_payload_scope_override = (
            payload_suggests_week_scope
            and (
                len(dominant_payload_dates_outside_existing)
                > (len(dominant_payload_dates_inside_existing) * 2)
                or existing_scope_is_partial
            )
        )
        if allow_payload_scope_override:
            logger.warning(
                "Reparse existing anchors look stale; prefer payload date anchors",
                existing_dates=[item.isoformat() for item in sorted(existing_line_dates)],
                payload_inside_existing=[item.isoformat() for item in sorted(payload_dates_inside_existing)],
                payload_outside_existing=[item.isoformat() for item in sorted(payload_dates_outside_existing)],
                dominant_payload_dates=[item.isoformat() for item in sorted(dominant_payload_dates)],
                dominant_payload_inside_existing=[
                    item.isoformat() for item in sorted(dominant_payload_dates_inside_existing)
                ],
                dominant_payload_outside_existing=[
                    item.isoformat() for item in sorted(dominant_payload_dates_outside_existing)
                ],
                payload_scope_span_days=payload_scope_span_days,
                existing_scope_is_partial=existing_scope_is_partial,
            )
            # Parsed line dates can still be stale (carried from old scope). When
            # payload anchors are strong enough to override existing scope, defer
            # to payload-driven positioning.
            lines_for_scope = []
        # When persisted lines already define a week scope, ignore parsed line-date
        # anchors that are clearly outside that scope (typical LLM date drift).
        if (
            not allow_payload_scope_override
            and line_dates
            and any(item < lower or item > upper for item in line_dates)
        ):
            logger.warning(
                "Reparse parsed line dates out of existing scope; fallback to existing anchors",
                existing_dates=[item.isoformat() for item in sorted(existing_line_dates)],
                parsed_line_dates=[item.isoformat() for item in sorted(line_dates)],
            )
            lines_for_scope = []
        if not allow_payload_scope_override:
            payload_dates = {
                item for item in observed_payload_dates if lower <= item <= upper
            } | existing_line_dates
        else:
            # Use full in-scope payload anchors when overriding stale/partial
            # existing scope. Keeping only "outside" anchors can drop valid
            # leading/trailing week dates.
            payload_dates = dominant_payload_dates or _select_dominant_date_cluster(
                payload_dates_in_scope or observed_payload_dates
            )
    else:
        payload_dates = observed_payload_dates | existing_line_dates
    return _build_position_entries_for_lines(
        week_id=week_id,
        facility_id=facility_id,
        lines=lines_for_scope,
        payload_dates=payload_dates,
    )


def _collect_line_dates_for_position_scope(lines: list[dict[str, Any]] | None) -> set[date]:
    dates: set[date] = set()
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        parsed = _parse_date_value(line.get("date"))
        if isinstance(parsed, date):
            dates.add(parsed)
    return dates


def _collect_source_row_indexes_for_position_scope(lines: list[dict[str, Any]] | None) -> list[int]:
    indexes: set[int] = set()
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        source_idx_raw = line.get("source_row_index")
        try:
            source_idx = int(source_idx_raw) if source_idx_raw is not None else -1
        except Exception:
            source_idx = -1
        if source_idx >= 0:
            indexes.add(source_idx)
    return sorted(indexes)


def _max_source_row_index_for_position_scope(lines: list[dict[str, Any]] | None) -> int:
    indexes = _collect_source_row_indexes_for_position_scope(lines)
    if not indexes:
        return -1
    return indexes[-1]


def _expand_scoped_entries_for_source_row_span(
    *,
    entries: list[dict],
    scoped_entries: list[dict],
    lines: list[dict[str, Any]] | None,
    max_extension_rows: int = 32,
) -> list[dict]:
    if not entries or not scoped_entries:
        return list(scoped_entries)
    source_indexes = _collect_source_row_indexes_for_position_scope(lines)
    if not source_indexes:
        return list(scoped_entries)
    max_source_row_index = source_indexes[-1]
    if max_source_row_index < 0:
        return list(scoped_entries)
    needed_count = max_source_row_index + 1
    span_density = len(source_indexes) / max(needed_count, 1)
    min_span_density = min(
        _read_reparse_float_env(
            "OCR_REPARSE_SOURCE_ROW_SPAN_MIN_DENSITY",
            0.85,
            min_value=0.0,
        ),
        1.0,
    )
    if span_density < min_span_density:
        return list(scoped_entries)
    overflow_rows = needed_count - len(scoped_entries)
    if overflow_rows <= 0:
        return list(scoped_entries)
    if overflow_rows > max_extension_rows:
        return list(scoped_entries)

    first_entry = scoped_entries[0]
    start_idx: int | None = None
    for idx, item in enumerate(entries):
        if item is first_entry:
            start_idx = idx
            break
    if start_idx is None:
        try:
            start_idx = entries.index(first_entry)
        except ValueError:
            return list(scoped_entries)

    end_idx = start_idx + needed_count
    if end_idx > len(entries):
        return list(scoped_entries)
    return list(entries[start_idx:end_idx])


def _build_position_entries_for_lines(
    *,
    week_id: str | None,
    facility_id: str | None = None,
    lines: list[dict[str, Any]] | None,
    payload_dates: set[date] | None = None,
) -> list[dict]:
    if not week_id:
        return []
    entries = _build_position_menu_entries_safe(week_id, facility_id)
    if not entries:
        return []

    line_dates = _collect_line_dates_for_position_scope(lines)
    line_count = len([line for line in (lines or []) if isinstance(line, dict)])
    line_scope_suspicious = False
    if line_dates:
        if len(line_dates) == 1:
            anchor_date = next(iter(line_dates))
            anchor_row_count = sum(
                1
                for item in entries
                if isinstance(item, dict) and item.get("menu_date") == anchor_date
            )
            # If parsed lines are much larger than one-day menu slots, parsed date anchors
            # likely collapsed into a single day; prefer broader payload anchors.
            if anchor_row_count > 0 and line_count > anchor_row_count * 2:
                line_scope_suspicious = True
        # Line dates are parsed from user-edited/apply inputs and are reliable;
        # allow single-date anchors for strict week/day scope alignment.
        filtered = _filter_position_menu_entries_by_dates(
            entries,
            line_dates,
            min_anchor_dates=1,
        )
        if filtered and not (line_scope_suspicious and len(filtered) < len(entries)):
            return _expand_scoped_entries_for_source_row_span(
                entries=entries,
                scoped_entries=filtered,
                lines=lines,
            )

    if payload_dates:
        # A single payload anchor is still safer than month-wide index drift.
        filtered = _filter_position_menu_entries_by_dates(entries, payload_dates)
        if not filtered or len(filtered) == len(entries):
            filtered = _filter_position_menu_entries_by_dates(
                entries,
                payload_dates,
                min_anchor_dates=1,
            )
        if filtered:
            scoped_entries = list(filtered)
            if not line_scope_suspicious:
                scoped_entries = _expand_scoped_entries_for_source_row_span(
                    entries=entries,
                    scoped_entries=filtered,
                    lines=lines,
                )
            return scoped_entries
    return entries


def _resolve_llm_expected_row_count(
    *,
    menu_expected_row_count: int,
    fallback_expected_row_count: int = 0,
    pipeline_rows: list[list[str]] | None = None,
    observed_rows: list[list[str]] | None = None,
    anchor_date_count: int = 0,
) -> int:
    expected = int(menu_expected_row_count) if menu_expected_row_count and menu_expected_row_count > 0 else 0
    if expected <= 0:
        expected = (
            int(fallback_expected_row_count)
            if fallback_expected_row_count and fallback_expected_row_count > 0
            else 0
        )

    pipeline_count = len([row for row in (pipeline_rows or []) if isinstance(row, list)])
    observed_count = len([row for row in (observed_rows or []) if isinstance(row, list)])
    partial_anchor_max = _read_reparse_int_env(
        "OCR_REPARSE_PARTIAL_ANCHOR_MAX_ROWS",
        24,
        min_value=1,
    )
    partial_anchor_min_gap = _read_reparse_int_env(
        "OCR_REPARSE_PARTIAL_ANCHOR_MIN_GAP_ROWS",
        12,
        min_value=1,
    )
    if pipeline_count <= 0:
        if expected <= 0:
            return observed_count
        # If menu scope is clearly over-broad (for example month-wide 224 rows)
        # while observed rows are week-sized and no reliable pipeline rows exist,
        # use observed row count to avoid false row-coverage failures.
        if observed_count > 0 and expected >= observed_count * 3:
            return observed_count
        # Existing anchors can also be too narrow (for example stale 1-2 day
        # scope after a failed save). When observed OCR rows are significantly
        # larger than this small expectation, trust observed rows.
        if (
            observed_count > 0
            and expected <= partial_anchor_max
            and observed_count >= (expected + partial_anchor_min_gap)
        ):
            return observed_count
        return expected
    if expected <= 0:
        return pipeline_count

    # Existing persisted anchors can be partial (for example only 1-2 dates kept
    # after a previous failed reparse). In that case menu expectation becomes too
    # small and row-coverage gating misses obvious shortfalls. When pipeline rows
    # are significantly larger than this small expectation, trust observable rows.
    if (
        expected <= partial_anchor_max
        and pipeline_count >= (expected + partial_anchor_min_gap)
    ):
        return pipeline_count

    weak_anchor_max_dates = _read_reparse_int_env(
        "OCR_REPARSE_WEAK_ANCHOR_MAX_DATES",
        1,
        min_value=0,
    )
    weak_anchor_min_gap = _read_reparse_int_env(
        "OCR_REPARSE_WEAK_ANCHOR_MIN_GAP_ROWS",
        8,
        min_value=1,
    )
    weak_anchor_observed_delta = _read_reparse_int_env(
        "OCR_REPARSE_WEAK_ANCHOR_OBSERVED_DELTA_ROWS",
        3,
        min_value=0,
    )
    if (
        pipeline_count > 0
        and expected > pipeline_count
        and int(anchor_date_count) <= weak_anchor_max_dates
        and (expected - pipeline_count) >= weak_anchor_min_gap
    ):
        observed_is_close = (
            observed_count <= 0
            or abs(observed_count - pipeline_count) <= weak_anchor_observed_delta
        )
        if observed_is_close:
            return max(pipeline_count, observed_count)

    # When menu expectation is month-wide but OCR/pipeline rows are week-scoped,
    # avoid over-rejecting by preferring the observable pipeline row count.
    if expected >= pipeline_count * 2:
        return pipeline_count
    return expected


def _group_lines_for_position_mapping(lines: list[dict]) -> list[list[dict]]:
    grouped: list[list[dict]] = []
    current: list[dict] = []
    current_key: tuple | None = None
    for row_idx, line in enumerate(lines):
        source_idx_raw = line.get("source_row_index")
        try:
            source_idx = int(source_idx_raw) if source_idx_raw is not None else None
        except Exception:
            source_idx = None
        key = (
            source_idx,
            line.get("date"),
            _normalize_daypart_key(line.get("daypart")),
            line.get("menu_name"),
        )
        if current and key != current_key:
            grouped.append(current)
            current = []
        if not current:
            current_key = key
        line_copy = dict(line)
        line_copy["_row_order"] = row_idx
        current.append(line_copy)
    if current:
        grouped.append(current)
    return grouped


def _apply_menu_position_mapping(
    lines: list[dict],
    week_id: str | None,
    *,
    facility_id: str | None = None,
    entries_override: list[dict] | None = None,
) -> tuple[list[dict], int]:
    entries: list[dict]
    if isinstance(entries_override, list):
        entries = [item for item in entries_override if isinstance(item, dict)]
    else:
        if not week_id:
            return lines, 0
        entries = _build_position_menu_entries_safe(week_id, facility_id)
    if not entries:
        return lines, 0

    entries_by_date_daypart: dict[tuple[date, str], list[dict]] = {}
    entries_by_date: dict[date, list[dict]] = {}
    entries_by_daypart: dict[str, list[dict]] = {}
    for entry in entries:
        entry_date = entry.get("menu_date")
        daypart_key = entry.get("daypart_key") or ""
        if isinstance(entry_date, date) and daypart_key:
            entries_by_date_daypart.setdefault((entry_date, daypart_key), []).append(entry)
        if isinstance(entry_date, date):
            entries_by_date.setdefault(entry_date, []).append(entry)
        if daypart_key:
            entries_by_daypart.setdefault(daypart_key, []).append(entry)

    grouped_rows = _group_lines_for_position_mapping(lines)
    counters_date_daypart: dict[tuple[date, str], int] = {}
    counters_date: dict[date, int] = {}
    counters_daypart: dict[str, int] = {}
    mapped_rows = 0
    mapped_lines: list[dict] = []

    for row in grouped_rows:
        first = row[0]
        row_date = _parse_date_value(first.get("date"))
        daypart_key = _normalize_daypart_key(first.get("daypart"))
        source_row_raw = first.get("source_row_index")
        try:
            source_row_index = int(source_row_raw) if source_row_raw is not None else None
        except Exception:
            source_row_index = None

        selected = None
        if (
            source_row_index is not None
            and source_row_index >= 0
            and source_row_index < len(entries)
        ):
            selected = entries[source_row_index]
        if selected is None and isinstance(row_date, date) and daypart_key:
            dd_key = (row_date, daypart_key)
            candidates = entries_by_date_daypart.get(dd_key) or []
            idx = counters_date_daypart.get(dd_key, 0)
            if idx < len(candidates):
                selected = candidates[idx]
                counters_date_daypart[dd_key] = idx + 1
        if selected is None and isinstance(row_date, date):
            candidates = entries_by_date.get(row_date) or []
            idx = counters_date.get(row_date, 0)
            if idx < len(candidates):
                selected = candidates[idx]
                counters_date[row_date] = idx + 1
        if selected is None and daypart_key:
            candidates = entries_by_daypart.get(daypart_key) or []
            idx = counters_daypart.get(daypart_key, 0)
            if idx < len(candidates):
                selected = candidates[idx]
                counters_daypart[daypart_key] = idx + 1

        if selected is None:
            for line in row:
                line.pop("_row_order", None)
                mapped_lines.append(line)
            continue

        mapped_rows += 1
        selected_name = str(selected.get("menu_name") or "").strip()
        selected_date = selected.get("menu_date")
        selected_daypart = selected.get("daypart_key") or ""
        for line in row:
            if selected_name:
                line["menu_name"] = selected_name
            # In weekly-menu position mapping, week menu is source of truth for
            # non-quantity fields. OCR daypart/date drift (especially around
            # noon/evening boundaries) must not survive into persisted lines.
            if selected_daypart:
                line["daypart"] = selected_daypart
            if isinstance(selected_date, date):
                line["date"] = selected_date
            line.pop("_row_order", None)
            mapped_lines.append(line)

    return mapped_lines, mapped_rows


def _apply_menu_position_mapping_safe(
    lines: list[dict[str, Any]],
    week_id: str | None,
    *,
    facility_id: str | None = None,
    entries_override: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    if entries_override:
        try:
            return _apply_menu_position_mapping(
                lines,
                week_id,
                facility_id=facility_id,
                entries_override=entries_override,
            )
        except TypeError as exc:
            if "entries_override" not in str(exc):
                raise
    return _apply_menu_position_mapping(lines, week_id, facility_id=facility_id)


def _build_canonical_menu_key(
    *,
    menu_date: object,
    daypart: object,
    menu_name: object,
) -> tuple[str, str, str] | None:
    parsed_date = _parse_date_value(menu_date)
    daypart_key = _normalize_daypart_key(daypart)
    menu_raw = str(menu_name or "").strip()
    menu_key = _normalize_menu_text(menu_raw) if menu_raw else ""
    if not isinstance(parsed_date, date) or not daypart_key or not menu_key:
        return None
    return parsed_date.isoformat(), daypart_key, menu_key


def _parse_strict_numeric_cell(value: object) -> float | None:
    text = str(value or "").translate(_MENU_TRANSLATION)
    cleaned = re.sub(r"[\s　]+", "", text)
    cleaned = cleaned.replace(",", "").strip("()[]（）")
    if not cleaned:
        return None
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    try:
        max_abs = float(os.getenv("OCR_SHEET_MAX_QTY", "150"))
    except Exception:
        max_abs = 150.0
    if max_abs > 0 and abs(parsed) > max_abs:
        return None
    return parsed


def _collect_numeric_source_rows_for_reparse(
    *,
    rows: list[list[str]],
    template: dict[str, Any],
    rows_are_body_only: bool = False,
) -> set[int]:
    if not rows:
        return set()
    columns = template.get("columns")
    if not isinstance(columns, list):
        return set()
    quantity_indexes = sorted(
        {
            int(col.get("index"))
            for col in columns
            if isinstance(col, dict)
            and col.get("role") in {"quantity", "quantity_change"}
            and isinstance(col.get("index"), int)
        }
    )
    if not quantity_indexes:
        return set()
    header_rows_raw = template.get("header_rows", 0)
    try:
        header_rows = max(0, int(header_rows_raw))
    except Exception:
        header_rows = 0
    effective_header_rows = 0 if rows_are_body_only else header_rows
    numeric_rows: set[int] = set()
    for source_row_index, row in enumerate(rows[effective_header_rows:]):
        if not isinstance(row, list):
            continue
        for col_idx in quantity_indexes:
            if col_idx < 0 or col_idx >= len(row):
                continue
            qty = _parse_strict_numeric_cell(row[col_idx])
            if qty is None:
                continue
            numeric_rows.add(source_row_index)
            break
    return numeric_rows


def _template_quantity_column_indexes(template: dict[str, Any]) -> list[int]:
    return [item["index"] for item in _template_quantity_columns(template)]


def _template_quantity_columns(template: dict[str, Any]) -> list[dict[str, str | int]]:
    columns = template.get("columns")
    if not isinstance(columns, list):
        return []
    unique_by_index: dict[int, dict[str, str | int]] = {}
    for col in columns:
        if not isinstance(col, dict):
            continue
        if col.get("role") not in {"quantity", "quantity_change"}:
            continue
        index_raw = col.get("index")
        if not isinstance(index_raw, int):
            continue
        item = {
            "index": int(index_raw),
            "diet_type": str(col.get("diet_type") or "").strip(),
            "area_id": str(col.get("area_id") or "").strip(),
            "bag_type": str(col.get("bag_type") or "").strip(),
        }
        unique_by_index[int(index_raw)] = item
    return [unique_by_index[idx] for idx in sorted(unique_by_index.keys())]


def _template_structural_column_indexes(template: dict[str, Any]) -> list[int]:
    columns = template.get("columns")
    if not isinstance(columns, list):
        return []
    roles = {"date", "daypart", "menu", "menu_name"}
    structural_indexes: set[int] = set()
    for col in columns:
        if not isinstance(col, dict):
            continue
        if str(col.get("role") or "").strip() not in roles:
            continue
        index_raw = col.get("index")
        if not isinstance(index_raw, int):
            continue
        structural_indexes.add(int(index_raw))
    return sorted(structural_indexes)


def _row_has_structural_data(
    row: list[str],
    structural_indexes: list[int],
) -> bool:
    for col_index in structural_indexes:
        if col_index < 0 or col_index >= len(row):
            continue
        if str(row[col_index]).strip():
            return True
    return False


def _should_project_quantity_rows_to_structural_rows(
    *,
    rows: list[list[str]],
    structural_rows: list[list[str]],
    template: dict[str, Any],
) -> bool:
    normalized_rows = [list(row) for row in rows if isinstance(row, list)]
    normalized_structural_rows = [list(row) for row in structural_rows if isinstance(row, list)]
    if not normalized_rows or not normalized_structural_rows:
        return False
    max_row_overflow = max(2, int(len(normalized_structural_rows) * 0.1))
    if len(normalized_rows) > len(normalized_structural_rows) + max_row_overflow:
        return False
    if _rows_look_like_quantity_only(
        rows=normalized_rows,
        template=template,
        rows_are_body_only=True,
    ):
        return True
    structural_indexes = _template_structural_column_indexes(template)
    if not structural_indexes:
        return False
    rows_with_structural_data = sum(
        1
        for row in normalized_rows
        if _row_has_structural_data(row=row, structural_indexes=structural_indexes)
    )
    if rows_with_structural_data <= 0:
        return len(normalized_rows) <= len(normalized_structural_rows)
    structural_ratio = float(rows_with_structural_data) / float(len(normalized_rows))
    if len(normalized_rows) < len(normalized_structural_rows):
        return structural_ratio <= 0.2
    return structural_ratio < 0.1


def _analyze_quantity_pair_similarity(
    *,
    rows: list[list[str]],
    left_index: int,
    right_index: int,
) -> dict[str, Any]:
    overlap = 0
    equal_count = 0
    non_zero_overlap = 0
    distinct_pairs: set[tuple[str, str]] = set()
    sample_rows: list[int] = []
    for row_idx, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        if left_index < 0 or right_index < 0:
            continue
        if left_index >= len(row) or right_index >= len(row):
            continue
        left_qty = _parse_strict_numeric_cell(row[left_index])
        right_qty = _parse_strict_numeric_cell(row[right_index])
        if left_qty is None or right_qty is None:
            continue
        overlap += 1
        if abs(left_qty) > 1e-9 or abs(right_qty) > 1e-9:
            non_zero_overlap += 1
        left_repr = _format_merged_quantity_cell(left_qty)
        right_repr = _format_merged_quantity_cell(right_qty)
        distinct_pairs.add((left_repr, right_repr))
        if abs(left_qty - right_qty) <= 1e-9:
            equal_count += 1
            if len(sample_rows) < 12:
                sample_rows.append(row_idx)
    equal_ratio = float(equal_count) / float(overlap) if overlap > 0 else 0.0
    return {
        "overlap": overlap,
        "equal_count": equal_count,
        "equal_ratio": equal_ratio,
        "non_zero_overlap": non_zero_overlap,
        "distinct_pairs": len(distinct_pairs),
        "sample_row_indexes": sample_rows,
    }


def _detect_mirrored_quantity_column_anomalies(
    *,
    rows: list[list[str]],
    quantity_columns: list[dict[str, str | int]],
    reference_rows: list[list[str]] | None = None,
) -> list[dict[str, Any]]:
    if len(quantity_columns) < 2:
        return []
    min_overlap = _read_reparse_int_env("OCR_REPARSE_COLUMN_MIRROR_MIN_OVERLAP", 10, min_value=2)
    min_non_zero_overlap = _read_reparse_int_env(
        "OCR_REPARSE_COLUMN_MIRROR_MIN_NONZERO_OVERLAP",
        4,
        min_value=1,
    )
    min_equal_ratio = _read_reparse_float_env(
        "OCR_REPARSE_COLUMN_MIRROR_MIN_EQUAL_RATIO",
        0.98,
        min_value=0.0,
    )
    if min_equal_ratio > 1.0:
        min_equal_ratio = 1.0
    min_distinct_pairs = _read_reparse_int_env(
        "OCR_REPARSE_COLUMN_MIRROR_MIN_DISTINCT_PAIRS",
        3,
        min_value=1,
    )

    anomalies: list[dict[str, Any]] = []
    for left_pos, left_col in enumerate(quantity_columns):
        left_index_raw = left_col.get("index")
        left_index = int(left_index_raw) if isinstance(left_index_raw, int) else -1
        left_area = str(left_col.get("area_id") or "").strip().lower()
        left_diet = str(left_col.get("diet_type") or "").strip().lower()
        left_bag = str(left_col.get("bag_type") or "").strip().lower()
        if left_index < 0 or not left_area:
            continue
        for right_col in quantity_columns[left_pos + 1 :]:
            right_index_raw = right_col.get("index")
            right_index = int(right_index_raw) if isinstance(right_index_raw, int) else -1
            right_area = str(right_col.get("area_id") or "").strip().lower()
            right_diet = str(right_col.get("diet_type") or "").strip().lower()
            right_bag = str(right_col.get("bag_type") or "").strip().lower()
            if right_index < 0 or not right_area:
                continue
            if left_area == right_area:
                continue
            if left_diet != right_diet or left_bag != right_bag:
                continue

            stats = _analyze_quantity_pair_similarity(
                rows=rows,
                left_index=left_index,
                right_index=right_index,
            )
            overlap = int(stats.get("overlap") or 0)
            equal_ratio = float(stats.get("equal_ratio") or 0.0)
            non_zero_overlap = int(stats.get("non_zero_overlap") or 0)
            distinct_pairs = int(stats.get("distinct_pairs") or 0)
            if overlap < min_overlap:
                continue
            if non_zero_overlap < min_non_zero_overlap:
                continue
            if distinct_pairs < min_distinct_pairs:
                continue
            if equal_ratio < min_equal_ratio:
                continue

            if isinstance(reference_rows, list) and reference_rows:
                reference_stats = _analyze_quantity_pair_similarity(
                    rows=reference_rows,
                    left_index=left_index,
                    right_index=right_index,
                )
                ref_overlap = int(reference_stats.get("overlap") or 0)
                ref_equal_ratio = float(reference_stats.get("equal_ratio") or 0.0)
                ref_non_zero_overlap = int(reference_stats.get("non_zero_overlap") or 0)
                ref_distinct_pairs = int(reference_stats.get("distinct_pairs") or 0)
                # When reference rows show the same mirror pattern, treat it as valid
                # sheet structure and avoid false positives.
                if (
                    ref_overlap >= min_overlap
                    and ref_non_zero_overlap >= min_non_zero_overlap
                    and ref_distinct_pairs >= min_distinct_pairs
                    and ref_equal_ratio >= min_equal_ratio
                ):
                    continue

            anomalies.append(
                {
                    "index": right_index,
                    "reason": "mirrored_sibling_columns",
                    "mirror_with_index": left_index,
                    "pair_signature": {
                        "diet_type": str(left_col.get("diet_type") or ""),
                        "bag_type": str(left_col.get("bag_type") or ""),
                        "left_area_id": str(left_col.get("area_id") or ""),
                        "right_area_id": str(right_col.get("area_id") or ""),
                    },
                    "overlap": overlap,
                    "equal_count": int(stats.get("equal_count") or 0),
                    "equal_ratio": round(equal_ratio, 4),
                    "non_zero_overlap": non_zero_overlap,
                    "distinct_pairs": distinct_pairs,
                    "sample_row_indexes": list(stats.get("sample_row_indexes") or []),
                    "thresholds": {
                        "min_overlap": min_overlap,
                        "min_non_zero_overlap": min_non_zero_overlap,
                        "min_equal_ratio": min_equal_ratio,
                        "min_distinct_pairs": min_distinct_pairs,
                    },
                }
            )
    return anomalies


def _format_merged_quantity_cell(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _merge_llm_quantity_only_rows_with_pipeline(
    *,
    llm_rows: list[list[str]],
    pipeline_rows: list[list[str]],
    template: dict[str, Any],
) -> tuple[list[list[str]], dict[str, int]]:
    stats = {
        "pipeline_rows": len(pipeline_rows),
        "llm_rows": len(llm_rows),
        "rows_with_qty": 0,
        "quantity_cells_updated": 0,
    }
    if not llm_rows or not pipeline_rows:
        return llm_rows, stats
    quantity_indexes = _template_quantity_column_indexes(template)
    if not quantity_indexes:
        return llm_rows, stats

    merged_rows: list[list[str]] = [list(row) if isinstance(row, list) else [] for row in pipeline_rows]
    for row_index, llm_row in enumerate(llm_rows):
        if not isinstance(llm_row, list):
            continue
        while row_index >= len(merged_rows):
            merged_rows.append([])
        target_row = merged_rows[row_index]
        row_has_qty = False
        for col_index in quantity_indexes:
            if col_index < 0 or col_index >= len(llm_row):
                continue
            qty = _parse_strict_numeric_cell(llm_row[col_index])
            if qty is None:
                continue
            while len(target_row) <= col_index:
                target_row.append("")
            target_row[col_index] = _format_merged_quantity_cell(qty)
            stats["quantity_cells_updated"] += 1
            row_has_qty = True
        if row_has_qty:
            stats["rows_with_qty"] += 1
    return merged_rows, stats


def _rows_look_like_quantity_only(
    *,
    rows: list[list[str]],
    template: dict[str, Any],
    rows_are_body_only: bool = False,
) -> bool:
    if not rows:
        return False
    columns = template.get("columns")
    if not isinstance(columns, list):
        return False
    quantity_indexes = sorted(
        {
            int(col.get("index"))
            for col in columns
            if isinstance(col, dict)
            and col.get("role") in {"quantity", "quantity_change"}
            and isinstance(col.get("index"), int)
        }
    )
    if not quantity_indexes:
        return False
    structural_indexes = sorted(
        {
            int(col.get("index"))
            for col in columns
            if isinstance(col, dict)
            and col.get("role") in {"date", "daypart", "menu_name"}
            and isinstance(col.get("index"), int)
        }
    )
    if not structural_indexes:
        return False
    header_rows_raw = template.get("header_rows", 0)
    try:
        header_rows = max(0, int(header_rows_raw))
    except Exception:
        header_rows = 0
    effective_header_rows = 0 if rows_are_body_only else header_rows
    data_rows = [row for row in rows[effective_header_rows:] if isinstance(row, list)]
    if not data_rows:
        return False

    rows_with_qty = 0
    rows_with_empty_structure = 0
    for row in data_rows:
        has_qty = False
        for col_idx in quantity_indexes:
            if col_idx < 0 or col_idx >= len(row):
                continue
            if _parse_strict_numeric_cell(row[col_idx]) is not None:
                has_qty = True
                break
        if has_qty:
            rows_with_qty += 1
        structure_values = [
            str(row[col_idx]).strip()
            for col_idx in structural_indexes
            if col_idx >= 0 and col_idx < len(row)
        ]
        if not any(structure_values):
            rows_with_empty_structure += 1
    if rows_with_qty == 0:
        return False
    # Treat as quantity-only when most rows have quantity cells but all structural
    # columns (date/daypart/menu) are blank.
    return rows_with_empty_structure >= max(1, int(len(data_rows) * 0.7))


def _read_reparse_float_env(name: str, default: float, *, min_value: float | None = None) -> float:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = float(raw)
    except Exception:
        value = float(default)
    if min_value is not None and value < min_value:
        value = float(min_value)
    return value


def _read_reparse_int_env(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = int(raw)
    except Exception:
        value = int(default)
    if min_value is not None and value < min_value:
        value = int(min_value)
    return value


def _read_reparse_bool_env(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _run_reparse_with_heartbeat(
    job_id: str,
    *,
    processing_stage: str,
    func,
    result_state: str = "processing",
    metrics_patch: dict[str, Any] | None = None,
):
    interval_seconds = _read_reparse_float_env(
        "OCR_REPARSE_HEARTBEAT_SECONDS",
        45.0,
        min_value=5.0,
    )
    stop_event = threading.Event()

    def _heartbeat() -> None:
        while not stop_event.wait(interval_seconds):
            try:
                _update_reparse_job_progress(
                    job_id,
                    status="running",
                    processing_stage=processing_stage,
                    result_state=result_state,
                    metrics_patch=metrics_patch,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Reparse heartbeat update skipped job_id={} stage={} error={}",
                    job_id,
                    processing_stage,
                    str(exc),
                )

    heartbeat_thread = threading.Thread(
        target=_heartbeat,
        name=f"reparse-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        return func()
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=1.0)


def _resolve_reparse_soft_warning_codes() -> set[str]:
    raw = str(
        os.getenv(
            "OCR_REPARSE_SOFT_WARNING_CODES",
            "sheet_column_anomaly",
        )
        or ""
    ).strip()
    if not raw:
        return set()
    return {
        token.strip().lower()
        for token in raw.split(",")
        if token and token.strip()
    }


def _is_soft_warning_validation_error(error_code: str | None) -> bool:
    if not _read_reparse_bool_env("OCR_REPARSE_ENABLE_SOFT_WARNINGS", True):
        return False
    normalized = str(error_code or "").strip().lower()
    if not normalized:
        return False
    return normalized in _resolve_reparse_soft_warning_codes()


def _coerce_reparse_quantity_value(value: object, *, max_abs: float) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed < 0:
        return None
    # Meal counts are integers; OCR/LLM can emit split digits like "2.1".
    if not float(parsed).is_integer():
        scaled = parsed * 10.0
        if 0 < parsed < 10 and abs(scaled - round(scaled)) < 1e-9:
            parsed = float(round(scaled))
        else:
            return None
    current = int(round(parsed))
    if max_abs > 0 and current > max_abs:
        digits = str(abs(current))
        candidates: list[int] = []
        if len(digits) > 1 and digits.endswith("0"):
            candidates.append(int(digits[:-1]))
        if len(digits) >= 3 and digits.startswith("1"):
            candidates.append(int(digits[1:]))
        if len(digits) > 1 and digits.endswith("9"):
            candidates.append(int(digits[:-1]))
        if len(digits) >= 2:
            candidates.append(int(digits[-2:]))
        if len(digits) >= 1:
            candidates.append(int(digits[-1]))
        picked: int | None = None
        for candidate in candidates:
            if 0 < candidate <= max_abs:
                picked = candidate
                break
        if picked is None:
            return None
        current = picked
    return float(current)


def _sanitize_reparse_line_quantities(lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    max_abs = _read_reparse_float_env("OCR_REPARSE_MAX_QTY", 50.0, min_value=1.0)
    stats = {
        "max_abs_qty": int(max_abs),
        "lines_in": len(lines),
        "lines_out": 0,
        "quantity_adjusted": 0,
        "quantity_dropped": 0,
        "lines_dropped": 0,
    }
    sanitized: list[dict[str, Any]] = []
    for raw in lines:
        if not isinstance(raw, dict):
            continue
        line = dict(raw)
        for key in ("quantity_corrected", "quantity_original"):
            if key not in line:
                continue
            original = line.get(key)
            if original is None:
                continue
            coerced = _coerce_reparse_quantity_value(original, max_abs=max_abs)
            if coerced is None:
                line[key] = None
                stats["quantity_dropped"] += 1
                continue
            if isinstance(original, (int, float)) and abs(float(original) - coerced) < 1e-9:
                line[key] = coerced
                continue
            line[key] = coerced
            stats["quantity_adjusted"] += 1
        if line.get("quantity_corrected") is None and line.get("quantity_original") is None:
            stats["lines_dropped"] += 1
            continue
        sanitized.append(line)
    stats["lines_out"] = len(sanitized)
    return sanitized, stats


def _coerce_usage_int(value: object) -> int:
    try:
        parsed = int(value)
    except Exception:
        return 0
    return parsed if parsed > 0 else 0


def _extract_usage_from_provider_debug(provider_debug: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(provider_debug, dict):
        return {}

    usage_raw = provider_debug.get("usage")
    if not isinstance(usage_raw, dict):
        attempts = provider_debug.get("attempts")
        if isinstance(attempts, list) and attempts:
            last_attempt = attempts[-1]
            if isinstance(last_attempt, dict):
                usage_raw = last_attempt.get("usage")
    if not isinstance(usage_raw, dict):
        return {}

    prompt_tokens = _coerce_usage_int(usage_raw.get("prompt_tokens"))
    completion_tokens = _coerce_usage_int(usage_raw.get("completion_tokens"))
    total_tokens = _coerce_usage_int(usage_raw.get("total_tokens"))
    cached_content_tokens = _coerce_usage_int(usage_raw.get("cached_content_tokens"))

    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_content_tokens": cached_content_tokens,
    }


def _resolve_reparse_cost_rates(
    *,
    provider: str,
    model: str | None,
) -> tuple[float, float]:
    normalized_provider = str(provider or "").strip().lower()
    normalized_model = str(model or "").strip().lower()
    if normalized_provider == "gemini":
        if "pro" in normalized_model:
            input_rate = _read_reparse_float_env(
                "OCR_REPARSE_COST_GEMINI_PRO_INPUT_USD_PER_1M",
                1.25,
                min_value=0.0,
            )
            output_rate = _read_reparse_float_env(
                "OCR_REPARSE_COST_GEMINI_PRO_OUTPUT_USD_PER_1M",
                10.0,
                min_value=0.0,
            )
        else:
            input_rate = _read_reparse_float_env(
                "OCR_REPARSE_COST_GEMINI_FLASH_INPUT_USD_PER_1M",
                0.30,
                min_value=0.0,
            )
            output_rate = _read_reparse_float_env(
                "OCR_REPARSE_COST_GEMINI_FLASH_OUTPUT_USD_PER_1M",
                2.50,
                min_value=0.0,
            )
    elif normalized_provider == "openai":
        input_rate = _read_reparse_float_env(
            "OCR_REPARSE_COST_OPENAI_INPUT_USD_PER_1M",
            5.0,
            min_value=0.0,
        )
        output_rate = _read_reparse_float_env(
            "OCR_REPARSE_COST_OPENAI_OUTPUT_USD_PER_1M",
            15.0,
            min_value=0.0,
        )
    else:
        input_rate = _read_reparse_float_env(
            "OCR_REPARSE_COST_INPUT_USD_PER_1M",
            0.0,
            min_value=0.0,
        )
        output_rate = _read_reparse_float_env(
            "OCR_REPARSE_COST_OUTPUT_USD_PER_1M",
            0.0,
            min_value=0.0,
        )
    return input_rate, output_rate


def _estimate_reparse_llm_cost(
    *,
    provider: str | None,
    model: str | None,
    provider_debug: dict[str, Any] | None,
) -> dict[str, Any] | None:
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in {"gemini", "openai"}:
        return None

    usage = _extract_usage_from_provider_debug(provider_debug)
    if not usage or usage.get("total_tokens", 0) <= 0:
        return None

    input_rate, output_rate = _resolve_reparse_cost_rates(
        provider=normalized_provider,
        model=model,
    )
    estimated_cost: float | None = None
    if input_rate > 0.0 or output_rate > 0.0:
        estimated_cost = (
            (float(usage.get("prompt_tokens", 0)) * input_rate)
            + (float(usage.get("completion_tokens", 0)) * output_rate)
        ) / 1_000_000.0

    soft_limit = _read_reparse_float_env(
        "OCR_REPARSE_COST_SOFT_LIMIT_USD",
        0.10,
        min_value=0.0,
    )
    hard_limit = _read_reparse_float_env(
        "OCR_REPARSE_COST_HARD_LIMIT_USD",
        1.00,
        min_value=0.0,
    )

    over_soft = (
        estimated_cost is not None
        and soft_limit > 0.0
        and estimated_cost > soft_limit
    )
    over_hard = (
        estimated_cost is not None
        and hard_limit > 0.0
        and estimated_cost > hard_limit
    )
    return {
        "provider": normalized_provider,
        "model": str(model or "").strip() or None,
        "usage": usage,
        "pricing": {
            "input_usd_per_1m_tokens": input_rate,
            "output_usd_per_1m_tokens": output_rate,
        },
        "estimated_cost_usd": estimated_cost,
        "soft_limit_usd": soft_limit,
        "hard_limit_usd": hard_limit,
        "over_soft_limit": bool(over_soft),
        "over_hard_limit": bool(over_hard),
    }


def _quantity_column_non_empty_counts(
    *,
    rows: list[list[str]],
    quantity_indexes: list[int],
) -> dict[int, int]:
    counts: dict[int, int] = {idx: 0 for idx in quantity_indexes}
    for row in rows:
        if not isinstance(row, list):
            continue
        for idx in quantity_indexes:
            if idx < 0 or idx >= len(row):
                continue
            if _parse_strict_numeric_cell(row[idx]) is not None:
                counts[idx] = counts.get(idx, 0) + 1
    return counts


def _evaluate_quantity_only_rows_quality(
    *,
    rows: list[list[str]],
    template: dict[str, Any],
    expected_row_count: int,
    reference_rows: list[list[str]] | None,
) -> tuple[str | None, dict[str, Any] | None]:
    normalized_rows = [list(row) for row in rows if isinstance(row, list)]
    row_count = len(normalized_rows)
    effective_expected = max(int(expected_row_count or 0), 0)
    if effective_expected <= 0 and isinstance(reference_rows, list):
        effective_expected = max(len([row for row in reference_rows if isinstance(row, list)]), 0)
    if effective_expected <= 0:
        return None, None

    row_coverage_ratio = float(row_count) / float(effective_expected) if effective_expected > 0 else 1.0
    missing_tail_rows = max(effective_expected - row_count, 0)
    min_coverage_ratio = _read_reparse_float_env(
        "OCR_REPARSE_ROW_COVERAGE_MIN_RATIO",
        0.98,
        min_value=0.0,
    )
    max_missing_tail = _read_reparse_int_env(
        "OCR_REPARSE_MAX_MISSING_TAIL_ROWS",
        0,
        min_value=0,
    )

    detail: dict[str, Any] = {
        "expected_row_count": int(effective_expected),
        "actual_row_count": int(row_count),
        "row_coverage_ratio": round(row_coverage_ratio, 4),
        "missing_tail_rows": int(missing_tail_rows),
        "min_coverage_ratio": float(min_coverage_ratio),
        "max_missing_tail_rows": int(max_missing_tail),
    }

    if row_coverage_ratio < min_coverage_ratio or missing_tail_rows > max_missing_tail:
        detail["quality_issue"] = "row_coverage"
        return "sheet_row_coverage_low", detail

    extra_rows = max(row_count - effective_expected, 0)
    max_extra_rows = _read_reparse_int_env(
        "OCR_REPARSE_MAX_EXTRA_ROWS",
        0,
        min_value=0,
    )
    detail["extra_rows"] = int(extra_rows)
    detail["max_extra_rows"] = int(max_extra_rows)
    if extra_rows > max_extra_rows:
        detail["quality_issue"] = "row_overfill"
        return "sheet_row_overfill", detail

    if str(os.getenv("OCR_REPARSE_ENABLE_COLUMN_ANOMALY_GATE", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return None, detail

    quantity_columns = _template_quantity_columns(template)
    quantity_indexes: list[int] = []
    for item in quantity_columns:
        index_raw = item.get("index")
        if not isinstance(index_raw, int):
            continue
        if index_raw < 0:
            continue
        quantity_indexes.append(index_raw)
    reference_normalized = (
        [list(row) for row in reference_rows if isinstance(row, list)]
        if isinstance(reference_rows, list)
        else []
    )
    if not quantity_indexes:
        return None, detail

    llm_counts = _quantity_column_non_empty_counts(
        rows=normalized_rows,
        quantity_indexes=quantity_indexes,
    )
    detail["column_non_empty"] = {
        "llm": {str(idx): int(llm_counts.get(idx, 0)) for idx in quantity_indexes},
    }

    anomaly_columns: list[dict[str, Any]] = []
    if not reference_normalized:
        detail["column_reference_missing"] = True
    else:
        detail["column_reference_missing"] = False
        ref_counts = _quantity_column_non_empty_counts(
            rows=reference_normalized,
            quantity_indexes=quantity_indexes,
        )
        detail["column_non_empty"]["reference"] = {
            str(idx): int(ref_counts.get(idx, 0)) for idx in quantity_indexes
        }
        min_ratio = _read_reparse_float_env("OCR_REPARSE_COLUMN_NONEMPTY_MIN_RATIO", 0.25, min_value=0.0)
        max_ratio = _read_reparse_float_env("OCR_REPARSE_COLUMN_NONEMPTY_MAX_RATIO", 3.0, min_value=0.1)
        if max_ratio < min_ratio:
            max_ratio = min_ratio
        min_reference_count = _read_reparse_int_env(
            "OCR_REPARSE_COLUMN_RATIO_MIN_REFERENCE_COUNT",
            6,
            min_value=1,
        )
        enable_unexpected_nonempty = _read_reparse_bool_env(
            "OCR_REPARSE_ENABLE_COLUMN_UNEXPECTED_NONEMPTY_GATE",
            False,
        )
        unexpected_abs = _read_reparse_int_env("OCR_REPARSE_COLUMN_UNEXPECTED_NONEMPTY_ABS", 4, min_value=1)
        unexpected_ratio = _read_reparse_float_env(
            "OCR_REPARSE_COLUMN_UNEXPECTED_NONEMPTY_RATIO",
            0.12,
            min_value=0.0,
        )

        expected_for_threshold = max(effective_expected, len(reference_normalized))
        unexpected_threshold = max(unexpected_abs, int(expected_for_threshold * unexpected_ratio))
        ratio_skipped_low_reference: dict[str, int] = {}
        for idx in quantity_indexes:
            llm_count = int(llm_counts.get(idx, 0))
            ref_count = int(ref_counts.get(idx, 0))
            if ref_count <= 0:
                if enable_unexpected_nonempty and llm_count >= unexpected_threshold:
                    anomaly_columns.append(
                        {
                            "index": idx,
                            "reason": "unexpected_non_empty",
                            "llm_non_empty": llm_count,
                            "reference_non_empty": ref_count,
                            "threshold": unexpected_threshold,
                        }
                    )
                continue
            if ref_count < min_reference_count:
                ratio_skipped_low_reference[str(idx)] = ref_count
                continue
            ratio = float(llm_count) / float(ref_count)
            if ratio < min_ratio or ratio > max_ratio:
                anomaly_columns.append(
                    {
                        "index": idx,
                        "reason": "non_empty_ratio_out_of_range",
                        "llm_non_empty": llm_count,
                        "reference_non_empty": ref_count,
                        "ratio": round(ratio, 4),
                        "min_ratio": min_ratio,
                        "max_ratio": max_ratio,
                    }
                )
        detail["column_ratio_min_reference_count"] = int(min_reference_count)
        detail["column_ratio_skipped_low_reference"] = ratio_skipped_low_reference
        detail["column_unexpected_nonempty_enabled"] = bool(enable_unexpected_nonempty)

    enable_mirror_gate = _read_reparse_bool_env("OCR_REPARSE_ENABLE_COLUMN_MIRROR_GATE", True)
    mirrored_column_anomalies: list[dict[str, Any]] = []
    if enable_mirror_gate:
        mirrored_column_anomalies = _detect_mirrored_quantity_column_anomalies(
            rows=normalized_rows,
            quantity_columns=quantity_columns,
            reference_rows=reference_normalized if reference_normalized else None,
        )
    if mirrored_column_anomalies:
        anomaly_columns.extend(mirrored_column_anomalies)
    detail["column_mirror_anomaly_count"] = len(mirrored_column_anomalies)

    if "reference" not in detail["column_non_empty"]:
        detail["column_non_empty"]["reference"] = {}

    detail["column_anomaly_count"] = len(anomaly_columns)
    if anomaly_columns:
        detail["quality_issue"] = "column_anomaly"
        detail["column_anomalies"] = anomaly_columns[:20]
        return "sheet_column_anomaly", detail
    return None, detail


def _is_reparse_quality_improved(
    *,
    before_error: str | None,
    before_detail: dict[str, Any] | None,
    after_error: str | None,
    after_detail: dict[str, Any] | None,
) -> bool:
    if before_error and not after_error:
        return True
    if not before_error:
        return False
    if before_error != after_error:
        return False
    if not isinstance(before_detail, dict) or not isinstance(after_detail, dict):
        return False

    before_coverage = float(before_detail.get("row_coverage_ratio") or 0.0)
    after_coverage = float(after_detail.get("row_coverage_ratio") or 0.0)
    before_missing_tail = int(before_detail.get("missing_tail_rows") or 0)
    after_missing_tail = int(after_detail.get("missing_tail_rows") or 0)
    before_extra_rows = int(before_detail.get("extra_rows") or 0)
    after_extra_rows = int(after_detail.get("extra_rows") or 0)
    before_col_anomaly = int(before_detail.get("column_anomaly_count") or 0)
    after_col_anomaly = int(after_detail.get("column_anomaly_count") or 0)

    if after_coverage > before_coverage:
        return True
    if after_missing_tail < before_missing_tail:
        return True
    if after_extra_rows < before_extra_rows:
        return True
    if after_col_anomaly < before_col_anomaly:
        return True
    return False


def _prefer_existing_week_when_derived_missing_menu(
    *,
    derived_week_id: str | None,
    existing_week_code: str | None,
    facility_id: str | None = None,
) -> str | None:
    if not derived_week_id:
        return existing_week_code
    if not existing_week_code:
        return derived_week_id
    if _build_position_menu_entries_safe(derived_week_id, facility_id):
        return derived_week_id
    if _build_position_menu_entries_safe(existing_week_code, facility_id):
        return existing_week_code
    return derived_week_id


def _compact_line_debug_item(line: dict[str, Any]) -> dict[str, Any]:
    qty_corrected = line.get("quantity_corrected")
    qty_original = line.get("quantity_original")
    quantity = qty_corrected if qty_corrected is not None else qty_original
    return {
        "source_row_index": line.get("source_row_index"),
        "date": str(line.get("date") or ""),
        "daypart": str(line.get("daypart") or ""),
        "menu_name": str(line.get("menu_name") or ""),
        "diet_type": str(line.get("diet_type") or ""),
        "area_id": str(line.get("area_id") or ""),
        "bag_type": str(line.get("bag_type") or ""),
        "quantity": quantity,
    }


def _validate_reparse_lines_against_weekly_menu(
    *,
    lines: list[dict[str, Any]],
    week_id: str | None,
    facility_id: str | None = None,
    ocr_rows: list[list[str]] | None,
    template: dict[str, Any],
    entries_override: list[dict[str, Any]] | None = None,
    rows_are_body_only: bool = False,
) -> tuple[str | None, dict[str, Any] | None]:
    if not lines:
        return None, None
    entries: list[dict[str, Any]]
    if isinstance(entries_override, list):
        entries = [item for item in entries_override if isinstance(item, dict)]
    else:
        if not week_id:
            return None, None
        entries = _build_position_menu_entries_safe(week_id, facility_id)
    if not entries:
        return None, None

    weekly_key_set: set[tuple[str, str, str]] = set()
    weekly_key_by_source_row: dict[int, tuple[str, str, str]] = {}
    for idx, entry in enumerate(entries):
        key = _build_canonical_menu_key(
            menu_date=entry.get("menu_date"),
            daypart=entry.get("daypart_key"),
            menu_name=entry.get("menu_name"),
        )
        if key is None:
            continue
        weekly_key_set.add(key)
        weekly_key_by_source_row[idx] = key

    if not weekly_key_set:
        return None, None

    invalid_lines: list[int] = []
    mismatch_source_rows: list[int] = []
    missing_source_rows: list[int] = []
    line_keys_by_source_row: dict[int, set[tuple[str, str, str]]] = {}
    line_samples: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        line_key = _build_canonical_menu_key(
            menu_date=line.get("date"),
            daypart=line.get("daypart"),
            menu_name=line.get("menu_name"),
        )
        if len(line_samples) < 8:
            line_samples.append(_compact_line_debug_item(line))
        if line_key is None or line_key not in weekly_key_set:
            invalid_lines.append(idx)
            continue
        source_row_raw = line.get("source_row_index")
        try:
            source_row_index = int(source_row_raw) if source_row_raw is not None else None
        except Exception:
            source_row_index = None
        if source_row_index is not None and source_row_index >= 0:
            line_keys_by_source_row.setdefault(source_row_index, set()).add(line_key)

    numeric_source_rows = _collect_numeric_source_rows_for_reparse(
        rows=[list(item) for item in (ocr_rows or []) if isinstance(item, list)],
        template=template,
        rows_are_body_only=rows_are_body_only,
    )
    for source_row_index in sorted(numeric_source_rows):
        expected_key = weekly_key_by_source_row.get(source_row_index)
        if expected_key is None:
            continue
        actual_keys = line_keys_by_source_row.get(source_row_index) or set()
        if not actual_keys:
            missing_source_rows.append(source_row_index)
            continue
        if expected_key not in actual_keys:
            mismatch_source_rows.append(source_row_index)

    if not invalid_lines and not mismatch_source_rows and not missing_source_rows:
        return None, None

    detail = {
        "week_id": week_id,
        "weekly_entry_count": len(entries),
        "line_count": len(lines),
        "invalid_line_indexes": invalid_lines[:40],
        "source_row_mismatches": mismatch_source_rows[:40],
        "source_row_missing": missing_source_rows[:40],
        "numeric_source_rows_checked": sorted(numeric_source_rows)[:80],
        "line_sample": line_samples,
    }
    if invalid_lines or mismatch_source_rows:
        return "sheet_canonical_mismatch", detail
    return "sheet_suspicious_blank_row", detail


def _collect_filled_source_rows_from_lines(
    lines: list[dict[str, Any]] | None,
) -> list[int]:
    indexes: set[int] = set()
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        quantity_value = line.get("quantity_corrected")
        if quantity_value is None or quantity_value == "":
            quantity_value = line.get("quantity_original")
        if _parse_strict_numeric_cell(quantity_value) is None:
            continue
        source_row_raw = line.get("source_row_index")
        try:
            source_row_index = int(source_row_raw) if source_row_raw is not None else -1
        except Exception:
            source_row_index = -1
        if source_row_index >= 0:
            indexes.add(source_row_index)
    return sorted(indexes)


def _validate_reparse_blank_anchor_drift(
    *,
    lines: list[dict[str, Any]],
    structural_fields: list[str] | None,
    structural_rows: list[list[str]] | None,
    reference_rows: list[list[str]] | None,
    reference_fields: list[str] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    normalized_structural_rows = [list(row) for row in (structural_rows or []) if isinstance(row, list)]
    normalized_reference_rows = [list(row) for row in (reference_rows or []) if isinstance(row, list)]
    if not lines or not normalized_structural_rows or not normalized_reference_rows:
        return None, None

    resolved_structural_fields = [
        str(field or "").strip()
        for field in (structural_fields or [])
        if str(field or "").strip()
    ]
    resolved_reference_fields = [
        str(field or "").strip()
        for field in ((reference_fields or structural_fields) or [])
        if str(field or "").strip()
    ]
    if not resolved_structural_fields:
        return None, None

    block_anchor_hints = _build_reparse_block_anchor_hints(
        structural_fields=resolved_structural_fields,
        structural_rows=normalized_structural_rows,
        first_pass_fields=resolved_reference_fields or resolved_structural_fields,
        first_pass_rows=normalized_reference_rows,
    )
    blank_anchor_row_indexes = sorted(
        {
            int(item)
            for item in (block_anchor_hints.get("unmatched_structural_row_indexes") or [])
            if isinstance(item, int) or (isinstance(item, str) and item.isdigit())
        }
    )
    if not blank_anchor_row_indexes:
        blank_anchor_row_indexes = sorted(
            {
                int(item)
                for item in (block_anchor_hints.get("structural_blank_anchor_row_indexes") or [])
                if isinstance(item, int) or (isinstance(item, str) and item.isdigit())
            }
        )
    if not blank_anchor_row_indexes:
        return None, None

    filled_source_rows = _collect_filled_source_rows_from_lines(lines)
    offending_rows = sorted(set(filled_source_rows) & set(blank_anchor_row_indexes))
    if not offending_rows:
        return None, None

    offending_blocks: list[dict[str, Any]] = []
    for block in (block_anchor_hints.get("blocks") or []):
        if not isinstance(block, dict):
            continue
        try:
            row_start = int(block.get("row_start"))
            row_end = int(block.get("row_end"))
        except Exception:
            continue
        block_offending = [idx for idx in offending_rows if row_start <= idx <= row_end]
        if not block_offending:
            continue
        offending_blocks.append(
            {
                "date_mmdd": block.get("date_mmdd"),
                "daypart": block.get("daypart"),
                "row_start": row_start,
                "row_end": row_end,
                "offending_source_rows": block_offending,
            }
        )

    return "sheet_blank_anchor_drift", {
        "quality_issue": "blank_anchor_drift",
        "offending_source_rows": offending_rows[:80],
        "offending_rows": offending_rows[:80],
        "blank_anchor_row_indexes": blank_anchor_row_indexes[:160],
        "filled_source_rows": filled_source_rows[:160],
        "offending_blocks": offending_blocks[:40],
    }


def _realign_quantity_only_rows_to_structural_blank_anchors(
    *,
    rows: list[list[str]],
    template: dict[str, Any],
    structural_fields: list[str] | None,
    structural_rows: list[list[str]] | None,
    reference_rows: list[list[str]] | None,
    reference_fields: list[str] | None = None,
) -> tuple[list[list[str]], dict[str, Any] | None]:
    normalized_rows = [list(row) for row in (rows or []) if isinstance(row, list)]
    normalized_structural_rows = [list(row) for row in (structural_rows or []) if isinstance(row, list)]
    normalized_reference_rows = [list(row) for row in (reference_rows or []) if isinstance(row, list)]
    if not normalized_rows or not normalized_structural_rows or not normalized_reference_rows:
        return normalized_rows, None

    quantity_indexes = _template_quantity_column_indexes(template)
    if not quantity_indexes:
        return normalized_rows, None

    resolved_structural_fields = [
        str(field or "").strip()
        for field in (structural_fields or [])
        if str(field or "").strip()
    ]
    resolved_reference_fields = [
        str(field or "").strip()
        for field in ((reference_fields or structural_fields) or [])
        if str(field or "").strip()
    ]
    if not resolved_structural_fields:
        return normalized_rows, None

    block_anchor_hints = _build_reparse_block_anchor_hints(
        structural_fields=resolved_structural_fields,
        structural_rows=normalized_structural_rows,
        first_pass_fields=resolved_reference_fields or resolved_structural_fields,
        first_pass_rows=normalized_reference_rows,
    )
    unmatched_structural_row_indexes = {
        int(item)
        for item in (block_anchor_hints.get("unmatched_structural_row_indexes") or [])
        if isinstance(item, int) or (isinstance(item, str) and item.isdigit())
    }
    used_structural_blank_fallback = not bool(unmatched_structural_row_indexes)
    if not unmatched_structural_row_indexes:
        unmatched_structural_row_indexes = {
            int(item)
            for item in (block_anchor_hints.get("structural_blank_anchor_row_indexes") or [])
            if isinstance(item, int) or (isinstance(item, str) and item.isdigit())
        }
    blocks = block_anchor_hints.get("blocks") or []
    if not unmatched_structural_row_indexes or not isinstance(blocks, list):
        return normalized_rows, None

    realigned_rows = [list(row) for row in normalized_rows]
    stats: dict[str, Any] = {
        "blocks_evaluated": 0,
        "blocks_realigned": 0,
        "rows_shifted": 0,
        "quantity_cells_shifted": 0,
        "skipped_blocks": [],
    }

    def _row_quantity_vector(row: list[str]) -> dict[int, str]:
        vector: dict[int, str] = {}
        for col_index in quantity_indexes:
            if col_index < 0 or col_index >= len(row):
                continue
            qty = _parse_strict_numeric_cell(row[col_index])
            if qty is None:
                continue
            vector[col_index] = _format_merged_quantity_cell(qty)
        return vector

    def _clear_row_quantities(row: list[str]) -> int:
        cleared_cells = 0
        for col_index in quantity_indexes:
            if col_index < 0 or col_index >= len(row):
                continue
            if row[col_index]:
                row[col_index] = ""
                cleared_cells += 1
        return cleared_cells

    for block in blocks:
        if not isinstance(block, dict):
            continue
        try:
            row_start = int(block.get("row_start"))
            row_end = int(block.get("row_end"))
        except Exception:
            continue
        if row_start < 0 or row_end < row_start:
            continue
        if row_end >= len(realigned_rows):
            continue
        block_indexes = list(range(row_start, row_end + 1))
        blank_anchor_rows = [
            idx for idx in block_indexes if idx in unmatched_structural_row_indexes
        ]
        if not blank_anchor_rows:
            continue
        target_indexes = [idx for idx in block_indexes if idx not in unmatched_structural_row_indexes]
        if not target_indexes:
            continue

        clear_made = False
        for row_index in blank_anchor_rows:
            if row_index < 0 or row_index >= len(realigned_rows):
                continue
            row = realigned_rows[row_index]
            clear_made = bool(clear_made or any(row[col_index] for col_index in quantity_indexes if 0 <= col_index < len(row)))

        candidate_vectors: list[tuple[int, dict[int, str]]] = []
        candidate_pattern: list[bool] = []
        for row_index in block_indexes:
            vector = _row_quantity_vector(realigned_rows[row_index])
            if 0 <= row_index < len(realigned_rows):
                candidate_pattern.append(bool(vector))
            if vector:
                candidate_vectors.append((row_index, vector))
        structural_pattern = [row_index in target_indexes for row_index in block_indexes]
        stats["blocks_evaluated"] = int(stats.get("blocks_evaluated") or 0) + 1
        if candidate_pattern == structural_pattern:
            if not clear_made:
                continue
            cleared_cells = 0
            for row_index in blank_anchor_rows:
                if 0 <= row_index < len(realigned_rows):
                    cleared_cells += _clear_row_quantities(realigned_rows[row_index])
            stats["blocks_realigned"] = int(stats.get("blocks_realigned") or 0) + 1
            stats["rows_shifted"] = int(stats.get("rows_shifted") or 0) + len(blank_anchor_rows)
            stats["quantity_cells_shifted"] = int(stats.get("quantity_cells_shifted") or 0) + cleared_cells
            continue
        if len(candidate_vectors) != len(target_indexes):
            if (
                used_structural_blank_fallback
                and clear_made
                and len(candidate_vectors) > 1
                and len(candidate_vectors) >= len(target_indexes)
            ):
                cleared_cells = 0
                for row_index in blank_anchor_rows:
                    if 0 <= row_index < len(realigned_rows):
                        cleared_cells += _clear_row_quantities(realigned_rows[row_index])
                stats["blocks_realigned"] = int(stats.get("blocks_realigned") or 0) + 1
                stats["rows_shifted"] = int(stats.get("rows_shifted") or 0) + len(blank_anchor_rows)
                stats["quantity_cells_shifted"] = int(stats.get("quantity_cells_shifted") or 0) + cleared_cells
                continue
            skipped = stats.setdefault("skipped_blocks", [])
            if isinstance(skipped, list):
                skipped.append(
                    {
                        "date_mmdd": block.get("date_mmdd"),
                        "daypart": block.get("daypart"),
                        "row_start": row_start,
                        "row_end": row_end,
                        "reason": "filled_count_mismatch",
                        "filled_rows": len(candidate_vectors),
                        "target_rows": len(target_indexes),
                    }
                )
            continue

        for row_index in block_indexes:
            target_row = realigned_rows[row_index]
            for col_index in quantity_indexes:
                if 0 <= col_index < len(target_row):
                    target_row[col_index] = ""

        block_shifted_rows = 0
        block_shifted_cells = 0
        for (source_row_index, quantity_vector), target_row_index in zip(candidate_vectors, target_indexes):
            target_row = realigned_rows[target_row_index]
            for col_index, value in quantity_vector.items():
                while len(target_row) <= col_index:
                    target_row.append("")
                target_row[col_index] = value
                block_shifted_cells += 1
            if source_row_index != target_row_index:
                block_shifted_rows += 1
        if block_shifted_cells <= 0:
            continue
        stats["blocks_realigned"] = int(stats.get("blocks_realigned") or 0) + 1
        stats["rows_shifted"] = int(stats.get("rows_shifted") or 0) + block_shifted_rows
        stats["quantity_cells_shifted"] = int(stats.get("quantity_cells_shifted") or 0) + block_shifted_cells

    if int(stats.get("blocks_realigned") or 0) <= 0:
        return normalized_rows, None
    skipped_blocks = stats.get("skipped_blocks")
    if isinstance(skipped_blocks, list):
        stats["skipped_blocks"] = skipped_blocks[:20]
    return realigned_rows, stats


def _project_quantity_only_rows_onto_structural_rows(
    *,
    rows: list[list[str]],
    template: dict[str, Any],
    structural_fields: list[str] | None,
    structural_rows: list[list[str]] | None,
) -> tuple[list[list[str]], dict[str, Any] | None]:
    normalized_rows = [list(row) for row in (rows or []) if isinstance(row, list)]
    normalized_structural_rows = [list(row) for row in (structural_rows or []) if isinstance(row, list)]
    resolved_fields = [str(field or "").strip() for field in (structural_fields or []) if str(field or "").strip()]
    if not normalized_rows or not normalized_structural_rows or not resolved_fields:
        return normalized_rows, None
    if not _should_project_quantity_rows_to_structural_rows(
        rows=normalized_rows,
        structural_rows=normalized_structural_rows,
        template=template,
    ):
        return normalized_rows, None

    quantity_indexes = _template_quantity_column_indexes(template)
    if not quantity_indexes:
        return normalized_rows, None

    target_width = max(
        len(resolved_fields),
        max((len(row) for row in normalized_structural_rows), default=0),
        max((len(row) for row in normalized_rows), default=0),
    )
    projected_rows: list[list[str]] = []
    rows_with_projected_quantity = 0
    quantity_cells_copied = 0
    padded_blank_rows = 0

    for row_index, structural_row in enumerate(normalized_structural_rows):
        target_row = list(structural_row)
        if len(target_row) < target_width:
            target_row.extend([""] * (target_width - len(target_row)))
        source_row = normalized_rows[row_index] if row_index < len(normalized_rows) else []
        if len(source_row) < target_width:
            source_row = list(source_row) + [""] * (target_width - len(source_row))
        copied_in_row = False
        for col_index in quantity_indexes:
            if col_index < 0 or col_index >= target_width:
                continue
            value = str(source_row[col_index] or "").strip()
            if target_row[col_index] != value:
                target_row[col_index] = value
            if value:
                copied_in_row = True
                quantity_cells_copied += 1
        if copied_in_row:
            rows_with_projected_quantity += 1
        elif row_index >= len(normalized_rows):
            padded_blank_rows += 1
        projected_rows.append(target_row)

    return projected_rows, {
        "projected_row_count": len(projected_rows),
        "source_row_count": len(normalized_rows),
        "structural_row_count": len(normalized_structural_rows),
        "rows_with_projected_quantity": rows_with_projected_quantity,
        "quantity_cells_copied": quantity_cells_copied,
        "padded_blank_rows": padded_blank_rows,
    }


def _rows_for_reparse_quality_gate(
    *,
    original_rows: list[list[str]] | None,
    projected_rows: list[list[str]] | None,
    llm_quantity_only_active: bool,
) -> list[list[str]]:
    if llm_quantity_only_active:
        return [list(row) for row in (original_rows or []) if isinstance(row, list)]
    return [list(row) for row in (projected_rows or original_rows or []) if isinstance(row, list)]


def _validate_reparse_date_anchor_stability(
    *,
    previous_lines: list[dict[str, Any]] | None,
    candidate_lines: list[dict[str, Any]] | None,
) -> tuple[str | None, dict[str, Any] | None]:
    previous_dates = sorted(_collect_line_dates_for_position_scope(previous_lines))
    candidate_dates = sorted(_collect_line_dates_for_position_scope(candidate_lines))
    if len(previous_dates) < 2 or len(candidate_dates) < 2:
        return None, None

    previous_set = set(previous_dates)
    candidate_set = set(candidate_dates)
    overlap_dates = sorted(previous_set & candidate_set)
    overlap_prev_ratio = len(overlap_dates) / len(previous_set) if previous_set else 0.0
    overlap_candidate_ratio = len(overlap_dates) / len(candidate_set) if candidate_set else 0.0

    previous_start = previous_dates[0]
    previous_end = previous_dates[-1]
    candidate_start = candidate_dates[0]
    candidate_end = candidate_dates[-1]
    start_shift_days = abs((candidate_start - previous_start).days)
    end_shift_days = abs((candidate_end - previous_end).days)

    severe_shift = start_shift_days > 1 or end_shift_days > 1
    low_overlap = overlap_prev_ratio < 0.5 and overlap_candidate_ratio < 0.5
    if severe_shift and low_overlap:
        return "sheet_date_anchor_drift", {
            "previous_dates": [item.isoformat() for item in previous_dates],
            "candidate_dates": [item.isoformat() for item in candidate_dates],
            "overlap_dates": [item.isoformat() for item in overlap_dates],
            "overlap_prev_ratio": round(overlap_prev_ratio, 4),
            "overlap_candidate_ratio": round(overlap_candidate_ratio, 4),
            "start_shift_days": start_shift_days,
            "end_shift_days": end_shift_days,
        }

    return None, None


def _parse_date_value(value):
    if value is None:
        return None
    if isinstance(value, date):
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        return value
    try:
        parsed = pd.to_datetime(value)
        if pd.isna(parsed):
            return None
        parsed_date = parsed.date()
        if pd.isna(parsed_date):
            return None
        return parsed_date
    except Exception:
        return None


def _normalize_received_at(value):
    if hasattr(value, "isoformat"):
        return value
    try:
        return pd.to_datetime(value)
    except Exception:
        return pd.Timestamp.utcnow()


def create_order_from_ingest(
    payload: IngestEmailPayload,
    lines: Optional[list[dict]] = None,
    ocr_attempts: int = 1,
    document_status: str = "success",
    error_message: Optional[str] = None,
):
    with session_scope() as session:
        received_at = _normalize_received_at(payload.received_at)
        doc_id = _make_document_id()
        document = OrderDocument(
            id=doc_id,
            facility_code=payload.facility_hint,
            week_code=payload.week_hint,
            storage_uri=payload.pdf_uri,
            source_email_id=payload.message_id,
            received_at=received_at,
            ocr_attempts=ocr_attempts,
            status=document_status,
            error_message=error_message,
        )
        session.add(document)
        existing = None
        if payload.facility_hint and payload.week_hint:
            existing = session.execute(
                select(Order).where(
                    Order.facility_code == payload.facility_hint,
                    Order.week_code == payload.week_hint,
                )
            ).scalars().first()
        if existing:
            order = existing
            if order.current_document_id:
                prior = order.superseded_document_ids or []
                order.superseded_document_ids = prior + [order.current_document_id]
            order.document_uri = payload.pdf_uri
            order.message_id = payload.message_id
            order.received_at = received_at
            order.status = "要確認"
            order.current_document_id = doc_id
            logger.info("Order superseded and updated", order_id=order.id)
        else:
            order = Order(
                id=_make_order_id(),
                facility_code=payload.facility_hint,
                week_code=payload.week_hint,
                document_uri=payload.pdf_uri,
                message_id=payload.message_id,
                received_at=received_at,
                status="要確認",
                current_document_id=doc_id,
            )
            session.add(order)
            logger.info("Order created", order_id=order.id)
        session.flush()
        if lines is not None:
            policy = config_service.load_ingest_policy()
            min_ratio = float(policy.get("menu_match_min_ratio", 0.72))
            week_id = payload.week_hint
            if not week_id:
                line_dates = [line.get("date") for line in lines if line.get("date")]
                week_id = month_id_from_dates(line_dates, received_at, policy)
            position_entries = _build_position_entries_for_lines(
                week_id=week_id,
                lines=lines,
                facility_id=payload.facility_hint,
            )
            lines, mapped_rows = _apply_menu_position_mapping_safe(
                lines,
                week_id,
                facility_id=payload.facility_hint,
                entries_override=position_entries if position_entries else None,
            )
            if mapped_rows <= 0:
                lines = _apply_menu_matching(lines, week_id, payload.facility_hint, min_ratio)
            lines = _ensure_unique_line_ids(lines)
            session.execute(delete(OrderLine).where(OrderLine.order_id == order.id))
            for line in lines:
                session.add(
                    OrderLine(
                        id=line.get("id") or _make_line_id(),
                        order_id=order.id,
                        line_id=line.get("line_id"),
                        date=_parse_date_value(line.get("date")),
                        daypart=line.get("daypart"),
                        menu_name=line.get("menu_name"),
                        diet_type=line.get("diet_type"),
                        area_id=line.get("area_id"),
                        bag_type=line.get("bag_type"),
                        quantity_original=line.get("quantity_original"),
                        quantity_corrected=line.get("quantity_corrected"),
                        change_note=line.get("change_note"),
                    )
                )
            order.lines_updated_at = datetime.utcnow()
        session.refresh(order)
        serialized = serialize_order(order)
    _invalidate_orders_cache()
    try:
        workflow_state_service.refresh_workflow_state(serialized["id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow state refresh failed after ingest create", order_id=serialized.get("id"), error=str(exc))
    return serialized


_orders_cache_lock = threading.Lock()
_orders_cache: dict[str, tuple[float, list[dict]]] = {}


def _invalidate_orders_cache() -> None:
    with _orders_cache_lock:
        _orders_cache.clear()


def _fetch_orders(status: Optional[str]) -> list[dict]:
    with session_scope() as session:
        query = select(Order)
        if status:
            query = query.where(Order.status == status)
        orders = session.execute(query).scalars().all()
        payloads = [serialize_order_summary(o) for o in orders]
        payloads.sort(
            key=lambda item: (
                item.get("received_at") or datetime.min,
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        return payloads


def list_orders(status: Optional[str] = None):
    key = status or "__all__"
    now = time.time()
    cached: list[dict] | None = None
    cached_at = 0.0
    with _orders_cache_lock:
        entry = _orders_cache.get(key)
        if entry:
            cached_at, cached = entry
    if cached is not None and now - cached_at < 30:
        return cached
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_fetch_orders, status)
        try:
            orders = future.result(timeout=5)
        except TimeoutError:
            return cached or []
        except Exception:
            raise
    with _orders_cache_lock:
        _orders_cache[key] = (time.time(), orders)
    return orders


def refresh_orders_cache(status: Optional[str] = None) -> int:
    orders = _fetch_orders(status)
    key = status or "__all__"
    with _orders_cache_lock:
        _orders_cache[key] = (time.time(), orders)
    return len(orders)


def list_orders_by_line_date(
    target_date: date,
    facility_id: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    with session_scope() as session:
        query = (
            select(Order, func.count(OrderLine.id).label("line_count"))
            .join(OrderLine, Order.id == OrderLine.order_id)
            .where(OrderLine.date == target_date)
            .group_by(Order.id)
        )
        if facility_id:
            query = query.where(Order.facility_code == facility_id)
        if status:
            query = query.where(Order.status == status)
        rows = session.execute(query).all()
        result: list[dict] = []
        for order, line_count in rows:
            payload = serialize_order_summary(order)
            payload["line_count"] = int(line_count or 0)
            result.append(payload)
        result.sort(key=lambda item: item.get("received_at") or "")
        return result


def update_lines(
    order_id: str,
    lines: list,
    *,
    expected_lines_updated_at: str | None = None,
    enforce_conflict_guard: bool = False,
) -> bool | tuple[bool, str | None]:
    event_context: dict[str, Any] | None = None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return (False, "order_not_found") if enforce_conflict_guard else False
        if enforce_conflict_guard:
            conflict = _lines_timestamp_conflict_detail(
                current_lines_updated_at=order.lines_updated_at,
                expected_lines_updated_at=expected_lines_updated_at,
            )
            if conflict is not None:
                return False, conflict["error"]
        normalized_lines = _ensure_unique_line_ids(lines)
        # wipe existing
        session.execute(delete(OrderLine).where(OrderLine.order_id == order_id))
        for line in normalized_lines:
            session.add(
                OrderLine(
                    id=line.get("id") or _make_line_id(),
                    order_id=order_id,
                    line_id=line.get("line_id"),
                    date=_parse_date_value(line.get("date")),
                    daypart=line.get("daypart"),
                    menu_name=line.get("menu_name"),
                    diet_type=line.get("diet_type"),
                    area_id=line.get("area_id"),
                    bag_type=line.get("bag_type"),
                    quantity_original=line.get("quantity_original"),
                    quantity_corrected=line.get("quantity_corrected"),
                    change_note=line.get("change_note"),
                )
            )
        order.lines_updated_at = datetime.utcnow()
        logger.info("Order lines updated", order_id=order_id)
        event_context = {
            "fac": order.facility_code,
            "wek": order.week_code,
            "line_count": len(normalized_lines),
        }
    if event_context:
        record_event(
            "order_lines_update",
            actor="system",
            target=order_id,
            fac=event_context.get("fac"),
            wek=event_context.get("wek"),
            metadata={"line_count": event_context.get("line_count", 0)},
        )
    _invalidate_orders_cache()
    try:
        workflow_state_service.refresh_workflow_state(order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow state refresh failed after lines update", order_id=order_id, error=str(exc))
    return (True, None) if enforce_conflict_guard else True


def _is_blank_menu_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _build_menu_snapshot(order: Order) -> dict:
    return _build_menu_snapshot_from_lines(
        facility_code=order.facility_code,
        week_code=order.week_code,
        lines=[
            {
                "menu_name": line.menu_name,
            }
            for line in (order.lines or [])
        ],
    )


def _build_menu_snapshot_from_lines(
    *,
    facility_code: str | None,
    week_code: str | None,
    lines: list[dict[str, Any]] | None,
) -> dict:
    names = sorted(
        {
            str(line.get("menu_name") or "").strip()
            for line in (lines or [])
            if isinstance(line, dict) and str(line.get("menu_name") or "").strip()
        }
    )
    items: list[dict] = []
    order_month_id = _to_sheet_month_id(week_code)
    if order_month_id:
        items = menu_service.get_menu_items_for_facility(order_month_id, facility_code)
    item_map = {item.get("name"): item for item in items if item.get("name")}
    defaults = menu_service.resolve_menu_defaults(names, facility_code)
    snapshot_items: dict[str, dict] = {}
    for name in names:
        item = item_map.get(name, {})
        fallback = defaults.get(name, {})
        payload: dict[str, object] = {}
        for field in (
            "unit_type",
            "qty_per_serving",
            "temp_type",
            "daypart",
            "category",
            "bag_max_qty",
            "bag_max_unit",
            "condiments",
        ):
            value = item.get(field)
            if _is_blank_menu_value(value):
                value = fallback.get(field)
            if value is not None:
                payload[field] = value
        snapshot_items[name] = payload
    return {
        "version": 1,
        "generated_at": datetime.utcnow().isoformat(),
        "menu_items": snapshot_items,
    }


def get_order_menu_snapshot(order_id: str) -> dict | None:
    with session_scope() as session:
        snapshot = (
            session.execute(
                select(OrderMenuSnapshot).where(OrderMenuSnapshot.order_id == order_id)
            )
            .scalars()
            .first()
        )
        if not snapshot:
            return None
        return snapshot.snapshot_json


def get_latest_confirmed_snapshot(order_id: str) -> dict[str, Any] | None:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    with session_scope() as session:
        snapshot = (
            session.query(OrderConfirmedSnapshot)
            .filter(OrderConfirmedSnapshot.order_id == normalized_order_id)
            .order_by(OrderConfirmedSnapshot.confirmed_at.desc(), OrderConfirmedSnapshot.id.desc())
            .first()
        )
        if not snapshot:
            return None
        return {
            "id": snapshot.id,
            "order_id": snapshot.order_id,
            "draft_id": snapshot.draft_id,
            "snapshot_digest": snapshot.snapshot_digest,
            "snapshot_json": snapshot.snapshot_json if isinstance(snapshot.snapshot_json, dict) else {},
            "confirmed_by": snapshot.confirmed_by,
            "confirmed_at": snapshot.confirmed_at.isoformat() if isinstance(snapshot.confirmed_at, datetime) else None,
            "created_at": snapshot.created_at.isoformat() if isinstance(snapshot.created_at, datetime) else None,
        }


def _register_training_sample_after_confirm(order_id: str) -> None:
    try:
        from src.services import ocr_training_dataset_service

        _, error = ocr_training_dataset_service.register_order_sample(
            order_id,
            source="confirm",
            note="registered on order confirm",
        )
        if error:
            logger.warning(
                "OCR training sample registration skipped",
                order_id=order_id,
                error=error,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "OCR training sample registration failed",
            order_id=order_id,
            error=str(exc),
        )


def _serialize_snapshot_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        line_date = line.get("date")
        serialized.append(
            {
                "id": str(line.get("id") or "").strip() or None,
                "line_id": str(line.get("line_id") or "").strip() or None,
                "date": line_date.isoformat() if isinstance(line_date, date) else None,
                "daypart": line.get("daypart"),
                "menu_name": line.get("menu_name"),
                "diet_type": line.get("diet_type"),
                "area_id": line.get("area_id"),
                "bag_type": line.get("bag_type"),
                "quantity_original": line.get("quantity_original"),
                "quantity_corrected": line.get("quantity_corrected"),
                "change_note": line.get("change_note"),
            }
        )
    return serialized


class ConfirmMaterializationError(Exception):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = str(code or "").strip() or "confirm_materialization_failed"
        self.message = str(message or "").strip() or self.code


def _build_materialization_candidate_from_draft_record(
    order_id: str,
    *,
    draft_record: dict[str, Any] | None,
    facility_id: str | None,
    existing_week_code: str | None,
    received_at: datetime | None,
) -> dict[str, Any] | None:
    if not isinstance(draft_record, dict):
        return {
            "source": "draft_sheet",
            "draft_id": None,
            "error": "draft_missing",
            "line_count": 0,
            "lines": [],
        }
    draft_sheet = draft_record.get("draft_sheet_json")
    if not isinstance(draft_sheet, dict):
        return None
    rows_payload = draft_sheet.get("rows")
    if not isinstance(rows_payload, list) or not rows_payload:
        return {
            "source": "draft_sheet",
            "draft_id": draft_record.get("id"),
            "error": "draft_rows_empty",
            "line_count": 0,
            "lines": [],
        }
    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility_config = None
    if facility_id:
        try:
            facility_config = config_service.get_facility_config(facility_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Facility config lookup failed during materialization candidate build",
                facility_id=facility_id,
                error=str(exc),
            )
        if not facility_config:
            facility_config = next(
                (
                    fac
                    for fac in master.get("facilities", [])
                    if fac.get("facility_id") == facility_id
                ),
                None,
            )
    if not facility_config:
        return {
            "source": "draft_sheet",
            "draft_id": draft_record.get("id"),
            "error": "facility_not_found",
            "line_count": 0,
            "lines": [],
        }
    template = facility_config.get("fax_template") or config_service._merge_template(
        base_template,
        facility_config.get("fax_template_override"),
    )
    parsed_rows = _normalize_structured_rows(
        header=draft_sheet.get("header"),
        rows_payload=rows_payload,
        template=template,
    )
    if not parsed_rows:
        return {
            "source": "draft_sheet",
            "draft_id": draft_record.get("id"),
            "error": "draft_rows_unparseable",
            "line_count": 0,
            "lines": [],
        }
    policy = config_service.load_ingest_policy()
    effective_received_at = received_at or datetime.utcnow()
    candidate_lines = parse_order_lines(
        parsed_rows,
        template,
        effective_received_at,
        policy.get("quantity_rules", {}),
    )
    if not candidate_lines:
        return {
            "source": "draft_sheet",
            "draft_id": draft_record.get("id"),
            "error": "draft_lines_empty",
            "line_count": 0,
            "lines": [],
        }

    facility_week_hint = None
    global_week_hint = None
    if facility_id:
        with session_scope() as session:
            facility_week_hint = (
                session.execute(
                    select(Order.week_code)
                    .where(Order.facility_code == facility_id, Order.week_code.is_not(None))
                    .order_by(Order.received_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            global_week_hint = (
                session.execute(
                    select(Order.week_code)
                    .where(Order.week_code.is_not(None))
                    .order_by(Order.received_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )

    week_resolution_lines: list[dict[str, Any]] = []
    for line in candidate_lines:
        if not isinstance(line, dict):
            continue
        normalized = dict(line)
        normalized["date"] = _parse_date_value(normalized.get("date"))
        week_resolution_lines.append(normalized)
    line_dates = [
        line.get("date")
        for line in week_resolution_lines
        if isinstance(line.get("date"), date)
    ]
    derived_week_id = (
        month_id_from_dates(line_dates, effective_received_at, policy) if line_dates else None
    )
    ocr_payload_for_week: dict[str, Any] | None = None
    payload_for_week, _ = get_ocr_output(order_id, persist_cache=False)
    if isinstance(payload_for_week, dict):
        ocr_payload_for_week = payload_for_week
    min_ratio = float(policy.get("menu_match_min_ratio", 0.72))
    week_id = _resolve_sheet_week_id(
        current_week_id=existing_week_code,
        received_at=effective_received_at,
        order_lines=week_resolution_lines,
        ocr_payload=ocr_payload_for_week,
        facility_id=facility_id,
        week_hints=[hint for hint in [facility_week_hint, global_week_hint] if hint],
    )
    if not week_id:
        week_id = _prefer_existing_week_when_derived_missing_menu(
            derived_week_id=derived_week_id,
            existing_week_code=existing_week_code,
            facility_id=facility_id,
        )
    payload_dates_for_position = _collect_sheet_dates_from_rows(
        parsed_rows,
        received_at=effective_received_at,
    )
    if isinstance(ocr_payload_for_week, dict):
        payload_dates_for_position |= {
            item
            for item in _collect_sheet_dates_from_payload(ocr_payload_for_week, effective_received_at)
            if isinstance(item, date)
        }
    position_entries_for_apply = _build_position_entries_for_lines(
        week_id=week_id,
        lines=week_resolution_lines,
        payload_dates=payload_dates_for_position,
    )
    enable_position_mapping = bool(template.get("map_menu_by_position", True))
    mapped_rows = 0
    if enable_position_mapping:
        candidate_lines, mapped_rows = _apply_menu_position_mapping_safe(
            candidate_lines,
            week_id,
            facility_id=facility_id,
            entries_override=position_entries_for_apply if position_entries_for_apply else None,
        )
    if mapped_rows <= 0:
        candidate_lines = _apply_menu_matching(candidate_lines, week_id, facility_id, min_ratio)

    serialized_lines = _serialize_snapshot_lines(candidate_lines)
    return {
        "source": "draft_sheet",
        "draft_id": draft_record.get("id"),
        "draft_state": draft_record.get("draft_state"),
        "line_count": len(serialized_lines),
        "lines": serialized_lines,
        "derived_week_code": week_id or derived_week_id or existing_week_code,
        "error": None if serialized_lines else "draft_lines_empty",
    }


def _build_confirm_materialization_candidate_from_draft(
    order_id: str,
    *,
    facility_id: str | None,
    existing_week_code: str | None,
    received_at: datetime | None,
) -> dict[str, Any] | None:
    latest_draft = get_latest_sheet_draft(
        order_id,
        backfill_from_revision=True,
        upgrade_generic_from_sheet=True,
    )
    if latest_draft is None:
        initial_draft = build_initial_sheet_draft(order_id)
        if isinstance(initial_draft, dict):
            latest_draft = persist_sheet_draft(
                order_id=order_id,
                draft_sheet_json=initial_draft,
                draft_state="draft_ready",
                blockers=[],
                warnings=[],
                edited_by="confirm-initial-draft",
            )
    return _build_materialization_candidate_from_draft_record(
        order_id,
        draft_record=latest_draft,
        facility_id=facility_id,
        existing_week_code=existing_week_code,
        received_at=received_at,
    )


def build_confirm_materialization_candidate(order_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None
        return _build_confirm_materialization_candidate_from_draft(
            order_id,
            facility_id=order.facility_code,
            existing_week_code=order.week_code,
            received_at=order.received_at or datetime.utcnow(),
        )


def _persist_confirmed_snapshot(
    order_id: str,
    *,
    confirmed_by: str | None = None,
    materialization_candidate: dict[str, Any] | None = None,
    materialized_lines: list[dict[str, Any]] | None = None,
) -> str | None:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None
        resolved_candidate = materialization_candidate or _build_confirm_materialization_candidate_from_draft(
            order_id,
            facility_id=order.facility_code,
            existing_week_code=order.week_code,
            received_at=order.received_at or datetime.utcnow(),
        )
        snapshot_lines = (
            _serialize_snapshot_lines(materialized_lines)
            if isinstance(materialized_lines, list)
            else _serialize_snapshot_lines(
                [
                    dict(
                        id=line.id,
                        line_id=line.line_id,
                        date=line.date,
                        daypart=line.daypart,
                        menu_name=line.menu_name,
                        diet_type=line.diet_type,
                        area_id=line.area_id,
                        bag_type=line.bag_type,
                        quantity_original=line.quantity_original,
                        quantity_corrected=line.quantity_corrected,
                        change_note=line.change_note,
                    )
                    for line in (order.lines or [])
                ]
            )
        )
        snapshot_json = {
            "order_id": order.id,
            "facility": order.facility_code,
            "week": order.week_code,
            "status": order.status,
            "lines_updated_at": order.lines_updated_at.isoformat() if isinstance(order.lines_updated_at, datetime) else None,
            "lines": snapshot_lines,
            "materialization_candidate": resolved_candidate,
        }
        snapshot_digest = hashlib.sha256(json.dumps(snapshot_json, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        snapshot = OrderConfirmedSnapshot(
            id=f"OCS{uuid4().hex[:12]}",
            order_id=order.id,
            draft_id=(get_latest_sheet_draft(order_id, backfill_from_revision=False) or {}).get("id"),
            snapshot_digest=snapshot_digest,
            snapshot_json=snapshot_json,
            confirmed_by=str(confirmed_by or "").strip() or None,
        )
        session.add(snapshot)
        session.flush()
        return snapshot.id


def _materialize_confirmed_lines_from_candidate(
    session,
    order: Order,
    candidate: dict[str, Any] | None,
) -> bool:
    if not isinstance(candidate, dict):
        raise ConfirmMaterializationError(
            "draft_missing",
            "latest draft is required before confirm",
        )
    candidate_error = str(candidate.get("error") or "").strip()
    if candidate_error:
        raise ConfirmMaterializationError(
            candidate_error,
            f"latest draft cannot be confirmed: {candidate_error}",
        )
    serialized_lines = candidate.get("lines")
    if not isinstance(serialized_lines, list) or not serialized_lines:
        raise ConfirmMaterializationError(
            "draft_lines_empty",
            "latest draft does not contain any materializable lines",
        )

    materialized_lines: list[dict[str, Any]] = []
    for line in serialized_lines:
        if not isinstance(line, dict):
            continue
        materialized_lines.append(
            {
                "id": str(line.get("id") or "").strip() or None,
                "line_id": str(line.get("line_id") or "").strip() or None,
                "date": _parse_date_value(line.get("date")),
                "daypart": line.get("daypart"),
                "menu_name": line.get("menu_name"),
                "diet_type": line.get("diet_type"),
                "area_id": line.get("area_id"),
                "bag_type": line.get("bag_type"),
                "quantity_original": line.get("quantity_original"),
                "quantity_corrected": line.get("quantity_corrected"),
                "change_note": line.get("change_note"),
            }
        )
    if not materialized_lines:
        raise ConfirmMaterializationError(
            "draft_lines_empty",
            "latest draft does not contain any materializable lines",
        )

    normalized_lines = _ensure_unique_line_ids(materialized_lines, exclude_order_id=order.id)
    session.execute(delete(OrderLine).where(OrderLine.order_id == order.id))
    for line in normalized_lines:
        session.add(
            OrderLine(
                id=line.get("id") or _make_line_id(),
                order_id=order.id,
                line_id=line.get("line_id"),
                date=_parse_date_value(line.get("date")),
                daypart=line.get("daypart"),
                menu_name=line.get("menu_name"),
                diet_type=line.get("diet_type"),
                area_id=line.get("area_id"),
                bag_type=line.get("bag_type"),
                quantity_original=line.get("quantity_original"),
                quantity_corrected=line.get("quantity_corrected"),
                change_note=line.get("change_note"),
            )
        )
    derived_week_code = str(candidate.get("derived_week_code") or "").strip()
    if derived_week_code:
        order.week_code = derived_week_code
    order.lines_updated_at = datetime.utcnow()
    return True


def _serialize_order_lines_for_digest(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": (
                line.get("date").isoformat()
                if isinstance(line.get("date"), date)
                else line.get("date")
            ),
            "daypart": line.get("daypart"),
            "menu_name": line.get("menu_name"),
            "diet_type": line.get("diet_type"),
            "area_id": line.get("area_id"),
            "bag_type": line.get("bag_type"),
            "quantity_original": line.get("quantity_original"),
            "quantity_corrected": line.get("quantity_corrected"),
            "change_note": line.get("change_note"),
        }
        for line in lines
        if isinstance(line, dict)
    ]


def apply_latest_draft(
    order_id: str,
    *,
    draft_record: dict[str, Any] | None = None,
    source: str = "draft_sheet",
    expected_lines_updated_at: str | None = None,
    enforce_lines_guard: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    serialized_order: dict[str, Any] | None = None
    materialization_candidate: dict[str, Any] | None = None
    before_count = 0
    after_count = 0
    before_digest = ""
    after_digest = ""
    facility_code: str | None = None
    week_code: str | None = None

    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        if enforce_lines_guard:
            lines_conflict = _lines_timestamp_conflict_detail(
                current_lines_updated_at=order.lines_updated_at,
                expected_lines_updated_at=expected_lines_updated_at,
            )
            if lines_conflict is not None:
                return None, lines_conflict["error"]
        existing_lines = session.execute(
            select(OrderLine).where(OrderLine.order_id == order_id)
        ).scalars().all()
        before_count = len(existing_lines)
        before_digest = _line_digest(
            [
                {
                    "date": line.date.isoformat() if line.date else None,
                    "daypart": line.daypart,
                    "menu_name": line.menu_name,
                    "diet_type": line.diet_type,
                    "area_id": line.area_id,
                    "bag_type": line.bag_type,
                    "quantity_original": line.quantity_original,
                    "quantity_corrected": line.quantity_corrected,
                    "change_note": line.change_note,
                }
                for line in existing_lines
            ]
        )
        materialization_candidate = (
            _build_materialization_candidate_from_draft_record(
                order_id,
                draft_record=draft_record,
                facility_id=order.facility_code,
                existing_week_code=order.week_code,
                received_at=order.received_at or datetime.utcnow(),
            )
            if isinstance(draft_record, dict)
            else _build_confirm_materialization_candidate_from_draft(
                order_id,
                facility_id=order.facility_code,
                existing_week_code=order.week_code,
                received_at=order.received_at or datetime.utcnow(),
            )
        )
        try:
            _materialize_confirmed_lines_from_candidate(session, order, materialization_candidate)
        except ConfirmMaterializationError as exc:
            return None, exc.code
        session.flush()
        session.expire(order, ["lines"])
        materialized_lines = [
            {
                "id": line.id,
                "line_id": line.line_id,
                "date": line.date,
                "daypart": line.daypart,
                "menu_name": line.menu_name,
                "diet_type": line.diet_type,
                "area_id": line.area_id,
                "bag_type": line.bag_type,
                "quantity_original": line.quantity_original,
                "quantity_corrected": line.quantity_corrected,
                "change_note": line.change_note,
            }
            for line in (order.lines or [])
        ]
        after_count = len(materialized_lines)
        after_digest = _line_digest(_serialize_order_lines_for_digest(materialized_lines))
        session.refresh(order)
        facility_code = order.facility_code
        week_code = order.week_code
        serialized_order = serialize_order(order)

    record_event(
        "order_reparse",
        actor="system",
        target=order_id,
        fac=facility_code,
        wek=week_code,
        metadata={"line_count": after_count, "source": source, "mode": "apply_latest_draft"},
    )
    update_job(
        f"OCR-{order_id}",
        status="done",
        error_message=None,
        metrics={
            "source": source,
            "materialized_source": "draft_sheet",
            "draft_id": (materialization_candidate or {}).get("draft_id"),
        },
    )
    if isinstance(serialized_order, dict):
        serialized_order["reparse"] = {
            "before_count": before_count,
            "after_count": after_count,
            "before_digest": before_digest,
            "after_digest": after_digest,
            "provider": source,
            "materialized_source": "draft_sheet",
            "changed": before_digest != after_digest,
        }
        serialized_order["ocr_job_id"] = f"OCR-{order_id}"
    try:
        workflow_state_service.refresh_workflow_state(order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow state refresh failed after draft apply", order_id=order_id, error=str(exc))
    return serialized_order, None


def confirm_order(order_id: str):
    serialized_order: dict | None = None
    materialization_candidate: dict[str, Any] | None = None
    materialized_lines_payload: list[dict[str, Any]] | None = None
    confirmed_facility: str | None = None
    confirmed_week: str | None = None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None
        materialization_candidate = _build_confirm_materialization_candidate_from_draft(
            order_id,
            facility_id=order.facility_code,
            existing_week_code=order.week_code,
            received_at=order.received_at or datetime.utcnow(),
        )
        _materialize_confirmed_lines_from_candidate(session, order, materialization_candidate)
        session.flush()
        session.expire(order, ["lines"])
        materialized_lines_payload = _serialize_snapshot_lines(
            [
                dict(
                    id=line.id,
                    line_id=line.line_id,
                    date=line.date,
                    daypart=line.daypart,
                    menu_name=line.menu_name,
                    diet_type=line.diet_type,
                    area_id=line.area_id,
                    bag_type=line.bag_type,
                    quantity_original=line.quantity_original,
                    quantity_corrected=line.quantity_corrected,
                    change_note=line.change_note,
                )
                for line in (order.lines or [])
            ]
        )
        snapshot_payload = (
            _build_menu_snapshot_from_lines(
                facility_code=order.facility_code,
                week_code=order.week_code,
                lines=materialized_lines_payload,
            )
            if materialized_lines_payload
            else _build_menu_snapshot(order)
        )
        existing_snapshot = (
            session.execute(
                select(OrderMenuSnapshot).where(OrderMenuSnapshot.order_id == order_id)
            )
            .scalars()
            .first()
        )
        if existing_snapshot:
            existing_snapshot.snapshot_json = snapshot_payload
        else:
            session.add(
                OrderMenuSnapshot(
                    id=f"OMS{uuid4().hex[:8]}",
                    order_id=order_id,
                    snapshot_json=snapshot_payload,
                )
            )
        order.status = "確定"
        session.flush()
        session.refresh(order)
        confirmed_facility = order.facility_code
        confirmed_week = order.week_code
        serialized_order = serialize_order(order)
    logger.info("Order confirmed", order_id=order_id)
    record_event(
        "order_confirm",
        actor="system",
        target=order_id,
        fac=confirmed_facility,
        wek=confirmed_week,
    )
    _persist_confirmed_snapshot(
        order_id,
        confirmed_by="system",
        materialization_candidate=materialization_candidate,
        materialized_lines=materialized_lines_payload,
    )
    _invalidate_orders_cache()
    try:
        workflow_state_service.refresh_workflow_state(order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow state refresh failed after confirm", order_id=order_id, error=str(exc))
    _register_training_sample_after_confirm(order_id)
    return serialized_order


def get_order_by_id(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None
        payload = serialize_order(order)
    raw_week_value = payload.get("week_value")
    raw_week_month = payload.get("week")
    payload["persisted_week_value"] = raw_week_value
    if (
        isinstance(raw_week_value, str)
        and isinstance(raw_week_month, str)
        and raw_week_value == raw_week_month
    ):
        options, error = get_order_week_options(order_id)
        if not error and isinstance(options, list):
            selected_option = next((item for item in options if item.get("selected")), None)
            if isinstance(selected_option, dict):
                selected_week_value = selected_option.get("week_id")
                selected_week_label = selected_option.get("label")
                if isinstance(selected_week_value, str) and "@" in selected_week_value:
                    payload["week_value"] = selected_week_value
                    payload["week_label"] = (
                        selected_week_label
                        if isinstance(selected_week_label, str) and selected_week_label.strip()
                        else _format_sheet_week_label(selected_week_value)
                    )
    return payload


def get_order_week_options(order_id: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        received_at = order.received_at or datetime.utcnow()
        facility_id = (order.facility_code or "").strip() or None
        current_week_value = _normalize_sheet_week_value(order.week_code) or _to_sheet_month_id(order.week_code)
        current_week_id = _to_sheet_month_id(order.week_code)
        line_dates = [
            line.date
            for line in session.execute(select(OrderLine).where(OrderLine.order_id == order_id)).scalars().all()
            if isinstance(line.date, date)
        ]

    inferred_date_from: date | None = None
    inferred_date_to: date | None = None
    if line_dates:
        inferred_date_from = min(line_dates)
        inferred_date_to = max(line_dates)
    else:
        cached_payload = _load_order_ocr_cache(order_id)
        if isinstance(cached_payload, dict):
            payload_dates = _collect_sheet_dates_from_payload(cached_payload, received_at)
            if payload_dates:
                inferred_date_from = min(payload_dates)
                inferred_date_to = max(payload_dates)

    candidate_months: list[str] = []

    def _append(value: object) -> None:
        month_id = _to_sheet_month_id(value)
        if month_id and month_id not in candidate_months:
            candidate_months.append(month_id)

    base_month = received_at.strftime("%Y-%m")
    today_month = datetime.utcnow().strftime("%Y-%m")
    _append(current_week_id)
    _append(base_month)
    _append(today_month)
    for anchor_month in [base_month, today_month]:
        for delta in (-1, 1, -2, 2, -3, 3):
            _append(_shift_sheet_month_id(anchor_month, delta))

    options: list[dict[str, Any]] = []
    for month_id in candidate_months:
        menu = menu_service.get_menu_for_facility(month_id, facility_id)
        if not isinstance(menu, dict):
            menu = {}
        entries = menu.get("entries") if isinstance(menu, dict) else None
        grouped_ranges: list[tuple[date, date]] = []
        if isinstance(entries, list) and entries:
            menu_dates: list[date] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                raw_date = entry.get("menu_date")
                if not isinstance(raw_date, str) or not raw_date.strip():
                    continue
                try:
                    parsed = date.fromisoformat(raw_date.strip())
                except Exception:
                    continue
                menu_dates.append(parsed)
            unique_dates = sorted(set(menu_dates))
            if unique_dates:
                grouped_dates: list[list[date]] = []
                current_group: list[date] = []
                previous_date: date | None = None
                for menu_date in unique_dates:
                    if (
                        previous_date is None
                        or (menu_date - previous_date).days > 1
                        or len(current_group) >= 7
                    ):
                        if current_group:
                            grouped_dates.append(current_group)
                        current_group = [menu_date]
                    else:
                        current_group.append(menu_date)
                    previous_date = menu_date
                if current_group:
                    grouped_dates.append(current_group)
                grouped_ranges = [
                    (min(date_group), max(date_group))
                    for date_group in grouped_dates
                    if date_group
                ]

        if not grouped_ranges:
            grouped_ranges = _calendar_week_ranges_for_month(month_id)

        for start_date, end_date in grouped_ranges:
            week_value = _format_sheet_week_value(month_id, start_date, end_date) or month_id
            selected = week_value == current_week_value
            if (
                not selected
                and current_week_value == current_week_id
                and current_week_id == month_id
                and isinstance(inferred_date_from, date)
                and isinstance(inferred_date_to, date)
                and start_date <= inferred_date_from <= end_date
                and start_date <= inferred_date_to <= end_date
            ):
                selected = True
            options.append(
                {
                    "week_id": week_value,
                    "label": f"{month_id} ({start_date.strftime('%m/%d')}-{end_date.strftime('%m/%d')})",
                    "date_from": start_date.isoformat(),
                    "date_to": end_date.isoformat(),
                    "selected": selected,
                }
            )
    return options, None


def _normalize_bag_summary_date(value: object) -> str:
    normalized = _normalize_entry_date(value)
    if isinstance(normalized, date):
        return normalized.isoformat()
    return str(value or "").strip()


def _bag_summary_group_key(
    *,
    date_value: object,
    daypart: object,
    menu_name: object,
    diet_type: object = None,
    area_id: object = None,
    bag_type: object = None,
) -> tuple[str, ...]:
    date_key = _normalize_bag_summary_date(date_value)
    daypart_key = _normalize_daypart_key(daypart)
    menu_key = _normalize_sheet_text(menu_name)
    bag_type_key = _normalize_sheet_text(bag_type).lower()
    if bag_type_key == "condiment":
        return ("condiment", date_key, daypart_key, menu_key)
    diet_key = _normalize_sheet_diet(diet_type) or _normalize_sheet_text(diet_type).lower()
    area_key = _normalize_sheet_area(area_id) or "X"
    return ("meal", date_key, daypart_key, menu_key, diet_key, area_key)


def _bag_summary_expected_totals(order_payload: dict[str, Any]) -> dict[tuple[str, ...], float]:
    from src.services.output_builder import build_order_lines_for_outputs

    totals: dict[tuple[str, ...], float] = {}
    order_lines = build_order_lines_for_outputs(order_payload)
    for line in order_lines:
        quantity_raw = (
            line.get("quantity_corrected")
            if line.get("quantity_corrected") is not None
            else line.get("quantity_original")
        )
        try:
            quantity = float(quantity_raw)
        except (TypeError, ValueError):
            continue
        if quantity <= 0:
            continue
        key = _bag_summary_group_key(
            date_value=line.get("date"),
            daypart=line.get("daypart"),
            menu_name=line.get("menu_name"),
            diet_type=line.get("diet_type"),
            area_id=line.get("area_id"),
            bag_type=line.get("bag_type"),
        )
        totals[key] = round(totals.get(key, 0.0) + quantity, 4)
    return totals


def _bag_summary_materialized_totals(rows: list[dict[str, Any]]) -> dict[tuple[str, ...], float]:
    totals: dict[tuple[str, ...], float] = {}
    for row in rows:
        try:
            quantity = float(row.get("quantity"))
        except (TypeError, ValueError):
            continue
        if quantity <= 0:
            continue
        key = _bag_summary_group_key(
            date_value=row.get("date"),
            daypart=row.get("daypart"),
            menu_name=row.get("menu_name"),
            diet_type=row.get("diet_type"),
            area_id=row.get("area_id"),
            bag_type=row.get("bag_type"),
        )
        totals[key] = round(totals.get(key, 0.0) + quantity, 4)
    return totals


def _bag_summary_requires_rebuild(
    order_payload: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    if not rows:
        return True, "missing_bags"
    expected_totals = _bag_summary_expected_totals(order_payload)
    materialized_totals = _bag_summary_materialized_totals(rows)
    if expected_totals != materialized_totals:
        return True, "bag_totals_mismatch"
    return False, None


def _normalize_output_unit(value: object) -> str:
    token = _normalize_sheet_text(value).lower()
    if not token:
        return ""
    if token in {"g", "gram", "grams"}:
        return "g"
    if token in {"ml"}:
        return "ml"
    if token in {"個"}:
        return "個"
    if token in {"切"}:
        return "切"
    return str(value or "").strip()


def _format_amount_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_amount_label(totals: dict[str, float] | None) -> str | None:
    if not isinstance(totals, dict):
        return None
    entries: list[tuple[str, float]] = []
    for unit, raw_value in totals.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        entries.append((str(unit or ""), value))
    if not entries:
        return None
    entries.sort(key=lambda item: item[0])
    return " + ".join(f"{_format_amount_number(value)}{unit}" for unit, value in entries)


def _merge_amount_totals(base: dict[str, float], incoming: dict[str, float] | None) -> dict[str, float]:
    if not isinstance(incoming, dict):
        return base
    for unit, raw_value in incoming.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        base[str(unit or "")] = round(base.get(str(unit or ""), 0.0) + value, 4)
    return base


def _build_condiment_amount_key(date_value: object, daypart: object) -> str:
    return f"condiment__{_normalize_bag_summary_date(date_value)}__{_normalize_daypart_key(daypart)}"


def _build_non_condiment_amount_key(
    date_value: object,
    daypart: object,
    menu_name: object,
    diet_type: object,
    area_id: object,
) -> str:
    return "__".join(
        [
            "normal",
            _normalize_bag_summary_date(date_value),
            _normalize_daypart_key(daypart),
            _normalize_sheet_text(menu_name),
            _normalize_sheet_diet(diet_type) or _normalize_sheet_text(diet_type).lower() or "unknown",
            _normalize_sheet_area(area_id) or "X",
        ]
    )


def _build_daily_bag_amount_stats(lines: list[dict[str, Any]]) -> dict[str, Any]:
    condiment_totals: dict[str, dict[str, float]] = {}
    non_condiment_stats: dict[str, dict[str, dict[str, float]]] = {}
    for line in lines:
        quantity_raw = (
            line.get("quantity_corrected")
            if line.get("quantity_corrected") is not None
            else line.get("quantity_original")
        )
        try:
            quantity = float(quantity_raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(quantity) or quantity <= 0:
            continue
        unit = _normalize_output_unit(line.get("actual_unit_type") or line.get("menu_unit_type"))
        if not unit:
            continue
        amount_raw = line.get("actual_amount")
        if amount_raw is None:
            per_serving = line.get("menu_qty_per_serving")
            try:
                amount_raw = float(per_serving) * quantity
            except (TypeError, ValueError):
                continue
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(amount) or amount < 0:
            continue
        if _normalize_sheet_text(line.get("bag_type")).lower() == "condiment":
            key = _build_condiment_amount_key(line.get("date"), line.get("daypart"))
            totals = condiment_totals.get(key, {})
            totals[unit] = round(totals.get(unit, 0.0) + amount, 4)
            condiment_totals[key] = totals
            continue
        key = _build_non_condiment_amount_key(
            line.get("date"),
            line.get("daypart"),
            line.get("menu_name"),
            line.get("diet_type"),
            line.get("area_id"),
        )
        unit_stats = non_condiment_stats.get(key, {})
        current = unit_stats.get(unit, {"amount": 0.0, "quantity": 0.0})
        current["amount"] = round(current.get("amount", 0.0) + amount, 4)
        current["quantity"] = round(current.get("quantity", 0.0) + quantity, 4)
        unit_stats[unit] = current
        non_condiment_stats[key] = unit_stats
    per_serving_by_group: dict[str, dict[str, float]] = {}
    for key, unit_stats in non_condiment_stats.items():
        per_serving: dict[str, float] = {}
        for unit, stat in unit_stats.items():
            try:
                amount = float(stat.get("amount", 0.0))
                quantity = float(stat.get("quantity", 0.0))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(amount) or not math.isfinite(quantity) or quantity <= 0:
                continue
            per_serving[unit] = round(amount / quantity, 6)
        if per_serving:
            per_serving_by_group[key] = per_serving
    return {"condiment_totals": condiment_totals, "per_serving_by_group": per_serving_by_group}


def _resolve_daily_bag_amount_totals(bag: dict[str, Any], stats: dict[str, Any]) -> dict[str, float] | None:
    bag_type = _normalize_sheet_text(bag.get("bag_type")).lower()
    if bag_type == "condiment":
        return stats.get("condiment_totals", {}).get(
            _build_condiment_amount_key(bag.get("date"), bag.get("daypart"))
        )
    try:
        quantity = float(bag.get("quantity"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(quantity) or quantity < 0:
        return None
    per_serving = stats.get("per_serving_by_group", {}).get(
        _build_non_condiment_amount_key(
            bag.get("date"),
            bag.get("daypart"),
            bag.get("menu_name"),
            bag.get("diet_type"),
            bag.get("area_id"),
        )
    )
    if not isinstance(per_serving, dict):
        return None
    totals: dict[str, float] = {}
    for unit, raw_value in per_serving.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        totals[unit] = round(value * quantity, 4)
    return totals or None


def get_daily_bag_summary(
    target_date: date,
    facility_id: Optional[str] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    orders = list_orders_by_line_date(target_date, facility_id=facility_id, status=status)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    facility_labels: dict[str, str] = {}
    from src.services.output_builder import build_order_lines_for_outputs

    for order_summary in orders:
        order_id = str(order_summary.get("id") or "").strip()
        if not order_id:
            continue
        order_payload = get_order_by_id(order_id)
        if not isinstance(order_payload, dict):
            continue
        order_lines = build_order_lines_for_outputs(order_payload)
        amount_stats = _build_daily_bag_amount_stats(order_lines)
        bag_summary, error = get_bag_summary(order_id)
        if error or not isinstance(bag_summary, dict):
            continue
        facility_code = str(order_payload.get("facility") or "").strip()
        facility_label = facility_labels.get(facility_code)
        if facility_label is None:
            facility_name = ""
            if facility_code:
                try:
                    facility_config = config_service.get_facility_config(facility_code) or {}
                    facility_name = str(facility_config.get("facility_name") or "").strip()
                except Exception:
                    facility_name = ""
            facility_label = f"{facility_name} ({facility_code})" if facility_name and facility_code else facility_code or "未確定"
            facility_labels[facility_code] = facility_label

        for bag in bag_summary.get("bags") or []:
            if _normalize_bag_summary_date(bag.get("date")) != target_date.isoformat():
                continue
            group_daypart = str(bag.get("daypart") or _normalize_daypart_key(bag.get("daypart")) or "-").strip() or "-"
            menu_name = str(bag.get("menu_name") or "-").strip() or "-"
            menu_key = (group_daypart, menu_name)
            menu_group = groups.get(menu_key)
            if menu_group is None:
                menu_group = {
                    "daypart": group_daypart,
                    "daypart_key": _normalize_daypart_key(group_daypart),
                    "menu_name": menu_name,
                    "diet_groups": {},
                }
                groups[menu_key] = menu_group

            diet_key = _normalize_sheet_diet(bag.get("diet_type")) or _normalize_sheet_text(bag.get("diet_type")).lower() or "unknown"
            diet_group = menu_group["diet_groups"].get(diet_key)
            if diet_group is None:
                diet_group = {
                    "diet_type": diet_key,
                    "total_quantity": 0.0,
                    "total_amounts": {},
                    "bag_type_groups": {},
                }
                menu_group["diet_groups"][diet_key] = diet_group

            try:
                quantity = float(bag.get("quantity"))
            except (TypeError, ValueError):
                quantity = 0.0
            if math.isfinite(quantity):
                diet_group["total_quantity"] = round(diet_group["total_quantity"] + quantity, 4)

            amount_totals = _resolve_daily_bag_amount_totals(bag, amount_stats)
            _merge_amount_totals(diet_group["total_amounts"], amount_totals)

            bag_type_key = _normalize_sheet_text(bag.get("bag_type")).lower() or "standard"
            bag_type_group = diet_group["bag_type_groups"].get(bag_type_key)
            if bag_type_group is None:
                bag_type_group = {
                    "bag_type": bag_type_key,
                    "bag_count": 0,
                    "total_quantity": 0.0,
                    "total_amounts": {},
                    "breakdowns": {},
                }
                diet_group["bag_type_groups"][bag_type_key] = bag_type_group
            bag_type_group["bag_count"] += 1
            if math.isfinite(quantity):
                bag_type_group["total_quantity"] = round(bag_type_group["total_quantity"] + quantity, 4)
            _merge_amount_totals(bag_type_group["total_amounts"], amount_totals)

            amount_label = _format_amount_label(amount_totals) or "計算不可"
            breakdown = bag_type_group["breakdowns"].get(amount_label)
            if breakdown is None:
                breakdown = {
                    "amount_label": amount_label,
                    "count": 0,
                    "order_refs": [],
                }
                bag_type_group["breakdowns"][amount_label] = breakdown
            breakdown["count"] += 1
            breakdown["order_refs"].append(
                {
                    "order_id": order_id,
                    "facility_label": facility_label,
                    "area_id": bag.get("area_id"),
                    "quantity": quantity if math.isfinite(quantity) else None,
                }
            )

    preferred_diet_order = [
        "regular",
        "regular_bag",
        "soft",
        "mixer",
        "daycare",
        "staff",
        "tea",
        "business",
        "diabetes",
        "pregnancy",
        "sesame_allergy",
        "no_meat",
        "no_fish",
        "change_1",
        "change_2",
        "placeholder",
        "unknown",
    ]
    diet_sort_index = {value: idx for idx, value in enumerate(preferred_diet_order)}
    bag_sort_order = {"large": 0, "medium": 1, "small": 2, "standard": 3, "condiment": 4}

    normalized_groups: list[dict[str, Any]] = []
    for menu_group in groups.values():
        diet_groups: list[dict[str, Any]] = []
        for diet_group in menu_group["diet_groups"].values():
            bag_type_groups: list[dict[str, Any]] = []
            for bag_type_group in diet_group["bag_type_groups"].values():
                breakdowns = list(bag_type_group["breakdowns"].values())
                breakdowns.sort(key=lambda item: (item.get("amount_label") == "計算不可", item.get("amount_label") or ""))
                bag_type_groups.append(
                    {
                        "bag_type": bag_type_group["bag_type"],
                        "bag_count": bag_type_group["bag_count"],
                        "total_quantity": bag_type_group["total_quantity"],
                        "total_amounts": bag_type_group["total_amounts"],
                        "total_amount_label": _format_amount_label(bag_type_group["total_amounts"]),
                        "breakdowns": breakdowns,
                    }
                )
            bag_type_groups.sort(
                key=lambda item: (
                    bag_sort_order.get(str(item.get("bag_type") or ""), 50),
                    str(item.get("bag_type") or ""),
                )
            )
            diet_groups.append(
                {
                    "diet_type": diet_group["diet_type"],
                    "total_quantity": diet_group["total_quantity"],
                    "total_amounts": diet_group["total_amounts"],
                    "total_amount_label": _format_amount_label(diet_group["total_amounts"]),
                    "bag_type_groups": bag_type_groups,
                }
            )
        diet_groups.sort(
            key=lambda item: (
                diet_sort_index.get(str(item.get("diet_type") or ""), 99),
                str(item.get("diet_type") or ""),
            )
        )
        normalized_groups.append(
            {
                "daypart": menu_group["daypart"],
                "daypart_key": menu_group["daypart_key"],
                "menu_name": menu_group["menu_name"],
                "diet_groups": diet_groups,
            }
        )
    normalized_groups.sort(
        key=lambda item: (
            _daypart_sort_components(item.get("daypart"))[0],
            _daypart_sort_components(item.get("daypart"))[1],
            str(item.get("menu_name") or ""),
        )
    )
    return {
        "date": target_date.isoformat(),
        "status": status,
        "facility_id": facility_id,
        "order_count": len(orders),
        "groups": normalized_groups,
    }


def get_bag_summary(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        bags = (
            session.execute(select(Bag).where(Bag.order_id == order_id))
            .scalars()
            .all()
        )
        payload = [
            {
                "id": bag.id,
                "date": bag.date.isoformat() if bag.date else None,
                "daypart": bag.daypart,
                "menu_name": bag.menu_name,
                "diet_type": bag.diet_type,
                "area_id": bag.area_id,
                "bag_type": bag.bag_type,
                "quantity": bag.quantity,
            }
            for bag in bags
        ]
    should_rebuild = False
    rebuild_reason: str | None = None
    order_payload = get_order_by_id(order_id)
    if isinstance(order_payload, dict):
        should_rebuild, rebuild_reason = _bag_summary_requires_rebuild(order_payload, payload)
    elif not payload:
        should_rebuild = True
        rebuild_reason = "missing_bags"
    if should_rebuild:
        # Bag rows are materialized during output generation/rebuild.
        # Auto-rebuild so operators always see bag rows that match current order lines.
        try:
            from src.services.output_builder import rebuild_bags

            rebuilt = rebuild_bags(order_id)
            payload = rebuilt.get("bags") if isinstance(rebuilt, dict) else payload
            logger.info(
                "Bag summary auto rebuilt",
                order_id=order_id,
                reason=rebuild_reason,
                bag_count=len(payload),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Bag auto rebuild failed",
                order_id=order_id,
                reason=rebuild_reason,
                error=str(exc),
            )
    payload.sort(
        key=lambda row: (
            row.get("date") or "",
            row.get("daypart") or "",
            row.get("menu_name") or "",
            row.get("diet_type") or "",
            row.get("area_id") or "",
            row.get("bag_type") or "",
        )
    )
    return {"order_id": order_id, "generated": bool(payload), "bags": payload}, None


def _load_cached_ocr(message_id: Optional[str]) -> Optional[dict]:
    if not message_id:
        return None
    candidates = []
    dump_dir = os.getenv("OCR_DEBUG_DUMP_DIR")
    if dump_dir:
        candidates.append(Path(dump_dir))
    candidates.append(Path(__file__).resolve().parents[1] / "tmp")
    for base in candidates:
        path = base / f"ocr_{message_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return None
    return None


def _load_pipeline_raw_text(order_id: str, message_id: Optional[str]) -> Optional[str]:
    job = get_ocr_job(f"OCR-{order_id}")
    if not job and message_id:
        job = get_ocr_job(f"OCR-{message_id}")
    if not job:
        return None
    output_ref = job.get("output_reference")
    if not output_ref:
        return None
    try:
        payload = load_bytes_from_uri(output_ref)
        parsed = json.loads(payload.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    table_raw = parsed.get("table_raw")
    if isinstance(table_raw, str) and table_raw.strip():
        return table_raw
    try:
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except TypeError:
        return None


def _save_order_ocr_cache(order_id: str, payload: dict) -> None:
    try:
        with session_scope() as session:
            cache = session.get(OrderOcrCache, order_id)
            if not cache:
                cache = OrderOcrCache(order_id=order_id)
                session.add(cache)
            next_payload = dict(payload) if isinstance(payload, dict) else {}
            existing_payload = cache.payload if isinstance(cache.payload, dict) else {}
            if (
                isinstance(existing_payload, dict)
                and _payload_has_page_artifacts(existing_payload)
                and not _payload_has_page_artifacts(next_payload)
            ):
                for preserved_key in ("pages", "combined", "engine", "template_id", "facility_id"):
                    preserved_value = existing_payload.get(preserved_key)
                    if preserved_key not in next_payload and preserved_value is not None:
                        next_payload[preserved_key] = preserved_value
            for preserved_key in ("_edited_ocr", "_reparse_debug"):
                preserved_value = existing_payload.get(preserved_key)
                if isinstance(preserved_value, dict) and preserved_key not in next_payload:
                    next_payload[preserved_key] = preserved_value
            cache.payload = next_payload
            cache.updated_at = datetime.utcnow()
        try:
            persist_ocr_evidence_run(
                order_id,
                next_payload,
                schema_version="v1_legacy",
                producer_version="legacy-cache-mirror/v1",
                status=str(next_payload.get("status") or "ready").strip() or "ready",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Order OCR evidence persistence failed", order_id=order_id, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Order OCR cache save failed", order_id=order_id, error=str(exc))


def _load_order_ocr_cache(order_id: str) -> Optional[dict]:
    try:
        with session_scope() as session:
            cache = session.get(OrderOcrCache, order_id)
            if not cache:
                return None
            return cache.payload if isinstance(cache.payload, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Order OCR cache load failed", order_id=order_id, error=str(exc))
        return None


def get_cached_ocr_payload(order_id: str) -> Optional[dict]:
    return evidence_manifest_service.ensure_evidence_manifest(_load_order_ocr_cache(order_id))


def persist_ocr_evidence_run(
    order_id: str,
    payload: dict[str, Any] | None,
    *,
    schema_version: str = "v1_legacy",
    producer_version: str | None = None,
    status: str = "ready",
    source: str | None = None,
) -> Optional[dict]:
    persisted = ocr_evidence_service.persist_evidence_run(
        order_id=order_id,
        payload=payload,
        schema_version=schema_version,
        producer_version=producer_version,
        status=status,
        source=source,
    )
    if persisted is not None:
        try:
            workflow_state_service.refresh_workflow_state(order_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Workflow state refresh failed after evidence persist", order_id=order_id, error=str(exc))
    return persisted


def get_latest_ocr_evidence_run(order_id: str, *, backfill_from_cache: bool = True) -> Optional[dict]:
    latest = ocr_evidence_service.get_latest_evidence_run(order_id)
    if latest is not None or not backfill_from_cache:
        return latest
    cached_payload = _load_order_ocr_cache(order_id)
    if not isinstance(cached_payload, dict):
        return None
    return ocr_evidence_service.backfill_evidence_run_from_cached_payload(
        order_id,
        cached_payload,
        schema_version="v1_legacy_backfill",
        producer_version="legacy-cache-backfill/v1",
        source="legacy-cache-backfill",
    )


def _resolve_active_ocr_evidence_run(order_id: str) -> Optional[dict]:
    latest_evidence = get_latest_ocr_evidence_run(order_id, backfill_from_cache=True)
    latest_draft = draft_sheet_service.get_latest_sheet_draft(order_id)
    resolved = workflow_state_service._resolve_active_evidence_run(latest_evidence, latest_draft)
    return resolved if isinstance(resolved, dict) else None


def _load_active_ocr_payload(order_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    active_evidence = _resolve_active_ocr_evidence_run(order_id)
    payload = active_evidence.get("payload_json") if isinstance(active_evidence, dict) else None
    if not isinstance(payload, dict):
        return None, active_evidence
    return evidence_manifest_service.ensure_evidence_manifest(dict(payload)), active_evidence


def get_ocr_evidence_run(evidence_run_id: str) -> Optional[dict]:
    return ocr_evidence_service.get_evidence_run(evidence_run_id)


def persist_sheet_draft(
    *,
    order_id: str,
    draft_sheet_json: dict[str, Any] | None,
    draft_state: str = "draft_ready",
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    latest_patch_candidate_id: str | None = None,
    edited_by: str | None = None,
) -> Optional[dict]:
    if not isinstance(draft_sheet_json, dict):
        return None
    latest_evidence = get_latest_ocr_evidence_run(order_id, backfill_from_cache=True)
    template_resolution_id = None
    if isinstance(latest_evidence, dict):
        payload_json = latest_evidence.get("payload_json")
        if isinstance(payload_json, dict):
            resolution = payload_json.get("template_resolution")
            if isinstance(resolution, dict):
                template_resolution_id = (
                    str(resolution.get("resolved_template_id") or resolution.get("template_id") or "").strip() or None
                )
    persisted = draft_sheet_service.persist_sheet_draft(
        order_id=order_id,
        draft_sheet_json=draft_sheet_json,
        base_evidence_run_id=(latest_evidence or {}).get("id") if isinstance(latest_evidence, dict) else None,
        base_template_resolution_id=template_resolution_id,
        draft_state=draft_state,
        blockers=blockers,
        warnings=warnings,
        latest_patch_candidate_id=latest_patch_candidate_id,
        edited_by=edited_by,
    )
    if persisted is not None:
        try:
            workflow_state_service.refresh_workflow_state(order_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Workflow state refresh failed after draft persist", order_id=order_id, error=str(exc))
    return persisted


def _draft_fields_look_generic(fields: object) -> bool:
    if not isinstance(fields, list) or not fields:
        return False
    normalized = [str(field or "").strip() for field in fields if str(field or "").strip()]
    if not normalized:
        return False
    return all(re.fullmatch(r"col\d+", token) for token in normalized)


def _maybe_upgrade_generic_sheet_draft(
    order_id: str,
    draft: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(draft, dict):
        return draft
    draft_sheet_json = draft.get("draft_sheet_json")
    if not isinstance(draft_sheet_json, dict):
        return draft
    if not _draft_fields_look_generic(draft_sheet_json.get("fields")):
        return draft
    upgraded_sheet = _build_best_available_semantic_draft(order_id)
    if not isinstance(upgraded_sheet, dict):
        return draft
    upgraded = persist_sheet_draft(
        order_id=order_id,
        draft_sheet_json=upgraded_sheet,
        draft_state=str(draft.get("draft_state") or "draft_ready").strip() or "draft_ready",
        blockers=[str(item).strip() for item in (draft.get("blockers_json") or []) if str(item).strip()],
        warnings=[str(item).strip() for item in (draft.get("warnings_json") or []) if str(item).strip()],
        edited_by="semantic-sheet-upgrade",
    )
    return upgraded if isinstance(upgraded, dict) else draft


def get_latest_sheet_draft(
    order_id: str,
    *,
    backfill_from_revision: bool = True,
    upgrade_generic_from_sheet: bool = False,
) -> Optional[dict]:
    latest = draft_sheet_service.get_latest_sheet_draft(order_id)
    if latest is not None:
        if upgrade_generic_from_sheet:
            return _maybe_upgrade_generic_sheet_draft(order_id, latest)
        return latest
    if not backfill_from_revision:
        return latest
    cached_payload = _load_order_ocr_cache(order_id)
    revision = _select_order_sheet_revision(
        order_id=order_id,
        payload=cached_payload,
        exact_only=False,
    )
    if not isinstance(revision, dict):
        return None
    return persist_sheet_draft(
        order_id=order_id,
        draft_sheet_json={
            "fields": list(revision.get("fields") or []),
            "header": list(revision.get("header") or []),
            "rows": list(revision.get("rows") or []),
            "row_ids": list(revision.get("row_ids") or []),
            "ui_mode": str(revision.get("ui_mode") or "sheet").strip() or "sheet",
            "revision_id": str(revision.get("revision_id") or "").strip() or None,
        },
        draft_state=str(revision.get("review_state") or "draft_ready").strip() or "draft_ready",
        blockers=[str(item).strip() for item in (revision.get("review_blockers") or []) if str(item).strip()],
        warnings=[str(item).strip() for item in (revision.get("review_warnings") or []) if str(item).strip()],
        edited_by="legacy-revision-backfill",
    )


def _build_initial_draft_from_sheet_payload(
    order_id: str,
    sheet_payload: dict[str, Any] | None,
) -> Optional[dict[str, Any]]:
    if not isinstance(sheet_payload, dict):
        return None
    fields = [str(field).strip() for field in (sheet_payload.get("fields") or []) if str(field).strip()]
    rows = _sanitize_revision_rows(rows_payload=sheet_payload.get("rows"), fields=fields)
    if not fields or not rows:
        return None
    row_ids = [str(item).strip() for item in (sheet_payload.get("row_ids") or []) if str(item).strip()]
    if len(row_ids) < len(rows):
        row_ids.extend([f"row-{idx + 1}" for idx in range(len(row_ids), len(rows))])
    header = [str(cell or "").strip() for cell in (sheet_payload.get("header") or [])]
    if len(header) < len(fields):
        header.extend([_field_label(field) for field in fields[len(header) :]])
    source = str(sheet_payload.get("source") or "ocr_sheet").strip() or "ocr_sheet"
    warnings = [str(item).strip() for item in (sheet_payload.get("warnings") or []) if str(item).strip()]
    return {
        "order_id": order_id,
        "source": source,
        "fields": fields,
        "header": header[: len(fields)],
        "rows": rows,
        "row_ids": row_ids[: len(rows)],
        "warnings": warnings,
    }


def _build_best_available_semantic_draft(
    order_id: str,
    *,
    use_saved_draft: bool = True,
    evidence_run_override: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    sheet_payload, sheet_error = get_ocr_sheet(
        order_id,
        use_saved_draft=use_saved_draft,
        evidence_run_override=evidence_run_override,
    )
    candidates: list[dict[str, Any]] = []
    if isinstance(sheet_payload, dict):
        candidates.append(sheet_payload)
    if sheet_error in _RECOVERABLE_OCR_SHEET_ERRORS:
        recovered, recover_error = build_recoverable_ocr_sheet_payload(order_id, sheet_error)
        if recover_error is None and isinstance(recovered, dict):
            candidates.append(recovered)
    for candidate in candidates:
        draft_payload = _build_initial_draft_from_sheet_payload(order_id, candidate)
        if isinstance(draft_payload, dict) and not _draft_fields_look_generic(draft_payload.get("fields")):
            return draft_payload
    return None


def build_initial_sheet_draft(order_id: str) -> Optional[dict]:
    draft_payload = _build_best_available_semantic_draft(order_id, use_saved_draft=True)
    if isinstance(draft_payload, dict):
        return draft_payload
    return draft_sheet_service.build_initial_sheet_draft(order_id)


def _get_ocr_output_bucket() -> str | None:
    return (
        str(os.getenv("OCR_PIPELINE_BUCKET") or "").strip()
        or str(os.getenv("OCR_PIPELINE_INPUT_BUCKET") or "").strip()
        or get_default_output_bucket()
    )


def _get_ocr_output_prefix() -> str:
    prefix = str(os.getenv("OCR_PIPELINE_OUTPUT_PREFIX") or "output/").strip() or "output/"
    if not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return prefix


def _list_latest_completed_ocr_outputs(
    order_id: str,
    *,
    not_before: datetime | None = None,
    limit: int = 5,
) -> list[tuple[str, dict[str, Any]]]:
    bucket = _get_ocr_output_bucket()
    if not bucket:
        return []
    try:
        from google.cloud import storage  # type: ignore
    except Exception:
        return []

    prefix = f"{_get_ocr_output_prefix()}OCR-{str(order_id or '').strip()}_"
    client = storage.Client()
    timeout = 15.0
    candidates: list[tuple[datetime, str]] = []
    try:
        for blob in client.list_blobs(bucket, prefix=prefix, timeout=timeout):
            name = str(getattr(blob, "name", "") or "").strip()
            if not name.endswith(".pdf.json"):
                continue
            updated = getattr(blob, "updated", None)
            updated_naive: datetime | None = None
            if isinstance(updated, datetime):
                updated_naive = updated.astimezone(timezone.utc).replace(tzinfo=None) if updated.tzinfo else updated
            if isinstance(not_before, datetime) and isinstance(updated_naive, datetime) and updated_naive < not_before:
                continue
            candidates.append((updated_naive or datetime.min, name))
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR output list failed", order_id=order_id, error=str(exc))
        return []

    completed: list[tuple[str, dict[str, Any]]] = []
    for _updated, name in sorted(candidates, reverse=True)[: max(limit, 1)]:
        object_uri = f"gs://{bucket}/{name}"
        try:
            raw = client.bucket(bucket).blob(name).download_as_bytes(timeout=timeout, retry=None)
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR output read failed", order_id=order_id, uri=object_uri, error=str(exc))
            continue
        state = ocr_evidence_service.classify_evidence_payload(payload)
        status = str(payload.get("status") or "").strip().lower()
        stage = str(payload.get("stage") or "").strip().lower()
        if state.get("persistable") and (status == "done" or stage == "done"):
            completed.append((object_uri, payload))
            continue
        if status in {"failed", "error"} or stage in {"failed", "error"}:
            completed.append((object_uri, payload))
    return completed


def _reconcile_finished_ocr_rerun(order_id: str) -> bool:
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return False
    ocr_job_id = f"OCR-{normalized_order_id}"
    reparse_job = get_ocr_job(ocr_job_id)
    if not isinstance(reparse_job, dict):
        return False
    status = str(reparse_job.get("status") or "").strip().lower()
    if status not in {"running", "pending", "failed"}:
        return False
    metrics = reparse_job.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    request_mode = str(metrics.get("request_mode") or metrics.get("rerun_mode") or "").strip().lower()
    if request_mode != "ocr_rerun":
        return False

    created_at = reparse_job.get("created_at")
    if isinstance(created_at, datetime) and created_at.tzinfo is not None:
        created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    completed_outputs = _list_latest_completed_ocr_outputs(
        normalized_order_id,
        not_before=created_at if isinstance(created_at, datetime) else None,
    )
    if not completed_outputs:
        return False

    output_ref, payload = completed_outputs[0]
    payload_state = ocr_evidence_service.classify_evidence_payload(payload)
    order_payload = workflow_state_service._load_order_payload(normalized_order_id)
    logger.info(
        "Reconciling OCR rerun from completed output order_id=%s job_status=%s output_reference=%s persistable=%s",
        normalized_order_id,
        status or "-",
        output_ref,
        bool(payload_state.get("persistable")),
    )
    base_metrics_patch = {
        "request_mode": "ocr_rerun",
        "confirmed_lines_retained": bool((order_payload or {}).get("lines_updated_at")),
    }
    if not payload_state.get("persistable"):
        error_code = str(payload_state.get("error") or "ocr_rerun_invalid_output").strip() or "ocr_rerun_invalid_output"
        error_detail = str(payload_state.get("message") or "").strip()
        error_message = f"{error_code}:{error_detail}" if error_detail else error_code
        _update_reparse_job_progress(
            ocr_job_id,
            status="failed",
            processing_stage=str(payload_state.get("stage") or "ocr_pipeline").strip() or "ocr_pipeline",
            result_state="hard_failed",
            error_message=error_message,
            metrics_patch={
                **base_metrics_patch,
                "error": error_code,
                "output_reference": output_ref,
            },
        )
        return True

    persisted = ocr_evidence_service.persist_evidence_run(
        order_id=normalized_order_id,
        payload=payload,
        schema_version="v2_evidence_rerun",
        producer_version="ocr_pipeline_rerun_reconcile",
        status=str(payload.get("status") or "ready").strip() or "ready",
        source="ocr-rerun-reconcile",
    )
    if not isinstance(persisted, dict):
        _update_reparse_job_progress(
            ocr_job_id,
            status="failed",
            processing_stage="persist_evidence",
            result_state="hard_failed",
            error_message="evidence_persist_failed",
            metrics_patch={
                **base_metrics_patch,
                "error": "evidence_persist_failed",
                "output_reference": output_ref,
            },
        )
        return True

    update_job(
        ocr_job_id,
        status="done",
        template_id=payload.get("template_id"),
        output_reference=output_ref,
        input_reference=str(payload.get("input_reference") or reparse_job.get("input_reference") or "").strip() or None,
        error_message=None,
        metrics={
            **metrics,
            "request_mode": "ocr_rerun",
            "processing_stage": "evidence_ready",
            "result_state": "evidence_ready",
            "evidence_run_id": persisted.get("id"),
            "new_evidence_available": True,
            "status": "done",
            "stage_updated_at": datetime.utcnow().isoformat(),
        },
    )
    return True


def rerun_ocr_evidence_only(
    order_id: str,
    *,
    job_id: str | None = None,
) -> tuple[Optional[dict], Optional[str]]:
    order = get_order_by_id(order_id)
    if not isinstance(order, dict):
        return None, "order_not_found"
    facility_id = str(order.get("facility") or "").strip() or None
    if not facility_id:
        return None, "facility_missing"
    document_uri = str(order.get("document") or "").strip()
    if not document_uri:
        return None, "document_not_found"

    ocr_job_id = str(job_id or f"OCR-{order_id}").strip() or f"OCR-{order_id}"
    _, created = create_job(ocr_job_id, input_reference=document_uri)
    if not created:
        update_job(
            ocr_job_id,
            status="running",
            input_reference=document_uri,
            error_message=None,
        )

    try:
        pdf_bytes = load_bytes_from_uri(document_uri)
    except Exception as exc:  # noqa: BLE001
        _update_reparse_job_progress(
            ocr_job_id,
            status="failed",
            processing_stage="document_load",
            result_state="hard_failed",
            error_message=f"document_load_failed:{exc}",
            metrics_patch={
                "error": "document_load_failed",
                "request_mode": "ocr_rerun",
                "confirmed_lines_retained": bool(order.get("lines_updated_at")),
            },
        )
        try:
            workflow_state_service.refresh_workflow_state(order_id)
        except Exception as refresh_exc:  # noqa: BLE001
            logger.warning("Workflow state refresh failed after OCR rerun document load error", order_id=order_id, error=str(refresh_exc))
        return None, "document_load_failed"

    facility_config = None
    try:
        facility_config = config_service.get_facility_config(facility_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Facility config lookup failed for OCR rerun", facility_id=facility_id, error=str(exc))
    preferred_template_id, preferred_template_ids = _resolve_preferred_template_ids(facility_config)
    base_metrics_patch = {
        "request_mode": "ocr_rerun",
        "confirmed_lines_retained": bool(order.get("lines_updated_at")),
    }
    _update_reparse_job_progress(
        ocr_job_id,
        status="running",
        processing_stage="ocr_pipeline",
        result_state="processing",
        error_message=None,
        metrics_patch=base_metrics_patch,
    )
    try:
        output = _run_reparse_with_heartbeat(
            ocr_job_id,
            processing_stage="ocr_pipeline",
            result_state="processing",
            metrics_patch=base_metrics_patch,
            func=lambda: run_ocr_pipeline(
                pdf_bytes=pdf_bytes,
                job_id=ocr_job_id,
                facility_id=facility_id,
                input_reference=document_uri,
                preferred_template_id=preferred_template_id,
                preferred_template_ids=preferred_template_ids,
                force_upload=True,
                wait_for_output=True,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _update_reparse_job_progress(
            ocr_job_id,
            status="failed",
            processing_stage="ocr_pipeline",
            result_state="hard_failed",
            error_message=f"evidence_rerun_failed:{exc}",
            metrics_patch={
                **base_metrics_patch,
                "error": "evidence_rerun_failed",
            },
        )
        try:
            workflow_state_service.refresh_workflow_state(order_id)
        except Exception as refresh_exc:  # noqa: BLE001
            logger.warning("Workflow state refresh failed after OCR rerun failure", order_id=order_id, error=str(refresh_exc))
        return None, "ocr_rerun_failed"

    if not isinstance(output, dict):
        _update_reparse_job_progress(
            ocr_job_id,
            status="failed",
            processing_stage="ocr_pipeline",
            result_state="hard_failed",
            error_message="evidence_rerun_invalid_output",
            metrics_patch={
                **base_metrics_patch,
                "error": "evidence_rerun_invalid_output",
            },
        )
        try:
            workflow_state_service.refresh_workflow_state(order_id)
        except Exception as refresh_exc:  # noqa: BLE001
            logger.warning("Workflow state refresh failed after OCR rerun invalid output", order_id=order_id, error=str(refresh_exc))
        return None, "ocr_rerun_invalid_output"

    payload_state = ocr_evidence_service.classify_evidence_payload(output)
    if not payload_state.get("persistable"):
        error_code = str(payload_state.get("error") or "evidence_unusable").strip() or "evidence_unusable"
        error_detail = str(payload_state.get("message") or "").strip()
        upstream_status = str(payload_state.get("status") or "").strip()
        upstream_stage = str(payload_state.get("stage") or "").strip()
        failure_stage = upstream_stage or "ocr_pipeline"
        error_message = f"{error_code}:{error_detail}" if error_detail else error_code
        _update_reparse_job_progress(
            ocr_job_id,
            status="failed",
            processing_stage=failure_stage,
            result_state="hard_failed",
            error_message=error_message,
            metrics_patch={
                **base_metrics_patch,
                "error": error_code,
                "upstream_status": upstream_status or None,
                "upstream_stage": upstream_stage or None,
            },
        )
        try:
            workflow_state_service.refresh_workflow_state(order_id)
        except Exception as refresh_exc:  # noqa: BLE001
            logger.warning(
                "Workflow state refresh failed after OCR rerun unusable payload",
                order_id=order_id,
                error=str(refresh_exc),
            )
        return None, error_code

    persisted = persist_ocr_evidence_run(
        order_id,
        output,
        schema_version="v2_evidence_rerun",
        producer_version="ocr_pipeline_rerun",
        status=str(output.get("status") or "ready").strip() or "ready",
        source="ocr-rerun",
    )
    if not isinstance(persisted, dict):
        _update_reparse_job_progress(
            ocr_job_id,
            status="failed",
            processing_stage="persist_evidence",
            result_state="hard_failed",
            error_message="evidence_persist_failed",
            metrics_patch={
                **base_metrics_patch,
                "error": "evidence_persist_failed",
            },
        )
        try:
            workflow_state_service.refresh_workflow_state(order_id)
        except Exception as refresh_exc:  # noqa: BLE001
            logger.warning("Workflow state refresh failed after OCR rerun persist failure", order_id=order_id, error=str(refresh_exc))
        return None, "evidence_persist_failed"

    update_job(
        ocr_job_id,
        template_id=output.get("template_id"),
        output_reference=output.get("output_reference"),
        input_reference=document_uri,
    )
    _update_reparse_job_progress(
        ocr_job_id,
        status="done",
        processing_stage="evidence_ready",
        result_state="evidence_ready",
        error_message=None,
        metrics_patch={
            **base_metrics_patch,
            "evidence_run_id": persisted.get("id"),
            "new_evidence_available": True,
        },
    )
    try:
        workflow_state_service.refresh_workflow_state(order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow state refresh failed after OCR rerun success", order_id=order_id, error=str(exc))
    return persisted, None


def switch_draft_to_latest_evidence(
    order_id: str,
    *,
    edited_by: str | None = None,
) -> tuple[Optional[dict], Optional[str]]:
    latest_evidence = get_latest_ocr_evidence_run(order_id, backfill_from_cache=True)
    if not isinstance(latest_evidence, dict):
        return None, "evidence_not_found"
    latest_evidence_id = str(latest_evidence.get("id") or "").strip() or None
    if not latest_evidence_id:
        return None, "evidence_not_found"
    current_draft = get_latest_sheet_draft(order_id, backfill_from_revision=True, upgrade_generic_from_sheet=False)
    current_base_evidence_id = (
        str((current_draft or {}).get("base_evidence_run_id") or "").strip() or None
        if isinstance(current_draft, dict)
        else None
    )
    if current_base_evidence_id == latest_evidence_id:
        return current_draft, "already_current"

    draft_payload = _build_best_available_semantic_draft(
        order_id,
        use_saved_draft=False,
        evidence_run_override=latest_evidence,
    )
    if not isinstance(draft_payload, dict):
        return None, "switch_draft_unavailable"

    persisted = persist_sheet_draft(
        order_id=order_id,
        draft_sheet_json=draft_payload,
        draft_state="draft_ready",
        blockers=[],
        warnings=[str(item).strip() for item in (draft_payload.get("warnings") or []) if str(item).strip()],
        edited_by=edited_by or "switch-evidence",
    )
    if not isinstance(persisted, dict):
        return None, "switch_draft_failed"
    return persisted, None


def get_order_workflow_state(order_id: str, *, refresh: bool = False) -> Optional[dict]:
    if _reconcile_finished_ocr_rerun(order_id):
        refresh = True
    if refresh:
        return workflow_state_service.refresh_workflow_state(order_id)
    state = workflow_state_service.get_workflow_state(order_id)
    if state is not None:
        return state


def get_latest_patch_candidate(order_id: str) -> Optional[dict]:
    return patch_candidate_service.get_latest_patch_candidate(order_id)


def apply_patch_candidate_to_draft(
    order_id: str,
    *,
    candidate_id: str | None = None,
    applied_by: str | None = None,
) -> tuple[Optional[dict], Optional[str]]:
    result, error = patch_candidate_service.apply_patch_candidate_to_draft(
        order_id=order_id,
        patch_candidate_id=candidate_id,
        edited_by=applied_by,
    )
    if result is not None and error is None:
        try:
            workflow_state_service.refresh_workflow_state(order_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Workflow state refresh failed after patch candidate apply", order_id=order_id, error=str(exc))
    return result, error


def get_order_candidate_resolution(order_id: str) -> Optional[dict]:
    state = get_order_workflow_state(order_id, refresh=False)
    if isinstance(state, dict):
        resolution = state.get("candidate_resolution")
        if isinstance(resolution, dict):
            return resolution
    order = get_order_by_id(order_id)
    evidence = get_latest_ocr_evidence_run(order_id, backfill_from_cache=True)
    payload = evidence.get("payload_json") if isinstance(evidence, dict) else None
    return candidate_resolution_service.resolve_order_candidates(
        order_id=order_id,
        facility_code=str((order or {}).get("facility") or "").strip() or None,
        week_code=str((order or {}).get("week_value") or (order or {}).get("week") or "").strip() or None,
        received_at=(order or {}).get("received_at"),
        evidence_payload=payload if isinstance(payload, dict) else None,
    )


def list_order_critical_decisions(order_id: str, *, refresh_workflow: bool = False) -> list[dict[str, Any]]:
    if refresh_workflow:
        get_order_workflow_state(order_id, refresh=True)
    return critical_decision_service.list_decisions(order_id)


def _evidence_only_step2_enabled() -> bool:
    # Step2 is now permanently evidence/draft driven. Confirmed order lines are
    # no longer a valid input source for OCR correction screens.
    return True


def _get_template_resolution(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    resolution = payload.get("template_resolution")
    if not isinstance(resolution, dict):
        return None
    normalized = template_resolution_service.normalize_template_resolution_state(resolution)
    return normalized if isinstance(normalized, dict) else resolution


def _template_resolution_blockers(payload: dict[str, Any] | None) -> list[str]:
    resolution = _get_template_resolution(payload)
    if not isinstance(resolution, dict):
        return []
    blockers = resolution.get("blocked_reasons")
    if not isinstance(blockers, list):
        return []
    return [str(item).strip() for item in blockers if str(item).strip()]


def _ocr_evidence_missing_artifacts(payload: dict[str, Any] | None) -> list[str]:
    return evidence_manifest_service.evidence_missing_artifacts(payload)


def _sheet_payload_mapping_block_reason(
    *,
    source: str,
    ocr_payload: dict[str, Any] | None,
    evidence_missing: list[str] | None,
    template_blockers: list[str] | None,
) -> str | None:
    if source != "weekly_menu":
        return None
    if not isinstance(_get_template_resolution(ocr_payload), dict):
        return "unresolved_template"
    if any(str(item or "").strip() for item in (template_blockers or [])):
        return "unresolved_template"
    missing = {str(item or "").strip() for item in (evidence_missing or []) if str(item or "").strip()}
    if "template_resolution" in missing:
        return "unresolved_template"
    # Weekly-menu rescue can only be trusted when quantity-column semantics are known.
    # Full row-edge metadata is useful for overlays, but Step2 quantity mapping only needs a
    # resolved template plus stable quantity column boundaries.
    if not ocr_evidence_service.payload_has_quantity_column_semantics(ocr_payload):
        return "unresolved_template"
    if ocr_evidence_service.payload_has_high_risk_numeric_issues(ocr_payload):
        return "numeric_review_required"
    return None


_RECOVERABLE_OCR_SHEET_ERRORS = {
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
    "template_unresolved",
}


def _load_order_ocr_cache_map(order_ids: list[str]) -> dict[str, dict[str, Any]]:
    normalized_ids = [str(item).strip() for item in (order_ids or []) if str(item).strip()]
    if not normalized_ids:
        return {}
    try:
        with session_scope() as session:
            rows = (
                session.execute(
                    select(OrderOcrCache).where(OrderOcrCache.order_id.in_(normalized_ids))
                )
                .scalars()
                .all()
            )
            return {
                cache.order_id: cache.payload
                for cache in rows
                if isinstance(cache.payload, dict)
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Order OCR cache bulk load failed", order_ids=normalized_ids[:20], error=str(exc))
        return {}


def _parse_iso_datetime_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _normalize_sheet_warning_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [
        token
        for token in (
            str(item or "").strip()
            for item in values
        )
        if token
    ]


def _build_sheet_rows_from_order_lines(
    *,
    fields: list[str],
    quantity_index: dict[tuple[str, str], int],
    order_lines: list[dict[str, Any]],
) -> tuple[list[list[str]], list[str]]:
    rows: list[list[str]] = []
    row_ids: list[str] = []
    for idx, line in enumerate(order_lines):
        if not isinstance(line, dict):
            continue
        row = [""] * len(fields)
        parsed_date = _parse_date_value(line.get("date"))
        qty_value = line.get("quantity_corrected")
        if qty_value is None:
            qty_value = line.get("quantity_original")
        qty_text = _field_value_to_str(qty_value)
        quantity_col_idx = _resolve_quantity_column_index(
            quantity_index=quantity_index,
            diet_key=_normalize_sheet_diet(line.get("diet_type")),
            area_key=_normalize_sheet_area(line.get("area_id")),
        )
        for col_idx, field in enumerate(fields):
            if field == "date_mmdd":
                row[col_idx] = parsed_date.strftime("%m/%d") if isinstance(parsed_date, date) else ""
            elif field == "date":
                row[col_idx] = parsed_date.isoformat() if isinstance(parsed_date, date) else ""
            elif field == "daypart":
                row[col_idx] = _field_value_to_str(line.get("daypart"))
            elif field in {"menu", "menu_name"}:
                row[col_idx] = _field_value_to_str(line.get("menu_name"))
            elif field == "diet_type":
                row[col_idx] = _field_value_to_str(line.get("diet_type"))
            elif field == "area_id":
                row[col_idx] = _field_value_to_str(line.get("area_id"))
            elif field == "bag_type":
                row[col_idx] = _field_value_to_str(line.get("bag_type"))
            elif field in {"remarks", "note"}:
                row[col_idx] = _field_value_to_str(line.get("change_note"))
            elif quantity_col_idx is not None and col_idx == quantity_col_idx:
                row[col_idx] = qty_text
        if any(cell.strip() for cell in row):
            rows.append(row)
            row_ids.append(
                str(line.get("id") or line.get("line_id") or f"line-{idx + 1}").strip()
                or f"line-{idx + 1}"
            )
    return rows, row_ids


def _build_ocr_review_metadata(
    *,
    order_id: str | None = None,
    order_status: str | None,
    lines_updated_at: datetime | None,
    cached_payload: dict[str, Any] | None,
    sheet_payload: dict[str, Any] | None = None,
    strict_error: str | None = None,
) -> dict[str, Any]:
    if order_id:
        latest_revision = _select_order_sheet_revision(
            order_id=order_id,
            payload=cached_payload,
            exact_only=True,
        )
    else:
        latest_revision = _select_edited_sheet_revision(cached_payload, exact_only=True)
    latest_revision = latest_revision if isinstance(latest_revision, dict) else None
    latest_revision_id = (
        str(latest_revision.get("revision_id") or "").strip()
        if latest_revision
        else ""
    )
    latest_edited_at = (
        _parse_iso_datetime_value(latest_revision.get("edited_at"))
        if latest_revision
        else None
    )
    latest_sheet_source = ""
    if isinstance(sheet_payload, dict):
        latest_sheet_source = str(sheet_payload.get("source") or "").strip()
    if not latest_sheet_source and latest_revision:
        latest_sheet_source = (
            "edited_sheet_exact"
            if bool(latest_revision.get("sheet_save_only"))
            or str(latest_revision.get("sheet_save_mode") or "").strip().lower() == "exact"
            else "edited_sheet"
        )
    warnings = _normalize_sheet_warning_list(
        sheet_payload.get("warnings") if isinstance(sheet_payload, dict) else None
    )
    reparse_debug = cached_payload.get("_reparse_debug") if isinstance(cached_payload, dict) else None
    last_reparse_error = (
        str(reparse_debug.get("error") or "").strip()
        if isinstance(reparse_debug, dict)
        else ""
    )
    latest_revision_has_rows = bool(
        latest_revision
        and _is_saved_draft_revision(latest_revision)
        and isinstance(latest_revision.get("rows"), list)
    )
    draft_newer_than_lines = False
    if latest_revision_has_rows:
        if lines_updated_at is None:
            draft_newer_than_lines = True
        elif latest_edited_at and latest_edited_at > lines_updated_at:
            draft_newer_than_lines = True
    auto_apply_blocked = bool(
        latest_revision_has_rows
        and draft_newer_than_lines
        and (
            latest_revision.get("auto_apply_blocked")
            or str(latest_revision.get("reject_reason") or "").strip()
        )
    )
    draft_available = bool(latest_revision_has_rows and (draft_newer_than_lines or auto_apply_blocked))

    apply_blockers: list[str] = []
    if strict_error in {
        "facility_missing",
        "facility_not_found",
        "sheet_fields_not_found",
        "sheet_fields_duplicate",
        "sheet_template_field_invalid",
        "sheet_quantity_columns_missing",
    }:
        apply_blockers.append(str(strict_error))

    confirm_blockers: list[str] = []
    if strict_error in {
        "week_unresolved",
        "menu_entries_missing",
        "sheet_week_dates_incomplete",
        "week_menu_date_mismatch",
        "sheet_date_mismatch",
        "sheet_canonical_mismatch",
        "sheet_suspicious_blank_row",
        "sheet_quantity_column_unmapped",
    }:
        confirm_blockers.append(str(strict_error))
    if "sheet_weekly_menu_missing" in warnings and "sheet_weekly_menu_missing" not in confirm_blockers:
        confirm_blockers.append("sheet_weekly_menu_missing")
    if draft_newer_than_lines and "draft_not_applied" not in warnings:
        warnings.append("draft_not_applied")

    review_badges: list[str] = []
    if draft_available:
        review_badges.append("draft_ready")
    if auto_apply_blocked:
        review_badges.append("auto_apply_blocked")
    if "sheet_ocr_review_required" in warnings:
        review_badges.append("review_required")
    if confirm_blockers:
        review_badges.append("confirm_blocked")
    elif last_reparse_error and not draft_available:
        review_badges.append("ocr_failed")

    if draft_available and (auto_apply_blocked or draft_newer_than_lines):
        review_state = "draft_ready"
    elif confirm_blockers or "sheet_ocr_review_required" in warnings:
        review_state = "review_required"
    elif last_reparse_error and not draft_available:
        review_state = "ocr_failed"
    elif str(order_status or "").strip() == "確定":
        review_state = "confirmed"
    elif lines_updated_at:
        review_state = "lines_ready"
    else:
        review_state = "pending"

    return {
        "review_state": review_state,
        "review_badges": review_badges,
        "draft_available": draft_available,
        "draft_newer_than_lines": draft_newer_than_lines,
        "auto_apply_blocked": auto_apply_blocked,
        "latest_draft_revision_id": latest_revision_id or None if draft_available else None,
        "latest_draft_edited_at": latest_edited_at.isoformat() if latest_edited_at and draft_available else None,
        "sheet_source": latest_sheet_source or None,
        "last_reparse_error": last_reparse_error or None,
        "can_apply": len(apply_blockers) == 0,
        "apply_blockers": apply_blockers,
        "can_confirm": len(confirm_blockers) == 0,
        "confirm_blockers": confirm_blockers,
        "confirm_warnings": warnings,
    }


def _attach_review_metadata_to_order_payload(
    payload: dict[str, Any],
    *,
    cached_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    metadata = _build_ocr_review_metadata(
        order_id=str(payload.get("id") or "").strip() or None,
        order_status=str(payload.get("status") or ""),
        lines_updated_at=payload.get("lines_updated_at"),
        cached_payload=cached_payload,
    )
    enriched = dict(payload)
    enriched.update(metadata)
    return enriched


def _load_pipeline_output_with_retry(
    output_ref: str | None,
    *,
    wait_seconds_override: float | None = None,
) -> Optional[dict]:
    if not output_ref:
        return None
    if wait_seconds_override is None:
        try:
            wait_seconds = float(os.getenv("OCR_REPARSE_OUTPUT_WAIT_SECONDS", "90"))
        except ValueError:
            wait_seconds = 15.0
    else:
        wait_seconds = float(wait_seconds_override)
    try:
        poll_seconds = float(os.getenv("OCR_REPARSE_OUTPUT_POLL_SECONDS", "1.5"))
    except ValueError:
        poll_seconds = 1.5
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    last_error = None
    while True:
        try:
            payload = load_bytes_from_uri(output_ref)
            parsed = json.loads(payload.decode("utf-8"))
            if _output_is_pending(parsed):
                last_error = RuntimeError("ocr_output_pending")
                if time.monotonic() >= deadline:
                    break
                time.sleep(max(poll_seconds, 0.1))
                continue
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(max(poll_seconds, 0.1))
    logger.warning("OCR output not ready after wait", output_reference=output_ref, error=str(last_error))
    return None


def _load_existing_first_pass_payload_for_reparse(order_id: str) -> dict[str, Any] | None:
    payload, error = get_ocr_output(order_id, persist_cache=False)
    if error is None and isinstance(payload, dict) and _payload_has_first_pass_ocr_content(payload):
        return payload
    cached_payload = _load_order_ocr_cache(order_id)
    if isinstance(cached_payload, dict) and _payload_has_first_pass_ocr_content(cached_payload):
        return cached_payload
    return None


def get_ocr_raw_text(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        message_id = order.message_id
    raw_text = _load_pipeline_raw_text(order_id, message_id)
    if not raw_text:
        return None, "ocr_raw_not_found"
    return {"order_id": order_id, "raw_text": raw_text}, None


def _coerce_table_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return str(int(value))
        return str(value)
    return str(value)


def _normalize_header_token(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"\s+", "", normalized)
    translation = str.maketrans(
        {
            "０": "0",
            "１": "1",
            "２": "2",
            "３": "3",
            "４": "4",
            "５": "5",
            "６": "6",
            "７": "7",
            "８": "8",
            "９": "9",
            "ｆ": "f",
            "Ｆ": "f",
        }
    )
    return normalized.translate(translation)


def _select_field(candidates: list[str], fields: set[str]) -> str | None:
    for candidate in candidates:
        if candidate in fields:
            return candidate
    return None


def _field_from_header(header: str, fields: set[str]) -> str | None:
    token = _normalize_header_token(header)
    if "備考" in token or "remarks" in token or "note" in token:
        return _select_field(["remarks", "note"], fields)
    if "献立" in token or "メニュー" in token or "menu" in token:
        return _select_field(["menu", "menu_name"], fields)
    if "日付" in token or token.startswith("日"):
        return _select_field(["date_mmdd", "date"], fields)
    if "区分" in token or "時間帯" in token:
        return _select_field(["daypart"], fields)
    if "常食" in token or "regular" in token or "常" in token:
        if "2f" in token:
            return _select_field(["qty.regular_2f", "regular_2f", "qty.regular_x", "regular_x"], fields)
        if "3f" in token:
            return _select_field(["qty.regular_3f", "regular_3f", "qty.regular_x", "regular_x"], fields)
        return _select_field(
            [
                "qty.regular_x",
                "regular_x",
                "qty.regular_2f",
                "regular_2f",
                "qty.regular_3f",
                "regular_3f",
            ],
            fields,
        )
    if "軟菜" in token or "soft" in token or "軟" in token:
        if "2f" in token:
            return _select_field(["qty.soft_2f", "soft_2f", "qty.soft_x", "soft_x"], fields)
        if "3f" in token:
            return _select_field(["qty.soft_3f", "soft_3f", "qty.soft_x", "soft_x"], fields)
        return _select_field(
            ["qty.soft_x", "soft_x", "qty.soft_2f", "soft_2f", "qty.soft_3f", "soft_3f"],
            fields,
        )
    if "ミキサ" in token or "mixer" in token or "ミキ" in token:
        if "2f" in token:
            return _select_field(["qty.mixer_2f", "mixer_2f", "qty.mixer_x", "mixer_x"], fields)
        if "3f" in token:
            return _select_field(["qty.mixer_3f", "mixer_3f", "qty.mixer_x", "mixer_x"], fields)
        return _select_field(
            [
                "qty.mixer_x",
                "mixer_x",
                "qty.mixer_2f",
                "mixer_2f",
                "qty.mixer_3f",
                "mixer_3f",
            ],
            fields,
        )
    if "職員" in token or "staff" in token:
        return _select_field(["qty.staff_x", "staff_x"], fields)
    if "お茶" in token or "tea" in token:
        return _select_field(["qty.tea_x", "tea_x"], fields)
    if "事業" in token or "business" in token:
        return _select_field(["qty.business_x", "business_x"], fields)
    if "通所" in token or "daycare" in token:
        return _select_field(["qty.daycare_x", "daycare_x"], fields)
    if "糖尿" in token or "diabetes" in token:
        return _select_field(["qty.diabetes_x", "diabetes_x", "qty.糖尿_x", "糖尿_x"], fields)
    if "妊娠" in token or "pregnancy" in token:
        return _select_field(["qty.pregnancy_x", "pregnancy_x"], fields)
    if ("ごま" in token or "ゴマ" in header or "sesame" in token) and (
        "アレル" in header or "allergy" in token
    ):
        return _select_field(["qty.sesame_allergy_x", "sesame_allergy_x"], fields)
    if ("禁" in token and "肉" in token) or "nomeat" in token:
        return _select_field(
            ["qty.no_meat_x", "no_meat_x", "qty.no_meat_2f", "no_meat_2f", "qty.no_meat_3f", "no_meat_3f"],
            fields,
        )
    if ("禁" in token and "魚" in token) or "nofish" in token:
        return _select_field(
            ["qty.no_fish_x", "no_fish_x", "qty.no_fish_2f", "no_fish_2f", "qty.no_fish_3f", "no_fish_3f"],
            fields,
        )
    if "変更1" in header or "change1" in token:
        return _select_field(["qty.change_1_x", "change_1_x"], fields)
    if "変更2" in header or "change2" in token:
        return _select_field(["qty.change_2_x", "change_2_x"], fields)
    if token in {"-", "placeholder"}:
        return _select_field(["qty.placeholder_x", "placeholder_x"], fields)
    return None


def _get_row_fields(template: dict[str, Any]) -> list[str]:
    fields = template.get("main_ocr_row_fields")
    if not isinstance(fields, list):
        return []
    return [str(field).strip() for field in fields if str(field).strip()]


_SHEET_TRANSLATION = str.maketrans(
    {
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "ｆ": "f",
        "Ｆ": "f",
    }
)


def _normalize_sheet_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = text.translate(_SHEET_TRANSLATION)
    text = re.sub(r"[\s　]+", "", text)
    return text


def _normalize_sheet_date_key(value: object) -> str:
    text = _field_value_to_str(value).strip()
    if not text:
        return ""
    normalized = text.translate(_SHEET_TRANSLATION)
    full = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", normalized)
    if full:
        month = int(full.group(2))
        day = int(full.group(3))
        return f"{month:02d}/{day:02d}"
    mmdd = re.search(r"(\d{1,2})[/-](\d{1,2})", normalized)
    if mmdd:
        month = int(mmdd.group(1))
        day = int(mmdd.group(2))
        return f"{month:02d}/{day:02d}"
    return ""


def _normalize_sheet_diet(value: object) -> str | None:
    token = _normalize_sheet_text(value).lower()
    if not token:
        return None
    if ("袋" in token or "bag" in token) and (
        "regular" in token or "常食" in token or "通常" in token or "常" in token
    ):
        return "regular_bag"
    if "regular" in token or "常食" in token or "通常" in token:
        return "regular"
    if "soft" in token or "軟菜" in token or "やわ" in token:
        return "soft"
    if "mixer" in token or "ミキサ" in token:
        return "mixer"
    if "daycare" in token or "通所" in token:
        return "daycare"
    if "staff" in token or "職員" in token:
        return "staff"
    if "tea" in token or "お茶" in token:
        return "tea"
    if "business" in token or "事業" in token:
        return "business"
    if "diabetes" in token or "糖尿" in token:
        return "diabetes"
    if "pregnancy" in token or "妊娠" in token:
        return "pregnancy"
    if ("ごま" in token or "ゴマ" in str(value or "") or "sesame" in token) and (
        "アレル" in str(value or "") or "allergy" in token
    ):
        return "sesame_allergy"
    if "nomeat" in token or ("禁" in token and "肉" in token):
        return "no_meat"
    if "nofish" in token or ("禁" in token and "魚" in token):
        return "no_fish"
    if "change1" in token or "変更1" in str(value or ""):
        return "change_1"
    if "change2" in token or "変更2" in str(value or ""):
        return "change_2"
    if token in {"-", "placeholder"}:
        return "placeholder"
    return token


def _normalize_sheet_area(value: object) -> str | None:
    token = _normalize_sheet_text(value).lower()
    if not token:
        return "X"
    if re.fullmatch(r"\d+", token):
        return f"{token}F"
    match = re.search(r"(\d)(?:f|階)", token)
    if match:
        return f"{match.group(1)}F"
    return token.upper()


def _quantity_meta_from_field(field: str) -> tuple[str | None, str | None]:
    token = _normalize_sheet_text(field).lower()
    raw_token = token
    if token.startswith("qty."):
        token = token[4:]
    token = token.replace(".", "_")
    # Quantity columns only. Avoid treating date/daypart/menu as quantity metadata.
    if not raw_token.startswith("qty.") and not re.search(
        r"(regular|常|soft|軟|mixer|ミキ|daycare|通所|staff|職員|nomeat|nofish|禁)",
        token,
    ):
        return None, None
    area_match = re.search(r"(?:_|)(\d(?:f|階)|x)$", token)
    if not area_match:
        return None, None
    area_token = area_match.group(1)
    diet_token = token[: area_match.start(1)].rstrip("_")
    diet = _normalize_sheet_diet(diet_token or token)
    area = _normalize_sheet_area(area_token)
    if not diet or not area:
        return None, None
    return diet, area


def _field_label(field: str) -> str:
    token = _normalize_sheet_text(field).lower()
    if token in {"date_mmdd", "date"} or token.startswith("date"):
        return "日付"
    if token in {"daypart", "meal", "time"}:
        return "区分"
    if token in {"menu", "menu_name"}:
        return "メニュー"
    if token in {"remarks", "note"}:
        return "備考"
    diet, area = _quantity_meta_from_field(field)
    if diet and area:
        diet_label = {
            "regular": "常食",
            "regular_bag": "常食(袋分け)",
            "soft": "軟菜",
            "soft_mixer": "軟菜/ミキサー",
            "mixer": "ミキサー",
            "daycare": "通所",
            "staff": "職員",
            "no_meat": "禁食(肉禁)",
            "no_fish": "禁食(魚禁)",
            "change_1": "変更1",
            "change_2": "変更2",
            "unknown": "不明",
        }.get(diet, diet)
        if area == "X":
            return diet_label
        return f"{diet_label}{area}"
    return field


def _field_name_from_template_column(column: dict[str, Any]) -> str | None:
    role = str(column.get("role") or "").strip().lower()
    if role == "date":
        return "date_mmdd"
    if role == "daypart":
        return "daypart"
    if role == "menu_name":
        return "menu"
    if role == "note":
        return "remarks"
    if role == "quantity":
        diet = _normalize_sheet_diet(column.get("diet_type")) or "unknown"
        area = _normalize_sheet_area(column.get("area_id")) or "X"
        return f"qty.{diet}_{area.lower()}"
    return None


def _sheet_header_from_template(
    fields: list[str],
    template: dict[str, Any] | None = None,
) -> list[str]:
    normalized_fields = [str(field).strip() for field in (fields or []) if str(field).strip()]
    if not normalized_fields:
        return []
    columns = template.get("columns") if isinstance(template, dict) else None
    if not isinstance(columns, list):
        return [_field_label(field) for field in normalized_fields]
    ordered = sorted(
        [col for col in columns if isinstance(col, dict)],
        key=lambda col: int(col.get("index") or 0),
    )
    header_by_field: dict[str, str] = {}
    for col in ordered:
        field = _field_name_from_template_column(col)
        if not field or field in header_by_field:
            continue
        header_by_field[field] = str(col.get("header") or "").strip() or _field_label(field)
    return [header_by_field.get(field, _field_label(field)) for field in normalized_fields]


def _row_fields_from_template(template: dict[str, Any]) -> list[str]:
    fields = _get_row_fields(template)
    if fields:
        return fields
    columns = template.get("columns")
    if not isinstance(columns, list):
        return []
    ordered = sorted(
        [col for col in columns if isinstance(col, dict)],
        key=lambda col: int(col.get("index") or 0),
    )
    derived: list[str] = []
    for col in ordered:
        field = _field_name_from_template_column(col)
        if field:
            derived.append(field)
    return derived


def _field_value_to_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return str(number)
    return str(value)


def _format_mmdd(value: object) -> str:
    parsed = _normalize_entry_date(value)
    if not parsed:
        return ""
    return f"{parsed.month:02d}/{parsed.day:02d}"


def _build_markdown_table_string(header: list[str], rows: list[list[str]]) -> str:
    normalized_header = [str(cell or "").strip() for cell in (header or [])]
    normalized_rows = [[_field_value_to_str(cell) for cell in row] for row in (rows or [])]
    if not normalized_header:
        width = max((len(row) for row in normalized_rows), default=0)
        normalized_header = [f"col{idx + 1}" for idx in range(max(width, 1))]
    padded_rows: list[list[str]] = []
    for row in normalized_rows:
        current = list(row[: len(normalized_header)])
        if len(current) < len(normalized_header):
            current.extend([""] * (len(normalized_header) - len(current)))
        padded_rows.append(current)
    header_line = f"| {' | '.join(normalized_header)} |"
    separator = f"| {' | '.join(['---'] * len(normalized_header))} |"
    body = [f"| {' | '.join(row)} |" for row in padded_rows]
    return "\\n".join([header_line, separator, *body])


def _snapshot_raw_ocr_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    snapshot_keys = [
        "engine",
        "provider",
        "template_id",
        "facility_id",
        "table_raw",
        "rows",
        "pages",
        "tables",
        "cell_issues",
        "yomitoku_cell_issues",
        "roi_cell_issues",
        "roi_extraction",
        "roi_overlay_rows",
        "roi_overlay_policy",
        "warnings",
        "failed_cells",
        "combined",
        "classification",
        "classification_confidence",
        "metrics",
    ]
    snapshot: dict[str, Any] = {}
    for key in snapshot_keys:
        if key in payload:
            snapshot[key] = payload.get(key)
    return snapshot


def _sanitize_revision_rows(
    *,
    rows_payload: object,
    fields: list[str],
) -> list[list[str]]:
    return ocr_sheet_revision_service.sanitize_revision_rows(
        rows_payload=rows_payload,
        fields=fields,
        field_value_to_str=_field_value_to_str,
    )


def _normalize_sheet_revision_snapshot(
    *,
    fields: object,
    header: object,
    rows_payload: object,
    row_ids: object,
) -> dict[str, Any]:
    return ocr_sheet_revision_service.normalize_sheet_revision_snapshot(
        fields=fields,
        header=header,
        rows_payload=rows_payload,
        row_ids=row_ids,
        field_label=_field_label,
        field_value_to_str=_field_value_to_str,
    )


def _sheet_digest(
    *,
    fields: object,
    header: object,
    rows_payload: object,
    row_ids: object,
) -> str:
    return ocr_sheet_revision_service.sheet_digest(
        fields=fields,
        header=header,
        rows_payload=rows_payload,
        row_ids=row_ids,
        field_label=_field_label,
        field_value_to_str=_field_value_to_str,
    )


def _select_edited_sheet_revision(
    payload: dict[str, Any] | None,
    *,
    exact_only: bool = False,
) -> dict[str, Any] | None:
    return ocr_sheet_revision_service.select_edited_sheet_revision(
        payload,
        exact_only=exact_only,
    )


def _select_order_sheet_revision(
    *,
    order_id: str,
    payload: dict[str, Any] | None,
    exact_only: bool = False,
) -> dict[str, Any] | None:
    try:
        persisted_revision = ocr_revision_store.get_latest_revision(order_id, exact_only=exact_only)
        if isinstance(persisted_revision, dict):
            return persisted_revision
    except Exception as exc:  # noqa: BLE001
        logger.warning("Persisted OCR revision lookup failed", order_id=order_id, error=str(exc))
    cached_revision = _select_edited_sheet_revision(payload, exact_only=exact_only)
    if isinstance(cached_revision, dict):
        return cached_revision
    return None


def _load_order_sheet_revisions(
    *,
    order_id: str,
    payload: dict[str, Any] | None,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    cached_revisions: list[dict[str, Any]] = []
    cached_raw_output: dict[str, Any] | None = None
    if isinstance(payload, dict):
        edited = payload.get("_edited_ocr")
        if isinstance(edited, dict):
            raw_output = edited.get("raw_output")
            if isinstance(raw_output, dict):
                cached_raw_output = raw_output
            revisions = edited.get("revisions")
            if isinstance(revisions, list):
                cached_revisions = [item for item in revisions if isinstance(item, dict)]
            latest = edited.get("latest")
            if isinstance(latest, dict):
                latest_id = str(latest.get("revision_id") or "").strip()
                if latest_id and not any(str(item.get("revision_id") or "").strip() == latest_id for item in cached_revisions):
                    cached_revisions.append(latest)
    try:
        persisted_revisions = ocr_revision_store.list_revisions(order_id, limit=limit)
        if persisted_revisions:
            return list(reversed(persisted_revisions)), cached_raw_output
    except Exception as exc:  # noqa: BLE001
        logger.warning("Persisted OCR revision history lookup failed", order_id=order_id, error=str(exc))
    if cached_revisions:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for revision in sorted(
            cached_revisions,
            key=lambda item: str(item.get("edited_at") or ""),
            reverse=False,
        ):
            revision_id = str(revision.get("revision_id") or "").strip()
            if revision_id and revision_id in seen:
                continue
            if revision_id:
                seen.add(revision_id)
            deduped.append(revision)
        return deduped[: max(1, limit)], cached_raw_output
    return [], cached_raw_output


def _build_ocr_history_fallback_from_evidence_run(
    evidence_run: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(evidence_run, dict):
        return None
    evidence_id = str(evidence_run.get("id") or "").strip()
    if not evidence_id:
        return None
    payload = evidence_run.get("payload_json")
    if not isinstance(payload, dict):
        return None
    structured_tables = payload.get("tables")
    page_tables = payload.get("pages")
    row_count = 0
    if isinstance(structured_tables, list):
        for table in structured_tables:
            if not isinstance(table, dict):
                continue
            rows = table.get("rows")
            if isinstance(rows, list):
                row_count += len(rows)
    elif isinstance(page_tables, list):
        for page in page_tables:
            if not isinstance(page, dict):
                continue
            tables = page.get("tables")
            if not isinstance(tables, list):
                continue
            for table in tables:
                if not isinstance(table, dict):
                    continue
                rows = table.get("rows")
                if isinstance(rows, list):
                    row_count += len(rows)
    return {
        "revision_id": evidence_id,
        "edited_at": str(evidence_run.get("created_at") or "").strip() or None,
        "ui_mode": "evidence",
        "row_count": row_count,
        "changed": False,
        "sheet_save_only": False,
        "sheet_save_mode": "evidence_run",
        "source": str(evidence_run.get("source") or "").strip() or "evidence_run",
        "status": str(evidence_run.get("status") or "").strip() or None,
        "schema_version": str(evidence_run.get("schema_version") or "").strip() or None,
        "producer_version": str(evidence_run.get("producer_version") or "").strip() or None,
        "artifact_digest": str(evidence_run.get("artifact_digest") or "").strip() or None,
        "capabilities": evidence_run.get("capabilities_json")
        if isinstance(evidence_run.get("capabilities_json"), dict)
        else {},
        "degraded_reasons": evidence_run.get("degraded_reasons_json")
        if isinstance(evidence_run.get("degraded_reasons_json"), list)
        else [],
        "rows": [],
        "row_ids": [],
        "header": [],
        "fields": [],
    }


def _build_sheet_payload_from_revision(
    *,
    order_id: str,
    revision: dict[str, Any],
    fallback_sheet: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return ocr_sheet_revision_service.build_sheet_payload_from_revision(
        order_id=order_id,
        revision=revision,
        fallback_sheet=fallback_sheet,
        field_label=_field_label,
        field_value_to_str=_field_value_to_str,
    )


def _build_sheet_payload_from_draft(
    *,
    order_id: str,
    draft: dict[str, Any],
    fallback_sheet: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(draft, dict):
        return None
    draft_sheet = draft.get("draft_sheet_json")
    if not isinstance(draft_sheet, dict):
        return None
    base = dict(fallback_sheet) if isinstance(fallback_sheet, dict) else {}
    fields = list(draft_sheet.get("fields") or base.get("fields") or [])
    rows = list(draft_sheet.get("rows") or [])
    if not fields or not isinstance(rows, list):
        return None
    header = list(draft_sheet.get("header") or base.get("header") or fields)
    row_ids = list(draft_sheet.get("row_ids") or base.get("row_ids") or [f"draft-{idx + 1}" for idx in range(len(rows))])
    payload = dict(base)
    payload.update(
        {
            "order_id": order_id,
            "fields": fields,
            "header": header,
            "rows": rows,
            "row_ids": row_ids,
            "source": "draft_sheet",
            "trace": {
                "rows": [{"source": "draft_sheet", "row_count": len(rows)}],
                "mapped_mode": "draft_sheet",
            },
        }
    )
    merged_warnings: list[str] = []
    for warning in list(base.get("warnings") or []) + list(draft.get("warnings_json") or []):
        token = str(warning or "").strip()
        if token and token not in merged_warnings:
            merged_warnings.append(token)
    payload["warnings"] = merged_warnings
    return payload


def _append_edited_ocr_revision(
    *,
    order_id: str,
    ui_mode: str | None,
    fields: list[str],
    header: list[str],
    rows_payload: object,
    row_ids: list[str],
    before_digest: str,
    after_digest: str,
    revision_meta: dict[str, Any] | None = None,
) -> None:
    if not fields:
        return
    resolved_revision_meta = dict(revision_meta) if isinstance(revision_meta, dict) else {}
    raw_output_override = (
        resolved_revision_meta.pop("raw_output_override", None)
        if isinstance(resolved_revision_meta.get("raw_output_override"), dict)
        else None
    )
    revision_rows = _sanitize_revision_rows(rows_payload=rows_payload, fields=fields)
    revision_header = [str(cell or "").strip() for cell in (header or fields)]
    if len(revision_header) < len(fields):
        revision_header.extend(fields[len(revision_header) :])
    revision_markdown = _build_markdown_table_string(revision_header, revision_rows)
    revision = {
        "revision_id": f"OCRREV{uuid4().hex[:10]}",
        "edited_at": datetime.utcnow().isoformat(),
        "ui_mode": ui_mode or "unknown",
        "fields": fields,
        "header": revision_header,
        "row_ids": row_ids[: len(revision_rows)],
        "rows": revision_rows,
        "row_count": len(revision_rows),
        "before_digest": before_digest,
        "after_digest": after_digest,
        "changed": before_digest != after_digest,
        "markdown": revision_markdown,
    }
    if resolved_revision_meta:
        revision.update(resolved_revision_meta)
    try:
        ocr_revision_store.append_revision(order_id, revision)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Persisted OCR revision save failed", order_id=order_id, error=str(exc))
    try:
        with session_scope() as session:
            cache = session.get(OrderOcrCache, order_id)
            if not cache:
                cache = OrderOcrCache(order_id=order_id, payload={})
                session.add(cache)
            payload = dict(cache.payload) if isinstance(cache.payload, dict) else {}
            edited = payload.get("_edited_ocr")
            if not isinstance(edited, dict):
                edited = {}
            raw_output = edited.get("raw_output")
            if isinstance(raw_output_override, dict):
                raw_output = raw_output_override
            elif not isinstance(raw_output, dict):
                raw_output = _snapshot_raw_ocr_payload(payload)
            revisions = edited.get("revisions")
            if not isinstance(revisions, list):
                revisions = []
            revisions = [item for item in revisions if isinstance(item, dict)]
            revisions.append(revision)
            revisions = revisions[-20:]
            edited["raw_output"] = raw_output
            edited["latest"] = revision
            edited["revisions"] = revisions
            payload["_edited_ocr"] = edited
            payload["table_raw"] = revision_markdown
            payload["edited_table"] = {
                "header": revision_header,
                "rows": revision_rows,
                "row_ids": revision.get("row_ids"),
                "edited_at": revision.get("edited_at"),
            }
            payload["ocr_source"] = "edited"
            cache.payload = payload
            cache.updated_at = datetime.utcnow()
        _invalidate_orders_cache()
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR edited revision save failed", order_id=order_id, error=str(exc))


def _attach_edited_ocr_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return parsed
    edited = parsed.get("_edited_ocr")
    if not isinstance(edited, dict):
        return parsed
    latest = edited.get("latest")
    if not isinstance(latest, dict):
        return parsed
    enriched = dict(parsed)
    markdown = latest.get("markdown")
    if isinstance(markdown, str) and markdown.strip():
        enriched["table_raw"] = markdown
    enriched["ocr_source"] = "edited"
    enriched["edited_table"] = {
        "header": latest.get("header") if isinstance(latest.get("header"), list) else [],
        "rows": latest.get("rows") if isinstance(latest.get("rows"), list) else [],
        "row_ids": latest.get("row_ids") if isinstance(latest.get("row_ids"), list) else [],
        "edited_at": latest.get("edited_at"),
        "ui_mode": latest.get("ui_mode"),
        "revision_id": latest.get("revision_id"),
    }
    return enriched


def _parse_revision_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_saved_draft_revision(revision: dict[str, Any] | None) -> bool:
    if not isinstance(revision, dict):
        return False
    revision_mode = str(revision.get("sheet_save_mode") or "").strip().lower()
    if bool(revision.get("sheet_save_only")):
        return True
    return revision_mode in {"exact", "draft_candidate", "reparse_reject"}


def _extract_llm_candidate_rows_from_revision(
    revision: dict[str, Any] | None,
) -> list[list[str]]:
    if not isinstance(revision, dict):
        return []
    rows = revision.get("rows")
    if not isinstance(rows, list):
        return []
    revision_mode = str(revision.get("sheet_save_mode") or "").strip().lower()
    draft_kind = str(revision.get("draft_kind") or "").strip().lower()
    review_state = str(revision.get("review_state") or "").strip().lower()
    is_reparse_candidate = bool(revision.get("draft_from_reparse_reject")) or revision_mode == "draft_candidate"
    if draft_kind in {"reparse_reject", "auto_llm_reparse", "manual_llm_reparse", "llm_candidate"}:
        is_reparse_candidate = True
    if not is_reparse_candidate and review_state in {"auto_apply_blocked", "draft_ready", "draft_saved"}:
        is_reparse_candidate = revision_mode not in {"exact", "applied"}
    if not is_reparse_candidate:
        return []
    return [list(row) for row in rows if isinstance(row, list)]


def _load_previous_llm_candidate_rows_for_reparse(
    *,
    order_id: str,
    payload: dict[str, Any] | None = None,
) -> tuple[list[list[str]], str | None]:
    revisions, _ = _load_order_sheet_revisions(order_id=order_id, payload=payload, limit=20)
    if not revisions:
        return [], None
    ranked_revisions = sorted(
        [revision for revision in revisions if isinstance(revision, dict)],
        key=lambda item: (
            _parse_revision_datetime(item.get("edited_at")) or datetime.min,
            str(item.get("revision_id") or ""),
        ),
        reverse=True,
    )
    for revision in ranked_revisions:
        candidate_rows = _extract_llm_candidate_rows_from_revision(revision)
        if not candidate_rows:
            continue
        edited_at = str(revision.get("edited_at") or "").strip()
        label = "Previous saved LLM candidate rows"
        if edited_at:
            label = f"{label} ({edited_at})"
        return candidate_rows, label
    return [], None


def _current_sheet_revision_id(
    *,
    order_id: str,
    payload: dict[str, Any] | None = None,
) -> str | None:
    latest_revision = _select_order_sheet_revision(
        order_id=order_id,
        payload=payload,
        exact_only=False,
    )
    if not isinstance(latest_revision, dict):
        return None
    revision_id = str(latest_revision.get("revision_id") or "").strip()
    return revision_id or None


def _normalize_compare_timestamp(value: object) -> datetime | None:
    parsed = _parse_revision_datetime(value)
    if not isinstance(parsed, datetime):
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _sheet_revision_conflict_detail(
    *,
    order_id: str,
    expected_revision_id: str | None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    expected = str(expected_revision_id or "").strip()
    current = _current_sheet_revision_id(order_id=order_id, payload=payload) or ""
    if not expected and not current:
        return None
    if expected == current:
        return None
    return {
        "error": "stale_revision_conflict",
        "expected_revision_id": expected or None,
        "current_revision_id": current or None,
    }


def _lines_timestamp_conflict_detail(
    *,
    current_lines_updated_at: datetime | None,
    expected_lines_updated_at: str | None,
) -> dict[str, Any] | None:
    expected = _normalize_compare_timestamp(expected_lines_updated_at)
    current = _normalize_compare_timestamp(current_lines_updated_at.isoformat() if current_lines_updated_at else None)
    if expected is None and current is None:
        return None
    if expected is not None and current is not None and expected == current:
        return None
    return {
        "error": "stale_lines_conflict",
        "expected_lines_updated_at": expected.isoformat() if isinstance(expected, datetime) else None,
        "current_lines_updated_at": current.isoformat() if isinstance(current, datetime) else None,
    }


def _selection_conflict_detail(
    *,
    field: str,
    current_value: str | None,
    expected_value: str | None,
    desired_value: str | None,
) -> dict[str, Any] | None:
    expected = str(expected_value or "").strip()
    current = str(current_value or "").strip()
    desired = str(desired_value or "").strip()
    if not expected:
        return None
    if current == expected or current == desired:
        return None
    return {
        "error": f"stale_{field}_conflict",
        f"expected_{field}": expected or None,
        f"current_{field}": current or None,
        f"desired_{field}": desired or None,
    }


def _order_context_conflict_detail(
    *,
    order_id: str,
    expected_facility_code: str | None,
    expected_week_code: str | None,
    expected_document_uri: str | None,
    expected_lines_updated_at: datetime | None,
) -> dict[str, Any] | None:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return {"error": "order_not_found"}
        mismatches: list[str] = []
        current_facility = str(order.facility_code or "").strip() or None
        current_week = str(order.week_code or "").strip() or None
        current_document = str(order.document_uri or "").strip() or None
        current_lines_updated_at = _normalize_compare_timestamp(
            order.lines_updated_at.isoformat() if order.lines_updated_at else None
        )
        expected_lines_normalized = _normalize_compare_timestamp(
            expected_lines_updated_at.isoformat() if isinstance(expected_lines_updated_at, datetime) else None
        )
        if str(expected_facility_code or "").strip() != str(current_facility or "").strip():
            mismatches.append("facility")
        if str(expected_week_code or "").strip() != str(current_week or "").strip():
            mismatches.append("week")
        if str(expected_document_uri or "").strip() != str(current_document or "").strip():
            mismatches.append("document")
        if expected_lines_normalized != current_lines_updated_at:
            mismatches.append("lines")
        if not mismatches:
            return None
        return {
            "error": "stale_order_context",
            "mismatches": mismatches,
            "current_facility": current_facility,
            "current_week": current_week,
            "current_document": current_document,
            "current_lines_updated_at": (
                current_lines_updated_at.isoformat() if isinstance(current_lines_updated_at, datetime) else None
            ),
        }


def _extract_order_draft_state(
    order_id: str,
    payload: dict[str, Any] | None,
    *,
    lines_updated_at: datetime | None = None,
) -> dict[str, Any]:
    latest_revision = _select_order_sheet_revision(order_id=order_id, payload=payload, exact_only=True)
    latest_revision_id = (
        str(latest_revision.get("revision_id") or "").strip()
        if isinstance(latest_revision, dict)
        else ""
    )
    draft_updated_at = (
        str(latest_revision.get("edited_at") or "").strip()
        if isinstance(latest_revision, dict)
        else ""
    )
    draft_updated_at_dt = _parse_revision_datetime(draft_updated_at)
    reparse_debug = payload.get("_reparse_debug") if isinstance(payload, dict) else None
    reject_reasons: list[str] = []
    if isinstance(reparse_debug, dict):
        raw_reject_reasons = reparse_debug.get("reject_reasons")
        if isinstance(raw_reject_reasons, list):
            reject_reasons = [
                str(item).strip()
                for item in raw_reject_reasons
                if str(item).strip()
            ]
        if not reject_reasons:
            error = str(reparse_debug.get("error") or "").strip()
            if error:
                reject_reasons = [error]
    latest_revision_has_rows = bool(
        isinstance(latest_revision, dict)
        and _is_saved_draft_revision(latest_revision)
        and isinstance(latest_revision.get("rows"), list)
    )
    draft_newer_than_lines = bool(
        latest_revision_has_rows
        and draft_updated_at_dt
        and (not lines_updated_at or draft_updated_at_dt > lines_updated_at)
    )
    auto_apply_blocked = bool(latest_revision_has_rows and draft_newer_than_lines and reject_reasons)
    has_saved_draft = bool(latest_revision_has_rows and (draft_newer_than_lines or auto_apply_blocked))
    return {
        "has_saved_draft": has_saved_draft,
        "draft_updated_at": draft_updated_at or None,
        "draft_revision_id": latest_revision_id or None if has_saved_draft else None,
        "draft_newer_than_lines": draft_newer_than_lines,
        "auto_apply_blocked": auto_apply_blocked,
        "reject_reasons": reject_reasons,
        "latest_revision": latest_revision if has_saved_draft and isinstance(latest_revision, dict) else None,
    }


def _sheet_order_lines_suppression_reason(
    *,
    order_status: str | None,
    order_lines: list[dict[str, Any]] | None,
    ocr_metrics: dict[str, Any] | None,
) -> str | None:
    if not order_lines:
        return None
    if str(order_status or "").strip() == "確定":
        return None
    if not isinstance(ocr_metrics, dict) or not ocr_metrics:
        return None
    result_state = str(ocr_metrics.get("result_state") or "").strip().lower()
    if result_state != "hard_failed":
        return None
    projection = ocr_metrics.get("structural_row_projection")
    if not isinstance(projection, dict) or not projection:
        return None
    min_rows = _read_reparse_int_env(
        "OCR_SHEET_SUPPRESS_FAILED_PROJECTION_MIN_ROWS",
        24,
        min_value=1,
    )
    min_cells = _read_reparse_int_env(
        "OCR_SHEET_SUPPRESS_FAILED_PROJECTION_MIN_CELLS",
        48,
        min_value=1,
    )
    try:
        projected_rows = int(projection.get("rows_with_projected_quantity") or 0)
    except Exception:
        projected_rows = 0
    try:
        projected_cells = int(projection.get("quantity_cells_copied") or 0)
    except Exception:
        projected_cells = 0
    if projected_rows < min_rows or projected_cells < min_cells:
        return None
    return "sheet_order_lines_suppressed_reparse_failed"


def _resolve_sheet_suppression_metrics(
    *,
    order_id: str,
    ocr_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload_metrics = ocr_payload.get("metrics") if isinstance(ocr_payload, dict) else None
    if isinstance(payload_metrics, dict) and payload_metrics:
        result_state = str(payload_metrics.get("result_state") or "").strip()
        projection = payload_metrics.get("structural_row_projection")
        if result_state or (isinstance(projection, dict) and projection):
            return payload_metrics
    job = get_ocr_job(f"OCR-{order_id}")
    job_metrics = job.get("metrics") if isinstance(job, dict) else None
    if isinstance(job_metrics, dict) and job_metrics:
        return job_metrics
    return payload_metrics if isinstance(payload_metrics, dict) and payload_metrics else None


def _validate_structural_projection_requires_manual_review(
    *,
    llm_quantity_only_active: bool,
    structural_row_projection: dict[str, Any] | None,
) -> tuple[str | None, dict[str, Any] | None]:
    if not llm_quantity_only_active:
        return None, None
    if not _read_reparse_bool_env(
        "OCR_REPARSE_REQUIRE_MANUAL_REVIEW_FOR_STRUCTURAL_PROJECTION",
        True,
    ):
        return None, None
    if not isinstance(structural_row_projection, dict) or not structural_row_projection:
        return None, None
    min_rows = _read_reparse_int_env(
        "OCR_REPARSE_STRUCTURAL_PROJECTION_REVIEW_MIN_ROWS",
        24,
        min_value=1,
    )
    min_cells = _read_reparse_int_env(
        "OCR_REPARSE_STRUCTURAL_PROJECTION_REVIEW_MIN_CELLS",
        48,
        min_value=1,
    )
    try:
        projected_rows = int(structural_row_projection.get("rows_with_projected_quantity") or 0)
    except Exception:
        projected_rows = 0
    try:
        projected_cells = int(structural_row_projection.get("quantity_cells_copied") or 0)
    except Exception:
        projected_cells = 0
    if projected_rows < min_rows or projected_cells < min_cells:
        return None, None
    detail = {
        "quality_issue": "structural_projection_requires_review",
        "projected_row_count": int(structural_row_projection.get("projected_row_count") or 0),
        "rows_with_projected_quantity": projected_rows,
        "quantity_cells_copied": projected_cells,
        "min_rows": int(min_rows),
        "min_cells": int(min_cells),
        "structural_row_projection": dict(structural_row_projection),
    }
    return "sheet_structural_projection_requires_review", detail


_REVIEW_REASON_MESSAGES: dict[str, str] = {
    "weekly_menu_missing": "対象週の月次メニューが未登録です。",
    "menu_entries_missing": "対象週のメニュー行が不足しています。",
    "week_unresolved": "週情報が確定していません。",
    "sheet_fields_not_found": "シート列の割り当てが不足しています。",
    "sheet_fields_duplicate": "シート列の割り当てが重複しています。",
    "sheet_template_field_invalid": "施設テンプレートの列定義に不整合があります。",
    "sheet_quantity_columns_missing": "数量列が見つかりませんでした。",
    "sheet_quantity_column_unmapped": "数量列の一部がシート列へ対応付けできていません。",
    "sheet_week_dates_incomplete": "週の日付が揃っていません。",
    "week_menu_date_mismatch": "週メニューの日付とOCR結果が一致していません。",
    "sheet_date_mismatch": "シートの日付列に不一致があります。",
    "sheet_canonical_mismatch": "メニューと数量の並びに不一致があります。",
    "sheet_suspicious_blank_row": "空行の位置が不自然です。",
    "draft_newer_than_lines": "保存済みの下書きが現在の明細より新しい状態です。",
    "auto_apply_blocked": "自動反映は監査または検証で保留されています。",
    "draft_rows_empty": "保存済みの下書きに行がありません。",
    "rows_empty": "シートに行がありません。",
    "ocr_review_required": "OCR結果の確認が必要です。",
    "ocr_table_fallback": "暫定OCRテーブルを表示しています。",
    "reparse_stale": "再解析ジョブが停止しているため、再実行が必要です。",
    "sheet_order_lines_suppressed_reparse_failed": "失敗した再解析の明細は採用せず、OCRの下書きを表示しています。",
    "sheet_structural_projection_requires_review": "広範囲の数量投影が必要だったため、自動反映を止めています。",
    "sheet_payload_mapping_blocked_numeric_review_required": "数量OCRの信頼度が低いため、自動投影を止めています。",
    "ocr_evidence_recovery_required": "OCR成果物が不足しているため、まず復旧が必要です。",
    "template_resolution_blocked": "施設テンプレートの判定が不安定なため、先に確認が必要です。",
    "template_mismatch": "OCRが選んだテンプレートと施設設定が一致していません。",
    "template_confidence_low": "施設テンプレートの判定信頼度が低いため、自動採用を止めています。",
}


def _review_reason_message(code: object) -> str:
    normalized = str(code or "").strip()
    if not normalized:
        return ""
    return _REVIEW_REASON_MESSAGES.get(normalized, normalized.replace("_", " "))


def _review_reason_details(codes: list[str] | None, *, severity: str) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    seen: set[str] = set()
    for code in codes or []:
        normalized = str(code or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        details.append(
            {
                "code": normalized,
                "message": _review_reason_message(normalized),
                "severity": severity,
            }
        )
    return details


def _derive_reparse_status(
    *,
    order_id: str,
    ocr_status: str | None,
    has_saved_draft: bool,
    auto_apply_blocked: bool,
    last_reparse_error: str,
) -> str:
    normalized = str(ocr_status or "").strip().lower()
    if normalized in {"running", "pending", "stalled"}:
        return "running"
    if auto_apply_blocked and has_saved_draft:
        return "blocked"
    if has_saved_draft:
        return "draft_ready"
    if normalized in {"failed", "error"} or last_reparse_error:
        return "failed"
    return "idle"


def _derive_review_stage(
    *,
    order_status: str | None,
    normalized_ocr_status: str,
    has_saved_draft: bool,
    draft_newer_than_lines: bool,
    auto_apply_blocked: bool,
    reparse_status: str,
    needs_human_review: bool,
) -> str:
    if reparse_status == "running" or normalized_ocr_status in {"running", "pending"}:
        return "parsing"
    if needs_human_review:
        return "needs_human_review"
    if has_saved_draft and not draft_newer_than_lines and not auto_apply_blocked:
        return "drafting"
    if has_saved_draft or auto_apply_blocked or normalized_ocr_status in {"failed", "stalled"}:
        return "needs_human_review"
    if str(order_status or "").strip() == "確定":
        return "confirmed"
    if normalized_ocr_status in {"done", "success"}:
        return "confirmed"
    return "idle"


def get_order_review_summary(
    order_id: str,
    *,
    lines_updated_at: datetime | None = None,
    ocr_status: str | None = None,
    cached_payload: dict[str, Any] | None = None,
    ocr_metrics: dict[str, Any] | None = None,
    order_status: str | None = None,
) -> dict[str, Any]:
    payload = cached_payload if isinstance(cached_payload, dict) else _load_order_ocr_cache(order_id)
    draft_state = _extract_order_draft_state(order_id, payload, lines_updated_at=lines_updated_at)
    revision_count = 0
    revision_last_id = str(draft_state.get("draft_revision_id") or "").strip() or None
    edited = payload.get("_edited_ocr") if isinstance(payload, dict) else None
    cached_revisions = edited.get("revisions") if isinstance(edited, dict) else None
    if isinstance(cached_revisions, list):
        revision_count = len([item for item in cached_revisions if isinstance(item, dict)])
    try:
        persisted_revision_summary = ocr_revision_store.get_revision_summary(order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Persisted OCR revision summary lookup failed", order_id=order_id, error=str(exc))
        persisted_revision_summary = {}
    if isinstance(persisted_revision_summary, dict):
        persisted_count = int(persisted_revision_summary.get("count") or 0)
        if persisted_count > 0:
            revision_count = persisted_count
            revision_last_id = str(persisted_revision_summary.get("last_revision_id") or "").strip() or None
    normalized_ocr_status = str(ocr_status or "").strip().lower()
    metrics = ocr_metrics if isinstance(ocr_metrics, dict) else {}
    latest_revision = draft_state.get("latest_revision") if isinstance(draft_state.get("latest_revision"), dict) else None
    latest_rows = latest_revision.get("rows") if isinstance(latest_revision, dict) else None
    draft_row_count = len(latest_rows) if isinstance(latest_rows, list) else 0
    apply_blockers: list[str] = []
    if draft_state["has_saved_draft"] and draft_row_count <= 0:
        apply_blockers.append("draft_rows_empty")
    confirm_blockers: list[str] = []
    confirm_warnings: list[str] = []
    if draft_state["draft_newer_than_lines"]:
        confirm_warnings.append("draft_newer_than_lines")
    if draft_state["auto_apply_blocked"]:
        confirm_warnings.append("auto_apply_blocked")
    reparse_debug = payload.get("_reparse_debug") if isinstance(payload, dict) else None
    last_reparse_error = (
        str(reparse_debug.get("error") or "").strip()
        if isinstance(reparse_debug, dict)
        else ""
    )
    if not last_reparse_error:
        last_reparse_error = str(metrics.get("error") or "").strip()
    processing_stage = str(metrics.get("processing_stage") or "").strip().lower() or None
    result_state = str(metrics.get("result_state") or "").strip().lower() or None
    confirmed_lines_retained = bool(metrics.get("confirmed_lines_retained"))
    if not result_state:
        if draft_state["has_saved_draft"] and draft_state["auto_apply_blocked"]:
            result_state = "draft_ready_blocked"
        elif normalized_ocr_status in {"failed", "empty", "stalled"}:
            result_state = "hard_failed"
        elif normalized_ocr_status in {"done", "success"} and lines_updated_at:
            result_state = "applied"
        elif normalized_ocr_status in {"running", "pending"}:
            result_state = "processing"
    reparse_status = _derive_reparse_status(
        order_id=order_id,
        ocr_status=ocr_status,
        has_saved_draft=bool(draft_state["has_saved_draft"]),
        auto_apply_blocked=bool(draft_state["auto_apply_blocked"]),
        last_reparse_error=last_reparse_error,
    )
    can_apply_draft = bool(draft_state["has_saved_draft"]) and not apply_blockers
    can_confirm = not confirm_blockers
    review_state = "none"
    review_badges: list[str] = []
    if draft_state["has_saved_draft"]:
        review_state = (
            "draft_ready"
            if draft_state["auto_apply_blocked"] or draft_state["draft_newer_than_lines"]
            else "draft_saved"
        )
        review_badges.append("下書きあり")
        if draft_state["auto_apply_blocked"]:
            review_badges.append("自動反映保留")
        if draft_state["draft_newer_than_lines"]:
            review_badges.append("下書き未反映")
        if confirmed_lines_retained:
            review_badges.append("確定明細保持")
    elif normalized_ocr_status in {"failed", "stalled"}:
        review_state = "processing_failed"
        review_badges.append("OCR失敗")
    elif normalized_ocr_status in {"running", "pending"}:
        review_state = "processing"
    if processing_stage and normalized_ocr_status in {"running", "pending"}:
        review_badges.append(f"処理:{processing_stage}")
    review_stage = _derive_review_stage(
        order_status=order_status,
        normalized_ocr_status=normalized_ocr_status,
        has_saved_draft=bool(draft_state["has_saved_draft"]),
        draft_newer_than_lines=bool(draft_state["draft_newer_than_lines"]),
        auto_apply_blocked=bool(draft_state["auto_apply_blocked"]),
        reparse_status=reparse_status,
        needs_human_review=bool(apply_blockers or confirm_blockers or confirm_warnings),
    )
    apply_blocker_details = _review_reason_details(apply_blockers, severity="blocker")
    confirm_blocker_details = _review_reason_details(confirm_blockers, severity="blocker")
    confirm_warning_details = _review_reason_details(confirm_warnings, severity="warning")
    return {
        "ocr_review_state": review_state,
        "ocr_review_stage": review_stage,
        "ocr_review_badges": review_badges,
        "ocr_has_saved_draft": draft_state["has_saved_draft"],
        "ocr_draft_updated_at": draft_state["draft_updated_at"],
        "ocr_draft_revision_id": draft_state["draft_revision_id"],
        "ocr_draft_row_count": draft_row_count,
        "ocr_draft_newer_than_lines": draft_state["draft_newer_than_lines"],
        "ocr_auto_apply_blocked": draft_state["auto_apply_blocked"],
        "ocr_reject_reasons": draft_state["reject_reasons"],
        "ocr_last_reparse_error": last_reparse_error or None,
        "ocr_reparse_status": reparse_status,
        "ocr_reparse_last_error_code": last_reparse_error or None,
        "ocr_can_apply_draft": can_apply_draft,
        "ocr_apply_blockers": apply_blockers,
        "ocr_apply_blocker_details": apply_blocker_details,
        "ocr_can_confirm": can_confirm,
        "ocr_confirm_blockers": confirm_blockers,
        "ocr_confirm_warnings": confirm_warnings,
        "ocr_confirm_blocker_details": confirm_blocker_details,
        "ocr_confirm_warning_details": confirm_warning_details,
        "ocr_processing_stage": processing_stage,
        "ocr_result_state": result_state,
        "ocr_confirmed_lines_retained": confirmed_lines_retained,
        "ocr_revision_count": revision_count,
        "ocr_revision_last_id": revision_last_id,
    }


def _augment_sheet_review_payload(
    *,
    order_id: str,
    payload: dict[str, Any],
    lines_updated_at: datetime | None = None,
    ocr_payload: dict[str, Any] | None = None,
    ocr_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = dict(payload)
    warnings = [
        str(item).strip()
        for item in (payload.get("warnings") or [])
        if str(item).strip()
    ]
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    source = str(payload.get("source") or "").strip()
    draft_state = _extract_order_draft_state(order_id, ocr_payload, lines_updated_at=lines_updated_at)
    job = get_ocr_job(f"OCR-{order_id}")
    sheet_gate = apply_gate_service.evaluate_sheet_gate(
        rows=rows if isinstance(rows, list) else None,
        source=source,
        blockers=None,
        warnings=warnings,
        draft_newer_than_lines=bool(draft_state["draft_newer_than_lines"]),
        auto_apply_blocked=bool(draft_state["auto_apply_blocked"]),
        reparse_status=(describe_ocr_job_state(job).get("status") if isinstance(job, dict) else None),
    )
    apply_blockers = list(sheet_gate.get("apply_blockers") or [])
    confirm_blockers = list(sheet_gate.get("confirm_blockers") or [])
    confirm_warnings = list(sheet_gate.get("confirm_warnings") or [])
    can_apply = bool(rows) and not apply_blockers
    can_confirm = not confirm_blockers
    processing_stage = (
        str((ocr_metrics or {}).get("processing_stage") or "").strip().lower()
        if isinstance(ocr_metrics, dict)
        else ""
    )
    result_state = (
        str((ocr_metrics or {}).get("result_state") or "").strip().lower()
        if isinstance(ocr_metrics, dict)
        else ""
    )
    confirmed_lines_retained = bool((ocr_metrics or {}).get("confirmed_lines_retained")) if isinstance(ocr_metrics, dict) else False
    confirmed_line_count = 0
    try:
        with session_scope() as session:
            confirmed_line_count = int(
                session.execute(
                    select(func.count(OrderLine.id)).where(OrderLine.order_id == order_id)
                ).scalar_one()
                or 0
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to count confirmed order lines", order_id=order_id, error=str(exc))
    draft_line_count = len(rows)
    line_count_delta = draft_line_count - confirmed_line_count
    line_count_mismatch = line_count_delta != 0

    review_state = "ready"
    if draft_state["has_saved_draft"] and draft_state["auto_apply_blocked"]:
        review_state = "auto_apply_blocked"
    elif draft_state["has_saved_draft"] and draft_state["draft_newer_than_lines"]:
        review_state = "draft_ready"
    elif draft_state["has_saved_draft"]:
        review_state = "draft_saved"
    elif apply_blockers or confirm_blockers or confirm_warnings:
        review_state = "review_required"
    reparse_debug = ocr_payload.get("_reparse_debug") if isinstance(ocr_payload, dict) else None
    reparse_error = (
        str(reparse_debug.get("error") or "").strip()
        if isinstance(reparse_debug, dict)
        else ""
    )
    if not reparse_error and isinstance(ocr_metrics, dict):
        reparse_error = str(ocr_metrics.get("error") or "").strip()
    reparse_status = _derive_reparse_status(
        order_id=order_id,
        ocr_status=(ocr_metrics or {}).get("status") if isinstance(ocr_metrics, dict) else None,
        has_saved_draft=bool(draft_state["has_saved_draft"]),
        auto_apply_blocked=bool(draft_state["auto_apply_blocked"]),
        last_reparse_error=reparse_error,
    )
    review_stage = _derive_review_stage(
        order_status=None,
        normalized_ocr_status="done",
        has_saved_draft=bool(draft_state["has_saved_draft"]),
        draft_newer_than_lines=bool(draft_state["draft_newer_than_lines"]),
        auto_apply_blocked=bool(draft_state["auto_apply_blocked"]),
        reparse_status=reparse_status,
        needs_human_review=bool(apply_blockers or confirm_blockers or confirm_warnings),
    )
    if review_state == "review_required":
        review_stage = "needs_human_review"
    apply_blocker_details = _review_reason_details(apply_blockers, severity="blocker")
    confirm_blocker_details = _review_reason_details(confirm_blockers, severity="blocker")
    confirm_warning_details = _review_reason_details(confirm_warnings, severity="warning")

    enriched.update(
        {
            "review_state": review_state,
            "review_stage": review_stage,
            "can_apply": can_apply,
            "can_confirm": can_confirm,
            "apply_blockers": apply_blockers,
            "apply_blocker_details": apply_blocker_details,
            "confirm_blockers": confirm_blockers,
            "confirm_warnings": confirm_warnings,
            "confirm_blocker_details": confirm_blocker_details,
            "confirm_warning_details": confirm_warning_details,
            "has_saved_draft": draft_state["has_saved_draft"],
            "draft_updated_at": draft_state["draft_updated_at"],
            "draft_revision_id": draft_state["draft_revision_id"],
            "draft_newer_than_lines": draft_state["draft_newer_than_lines"],
            "auto_apply_blocked": draft_state["auto_apply_blocked"],
            "reject_reasons": draft_state["reject_reasons"],
            "reparse_status": reparse_status,
            "reparse_last_error_code": reparse_error or None,
            "draft_line_count": draft_line_count,
            "confirmed_line_count": confirmed_line_count,
            "line_count_delta": line_count_delta,
            "line_count_mismatch": line_count_mismatch,
            "processing_stage": processing_stage or None,
            "result_state": result_state or None,
            "confirmed_lines_retained": confirmed_lines_retained,
        }
    )
    return enriched


def _normalize_structured_rows(
    *,
    header: object,
    rows_payload: object,
    template: dict[str, Any],
) -> list[list[str]]:
    if not isinstance(rows_payload, list):
        return []

    header_cells: list[str] = []
    if isinstance(header, list):
        header_cells = [_coerce_table_cell(cell) for cell in header]
    expected_fields = _get_row_fields(template)

    normalized_rows: list[list[str]] = []
    for row in rows_payload:
        normalized_row: list[str] = []
        if isinstance(row, list):
            normalized_row = [_coerce_table_cell(cell) for cell in row]
        elif isinstance(row, dict):
            if expected_fields:
                normalized_row = [_coerce_table_cell(row.get(field)) for field in expected_fields]
            elif header_cells:
                normalized_row = [_coerce_table_cell(row.get(col)) for col in header_cells]
            else:
                normalized_row = [_coerce_table_cell(value) for value in row.values()]
        if normalized_row and any(cell.strip() for cell in normalized_row):
            normalized_rows.append(normalized_row)
    if not normalized_rows:
        return []
    if not expected_fields:
        return normalized_rows

    field_count = len(expected_fields)
    if field_count and all(len(row) == field_count for row in normalized_rows):
        return normalized_rows

    mapped_indexes: dict[int, int] = {}
    if header_cells:
        fields_set = set(expected_fields)
        for idx, cell in enumerate(header_cells):
            field = _field_from_header(cell, fields_set)
            if not field:
                continue
            dest_idx = expected_fields.index(field)
            if dest_idx in mapped_indexes.values():
                continue
            mapped_indexes[idx] = dest_idx
    if not mapped_indexes and header_cells and len(header_cells) == field_count:
        mapped_indexes = {idx: idx for idx in range(field_count)}

    if mapped_indexes:
        aligned_rows: list[list[str]] = []
        for row in normalized_rows:
            output_row = [""] * field_count
            for src_idx, dst_idx in mapped_indexes.items():
                if src_idx < len(row):
                    output_row[dst_idx] = row[src_idx]
            if any(cell.strip() for cell in output_row):
                aligned_rows.append(output_row)
        if aligned_rows:
            return aligned_rows

    fallback_rows: list[list[str]] = []
    for row in normalized_rows:
        output_row = row[:field_count]
        if len(output_row) < field_count:
            output_row = output_row + [""] * (field_count - len(output_row))
        if any(cell.strip() for cell in output_row):
            fallback_rows.append(output_row)
    return fallback_rows


def apply_ocr_markdown(order_id: str, markdown: str):
    return apply_ocr_table(order_id, markdown=markdown, header=None, rows=None)


def apply_submitted_ocr_sheet(
    order_id: str,
    *,
    markdown: str | None = None,
    header: object = None,
    rows: object = None,
    ui_mode: str | None = None,
    fields: object = None,
    row_ids: object = None,
    expected_revision_id: str | None = None,
    expected_lines_updated_at: str | None = None,
    enforce_revision_guard: bool = False,
    enforce_lines_guard: bool = False,
    revision_meta: dict[str, Any] | None = None,
):
    config_service.reload_configs()
    has_markdown = isinstance(markdown, str) and bool(markdown.strip())
    has_rows = isinstance(rows, list) and bool(rows)
    if not has_markdown and not has_rows:
        return None, "markdown_empty"

    current_payload = _load_order_ocr_cache(order_id)
    if enforce_revision_guard:
        revision_conflict = _sheet_revision_conflict_detail(
            order_id=order_id,
            expected_revision_id=expected_revision_id,
            payload=current_payload,
        )
        if revision_conflict is not None:
            return None, revision_conflict["error"]

    received_at = None
    facility_id = None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        if not order.facility_code:
            return None, "facility_missing"
        received_at = order.received_at or pd.Timestamp.utcnow()
        facility_id = order.facility_code

    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility_config = None
    try:
        facility_config = config_service.get_facility_config(facility_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Facility config lookup failed", facility_id=facility_id, error=str(exc))
    if not facility_config:
        facility_config = next(
            (
                fac
                for fac in master.get("facilities", [])
                if fac.get("facility_id") == facility_id
            ),
            None,
        )
    if not facility_config:
        return None, "facility_not_found"

    template = facility_config.get("fax_template") or config_service._merge_template(
        base_template,
        facility_config.get("fax_template_override"),
    )

    source = "markdown"
    parsed_rows = _normalize_structured_rows(header=header, rows_payload=rows, template=template)
    if parsed_rows:
        source = "structured_rows"
    elif has_markdown:
        parsed_rows = rows_from_markdown(markdown or "", template) or []
    if not parsed_rows:
        return None, "rows_empty"

    revision_fields: list[str] = []
    if isinstance(fields, list):
        revision_fields = [str(field).strip() for field in fields if str(field).strip()]
    if not revision_fields:
        revision_fields = _row_fields_from_template(template)
    max_width = max((len(row) for row in parsed_rows), default=0)
    if isinstance(header, list):
        max_width = max(max_width, len(header))
    if not revision_fields:
        revision_fields = [f"col{idx + 1}" for idx in range(max(max_width, 1))]
    elif len(revision_fields) < max_width:
        revision_fields.extend(
            [f"col{idx + 1}" for idx in range(len(revision_fields), max_width)]
        )

    revision_header: list[str] = []
    if isinstance(header, list):
        revision_header = [_coerce_table_cell(cell) for cell in header]
    if not revision_header:
        revision_header = [_field_label(field) for field in revision_fields]
    if len(revision_header) < len(revision_fields):
        revision_header.extend(
            [_field_label(field) for field in revision_fields[len(revision_header) :]]
        )

    revision_row_ids: list[str] = []
    if isinstance(row_ids, list):
        revision_row_ids = [str(item).strip() for item in row_ids if str(item).strip()]
    revision_row_count = len(rows) if isinstance(rows, list) else len(parsed_rows)
    if len(revision_row_ids) < revision_row_count:
        revision_row_ids.extend(
            [f"row-{idx + 1}" for idx in range(len(revision_row_ids), revision_row_count)]
        )

    revision_ui_mode = str(ui_mode or "").strip().lower()
    if not revision_ui_mode:
        revision_ui_mode = "sheet" if source == "structured_rows" else "legacy"
    save_result, save_error = save_ocr_sheet_exact(
        order_id,
        header=revision_header,
        rows=rows if isinstance(rows, list) else parsed_rows,
        fields=revision_fields,
        row_ids=revision_row_ids,
        ui_mode=revision_ui_mode,
        expected_revision_id=expected_revision_id,
        expected_lines_updated_at=expected_lines_updated_at,
        enforce_revision_guard=enforce_revision_guard,
        enforce_lines_guard=enforce_lines_guard,
    )
    if save_error:
        return None, save_error
    draft_record = save_result.get("draft") if isinstance(save_result, dict) else None
    serialized, apply_error = apply_latest_draft(
        order_id,
        draft_record=draft_record if isinstance(draft_record, dict) else None,
        source=source,
        expected_lines_updated_at=expected_lines_updated_at,
        enforce_lines_guard=enforce_lines_guard,
    )
    if apply_error:
        return None, apply_error
    if isinstance(serialized, dict):
        revision = save_result.get("revision") if isinstance(save_result, dict) else None
        if isinstance(revision, dict):
            serialized["draft_revision"] = revision
    return serialized, None


def apply_ocr_table(
    order_id: str,
    *,
    markdown: str | None = None,
    header: object = None,
    rows: object = None,
    ui_mode: str | None = None,
    fields: object = None,
    row_ids: object = None,
    expected_revision_id: str | None = None,
    expected_lines_updated_at: str | None = None,
    enforce_revision_guard: bool = False,
    enforce_lines_guard: bool = False,
    revision_meta: dict[str, Any] | None = None,
):
    return apply_submitted_ocr_sheet(
        order_id,
        markdown=markdown,
        header=header,
        rows=rows,
        ui_mode=ui_mode,
        fields=fields,
        row_ids=row_ids,
        expected_revision_id=expected_revision_id,
        expected_lines_updated_at=expected_lines_updated_at,
        enforce_revision_guard=enforce_revision_guard,
        enforce_lines_guard=enforce_lines_guard,
        revision_meta=revision_meta,
    )


def save_ocr_sheet_exact(
    order_id: str,
    *,
    header: object = None,
    rows: object = None,
    fields: object = None,
    row_ids: object = None,
    ui_mode: str | None = None,
    expected_revision_id: str | None = None,
    expected_lines_updated_at: str | None = None,
    enforce_revision_guard: bool = False,
    enforce_lines_guard: bool = False,
):
    snapshot = _normalize_sheet_revision_snapshot(
        fields=fields,
        header=header,
        rows_payload=rows,
        row_ids=row_ids,
    )
    if not snapshot["rows"]:
        return None, "rows_empty"

    facility_id: str | None = None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        facility_id = order.facility_code

    current_payload = _load_order_ocr_cache(order_id)
    if enforce_revision_guard:
        revision_conflict = _sheet_revision_conflict_detail(
            order_id=order_id,
            expected_revision_id=expected_revision_id,
            payload=current_payload,
        )
        if revision_conflict is not None:
            return None, revision_conflict["error"]
    previous_revision = _select_order_sheet_revision(
        order_id=order_id,
        payload=current_payload,
        exact_only=False,
    )
    if isinstance(previous_revision, dict):
        before_digest = _sheet_digest(
            fields=previous_revision.get("fields"),
            header=previous_revision.get("header"),
            rows_payload=previous_revision.get("rows"),
            row_ids=previous_revision.get("row_ids"),
        )
    else:
        current_sheet, current_error = get_ocr_sheet(order_id)
        if current_error or not isinstance(current_sheet, dict):
            before_digest = _sheet_digest(
                fields=snapshot["fields"],
                header=snapshot["header"],
                rows_payload=snapshot["rows"],
                row_ids=snapshot["row_ids"],
            )
        else:
            before_digest = _sheet_digest(
                fields=current_sheet.get("fields"),
                header=current_sheet.get("header"),
                rows_payload=current_sheet.get("rows"),
                row_ids=current_sheet.get("row_ids"),
            )
    after_digest = _sheet_digest(
        fields=snapshot["fields"],
        header=snapshot["header"],
        rows_payload=snapshot["rows"],
        row_ids=snapshot["row_ids"],
    )

    if enforce_lines_guard:
        latest_lines_updated_at: datetime | None = None
        with session_scope() as session:
            latest_order = session.get(Order, order_id)
            if not latest_order:
                return None, "order_not_found"
            latest_lines_updated_at = latest_order.lines_updated_at
        lines_conflict = _lines_timestamp_conflict_detail(
            current_lines_updated_at=latest_lines_updated_at,
            expected_lines_updated_at=expected_lines_updated_at,
        )
        if lines_conflict is not None:
            return None, lines_conflict["error"]

    resolved_ui_mode = str(ui_mode or "").strip().lower() or "sheet"
    _append_edited_ocr_revision(
        order_id=order_id,
        ui_mode=resolved_ui_mode,
        fields=snapshot["fields"],
        header=snapshot["header"],
        rows_payload=snapshot["rows"],
        row_ids=snapshot["row_ids"],
        before_digest=before_digest,
        after_digest=after_digest,
        revision_meta={
            "sheet_save_only": True,
            "sheet_save_mode": "exact",
            "review_state": "draft_ready",
            "review_blockers": [],
            "review_warnings": [],
        },
    )
    persisted_draft = persist_sheet_draft(
        order_id=order_id,
        draft_sheet_json={
            "fields": snapshot["fields"],
            "header": snapshot["header"],
            "rows": snapshot["rows"],
            "row_ids": snapshot["row_ids"],
            "ui_mode": resolved_ui_mode,
        },
        draft_state="draft_ready",
        blockers=[],
        warnings=[],
    )
    record_event(
        "ocr_sheet_save",
        actor="system",
        target=order_id,
        fac=facility_id,
        metadata={
            "row_count": len(snapshot["rows"]),
            "changed": before_digest != after_digest,
            "mode": "exact",
        },
    )
    history, history_error = get_ocr_edit_history(order_id)
    if history_error:
        return None, history_error
    latest = history.get("latest") if isinstance(history, dict) else None
    return {
        "order_id": order_id,
        "revision": latest if isinstance(latest, dict) else None,
        "draft": persisted_draft if isinstance(persisted_draft, dict) else None,
    }, None


def _save_reparse_candidate_as_draft(
    *,
    order_id: str,
    template: dict[str, Any],
    rows: list[list[str]],
    before_digest: str,
    raw_output_override: dict[str, Any] | None = None,
    review_state: str,
    review_blockers: list[str] | None = None,
    review_warnings: list[str] | None = None,
) -> None:
    candidate_fields = _row_fields_from_template(template)
    if not candidate_fields:
        width = max((len(row) for row in rows if isinstance(row, list)), default=0)
        if width <= 0:
            return
        candidate_fields = [f"col{idx + 1}" for idx in range(width)]
    candidate_header = _sheet_header_from_template(candidate_fields, template)
    candidate_row_ids = [f"draft-{idx + 1}" for idx in range(len(rows))]
    after_digest = _sheet_digest(
        fields=candidate_fields,
        header=candidate_header,
        rows_payload=rows,
        row_ids=candidate_row_ids,
    )
    _append_edited_ocr_revision(
        order_id=order_id,
        ui_mode="sheet",
        fields=candidate_fields,
        header=candidate_header,
        rows_payload=rows,
        row_ids=candidate_row_ids,
        before_digest=before_digest or after_digest,
        after_digest=after_digest,
        revision_meta={
            "sheet_save_only": True,
            "sheet_save_mode": "draft_candidate",
            "review_state": str(review_state or "draft_ready").strip() or "draft_ready",
            "review_blockers": [str(item).strip() for item in (review_blockers or []) if str(item).strip()],
            "review_warnings": [str(item).strip() for item in (review_warnings or []) if str(item).strip()],
            "draft_from_reparse_reject": True,
            "raw_output_override": raw_output_override,
        },
    )
    persist_sheet_draft(
        order_id=order_id,
        draft_sheet_json={
            "fields": candidate_fields,
            "header": candidate_header,
            "rows": rows,
            "row_ids": candidate_row_ids,
            "ui_mode": "sheet",
        },
        draft_state=str(review_state or "draft_ready").strip() or "draft_ready",
        blockers=[str(item).strip() for item in (review_blockers or []) if str(item).strip()],
        warnings=[str(item).strip() for item in (review_warnings or []) if str(item).strip()],
    )


def _parse_llm_review_confidence(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = float(text)
    except Exception:
        return 0.0
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def _parse_llm_review_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"-?\d+", text):
        return None
    try:
        return int(text)
    except Exception:
        return None


def _merge_ocr_job_metrics(job_id: str, patch: dict[str, Any] | None = None) -> dict[str, Any]:
    current = get_ocr_job(job_id) or {}
    merged = dict(current.get("metrics") or {})
    if isinstance(patch, dict):
        for key, value in patch.items():
            merged[key] = value
    return merged


def _update_reparse_job_progress(
    job_id: str,
    *,
    status: str | None = None,
    processing_stage: str | None = None,
    result_state: str | None = None,
    error_message: str | None = None,
    metrics_patch: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    metrics = _merge_ocr_job_metrics(job_id, metrics_patch)
    if processing_stage is not None:
        metrics["processing_stage"] = str(processing_stage or "").strip() or None
    if result_state is not None:
        metrics["result_state"] = str(result_state or "").strip() or None
    metrics["stage_updated_at"] = datetime.utcnow().isoformat()
    updates: dict[str, Any] = {
        "metrics": metrics,
    }
    if status is not None:
        updates["status"] = status
    if error_message is not None or (status in {"done", "success"} and result_state in {"applied", "draft_ready_blocked"}):
        updates["error_message"] = error_message
    return update_job(job_id, **updates)


def _resolve_llm_review_baseline(
    *,
    order_id: str,
    payload: dict[str, Any],
    template: dict[str, Any],
) -> dict[str, Any]:
    latest_draft = get_latest_sheet_draft(
        order_id,
        backfill_from_revision=False,
        upgrade_generic_from_sheet=True,
    )
    draft_payload = latest_draft.get("draft_sheet_json") if isinstance(latest_draft, dict) else None
    if isinstance(draft_payload, dict):
        fields = [str(field).strip() for field in (draft_payload.get("fields") or []) if str(field).strip()]
        rows = _sanitize_revision_rows(rows_payload=draft_payload.get("rows"), fields=fields)
        row_ids = [str(item).strip() for item in (draft_payload.get("row_ids") or []) if str(item).strip()]
        header = [str(cell or "").strip() for cell in (draft_payload.get("header") or [])]
        if fields and rows:
            if len(row_ids) < len(rows):
                row_ids.extend([f"row-{idx + 1}" for idx in range(len(row_ids), len(rows))])
            if len(header) < len(fields):
                header.extend([_field_label(field) for field in fields[len(header) :]])
            return {
                "fields": fields,
                "header": header,
                "rows": rows,
                "row_ids": row_ids[: len(rows)],
                "baseline_revision_id": str(latest_draft.get("id") or "").strip() or None,
                "raw_output": _snapshot_raw_ocr_payload(payload),
                "baseline_source": "draft",
            }

    edited = payload.get("_edited_ocr")
    latest = edited.get("latest") if isinstance(edited, dict) else None
    if not isinstance(latest, dict):
        latest = _select_order_sheet_revision(order_id=order_id, payload=payload, exact_only=False)
    raw_output = edited.get("raw_output") if isinstance(edited, dict) else None
    if not isinstance(raw_output, dict):
        raw_output = _snapshot_raw_ocr_payload(payload)

    if isinstance(latest, dict):
        latest_llm_review = latest.get("llm_review")
        latest_output_payload = (
            latest_llm_review.get("output_payload")
            if isinstance(latest_llm_review, dict)
            else None
        )
        if isinstance(latest_output_payload, dict):
            raw_output = latest_output_payload
        fields = [str(field).strip() for field in (latest.get("fields") or []) if str(field).strip()]
        rows = _sanitize_revision_rows(rows_payload=latest.get("rows"), fields=fields)
        row_ids = [str(item).strip() for item in (latest.get("row_ids") or []) if str(item).strip()]
        header = [str(cell or "").strip() for cell in (latest.get("header") or [])]
        if len(row_ids) < len(rows):
            row_ids.extend([f"row-{idx + 1}" for idx in range(len(row_ids), len(rows))])
        if len(header) < len(fields):
            header.extend([_field_label(field) for field in fields[len(header) :]])
        return {
            "fields": fields,
            "header": header,
            "rows": rows,
            "row_ids": row_ids[: len(rows)],
            "baseline_revision_id": str(latest.get("revision_id") or "").strip() or None,
            "raw_output": raw_output,
            "baseline_source": "edited",
        }

    fields = _row_fields_from_template(template)
    rows = _extract_first_pass_rows_from_payload(payload, template)
    if not fields:
        width = max((len(row) for row in rows), default=0)
        fields = [f"col{idx + 1}" for idx in range(max(width, 1))]
    header = _sheet_header_from_template(fields, template)
    row_ids = [f"row-{idx + 1}" for idx in range(len(rows))]
    return {
        "fields": fields,
        "header": header,
        "rows": rows,
        "row_ids": row_ids,
        "baseline_revision_id": None,
        "raw_output": raw_output,
        "baseline_source": "yomitoku",
    }


def _build_llm_review_prompt_rows(
    *,
    fields: list[str],
    rows: list[list[str]],
    row_ids: list[str],
) -> list[dict[str, Any]]:
    return ocr_llm_review_service.build_llm_review_prompt_rows(
        fields=fields,
        rows=rows,
        row_ids=row_ids,
    )


def _build_llm_review_payload_rows(
    *,
    fields: list[str],
    rows: list[list[str]],
) -> list[dict[str, str]]:
    return ocr_llm_review_service.build_llm_review_payload_rows(
        fields=fields,
        rows=rows,
    )


def _resolve_llm_review_row_ids(
    *,
    baseline_row_ids: list[str],
    row_count: int,
) -> list[str]:
    return ocr_llm_review_service.resolve_llm_review_row_ids(
        baseline_row_ids=baseline_row_ids,
        row_count=row_count,
    )


def _build_llm_review_response_schema(fields: list[str]) -> dict[str, Any]:
    return ocr_llm_review_service.build_llm_review_response_schema(fields)


def _build_llm_review_prompts(
    *,
    provider: str,
    template: dict[str, Any],
    baseline: dict[str, Any],
    pdf_variant_requested: str = "raw",
    pdf_variant_used: str = "raw",
    pdf_variant_fallback_reason: str | None = None,
    prompt_override: str | None = None,
) -> tuple[str, str]:
    return ocr_llm_review_service.build_llm_review_prompts(
        provider=provider,
        template=template,
        baseline=baseline,
        pdf_variant_requested=pdf_variant_requested,
        pdf_variant_used=pdf_variant_used,
        pdf_variant_fallback_reason=pdf_variant_fallback_reason,
        prompt_override=prompt_override,
        truncate_assist_text=_truncate_assist_text,
        compact_prompt_tables=_compact_prompt_tables,
        compact_prompt_cell_issues=_compact_prompt_cell_issues,
    )


def _extract_llm_review_json_object(raw_text: object) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None

    candidates: list[str] = []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(text)

    start = text.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
                continue
            if char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : idx + 1].strip())
                    break

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_llm_review_json_value(value: object, *, depth: int = 0) -> Any:
    if depth >= 3:
        return _field_value_to_str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        normalized_items: list[Any] = []
        for item in value[:20]:
            normalized_item = _normalize_llm_review_json_value(item, depth=depth + 1)
            if normalized_item is not None:
                normalized_items.append(normalized_item)
        return normalized_items
    if isinstance(value, dict):
        normalized_dict: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:20]:
            key = str(raw_key or "").strip()
            if not key:
                continue
            normalized_value = _normalize_llm_review_json_value(raw_value, depth=depth + 1)
            if normalized_value is None:
                continue
            normalized_dict[key] = normalized_value
        return normalized_dict
    return _field_value_to_str(value)


def _normalize_llm_review_summary(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        normalized = _normalize_llm_review_json_value(value)
        if isinstance(normalized, dict):
            return normalized
    if isinstance(value, str) and value.strip():
        return {"text": value.strip()}
    return {}


def _normalize_llm_review_issue(item: object, *, issue_id: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    row_id = str(item.get("row_id") or "").strip()
    field = str(item.get("field") or "").strip()
    if not row_id or not field:
        return None
    normalized: dict[str, Any] = {
        "issue_id": issue_id,
        "row_id": row_id,
        "field": field,
        "issue_code": str(item.get("issue_code") or item.get("status") or item.get("reason") or "review_required").strip()
        or "review_required",
        "status": str(item.get("status") or "").strip(),
        "current_text": _field_value_to_str(item.get("current_text")),
        "confidence": round(_parse_llm_review_confidence(item.get("confidence")), 4),
        "evidence": str(item.get("evidence") or "").strip(),
        "reason": str(item.get("reason") or "").strip(),
        "severity": str(item.get("severity") or "warning").strip() or "warning",
        "source": "llm_review",
    }
    table_id = str(item.get("table_id") or "").strip()
    if table_id:
        normalized["table_id"] = table_id
    page_index = _parse_llm_review_int(item.get("page_index"))
    if page_index is not None:
        normalized["page_index"] = page_index
    return normalized


def _normalize_llm_review_output_issue(
    item: object,
    *,
    issue_id: str,
    fields: list[str],
    row_ids: list[str],
    current_rows: list[list[str]],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    field = str(item.get("field") or "").strip()
    if not field or field not in fields:
        return None
    row_id = str(item.get("row_id") or "").strip()
    row_index = _parse_llm_review_int(item.get("row_index"))
    if row_index is None:
        row_index = _parse_llm_review_int(item.get("source_row_index"))
    if not row_id and row_index is not None and 0 <= row_index < len(row_ids):
        row_id = row_ids[row_index]
    if not row_id:
        return None
    if row_index is None and row_id in row_ids:
        row_index = row_ids.index(row_id)
    normalized = _normalize_llm_review_issue(
        {
            **item,
            "row_id": row_id,
            "field": field,
        },
        issue_id=issue_id,
    )
    if not isinstance(normalized, dict):
        return None
    if row_index is not None and 0 <= row_index < len(current_rows):
        normalized["row_index"] = int(row_index)
        normalized["source_row_index"] = int(row_index)
        try:
            col_index = fields.index(field)
        except ValueError:
            col_index = None
        if col_index is not None:
            normalized["column_index"] = int(col_index)
            normalized["col_index"] = int(col_index)
            normalized.setdefault("current_text", current_rows[row_index][col_index])
    return normalized


def _normalize_llm_review_overwrite(item: object, *, issue_id: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    row_id = str(item.get("row_id") or "").strip()
    field = str(item.get("field") or "").strip()
    if not row_id or not field:
        return None
    current_text = _field_value_to_str(item.get("current_text"))
    old_text = item.get("old_text")
    normalized: dict[str, Any] = {
        "issue_id": issue_id,
        "row_id": row_id,
        "field": field,
        "status": str(item.get("status") or "").strip(),
        "current_text": current_text,
        "old_text": _field_value_to_str(old_text if old_text is not None else current_text),
        "new_text": _field_value_to_str(item.get("new_text")),
        "confidence": round(_parse_llm_review_confidence(item.get("confidence")), 4),
        "evidence": str(item.get("evidence") or "").strip(),
        "reason": str(item.get("reason") or "").strip(),
        "source": "llm_review",
    }
    table_id = str(item.get("table_id") or "").strip()
    if table_id:
        normalized["table_id"] = table_id
    page_index = _parse_llm_review_int(item.get("page_index"))
    if page_index is not None:
        normalized["page_index"] = page_index
    return normalized


def _parse_llm_review_response(raw_text: object) -> dict[str, Any] | None:
    payload = _extract_llm_review_json_object(raw_text)
    if not isinstance(payload, dict):
        return None

    if any(key in payload for key in ("rows", "tables", "table_raw", "llm_review")) and "overwrites" not in payload:
        llm_review_payload = payload.get("llm_review") if isinstance(payload.get("llm_review"), dict) else {}
        return {
            "summary": _normalize_llm_review_summary(llm_review_payload or payload.get("summary")),
            "issues": [],
            "overwrites": [],
            "output_payload": payload,
        }

    issues_payload = payload.get("issues")
    if isinstance(issues_payload, dict):
        issues_payload = [issues_payload]
    if not isinstance(issues_payload, list):
        issues_payload = []

    overwrites_payload = payload.get("overwrites")
    if isinstance(overwrites_payload, dict):
        overwrites_payload = [overwrites_payload]
    if not isinstance(overwrites_payload, list):
        overwrites_payload = []

    issues: list[dict[str, Any]] = []
    overwrites: list[dict[str, Any]] = []
    generated_issue_seq = 0

    def _next_issue_id(raw_item: object) -> str:
        nonlocal generated_issue_seq
        if isinstance(raw_item, dict):
            existing = str(raw_item.get("issue_id") or "").strip()
            if existing:
                return existing
        generated_issue_seq += 1
        return f"llm-review-{generated_issue_seq}"

    for item in issues_payload:
        normalized = _normalize_llm_review_issue(item, issue_id=_next_issue_id(item))
        if normalized:
            issues.append(normalized)
    for item in overwrites_payload:
        normalized = _normalize_llm_review_overwrite(item, issue_id=_next_issue_id(item))
        if normalized:
            overwrites.append(normalized)

    return {
        "summary": _normalize_llm_review_summary(payload.get("summary")),
        "issues": issues,
        "overwrites": overwrites,
    }


def _prepare_llm_review_output_payload(
    *,
    payload: dict[str, Any],
    baseline: dict[str, Any],
    template: dict[str, Any],
    pdf_variant_requested: str,
    pdf_variant_used: str,
    pdf_variant_fallback_reason: str | None = None,
) -> dict[str, Any] | None:
    fields = [str(field).strip() for field in (baseline.get("fields") or []) if str(field).strip()]
    if not fields:
        return None
    header = [str(cell or "").strip() for cell in (baseline.get("header") or [])]
    if len(header) < len(fields):
        header.extend([_field_label(field) for field in fields[len(header) :]])
    baseline_rows = _sanitize_revision_rows(rows_payload=baseline.get("rows"), fields=fields)
    current_rows = _sanitize_revision_rows(rows_payload=payload.get("rows"), fields=fields)
    if not current_rows:
        current_rows = _extract_first_pass_rows_from_payload(payload, template)
    if not current_rows:
        return None

    normalized_payload = dict(payload)
    normalized_payload["rows"] = _build_llm_review_payload_rows(fields=fields, rows=current_rows)
    if not isinstance(normalized_payload.get("table_raw"), str) or not str(normalized_payload.get("table_raw") or "").strip():
        normalized_payload["table_raw"] = _build_markdown_table_string(header, current_rows)

    existing_tables = _collect_structured_tables_from_payload(normalized_payload)
    if not existing_tables:
        baseline_table = _collect_structured_tables_from_payload(
            baseline.get("raw_output") if isinstance(baseline.get("raw_output"), dict) else {}
        )
        template_table = baseline_table[0] if baseline_table else {}
        table_id = str(template_table.get("table_id") or "").strip() or "llm_review_table_1"
        page_index = _parse_llm_review_int(template_table.get("page_index"))
        normalized_payload["tables"] = [
            {
                "table_id": table_id,
                "page_index": page_index if page_index is not None else 1,
                "rows": [header, *current_rows],
            }
        ]

    llm_review_payload = normalized_payload.get("llm_review")
    llm_review_payload = dict(llm_review_payload) if isinstance(llm_review_payload, dict) else {}
    summary = _normalize_llm_review_summary(llm_review_payload or normalized_payload.get("summary"))
    status = str(
        llm_review_payload.get("status")
        or summary.get("status")
        or summary.get("review_status")
        or "verified"
    ).strip() or "verified"
    notes = str(llm_review_payload.get("notes") or summary.get("notes") or "").strip()
    needs_more_review = _llm_review_summary_needs_more_review(
        {
            **summary,
            "status": status,
            "needs_more_review": llm_review_payload.get("needs_more_review"),
        }
    )

    row_ids = _resolve_llm_review_row_ids(
        baseline_row_ids=[str(item) for item in (baseline.get("row_ids") or [])],
        row_count=len(current_rows),
    )
    raw_issue_candidates: list[object] = []
    for candidate in (
        llm_review_payload.get("issues"),
        normalized_payload.get("issues"),
        normalized_payload.get("cell_issues"),
    ):
        if isinstance(candidate, list):
            raw_issue_candidates.extend(candidate)
        elif isinstance(candidate, dict):
            raw_issue_candidates.append(candidate)
    normalized_issues: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_issue_candidates, start=1):
        normalized = _normalize_llm_review_output_issue(
            item,
            issue_id=f"llm-review-{idx}",
            fields=fields,
            row_ids=row_ids,
            current_rows=current_rows,
        )
        if normalized:
            normalized_issues.append(normalized)

    issue_by_target = {
        (
            str(issue.get("row_id") or "").strip(),
            str(issue.get("field") or "").strip(),
        ): issue
        for issue in normalized_issues
    }
    applied_overwrites: list[dict[str, Any]] = []
    changed_targets: set[tuple[str, str]] = set()
    for row_index, row in enumerate(current_rows):
        row_id = row_ids[row_index] if row_index < len(row_ids) else f"row-{row_index + 1}"
        baseline_row = baseline_rows[row_index] if row_index < len(baseline_rows) else [""] * len(fields)
        for col_index, field in enumerate(fields):
            old_text = baseline_row[col_index] if col_index < len(baseline_row) else ""
            new_text = row[col_index] if col_index < len(row) else ""
            if old_text == new_text:
                continue
            changed_targets.add((row_id, field))
            issue = issue_by_target.get((row_id, field), {})
            overwrite = {
                "issue_id": str(issue.get("issue_id") or f"llm-review-change-{row_index + 1}-{col_index + 1}").strip(),
                "row_id": row_id,
                "row_index": int(row_index),
                "source_row_index": int(row_index),
                "field": field,
                "column_index": int(col_index),
                "col_index": int(col_index),
                "current_text": old_text,
                "old_text": old_text,
                "new_text": new_text,
                "status": str(issue.get("status") or "").strip(),
                "confidence": round(_parse_llm_review_confidence(issue.get("confidence")), 4),
                "evidence": str(issue.get("evidence") or "").strip(),
                "reason": str(issue.get("reason") or "").strip(),
                "source": "llm_review",
            }
            table_id = str(issue.get("table_id") or "").strip()
            if table_id:
                overwrite["table_id"] = table_id
            page_index = _parse_llm_review_int(issue.get("page_index"))
            if page_index is not None:
                overwrite["page_index"] = page_index
            applied_overwrites.append(overwrite)

    unresolved_issues = [
        issue
        for issue in normalized_issues
        if (str(issue.get("row_id") or "").strip(), str(issue.get("field") or "").strip()) not in changed_targets
    ]
    needs_more_review = bool(needs_more_review or unresolved_issues)
    llm_review_payload.update(
        {
            "status": status,
            "needs_more_review": needs_more_review,
            "notes": notes,
            "issues": unresolved_issues,
            "pdf_variant_requested": pdf_variant_requested,
            "pdf_variant_used": pdf_variant_used,
        }
    )
    if pdf_variant_fallback_reason:
        llm_review_payload["pdf_variant_fallback_reason"] = pdf_variant_fallback_reason
    normalized_payload["cell_issues"] = unresolved_issues
    normalized_payload["llm_review"] = llm_review_payload

    return {
        "summary": {
            **summary,
            "status": status,
            "needs_more_review": needs_more_review,
            "notes": notes,
        },
        "issues": unresolved_issues,
        "applied_overwrites": applied_overwrites,
        "rejected_overwrites": [],
        "rows": current_rows,
        "row_ids": row_ids,
        "output_payload": normalized_payload,
        "needs_more_review": needs_more_review,
    }


def _llm_review_summary_needs_more_review(summary: dict[str, Any] | None) -> bool:
    if not isinstance(summary, dict):
        return False
    direct = summary.get("needs_more_review")
    if isinstance(direct, bool):
        return direct
    status = str(summary.get("status") or summary.get("review_status") or "").strip().lower()
    return status in {"needs_review", "needs_more_review", "review_required", "unresolved"}


def _apply_llm_review_overwrites(
    *,
    fields: list[str],
    rows: list[list[str]],
    row_ids: list[str],
    issues: list[dict[str, Any]],
    overwrites: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_fields = [str(field).strip() for field in fields if str(field).strip()]
    if not normalized_fields:
        return {
            "rows": [],
            "applied_overwrites": [],
            "rejected_overwrites": [],
            "issues": list(issues or []),
            "needs_more_review": bool(issues),
        }

    updated_rows: list[list[str]] = []
    for row in rows:
        current = [_field_value_to_str(cell) for cell in list(row)[: len(normalized_fields)]]
        if len(current) < len(normalized_fields):
            current.extend([""] * (len(normalized_fields) - len(current)))
        updated_rows.append(current)

    resolved_row_ids: list[str] = []
    for idx in range(len(updated_rows)):
        row_id = str(row_ids[idx] if idx < len(row_ids) else "").strip()
        resolved_row_ids.append(row_id or f"row-{idx + 1}")

    row_index_by_id = {row_id: idx for idx, row_id in enumerate(resolved_row_ids) if row_id}
    field_index = {
        field: idx
        for idx, field in enumerate(normalized_fields)
        if field
    }
    min_confidence = _read_reparse_float_env(
        "OCR_LLM_REVIEW_OVERWRITE_MIN_CONFIDENCE",
        0.75,
        min_value=0.0,
    )
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    applied_issue_ids: set[str] = set()
    applied_targets: set[tuple[str, str]] = set()

    def _issue_with_mapping(item: dict[str, Any], *, reject_reason: str | None = None) -> dict[str, Any]:
        normalized = dict(item)
        row_id = str(item.get("row_id") or "").strip()
        field = str(item.get("field") or "").strip()
        target_row_index = row_index_by_id.get(row_id)
        target_col_index = field_index.get(field)
        if target_row_index is not None:
            normalized["source_row_index"] = int(target_row_index)
            normalized["row_index"] = int(target_row_index)
        if target_col_index is not None:
            normalized["column_index"] = int(target_col_index)
            normalized["col_index"] = int(target_col_index)
        if target_row_index is not None and target_col_index is not None:
            current_value = updated_rows[target_row_index][target_col_index]
            normalized.setdefault("current_text", current_value)
        if reject_reason:
            normalized["reject_reason"] = reject_reason
            normalized["issue_code"] = "overwrite_rejected"
            normalized.setdefault("reason", reject_reason)
            normalized.setdefault("severity", "warning")
        return normalized

    for overwrite in overwrites:
        row_id = str(overwrite.get("row_id") or "").strip()
        field = str(overwrite.get("field") or "").strip()
        reject_reason = ""
        target_row_index = row_index_by_id.get(row_id)
        if target_row_index is None:
            reject_reason = "row_id_not_found"
        target_col_index = field_index.get(field)
        if not reject_reason and target_col_index is None:
            reject_reason = "field_not_found"
        confidence = _parse_llm_review_confidence(overwrite.get("confidence"))
        if not reject_reason and confidence < min_confidence:
            reject_reason = "low_confidence"
        evidence = str(overwrite.get("evidence") or "").strip()
        if not reject_reason and not evidence:
            reject_reason = "missing_evidence"
        current_value = ""
        if not reject_reason and target_row_index is not None and target_col_index is not None:
            current_value = updated_rows[target_row_index][target_col_index]
            old_text = _field_value_to_str(overwrite.get("old_text"))
            if old_text != current_value:
                reject_reason = "old_text_mismatch"
            new_text = _field_value_to_str(overwrite.get("new_text"))
            if not reject_reason and field.startswith("qty.") and new_text and not re.fullmatch(r"\d+", new_text):
                reject_reason = "invalid_quantity_text"
        if reject_reason:
            rejected.append(_issue_with_mapping(overwrite, reject_reason=reject_reason))
            continue
        new_text = _field_value_to_str(overwrite.get("new_text"))
        updated_rows[target_row_index][target_col_index] = new_text
        applied_item = _issue_with_mapping(overwrite)
        applied_item["old_text"] = current_value
        applied_item["new_text"] = new_text
        applied.append(applied_item)
        issue_id = str(overwrite.get("issue_id") or "").strip()
        if issue_id:
            applied_issue_ids.add(issue_id)
        applied_targets.add((row_id, field))

    unresolved_issues: list[dict[str, Any]] = []
    for issue in issues:
        issue_id = str(issue.get("issue_id") or "").strip()
        issue_target = (
            str(issue.get("row_id") or "").strip(),
            str(issue.get("field") or "").strip(),
        )
        if issue_id and issue_id in applied_issue_ids:
            continue
        if issue_target in applied_targets:
            continue
        unresolved_issues.append(_issue_with_mapping(issue))
    unresolved_issues.extend(rejected)

    return {
        "rows": updated_rows,
        "row_ids": resolved_row_ids,
        "applied_overwrites": applied,
        "rejected_overwrites": rejected,
        "issues": unresolved_issues,
        "needs_more_review": bool(unresolved_issues),
    }


def _extract_corrected_pdf_uri_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates: list[dict[str, Any]] = [payload]
    edited = payload.get("_edited_ocr")
    if isinstance(edited, dict):
        raw_output = edited.get("raw_output")
        if isinstance(raw_output, dict):
            candidates.append(raw_output)
        latest = edited.get("latest")
        if isinstance(latest, dict):
            llm_review = latest.get("llm_review")
            if isinstance(llm_review, dict):
                output_payload = llm_review.get("output_payload")
                if isinstance(output_payload, dict):
                    candidates.append(output_payload)
    for candidate in candidates:
        direct_uri = candidate.get("corrected_pdf_uri")
        if isinstance(direct_uri, str) and direct_uri.strip():
            return direct_uri.strip()
        combined = candidate.get("combined")
        if isinstance(combined, dict):
            combined_uri = combined.get("corrected_pdf")
            if isinstance(combined_uri, str) and combined_uri.strip():
                return combined_uri.strip()
        page_correction = candidate.get("page_correction")
        if isinstance(page_correction, dict):
            correction_uri = page_correction.get("corrected_pdf_uri")
            if isinstance(correction_uri, str) and correction_uri.strip():
                return correction_uri.strip()
    return None


def _resolve_llm_review_pdf_bytes(
    *,
    document_uri: str,
    payload: dict[str, Any] | None = None,
    requested_variant: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    requested = str(requested_variant or "raw").strip().lower() or "raw"
    used = "raw"
    fallback_reason: str | None = None
    if requested not in {"raw", "corrected"}:
        requested = "raw"
    if requested == "corrected":
        corrected_pdf_uri = _extract_corrected_pdf_uri_from_payload(payload)
        if corrected_pdf_uri:
            try:
                corrected_pdf_bytes = load_bytes_from_uri(corrected_pdf_uri)
                return corrected_pdf_bytes, {
                    "requested": requested,
                    "used": "corrected",
                    "fallback_reason": None,
                    "corrected_pdf_uri": corrected_pdf_uri,
                }
            except Exception:
                fallback_reason = "corrected_pdf_load_failed"
        else:
            fallback_reason = "corrected_pdf_unavailable_in_backend_cache"
    pdf_bytes = load_bytes_from_uri(document_uri)
    return pdf_bytes, {
        "requested": requested,
        "used": used,
        "fallback_reason": fallback_reason,
    }


def _resolve_reparse_llm_pdf_bytes(
    *,
    document_uri: str,
    payload: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    return _resolve_llm_review_pdf_bytes(
        document_uri=document_uri,
        payload=payload,
        requested_variant="corrected",
    )


def _png_data_uri(png_bytes: bytes) -> str:
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def review_ocr_table_with_llm(
    order_id: str,
    *,
    provider: str | None = None,
    prompt: str | None = None,
    pdf_variant: str | None = None,
):
    config_service.reload_configs()
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        if not order.facility_code:
            return None, "facility_missing"
        if not order.document_uri:
            return None, "document_missing"
        facility_id = order.facility_code
        document_uri = order.document_uri

    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility_config = None
    try:
        facility_config = config_service.get_facility_config(facility_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Facility config lookup failed", facility_id=facility_id, error=str(exc))
    if not facility_config:
        facility_config = next(
            (
                fac
                for fac in master.get("facilities", [])
                if fac.get("facility_id") == facility_id
            ),
            None,
        )
    if not facility_config:
        return None, "facility_not_found"

    template = facility_config.get("fax_template") or config_service._merge_template(
        base_template,
        facility_config.get("fax_template_override"),
    )
    payload = _load_order_ocr_cache(order_id)
    if not isinstance(payload, dict):
        return None, "ocr_payload_missing"

    baseline = _resolve_llm_review_baseline(order_id=order_id, payload=payload, template=template)
    baseline_rows = baseline.get("rows") if isinstance(baseline, dict) else []
    baseline_fields = baseline.get("fields") if isinstance(baseline, dict) else []
    baseline_row_ids = baseline.get("row_ids") if isinstance(baseline, dict) else []
    baseline_header = baseline.get("header") if isinstance(baseline, dict) else []
    if not isinstance(baseline_rows, list) or not baseline_rows:
        return None, "rows_empty"
    if not isinstance(baseline_fields, list) or not baseline_fields:
        return None, "fields_empty"

    resolved_provider = _normalize_reparse_provider(provider)
    effective_provider = (
        resolved_provider
        or str(template.get("main_ocr_provider") or os.getenv("OCR_MAIN_PROVIDER") or "gemini").strip().lower()
    )
    if effective_provider not in {"openai", "gemini"}:
        effective_provider = "gemini"

    pdf_bytes, pdf_variant_meta = _resolve_llm_review_pdf_bytes(
        document_uri=document_uri,
        payload=payload,
        requested_variant=pdf_variant,
    )
    system_prompt, user_prompt = _build_llm_review_prompts(
        provider=effective_provider,
        template=template,
        baseline=baseline,
        pdf_variant_requested=str(pdf_variant_meta.get("requested") or "raw"),
        pdf_variant_used=str(pdf_variant_meta.get("used") or "raw"),
        pdf_variant_fallback_reason=(
            str(pdf_variant_meta.get("fallback_reason")).strip()
            if pdf_variant_meta.get("fallback_reason")
            else None
        ),
        prompt_override=prompt,
    )
    review_template = dict(template)
    review_template["main_ocr_provider"] = effective_provider
    review_template["_force_main_ocr_provider"] = effective_provider
    review_template["llm_quantity_only_mode"] = False
    review_template["main_ocr_row_fields"] = baseline_fields
    if effective_provider == "openai":
        review_template["openai_ocr_enabled"] = True
        review_template["openai_ocr_fallback_provider"] = "disabled"
        review_template["openai_ocr_prompt"] = system_prompt
        review_template["openai_ocr_user_prompt"] = user_prompt
    else:
        review_template["gemini_ocr_enabled"] = True
        review_template["gemini_ocr_fallback_provider"] = "disabled"
        review_template["gemini_ocr_prompt"] = system_prompt
        review_template["gemini_ocr_user_prompt"] = user_prompt
        review_template["gemini_ocr_response_schema"] = _build_llm_review_response_schema(
            [str(field) for field in baseline_fields]
        )

    extracted = extract_fax_data(
        pdf_bytes,
        review_template,
        facility_id=facility_id,
    )
    parsed_review = _parse_llm_review_response(getattr(extracted, "raw_text", None))
    if not isinstance(parsed_review, dict):
        return None, "llm_review_invalid_json"
    provider_debug = extracted.provider_debug if isinstance(extracted.provider_debug, dict) else {}
    model_name = str(provider_debug.get("model") or "").strip() or None
    prepared_payload = None
    if isinstance(parsed_review.get("output_payload"), dict):
        prepared_payload = _prepare_llm_review_output_payload(
            payload=parsed_review["output_payload"],
            baseline=baseline,
            template=template,
            pdf_variant_requested=str(pdf_variant_meta.get("requested") or "raw"),
            pdf_variant_used=str(pdf_variant_meta.get("used") or "raw"),
            pdf_variant_fallback_reason=(
                str(pdf_variant_meta.get("fallback_reason")).strip()
                if pdf_variant_meta.get("fallback_reason")
                else None
            ),
        )
        if prepared_payload is None:
            return None, "llm_review_invalid_json"
    if prepared_payload is None:
        prepared_payload = _apply_llm_review_overwrites(
            fields=baseline_fields,
            rows=[list(row) for row in baseline_rows if isinstance(row, list)],
            row_ids=[str(item) for item in baseline_row_ids],
            issues=parsed_review.get("issues") or [],
            overwrites=parsed_review.get("overwrites") or [],
        )
        prepared_payload["summary"] = parsed_review.get("summary") or {}
        prepared_payload["output_payload"] = None
    needs_more_review = bool(
        prepared_payload.get("needs_more_review")
        or _llm_review_summary_needs_more_review(prepared_payload.get("summary"))
    )
    review_mode = "llm_yomitoku_payload_review" if prepared_payload.get("output_payload") else "llm_verify_overwrite"
    review_meta = {
        "review_mode": review_mode,
        "llm_review": {
            "provider": effective_provider,
            "model": model_name,
            "summary": prepared_payload.get("summary") or {},
            "baseline_source": baseline.get("baseline_source"),
            "baseline_revision_id": baseline.get("baseline_revision_id"),
            "issues": prepared_payload.get("issues") or [],
            "proposed_overwrites": prepared_payload.get("applied_overwrites") or parsed_review.get("overwrites") or [],
            "applied_overwrites": prepared_payload.get("applied_overwrites") or [],
            "rejected_overwrites": prepared_payload.get("rejected_overwrites") or [],
            "applied_count": len(prepared_payload.get("applied_overwrites") or []),
            "rejected_count": len(prepared_payload.get("rejected_overwrites") or []),
            "needs_more_review": needs_more_review,
            "pdf_variant_requested": str(pdf_variant_meta.get("requested") or "raw"),
            "pdf_variant_used": str(pdf_variant_meta.get("used") or "raw"),
        },
    }
    if pdf_variant_meta.get("fallback_reason"):
        review_meta["llm_review"]["pdf_variant_fallback_reason"] = str(pdf_variant_meta["fallback_reason"])
    if isinstance(prepared_payload.get("output_payload"), dict):
        review_meta["llm_review"]["output_payload"] = prepared_payload["output_payload"]
    latest_draft = get_latest_sheet_draft(order_id, backfill_from_revision=True)
    candidate_rows = prepared_payload.get("rows") or baseline_rows
    candidate_row_ids = prepared_payload.get("row_ids") or baseline_row_ids
    candidate_sheet_json = {
        "fields": list(baseline_fields),
        "header": list(baseline_header),
        "rows": [list(row) for row in candidate_rows if isinstance(row, list)],
        "row_ids": [str(item) for item in candidate_row_ids],
        "ui_mode": "sheet",
        "source": "llm_patch_candidate",
        "warnings": ["llm_patch_needs_review"] if needs_more_review else [],
    }
    latest_evidence = get_latest_ocr_evidence_run(order_id, backfill_from_cache=True)
    patch_candidate = patch_candidate_service.persist_patch_candidate(
        order_id=order_id,
        base_draft_id=(latest_draft or {}).get("id") if isinstance(latest_draft, dict) else None,
        base_evidence_run_id=(latest_evidence or {}).get("id") if isinstance(latest_evidence, dict) else None,
        provider=effective_provider,
        model=model_name,
        prompt_preset=None,
        baseline_source=review_meta["llm_review"].get("baseline_source"),
        baseline_revision_id=review_meta["llm_review"].get("baseline_revision_id"),
        candidate_state="proposed",
        summary_json={
            "llm_review": review_meta["llm_review"],
            "pdf_variant_requested": str(pdf_variant_meta.get("requested") or "raw"),
            "pdf_variant_used": str(pdf_variant_meta.get("used") or "raw"),
            "needs_more_review": needs_more_review,
        },
        issues_json=list(review_meta["llm_review"].get("issues") or []),
        patches_json={
            "applied_overwrites": prepared_payload.get("applied_overwrites") or [],
            "rejected_overwrites": prepared_payload.get("rejected_overwrites") or [],
        },
        proposed_draft_sheet_json=candidate_sheet_json,
    )
    if patch_candidate is None:
        return None, "llm_patch_candidate_persist_failed"
    try:
        workflow_state_service.refresh_workflow_state(order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow state refresh failed after LLM review candidate persist", order_id=order_id, error=str(exc))
    return {
        "id": order_id,
        "patch_candidate": patch_candidate,
        "latest_patch_candidate_id": patch_candidate.get("id"),
        "draft": get_latest_sheet_draft(order_id, backfill_from_revision=False),
        "workflow_state": get_order_workflow_state(order_id, refresh=False),
        "llm_review": review_meta["llm_review"],
    }, None


def _signed_url_from_uri(uri: str | None) -> str | None:
    if not uri or not isinstance(uri, str):
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "gs":
        return uri
    bucket = parsed.netloc
    object_path = parsed.path.lstrip("/")
    if not bucket or not object_path:
        return uri
    try:
        return generate_signed_url(bucket, object_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Signed URL generation failed", uri=uri, error=str(exc))
        return uri


def _load_job_output(job: dict | None, label: str) -> Optional[dict]:
    if not job:
        return None
    output_ref = job.get("output_reference")
    if not output_ref:
        return None
    try:
        payload = load_bytes_from_uri(output_ref)
        parsed = json.loads(payload.decode("utf-8"))
        if isinstance(parsed, dict) and _output_is_pending(parsed):
            chained_ref = parsed.get("output_reference")
            if isinstance(chained_ref, str) and chained_ref and chained_ref != output_ref:
                chained = _load_pipeline_output_with_retry(chained_ref)
                if isinstance(chained, dict):
                    return chained
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "OCR output load failed",
            job_id=job.get("id"),
            output_reference=output_ref,
            label=label,
            error=str(exc),
        )
        return _load_pipeline_output_with_retry(output_ref)


def _job_is_pending(job: dict | None) -> bool:
    if not job:
        return False
    status = str(job.get("status") or "").lower()
    if status in {"running", "pending", "queued"}:
        return True
    error = job.get("error_message")
    if isinstance(error, str) and "read operation timed out" in error.lower():
        return True
    return False


def _output_is_pending(parsed: dict | None) -> bool:
    if not isinstance(parsed, dict):
        return False
    status = str(parsed.get("status") or "").lower()
    stage = str(parsed.get("stage") or "").lower()
    if status in {"running", "pending", "queued"}:
        return True
    if stage in {"upload", "running"}:
        return True
    return False


def _payload_has_first_pass_ocr_content(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    table_raw = payload.get("table_raw")
    if isinstance(table_raw, str) and table_raw.strip():
        return True
    for key in ("pages", "rows", "table_rows", "tables"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _payload_has_page_artifacts(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    pages = payload.get("pages")
    return isinstance(pages, list) and bool(pages)


def _attach_facility_candidates(parsed: dict) -> dict:
    if not isinstance(parsed, dict):
        return parsed
    candidate_texts: list[str] = []
    table_raw = parsed.get("table_raw")
    if isinstance(table_raw, str) and table_raw.strip():
        candidate_texts.append(table_raw)
    pages = parsed.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            markdown_text = page.get("markdown_text")
            if isinstance(markdown_text, str) and markdown_text.strip():
                candidate_texts.append(markdown_text)
    if not candidate_texts:
        return parsed
    candidates = config_service.match_facility_candidates("\n".join(candidate_texts))
    if not candidates:
        return parsed
    enriched = dict(parsed)
    enriched["facility_candidates"] = candidates
    return enriched


def _normalize_debug_timestamp(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _sync_reparse_debug_from_job_metrics(
    parsed: dict[str, Any],
    job: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(parsed, dict) or not isinstance(job, dict):
        return parsed, False
    metrics = job.get("metrics")
    if not isinstance(metrics, dict):
        return parsed, False
    if not any(
        key in metrics
        for key in (
            "before_count",
            "after_count",
            "changed",
            "requested_provider",
            "llm_assist",
            "processing_stage",
            "result_state",
            "confirmed_lines_retained",
        )
    ):
        return parsed, False

    provider_raw = metrics.get("provider") or metrics.get("requested_provider")
    provider = str(provider_raw or "").strip()
    if not provider:
        return parsed, False
    job_status = str(job.get("status") or "").strip().lower()

    debug = parsed.get("_reparse_debug")
    next_debug = dict(debug) if isinstance(debug, dict) else {}
    changed = False

    def _set_value(key: str, value: object) -> None:
        nonlocal changed
        if next_debug.get(key) != value:
            next_debug[key] = value
            changed = True

    updated_at = _normalize_debug_timestamp(job.get("updated_at"))
    if updated_at:
        _set_value("updated_at", updated_at)
    _set_value("provider", provider)

    requested_provider = metrics.get("requested_provider")
    if isinstance(requested_provider, str) and requested_provider.strip():
        _set_value("requested_provider", requested_provider.strip())
    if isinstance(metrics.get("llm_assist"), bool):
        _set_value("llm_assist", bool(metrics.get("llm_assist")))
    if isinstance(metrics.get("changed"), bool):
        _set_value("changed", bool(metrics.get("changed")))
    if isinstance(metrics.get("pdf_variant_used"), str) and metrics.get("pdf_variant_used").strip():
        _set_value("pdf_variant_used", metrics.get("pdf_variant_used").strip())
    if isinstance(metrics.get("pdf_variant_fallback_reason"), str):
        normalized_pdf_variant_reason = metrics.get("pdf_variant_fallback_reason").strip() or None
        _set_value("pdf_variant_fallback_reason", normalized_pdf_variant_reason)
    if isinstance(metrics.get("finish_reason"), str) and metrics.get("finish_reason").strip():
        _set_value("finish_reason", metrics.get("finish_reason").strip())
    if isinstance(metrics.get("truncated_output"), bool):
        _set_value("truncated_output", bool(metrics.get("truncated_output")))
    if isinstance(metrics.get("rows_replaced_with_pipeline"), bool):
        _set_value("rows_replaced_with_pipeline", bool(metrics.get("rows_replaced_with_pipeline")))
    if isinstance(metrics.get("processing_stage"), str):
        normalized_stage = metrics.get("processing_stage").strip().lower() or None
        _set_value("processing_stage", normalized_stage)
    if isinstance(metrics.get("result_state"), str):
        normalized_result_state = metrics.get("result_state").strip().lower() or None
        _set_value("result_state", normalized_result_state)
    if isinstance(metrics.get("confirmed_lines_retained"), bool):
        _set_value("confirmed_lines_retained", bool(metrics.get("confirmed_lines_retained")))
    if isinstance(metrics.get("error"), str):
        normalized_error = metrics.get("error").strip() or None
        _set_value("error", normalized_error)
    elif job_status in {"done", "success"} and next_debug.get("error") not in {None, ""}:
        _set_value("error", None)
    reject_reasons = metrics.get("reject_reasons")
    if isinstance(reject_reasons, list):
        normalized_reasons = [str(item).strip() for item in reject_reasons if str(item).strip()]
        _set_value("reject_reasons", normalized_reasons[:20])
    elif job_status in {"done", "success"} and "reject_reasons" in next_debug:
        _set_value("reject_reasons", [])
    validation_detail = metrics.get("validation_detail")
    if "validation_detail" in metrics:
        normalized_validation_detail = validation_detail if isinstance(validation_detail, dict) else {}
        _set_value("validation_detail", normalized_validation_detail)
    elif job_status in {"done", "success"} and "validation_detail" in next_debug:
        _set_value("validation_detail", {})
    warning_reasons = metrics.get("warning_reasons")
    if isinstance(warning_reasons, list):
        normalized_warning_reasons = [str(item).strip() for item in warning_reasons if str(item).strip()]
        _set_value("warning_reasons", normalized_warning_reasons[:20])
    elif job_status in {"done", "success"} and "warning_reasons" in next_debug:
        _set_value("warning_reasons", [])
    warning_detail = metrics.get("warning_detail")
    if "warning_detail" in metrics:
        normalized_warning_detail = warning_detail if isinstance(warning_detail, dict) else {}
        _set_value("warning_detail", normalized_warning_detail)
    elif job_status in {"done", "success"} and "warning_detail" in next_debug:
        _set_value("warning_detail", {})
    llm_quantity_only_merge = metrics.get("llm_quantity_only_merge")
    if isinstance(llm_quantity_only_merge, dict) and llm_quantity_only_merge:
        _set_value("llm_quantity_only_merge", llm_quantity_only_merge)

    for key in ("row_count", "line_count", "before_count", "after_count"):
        value = metrics.get(key)
        if isinstance(value, (int, float)):
            _set_value(key, int(value))

    if not changed:
        return parsed, False

    enriched = dict(parsed)
    enriched["_reparse_debug"] = next_debug
    return enriched, True


def get_ocr_output(order_id: str, *, persist_cache: bool = True):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        message_id = order.message_id
    active_evidence_payload = None
    active_evidence_run = None
    active_evidence_payload, active_evidence_run = _load_active_ocr_payload(order_id)
    job = get_ocr_job(f"OCR-{order_id}")
    parsed = None
    parsed_source = ""
    order_job_pending = _job_is_pending(job)
    if _payload_has_first_pass_ocr_content(active_evidence_payload):
        parsed = active_evidence_payload
        parsed_source = "active_evidence"
    else:
        parsed = _load_job_output(job, "order")
        parsed_source = "job"
        order_job_pending = _job_is_pending(job) or _output_is_pending(parsed)
        if _output_is_pending(parsed):
            parsed = None
        elif not _payload_has_first_pass_ocr_content(parsed):
            parsed = None
    fallback_job = None
    if (
        message_id
        and not order_job_pending
        and parsed is None
    ):
        fallback_job = get_ocr_job(f"OCR-{message_id}")
        fallback_parsed = _load_job_output(fallback_job, "message")
        if _output_is_pending(fallback_parsed):
            fallback_parsed = None
        if _payload_has_first_pass_ocr_content(fallback_parsed):
            parsed = fallback_parsed
            parsed_source = "message"
    if parsed is None:
        parsed = _load_order_ocr_cache(order_id)
        parsed_source = "cache"
    if parsed is None:
        active_job = job or fallback_job
        if not active_job:
            return None, "ocr_job_not_found"
        if order_job_pending:
            return None, "ocr_output_pending"
        if _job_is_pending(active_job):
            return None, "ocr_output_pending"
        if active_job.get("output_reference"):
            return None, "ocr_output_invalid"
        return None, "ocr_output_not_found"
    if isinstance(parsed, dict):
        parsed = _sanitize_payload_table_raw(parsed)
        parsed = evidence_manifest_service.ensure_evidence_manifest(parsed)
    if persist_cache and not _output_is_pending(parsed):
        _save_order_ocr_cache(order_id, parsed)
    cached_payload = _load_order_ocr_cache(order_id)
    if isinstance(cached_payload, dict):
        cached_payload = evidence_manifest_service.ensure_evidence_manifest(cached_payload)
        enriched = dict(parsed) if isinstance(parsed, dict) else {}
        merged = False
        if parsed_source != "active_evidence":
            edited = cached_payload.get("_edited_ocr")
            if isinstance(edited, dict) and "_edited_ocr" not in enriched:
                enriched["_edited_ocr"] = edited
                merged = True
        reparse_debug = cached_payload.get("_reparse_debug")
        if isinstance(reparse_debug, dict) and "_reparse_debug" not in enriched:
            enriched["_reparse_debug"] = reparse_debug
            merged = True
        if merged:
            parsed = enriched
    metrics_job = job or fallback_job
    if isinstance(parsed, dict):
        parsed, synced = _sync_reparse_debug_from_job_metrics(parsed, metrics_job)
        if synced and persist_cache and not _output_is_pending(parsed):
            _save_order_ocr_cache(order_id, parsed)
    parsed = _attach_edited_ocr_payload(parsed)
    parsed = evidence_manifest_service.ensure_evidence_manifest(parsed)
    return _attach_facility_candidates(parsed), None


def _build_synthetic_ocr_pages(
    *,
    document_uri: str,
    payload: dict[str, Any] | None = None,
) -> tuple[list[dict[str, object]], dict[str, Any]] | tuple[None, None]:
    try:
        pdf_bytes, pdf_variant_meta = _resolve_reparse_llm_pdf_bytes(
            document_uri=document_uri,
            payload=payload,
        )
        png_bytes = render_pdf_to_png_bytes(
            pdf_bytes=pdf_bytes,
            dpi=220,
            page=1,
            # Synthetic preview is for operator guidance, not OCR quality. Cap pixel count
            # so oversized corrected PDFs cannot OOM the request path.
            max_pixels=18_000_000,
        )
    except Exception:
        return None, None
    markdown_text = None
    if isinstance(payload, dict):
        table_raw = payload.get("table_raw")
        if isinstance(table_raw, str) and table_raw.strip():
            markdown_text = table_raw.strip()
    pages = [
        {
            "page_index": 1,
            "markdown_uri": None,
            "markdown_text": markdown_text,
            "ocr_overlay_uri": None,
            "ocr_overlay_url": _png_data_uri(png_bytes),
            "layout_overlay_uri": None,
            "layout_overlay_url": None,
            "figure_uris": [],
            "figure_urls": [],
            "synthetic": True,
            "synthetic_source": "pdf_render",
            "pdf_variant_used": str(pdf_variant_meta.get("used") or "raw"),
        }
    ]
    return pages, pdf_variant_meta


def get_ocr_pages(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        message_id = order.message_id
        facility_id = order.facility_code
        document_uri = order.document_uri
    active_evidence_payload = None
    job = get_ocr_job(f"OCR-{order_id}")
    parsed = _load_job_output(job, "order")
    parsed_source = "job"
    order_job_pending = _job_is_pending(job) or _output_is_pending(parsed)
    if _output_is_pending(parsed):
        parsed = None
    elif not _payload_has_page_artifacts(parsed):
        parsed = None
    fallback_job = None
    if (
        message_id
        and not order_job_pending
        and parsed is None
    ):
        fallback_job = get_ocr_job(f"OCR-{message_id}")
        fallback_parsed = _load_job_output(fallback_job, "message")
        if _output_is_pending(fallback_parsed):
            fallback_parsed = None
        if _payload_has_page_artifacts(fallback_parsed):
            parsed = fallback_parsed
            parsed_source = "message"
            if isinstance(parsed, dict) and not _output_is_pending(parsed):
                _save_order_ocr_cache(order_id, parsed)
    if parsed is None:
        active_evidence_payload, _active_evidence_run = _load_active_ocr_payload(order_id)
        if _payload_has_page_artifacts(active_evidence_payload):
            parsed = active_evidence_payload
            parsed_source = "active_evidence"
    if parsed is None:
        parsed = _load_order_ocr_cache(order_id)
        parsed_source = "cache"
    if parsed is not None and parsed_source != "cache":
        pages_payload = parsed.get("pages")
        if not isinstance(pages_payload, list):
            cached = _load_order_ocr_cache(order_id)
            if cached is not None:
                parsed = cached
                parsed_source = "cache"
    if parsed is None:
        active_job = job or fallback_job
        if order_job_pending:
            return None, "ocr_output_pending"
        if active_job and _job_is_pending(active_job):
            return None, "ocr_output_pending"
        if not document_uri:
            if not active_job:
                return None, "ocr_job_not_found"
            if active_job.get("output_reference"):
                return None, "ocr_output_invalid"
            return None, "ocr_output_not_found"
    if isinstance(parsed, dict) and not _output_is_pending(parsed):
        parsed = evidence_manifest_service.ensure_evidence_manifest(parsed)
        _save_order_ocr_cache(order_id, parsed)
    pages_payload = parsed.get("pages")
    pages: list[dict[str, object]] = []
    if isinstance(pages_payload, list):
        for page in pages_payload:
            if not isinstance(page, dict):
                continue
            markdown_text = None
            markdown_uri = page.get("markdown_uri")
            if isinstance(markdown_uri, str):
                try:
                    markdown_text = load_bytes_from_uri(markdown_uri).decode("utf-8")
                except Exception:  # noqa: BLE001
                    markdown_text = None
            ocr_overlay_uri = page.get("ocr_overlay_uri")
            layout_overlay_uri = page.get("layout_overlay_uri")
            figure_uris = page.get("figure_uris") if isinstance(page.get("figure_uris"), list) else []
            figure_urls = [_signed_url_from_uri(uri) for uri in figure_uris]
            if markdown_text and figure_uris:
                for uri, signed in zip(figure_uris, figure_urls):
                    if uri and signed:
                        markdown_text = markdown_text.replace(uri, signed)
            pages.append(
                {
                    "page_index": page.get("page_index"),
                    "markdown_uri": markdown_uri,
                    "markdown_text": markdown_text,
                    "ocr_overlay_uri": ocr_overlay_uri,
                    "ocr_overlay_url": _signed_url_from_uri(ocr_overlay_uri),
                    "layout_overlay_uri": layout_overlay_uri,
                    "layout_overlay_url": _signed_url_from_uri(layout_overlay_uri),
                    "figure_uris": figure_uris,
                    "figure_urls": figure_urls,
                    "synthetic": bool(page.get("synthetic")) if "synthetic" in page else None,
                    "synthetic_source": page.get("synthetic_source"),
                    "pdf_variant_used": page.get("pdf_variant_used"),
                }
            )
    evidence_missing = _ocr_evidence_missing_artifacts(parsed if isinstance(parsed, dict) else None)
    if "overlay_pages" in evidence_missing or not pages:
        return None, "ocr_evidence_recovery_required"
    if isinstance(parsed, dict) and parsed_source != "cache":
        _save_order_ocr_cache(order_id, parsed)
    combined = parsed.get("combined") if isinstance(parsed.get("combined"), dict) else {}
    combined_urls = {
        key: _signed_url_from_uri(value) for key, value in combined.items() if isinstance(value, str)
    }
    if document_uri and "raw_pdf" not in combined_urls:
        signed_raw_pdf = _signed_url_from_uri(document_uri)
        if signed_raw_pdf:
            combined_urls["raw_pdf"] = signed_raw_pdf
    table_box = None
    table_units = None
    grid_column_edges = None
    grid_row_edges = None
    grid_detection_status = "ready"
    grid_detection_deferred_reason = None
    grid_params = {
        "grid_dpi": 300,
        "grid_line_scale": 30,
        "grid_line_scale_horizontal": 30,
        "grid_line_scale_vertical": 30,
        "grid_line_min_ratio": 0.6,
        "grid_line_merge_gap": 2,
        "grid_line_merge_tolerance": 0.02,
        "grid_expected_columns": 0,
        "grid_qty_gap_tolerance": 0.02,
        "grid_left_date_ratio": 0.2,
    }
    template = None
    if isinstance(facility_id, str) and facility_id:
        try:
            fac_config = config_service.get_facility_config(facility_id)
        except Exception:  # noqa: BLE001
            fac_config = None
        if fac_config:
            template = fac_config.get("fax_template") or {}
    if not template:
        template_id = parsed.get("template_id")
        if isinstance(template_id, str) and template_id:
            registry = config_service.load_fax_template_registry()
            template = registry.get(template_id) or {}
    if isinstance(template, dict):
        table_box, table_units, grid_column_edges, grid_row_edges = _resolve_ocr_pages_grid_metadata(
            template=template,
            pages=pages,
            existing_table_box=table_box,
            existing_table_units=table_units,
            existing_grid_column_edges=grid_column_edges,
            existing_grid_row_edges=grid_row_edges,
            allow_expensive_detection=False,
        )
        for key in grid_params.keys():
            if key in template:
                grid_params[key] = template.get(key)
    missing_grid_parts: list[str] = []
    if not table_box:
        missing_grid_parts.append("table_box")
    if not grid_column_edges:
        missing_grid_parts.append("grid_column_edges")
    if not grid_row_edges:
        missing_grid_parts.append("grid_row_edges")
    if missing_grid_parts:
        grid_detection_status = "deferred"
        grid_detection_deferred_reason = f"missing_template_grid_metadata:{','.join(missing_grid_parts)}"
    return (
        {
            "order_id": order_id,
            "engine": parsed.get("engine"),
            "template_id": parsed.get("template_id"),
            "facility_id": parsed.get("facility_id"),
            "pages": pages,
            "combined": combined_urls,
            "table_box": table_box,
            "table_units": table_units,
            "grid_column_edges": grid_column_edges,
            "grid_row_edges": grid_row_edges,
            "grid_params": grid_params,
            "grid_detection_status": grid_detection_status,
            "grid_detection_deferred_reason": grid_detection_deferred_reason,
        },
        None,
    )


def _parse_grid_table_box(template: dict[str, Any]) -> list[float] | None:
    raw_box = template.get("grid_table_box") or template.get("table_box")
    if not isinstance(raw_box, list) or len(raw_box) < 4:
        return None
    try:
        return [float(value) for value in raw_box[:4]]
    except (TypeError, ValueError):
        return None


def _parse_grid_edges(template: dict[str, Any], key: str) -> list[float] | None:
    raw_edges = template.get(key)
    if not isinstance(raw_edges, list) or len(raw_edges) < 2:
        return None
    try:
        return [float(value) for value in raw_edges]
    except (TypeError, ValueError):
        return None


def _expected_grid_columns(template: dict[str, Any]) -> int:
    try:
        expected = int(template.get("grid_expected_columns") or 0)
    except (TypeError, ValueError):
        expected = 0
    if expected >= 2:
        return expected
    grid_columns = template.get("grid_columns")
    if isinstance(grid_columns, list) and len(grid_columns) >= 2:
        return len(grid_columns)
    return 0


def _synthesize_grid_column_edges(table_box: list[float] | None, template: dict[str, Any]) -> list[float] | None:
    if not isinstance(table_box, list) or len(table_box) < 4:
        return None
    expected = _expected_grid_columns(template)
    if expected < 2:
        return None
    left = float(table_box[0])
    right = float(table_box[2])
    span = right - left
    if span <= 0:
        return None
    return [left + span * idx / expected for idx in range(expected + 1)]


def _detect_grid_from_page_overlay(
    pages: list[dict[str, object]],
    template: dict[str, Any],
) -> GridDetectionResult | None:
    overlay_candidates: list[str] = []
    for page in pages:
        for key in ("layout_overlay_uri", "ocr_overlay_uri"):
            uri = page.get(key)
            if isinstance(uri, str) and uri:
                overlay_candidates.append(uri)
    for uri in overlay_candidates:
        try:
            overlay_bytes = load_bytes_from_uri(uri)
            detected = detect_table_grid_image(overlay_bytes, template)
        except Exception:  # noqa: BLE001
            detected = None
        if detected:
            return detected
    return None


def _resolve_ocr_pages_grid_metadata(
    *,
    template: dict[str, Any],
    pages: list[dict[str, object]],
    existing_table_box: list[float] | None,
    existing_table_units: str | None,
    existing_grid_column_edges: list[float] | None,
    existing_grid_row_edges: list[float] | None,
    allow_expensive_detection: bool,
) -> tuple[list[float] | None, str | None, list[float] | None, list[float] | None]:
    table_box = existing_table_box or _parse_grid_table_box(template)
    units = existing_table_units
    if not units:
        raw_units = template.get("units")
        if isinstance(raw_units, str) and raw_units:
            units = raw_units
    column_edges = existing_grid_column_edges or _parse_grid_edges(template, "grid_column_edges")
    row_edges = existing_grid_row_edges or _parse_grid_edges(template, "grid_row_edges")

    if table_box and column_edges and row_edges:
        return table_box, units, column_edges, row_edges

    synthesized_edges = _synthesize_grid_column_edges(table_box, template)
    if table_box and synthesized_edges:
        return table_box, units, synthesized_edges, row_edges

    if allow_expensive_detection:
        detected_grid = _detect_grid_from_page_overlay(pages, template)
        if detected_grid:
            return (
                table_box or list(detected_grid.table_box),
                units,
                column_edges or list(detected_grid.column_edges),
                row_edges or list(detected_grid.row_edges),
            )

    return table_box, units, column_edges, row_edges


def _sheet_row_identity(date_value: object, daypart: object, menu_name: object) -> tuple[str, str, str]:
    parsed_date = _normalize_entry_date(date_value)
    date_key = parsed_date.isoformat() if parsed_date else ""
    daypart_key = _normalize_daypart_key(daypart)
    menu_key = _normalize_menu_text(str(menu_name or ""))
    return date_key, daypart_key, menu_key


def _build_sheet_fields_and_indexes(template: dict[str, Any]) -> tuple[list[str], dict[str, int]]:
    fields = _row_fields_from_template(template)
    index_map: dict[str, int] = {}
    for idx, field in enumerate(fields):
        index_map[field] = idx
    return fields, index_map


def _build_sheet_quantity_index(fields: list[str]) -> dict[tuple[str, str], int]:
    quantity_index: dict[tuple[str, str], int] = {}
    for idx, field in enumerate(fields):
        diet, area = _quantity_meta_from_field(field)
        if not diet or not area:
            continue
        quantity_index[(diet, area)] = idx
    return quantity_index


def _validate_sheet_template_fields(fields: list[str]) -> str | None:
    if not fields:
        return "sheet_fields_not_found"
    seen: set[str] = set()
    quantity_columns = 0
    for field in fields:
        token = str(field or "").strip()
        if not token:
            return "sheet_template_field_invalid"
        if token in seen:
            return "sheet_fields_duplicate"
        seen.add(token)
        if token in {"date_mmdd", "date", "daypart", "menu", "menu_name", "remarks", "note"}:
            continue
        diet, area = _quantity_meta_from_field(token)
        if not diet or not area:
            return "sheet_template_field_invalid"
        quantity_columns += 1
    if quantity_columns <= 0:
        return "sheet_quantity_columns_missing"
    return None


def _numeric_string_add(current: str, qty: float) -> str:
    base = 0.0
    if current:
        try:
            base = float(current)
        except Exception:
            base = 0.0
    value = base + qty
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _build_rows_from_menu_entries(
    *,
    entries: list[dict],
    fields: list[str],
    field_index: dict[str, int],
    line_dates: set[date],
    source: str,
    payload_dates: set[date] | None = None,
    payload_row_count: int = 0,
    scope_anchor_date: date | None = None,
) -> tuple[list[dict[str, Any]], str]:
    should_filter_by_line_dates = source == "weekly_menu"
    if should_filter_by_line_dates and line_dates:
        entry_dates = {
            entry.get("menu_date")
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("menu_date"), date)
        }
        matched_line_dates = sorted(
            {
                item
                for item in line_dates
                if isinstance(item, date) and item in entry_dates
            }
        )
        filtered = [
            entry
            for entry in entries
            if isinstance(entry.get("menu_date"), date) and entry.get("menu_date") in matched_line_dates
        ]
        # Keep full weekly menu rows when OCR date extraction failed or produced off-month dates.
        if not filtered:
            filtered = list(entries)
        # If OCR date extraction misses an intermediate day (e.g. only 2/15 and 2/17),
        # keep the contiguous menu range to avoid row shifts in sheet mapping.
        elif len(matched_line_dates) >= 2:
            min_date = matched_line_dates[0]
            max_date = matched_line_dates[-1]
            span_days = (max_date - min_date).days
            if 1 < span_days <= 10:
                range_filtered = [
                    entry
                    for entry in entries
                    if isinstance(entry.get("menu_date"), date)
                    and min_date <= entry.get("menu_date") <= max_date
                ]
                if len(range_filtered) > len(filtered):
                    filtered = range_filtered
    elif should_filter_by_line_dates and payload_dates:
        entry_dates = {
            entry.get("menu_date")
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("menu_date"), date)
        }
        matched_payload_dates = sorted(
            {
                item
                for item in payload_dates
                if isinstance(item, date) and item in entry_dates
            }
        )
        filtered = [
            entry
            for entry in entries
            if isinstance(entry.get("menu_date"), date) and entry.get("menu_date") in matched_payload_dates
        ]
        # If OCR date extraction produced sparse anchors (e.g. 2/15 and 2/17),
        # keep intermediate weekly-menu days to avoid row-index shifts.
        if filtered and len(matched_payload_dates) >= 2:
            min_date = matched_payload_dates[0]
            max_date = matched_payload_dates[-1]
            span_days = (max_date - min_date).days
            if 1 < span_days <= 10:
                range_filtered = [
                    entry
                    for entry in entries
                    if isinstance(entry.get("menu_date"), date)
                    and min_date <= entry.get("menu_date") <= max_date
                ]
                if len(range_filtered) > len(filtered):
                    filtered = range_filtered
        if not filtered:
            filtered = list(entries)
    else:
        filtered = list(entries)
    if (
        source == "weekly_menu"
        and not line_dates
        and not payload_dates
        and payload_row_count > 0
        and len(filtered) > payload_row_count
        and len(filtered) >= payload_row_count * 2
    ):
        window = int(max(payload_row_count, 1))
        window = min(window, len(filtered))
        best_start = 0
        best_score: tuple[int, int, int, int] | None = None
        for start in range(0, len(filtered) - window + 1):
            candidate = filtered[start : start + window]
            candidate_dates = [
                item.get("menu_date")
                for item in candidate
                if isinstance(item, dict) and isinstance(item.get("menu_date"), date)
            ]
            if not candidate_dates:
                continue
            min_date = min(candidate_dates)
            max_date = max(candidate_dates)
            span_days = (max_date - min_date).days
            if scope_anchor_date is None:
                distance_days = 0
                contains_anchor = True
            elif min_date <= scope_anchor_date <= max_date:
                distance_days = 0
                contains_anchor = True
            elif scope_anchor_date < min_date:
                distance_days = (min_date - scope_anchor_date).days
                contains_anchor = False
            else:
                distance_days = (scope_anchor_date - max_date).days
                contains_anchor = False
            score = (
                0 if contains_anchor else 1,
                int(distance_days),
                int(span_days),
                int(start),
            )
            if best_score is None or score < best_score:
                best_score = score
                best_start = start
        filtered = filtered[best_start : best_start + window]
    if not filtered:
        return [], source
    date_field = next((field for field in fields if field.startswith("date")), None)
    daypart_field = "daypart" if "daypart" in field_index else None
    menu_field = "menu" if "menu" in field_index else ("menu_name" if "menu_name" in field_index else None)
    rows: list[dict[str, Any]] = []
    for idx, entry in enumerate(filtered):
        menu_date = entry.get("menu_date")
        daypart = entry.get("daypart_key")
        menu_name = str(entry.get("menu_name") or "").strip()
        values = [""] * len(fields)
        if date_field:
            values[field_index[date_field]] = _format_mmdd(menu_date)
        if daypart_field:
            values[field_index[daypart_field]] = str(daypart or "")
        if menu_field:
            values[field_index[menu_field]] = menu_name
        row_id = "__".join(
            [
                menu_date.isoformat() if isinstance(menu_date, date) else "",
                str(daypart or ""),
                str(entry.get("slot_index") if entry.get("slot_index") is not None else idx),
                str(idx),
            ]
        )
        rows.append(
            {
                "row_id": row_id,
                "values": values,
                "identity": _sheet_row_identity(menu_date, daypart, menu_name),
                "sort_key": (
                    int(entry.get("source_order") or idx),
                    int(entry.get("slot_index") or 0),
                    idx,
                )
                if source == "ocr_table"
                else (
                    menu_date or date.min,
                    _daypart_sort_components(daypart)[0],
                    _daypart_sort_components(daypart)[1],
                    int(entry.get("slot_index") or 0),
                    idx,
                ),
            }
        )
    rows.sort(key=lambda item: item.get("sort_key"))
    return rows, source


def _collect_dates_from_sheet_rows(
    rows: list[dict[str, Any]],
    *,
    fields: list[str],
    received_at: datetime,
) -> set[date]:
    if not rows or not fields:
        return set()
    date_idx = next((idx for idx, field in enumerate(fields) if field.startswith("date")), None)
    if date_idx is None:
        return set()
    dates: set[date] = set()
    for row in rows:
        values = row.get("values")
        if not isinstance(values, list) or date_idx >= len(values):
            continue
        parsed = parse_date_string(str(values[date_idx] or ""), received_at)
        if parsed:
            dates.add(parsed)
    return dates


def _collect_sheet_dates_from_rows(rows: list[list[Any]] | None, *, received_at: datetime) -> set[date]:
    dates: set[date] = set()
    for row in rows or []:
        if not isinstance(row, list):
            continue
        for cell in row[:2]:
            parsed = parse_date_string(str(cell or "").strip(), received_at)
            if parsed:
                dates.add(parsed)
    return dates


def _to_sheet_month_id(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if re.match(r"^\d{4}-\d{2}$", text):
        return text
    match = re.match(r"^(\d{4}-\d{2})@\d{4}-\d{2}-\d{2}~\d{4}-\d{2}-\d{2}$", text)
    if match:
        return match.group(1)
    return None


def _shift_sheet_month_id(month_id: str, delta: int) -> str | None:
    base = _to_sheet_month_id(month_id)
    if not base:
        return None
    year = int(base[:4])
    month = int(base[5:7])
    index = year * 12 + (month - 1) + delta
    shifted_year = index // 12
    shifted_month = (index % 12) + 1
    return f"{shifted_year:04d}-{shifted_month:02d}"


def _sheet_month_distance(from_month_id: str | None, to_month_id: str | None) -> int | None:
    from_month = _to_sheet_month_id(from_month_id)
    to_month = _to_sheet_month_id(to_month_id)
    if not from_month or not to_month:
        return None
    fy = int(from_month[:4])
    fm = int(from_month[5:7])
    ty = int(to_month[:4])
    tm = int(to_month[5:7])
    return abs((fy * 12 + fm) - (ty * 12 + tm))


def _collect_sheet_dates_from_payload(payload: dict[str, Any], received_at: datetime) -> list[date]:
    dates: list[date] = []
    seen: set[str] = set()

    def _push(raw: object) -> None:
        if raw is None:
            return
        parsed: date | None = None
        if isinstance(raw, date):
            parsed = raw
        else:
            text = str(raw).strip()
            if not text:
                return
            parsed = parse_date_string(text, received_at)
            if not parsed:
                match_full = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
                if match_full:
                    try:
                        parsed = date(
                            int(match_full.group(1)),
                            int(match_full.group(2)),
                            int(match_full.group(3)),
                        )
                    except Exception:
                        parsed = None
        if not parsed:
            return
        key = parsed.isoformat()
        if key in seen:
            return
        seen.add(key)
        dates.append(parsed)

    for raw in payload.get("date_strings") or []:
        _push(raw)

    table_rows = payload.get("table_rows")
    if isinstance(table_rows, list):
        for row in table_rows[:40]:
            if not isinstance(row, list):
                continue
            for cell in row[:2]:
                _push(cell)

    table_raw = payload.get("table_raw")
    if isinstance(table_raw, str) and table_raw.strip():
        table_raw = _normalize_table_raw_text(table_raw)
        # Prefer markdown table rows to avoid matching unrelated footer timestamps.
        for raw_line in table_raw.splitlines():
            line = raw_line.strip()
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            for cell in cells[:2]:
                _push(cell)
        if not dates:
            for match in re.finditer(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}", table_raw):
                _push(match.group(0))

    return dates


def _normalize_table_raw_text(value: str) -> str:
    text = value
    if "\\r\\n" in text or "\\n" in text or "\\r" in text:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    return text


def _extract_markdown_table_blocks(markdown: str) -> tuple[list[str], list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    non_table_lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("|"):
            current.append(line)
            continue
        if current:
            blocks.append(current)
            current = []
        if stripped:
            non_table_lines.append(stripped)
    if current:
        blocks.append(current)
    joined_blocks = ["\n".join(block) for block in blocks if len(block) >= 2]
    return joined_blocks, non_table_lines


def _extract_standalone_quantity_candidates(lines: list[str]) -> list[str]:
    if not lines:
        return []
    candidates: list[str] = []
    seen: set[str] = set()
    try:
        max_qty = max(float(os.getenv("OCR_SHEET_MAX_QTY", "150")), 1.0)
    except Exception:
        max_qty = 150.0

    for line in lines:
        token = line.strip()
        if not token:
            continue
        token = token.strip("|").strip()
        if not token:
            continue
        # Keep only standalone numeric lines to avoid dates/page numbers/noise.
        if not re.fullmatch(r"[+-]?\d{1,3}(?:\.\d+)?", token):
            continue
        try:
            value = float(token)
        except Exception:
            continue
        if value <= 0 or value > max_qty:
            continue
        normalized = str(int(value)) if value.is_integer() else str(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def _sanitize_payload_table_raw(payload: dict[str, Any]) -> dict[str, Any]:
    table_raw = payload.get("table_raw")
    if not isinstance(table_raw, str) or not table_raw.strip():
        return payload
    normalized = _normalize_table_raw_text(table_raw)
    table_blocks, non_table_lines = _extract_markdown_table_blocks(normalized)
    if not table_blocks:
        return payload
    sanitized = dict(payload)
    joined = "\n\n".join(table_blocks)
    sanitized["table_raw"] = joined
    sanitized["_table_raw_blocks"] = table_blocks
    if joined.strip() != normalized.strip():
        sanitized["table_raw_truncated"] = True
        sanitized["_table_raw_original_chars"] = len(normalized)
        sanitized["_table_raw_non_table_lines"] = non_table_lines[:200]
        candidates = _extract_standalone_quantity_candidates(non_table_lines)
        if candidates:
            sanitized["_table_raw_unstructured_qty"] = candidates[:200]
    return sanitized


def _extract_sheet_rows_from_payload(payload: dict[str, Any], template: dict[str, Any]) -> list[list[str]]:
    payload = _sanitize_payload_table_raw(payload)
    rows = rows_from_pipeline_payload(payload, template)
    if not rows:
        return []
    return [[_field_value_to_str(cell) for cell in row] for row in rows]


def _extract_first_pass_rows_from_payload(
    payload: dict[str, Any],
    template: dict[str, Any],
) -> list[list[str]]:
    payload = _sanitize_payload_table_raw(payload)
    rows = rows_from_structured_payload(payload, template)
    if not rows:
        table_raw = payload.get("table_raw")
        if isinstance(table_raw, str) and table_raw.strip():
            rows = rows_from_markdown(table_raw, template) or []
    if not rows:
        raw_rows = payload.get("rows")
        if isinstance(raw_rows, list):
            normalized: list[list[str]] = []
            for row in raw_rows:
                if not isinstance(row, list):
                    continue
                normalized.append([_field_value_to_str(cell) for cell in row])
            rows = normalized
    if not rows:
        return []
    return [[_field_value_to_str(cell) for cell in row] for row in rows]


def _resolve_payload_sheet_template(payload: dict[str, Any], template: dict[str, Any] | None) -> dict[str, Any]:
    resolved = template if isinstance(template, dict) else {}
    if not isinstance(payload, dict):
        return resolved
    template_id = payload.get("template_id")
    if not isinstance(template_id, str):
        classification = payload.get("classification")
        if isinstance(classification, dict):
            template_id = classification.get("matched_template_id")
    template_id = str(template_id or "").strip()
    if not template_id:
        return resolved
    if resolved.get("template_id") == template_id:
        return resolved
    try:
        registry = config_service.load_fax_template_registry()
    except Exception:
        return resolved
    matched = registry.get(template_id)
    if isinstance(matched, dict) and matched:
        return matched
    return resolved


def _collect_structured_tables_from_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    tables: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    def _push(table_payload: object) -> None:
        if not isinstance(table_payload, dict):
            return
        table_id = str(table_payload.get("table_id") or "").strip()
        try:
            page_index = int(table_payload.get("page_index") or -1)
        except Exception:
            page_index = -1
        key = (table_id, page_index)
        if table_id and key in seen:
            return
        if table_id:
            seen.add(key)
        tables.append(table_payload)

    raw_tables = payload.get("tables")
    if isinstance(raw_tables, list):
        for table_payload in raw_tables:
            _push(table_payload)
    pages = payload.get("pages")
    if isinstance(pages, list):
        for page_payload in pages:
            if not isinstance(page_payload, dict):
                continue
            page_tables = page_payload.get("tables")
            if not isinstance(page_tables, list):
                continue
            for table_payload in page_tables:
                _push(table_payload)
    return tables


def _collect_raw_payload_cell_issues(
    payload: dict[str, Any] | None,
    template: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    candidates: list[object] = []
    for key in ("cell_issues", "yomitoku_cell_issues", "roi_cell_issues"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    roi_extraction = payload.get("roi_extraction")
    if isinstance(roi_extraction, dict):
        roi_issues = roi_extraction.get("cell_issues")
        if isinstance(roi_issues, list):
            candidates.extend(roi_issues)
    edited = payload.get("_edited_ocr")
    latest = edited.get("latest") if isinstance(edited, dict) else None
    llm_review = latest.get("llm_review") if isinstance(latest, dict) else None
    if isinstance(llm_review, dict):
        for key in ("issues", "rejected_overwrites"):
            value = llm_review.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    candidates.extend(structured_cell_issues_from_payload(payload, template or {}))

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in candidates:
        if not isinstance(issue, dict):
            continue
        try:
            dedupe_key = json.dumps(issue, ensure_ascii=False, sort_keys=True)
        except TypeError:
            dedupe_key = str(issue)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        collected.append(dict(issue))
    return collected


def _sheet_identity_from_values(
    row: list[str],
    *,
    date_idx: int | None,
    daypart_idx: int | None,
    menu_idx: int | None,
) -> tuple[str, str, str]:
    date_value = row[date_idx] if date_idx is not None and date_idx < len(row) else ""
    daypart_value = row[daypart_idx] if daypart_idx is not None and daypart_idx < len(row) else ""
    menu_value = row[menu_idx] if menu_idx is not None and menu_idx < len(row) else ""
    return _sheet_row_identity(date_value, daypart_value, menu_value)


def _extract_payload_cell_issues(
    payload: dict[str, Any] | None,
    template: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    template = _resolve_payload_sheet_template(payload, template)
    if not isinstance(template, dict) or not template:
        return []

    raw_issues = _collect_raw_payload_cell_issues(payload, template)
    if not raw_issues:
        return []

    fields, field_index = _build_sheet_fields_and_indexes(template)
    if not fields:
        return []
    payload_rows = _extract_first_pass_rows_from_payload(payload, template)
    date_idx = field_index.get("date_mmdd")
    if date_idx is None:
        date_idx = field_index.get("date")
    daypart_idx = field_index.get("daypart")
    menu_idx = field_index.get("menu")
    if menu_idx is None:
        menu_idx = field_index.get("menu_name")

    issues: list[dict[str, Any]] = []
    for issue in raw_issues:
        if not isinstance(issue, dict):
            continue
        field = str(issue.get("field") or "").strip()
        column_index = issue.get("column_index")
        try:
            resolved_column_index = int(column_index) if column_index is not None else None
        except Exception:
            resolved_column_index = None
        if not field:
            col_key = str(issue.get("col") or "").strip()
            if col_key:
                field = f"qty.{col_key}"
        if field:
            resolved_column_index = field_index.get(field, resolved_column_index)
        if resolved_column_index is None:
            try:
                fallback_column_index = int(column_index) if column_index is not None else -1
            except Exception:
                fallback_column_index = -1
            if 0 <= fallback_column_index < len(fields):
                resolved_column_index = fallback_column_index
        if resolved_column_index is None:
            continue
        if not field and 0 <= resolved_column_index < len(fields):
            field = fields[resolved_column_index]
        try:
            source_row_index = int(
                issue.get("source_row_index")
                if issue.get("source_row_index") is not None
                else issue.get("row_index")
                if issue.get("row_index") is not None
                else -1
            )
        except Exception:
            source_row_index = -1
        source_identity = ("", "", "")
        if 0 <= source_row_index < len(payload_rows):
            source_identity = _sheet_identity_from_values(
                payload_rows[source_row_index],
                date_idx=date_idx,
                daypart_idx=daypart_idx,
                menu_idx=menu_idx,
            )
        normalized = {
            "source_row_index": source_row_index,
            "column_index": resolved_column_index,
            "field": field,
            "issue_code": str(issue.get("issue_code") or issue.get("reason") or "review_required").strip(),
            "severity": str(issue.get("severity") or "warning").strip() or "warning",
            "source": str(issue.get("source") or "ocr_payload").strip() or "ocr_payload",
            "route": str(issue.get("route") or "").strip(),
            "row_key": str(issue.get("row_key") or "").strip(),
            "col": str(issue.get("col") or "").strip(),
            "date_key": source_identity[0],
            "daypart_key": source_identity[1],
            "menu_key": source_identity[2],
        }
        for key in (
            "confidence",
            "votes",
            "value",
            "max_allowed",
            "raw",
            "reason",
            "bbox",
            "text",
            "page_index",
            "table_id",
            "row_span",
            "col_span",
        ):
            if key in issue:
                normalized[key] = issue.get(key)
        raw_texts = issue.get("raw_texts")
        if isinstance(raw_texts, list):
            normalized["raw_texts"] = [str(text) for text in raw_texts if str(text).strip()][:5]
        issues.append(normalized)
    return issues


def _map_payload_cell_issues_to_sheet_rows(
    *,
    payload: dict[str, Any] | None,
    template: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    fields: list[str],
) -> list[dict[str, Any]]:
    issues = _extract_payload_cell_issues(payload, template)
    if not issues or not rows:
        return []
    has_daypart_field = "daypart" in fields
    lookup_exact: dict[tuple[str, str, str], int] = {}
    lookup_date_menu: dict[tuple[str, str], int] = {}
    for row_index, row in enumerate(rows):
        identity = row.get("identity")
        if not isinstance(identity, tuple) or len(identity) != 3:
            continue
        lookup_exact[identity] = row_index
        lookup_date_menu[(identity[0], identity[2])] = row_index

    mapped: list[dict[str, Any]] = []
    for issue in issues:
        date_key = str(issue.get("date_key") or "")
        daypart_key = str(issue.get("daypart_key") or "")
        menu_key = str(issue.get("menu_key") or "")
        target_row_index: int | None = None
        if date_key or daypart_key or menu_key:
            target_row_index = lookup_exact.get((date_key, daypart_key, menu_key))
            if target_row_index is None and not has_daypart_field:
                target_row_index = lookup_date_menu.get((date_key, menu_key))
        if target_row_index is None:
            source_row_index = issue.get("source_row_index")
            if isinstance(source_row_index, int) and 0 <= source_row_index < len(rows):
                target_row_index = source_row_index
        normalized = dict(issue)
        normalized["row_index"] = target_row_index if target_row_index is not None else -1
        normalized["mapped"] = target_row_index is not None
        mapped.append(normalized)
    return mapped


def _extract_payload_unstructured_quantity_candidates(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    collected: list[str] = []
    seen: set[str] = set()

    def _push(items: list[str]) -> None:
        for item in items:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            collected.append(token)

    raw_candidates = payload.get("_table_raw_unstructured_qty")
    if isinstance(raw_candidates, list):
        _push([str(item) for item in raw_candidates if str(item).strip()])

    non_table_lines = payload.get("_table_raw_non_table_lines")
    if isinstance(non_table_lines, list):
        _push(_extract_standalone_quantity_candidates([str(item) for item in non_table_lines]))

    scan_texts: list[str] = []
    for key in ("raw_text", "text", "markdown_text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            scan_texts.append(value)
    pages = payload.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            text = page.get("markdown_text")
            if isinstance(text, str) and text.strip():
                scan_texts.append(text)
    if scan_texts:
        lines: list[str] = []
        for text in scan_texts:
            lines.extend(text.splitlines())
        _push(_extract_standalone_quantity_candidates(lines))
    return collected


def _build_sheet_lines_from_ocr_payload(
    *,
    payload: dict[str, Any],
    template: dict[str, Any],
    received_at: datetime,
    week_id: str | None,
    facility_id: str | None,
    quantity_rules: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    sheet_rows = _extract_sheet_rows_from_payload(payload, template)
    if not sheet_rows:
        return []
    policy = config_service.load_ingest_policy()
    effective_quantity_rules = (
        dict(quantity_rules)
        if isinstance(quantity_rules, dict)
        else policy.get("quantity_rules", {})
    )
    date_candidates = _collect_sheet_dates_from_payload(payload, received_at)
    default_date = min(date_candidates) if date_candidates else None
    lines = parse_order_lines(
        sheet_rows,
        template,
        received_at,
        effective_quantity_rules,
        default_date=default_date,
        tokens=[],
        grid=None,
        pdf_bytes=None,
    )
    if not lines:
        return []
    if week_id:
        position_entries = _build_position_entries_for_lines(
            week_id=week_id,
            lines=lines,
            facility_id=facility_id,
        )
        mapped_lines, mapped_rows = _apply_menu_position_mapping_safe(
            lines,
            week_id,
            facility_id=facility_id,
            entries_override=position_entries if position_entries else None,
        )
        lines = mapped_lines
        if mapped_rows <= 0:
            min_ratio = float(policy.get("menu_match_min_ratio", 0.72))
            lines = _apply_menu_matching(lines, week_id, facility_id, min_ratio)
    return lines


def _resolve_sheet_week_id(
    *,
    current_week_id: str | None,
    received_at: datetime,
    order_lines: list[dict[str, Any]],
    ocr_payload: dict[str, Any] | None,
    facility_id: str | None,
    week_hints: list[str] | None = None,
) -> str | None:
    policy = config_service.load_ingest_policy()
    primary_candidates: list[str] = []
    fallback_candidates: list[str] = []
    stale_hint_candidates: list[str] = []

    def _append(target: list[str], value: object) -> None:
        candidate = _normalize_sheet_week_candidate(value)
        if candidate and candidate not in target:
            target.append(candidate)

    explicit_current_week = _normalize_sheet_week_value(current_week_id)
    if explicit_current_week and "@" in explicit_current_week:
        return explicit_current_week

    base_month = received_at.strftime("%Y-%m")

    line_dates = [
        line.get("date")
        for line in order_lines
        if isinstance(line, dict) and isinstance(line.get("date"), date)
    ]
    if line_dates:
        _append(primary_candidates, month_id_from_dates(line_dates, received_at, policy))

    if isinstance(ocr_payload, dict):
        ocr_dates = _collect_sheet_dates_from_payload(ocr_payload, received_at)
        if ocr_dates:
            ocr_month = month_id_from_dates(ocr_dates, received_at, policy)
            distance = _sheet_month_distance(ocr_month, base_month)
            if distance is None or distance <= 1:
                _append(primary_candidates, ocr_month)
            else:
                _append(stale_hint_candidates, ocr_month)

    _append(primary_candidates, current_week_id)
    for hint in week_hints or []:
        distance = _sheet_month_distance(hint, base_month)
        if distance is not None and distance > 1:
            _append(stale_hint_candidates, hint)
        else:
            _append(fallback_candidates, hint)

    _append(fallback_candidates, base_month)
    for delta in (-1, 1, -2, 2, -3, 3):
        _append(fallback_candidates, _shift_sheet_month_id(base_month, delta))

    def _has_menu_entries(month_id: str) -> bool:
        return bool(_build_position_menu_entries_safe(month_id, facility_id))

    for month_id in primary_candidates:
        if _has_menu_entries(month_id):
            return month_id
    if primary_candidates:
        # Prefer OCR/order-line derived month even when no weekly menu exists
        # to avoid binding to stale historical hints.
        return primary_candidates[0]

    for month_id in fallback_candidates:
        if _has_menu_entries(month_id):
            return month_id
    for month_id in stale_hint_candidates:
        if _has_menu_entries(month_id):
            return month_id
    return fallback_candidates[0] if fallback_candidates else None


def _build_rows_from_order_lines(
    *,
    order_lines: list[Any],
    fields: list[str],
    field_index: dict[str, int],
) -> list[dict[str, Any]]:
    def _line_value(line: Any, key: str):
        if isinstance(line, dict):
            return line.get(key)
        return getattr(line, key, None)

    date_field = next((field for field in fields if field.startswith("date")), None)
    daypart_field = "daypart" if "daypart" in field_index else None
    menu_field = "menu" if "menu" in field_index else ("menu_name" if "menu_name" in field_index else None)
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for line in order_lines:
        line_date = _line_value(line, "date")
        line_daypart = _line_value(line, "daypart")
        line_menu_name = _line_value(line, "menu_name")
        line_id = _line_value(line, "id")
        identity = _sheet_row_identity(line_date, line_daypart, line_menu_name)
        existing = grouped.get(identity)
        if existing:
            continue
        values = [""] * len(fields)
        if date_field:
            values[field_index[date_field]] = _format_mmdd(line_date)
        if daypart_field:
            values[field_index[daypart_field]] = str(line_daypart or "")
        if menu_field:
            values[field_index[menu_field]] = str(line_menu_name or "")
        grouped[identity] = {
            "row_id": f"line__{line_id}",
            "values": values,
            "identity": identity,
            "sort_key": (
                line_date or date.min,
                str(line_daypart or ""),
                str(line_menu_name or ""),
            ),
        }
    rows = list(grouped.values())
    rows.sort(key=lambda item: item.get("sort_key"))
    return rows


def _apply_order_line_quantities_to_sheet_rows(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_index: dict[tuple[str, str], int],
    order_lines: list[Any],
) -> None:
    def _line_value(line: Any, key: str):
        if isinstance(line, dict):
            return line.get(key)
        return getattr(line, key, None)

    if not rows or not fields:
        return
    has_daypart_field = "daypart" in fields
    note_field = "remarks" if "remarks" in fields else ("note" if "note" in fields else None)
    note_idx = fields.index(note_field) if note_field else None
    lookup_exact: dict[tuple[str, str, str], dict[str, Any]] = {}
    lookup_date_menu: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        identity = row.get("identity")
        if not isinstance(identity, tuple) or len(identity) != 3:
            continue
        lookup_exact[identity] = row
        lookup_date_menu[(identity[0], identity[2])] = row

    for line in order_lines:
        line_qty_corrected = _line_value(line, "quantity_corrected")
        line_qty_original = _line_value(line, "quantity_original")
        line_diet_type = _line_value(line, "diet_type")
        line_area_id = _line_value(line, "area_id")
        line_date = _line_value(line, "date")
        line_daypart = _line_value(line, "daypart")
        line_menu_name = _line_value(line, "menu_name")
        line_change_note = _line_value(line, "change_note")
        qty = line_qty_corrected if line_qty_corrected is not None else line_qty_original
        if qty is None:
            continue
        try:
            qty_float = float(qty)
        except Exception:
            continue
        diet_key = _normalize_sheet_diet(line_diet_type)
        area_key = _normalize_sheet_area(line_area_id)
        if not diet_key or not area_key:
            continue
        col_idx = _resolve_quantity_column_index(
            quantity_index=quantity_index,
            diet_key=diet_key,
            area_key=area_key,
        )
        if col_idx is None:
            continue
        identity = _sheet_row_identity(line_date, line_daypart, line_menu_name)
        target = lookup_exact.get(identity)
        if not target and not has_daypart_field:
            target = lookup_date_menu.get((identity[0], identity[2]))
        if not target:
            continue
        values = target.get("values")
        if not isinstance(values, list):
            continue
        while len(values) <= col_idx:
            values.append("")
        values[col_idx] = _numeric_string_add(str(values[col_idx] or ""), qty_float)
        if note_idx is not None and line_change_note:
            while len(values) <= note_idx:
                values.append("")
            current_note = str(values[note_idx] or "")
            if line_change_note not in current_note:
                values[note_idx] = (
                    f"{current_note} / {line_change_note}".strip(" /")
                    if current_note
                    else str(line_change_note)
                )


def _count_non_empty_quantity_cells(
    *,
    rows: list[dict[str, Any]],
    quantity_index: dict[tuple[str, str], int],
) -> int:
    if not rows or not quantity_index:
        return 0
    quantity_columns = sorted(set(quantity_index.values()))
    count = 0
    for row in rows:
        values = row.get("values")
        if not isinstance(values, list):
            continue
        for col_idx in quantity_columns:
            if col_idx < len(values) and str(values[col_idx] or "").strip():
                count += 1
    return count


def _count_non_empty_quantity_rows(
    *,
    rows: list[dict[str, Any]],
    quantity_index: dict[tuple[str, str], int],
) -> int:
    if not rows or not quantity_index:
        return 0
    quantity_columns = sorted(set(quantity_index.values()))
    count = 0
    for row in rows:
        values = row.get("values")
        if not isinstance(values, list):
            continue
        has_qty = False
        for col_idx in quantity_columns:
            if col_idx < len(values) and str(values[col_idx] or "").strip():
                has_qty = True
                break
        if has_qty:
            count += 1
    return count


def _count_non_empty_quantity_columns(
    *,
    rows: list[dict[str, Any]],
    quantity_index: dict[tuple[str, str], int],
) -> int:
    if not rows or not quantity_index:
        return 0
    quantity_columns = sorted(set(quantity_index.values()))
    active: set[int] = set()
    for row in rows:
        values = row.get("values")
        if not isinstance(values, list):
            continue
        for col_idx in quantity_columns:
            if col_idx < len(values) and str(values[col_idx] or "").strip():
                active.add(col_idx)
    return len(active)


def _count_non_empty_quantity_cells_for_row_indexes(
    *,
    rows: list[dict[str, Any]],
    quantity_index: dict[tuple[str, str], int],
    row_indexes: set[int],
) -> int:
    if not rows or not quantity_index or not row_indexes:
        return 0
    quantity_columns = sorted(set(quantity_index.values()))
    count = 0
    for row_idx in sorted(row_indexes):
        if row_idx < 0 or row_idx >= len(rows):
            continue
        values = rows[row_idx].get("values")
        if not isinstance(values, list):
            continue
        for col_idx in quantity_columns:
            if col_idx < len(values) and str(values[col_idx] or "").strip():
                count += 1
    return count


def _count_source_row_alignment_penalty_cells(
    *,
    base_rows: list[dict[str, Any]],
    rows_by_source_index: list[dict[str, Any]],
    fields: list[str],
    quantity_index: dict[tuple[str, str], int],
    order_lines: list[Any],
) -> int:
    if not base_rows or not rows_by_source_index or not fields or not quantity_index or not order_lines:
        return 0

    def _line_value(line: Any, key: str):
        if isinstance(line, dict):
            return line.get(key)
        return getattr(line, key, None)

    has_daypart_field = "daypart" in fields
    mismatched_row_indexes: set[int] = set()
    invalid_source_row_penalty = 0
    for line in order_lines:
        qty_corrected = _line_value(line, "quantity_corrected")
        qty_original = _line_value(line, "quantity_original")
        qty = qty_corrected if qty_corrected is not None else qty_original
        if qty is None:
            continue
        try:
            float(qty)
        except Exception:
            continue
        diet_key = _normalize_sheet_diet(_line_value(line, "diet_type"))
        area_key = _normalize_sheet_area(_line_value(line, "area_id"))
        if not diet_key or not area_key:
            continue
        col_idx = _resolve_quantity_column_index(
            quantity_index=quantity_index,
            diet_key=diet_key,
            area_key=area_key,
        )
        if col_idx is None:
            continue
        source_idx_raw = _line_value(line, "source_row_index")
        try:
            source_row_index = int(source_idx_raw) if source_idx_raw is not None else None
        except Exception:
            source_row_index = None
        if source_row_index is None:
            continue
        if source_row_index < 0 or source_row_index >= len(base_rows):
            invalid_source_row_penalty += 1
            continue
        target_identity = base_rows[source_row_index].get("identity")
        if not isinstance(target_identity, tuple) or len(target_identity) != 3:
            continue
        line_identity = _sheet_row_identity(
            _line_value(line, "date"),
            _line_value(line, "daypart"),
            _line_value(line, "menu_name"),
        )
        if not line_identity[0] or not line_identity[2]:
            continue
        if has_daypart_field and not line_identity[1]:
            continue
        same_identity = target_identity == line_identity
        if not same_identity and not has_daypart_field:
            same_identity = target_identity[0] == line_identity[0] and target_identity[2] == line_identity[2]
        if same_identity:
            continue
        mismatched_row_indexes.add(source_row_index)
    return invalid_source_row_penalty + _count_non_empty_quantity_cells_for_row_indexes(
        rows=rows_by_source_index,
        quantity_index=quantity_index,
        row_indexes=mismatched_row_indexes,
    )


def _sheet_candidate_sort_key(
    *,
    mapped_count: int,
    mapped_row_count: int,
    mapped_column_count: int,
    priority: int,
    mismatch_penalty_cells: int = 0,
    payload_match_stats: dict[str, Any] | None = None,
) -> tuple[int, int, int, int, int, int]:
    effective_count = int(mapped_count) - max(int(mismatch_penalty_cells), 0)
    payload_trusted_match_score = 0
    payload_row_index_penalty = 0
    if isinstance(payload_match_stats, dict):
        payload_trusted_match_score = (
            int(payload_match_stats.get("exact", 0)) * 4
            + int(payload_match_stats.get("partial", 0)) * 3
            + int(payload_match_stats.get("neighbor", 0)) * 2
        )
        payload_row_index_penalty = int(payload_match_stats.get("row_index", 0))
    return (
        effective_count,
        int(mapped_count),
        payload_trusted_match_score,
        -payload_row_index_penalty,
        int(mapped_row_count) + int(mapped_column_count),
        int(priority),
    )


def _select_dominant_quantity_columns_from_rows(
    *,
    rows: list[dict[str, Any]],
    quantity_index: dict[tuple[str, str], int],
) -> list[int]:
    if not rows or not quantity_index:
        return []
    quantity_columns = sorted(set(quantity_index.values()))
    if not quantity_columns:
        return []
    hits: dict[int, int] = {col_idx: 0 for col_idx in quantity_columns}
    for row in rows:
        values = row.get("values")
        if not isinstance(values, list):
            continue
        for col_idx in quantity_columns:
            if col_idx < 0 or col_idx >= len(values):
                continue
            if _row_quantity_value(values, col_idx) is None:
                continue
            hits[col_idx] = int(hits.get(col_idx, 0)) + 1
    max_hit = max((int(hits.get(col_idx, 0)) for col_idx in quantity_columns), default=0)
    if max_hit <= 0:
        return []
    return [col_idx for col_idx in quantity_columns if int(hits.get(col_idx, 0)) == max_hit]


def _apply_weekly_menu_order_line_cluster_consensus_fill(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_index: dict[tuple[str, str], int],
) -> int:
    if not rows or not fields or not quantity_index:
        return 0
    dominant_columns = _select_dominant_quantity_columns_from_rows(
        rows=rows,
        quantity_index=quantity_index,
    )
    if not dominant_columns:
        return 0
    # Keep this pass conservative for order-line based sheets.
    # Use only one dominant quantity column to avoid cross-column side effects.
    target_columns = [min(dominant_columns)]
    return _fill_cluster_consensus_quantities(
        rows=rows,
        fields=fields,
        quantity_columns=target_columns,
    )


def _parse_sheet_fill_decision(value: object) -> bool | None:
    if isinstance(value, bool):
        return bool(value)
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "allow", "allowed", "approve", "approved", "ok"}:
        return True
    if text in {"0", "false", "no", "deny", "denied", "reject", "rejected"}:
        return False
    return None


def _llm_allows_order_line_cluster_consensus_fill(ocr_payload: dict[str, Any] | None) -> bool:
    if not isinstance(ocr_payload, dict):
        return False

    candidates: list[object] = [
        ocr_payload.get("_sheet_fill_decision"),
    ]
    reparse_debug = ocr_payload.get("_reparse_debug")
    if isinstance(reparse_debug, dict):
        candidates.append(reparse_debug.get("sheet_cluster_fill_decision"))
        candidates.append(reparse_debug.get("order_line_cluster_fill_decision"))
    ocr_debug = ocr_payload.get("_ocr_debug")
    if isinstance(ocr_debug, dict):
        candidates.append(ocr_debug.get("sheet_cluster_fill_decision"))
        candidates.append(ocr_debug.get("order_line_cluster_fill_decision"))

    for candidate in candidates:
        parsed = _parse_sheet_fill_decision(candidate)
        if parsed is None:
            continue
        return parsed
    return False


def _build_sheet_trace_rows(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_index: dict[tuple[str, str], int],
    source: str,
    mapped_mode: str,
    has_order_lines: bool,
) -> list[list[str]]:
    quantity_columns = set(quantity_index.values())
    source_token = "none"
    if source.startswith("weekly_menu"):
        source_token = "weekly_menu"
    elif source.startswith("ocr_table"):
        source_token = "ocr_table"

    trace_rows: list[list[str]] = []
    for row in rows:
        values = row.get("values")
        if not isinstance(values, list):
            values = []
        current: list[str] = []
        for col_idx, field in enumerate(fields):
            value = str(values[col_idx] or "").strip() if col_idx < len(values) else ""
            if col_idx in quantity_columns:
                if not value:
                    current.append("none")
                elif mapped_mode == "payload_row":
                    current.append("ocr_payload")
                elif has_order_lines and mapped_mode in {"identity", "source_row"}:
                    current.append("order_lines")
                else:
                    current.append("unknown")
                continue

            if not value:
                current.append("none")
                continue
            if str(field).startswith("date") or field in {"daypart", "menu", "menu_name"}:
                current.append(source_token)
                continue
            if field in {"remarks", "note"}:
                if mapped_mode == "payload_row":
                    current.append("ocr_payload")
                elif has_order_lines and mapped_mode in {"identity", "source_row"}:
                    current.append("order_lines")
                else:
                    current.append("unknown")
                continue
            current.append(source_token)
        trace_rows.append(current)
    return trace_rows


def _collect_sheet_row_dates_from_identity(rows: list[dict[str, Any]]) -> set[date]:
    dates: set[date] = set()
    for row in rows:
        identity = row.get("identity")
        if not isinstance(identity, tuple) or len(identity) != 3:
            continue
        date_key = str(identity[0] or "").strip()
        if not date_key:
            continue
        try:
            dates.add(date.fromisoformat(date_key))
        except Exception:
            continue
    return dates


def _collect_missing_weekly_menu_dates(
    *,
    entries: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    line_dates: set[date],
) -> list[date]:
    if len(line_dates) < 2 or not entries or not rows:
        return []
    entry_dates = sorted(
        {
            item.get("menu_date")
            for item in entries
            if isinstance(item, dict) and isinstance(item.get("menu_date"), date)
        }
    )
    if not entry_dates:
        return []
    entry_date_set = set(entry_dates)
    matched_line_dates = sorted({item for item in line_dates if item in entry_date_set})
    if len(matched_line_dates) < 2:
        return []
    min_date = matched_line_dates[0]
    max_date = matched_line_dates[-1]
    span_days = (max_date - min_date).days
    if span_days <= 1 or span_days > 10:
        return []
    expected_dates = {item for item in entry_dates if min_date <= item <= max_date}
    if not expected_dates:
        return []
    sheet_dates = _collect_sheet_row_dates_from_identity(rows)
    missing = sorted(expected_dates - sheet_dates)
    return missing


def _collect_unmapped_quantity_lines(
    *,
    order_lines: list[Any],
    quantity_index: dict[tuple[str, str], int],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    def _line_value(line: Any, key: str):
        if isinstance(line, dict):
            return line.get(key)
        return getattr(line, key, None)

    for line in order_lines:
        qty_corrected = _line_value(line, "quantity_corrected")
        qty_original = _line_value(line, "quantity_original")
        qty = qty_corrected if qty_corrected is not None else qty_original
        if qty is None:
            continue
        try:
            qty_float = float(qty)
        except Exception:
            continue
        diet_key = _normalize_sheet_diet(_line_value(line, "diet_type"))
        area_key = _normalize_sheet_area(_line_value(line, "area_id"))
        if not diet_key or not area_key:
            issues.append(
                {
                    "date": str(_line_value(line, "date") or ""),
                    "daypart": str(_line_value(line, "daypart") or ""),
                    "menu_name": str(_line_value(line, "menu_name") or ""),
                    "diet_type": str(_line_value(line, "diet_type") or ""),
                    "area_id": str(_line_value(line, "area_id") or ""),
                    "reason": "diet_or_area_missing",
                }
            )
            continue
        col_idx = _resolve_quantity_column_index(
            quantity_index=quantity_index,
            diet_key=diet_key,
            area_key=area_key,
        )
        if col_idx is None:
            issues.append(
                {
                    "date": str(_line_value(line, "date") or ""),
                    "daypart": str(_line_value(line, "daypart") or ""),
                    "menu_name": str(_line_value(line, "menu_name") or ""),
                    "diet_type": str(_line_value(line, "diet_type") or ""),
                    "area_id": str(_line_value(line, "area_id") or ""),
                    "reason": "quantity_column_unmapped",
                }
            )
    return issues


def _resolve_quantity_column_index(
    *,
    quantity_index: dict[tuple[str, str], int],
    diet_key: str | None,
    area_key: str | None,
) -> int | None:
    if not diet_key:
        return None
    if area_key:
        direct = quantity_index.get((diet_key, area_key))
        if direct is not None:
            return direct
    fallback = quantity_index.get((diet_key, "X"))
    if fallback is not None:
        return fallback
    same_diet_indexes = [idx for (diet, _area), idx in quantity_index.items() if diet == diet_key]
    if len(same_diet_indexes) == 1:
        return same_diet_indexes[0]
    return None


def _clone_sheet_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cloned: list[dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        values = row.get("values")
        copied["values"] = list(values) if isinstance(values, list) else []
        cloned.append(copied)
    return cloned


def _parse_sheet_quantity_cell(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = (
        text.replace("<br>", " ")
        .replace("<br/>", " ")
        .replace("<br />", " ")
        .translate(_SHEET_TRANSLATION)
    )
    compact = re.sub(r"[\s　]+", "", normalized)
    compact = compact.replace(",", "").replace("，", "")
    compact = compact.replace("．", ".").replace("。", ".")
    # Accept only pure numeric cells (with optional surrounding brackets).
    # Avoid parsing tokens from free text such as "副23" or "No.23".
    compact = compact.strip("()[]（）")
    if re.fullmatch(r"-?\d+\.", compact):
        compact = compact[:-1]
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", compact):
        # Limited OCR single-glyph rescue for quantity columns only.
        # Keep this intentionally narrow to avoid false positives.
        single_char_map = {
            "o": "0",
            "O": "0",
            "〇": "0",
            "○": "0",
            "l": "1",
            "I": "1",
            "|": "1",
            "s": "5",
            "S": "5",
            "B": "8",
            "g": "9",
            "q": "9",
            "Q": "9",
        }
        if len(compact) == 1 and compact in single_char_map:
            compact = single_char_map[compact]
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", compact):
        return None
    token = compact
    try:
        parsed = float(token)
    except Exception:
        return None
    # Meal counts are integers. OCR often emits split digits as "2.1"/"1.5";
    # when the token is a single-digit decimal pair, restore it as two digits.
    if not float(parsed).is_integer():
        if re.fullmatch(r"\d\.\d", token):
            parsed = float(token.replace(".", ""))
        else:
            return None
    # Guard against OCR garbage values (e.g. 3000/8000) that frequently appear
    # in free text/noisy rows and must not be applied as meal counts.
    try:
        max_abs = float(os.getenv("OCR_SHEET_MAX_QTY", "50"))
    except Exception:
        max_abs = 50.0
    if parsed < 0:
        return None
    if max_abs > 0 and abs(parsed) > max_abs:
        return None
    return parsed


def _cell_contains_explicit_span_marker(value: object) -> bool:
    text = _field_value_to_str(value).strip()
    if not text:
        return False
    normalized = (
        text.replace("<br>", " ")
        .replace("<br/>", " ")
        .replace("<br />", " ")
        .translate(_SHEET_TRANSLATION)
    )
    compact = re.sub(r"[\s　]+", "", normalized)
    if not compact:
        return False
    if "->" in compact or "→" in compact or "←" in compact or "↔" in compact or "↕" in compact:
        return True
    marker_count = 0
    for marker in (")", "）", "]", "】", "}", "｝", "|", "｜", "¦"):
        marker_count += compact.count(marker)
    return marker_count >= 2


def _parse_explicit_span_quantity_cell(value: object) -> tuple[float, int] | None:
    if not _cell_contains_explicit_span_marker(value):
        return None
    text = _field_value_to_str(value).strip()
    if not text:
        return None
    normalized = (
        text.replace("<br>", " ")
        .replace("<br/>", " ")
        .replace("<br />", " ")
        .translate(_SHEET_TRANSLATION)
    )
    compact = re.sub(r"[\s　]+", "", normalized)
    compact = compact.replace(",", "").replace("，", "")
    compact = compact.replace("．", ".").replace("。", ".")
    if not compact:
        return None
    # Ignore plain "(20)" style tokens that do not represent a span.
    stripped = compact.strip("()[]{}（）［］【】｛｝")
    if re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
        return None

    raw_tokens = re.findall(r"-?\d+(?:\.\d+)?", compact)
    if not raw_tokens:
        return None
    parsed_tokens: list[float] = []
    for token in raw_tokens:
        parsed = _parse_sheet_quantity_cell(token)
        if parsed is None:
            continue
        parsed_tokens.append(parsed)
    if not parsed_tokens:
        return None

    counts: dict[float, int] = {}
    for parsed in parsed_tokens:
        counts[parsed] = int(counts.get(parsed, 0)) + 1
    dominant_qty, dominant_count = max(counts.items(), key=lambda item: (item[1], item[0]))
    if len(counts) == 1:
        return dominant_qty, dominant_count
    total = len(parsed_tokens)
    if dominant_count >= 2 and (dominant_count / max(total, 1)) >= 0.6:
        return dominant_qty, dominant_count
    return None


def _apply_payload_quantities_by_row_index(
    *,
    rows: list[dict[str, Any]],
    quantity_index: dict[tuple[str, str], int],
    payload_rows: list[list[str]],
) -> None:
    if not rows or not quantity_index or not payload_rows:
        return
    quantity_columns = sorted(set(quantity_index.values()))
    for row_idx, payload_row in enumerate(payload_rows):
        if row_idx < 0 or row_idx >= len(rows):
            continue
        if not isinstance(payload_row, list):
            continue
        target = rows[row_idx]
        values = target.get("values")
        if not isinstance(values, list):
            continue
        for col_idx in quantity_columns:
            if col_idx >= len(payload_row):
                continue
            qty = _parse_sheet_quantity_cell(payload_row[col_idx])
            if qty is None:
                continue
            while len(values) <= col_idx:
                values.append("")
            values[col_idx] = _numeric_string_add(str(values[col_idx] or ""), qty)


def _resolve_sheet_field_indexes(fields: list[str]) -> tuple[int | None, int | None, int | None]:
    date_idx = next((idx for idx, field in enumerate(fields) if str(field).startswith("date")), None)
    daypart_idx = fields.index("daypart") if "daypart" in fields else None
    if "menu" in fields:
        menu_idx = fields.index("menu")
    elif "menu_name" in fields:
        menu_idx = fields.index("menu_name")
    else:
        menu_idx = None
    return date_idx, daypart_idx, menu_idx


def _safe_row_get(values: list[Any], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(values):
        return ""
    return _field_value_to_str(values[idx])


def _build_neighbor_menu_refs(items: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    previous: list[str] = []
    next_values: list[str] = [""] * len(items)

    last = ""
    for item in items:
        previous.append(last)
        current = str(item.get("menu_norm") or "")
        if current:
            last = current

    nxt = ""
    for idx in range(len(items) - 1, -1, -1):
        next_values[idx] = nxt
        current = str(items[idx].get("menu_norm") or "")
        if current:
            nxt = current
    return previous, next_values


def _build_payload_row_mapping_by_menu_priority(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    payload_rows: list[list[str]],
    quantity_index: dict[tuple[str, str], int] | None = None,
) -> tuple[dict[int, int], dict[str, int], dict[int, str]]:
    if not rows or not payload_rows:
        return {}, {"exact": 0, "partial": 0, "neighbor": 0, "row_index": 0}, {}

    date_idx, daypart_idx, menu_idx = _resolve_sheet_field_indexes(fields)
    if menu_idx is None:
        fallback = {
            idx: idx
            for idx in range(min(len(rows), len(payload_rows)))
        }
        fallback_stage = {idx: "row_index" for idx in fallback.keys()}
        return fallback, {"exact": 0, "partial": 0, "neighbor": 0, "row_index": len(fallback)}, fallback_stage

    sheet_items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        values = row.get("values")
        if not isinstance(values, list):
            values = []
        menu_raw = _safe_row_get(values, menu_idx)
        sheet_items.append(
            {
                "idx": idx,
                "menu_raw": menu_raw,
                "menu_norm": _normalize_menu_text(menu_raw),
                "date_norm": _normalize_sheet_date_key(_safe_row_get(values, date_idx)),
                "daypart_norm": _normalize_sheet_text(_safe_row_get(values, daypart_idx)),
            }
        )

    payload_items: list[dict[str, Any]] = []
    for idx, payload_row in enumerate(payload_rows):
        row_values = payload_row if isinstance(payload_row, list) else []
        menu_raw = _safe_row_get(row_values, menu_idx)
        payload_items.append(
            {
                "idx": idx,
                "menu_raw": menu_raw,
                "menu_norm": _normalize_menu_text(menu_raw),
                "date_norm": _normalize_sheet_date_key(_safe_row_get(row_values, date_idx)),
                "daypart_norm": _normalize_sheet_text(_safe_row_get(row_values, daypart_idx)),
            }
        )
    last_payload_date = ""
    for item in payload_items:
        current_date = str(item.get("date_norm") or "")
        if current_date:
            last_payload_date = current_date
        elif last_payload_date:
            item["date_norm"] = last_payload_date

    quantity_columns = (
        sorted(set(quantity_index.values()))
        if isinstance(quantity_index, dict) and quantity_index
        else []
    )
    sheet_menu_norms = [
        str(item.get("menu_norm") or "")
        for item in sheet_items
        if str(item.get("menu_norm") or "")
    ]
    try:
        row_index_window = max(0, int(os.getenv("OCR_SHEET_ROW_INDEX_WINDOW", "3")))
    except Exception:
        row_index_window = 3

    def _payload_row_has_qty(payload_idx: int) -> bool:
        if payload_idx < 0 or payload_idx >= len(payload_rows):
            return False
        payload_row = payload_rows[payload_idx]
        if not isinstance(payload_row, list):
            return False
        if not quantity_columns:
            return True
        return _payload_row_has_numeric_quantity(payload_row, quantity_columns)

    def _payload_row_has_menu(payload_idx: int) -> bool:
        if payload_idx < 0 or payload_idx >= len(payload_items):
            return False
        return bool(str(payload_items[payload_idx].get("menu_norm") or ""))

    def _payload_row_has_loose_numeric(payload_idx: int) -> bool:
        if payload_idx < 0 or payload_idx >= len(payload_rows):
            return False
        payload_row = payload_rows[payload_idx]
        if not isinstance(payload_row, list):
            return False
        skip_columns = set(quantity_columns)
        if date_idx is not None:
            skip_columns.add(date_idx)
        if daypart_idx is not None:
            skip_columns.add(daypart_idx)
        if menu_idx is not None:
            skip_columns.add(menu_idx)
        return bool(_payload_row_numeric_candidates(payload_row, skip_columns=skip_columns))

    def _payload_row_has_assignable_signal(payload_idx: int) -> bool:
        if _payload_row_has_qty(payload_idx):
            return True
        if _payload_row_has_menu(payload_idx):
            return True
        return _payload_row_has_loose_numeric(payload_idx)

    def _payload_row_is_row_index_eligible(payload_idx: int, *, sheet_idx: int | None = None) -> bool:
        if payload_idx < 0 or payload_idx >= len(payload_rows):
            return False
        payload_row = payload_rows[payload_idx]
        if not isinstance(payload_row, list):
            return False
        if not _payload_row_has_assignable_signal(payload_idx):
            return False
        if sheet_idx is not None:
            if row_index_window > 0 and abs(sheet_idx - payload_idx) > row_index_window:
                return False
            if 0 <= sheet_idx < len(sheet_items):
                sheet_date = str(sheet_items[sheet_idx].get("date_norm") or "")
                payload_date = str(payload_items[payload_idx].get("date_norm") or "")
                if sheet_date and payload_date and sheet_date != payload_date:
                    return False
        menu_norm = str(payload_items[payload_idx].get("menu_norm") or "")
        if not menu_norm:
            return True
        for sheet_menu in sheet_menu_norms:
            if menu_norm in sheet_menu or sheet_menu in menu_norm:
                return True
            if SequenceMatcher(None, menu_norm, sheet_menu).ratio() >= 0.62:
                return True
        return False

    sheet_prev, sheet_next = _build_neighbor_menu_refs(sheet_items)
    payload_prev, payload_next = _build_neighbor_menu_refs(payload_items)
    for idx, item in enumerate(sheet_items):
        item["prev_menu"] = sheet_prev[idx]
        item["next_menu"] = sheet_next[idx]
    for idx, item in enumerate(payload_items):
        item["prev_menu"] = payload_prev[idx]
        item["next_menu"] = payload_next[idx]

    mapping: dict[int, int] = {}
    used_payload: set[int] = set()
    mapping_stage: dict[int, str] = {}
    stage_counts = {"exact": 0, "partial": 0, "neighbor": 0, "row_index": 0}

    def _mark(stage: str, sheet_idx: int, payload_idx: int) -> None:
        mapping[sheet_idx] = payload_idx
        used_payload.add(payload_idx)
        mapping_stage[sheet_idx] = stage
        stage_counts[stage] += 1

    def _score_date_day(sheet_item: dict[str, Any], payload_item: dict[str, Any]) -> tuple[int, int]:
        date_match = int(
            bool(sheet_item.get("date_norm"))
            and bool(payload_item.get("date_norm"))
            and sheet_item.get("date_norm") == payload_item.get("date_norm")
        )
        daypart_match = int(
            bool(sheet_item.get("daypart_norm"))
            and bool(payload_item.get("daypart_norm"))
            and sheet_item.get("daypart_norm") == payload_item.get("daypart_norm")
        )
        return date_match, daypart_match

    # 1) Exact menu match
    for sheet_item in sheet_items:
        sheet_idx = int(sheet_item["idx"])
        if sheet_idx in mapping:
            continue
        menu_norm = str(sheet_item.get("menu_norm") or "")
        if not menu_norm:
            continue
        candidates: list[tuple[tuple[int, int, int], int]] = []
        for payload_item in payload_items:
            payload_idx = int(payload_item["idx"])
            if payload_idx in used_payload:
                continue
            if not _payload_row_has_assignable_signal(payload_idx):
                continue
            if menu_norm != str(payload_item.get("menu_norm") or ""):
                continue
            date_match, daypart_match = _score_date_day(sheet_item, payload_item)
            score = (date_match, daypart_match, -abs(sheet_idx - payload_idx))
            candidates.append((score, payload_idx))
        if candidates:
            _, selected = max(candidates, key=lambda item: item[0])
            _mark("exact", sheet_idx, selected)

    # 2) Partial menu match
    for sheet_item in sheet_items:
        sheet_idx = int(sheet_item["idx"])
        if sheet_idx in mapping:
            continue
        menu_norm = str(sheet_item.get("menu_norm") or "")
        if not menu_norm:
            continue
        candidates: list[tuple[tuple[float, int, int, int], int]] = []
        for payload_item in payload_items:
            payload_idx = int(payload_item["idx"])
            if payload_idx in used_payload:
                continue
            if not _payload_row_has_assignable_signal(payload_idx):
                continue
            payload_menu = str(payload_item.get("menu_norm") or "")
            if not payload_menu:
                continue
            ratio = SequenceMatcher(None, menu_norm, payload_menu).ratio()
            contains = menu_norm in payload_menu or payload_menu in menu_norm
            if not contains and ratio < 0.72:
                continue
            date_match, daypart_match = _score_date_day(sheet_item, payload_item)
            score = (ratio, date_match, daypart_match, -abs(sheet_idx - payload_idx))
            candidates.append((score, payload_idx))
        if candidates:
            _, selected = max(candidates, key=lambda item: item[0])
            _mark("partial", sheet_idx, selected)

    # 3) Neighbor menu match
    for sheet_item in sheet_items:
        sheet_idx = int(sheet_item["idx"])
        if sheet_idx in mapping:
            continue
        candidates: list[tuple[tuple[int, int, int, int], int]] = []
        for payload_item in payload_items:
            payload_idx = int(payload_item["idx"])
            if payload_idx in used_payload:
                continue
            if not _payload_row_has_assignable_signal(payload_idx):
                continue
            payload_menu = str(payload_item.get("menu_norm") or "")
            if not payload_menu:
                continue
            prev_match = int(
                bool(sheet_item.get("prev_menu"))
                and bool(payload_item.get("prev_menu"))
                and sheet_item.get("prev_menu") == payload_item.get("prev_menu")
            )
            next_match = int(
                bool(sheet_item.get("next_menu"))
                and bool(payload_item.get("next_menu"))
                and sheet_item.get("next_menu") == payload_item.get("next_menu")
            )
            neighbor_hits = prev_match + next_match
            if neighbor_hits <= 0:
                continue
            date_match, daypart_match = _score_date_day(sheet_item, payload_item)
            score = (neighbor_hits, date_match, daypart_match, -abs(sheet_idx - payload_idx))
            candidates.append((score, payload_idx))
        if candidates:
            _, selected = max(candidates, key=lambda item: item[0])
            _mark("neighbor", sheet_idx, selected)

    # 4) Row index fallback
    reserved_direct_payload: set[int] = set()
    for sheet_item in sheet_items:
        sheet_idx = int(sheet_item["idx"])
        if sheet_idx in mapping:
            continue
        if (
            0 <= sheet_idx < len(payload_items)
            and sheet_idx not in used_payload
            and _payload_row_is_row_index_eligible(sheet_idx, sheet_idx=sheet_idx)
        ):
            reserved_direct_payload.add(sheet_idx)

    for sheet_item in sheet_items:
        sheet_idx = int(sheet_item["idx"])
        if sheet_idx in mapping:
            continue
        direct = sheet_idx
        if (
            0 <= direct < len(payload_items)
            and direct not in used_payload
            and _payload_row_is_row_index_eligible(direct, sheet_idx=sheet_idx)
        ):
            _mark("row_index", sheet_idx, direct)
            continue
        candidates: list[tuple[int, int]] = []
        for payload_item in payload_items:
            payload_idx = int(payload_item["idx"])
            if payload_idx in used_payload:
                continue
            if payload_idx in reserved_direct_payload and payload_idx != sheet_idx:
                continue
            if not _payload_row_is_row_index_eligible(payload_idx, sheet_idx=sheet_idx):
                continue
            candidates.append((abs(sheet_idx - payload_idx), payload_idx))
        if candidates:
            _, selected = min(candidates, key=lambda item: item[0])
            _mark("row_index", sheet_idx, selected)

    return mapping, stage_counts, mapping_stage


def _build_sheet_cluster_starts(rows: list[dict[str, Any]], fields: list[str]) -> dict[int, int]:
    if not rows:
        return {}
    date_idx, daypart_idx, _ = _resolve_sheet_field_indexes(fields)
    cluster_starts: dict[int, int] = {}
    current_key: tuple[str, str] | None = None
    current_start = 0
    for idx, row in enumerate(rows):
        values = row.get("values")
        if not isinstance(values, list):
            values = []
        key = (
            _normalize_sheet_text(_safe_row_get(values, date_idx)),
            _normalize_sheet_text(_safe_row_get(values, daypart_idx)),
        )
        if current_key is None or key != current_key:
            current_key = key
            current_start = idx
        cluster_starts[idx] = current_start
    return cluster_starts


def _payload_row_has_numeric_quantity(
    payload_row: list[Any],
    quantity_columns: list[int],
) -> bool:
    for col_idx in quantity_columns:
        if col_idx < 0 or col_idx >= len(payload_row):
            continue
        qty = _parse_sheet_quantity_cell(payload_row[col_idx])
        if qty is not None:
            return True
        span_candidate = _parse_explicit_span_quantity_cell(payload_row[col_idx])
        if span_candidate is not None:
            return True
    return False


def _extract_loose_numeric_tokens(value: object) -> list[float]:
    text = _field_value_to_str(value).strip()
    if not text:
        return []
    normalized = (
        text.replace("<br>", " ")
        .replace("<br/>", " ")
        .replace("<br />", " ")
        .translate(_SHEET_TRANSLATION)
    )
    compact = re.sub(r"[\s　]+", "", normalized)
    if not compact:
        return []
    # Avoid dates/time/page fragments.
    if re.search(r"\d{1,2}[/-]\d{1,2}", compact):
        return []
    if re.search(r"\d{1,2}:\d{2}", compact):
        return []
    tokens: list[float] = []
    try:
        max_abs = float(os.getenv("OCR_SHEET_MAX_QTY", "150"))
    except Exception:
        max_abs = 150.0
    for raw in re.findall(r"(?<!\d)(-?\d{1,3}(?:\.\d+)?)(?!\d)", compact):
        try:
            parsed = float(raw)
        except Exception:
            continue
        if parsed <= 0:
            continue
        if max_abs > 0 and parsed > max_abs:
            continue
        tokens.append(parsed)
    return tokens


def _payload_row_numeric_candidates(
    payload_row: list[Any],
    *,
    skip_columns: set[int],
) -> list[float]:
    values: list[float] = []
    seen: set[float] = set()
    for idx, cell in enumerate(payload_row):
        if idx in skip_columns:
            continue
        direct = _parse_sheet_quantity_cell(cell)
        if direct is not None and direct > 0 and direct not in seen:
            seen.add(direct)
            values.append(direct)
            continue
        for token in _extract_loose_numeric_tokens(cell):
            if token in seen:
                continue
            seen.add(token)
            values.append(token)
    return values


def _extract_payload_row_span_hint(
    payload_row: list[Any],
    *,
    quantity_columns: list[int],
) -> int:
    if not isinstance(payload_row, list) or not payload_row:
        return 0
    skip_columns = set(quantity_columns)
    hint = 0
    has_arrow = False
    for idx, cell in enumerate(payload_row):
        if idx in skip_columns:
            continue
        text = _field_value_to_str(cell).strip()
        if not text:
            continue
        normalized = (
            text.replace("<br>", " ")
            .replace("<br/>", " ")
            .replace("<br />", " ")
            .translate(_SHEET_TRANSLATION)
        )
        compact = re.sub(r"[\s　]+", "", normalized)
        if not compact:
            continue
        for match in re.finditer(r"[（(]\s*(\d{1,2})\s*[)）]", compact):
            try:
                parsed = int(match.group(1))
            except Exception:
                continue
            if 1 <= parsed <= 8:
                hint = max(hint, parsed)
        if "->" in compact or "→" in compact or "←" in compact:
            has_arrow = True
        marker_count = compact.count(")") + compact.count("）")
        if marker_count >= 2 and hint <= 0:
            hint = max(hint, 2)
    if hint <= 0 and has_arrow:
        hint = 2
    return hint


def _select_trusted_payload_quantity_columns(
    *,
    payload_rows: list[list[str]],
    quantity_columns: list[int],
) -> tuple[list[int], dict[int, int], dict[int, int]]:
    parsed_hits: dict[int, int] = {col_idx: 0 for col_idx in quantity_columns}
    non_empty_hits: dict[int, int] = {col_idx: 0 for col_idx in quantity_columns}
    if not payload_rows or not quantity_columns:
        return list(quantity_columns), parsed_hits, non_empty_hits

    for payload_row in payload_rows:
        if not isinstance(payload_row, list):
            continue
        for col_idx in quantity_columns:
            if col_idx >= len(payload_row):
                continue
            raw = _field_value_to_str(payload_row[col_idx]).strip()
            if not raw:
                continue
            non_empty_hits[col_idx] = int(non_empty_hits.get(col_idx, 0)) + 1
            qty = _parse_sheet_quantity_cell(payload_row[col_idx])
            if qty is None:
                continue
            parsed_hits[col_idx] = int(parsed_hits.get(col_idx, 0)) + 1

    try:
        min_non_empty = max(1, int(os.getenv("OCR_SHEET_QTY_COLUMN_MIN_NON_EMPTY", "6")))
    except Exception:
        min_non_empty = 6
    try:
        min_purity = float(os.getenv("OCR_SHEET_QTY_COLUMN_MIN_PURITY", "0.6"))
    except Exception:
        min_purity = 0.6
    if min_purity <= 0 or min_purity > 1:
        min_purity = 0.6

    trusted: list[int] = []
    for col_idx in quantity_columns:
        non_empty = int(non_empty_hits.get(col_idx, 0))
        parsed = int(parsed_hits.get(col_idx, 0))
        if parsed <= 0:
            # Column has no numeric parse; keep it neutral (no writes will happen).
            trusted.append(col_idx)
            continue
        if non_empty >= min_non_empty:
            purity = parsed / max(non_empty, 1)
            if purity < min_purity:
                continue
        trusted.append(col_idx)

    if not trusted:
        trusted = [col_idx for col_idx in quantity_columns if int(parsed_hits.get(col_idx, 0)) > 0]
    if not trusted:
        trusted = list(quantity_columns)
    return trusted, parsed_hits, non_empty_hits


def _row_has_any_quantity(values: list[Any], quantity_columns: list[int]) -> bool:
    for col_idx in quantity_columns:
        if col_idx < 0 or col_idx >= len(values):
            continue
        qty = _parse_sheet_quantity_cell(values[col_idx])
        if qty is None:
            continue
        return True
    return False


def _row_quantity_value(values: list[Any], col_idx: int) -> float | None:
    if col_idx < 0 or col_idx >= len(values):
        return None
    return _parse_sheet_quantity_cell(values[col_idx])


def _set_row_quantity_value(values: list[Any], col_idx: int, qty: float) -> None:
    if col_idx < 0:
        return
    while len(values) <= col_idx:
        values.append("")
    values[col_idx] = _numeric_string_add(str(values[col_idx] or ""), qty)


def _append_note_cell(current: str, addition: str) -> str:
    base = str(current or "").strip()
    extra = str(addition or "").strip()
    if not extra:
        return base
    if not base:
        return extra
    if extra in base:
        return base
    return f"{base} / {extra}"


def _fill_isolated_sheet_quantity_gaps(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_columns: list[int],
    preferred_columns: list[int],
) -> int:
    if len(rows) < 3 or not quantity_columns:
        return 0
    cluster_starts = _build_sheet_cluster_starts(rows, fields)
    target_columns = preferred_columns or quantity_columns
    filled = 0
    for row_idx in range(1, len(rows) - 1):
        prev_idx = row_idx - 1
        next_idx = row_idx + 1
        if cluster_starts.get(prev_idx) != cluster_starts.get(row_idx):
            continue
        if cluster_starts.get(next_idx) != cluster_starts.get(row_idx):
            continue
        current_values = rows[row_idx].get("values")
        prev_values = rows[prev_idx].get("values")
        next_values = rows[next_idx].get("values")
        if not isinstance(current_values, list) or not isinstance(prev_values, list) or not isinstance(next_values, list):
            continue
        if _row_has_any_quantity(current_values, quantity_columns):
            continue
        for col_idx in target_columns:
            prev_qty = _row_quantity_value(prev_values, col_idx)
            next_qty = _row_quantity_value(next_values, col_idx)
            if prev_qty is None or next_qty is None:
                continue
            if abs(prev_qty - next_qty) > 0.0001:
                continue
            _set_row_quantity_value(current_values, col_idx, prev_qty)
            filled += 1
            break
    return filled


def _fill_cluster_consensus_quantities(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_columns: list[int],
) -> int:
    if not rows or not quantity_columns:
        return 0
    cluster_starts = _build_sheet_cluster_starts(rows, fields)
    ranges: list[tuple[int, int]] = []
    if rows:
        start = 0
        current_cluster = cluster_starts.get(0, 0)
        for idx in range(1, len(rows)):
            if cluster_starts.get(idx) != current_cluster:
                ranges.append((start, idx - 1))
                start = idx
                current_cluster = cluster_starts.get(idx, idx)
        ranges.append((start, len(rows) - 1))

    filled = 0
    for start_idx, end_idx in ranges:
        cluster_len = end_idx - start_idx + 1
        if cluster_len <= 1:
            continue
        for col_idx in quantity_columns:
            counts: dict[float, int] = {}
            non_empty = 0
            for row_idx in range(start_idx, end_idx + 1):
                values = rows[row_idx].get("values")
                if not isinstance(values, list):
                    continue
                qty = _row_quantity_value(values, col_idx)
                if qty is None:
                    continue
                non_empty += 1
                counts[qty] = int(counts.get(qty, 0)) + 1
            missing = cluster_len - non_empty
            if missing <= 0 or not counts:
                continue

            best_qty, best_count = max(counts.items(), key=lambda item: (item[1], item[0]))
            should_fill = False
            if non_empty == 1:
                # Span-written fax styles frequently place one quantity for a
                # 2-3 row daypart cluster. Mirror only for small clusters.
                should_fill = cluster_len in {2, 3} and missing == (cluster_len - 1)
            else:
                dominance = best_count / max(non_empty, 1)
                should_fill = best_count >= 2 and dominance >= 0.8
            if not should_fill:
                continue

            for row_idx in range(start_idx, end_idx + 1):
                values = rows[row_idx].get("values")
                if not isinstance(values, list):
                    continue
                if _row_quantity_value(values, col_idx) is not None:
                    continue
                _set_row_quantity_value(values, col_idx, best_qty)
                filled += 1
    return filled


def _fill_blank_daypart_clusters_by_consensus(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_columns: list[int],
) -> int:
    if not rows or not quantity_columns:
        return 0
    _date_idx, daypart_idx, _menu_idx = _resolve_sheet_field_indexes(fields)
    if daypart_idx is None:
        return 0

    cluster_starts = _build_sheet_cluster_starts(rows, fields)
    cluster_ranges: list[tuple[int, int]] = []
    if rows:
        start = 0
        current_cluster = cluster_starts.get(0, 0)
        for idx in range(1, len(rows)):
            if cluster_starts.get(idx) != current_cluster:
                cluster_ranges.append((start, idx - 1))
                start = idx
                current_cluster = cluster_starts.get(idx, idx)
        cluster_ranges.append((start, len(rows) - 1))
    if not cluster_ranges:
        return 0

    try:
        min_dominance = float(os.getenv("OCR_SHEET_DAYPART_CONSENSUS_MIN_DOMINANCE", "0.85"))
    except Exception:
        min_dominance = 0.85
    if min_dominance <= 0:
        min_dominance = 0.85
    if min_dominance > 1:
        min_dominance = 1.0
    try:
        edge_min_support = max(2, int(os.getenv("OCR_SHEET_DAYPART_CONSENSUS_EDGE_MIN_SUPPORT", "3")))
    except Exception:
        edge_min_support = 3
    try:
        edge_min_dominance = float(os.getenv("OCR_SHEET_DAYPART_CONSENSUS_EDGE_MIN_DOMINANCE", "0.95"))
    except Exception:
        edge_min_dominance = 0.95
    if edge_min_dominance <= 0:
        edge_min_dominance = 0.95
    if edge_min_dominance > 1:
        edge_min_dominance = 1.0

    cluster_infos: list[dict[str, Any]] = []
    daypart_clusters: dict[str, list[int]] = {}
    for start_idx, end_idx in cluster_ranges:
        sample_values = rows[start_idx].get("values")
        if not isinstance(sample_values, list):
            sample_values = []
        daypart_key = _normalize_sheet_text(_safe_row_get(sample_values, daypart_idx))
        cluster_idx = len(cluster_infos)
        daypart_clusters.setdefault(daypart_key, []).append(cluster_idx)

        non_empty_by_col: dict[int, int] = {}
        representative_by_col: dict[int, float | None] = {}
        for col_idx in quantity_columns:
            counts: dict[float, int] = {}
            non_empty = 0
            for row_idx in range(start_idx, end_idx + 1):
                values = rows[row_idx].get("values")
                if not isinstance(values, list):
                    continue
                qty = _row_quantity_value(values, col_idx)
                if qty is None:
                    continue
                non_empty += 1
                counts[qty] = int(counts.get(qty, 0)) + 1
            non_empty_by_col[col_idx] = non_empty
            if not counts:
                representative_by_col[col_idx] = None
                continue
            best_qty, best_count = max(counts.items(), key=lambda item: (item[1], item[0]))
            if non_empty == 1:
                representative_by_col[col_idx] = best_qty
                continue
            dominance = best_count / max(non_empty, 1)
            representative_by_col[col_idx] = best_qty if best_count >= 2 and dominance >= 0.8 else None

        cluster_infos.append(
            {
                "start": start_idx,
                "end": end_idx,
                "non_empty_by_col": non_empty_by_col,
                "representative_by_col": representative_by_col,
            }
        )

    filled = 0
    for col_idx in quantity_columns:
        for daypart_key, cluster_indexes in daypart_clusters.items():
            if not daypart_key:
                continue
            observed: list[tuple[int, float]] = []
            for cluster_idx in cluster_indexes:
                representative = cluster_infos[cluster_idx]["representative_by_col"].get(col_idx)
                if representative is None or representative <= 0:
                    continue
                observed.append((cluster_idx, float(representative)))
            if len(observed) < 2:
                continue

            observed_counts: dict[float, int] = {}
            for _cluster_idx, quantity in observed:
                observed_counts[quantity] = int(observed_counts.get(quantity, 0)) + 1
            dominant_qty, dominant_count = max(observed_counts.items(), key=lambda item: (item[1], item[0]))
            dominance = dominant_count / max(len(observed), 1)
            if dominance < min_dominance:
                continue

            for cluster_idx in cluster_indexes:
                non_empty = int(cluster_infos[cluster_idx]["non_empty_by_col"].get(col_idx, 0))
                if non_empty > 0:
                    continue

                start_idx = int(cluster_infos[cluster_idx]["start"])
                end_idx = int(cluster_infos[cluster_idx]["end"])
                cluster_len = end_idx - start_idx + 1
                if cluster_len not in {2, 3}:
                    continue

                prev_qty: float | None = None
                next_qty: float | None = None
                for observed_idx, observed_qty in reversed(observed):
                    if observed_idx < cluster_idx:
                        prev_qty = observed_qty
                        break
                for observed_idx, observed_qty in observed:
                    if observed_idx > cluster_idx:
                        next_qty = observed_qty
                        break

                should_fill = False
                if prev_qty is not None and next_qty is not None:
                    should_fill = (
                        abs(prev_qty - dominant_qty) <= 0.0001 and abs(next_qty - dominant_qty) <= 0.0001
                    )
                elif prev_qty is None and next_qty is not None:
                    should_fill = (
                        abs(next_qty - dominant_qty) <= 0.0001
                        and len(observed) >= edge_min_support
                        and dominance >= edge_min_dominance
                    )
                elif next_qty is None and prev_qty is not None:
                    should_fill = (
                        abs(prev_qty - dominant_qty) <= 0.0001
                        and len(observed) >= edge_min_support
                        and dominance >= edge_min_dominance
                    )
                if not should_fill:
                    continue

                for row_idx in range(start_idx, end_idx + 1):
                    values = rows[row_idx].get("values")
                    if not isinstance(values, list):
                        continue
                    if _row_quantity_value(values, col_idx) is not None:
                        continue
                    _set_row_quantity_value(values, col_idx, dominant_qty)
                    filled += 1

    return filled


def _apply_explicit_span_quantity_copy(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    span_copy_hints: dict[tuple[int, int], int],
) -> int:
    if not rows or not span_copy_hints:
        return 0
    cluster_starts = _build_sheet_cluster_starts(rows, fields)
    cluster_end_by_start: dict[int, int] = {}
    for row_idx in range(len(rows)):
        start = int(cluster_starts.get(row_idx, row_idx))
        cluster_end_by_start[start] = row_idx

    filled = 0
    for (row_idx, col_idx), repeat_count in sorted(span_copy_hints.items()):
        if row_idx < 0 or row_idx >= len(rows):
            continue
        values = rows[row_idx].get("values")
        if not isinstance(values, list):
            continue
        qty = _row_quantity_value(values, col_idx)
        if qty is None:
            continue
        start_idx = int(cluster_starts.get(row_idx, row_idx))
        end_idx = int(cluster_end_by_start.get(start_idx, row_idx))
        if end_idx <= row_idx:
            continue
        remaining = max(0, int(repeat_count) - 1)
        if remaining <= 0:
            continue
        for target_idx in range(row_idx + 1, end_idx + 1):
            if remaining <= 0:
                break
            target_values = rows[target_idx].get("values")
            if not isinstance(target_values, list):
                continue
            if _row_quantity_value(target_values, col_idx) is not None:
                continue
            _set_row_quantity_value(target_values, col_idx, qty)
            filled += 1
            remaining -= 1
    return filled


def _apply_payload_row_span_hints(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_columns: list[int],
    span_row_hints: dict[int, int],
) -> int:
    if not rows or not quantity_columns or not span_row_hints:
        return 0
    cluster_starts = _build_sheet_cluster_starts(rows, fields)
    cluster_end_by_start: dict[int, int] = {}
    for row_idx in range(len(rows)):
        start = int(cluster_starts.get(row_idx, row_idx))
        cluster_end_by_start[start] = row_idx

    filled = 0
    for row_idx, span_len_raw in sorted(span_row_hints.items()):
        if row_idx < 0 or row_idx >= len(rows):
            continue
        span_len = max(1, int(span_len_raw))
        start_idx = int(cluster_starts.get(row_idx, row_idx))
        end_idx = int(cluster_end_by_start.get(start_idx, row_idx))
        if end_idx < row_idx:
            continue
        current_values = rows[row_idx].get("values")
        if not isinstance(current_values, list):
            continue
        prev_values = None
        if row_idx - 1 >= start_idx:
            maybe_prev = rows[row_idx - 1].get("values")
            if isinstance(maybe_prev, list):
                prev_values = maybe_prev
        for col_idx in quantity_columns:
            anchor_qty = _row_quantity_value(current_values, col_idx)
            current_has_qty = anchor_qty is not None
            if anchor_qty is None and isinstance(prev_values, list):
                anchor_qty = _row_quantity_value(prev_values, col_idx)
            if anchor_qty is None:
                continue
            if current_has_qty:
                cursor = row_idx + 1
                remaining = max(0, span_len - 1)
            else:
                cursor = row_idx
                remaining = span_len
            while cursor <= end_idx and remaining > 0:
                target_values = rows[cursor].get("values")
                if not isinstance(target_values, list):
                    cursor += 1
                    continue
                if _row_quantity_value(target_values, col_idx) is None:
                    _set_row_quantity_value(target_values, col_idx, anchor_qty)
                    filled += 1
                    remaining -= 1
                cursor += 1
    return filled


def _apply_unstructured_quantity_candidates(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_columns: list[int],
    preferred_columns: list[int],
    candidates: list[str],
) -> int:
    if not rows or not quantity_columns or not candidates:
        return 0
    parsed_candidates: list[float] = []
    for raw in candidates:
        qty = _parse_sheet_quantity_cell(raw)
        if qty is None or qty == 0:
            continue
        parsed_candidates.append(qty)
    if not parsed_candidates:
        return 0
    cluster_starts = _build_sheet_cluster_starts(rows, fields)
    target_columns = preferred_columns or quantity_columns
    queue = list(parsed_candidates)
    filled = 0

    # 1) Trailing blank-run fill in a cluster.
    # Handles formats where one handwritten number applies to multiple bottom rows
    # and appears once as unstructured OCR text.
    cluster_ranges: list[tuple[int, int]] = []
    if rows:
        start = 0
        current_cluster = cluster_starts.get(0, 0)
        for idx in range(1, len(rows)):
            if cluster_starts.get(idx) != current_cluster:
                cluster_ranges.append((start, idx - 1))
                start = idx
                current_cluster = cluster_starts.get(idx, idx)
        cluster_ranges.append((start, len(rows) - 1))

    for start_idx, end_idx in cluster_ranges:
        if not queue:
            break
        for col_idx in target_columns:
            cursor = end_idx
            while cursor >= start_idx:
                values = rows[cursor].get("values")
                if not isinstance(values, list):
                    cursor -= 1
                    continue
                if _row_quantity_value(values, col_idx) is not None:
                    break
                cursor -= 1
            blank_run_len = end_idx - cursor
            if blank_run_len < 2 or cursor < start_idx:
                continue
            anchor_values = rows[cursor].get("values")
            if not isinstance(anchor_values, list):
                continue
            anchor_qty = _row_quantity_value(anchor_values, col_idx)
            if anchor_qty is None:
                continue
            anchor_run_start = cursor
            while anchor_run_start - 1 >= start_idx:
                prev_values = rows[anchor_run_start - 1].get("values")
                if not isinstance(prev_values, list):
                    break
                prev_qty = _row_quantity_value(prev_values, col_idx)
                if prev_qty is None or abs(prev_qty - anchor_qty) > 0.0001:
                    break
                anchor_run_start -= 1
            anchor_run_len = cursor - anchor_run_start + 1
            if anchor_run_len < 2:
                continue
            selected_idx: int | None = None
            for idx, candidate in enumerate(queue):
                if candidate >= anchor_qty:
                    continue
                if candidate > anchor_qty * 0.95:
                    continue
                selected_idx = idx
                break
            if selected_idx is None:
                continue
            selected = queue.pop(selected_idx)
            for row_idx in range(cursor + 1, end_idx + 1):
                row_values = rows[row_idx].get("values")
                if not isinstance(row_values, list):
                    continue
                if _row_quantity_value(row_values, col_idx) is not None:
                    continue
                _set_row_quantity_value(row_values, col_idx, selected)
                filled += 1
            break

    # 2) Anchor-based per-row fill.
    for row_idx, row in enumerate(rows):
        if not queue:
            break
        values = row.get("values")
        if not isinstance(values, list):
            continue
        if _row_has_any_quantity(values, quantity_columns):
            continue
        prev_idx = row_idx - 1
        next_idx = row_idx + 1
        anchor: float | None = None
        if 0 <= prev_idx < len(rows) and cluster_starts.get(prev_idx) == cluster_starts.get(row_idx):
            prev_values = rows[prev_idx].get("values")
            if isinstance(prev_values, list):
                for col_idx in target_columns:
                    prev_qty = _row_quantity_value(prev_values, col_idx)
                    if prev_qty is not None:
                        anchor = prev_qty
                        break
        next_anchor: float | None = None
        if 0 <= next_idx < len(rows) and cluster_starts.get(next_idx) == cluster_starts.get(row_idx):
            next_values = rows[next_idx].get("values")
            if isinstance(next_values, list):
                for col_idx in target_columns:
                    next_qty = _row_quantity_value(next_values, col_idx)
                    if next_qty is None:
                        continue
                    next_anchor = next_qty
                    break
        if anchor is not None and next_anchor is not None and abs(anchor - next_anchor) > 0.0001:
            continue
        if anchor is None:
            anchor = next_anchor
        if anchor is None:
            continue
        selected_idx: int | None = None
        for idx, candidate in enumerate(queue):
            if abs(candidate - anchor) <= 0.0001:
                selected_idx = idx
                break
        if selected_idx is None:
            continue
        selected = queue.pop(selected_idx)
        target_col = target_columns[0]
        _set_row_quantity_value(values, target_col, selected)
        filled += 1
    return filled


def _apply_payload_cells_by_menu_priority(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_index: dict[tuple[str, str], int],
    payload_rows: list[list[str]],
    payload_unstructured_qty: list[str] | None = None,
    allow_heuristics: bool = True,
) -> dict[str, int]:
    if not rows or not fields or not payload_rows:
        return {
            "exact": 0,
            "partial": 0,
            "neighbor": 0,
            "row_index": 0,
            "loose_cell": 0,
            "gap_fill": 0,
            "unstructured": 0,
        }

    row_mapping, stage_counts, mapping_stage = _build_payload_row_mapping_by_menu_priority(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        quantity_index=quantity_index,
    )
    stage_counts.setdefault("loose_cell", 0)
    stage_counts.setdefault("gap_fill", 0)
    stage_counts.setdefault("unstructured", 0)
    stage_counts.setdefault("span_copy", 0)
    if not row_mapping:
        return stage_counts

    quantity_columns = sorted(set(quantity_index.values()))
    trusted_quantity_columns, _raw_quantity_hits, _raw_non_empty_hits = _select_trusted_payload_quantity_columns(
        payload_rows=payload_rows,
        quantity_columns=quantity_columns,
    )
    immutable_columns = {
        idx
        for idx, field in enumerate(fields)
        if str(field).startswith("date") or field in {"daypart", "menu", "menu_name"}
    }
    note_columns = [
        idx
        for idx in range(len(fields))
        if idx not in immutable_columns and idx not in quantity_columns
    ]
    _date_idx, _daypart_idx, menu_idx = _resolve_sheet_field_indexes(fields)
    dominant_quantity_columns: set[int] = set(trusted_quantity_columns)
    if trusted_quantity_columns and menu_idx is not None:
        quantity_hits: dict[int, int] = {col_idx: 0 for col_idx in trusted_quantity_columns}
        for payload_row in payload_rows:
            if not isinstance(payload_row, list):
                continue
            payload_menu = _safe_row_get(payload_row, menu_idx)
            if not _normalize_menu_text(payload_menu):
                continue
            for col_idx in trusted_quantity_columns:
                if col_idx >= len(payload_row):
                    continue
                qty = _parse_sheet_quantity_cell(payload_row[col_idx])
                if qty is None:
                    continue
                quantity_hits[col_idx] = quantity_hits.get(col_idx, 0) + 1
        max_hit = max(quantity_hits.values()) if quantity_hits else 0
        if max_hit > 0:
            dominant_quantity_columns = {col for col, count in quantity_hits.items() if count == max_hit}
    span_copy_hints: dict[tuple[int, int], int] = {}

    def _apply_payload_row_to_sheet_row(row_idx: int, payload_idx: int) -> None:
        if row_idx < 0 or row_idx >= len(rows):
            return
        if payload_idx < 0 or payload_idx >= len(payload_rows):
            return
        payload_row = payload_rows[payload_idx]
        if not isinstance(payload_row, list):
            return
        target = rows[row_idx]
        values = target.get("values")
        if not isinstance(values, list):
            return

        # Temporary policy: quantity columns are mapped by column position.
        apply_quantity_columns = list(trusted_quantity_columns)
        stage = mapping_stage.get(row_idx)
        payload_menu = _safe_row_get(payload_row, menu_idx) if menu_idx is not None else ""
        if stage == "row_index" and not _normalize_menu_text(payload_menu) and dominant_quantity_columns:
            apply_quantity_columns = [
                col_idx for col_idx in trusted_quantity_columns if col_idx in dominant_quantity_columns
            ]

        applied_qty = False
        for col_idx in apply_quantity_columns:
            if col_idx >= len(payload_row):
                continue
            span_repeat = 0
            span_candidate = _parse_explicit_span_quantity_cell(payload_row[col_idx])
            qty = _parse_sheet_quantity_cell(payload_row[col_idx])
            if qty is None and span_candidate is not None:
                qty = span_candidate[0]
            if qty is None:
                continue
            _set_row_quantity_value(values, col_idx, qty)
            applied_qty = True
            if span_candidate is not None:
                span_repeat = int(span_candidate[1])
            if span_repeat >= 2:
                key = (row_idx, col_idx)
                span_copy_hints[key] = max(int(span_copy_hints.get(key, 0)), int(span_repeat))

        if allow_heuristics and not applied_qty and trusted_quantity_columns:
            loose_candidates = _payload_row_numeric_candidates(
                payload_row,
                skip_columns=immutable_columns | set(quantity_columns),
            )
            if loose_candidates:
                target_columns = (
                    list(dominant_quantity_columns)
                    if dominant_quantity_columns
                    else list(trusted_quantity_columns)
                )
                if target_columns:
                    _set_row_quantity_value(values, target_columns[0], loose_candidates[0])
                    stage_counts["loose_cell"] = int(stage_counts.get("loose_cell", 0)) + 1

        for col_idx in note_columns:
            if col_idx >= len(payload_row):
                continue
            note_value = _field_value_to_str(payload_row[col_idx]).strip()
            if not note_value or note_value == "-":
                continue
            while len(values) <= col_idx:
                values.append("")
            values[col_idx] = _append_note_cell(str(values[col_idx] or ""), note_value)

    for row_idx, payload_idx in row_mapping.items():
        _apply_payload_row_to_sheet_row(row_idx, payload_idx)

    span_filled = _apply_explicit_span_quantity_copy(
        rows=rows,
        fields=fields,
        span_copy_hints=span_copy_hints,
    )
    if span_filled > 0:
        stage_counts["span_copy"] = int(stage_counts.get("span_copy", 0)) + span_filled

    if allow_heuristics:
        preferred_columns = (
            sorted(dominant_quantity_columns) if dominant_quantity_columns else list(trusted_quantity_columns)
        )
        gap_filled = _fill_isolated_sheet_quantity_gaps(
            rows=rows,
            fields=fields,
            quantity_columns=trusted_quantity_columns,
            preferred_columns=preferred_columns,
        )
        if gap_filled > 0:
            stage_counts["gap_fill"] = int(stage_counts.get("gap_fill", 0)) + gap_filled

        unstructured_filled = _apply_unstructured_quantity_candidates(
            rows=rows,
            fields=fields,
            quantity_columns=trusted_quantity_columns,
            preferred_columns=preferred_columns,
            candidates=list(payload_unstructured_qty or []),
        )
        if unstructured_filled > 0:
            stage_counts["unstructured"] = int(stage_counts.get("unstructured", 0)) + unstructured_filled

    return stage_counts


def _build_payload_row_mapping_by_row_index_numeric_only(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    payload_rows: list[list[str]],
    quantity_columns: list[int],
) -> tuple[dict[int, int], dict[str, int], dict[int, str]]:
    stage_counts = {"exact": 0, "partial": 0, "neighbor": 0, "row_index": 0}
    if not rows or not payload_rows:
        return {}, stage_counts, {}

    date_idx, daypart_idx, menu_idx = _resolve_sheet_field_indexes(fields)
    skip_columns = set(quantity_columns)
    if date_idx is not None:
        skip_columns.add(date_idx)
    if daypart_idx is not None:
        skip_columns.add(daypart_idx)
    if menu_idx is not None:
        skip_columns.add(menu_idx)

    try:
        row_index_window = max(0, int(os.getenv("OCR_SHEET_ROW_INDEX_WINDOW", "3")))
    except Exception:
        row_index_window = 3

    sheet_items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        values = row.get("values")
        if not isinstance(values, list):
            values = []
        sheet_items.append(
            {
                "idx": idx,
                "date_norm": _normalize_sheet_date_key(_safe_row_get(values, date_idx)),
                "daypart_norm": _normalize_sheet_text(_safe_row_get(values, daypart_idx)),
            }
        )

    payload_items: list[dict[str, Any]] = []
    for idx, payload_row in enumerate(payload_rows):
        row_values = payload_row if isinstance(payload_row, list) else []
        payload_items.append(
            {
                "idx": idx,
                "date_norm": _normalize_sheet_date_key(_safe_row_get(row_values, date_idx)),
                "daypart_norm": _normalize_sheet_text(_safe_row_get(row_values, daypart_idx)),
            }
        )
    # OCR table often omits date/daypart for continuation rows. Carry-forward
    # keeps row-index rescue aligned within the same daily block.
    last_payload_date = ""
    last_payload_daypart = ""
    for item in payload_items:
        current_date = str(item.get("date_norm") or "")
        current_daypart = str(item.get("daypart_norm") or "")
        if current_date:
            last_payload_date = current_date
        elif last_payload_date:
            item["date_norm"] = last_payload_date
        if current_daypart:
            last_payload_daypart = current_daypart
        elif last_payload_daypart:
            item["daypart_norm"] = last_payload_daypart

    def _strict_canonical_daypart(value: object) -> str:
        text = _normalize_sheet_text(value)
        if not text:
            return ""
        hits: list[str] = []
        if "朝" in text:
            hits.append("朝")
        if "昼" in text:
            hits.append("昼")
        if "夕" in text or "夜" in text:
            hits.append("夕")
        if len(set(hits)) != 1:
            return ""
        return hits[0]

    def _payload_row_has_numeric_signal(payload_idx: int) -> bool:
        if payload_idx < 0 or payload_idx >= len(payload_rows):
            return False
        payload_row = payload_rows[payload_idx]
        if not isinstance(payload_row, list):
            return False
        if _payload_row_has_numeric_quantity(payload_row, quantity_columns):
            return True
        if _extract_payload_row_span_hint(payload_row, quantity_columns=quantity_columns) > 0:
            return True
        return bool(_payload_row_numeric_candidates(payload_row, skip_columns=skip_columns))

    def _payload_row_is_row_index_eligible(payload_idx: int, *, sheet_idx: int | None = None) -> bool:
        if not _payload_row_has_numeric_signal(payload_idx):
            return False
        if sheet_idx is None:
            return True
        if row_index_window > 0 and abs(sheet_idx - payload_idx) > row_index_window:
            return False
        if 0 <= sheet_idx < len(sheet_items) and 0 <= payload_idx < len(payload_items):
            sheet_date = str(sheet_items[sheet_idx].get("date_norm") or "")
            payload_date = str(payload_items[payload_idx].get("date_norm") or "")
            if sheet_date and payload_date and sheet_date != payload_date:
                return False
            sheet_daypart = _strict_canonical_daypart(sheet_items[sheet_idx].get("daypart_norm"))
            payload_daypart = _strict_canonical_daypart(payload_items[payload_idx].get("daypart_norm"))
            if sheet_daypart and payload_daypart and sheet_daypart != payload_daypart:
                return False
        return True

    eligible_payload_indexes = [
        idx for idx in range(len(payload_rows)) if _payload_row_has_numeric_signal(idx)
    ]
    if not eligible_payload_indexes:
        return {}, stage_counts, {}

    mapping: dict[int, int] = {}
    used_payload: set[int] = set()
    mapping_stage: dict[int, str] = {}
    reserved_direct_payload = {
        idx
        for idx in eligible_payload_indexes
        if 0 <= idx < len(rows) and _payload_row_is_row_index_eligible(idx, sheet_idx=idx)
    }

    def _mark(sheet_idx: int, payload_idx: int) -> None:
        mapping[sheet_idx] = payload_idx
        used_payload.add(payload_idx)
        mapping_stage[sheet_idx] = "row_index"
        stage_counts["row_index"] += 1

    for sheet_idx in range(len(rows)):
        if (
            0 <= sheet_idx < len(payload_rows)
            and sheet_idx not in used_payload
            and _payload_row_is_row_index_eligible(sheet_idx, sheet_idx=sheet_idx)
        ):
            _mark(sheet_idx, sheet_idx)
            continue
        candidates: list[tuple[int, int]] = []
        for payload_idx in eligible_payload_indexes:
            if payload_idx in used_payload:
                continue
            if payload_idx in reserved_direct_payload and payload_idx != sheet_idx:
                continue
            if not _payload_row_is_row_index_eligible(payload_idx, sheet_idx=sheet_idx):
                continue
            distance = abs(sheet_idx - payload_idx)
            candidates.append((distance, payload_idx))
        if candidates:
            _distance, selected = min(candidates, key=lambda item: item[0])
            _mark(sheet_idx, selected)

    return mapping, stage_counts, mapping_stage


def _apply_payload_quantities_numeric_only(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_index: dict[tuple[str, str], int],
    payload_rows: list[list[str]],
    payload_unstructured_qty: list[str] | None = None,
    allow_heuristics: bool = False,
    enable_daypart_consensus: bool = True,
) -> dict[str, int]:
    stage_counts = {
        "exact": 0,
        "partial": 0,
        "neighbor": 0,
        "row_index": 0,
        "loose_cell": 0,
        "gap_fill": 0,
        "unstructured": 0,
        "cluster_fill": 0,
        "span_copy": 0,
    }
    if not rows or not fields or not payload_rows:
        return stage_counts

    quantity_columns = sorted(set(quantity_index.values()))
    if not quantity_columns:
        return stage_counts
    immutable_columns = {
        idx
        for idx, field in enumerate(fields)
        if str(field).startswith("date") or field in {"daypart", "menu", "menu_name"}
    }
    trusted_quantity_columns, quantity_hits, _quantity_non_empty_hits = _select_trusted_payload_quantity_columns(
        payload_rows=payload_rows,
        quantity_columns=quantity_columns,
    )
    mapped_quantity_columns = list(trusted_quantity_columns)
    if not mapped_quantity_columns:
        mapped_quantity_columns = list(quantity_columns)
    dominant_quantity_columns: set[int] = set(mapped_quantity_columns)
    max_hit = max((int(quantity_hits.get(col, 0)) for col in mapped_quantity_columns), default=0)
    if max_hit > 0:
        dominant_quantity_columns = {
            col
            for col in mapped_quantity_columns
            if int(quantity_hits.get(col, 0)) == max_hit
        }
    sparse_threshold = 2
    if max_hit > 0:
        sparse_threshold = max(sparse_threshold, int((max_hit * 0.25) + 0.9999))

    row_mapping, mapping_counts, _mapping_stage = _build_payload_row_mapping_by_row_index_numeric_only(
        rows=rows,
        fields=fields,
        payload_rows=payload_rows,
        quantity_columns=mapped_quantity_columns,
    )
    for key, value in mapping_counts.items():
        stage_counts[key] = int(value)
    span_copy_hints: dict[tuple[int, int], int] = {}
    span_row_hints: dict[int, int] = {}

    for row_idx, payload_idx in row_mapping.items():
        if row_idx < 0 or row_idx >= len(rows):
            continue
        if payload_idx < 0 or payload_idx >= len(payload_rows):
            continue
        payload_row = payload_rows[payload_idx]
        if not isinstance(payload_row, list):
            continue
        row_span_hint = _extract_payload_row_span_hint(
            payload_row,
            quantity_columns=mapped_quantity_columns,
        )
        if row_span_hint > 0:
            span_row_hints[row_idx] = max(int(span_row_hints.get(row_idx, 0)), int(row_span_hint))
        target = rows[row_idx]
        values = target.get("values")
        if not isinstance(values, list):
            continue

        parsed_quantities: list[tuple[int, float, int]] = []
        for col_idx in mapped_quantity_columns:
            if col_idx >= len(payload_row):
                continue
            span_repeat = 0
            span_candidate = _parse_explicit_span_quantity_cell(payload_row[col_idx])
            qty = _parse_sheet_quantity_cell(payload_row[col_idx])
            if qty is None and span_candidate is not None:
                qty = span_candidate[0]
            if qty is None:
                continue
            if span_candidate is not None:
                span_repeat = int(span_candidate[1])
            parsed_quantities.append((col_idx, qty, span_repeat))

        filtered_quantities: list[tuple[int, float, int]] = []
        for col_idx, qty, span_repeat in parsed_quantities:
            others = [value for other_col, value, _ in parsed_quantities if other_col != col_idx and value > 0]
            col_hits = int(quantity_hits.get(col_idx, 0))
            is_sparse_column = max_hit > 0 and col_hits < sparse_threshold
            is_spike = bool(others) and qty >= 10 and qty > (max(others) * 2.5)
            if is_sparse_column and is_spike:
                continue
            filtered_quantities.append((col_idx, qty, span_repeat))
        if not filtered_quantities:
            filtered_quantities = parsed_quantities

        applied_qty = False
        for col_idx, qty, span_repeat in filtered_quantities:
            _set_row_quantity_value(values, col_idx, qty)
            applied_qty = True
            if span_repeat >= 2:
                key = (row_idx, col_idx)
                span_copy_hints[key] = max(int(span_copy_hints.get(key, 0)), int(span_repeat))

        if allow_heuristics and not applied_qty:
            loose_candidates = _payload_row_numeric_candidates(
                payload_row,
                skip_columns=immutable_columns | set(quantity_columns),
            )
            target_columns = (
                sorted(dominant_quantity_columns)
                if dominant_quantity_columns
                else list(mapped_quantity_columns)
            )
            if loose_candidates and target_columns:
                _set_row_quantity_value(values, target_columns[0], loose_candidates[0])
                stage_counts["loose_cell"] = int(stage_counts.get("loose_cell", 0)) + 1

    span_filled = _apply_explicit_span_quantity_copy(
        rows=rows,
        fields=fields,
        span_copy_hints=span_copy_hints,
    )
    if span_filled > 0:
        stage_counts["span_copy"] = int(stage_counts.get("span_copy", 0)) + span_filled

    span_row_filled = _apply_payload_row_span_hints(
        rows=rows,
        fields=fields,
        quantity_columns=mapped_quantity_columns,
        span_row_hints=span_row_hints,
    )
    if span_row_filled > 0:
        stage_counts["span_copy"] = int(stage_counts.get("span_copy", 0)) + span_row_filled

    if enable_daypart_consensus:
        cluster_filled = _fill_cluster_consensus_quantities(
            rows=rows,
            fields=fields,
            quantity_columns=mapped_quantity_columns,
        )
        if cluster_filled > 0:
            stage_counts["cluster_fill"] = int(stage_counts.get("cluster_fill", 0)) + cluster_filled
        daypart_consensus_columns: list[int] = []
        if dominant_quantity_columns:
            daypart_consensus_columns = [min(dominant_quantity_columns)]
        elif mapped_quantity_columns:
            daypart_consensus_columns = [mapped_quantity_columns[0]]
        daypart_consensus_filled = _fill_blank_daypart_clusters_by_consensus(
            rows=rows,
            fields=fields,
            quantity_columns=daypart_consensus_columns,
        )
        if daypart_consensus_filled > 0:
            stage_counts["cluster_fill"] = int(stage_counts.get("cluster_fill", 0)) + daypart_consensus_filled

    if allow_heuristics:
        preferred_columns = (
            sorted(dominant_quantity_columns)
            if dominant_quantity_columns
            else list(mapped_quantity_columns)
        )
        gap_filled = _fill_isolated_sheet_quantity_gaps(
            rows=rows,
            fields=fields,
            quantity_columns=mapped_quantity_columns,
            preferred_columns=preferred_columns,
        )
        if gap_filled > 0:
            stage_counts["gap_fill"] = int(stage_counts.get("gap_fill", 0)) + gap_filled

        unstructured_filled = _apply_unstructured_quantity_candidates(
            rows=rows,
            fields=fields,
            quantity_columns=mapped_quantity_columns,
            preferred_columns=preferred_columns,
            candidates=list(payload_unstructured_qty or []),
        )
        if unstructured_filled > 0:
            stage_counts["unstructured"] = int(stage_counts.get("unstructured", 0)) + unstructured_filled
    return stage_counts


def _apply_order_line_quantities_by_source_row_index(
    *,
    rows: list[dict[str, Any]],
    fields: list[str],
    quantity_index: dict[tuple[str, str], int],
    order_lines: list[Any],
) -> None:
    def _line_value(line: Any, key: str):
        if isinstance(line, dict):
            return line.get(key)
        return getattr(line, key, None)

    if not rows or not fields or not quantity_index or not order_lines:
        return
    note_field = "remarks" if "remarks" in fields else ("note" if "note" in fields else None)
    note_idx = fields.index(note_field) if note_field else None

    for line in order_lines:
        source_idx_raw = _line_value(line, "source_row_index")
        if source_idx_raw is None:
            continue
        try:
            source_idx = int(source_idx_raw)
        except Exception:
            continue
        if source_idx < 0 or source_idx >= len(rows):
            continue

        qty_corrected = _line_value(line, "quantity_corrected")
        qty_original = _line_value(line, "quantity_original")
        qty = qty_corrected if qty_corrected is not None else qty_original
        if qty is None:
            continue
        try:
            qty_float = float(qty)
        except Exception:
            continue
        diet_key = _normalize_sheet_diet(_line_value(line, "diet_type"))
        area_key = _normalize_sheet_area(_line_value(line, "area_id"))
        if not diet_key or not area_key:
            continue
        col_idx = _resolve_quantity_column_index(
            quantity_index=quantity_index,
            diet_key=diet_key,
            area_key=area_key,
        )
        if col_idx is None:
            continue

        target = rows[source_idx]
        values = target.get("values")
        if not isinstance(values, list):
            continue
        while len(values) <= col_idx:
            values.append("")
        values[col_idx] = _numeric_string_add(str(values[col_idx] or ""), qty_float)

        change_note = _line_value(line, "change_note")
        if note_idx is not None and change_note:
            while len(values) <= note_idx:
                values.append("")
            current_note = str(values[note_idx] or "")
            if change_note not in current_note:
                values[note_idx] = (
                    f"{current_note} / {change_note}".strip(" /")
                    if current_note
                    else str(change_note)
                )


def _summarize_order_line_source_row_mapping(
    *,
    base_rows: list[dict[str, Any]],
    quantity_index: dict[tuple[str, str], int],
    order_lines: list[Any],
) -> dict[str, int]:
    def _line_value(line: Any, key: str):
        if isinstance(line, dict):
            return line.get(key)
        return getattr(line, key, None)

    summary = {
        "eligible_line_count": 0,
        "matched_source_row_count": 0,
        "mismatched_source_row_count": 0,
        "missing_source_row_count": 0,
        "invalid_identity_line_count": 0,
    }
    if not base_rows or not quantity_index or not order_lines:
        return summary

    for line in order_lines:
        qty_corrected = _line_value(line, "quantity_corrected")
        qty_original = _line_value(line, "quantity_original")
        qty = qty_corrected if qty_corrected is not None else qty_original
        if qty is None:
            continue
        try:
            float(qty)
        except Exception:
            continue
        diet_key = _normalize_sheet_diet(_line_value(line, "diet_type"))
        area_key = _normalize_sheet_area(_line_value(line, "area_id"))
        if not diet_key or not area_key:
            continue
        col_idx = _resolve_quantity_column_index(
            quantity_index=quantity_index,
            diet_key=diet_key,
            area_key=area_key,
        )
        if col_idx is None:
            continue
        summary["eligible_line_count"] += 1

        line_identity = _build_canonical_menu_key(
            menu_date=_line_value(line, "date"),
            daypart=_line_value(line, "daypart"),
            menu_name=_line_value(line, "menu_name"),
        )
        if line_identity is None:
            summary["invalid_identity_line_count"] += 1
            continue

        source_idx_raw = _line_value(line, "source_row_index")
        try:
            source_idx = int(source_idx_raw) if source_idx_raw is not None else None
        except Exception:
            source_idx = None
        if source_idx is None or source_idx < 0 or source_idx >= len(base_rows):
            summary["missing_source_row_count"] += 1
            continue

        target_identity = base_rows[source_idx].get("identity")
        if isinstance(target_identity, tuple) and len(target_identity) == 3 and target_identity == line_identity:
            summary["matched_source_row_count"] += 1
        else:
            summary["mismatched_source_row_count"] += 1

    return summary


def _should_prefer_source_row_candidate(
    *,
    identity_count: int,
    source_row_count: int,
    source_row_summary: dict[str, int] | None,
) -> bool:
    if source_row_count <= identity_count:
        return False
    if not isinstance(source_row_summary, dict):
        return False
    if int(source_row_summary.get("eligible_line_count") or 0) <= 0:
        return False
    if int(source_row_summary.get("mismatched_source_row_count") or 0) > 0:
        return False
    if int(source_row_summary.get("missing_source_row_count") or 0) > 0:
        return False
    if int(source_row_summary.get("invalid_identity_line_count") or 0) > 0:
        return False
    return True


def build_recoverable_ocr_sheet_payload(
    order_id: str,
    error_code: str,
) -> tuple[dict[str, Any] | None, str | None]:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        facility_id = order.facility_code
        week_id = order.week_code
        lines_updated_at = order.lines_updated_at
    if not facility_id:
        return None, error_code

    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility_config = None
    try:
        facility_config = config_service.get_facility_config(facility_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Facility config lookup failed", facility_id=facility_id, error=str(exc))
    if not facility_config:
        facility_config = next(
            (
                fac
                for fac in master.get("facilities", [])
                if fac.get("facility_id") == facility_id
            ),
            None,
        )
    if not facility_config:
        return None, error_code
    template = facility_config.get("fax_template") or config_service._merge_template(
        base_template,
        facility_config.get("fax_template_override"),
    )
    fields, field_index = _build_sheet_fields_and_indexes(template)
    field_error = _validate_sheet_template_fields(fields)
    if field_error:
        return None, error_code
    quantity_index = _build_sheet_quantity_index(fields)
    cached_payload = _load_order_ocr_cache(order_id)
    latest_draft = get_latest_sheet_draft(order_id, backfill_from_revision=True)
    latest_revision = _select_order_sheet_revision(
        order_id=order_id,
        payload=cached_payload,
        exact_only=False,
    )
    fallback_payload: dict[str, Any] = {
        "order_id": order_id,
        "facility_id": facility_id,
        "week_id": week_id,
        "fields": fields,
        "header": _sheet_header_from_template(fields, template),
        "rows": [],
        "row_ids": [],
        "quantity_column_count": len(quantity_index),
        "source": "review_blocked",
        "legacy_available": True,
        "warnings": [str(error_code).strip()] if str(error_code).strip() else [],
        "cell_issues": [],
        "issue_summary": {"review_required_cell_count": 0, "issue_codes": []},
        "trace": {"rows": [], "mapped_mode": "unavailable"},
        "recovery_source": "none",
    }
    payload = fallback_payload
    if isinstance(latest_draft, dict):
        rebuilt = _build_sheet_payload_from_draft(
            order_id=order_id,
            draft=latest_draft,
            fallback_sheet=fallback_payload,
        )
        if isinstance(rebuilt, dict):
            payload = rebuilt
            payload["source"] = "draft_sheet_blocked"
            warnings = list(payload.get("warnings") or [])
            normalized_error = str(error_code).strip()
            if normalized_error and normalized_error not in warnings:
                warnings.append(normalized_error)
            payload["warnings"] = warnings
            payload["recovery_source"] = "draft_sheet"
    elif isinstance(latest_revision, dict):
        rebuilt = _build_sheet_payload_from_revision(
            order_id=order_id,
            revision=latest_revision,
            fallback_sheet=fallback_payload,
        )
        if isinstance(rebuilt, dict):
            payload = rebuilt
            payload["source"] = "edited_sheet_blocked"
            warnings = list(payload.get("warnings") or [])
            normalized_error = str(error_code).strip()
            if normalized_error and normalized_error not in warnings:
                warnings.append(normalized_error)
            payload["warnings"] = warnings
            payload["recovery_source"] = "saved_draft"
    elif isinstance(cached_payload, dict):
        recovered_rows = _extract_sheet_rows_from_payload(cached_payload, template)
        if recovered_rows:
            payload = dict(fallback_payload)
            payload["rows"] = recovered_rows
            payload["row_ids"] = [f"recover-{idx + 1}" for idx in range(len(recovered_rows))]
            payload["trace"] = {
                "rows": [{"source": "ocr_payload", "row_count": len(recovered_rows)}],
                "mapped_mode": "ocr_payload",
            }
            payload["recovery_source"] = "ocr_payload"
    if isinstance(cached_payload, dict):
        evidence_missing = _ocr_evidence_missing_artifacts(cached_payload)
        template_blockers = _template_resolution_blockers(cached_payload)
        warnings = list(payload.get("warnings") or [])
        if evidence_missing and "ocr_evidence_recovery_required" not in warnings:
            warnings.append("ocr_evidence_recovery_required")
            payload["evidence_missing_artifacts"] = evidence_missing
        if template_blockers and "template_resolution_blocked" not in warnings:
            warnings.append("template_resolution_blocked")
            payload["template_resolution_blockers"] = template_blockers
        payload["warnings"] = warnings
    return (
        _augment_sheet_review_payload(
            order_id=order_id,
            payload=payload,
            lines_updated_at=lines_updated_at,
            ocr_payload=cached_payload,
            ocr_metrics=cached_payload.get("metrics") if isinstance(cached_payload, dict) else None,
        ),
        None,
    )


def get_ocr_sheet(
    order_id: str,
    *,
    use_saved_draft: bool = True,
    evidence_run_override: dict[str, Any] | None = None,
):
    lines_updated_at: datetime | None = None
    order_status: str | None = None
    evidence_only_step2 = _evidence_only_step2_enabled()
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        facility_id = order.facility_code
        lines_updated_at = order.lines_updated_at
        order_status = order.status
        if not facility_id:
            return None, "facility_missing"
        week_id = order.week_code
        received_at = order.received_at or datetime.utcnow()
        facility_week_hint = (
            session.execute(
                select(Order.week_code)
                .where(Order.facility_code == facility_id, Order.week_code.is_not(None))
                .order_by(Order.received_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        global_week_hint = (
            session.execute(
                select(Order.week_code)
                .where(Order.week_code.is_not(None))
                .order_by(Order.received_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        order_lines: list[dict[str, Any]] = []

    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility_config = None
    try:
        facility_config = config_service.get_facility_config(facility_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Facility config lookup failed", facility_id=facility_id, error=str(exc))
    if not facility_config:
        facility_config = next(
            (
                fac
                for fac in master.get("facilities", [])
                if fac.get("facility_id") == facility_id
            ),
            None,
        )
    if not facility_config:
        return None, "facility_not_found"
    template = facility_config.get("fax_template") or config_service._merge_template(
        base_template,
        facility_config.get("fax_template_override"),
    )

    fields, field_index = _build_sheet_fields_and_indexes(template)
    field_error = _validate_sheet_template_fields(fields)
    if field_error:
        return None, field_error
    quantity_index = _build_sheet_quantity_index(fields)

    ocr_payload: dict[str, Any] | None = None
    override_payload = evidence_run_override.get("payload_json") if isinstance(evidence_run_override, dict) else None
    if isinstance(override_payload, dict):
        ocr_payload = evidence_manifest_service.ensure_evidence_manifest(dict(override_payload))
    else:
        payload, _ = get_ocr_output(order_id, persist_cache=False)
        if isinstance(payload, dict):
            ocr_payload = payload
    latest_draft = (
        get_latest_sheet_draft(order_id, backfill_from_revision=True)
        if use_saved_draft
        else None
    )
    latest_revision = (
        _select_order_sheet_revision(
            order_id=order_id,
            payload=ocr_payload,
            exact_only=False,
        )
        if use_saved_draft
        else None
    )
    evidence_missing = _ocr_evidence_missing_artifacts(ocr_payload)
    template_blockers = _template_resolution_blockers(ocr_payload)
    # Step2 should still prefer a semantic sheet when weekly/menu/template data are
    # sufficient to build one. Missing evidence artifacts or unresolved template
    # resolution remain warnings/blockers for apply/confirm, but they should not
    # force an immediate fallback to raw OCR rows.
    ocr_metrics = _resolve_sheet_suppression_metrics(
        order_id=order_id,
        ocr_payload=ocr_payload,
    )
    suppress_order_lines_reason = _sheet_order_lines_suppression_reason(
        order_status=order_status,
        order_lines=order_lines,
        ocr_metrics=ocr_metrics if isinstance(ocr_metrics, dict) else None,
    )

    resolved_week_id = _resolve_sheet_week_id(
        current_week_id=week_id,
        received_at=received_at,
        order_lines=order_lines,
        ocr_payload=ocr_payload,
        facility_id=facility_id,
        week_hints=[
            hint for hint in [facility_week_hint, global_week_hint] if hint
        ],
    )
    if not resolved_week_id:
        return None, "week_unresolved"
    has_weekly_menu_entries = bool(_build_position_menu_entries_safe(resolved_week_id, facility_id))

    if not isinstance(ocr_payload, dict):
        # Weekly menu master missing: build editable rows from this order's OCR table.
        if not has_weekly_menu_entries:
            payload, _ = get_ocr_output(order_id, persist_cache=False)
            if isinstance(payload, dict):
                ocr_payload = payload

    sheet_lines = [] if suppress_order_lines_reason else list(order_lines)
    sheet_lines_source = "suppressed" if suppress_order_lines_reason else "order_lines"
    if not sheet_lines and isinstance(ocr_payload, dict):
        payload_sheet_lines = _build_sheet_lines_from_ocr_payload(
            payload=ocr_payload,
            template=template,
            received_at=received_at,
            week_id=resolved_week_id,
            facility_id=facility_id,
        )
        if payload_sheet_lines:
            sheet_lines = payload_sheet_lines
            sheet_lines_source = "ocr_payload"

    entries, entry_source = _build_sheet_menu_entries(
        week_id=resolved_week_id,
        facility_id=facility_id,
        ocr_payload=ocr_payload,
        template=template,
        received_at=received_at,
    )
    if not entries:
        if evidence_only_step2 and isinstance(latest_revision, dict):
            return build_recoverable_ocr_sheet_payload(order_id, "menu_entries_missing")
        return None, "menu_entries_missing"

    payload_rows: list[list[str]] = []
    payload_unstructured_qty: list[str] = []
    payload_has_structured_table_rows = False
    if isinstance(ocr_payload, dict):
        payload_rows = _extract_sheet_rows_from_payload(ocr_payload, template)
        payload_unstructured_qty = _extract_payload_unstructured_quantity_candidates(ocr_payload)
        table_rows_payload = ocr_payload.get("table_rows")
        payload_has_structured_table_rows = isinstance(table_rows_payload, list) and bool(table_rows_payload)

    confirmed_line_dates = {
        line.get("date")
        for line in order_lines
        if isinstance(line, dict) and isinstance(line.get("date"), date)
    }
    line_dates = {
        line.get("date")
        for line in sheet_lines
        if isinstance(line, dict) and isinstance(line.get("date"), date)
    }
    payload_dates = (
        {
            item
            for item in _collect_sheet_dates_from_payload(ocr_payload, received_at)
            if isinstance(item, date)
        }
        if isinstance(ocr_payload, dict)
        else set()
    )

    rows, source = _build_rows_from_menu_entries(
        entries=entries,
        fields=fields,
        field_index=field_index,
        line_dates=confirmed_line_dates,
        source=entry_source,
        payload_dates=payload_dates,
        payload_row_count=len(payload_rows),
        scope_anchor_date=received_at.date(),
    )
    if not rows:
        if evidence_only_step2 and isinstance(latest_revision, dict):
            return build_recoverable_ocr_sheet_payload(order_id, "menu_entries_missing")
        return None, "menu_entries_missing"

    if source == "weekly_menu":
        missing_week_dates = _collect_missing_weekly_menu_dates(
            entries=entries,
            rows=rows,
            line_dates=confirmed_line_dates,
        )
        if missing_week_dates:
            logger.warning(
                "Sheet weekly menu dates incomplete",
                order_id=order_id,
                facility_id=facility_id,
                week_id=resolved_week_id,
                missing_dates=[item.isoformat() for item in missing_week_dates],
            )
            return None, "sheet_week_dates_incomplete"
    base_rows = _clone_sheet_rows(rows)
    mapped_count = 0
    mapped_mode = "identity"
    rows = _clone_sheet_rows(base_rows)
    sheet_warnings: list[str] = []
    payload_mapping_block_reason = _sheet_payload_mapping_block_reason(
        source=source,
        ocr_payload=ocr_payload,
        evidence_missing=evidence_missing,
        template_blockers=template_blockers,
    )
    payload_mapping_blocked = bool(payload_mapping_block_reason)
    if payload_mapping_blocked and sheet_lines_source == "ocr_payload":
        sheet_lines = []
        sheet_lines_source = "suppressed"

    def _append_sheet_warning(code: str) -> None:
        token = str(code or "").strip()
        if token and token not in sheet_warnings:
            sheet_warnings.append(token)

    if not has_weekly_menu_entries:
        _append_sheet_warning("sheet_weekly_menu_missing")
    if suppress_order_lines_reason:
        _append_sheet_warning(suppress_order_lines_reason)
    if payload_mapping_blocked and payload_rows:
        if payload_mapping_block_reason == "numeric_review_required":
            _append_sheet_warning("sheet_payload_mapping_blocked_numeric_review_required")
        else:
            _append_sheet_warning("sheet_payload_mapping_blocked_unresolved_template")

    llm_allows_cluster_fill = _llm_allows_order_line_cluster_consensus_fill(ocr_payload)

    # Weekly menu + template is the primary source of truth.
    # When weekly menu is available, keep non-numeric cells from weekly menu only.
    # If persisted order lines exist, those quantities are authoritative.
    # OCR payload numeric rescue is used only when persisted order lines are absent.
    if source == "weekly_menu":
        if sheet_lines_source == "order_lines" and sheet_lines:
            unmapped_quantity_lines = _collect_unmapped_quantity_lines(
                order_lines=sheet_lines,
                quantity_index=quantity_index,
            )

            rows_by_identity = _clone_sheet_rows(base_rows)
            _apply_order_line_quantities_to_sheet_rows(
                rows=rows_by_identity,
                fields=fields,
                quantity_index=quantity_index,
                order_lines=sheet_lines,
            )
            identity_count = _count_non_empty_quantity_cells(
                rows=rows_by_identity,
                quantity_index=quantity_index,
            )

            rows_by_source_index = _clone_sheet_rows(base_rows)
            _apply_order_line_quantities_by_source_row_index(
                rows=rows_by_source_index,
                fields=fields,
                quantity_index=quantity_index,
                order_lines=sheet_lines,
            )
            source_index_count = _count_non_empty_quantity_cells(
                rows=rows_by_source_index,
                quantity_index=quantity_index,
            )
            source_row_summary = _summarize_order_line_source_row_mapping(
                base_rows=base_rows,
                quantity_index=quantity_index,
                order_lines=sheet_lines,
            )
            mapped_count = identity_count
            mapped_mode = "identity"
            rows = rows_by_identity
            if _should_prefer_source_row_candidate(
                identity_count=identity_count,
                source_row_count=source_index_count,
                source_row_summary=source_row_summary,
            ):
                mapped_count = source_index_count
                mapped_mode = "source_row"
                rows = rows_by_source_index
            elif (
                source_index_count >= identity_count
                and (
                    int(source_row_summary.get("mismatched_source_row_count") or 0) > 0
                    or int(source_row_summary.get("missing_source_row_count") or 0) > 0
                    or int(source_row_summary.get("invalid_identity_line_count") or 0) > 0
                )
            ):
                logger.info(
                    "Rejected source-row sheet mapping due to row identity conflicts",
                    order_id=order_id,
                    facility_id=facility_id,
                    week_id=resolved_week_id,
                    source=source,
                    identity_count=identity_count,
                    source_row_count=source_index_count,
                    **source_row_summary,
                )
            mapped_row_count = _count_non_empty_quantity_rows(
                rows=rows,
                quantity_index=quantity_index,
            )
            mapped_column_count = _count_non_empty_quantity_columns(
                rows=rows,
                quantity_index=quantity_index,
            )

            if payload_rows and not payload_mapping_blocked:
                rows_by_payload_index = _clone_sheet_rows(base_rows)
                payload_match_stats = _apply_payload_quantities_numeric_only(
                    rows=rows_by_payload_index,
                    fields=fields,
                    quantity_index=quantity_index,
                    payload_rows=payload_rows,
                    payload_unstructured_qty=payload_unstructured_qty,
                    allow_heuristics=False,
                    enable_daypart_consensus=(
                        not payload_has_structured_table_rows and llm_allows_cluster_fill
                    ),
                )
                payload_mapped_count = _count_non_empty_quantity_cells(
                    rows=rows_by_payload_index,
                    quantity_index=quantity_index,
                )
                payload_mapped_row_count = _count_non_empty_quantity_rows(
                    rows=rows_by_payload_index,
                    quantity_index=quantity_index,
                )
                payload_mapped_column_count = _count_non_empty_quantity_columns(
                    rows=rows_by_payload_index,
                    quantity_index=quantity_index,
                )
                try:
                    min_row_gain_abs = max(
                        1,
                        int(os.getenv("OCR_SHEET_WEEKLY_MENU_PAYLOAD_OVERRIDE_MIN_ROW_GAIN_ABS", "8")),
                    )
                except Exception:
                    min_row_gain_abs = 8
                try:
                    min_row_gain_ratio = float(
                        os.getenv("OCR_SHEET_WEEKLY_MENU_PAYLOAD_OVERRIDE_MIN_ROW_GAIN_RATIO", "1.5")
                    )
                except Exception:
                    min_row_gain_ratio = 1.5
                if min_row_gain_ratio < 1.0:
                    min_row_gain_ratio = 1.0
                allow_payload_override = mapped_row_count <= 0
                if not allow_payload_override and payload_mapped_row_count > 0:
                    row_gain = payload_mapped_row_count - mapped_row_count
                    row_gain_ratio = payload_mapped_row_count / max(mapped_row_count, 1)
                    allow_payload_override = row_gain >= min_row_gain_abs and row_gain_ratio >= min_row_gain_ratio
                payload_preferred_for_unmapped_lines = bool(unmapped_quantity_lines) and (
                    payload_mapped_count >= mapped_count and payload_mapped_row_count >= mapped_row_count
                )
                payload_preferred_for_stale_family = bool(unmapped_quantity_lines) and (
                    payload_mapped_row_count >= max(
                        1,
                        int((mapped_row_count * 0.95) + 0.9999),
                    )
                    and payload_mapped_column_count > mapped_column_count
                )
                if (allow_payload_override and payload_mapped_count > mapped_count) or payload_preferred_for_unmapped_lines:
                    logger.warning(
                        "Selected OCR payload numeric-only mapping over order-line mapping",
                        order_id=order_id,
                        facility_id=facility_id,
                        week_id=resolved_week_id,
                        source=source,
                        mapped_count=mapped_count,
                        mapped_row_count=mapped_row_count,
                        payload_mapped_count=payload_mapped_count,
                        payload_mapped_row_count=payload_mapped_row_count,
                        match_exact=payload_match_stats.get("exact", 0),
                        match_partial=payload_match_stats.get("partial", 0),
                        match_neighbor=payload_match_stats.get("neighbor", 0),
                        match_row_index=payload_match_stats.get("row_index", 0),
                        match_span_copy=payload_match_stats.get("span_copy", 0),
                        match_loose_cell=payload_match_stats.get("loose_cell", 0),
                        match_gap_fill=payload_match_stats.get("gap_fill", 0),
                        match_unstructured=payload_match_stats.get("unstructured", 0),
                        unmapped_quantity_lines=len(unmapped_quantity_lines),
                        mapped_column_count=mapped_column_count,
                        payload_mapped_column_count=payload_mapped_column_count,
                    )
                    _append_sheet_warning("sheet_order_lines_unmapped_fallback_payload")
                    mapped_count = payload_mapped_count
                    mapped_mode = "payload_row"
                    rows = rows_by_payload_index
                elif payload_preferred_for_stale_family:
                    logger.warning(
                        "Selected OCR payload mapping due to broader quantity column coverage",
                        order_id=order_id,
                        facility_id=facility_id,
                        week_id=resolved_week_id,
                        source=source,
                        mapped_count=mapped_count,
                        mapped_row_count=mapped_row_count,
                        mapped_column_count=mapped_column_count,
                        payload_mapped_count=payload_mapped_count,
                        payload_mapped_row_count=payload_mapped_row_count,
                        payload_mapped_column_count=payload_mapped_column_count,
                        unmapped_quantity_lines=len(unmapped_quantity_lines),
                    )
                    _append_sheet_warning("sheet_order_lines_unmapped_fallback_payload")
                    mapped_count = payload_mapped_count
                    mapped_mode = "payload_row"
                    rows = rows_by_payload_index
        elif payload_rows and not payload_mapping_blocked:
            rows_by_payload_index = _clone_sheet_rows(base_rows)
            payload_match_stats = _apply_payload_quantities_numeric_only(
                rows=rows_by_payload_index,
                fields=fields,
                quantity_index=quantity_index,
                payload_rows=payload_rows,
                payload_unstructured_qty=payload_unstructured_qty,
                allow_heuristics=False,
                enable_daypart_consensus=(
                    not payload_has_structured_table_rows and llm_allows_cluster_fill
                ),
            )
            mapped_count = _count_non_empty_quantity_cells(
                rows=rows_by_payload_index,
                quantity_index=quantity_index,
            )
            logger.info(
                "Applied OCR payload numeric-only rescue",
                order_id=order_id,
                facility_id=facility_id,
                week_id=resolved_week_id,
                source=source,
                match_exact=payload_match_stats.get("exact", 0),
                match_partial=payload_match_stats.get("partial", 0),
                match_neighbor=payload_match_stats.get("neighbor", 0),
                match_row_index=payload_match_stats.get("row_index", 0),
                match_loose_cell=payload_match_stats.get("loose_cell", 0),
                match_gap_fill=payload_match_stats.get("gap_fill", 0),
                match_unstructured=payload_match_stats.get("unstructured", 0),
            )
            mapped_mode = "payload_row"
            rows = rows_by_payload_index
        else:
            rows_by_identity = _clone_sheet_rows(base_rows)
            _apply_order_line_quantities_to_sheet_rows(
                rows=rows_by_identity,
                fields=fields,
                quantity_index=quantity_index,
                order_lines=sheet_lines,
            )
            mapped_count = _count_non_empty_quantity_cells(
                rows=rows_by_identity,
                quantity_index=quantity_index,
            )
            mapped_mode = "identity"
            rows = rows_by_identity
    else:
        rows_by_identity = _clone_sheet_rows(base_rows)
        _apply_order_line_quantities_to_sheet_rows(
            rows=rows_by_identity,
            fields=fields,
            quantity_index=quantity_index,
            order_lines=sheet_lines,
        )
        identity_count = _count_non_empty_quantity_cells(
            rows=rows_by_identity,
            quantity_index=quantity_index,
        )

        rows_by_source_index = _clone_sheet_rows(base_rows)
        _apply_order_line_quantities_by_source_row_index(
            rows=rows_by_source_index,
            fields=fields,
            quantity_index=quantity_index,
            order_lines=sheet_lines,
        )
        source_index_count = _count_non_empty_quantity_cells(
            rows=rows_by_source_index,
            quantity_index=quantity_index,
        )
        source_row_summary = _summarize_order_line_source_row_mapping(
            base_rows=base_rows,
            quantity_index=quantity_index,
            order_lines=sheet_lines,
        )
        mapped_count = identity_count
        mapped_mode = "identity"
        rows = rows_by_identity
        if _should_prefer_source_row_candidate(
            identity_count=identity_count,
            source_row_count=source_index_count,
            source_row_summary=source_row_summary,
        ):
            mapped_count = source_index_count
            mapped_mode = "source_row"
            rows = rows_by_source_index

        if payload_rows:
            rows_by_payload_index = _clone_sheet_rows(base_rows)
            payload_match_stats = _apply_payload_cells_by_menu_priority(
                rows=rows_by_payload_index,
                fields=fields,
                quantity_index=quantity_index,
                payload_rows=payload_rows,
                payload_unstructured_qty=payload_unstructured_qty,
                allow_heuristics=False,
            )
            payload_index_count = _count_non_empty_quantity_cells(
                rows=rows_by_payload_index,
                quantity_index=quantity_index,
            )
            mapped_row_count = _count_non_empty_quantity_rows(
                rows=rows,
                quantity_index=quantity_index,
            )
            mapped_column_count = _count_non_empty_quantity_columns(
                rows=rows,
                quantity_index=quantity_index,
            )
            mapped_priority = 1 if mapped_mode == "source_row" else 0
            mapped_penalty_cells = 0
            if mapped_mode == "source_row":
                mapped_penalty_cells = _count_source_row_alignment_penalty_cells(
                    base_rows=base_rows,
                    rows_by_source_index=rows,
                    fields=fields,
                    quantity_index=quantity_index,
                    order_lines=sheet_lines,
                )
            payload_row_count = _count_non_empty_quantity_rows(
                rows=rows_by_payload_index,
                quantity_index=quantity_index,
            )
            payload_column_count = _count_non_empty_quantity_columns(
                rows=rows_by_payload_index,
                quantity_index=quantity_index,
            )
            mapped_sort_key = _sheet_candidate_sort_key(
                mapped_count=mapped_count,
                mapped_row_count=mapped_row_count,
                mapped_column_count=mapped_column_count,
                priority=mapped_priority,
                mismatch_penalty_cells=mapped_penalty_cells,
            )
            payload_sort_key = _sheet_candidate_sort_key(
                mapped_count=payload_index_count,
                mapped_row_count=payload_row_count,
                mapped_column_count=payload_column_count,
                priority=2,
                payload_match_stats=payload_match_stats,
            )
            if payload_sort_key > mapped_sort_key:
                mapped_count = payload_index_count
                mapped_mode = "payload_row"
                rows = rows_by_payload_index

    if mapped_count == 0 and (sheet_lines or payload_rows):
        logger.warning(
            "Sheet quantity mapping failed",
            order_id=order_id,
            facility_id=facility_id,
            week_id=resolved_week_id,
            source=source,
            mapped_mode=mapped_mode,
            has_payload_rows=bool(payload_rows),
            has_sheet_lines=bool(sheet_lines),
        )
        _append_sheet_warning("sheet_quantity_column_unmapped")

    if (
        source == "weekly_menu"
        and mapped_mode in {"identity", "source_row"}
        and llm_allows_cluster_fill
    ):
        order_line_cluster_filled = _apply_weekly_menu_order_line_cluster_consensus_fill(
            rows=rows,
            fields=fields,
            quantity_index=quantity_index,
        )
        if order_line_cluster_filled > 0:
            mapped_count = _count_non_empty_quantity_cells(
                rows=rows,
                quantity_index=quantity_index,
            )
            logger.info(
                "Applied weekly-menu order-line cluster consensus fill",
                order_id=order_id,
                facility_id=facility_id,
                week_id=resolved_week_id,
                source=source,
                mapped_mode=mapped_mode,
                filled_cells=order_line_cluster_filled,
                mapped_count=mapped_count,
            )

    # If we are not using payload-row mapping, validate order-line column compatibility.
    if sheet_lines_source == "order_lines" and sheet_lines and mapped_mode != "payload_row":
        unmapped_quantity_lines = _collect_unmapped_quantity_lines(
            order_lines=sheet_lines,
            quantity_index=quantity_index,
        )
        if unmapped_quantity_lines:
            logger.warning(
                "Sheet quantity column unmapped",
                order_id=order_id,
                facility_id=facility_id,
                week_id=resolved_week_id,
                issue_count=len(unmapped_quantity_lines),
                sample=unmapped_quantity_lines[:5],
            )
            _append_sheet_warning("sheet_quantity_column_unmapped")

    if source == "weekly_menu" and (
        mapped_mode == "payload_row" or sheet_lines_source == "ocr_payload"
    ):
        source = "weekly_menu+ocr_payload"
    if source == "ocr_table" and mapped_mode == "payload_row":
        source = "ocr_table+ocr_payload"
    if (
        source == "weekly_menu"
        and suppress_order_lines_reason
        and not sheet_lines
        and bool(payload_rows)
    ):
        source = "weekly_menu+ocr_payload"

    cell_issues = _map_payload_cell_issues_to_sheet_rows(
        payload=ocr_payload,
        template=template,
        rows=rows,
        fields=fields,
    )
    if cell_issues:
        _append_sheet_warning("sheet_ocr_review_required")

    trace_rows = _build_sheet_trace_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        source=source,
        mapped_mode=mapped_mode,
        has_order_lines=(sheet_lines_source == "order_lines"),
    )
    header = _sheet_header_from_template(fields, template)
    payload = {
        "order_id": order_id,
        "facility_id": facility_id,
        "week_id": resolved_week_id,
        "fields": fields,
        "header": header,
        "rows": [row.get("values", []) for row in rows],
        "row_ids": [str(row.get("row_id") or "") for row in rows],
        "quantity_column_count": len(quantity_index),
        "source": source,
        "legacy_available": True,
        "warnings": list(sheet_warnings),
        "cell_issues": cell_issues,
        "issue_summary": {
            "review_required_cell_count": len(cell_issues),
            "issue_codes": sorted(
                {
                    str(issue.get("issue_code") or "").strip()
                    for issue in cell_issues
                    if str(issue.get("issue_code") or "").strip()
                }
            ),
        },
        "trace": {
            "rows": trace_rows,
            "mapped_mode": mapped_mode,
        },
    }
    if evidence_missing and "ocr_evidence_recovery_required" not in payload["warnings"]:
        payload["warnings"].append("ocr_evidence_recovery_required")
        payload["evidence_missing_artifacts"] = evidence_missing
    if template_blockers and "template_resolution_blocked" not in payload["warnings"]:
        payload["warnings"].append("template_resolution_blocked")
        payload["template_resolution_blockers"] = template_blockers
    if evidence_only_step2 and (isinstance(latest_draft, dict) or isinstance(latest_revision, dict)):
        rebuilt = (
            _build_sheet_payload_from_draft(
                order_id=order_id,
                draft=latest_draft,
                fallback_sheet=payload,
            )
            if isinstance(latest_draft, dict)
            else _build_sheet_payload_from_revision(
                order_id=order_id,
                revision=latest_revision,
                fallback_sheet=payload,
            )
        )
        if isinstance(rebuilt, dict):
            merged_warnings = [
                str(item).strip()
                for item in list(payload.get("warnings") or []) + list(rebuilt.get("warnings") or [])
                if str(item).strip()
            ]
            deduped_warnings: list[str] = []
            for warning in merged_warnings:
                if warning not in deduped_warnings:
                    deduped_warnings.append(warning)
            rebuilt["warnings"] = deduped_warnings
            if evidence_missing:
                rebuilt["evidence_missing_artifacts"] = evidence_missing
            if template_blockers:
                rebuilt["template_resolution_blockers"] = template_blockers
            payload = rebuilt
    return (
        _augment_sheet_review_payload(
            order_id=order_id,
            payload=payload,
            lines_updated_at=lines_updated_at,
            ocr_payload=ocr_payload,
            ocr_metrics=ocr_payload.get("metrics") if isinstance(ocr_payload, dict) else None,
        ),
        None,
    )


def export_ocr_sheet_label(
    order_id: str,
    *,
    output_path: str | Path | None = None,
    output_dir: str | Path | None = None,
):
    sheet, error = get_ocr_sheet(order_id)
    if error:
        return None, error
    if not isinstance(sheet, dict):
        return None, "sheet_missing"
    payload = _load_order_ocr_cache(order_id)
    exact_revision = _select_order_sheet_revision(
        order_id=order_id,
        payload=payload,
        exact_only=True,
    )
    if isinstance(exact_revision, dict):
        sheet_from_revision = _build_sheet_payload_from_revision(
            order_id=order_id,
            revision=exact_revision,
            fallback_sheet=sheet,
        )
        if isinstance(sheet_from_revision, dict):
            sheet = sheet_from_revision

    if output_path is not None:
        resolved_path = Path(output_path)
    else:
        if output_dir is not None:
            base_dir = Path(output_dir)
        else:
            base_dir = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ocr_sheet_corpus" / "manual_labels"
        resolved_path = base_dir / f"{order_id}.expected_sheet.json"
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(sheet, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "order_id": order_id,
        "output_path": str(resolved_path),
        "sheet": sheet,
    }, None


def get_ocr_edit_history(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
    payload = _load_order_ocr_cache(order_id)
    revisions, raw_output = _load_order_sheet_revisions(order_id=order_id, payload=payload, limit=20)
    if not revisions:
        latest_evidence = get_latest_ocr_evidence_run(order_id, backfill_from_cache=True)
        if isinstance(latest_evidence, dict):
            evidence_payload = latest_evidence.get("payload_json")
            synthetic_revision = _build_ocr_history_fallback_from_evidence_run(latest_evidence)
            if isinstance(synthetic_revision, dict):
                revisions = [synthetic_revision]
                if not isinstance(raw_output, dict) and isinstance(evidence_payload, dict):
                    raw_output = evidence_payload
    latest = revisions[-1] if revisions else None
    return {
        "order_id": order_id,
        "latest": latest,
        "revisions": revisions,
        "raw_output": raw_output,
    }, None


def get_order_history(order_id: str, limit: int = 100):
    normalized_limit = max(1, min(int(limit), 500))
    order_payload: dict[str, Any] | None = None
    audit_rows: list[dict[str, Any]] = []
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        order_payload = {
            "facility_code": order.facility_code,
            "week_code": order.week_code,
            "received_at": order.received_at.isoformat() if order.received_at else None,
            "lines_updated_at": order.lines_updated_at.isoformat() if order.lines_updated_at else None,
        }
        targets = {order_id, f"OCR-{order_id}"}
        if order.message_id:
            targets.add(f"OCR-{order.message_id}")
        rows = (
            session.execute(
                select(AuditLog)
                .where(AuditLog.target.in_(sorted(targets)))
                .order_by(AuditLog.created_at.desc())
                .limit(normalized_limit)
            )
            .scalars()
            .all()
        )
        audit_rows = [
            {
                "id": row.id,
                "actor": row.actor,
                "action": row.action,
                "target": row.target,
                "facility": row.fac,
                "week": row.wek,
                "metadata": row.metadata_json if isinstance(row.metadata_json, dict) else row.metadata_json,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    items: list[dict[str, Any]] = []
    if order_payload and order_payload.get("received_at"):
        items.append(
            {
                "id": f"synthetic-created-{order_id}",
                "actor": "system",
                "action": "order_created",
                "target": order_id,
                "facility": order_payload.get("facility_code"),
                "week": order_payload.get("week_code"),
                "metadata": {"source": "synthetic"},
                "created_at": order_payload.get("received_at"),
            }
        )
    if order_payload and order_payload.get("lines_updated_at"):
        items.append(
            {
                "id": f"synthetic-lines-{order_id}",
                "actor": "system",
                "action": "order_lines_update",
                "target": order_id,
                "facility": order_payload.get("facility_code"),
                "week": order_payload.get("week_code"),
                "metadata": {"source": "synthetic"},
                "created_at": order_payload.get("lines_updated_at"),
            }
        )
    payload = _load_order_ocr_cache(order_id)
    revisions, _ = _load_order_sheet_revisions(order_id=order_id, payload=payload, limit=min(normalized_limit, 100))
    for revision in revisions:
        if not isinstance(revision, dict):
            continue
        created_at = revision.get("edited_at")
        if not isinstance(created_at, str) or not created_at:
            continue
        items.append(
            {
                "id": revision.get("revision_id") or f"synthetic-ocr-{order_id}",
                "actor": "system",
                "action": "ocr_table_apply",
                "target": order_id,
                "facility": order_payload.get("facility_code") if order_payload else None,
                "week": order_payload.get("week_code") if order_payload else None,
                "metadata": {
                    "source": "synthetic",
                    "ui_mode": revision.get("ui_mode"),
                    "row_count": revision.get("row_count"),
                    "changed": revision.get("changed"),
                },
                "created_at": created_at,
            }
        )

    for row in audit_rows:
        items.append(row)
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    items = items[:normalized_limit]
    return {
        "order_id": order_id,
        "items": items,
    }, None


def detect_order_grid(
    order_id: str,
    table_box: Optional[list[float]] = None,
    grid_params: Optional[dict] = None,
):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found", None
        document_uri = order.document_uri
        facility_id = order.facility_code
        message_id = order.message_id
    if not document_uri:
        return None, "document_missing", None
    template = None
    if isinstance(facility_id, str) and facility_id:
        try:
            fac_config = config_service.get_facility_config(facility_id)
        except Exception:  # noqa: BLE001
            fac_config = None
        if fac_config:
            template = fac_config.get("fax_template") or {}
    if not template:
        parsed = _load_order_ocr_cache(order_id)
        if not parsed and message_id:
            parsed = _load_order_ocr_cache(message_id)
        template_id = parsed.get("template_id") if isinstance(parsed, dict) else None
        if isinstance(template_id, str) and template_id:
            registry = config_service.load_fax_template_registry()
            template = registry.get(template_id) or {}
    if not template:
        master = config_service.load_facility_master()
        template = master.get("fax_template_base") or {}
    if not isinstance(template, dict) or not template:
        return None, "template_not_found", None
    override_box = None
    if isinstance(table_box, list) and len(table_box) >= 4:
        try:
            override_box = [float(value) for value in table_box[:4]]
        except (TypeError, ValueError):
            override_box = None
    template_to_use = dict(template)
    if override_box:
        template_to_use["grid_table_box"] = override_box
        template_to_use["table_box"] = override_box
    if template_to_use.get("grid_auto_table_box"):
        if not template_to_use.get("grid_column_band"):
            template_to_use["grid_column_band"] = [0.02, 0.16]
        if not template_to_use.get("grid_row_band"):
            template_to_use["grid_row_band"] = [0.0, 0.35]
    grid_source = None
    if isinstance(grid_params, dict) and grid_params:
        for key, value in grid_params.items():
            if not key.startswith("grid_"):
                continue
            if value is None:
                continue
            if key == "grid_auto_table_box":
                if isinstance(value, bool):
                    template_to_use[key] = value
                elif isinstance(value, str):
                    template_to_use[key] = value.strip().lower() in {"1", "true", "yes", "on"}
                continue
            if key == "grid_auto_use_raw_edges":
                if isinstance(value, bool):
                    template_to_use[key] = value
                elif isinstance(value, str):
                    template_to_use[key] = value.strip().lower() in {"1", "true", "yes", "on"}
                continue
            if key == "grid_source":
                if isinstance(value, str):
                    grid_source = value.strip().lower()
                continue
            if key in {
                "grid_dpi",
                "grid_line_scale",
                "grid_line_scale_horizontal",
                "grid_line_scale_vertical",
                "grid_line_merge_gap",
                "grid_expected_columns",
            }:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed < 0:
                    continue
                template_to_use[key] = parsed
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            template_to_use[key] = parsed
    try:
        import cv2  # noqa: F401
    except Exception:  # noqa: BLE001
        return None, "grid_not_found", {"reason": "grid_detector_unavailable"}
    try:
        pdf_bytes = load_bytes_from_uri(document_uri)
    except Exception:  # noqa: BLE001
        return None, "document_load_failed", None
    overlay_uri = None
    layout_overlay_uri = None
    parsed = _load_order_ocr_cache(order_id)
    if not parsed and message_id:
        parsed = _load_order_ocr_cache(message_id)
    if isinstance(parsed, dict):
        pages = parsed.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    uri = page.get("ocr_overlay_uri")
                    if isinstance(uri, str) and uri:
                        overlay_uri = uri
                        break
            for page in pages:
                if isinstance(page, dict):
                    uri = page.get("layout_overlay_uri")
                    if isinstance(uri, str) and uri:
                        layout_overlay_uri = uri
                        break
    grid = None
    source = None
    if grid_source == "layout_overlay" and layout_overlay_uri:
        try:
            overlay_bytes = load_bytes_from_uri(layout_overlay_uri)
            grid = detect_table_grid_image(overlay_bytes, template_to_use)
            if grid:
                source = "layout_overlay"
        except Exception:  # noqa: BLE001
            grid = None
            source = None
    if grid_source == "ocr_overlay" and overlay_uri and not grid:
        try:
            overlay_bytes = load_bytes_from_uri(overlay_uri)
            grid = detect_table_grid_image(overlay_bytes, template_to_use)
            if grid:
                source = "ocr_overlay"
        except Exception:  # noqa: BLE001
            grid = None
            source = None
    if grid_source == "pdf" and not grid:
        grid = detect_table_grid(pdf_bytes, template_to_use)
        if grid:
            source = "pdf"
    if grid_source is None and not grid:
        if layout_overlay_uri:
            try:
                overlay_bytes = load_bytes_from_uri(layout_overlay_uri)
                grid = detect_table_grid_image(overlay_bytes, template_to_use)
                if grid:
                    source = "layout_overlay"
            except Exception:  # noqa: BLE001
                grid = None
                source = None
        if not grid and overlay_uri:
            try:
                overlay_bytes = load_bytes_from_uri(overlay_uri)
                grid = detect_table_grid_image(overlay_bytes, template_to_use)
                if grid:
                    source = "ocr_overlay"
            except Exception:  # noqa: BLE001
                grid = None
                source = None
        if not grid:
            grid = detect_table_grid(pdf_bytes, template_to_use)
            if grid:
                source = "pdf"
    if not grid:
        base_scale = int(template_to_use.get("grid_line_scale", 30) or 30)
        base_ratio = float(template_to_use.get("grid_line_min_ratio", 0.6) or 0.6)
        candidate_scales = [base_scale, 40, 50]
        candidate_ratios = [base_ratio, 0.45, 0.35, 0.25, 0.2]
        for ratio in candidate_ratios:
            for scale in candidate_scales:
                if ratio == base_ratio and scale == base_scale:
                    continue
                retry_template = dict(template_to_use)
                retry_template["grid_line_min_ratio"] = ratio
                retry_template["grid_line_scale"] = scale
                grid = detect_table_grid(pdf_bytes, retry_template)
                if grid:
                    break
            if grid:
                break
    if not grid:
        table_box = template_to_use.get("grid_table_box") or template_to_use.get("table_box") or [0, 0, 1, 1]
        try:
            table_box = [float(value) for value in table_box[:4]]
        except (TypeError, ValueError):
            table_box = [0, 0, 1, 1]
        expected = int(template_to_use.get("grid_expected_columns", 0) or 0)
        if expected <= 0:
            grid_columns = template_to_use.get("grid_columns") or []
            expected = len(grid_columns)
        if expected >= 2:
            left, top, right, bottom = table_box
            span = right - left
            edges = [left + span * idx / expected for idx in range(expected + 1)]
            return (
                {
                    "table_box": table_box,
                    "grid_column_edges": edges,
                    "grid_row_edges": None,
                    "confidence": 0.1,
                    "fallback": True,
                    "source": source,
                    "table_units": "normalized",
                },
                None,
                None,
            )
        reason = "grid_not_found"
        if isinstance(table_box, list) and any(value > 1.5 for value in table_box):
            reason = "table_box_units_mismatch"
        elif expected <= 1:
            reason = "expected_columns_missing"
        return None, "grid_not_found", {"reason": reason, "expected_columns": expected, "table_box": table_box}
    return (
        {
            "table_box": grid.table_box,
            "grid_column_edges": grid.column_edges,
            "grid_row_edges": grid.row_edges,
            "confidence": grid.confidence,
            "fallback": False,
            "source": source,
            "table_units": "normalized",
        },
        None,
        None,
    )


def _normalize_reparse_provider(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"pipeline", "tesseract", "openai", "gemini"}:
        return normalized
    return None


def _build_reparse_quality_metadata(
    *,
    requested_provider: str | None,
    effective_provider: str | None,
    llm_assist: bool,
    auto_fallback_applied: bool,
    feedback_retry_depth: int,
) -> dict[str, Any]:
    normalized_requested = _normalize_reparse_provider(requested_provider)
    normalized_effective = _normalize_reparse_provider(effective_provider)
    is_llm_reparse = bool(
        llm_assist
        or normalized_requested in {"openai", "gemini"}
        or normalized_effective in {"openai", "gemini"}
    )
    if auto_fallback_applied:
        reparse_origin = "auto_fallback"
    elif llm_assist:
        reparse_origin = "llm_assist"
    elif normalized_requested in {"openai", "gemini"}:
        reparse_origin = "provider_override"
    else:
        reparse_origin = "standard"
    return {
        "quality_track": "llm_reparse" if is_llm_reparse else "non_llm_reparse",
        "reparse_origin": reparse_origin,
        "feedback_retry_depth": max(0, int(feedback_retry_depth)),
    }


def _resolve_explicit_reparse_inference_provider(
    *,
    requested_provider: str | None,
    llm_assist: bool,
    template: dict[str, Any],
) -> str | None:
    normalized_requested = _normalize_reparse_provider(requested_provider)
    if normalized_requested in {"openai", "gemini"}:
        return normalized_requested
    if not llm_assist:
        return normalized_requested

    configured = str(
        template.get("main_ocr_provider")
        or os.getenv("OCR_MAIN_PROVIDER")
        or ""
    ).strip().lower()
    if configured in {"openai", "gemini"}:
        return configured

    return _resolve_auto_llm_fallback_provider(template=template) or normalized_requested


def _resolve_auto_llm_fallback_provider(*, template: dict[str, Any]) -> str | None:
    configured = str(
        template.get("auto_llm_fallback_provider")
        or os.getenv("OCR_REPARSE_AUTO_LLM_FALLBACK_PROVIDER", "")
    ).strip().lower()
    if configured in {"disabled", "none", "off", "false", "0"}:
        return None

    if configured in {"gemini", "openai"}:
        preferred = configured
    else:
        if _has_gemini_api_key():
            preferred = "gemini"
        elif _has_openai_api_key():
            preferred = "openai"
        else:
            return None

    if preferred == "gemini" and _has_gemini_api_key():
        return "gemini"
    if preferred == "openai" and _has_openai_api_key():
        return "openai"
    if preferred == "gemini" and _has_openai_api_key():
        return "openai"
    if preferred == "openai" and _has_gemini_api_key():
        return "gemini"
    return None


def _is_llm_finish_reason_truncated(value: object) -> bool:
    if not isinstance(value, str):
        return False
    token = value.strip().upper().replace("-", "_").replace(" ", "_")
    if token in {"MAX_TOKENS", "MAX_OUTPUT_TOKENS", "MAX_OUTPUT_TOKEN", "LENGTH"}:
        return True
    return token.startswith("MAX_")


def _extract_llm_finish_reason(extracted: object | None) -> str | None:
    if extracted is None:
        return None
    provider_debug = getattr(extracted, "provider_debug", None)
    if not isinstance(provider_debug, dict):
        return None
    finish_reason = provider_debug.get("finish_reason")
    if not isinstance(finish_reason, str):
        return None
    normalized = finish_reason.strip()
    return normalized or None


def _is_truncated_llm_output(extracted: object | None) -> bool:
    if extracted is None:
        return False
    provider_debug = getattr(extracted, "provider_debug", None)
    if not isinstance(provider_debug, dict):
        return False
    if bool(provider_debug.get("recovered_truncated_json")):
        return True
    return _is_llm_finish_reason_truncated(provider_debug.get("finish_reason"))


def _truncate_assist_text(value: str, max_chars: int = 6000) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n...(truncated)"


def _looks_like_generated_reparse_prompt(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    markers = (
        "Second-pass repair mode:",
        "Second-pass OCR repair mode:",
        "Current sheet/baseline rows shown to the user:",
        "Structural block anchor summary:",
        "Evaluator feedback from previous OCR draft:",
        "Automatic fallback context:",
        "Date block layout summary:",
        "Suspicious blank-edge placement hints from the current OCR draft:",
    )
    return any(marker in text for marker in markers)


def _compact_prompt_tables(pipeline_output: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(pipeline_output, dict):
        return []
    collected: list[dict[str, Any]] = []
    seen_table_ids: set[str] = set()

    def _push(table_payload: object) -> None:
        if not isinstance(table_payload, dict):
            return
        table_id = str(table_payload.get("table_id") or "").strip()
        if table_id and table_id in seen_table_ids:
            return
        if table_id:
            seen_table_ids.add(table_id)
        rows = table_payload.get("rows")
        cells = table_payload.get("cells")
        entry: dict[str, Any] = {
            "table_id": table_id,
            "page_index": table_payload.get("page_index"),
            "row_count": table_payload.get("row_count"),
            "col_count": table_payload.get("col_count"),
        }
        if isinstance(rows, list) and rows:
            entry["rows_preview"] = rows[:12]
        if isinstance(cells, list) and cells:
            compact_cells: list[dict[str, Any]] = []
            for cell in cells[:24]:
                if not isinstance(cell, dict):
                    continue
                compact_cells.append(
                    {
                        "row_index": cell.get("row_index"),
                        "col_index": cell.get("col_index"),
                        "row_span": cell.get("row_span"),
                        "col_span": cell.get("col_span"),
                        "text": cell.get("text"),
                        "bbox": cell.get("bbox"),
                    }
                )
            if compact_cells:
                entry["cells_preview"] = compact_cells
        if entry.get("rows_preview") or entry.get("cells_preview"):
            collected.append(entry)

    top_tables = pipeline_output.get("tables")
    if isinstance(top_tables, list):
        for table_payload in top_tables[:6]:
            _push(table_payload)
    pages = pipeline_output.get("pages")
    if isinstance(pages, list):
        for page_payload in pages[:4]:
            if not isinstance(page_payload, dict):
                continue
            page_tables = page_payload.get("tables")
            if not isinstance(page_tables, list):
                continue
            for table_payload in page_tables[:4]:
                _push(table_payload)
    return collected[:6]


def _compact_prompt_quantity_subgrid_passes(
    pipeline_output: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(pipeline_output, dict):
        return []
    raw_passes = pipeline_output.get("quantity_subgrid_passes")
    if not isinstance(raw_passes, list):
        return []
    collected: list[dict[str, Any]] = []
    for item in raw_passes[:4]:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {}
        for key in (
            "page_index",
            "table_index",
            "body_start_row",
            "menu_col_index",
            "quantity_start_col_index",
            "row_count",
            "quantity_col_count",
            "crop_box_norm",
        ):
            value = item.get(key)
            if value is not None:
                entry[key] = value
        table_raw = item.get("table_raw")
        if isinstance(table_raw, str) and table_raw.strip():
            entry["table_raw"] = _truncate_assist_text(table_raw.strip(), max_chars=4000)
        tables = item.get("tables")
        if isinstance(tables, list) and tables:
            compact_tables: list[dict[str, Any]] = []
            for table in tables[:2]:
                if not isinstance(table, dict):
                    continue
                compact_table: dict[str, Any] = {
                    "table_id": table.get("table_id"),
                    "page_index": table.get("page_index"),
                    "row_count": table.get("row_count"),
                    "col_count": table.get("col_count"),
                }
                rows = table.get("rows")
                if isinstance(rows, list) and rows:
                    compact_table["rows_preview"] = [list(row) for row in rows[:20] if isinstance(row, list)]
                if compact_table.get("rows_preview"):
                    compact_tables.append(compact_table)
            if compact_tables:
                entry["tables"] = compact_tables
        normalized_rows = item.get("normalized_rows")
        if isinstance(normalized_rows, list) and normalized_rows:
            entry["normalized_rows_preview"] = [
                list(row) for row in normalized_rows[:20] if isinstance(row, list)
            ]
        normalization_patches = item.get("normalization_patches")
        if isinstance(normalization_patches, list) and normalization_patches:
            entry["normalization_patches"] = [
                {
                    "row_index": patch.get("row_index"),
                    "col_index": patch.get("col_index"),
                    "original_text": patch.get("original_text"),
                    "normalized_text": patch.get("normalized_text"),
                    "prev_neighbor": patch.get("prev_neighbor"),
                    "next_neighbor": patch.get("next_neighbor"),
                }
                for patch in normalization_patches[:40]
                if isinstance(patch, dict)
            ]
        if entry:
            collected.append(entry)
    return collected


def _compact_prompt_cell_issues(
    pipeline_output: dict[str, Any] | None,
    template: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(pipeline_output, dict):
        return []
    raw_issues = _collect_raw_payload_cell_issues(pipeline_output, template)
    compact: list[dict[str, Any]] = []
    for issue in raw_issues[:60]:
        compact.append(
            {
                "table_id": issue.get("table_id"),
                "source_row_index": issue.get("source_row_index", issue.get("row_index")),
                "column_index": issue.get("column_index"),
                "field": issue.get("field"),
                "issue_code": issue.get("issue_code"),
                "severity": issue.get("severity"),
                "bbox": issue.get("bbox"),
                "text": issue.get("text"),
                "value": issue.get("value"),
                "row_span": issue.get("row_span"),
                "col_span": issue.get("col_span"),
                "max_allowed": issue.get("max_allowed"),
                "source": issue.get("source"),
            }
        )
    return compact


def _build_llm_assist_baseline_rows(
    baseline: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(baseline, dict):
        return []
    fields = [str(field).strip() for field in (baseline.get("fields") or []) if str(field).strip()]
    rows = [list(row) for row in (baseline.get("rows") or []) if isinstance(row, list)]
    row_ids = [str(item).strip() for item in (baseline.get("row_ids") or []) if str(item).strip()]
    if not fields or not rows:
        return []
    return _build_llm_review_prompt_rows(
        fields=fields,
        rows=rows,
        row_ids=row_ids,
    )


def _resolve_reparse_baseline_rows_for_structure(
    baseline: dict[str, Any] | None,
) -> tuple[list[str], list[list[str]], list[str], str]:
    if not isinstance(baseline, dict):
        return [], [], [], ""
    structure_fields = baseline.get("structure_fields")
    fields = [
        str(field or "").strip()
        for field in ((structure_fields if isinstance(structure_fields, list) and structure_fields else baseline.get("fields")) or [])
        if str(field or "").strip()
    ]
    if not fields:
        return [], [], [], ""
    structural_rows = [
        list(row)
        for row in ((baseline.get("structural_rows") or baseline.get("structure_rows")) or [])
        if isinstance(row, list)
    ]
    structural_row_ids = [
        str(item).strip()
        for item in ((baseline.get("structural_row_ids") or baseline.get("structure_row_ids")) or [])
        if str(item).strip()
    ]
    structural_source = str(
        baseline.get("structural_baseline_source") or baseline.get("structure_source") or ""
    ).strip()
    if structural_rows:
        if len(structural_row_ids) < len(structural_rows):
            structural_row_ids.extend(
                [f"structural-row-{idx + 1}" for idx in range(len(structural_row_ids), len(structural_rows))]
            )
        return fields, structural_rows, structural_row_ids[: len(structural_rows)], structural_source or "structure"

    rows = [list(row) for row in (baseline.get("rows") or []) if isinstance(row, list)]
    row_ids = [str(item).strip() for item in (baseline.get("row_ids") or []) if str(item).strip()]
    if len(row_ids) < len(rows):
        row_ids.extend([f"sheet-row-{idx + 1}" for idx in range(len(row_ids), len(rows))])
    return fields, rows, row_ids[: len(rows)], str(baseline.get("baseline_source") or "").strip() or "sheet"


def _build_llm_assist_structural_rows(
    baseline: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    fields, rows, row_ids, _source = _resolve_reparse_baseline_rows_for_structure(baseline)
    if not fields or not rows:
        return []
    return _build_llm_review_prompt_rows(
        fields=fields,
        rows=rows,
        row_ids=row_ids,
    )


def _resolve_structural_row_field_indexes(
    fields: list[str] | None,
) -> tuple[int | None, int | None, int | None, list[int]]:
    normalized_fields = [str(field or "").strip() for field in (fields or [])]
    if not normalized_fields:
        return None, None, None, []
    date_idx = next(
        (
            idx
            for idx, field in enumerate(normalized_fields)
            if _normalize_sheet_text(field).lower().startswith("date")
        ),
        None,
    )
    daypart_idx = next(
        (
            idx
            for idx, field in enumerate(normalized_fields)
            if _normalize_sheet_text(field).lower() in {"daypart", "meal", "time"}
        ),
        None,
    )
    menu_idx = next(
        (
            idx
            for idx, field in enumerate(normalized_fields)
            if _normalize_sheet_text(field).lower() in {"menu", "menuname"}
        ),
        None,
    )
    quantity_indexes = [
        idx
        for idx, field in enumerate(normalized_fields)
        if str(field or "").strip().startswith("qty.")
    ]
    return date_idx, daypart_idx, menu_idx, quantity_indexes


def _build_structural_row_key(
    *,
    row: list[str],
    fields: list[str] | None,
) -> tuple[str, str, str] | None:
    date_idx, daypart_idx, menu_idx, _ = _resolve_structural_row_field_indexes(fields)
    if menu_idx is None or menu_idx >= len(row):
        return None
    menu_key = _normalize_sheet_text(row[menu_idx])
    if not menu_key:
        return None
    raw_date = row[date_idx] if date_idx is not None and date_idx < len(row) else ""
    raw_daypart = row[daypart_idx] if daypart_idx is not None and daypart_idx < len(row) else ""
    return (
        _normalize_sheet_date_key(raw_date),
        _normalize_daypart_key(raw_daypart),
        menu_key,
    )


def _build_reparse_block_anchor_hints(
    *,
    structural_fields: list[str] | None,
    structural_rows: list[list[str]],
    first_pass_fields: list[str] | None = None,
    first_pass_rows: list[list[str]] | None = None,
) -> dict[str, Any]:
    normalized_rows = [list(row) for row in structural_rows if isinstance(row, list)]
    if not normalized_rows:
        return {}

    date_idx, daypart_idx, _menu_idx, quantity_indexes = _resolve_structural_row_field_indexes(structural_fields)
    structural_blank_hint_enabled = False
    if quantity_indexes:
        for row in normalized_rows:
            has_structural_quantity = False
            for col_idx in quantity_indexes:
                if col_idx < 0 or col_idx >= len(row):
                    continue
                if _parse_strict_numeric_cell(row[col_idx]) is not None:
                    has_structural_quantity = True
                    break
            if has_structural_quantity:
                structural_blank_hint_enabled = True
                break
    blocks: list[dict[str, Any]] = []
    current_block: dict[str, Any] | None = None
    for row_idx, row in enumerate(normalized_rows):
        raw_date = row[date_idx] if date_idx is not None and date_idx < len(row) else ""
        raw_daypart = row[daypart_idx] if daypart_idx is not None and daypart_idx < len(row) else ""
        block_date = _normalize_sheet_date_key(raw_date) or str(raw_date or "").strip()
        block_daypart = _normalize_daypart_key(raw_daypart) or str(raw_daypart or "").strip()
        block_key = (block_date, block_daypart)
        has_quantity = False
        for col_idx in quantity_indexes:
            if col_idx < 0 or col_idx >= len(row):
                continue
            if _parse_strict_numeric_cell(row[col_idx]) is not None:
                has_quantity = True
                break
        if current_block is None or current_block.get("key") != block_key:
            current_block = {
                "key": block_key,
                "date_mmdd": block_date,
                "daypart": block_daypart,
                "row_start": row_idx,
                "row_end": row_idx,
                "row_count": 1,
            }
            if structural_blank_hint_enabled:
                current_block["blank_quantity_row_indexes"] = ([] if has_quantity else [row_idx])
            blocks.append(current_block)
            continue
        current_block["row_end"] = row_idx
        current_block["row_count"] = int(current_block.get("row_count") or 0) + 1
        if structural_blank_hint_enabled and not has_quantity:
            blank_indexes = current_block.get("blank_quantity_row_indexes")
            if isinstance(blank_indexes, list):
                blank_indexes.append(row_idx)

    unmatched_structural_row_indexes: list[int] = []
    matched_reference_key_count = 0
    reference_key_count = 0
    reference_alignment_weak = False
    normalized_first_rows = [list(row) for row in (first_pass_rows or []) if isinstance(row, list)]
    if normalized_first_rows:
        resolved_first_fields = list(first_pass_fields or structural_fields or [])
        first_keys = [
            key
            for key in (
                _build_structural_row_key(row=row, fields=resolved_first_fields)
                for row in normalized_first_rows
            )
            if key is not None
        ]
        reference_key_count = len(first_keys)
        if first_keys:
            first_idx = 0
            for row_idx, row in enumerate(normalized_rows):
                structure_key = _build_structural_row_key(row=row, fields=structural_fields)
                if structure_key is None:
                    continue
                if first_idx < len(first_keys) and structure_key == first_keys[first_idx]:
                    first_idx += 1
                    matched_reference_key_count += 1
                    continue
                unmatched_structural_row_indexes.append(row_idx)
            match_ratio = (
                float(matched_reference_key_count) / float(reference_key_count)
                if reference_key_count > 0
                else 0.0
            )
            if matched_reference_key_count <= 0 or match_ratio < 0.5:
                reference_alignment_weak = True
                unmatched_structural_row_indexes = []

    structural_blank_anchor_row_indexes: list[int] = []
    if structural_blank_hint_enabled:
        for block in blocks:
            blank_indexes = block.get("blank_quantity_row_indexes")
            if isinstance(blank_indexes, list):
                structural_blank_anchor_row_indexes.extend(
                    int(item)
                    for item in blank_indexes
                    if isinstance(item, int) or (isinstance(item, str) and item.isdigit())
                )
        structural_blank_anchor_row_indexes = sorted(set(structural_blank_anchor_row_indexes))

    compact_blocks: list[dict[str, Any]] = []
    for block in blocks[:48]:
        compact_block = {
            "date_mmdd": block.get("date_mmdd"),
            "daypart": block.get("daypart"),
            "row_start": block.get("row_start"),
            "row_end": block.get("row_end"),
            "row_count": block.get("row_count"),
        }
        blank_indexes = block.get("blank_quantity_row_indexes")
        if isinstance(blank_indexes, list) and blank_indexes:
            compact_block["blank_quantity_row_indexes"] = [int(idx) for idx in blank_indexes[:24]]
        compact_blocks.append(compact_block)
    return {
        "blocks": compact_blocks,
        "unmatched_structural_row_indexes": unmatched_structural_row_indexes[:160],
        "structural_blank_anchor_row_indexes": structural_blank_anchor_row_indexes[:160],
        "reference_alignment_weak": bool(reference_alignment_weak),
        "matched_reference_key_count": int(matched_reference_key_count),
        "reference_key_count": int(reference_key_count),
        "reference_key_match_ratio": (
            round(float(matched_reference_key_count) / float(reference_key_count), 4)
            if reference_key_count > 0
            else None
        ),
    }


def _normalize_prompt_block_date(value: object) -> str:
    normalized = _normalize_entry_date(value)
    if isinstance(normalized, date):
        return normalized.strftime("%m/%d")
    return str(value or "").strip()


def _prompt_row_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _summarize_prompt_row_blocks(
    prompt_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in (prompt_rows or []) if isinstance(row, dict)]
    if not rows:
        return []
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for idx, row in enumerate(rows):
        date_label = _normalize_prompt_block_date(
            _prompt_row_value(row, "date_mmdd", "date", "menu_date")
        )
        daypart_raw = _prompt_row_value(row, "daypart")
        daypart = _normalize_daypart_key(daypart_raw) or daypart_raw
        menu_name = _prompt_row_value(row, "menu_name", "menu")
        key = (date_label, daypart)
        if current and current.get("_key") == key:
            current["row_end"] = idx
            current["row_count"] = int(current.get("row_count") or 0) + 1
            if menu_name:
                current_menus = current.setdefault("_menus", [])
                if menu_name not in current_menus and len(current_menus) < 4:
                    current_menus.append(menu_name)
            continue
        current = {
            "_key": key,
            "_menus": [menu_name] if menu_name else [],
            "row_start": idx,
            "row_end": idx,
            "row_count": 1,
            "date_mmdd": date_label,
            "daypart": daypart,
        }
        blocks.append(current)
    summarized: list[dict[str, Any]] = []
    for block in blocks:
        entry = {
            "row_start": int(block.get("row_start") or 0),
            "row_end": int(block.get("row_end") or 0),
            "row_count": int(block.get("row_count") or 0),
            "date_mmdd": str(block.get("date_mmdd") or ""),
            "daypart": str(block.get("daypart") or ""),
        }
        menus = [str(item).strip() for item in block.get("_menus", []) if str(item).strip()]
        if menus:
            entry["menu_examples"] = menus[:4]
        summarized.append(entry)
    return summarized


def _summarize_prompt_date_blocks(
    prompt_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    blocks = _summarize_prompt_row_blocks(prompt_rows)
    if not blocks:
        return []
    summarized: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        date_mmdd = str(block.get("date_mmdd") or "").strip()
        row_start = block.get("row_start")
        row_end = block.get("row_end")
        row_count = block.get("row_count")
        daypart = str(block.get("daypart") or "").strip()
        if not isinstance(row_start, int) or not isinstance(row_end, int):
            continue
        if current is None or current.get("date_mmdd") != date_mmdd:
            current = {
                "date_mmdd": date_mmdd,
                "row_start": row_start,
                "row_end": row_end,
                "row_count": int(row_count or 0),
                "sub_blocks": [
                    {
                        "daypart": daypart,
                        "row_start": row_start,
                        "row_end": row_end,
                        "row_count": int(row_count or 0),
                    }
                ],
            }
            summarized.append(current)
            continue
        current["row_end"] = row_end
        current["row_count"] = int(current.get("row_count") or 0) + int(row_count or 0)
        sub_blocks = current.setdefault("sub_blocks", [])
        if isinstance(sub_blocks, list):
            sub_blocks.append(
                {
                    "daypart": daypart,
                    "row_start": row_start,
                    "row_end": row_end,
                    "row_count": int(row_count or 0),
                }
            )
    return summarized


def _summarize_tabular_row_blocks(
    rows: list[list[str]] | None,
    *,
    date_index: int = 0,
    daypart_index: int = 1,
    menu_index: int = 2,
) -> list[dict[str, Any]]:
    prompt_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows or []):
        if not isinstance(row, list):
            continue
        prompt_rows.append(
            {
                "row_id": f"row-{idx + 1}",
                "date_mmdd": row[date_index] if date_index < len(row) else "",
                "daypart": row[daypart_index] if daypart_index < len(row) else "",
                "menu_name": row[menu_index] if menu_index < len(row) else "",
            }
        )
    return _summarize_prompt_row_blocks(prompt_rows)


def _summarize_tabular_date_blocks(
    rows: list[list[str]] | None,
    *,
    date_index: int = 0,
    daypart_index: int = 1,
    menu_index: int = 2,
) -> list[dict[str, Any]]:
    prompt_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows or []):
        if not isinstance(row, list):
            continue
        prompt_rows.append(
            {
                "row_id": f"row-{idx + 1}",
                "date_mmdd": row[date_index] if date_index < len(row) else "",
                "daypart": row[daypart_index] if daypart_index < len(row) else "",
                "menu_name": row[menu_index] if menu_index < len(row) else "",
            }
        )
    return _summarize_prompt_date_blocks(prompt_rows)


def _build_prompt_rows_from_table_rows(
    *,
    fields: list[str],
    rows: list[list[str]] | None,
    row_id_prefix: str,
) -> list[dict[str, Any]]:
    normalized_rows = [list(row) for row in (rows or []) if isinstance(row, list)]
    if not fields or not normalized_rows:
        return []
    row_ids = [f"{row_id_prefix}-{idx + 1}" for idx in range(len(normalized_rows))]
    return _build_llm_review_prompt_rows(fields=fields, rows=normalized_rows, row_ids=row_ids)


def _summarize_prompt_block_coverage_gaps(
    *,
    baseline_prompt_rows: list[dict[str, Any]] | None,
    first_pass_prompt_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    baseline_blocks = _summarize_prompt_row_blocks(baseline_prompt_rows)
    if not baseline_blocks or not first_pass_prompt_rows:
        return []
    first_pass_counts: dict[tuple[str, str], int] = {}
    for row in first_pass_prompt_rows:
        if not isinstance(row, dict):
            continue
        date_label = _normalize_prompt_block_date(
            _prompt_row_value(row, "date_mmdd", "date", "menu_date")
        )
        daypart_raw = _prompt_row_value(row, "daypart")
        daypart = _normalize_daypart_key(daypart_raw) or daypart_raw
        key = (date_label, daypart)
        if not any(key):
            continue
        first_pass_counts[key] = first_pass_counts.get(key, 0) + 1
    coverage_gaps: list[dict[str, Any]] = []
    for block in baseline_blocks:
        key = (str(block.get("date_mmdd") or ""), str(block.get("daypart") or ""))
        baseline_count = int(block.get("row_count") or 0)
        first_pass_count = int(first_pass_counts.get(key, 0))
        if first_pass_count >= baseline_count:
            continue
        entry = dict(block)
        entry["first_pass_row_count"] = first_pass_count
        entry["missing_structural_rows"] = max(baseline_count - first_pass_count, 0)
        coverage_gaps.append(entry)
    return coverage_gaps


def _format_block_order_hint(blocks: list[dict[str, Any]] | None) -> str:
    lines: list[str] = []
    for block in (blocks or [])[:80]:
        if not isinstance(block, dict):
            continue
        date_mmdd = str(block.get("date_mmdd") or "").strip() or "?"
        daypart = str(block.get("daypart") or "").strip() or "?"
        row_start = block.get("row_start")
        row_end = block.get("row_end")
        if isinstance(row_start, int) and isinstance(row_end, int):
            if row_start == row_end:
                range_text = f"row_index {row_start}"
            else:
                range_text = f"row_index {row_start}-{row_end}"
        else:
            range_text = "row_index ?"
        lines.append(f"{date_mmdd} {daypart} -> {range_text}")
    return "\n".join(lines)


def _collect_candidate_blank_edge_hints(
    *,
    candidate_rows: list[list[str]] | None,
    quantity_columns: list[dict[str, str | int]] | None,
    date_blocks: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    normalized_rows = [list(row) for row in (candidate_rows or []) if isinstance(row, list)]
    if not normalized_rows:
        return []
    quantity_indexes: list[int] = []
    for column in quantity_columns or []:
        try:
            col_idx = int(column.get("index"))  # type: ignore[arg-type]
        except Exception:
            continue
        if col_idx >= 0:
            quantity_indexes.append(col_idx)
    if not quantity_indexes:
        return []

    hints: list[dict[str, Any]] = []
    for block in date_blocks or []:
        if not isinstance(block, dict):
            continue
        row_start = block.get("row_start")
        row_end = block.get("row_end")
        if not isinstance(row_start, int) or not isinstance(row_end, int):
            continue
        if row_start < 0 or row_end < row_start:
            continue
        row_indexes = list(range(row_start, min(row_end, len(normalized_rows) - 1) + 1))
        if not row_indexes:
            continue
        filled_row_indexes: list[int] = []
        blank_row_indexes: list[int] = []
        for row_idx in row_indexes:
            row = normalized_rows[row_idx]
            has_numeric = False
            for col_idx in quantity_indexes:
                if col_idx < len(row) and _parse_strict_numeric_cell(row[col_idx]) is not None:
                    has_numeric = True
                    break
            if has_numeric:
                filled_row_indexes.append(row_idx)
            else:
                blank_row_indexes.append(row_idx)
        if not filled_row_indexes or not blank_row_indexes:
            continue
        leading_blank_count = 0
        for row_idx in row_indexes:
            if row_idx in blank_row_indexes:
                leading_blank_count += 1
                continue
            break
        trailing_blank_count = 0
        for row_idx in reversed(row_indexes):
            if row_idx in blank_row_indexes:
                trailing_blank_count += 1
                continue
            break
        if trailing_blank_count <= 0 or leading_blank_count > 0:
            continue
        hint = {
            "date_mmdd": block.get("date_mmdd"),
            "row_start": row_start,
            "row_end": row_end,
            "filled_row_indexes": filled_row_indexes[:40],
            "blank_row_indexes": blank_row_indexes[:40],
            "trailing_blank_row_indexes": row_indexes[-trailing_blank_count:][:20],
            "pattern": "trailing_blank_run_after_filled_rows",
            "note": "Verify that blank rows were not rotated to the end of the date block.",
        }
        sub_blocks = block.get("sub_blocks")
        if isinstance(sub_blocks, list) and sub_blocks:
            hint["sub_blocks"] = sub_blocks[:8]
        hints.append(hint)
    return hints


def _compact_llm_reparse_audit_feedback(audit_result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(audit_result, dict) or not audit_result:
        return None
    compact = {
        "status": str(audit_result.get("status") or "").strip().lower() or None,
        "provider": str(audit_result.get("actual_provider") or audit_result.get("provider") or "").strip() or None,
        "model": str(audit_result.get("model") or "").strip() or None,
        "issue_count": int(audit_result.get("issue_count") or 0),
        "blocking_issue_count": int(audit_result.get("blocking_issue_count") or 0),
        "issues": [dict(item) for item in (audit_result.get("issues") or [])[:12] if isinstance(item, dict)],
        "blocking_issues": [
            dict(item) for item in (audit_result.get("blocking_issues") or [])[:8] if isinstance(item, dict)
        ],
    }
    error = str(audit_result.get("error") or "").strip()
    if error:
        compact["error"] = error
    threshold = audit_result.get("threshold")
    if isinstance(threshold, dict) and threshold:
        compact["threshold"] = dict(threshold)
    return compact


def _resolve_reparse_llm_baseline(
    *,
    order_id: str,
    template: dict[str, Any],
    fallback_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    structural_baseline = _build_reparse_structural_baseline(
        order_id=order_id,
        template=template,
        fallback_payload=fallback_payload,
    )
    current_sheet, current_sheet_error = get_ocr_sheet(order_id)
    if isinstance(current_sheet, dict) and not current_sheet_error:
        raw_fields = current_sheet.get("fields") if isinstance(current_sheet.get("fields"), list) else []
        fields: list[str] = []
        header: list[str] = []
        for idx, item in enumerate(raw_fields):
            if isinstance(item, dict):
                field_name = str(item.get("field") or "").strip()
                header_name = str(item.get("header") or "").strip()
            else:
                field_name = str(item or "").strip()
                header_name = ""
            if not field_name:
                continue
            fields.append(field_name)
            header.append(header_name or _field_label(field_name))
        rows_payload = current_sheet.get("rows") if isinstance(current_sheet.get("rows"), list) else []
        rows: list[list[str]] = []
        for row in rows_payload:
            if not isinstance(row, list):
                continue
            normalized_row = [str(cell or "").strip() for cell in row]
            if fields:
                while len(normalized_row) < len(fields):
                    normalized_row.append("")
                normalized_row = normalized_row[: len(fields)]
            rows.append(normalized_row)
        row_ids = [
            str(item).strip()
            for item in (
                current_sheet.get("row_ids")
                if isinstance(current_sheet.get("row_ids"), list)
                else []
            )
            if str(item).strip()
        ]
        if len(row_ids) < len(rows):
            row_ids.extend([f"sheet-row-{idx + 1}" for idx in range(len(row_ids), len(rows))])
        payload = _load_order_ocr_cache(order_id)
        raw_output = _snapshot_raw_ocr_payload(payload) if isinstance(payload, dict) else {}
        if not isinstance(raw_output, dict):
            raw_output = {}
        if not raw_output and isinstance(fallback_payload, dict):
            raw_output = _snapshot_raw_ocr_payload(fallback_payload)
        if fields and rows:
            current_sheet_baseline = {
                "fields": fields,
                "header": header[: len(fields)],
                "rows": rows,
                "row_ids": row_ids[: len(rows)],
                "baseline_revision_id": None,
                "raw_output": raw_output,
                "baseline_source": "sheet",
            }
            structural_rows = (
                [list(row) for row in (structural_baseline.get("rows") or []) if isinstance(row, list)]
                if isinstance(structural_baseline, dict)
                else []
            )
            if structural_rows:
                current_sheet_baseline["structure_rows"] = structural_rows
                current_sheet_baseline["structure_fields"] = [
                    str(field).strip()
                    for field in (structural_baseline.get("fields") or [])
                    if str(field).strip()
                ] or fields
                current_sheet_baseline["structure_row_ids"] = [
                    str(item).strip()
                    for item in (structural_baseline.get("row_ids") or [])
                    if str(item).strip()
                ][: len(structural_rows)]
                current_sheet_baseline["structure_source"] = str(
                    structural_baseline.get("baseline_source") or ""
                ).strip() or None
            if len(structural_rows) > len(rows):
                return structural_baseline
            return current_sheet_baseline
    if isinstance(structural_baseline, dict) and structural_baseline:
        return structural_baseline
    payload = _load_order_ocr_cache(order_id)
    if isinstance(payload, dict):
        try:
            return _resolve_llm_review_baseline(order_id=order_id, payload=payload, template=template)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reparse baseline resolution from cache failed", order_id=order_id, error=str(exc))
    if isinstance(fallback_payload, dict):
        try:
            return _resolve_llm_review_baseline(order_id=order_id, payload=fallback_payload, template=template)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reparse baseline resolution from fallback payload failed", order_id=order_id, error=str(exc))
    return None


def _build_reparse_structural_baseline(
    *,
    order_id: str,
    template: dict[str, Any],
    fallback_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        with session_scope() as session:
            order = session.get(Order, order_id)
            if not order:
                return None
            facility_id = str(order.facility_code or "").strip()
            if not facility_id:
                return None
            week_id = str(order.week_code or "").strip() or None
            received_at = order.received_at or datetime.utcnow()
            facility_week_hint = (
                session.execute(
                    select(Order.week_code)
                    .where(Order.facility_code == facility_id, Order.week_code.is_not(None))
                    .order_by(Order.received_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            global_week_hint = (
                session.execute(
                    select(Order.week_code)
                    .where(Order.week_code.is_not(None))
                    .order_by(Order.received_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            raw_order_lines = (
                session.execute(select(OrderLine).where(OrderLine.order_id == order_id))
                .scalars()
                .all()
            )
            order_lines = [
                {
                    "date": line.date,
                    "daypart": line.daypart,
                    "menu_name": line.menu_name,
                    "diet_type": line.diet_type,
                    "area_id": line.area_id,
                    "quantity_original": line.quantity_original,
                    "quantity_corrected": line.quantity_corrected,
                }
                for line in raw_order_lines
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reparse structural baseline order lookup failed", order_id=order_id, error=str(exc))
        return None

    fields, field_index = _build_sheet_fields_and_indexes(template)
    if not fields or _validate_sheet_template_fields(fields):
        return None

    ocr_payload = None
    if isinstance(fallback_payload, dict):
        ocr_payload = dict(fallback_payload)
    else:
        payload, _ = get_ocr_output(order_id, persist_cache=False)
        if isinstance(payload, dict):
            ocr_payload = payload

    resolved_week_id = _resolve_sheet_week_id(
        current_week_id=week_id,
        received_at=received_at,
        order_lines=order_lines,
        ocr_payload=ocr_payload,
        facility_id=facility_id,
        week_hints=[hint for hint in [facility_week_hint, global_week_hint] if hint],
    )
    if not resolved_week_id:
        return None

    entries, entry_source = _build_sheet_menu_entries(
        week_id=resolved_week_id,
        facility_id=facility_id,
        ocr_payload=ocr_payload,
        template=template,
        received_at=received_at,
    )
    if not entries:
        return None

    rows, resolved_source = _build_rows_from_menu_entries(
        entries=entries,
        fields=fields,
        field_index=field_index,
        line_dates=set(),
        source=entry_source,
        payload_dates=set(),
        payload_row_count=0,
        scope_anchor_date=None,
    )
    if not rows:
        return None

    header = _sheet_header_from_template(fields, template)
    raw_output = _snapshot_raw_ocr_payload(ocr_payload) if isinstance(ocr_payload, dict) else {}
    if not raw_output and isinstance(fallback_payload, dict):
        raw_output = _snapshot_raw_ocr_payload(fallback_payload)
    return {
        "fields": fields,
        "header": header[: len(fields)],
        "rows": [list(row.get("values") or []) for row in rows],
        "row_ids": [str(row.get("row_id") or "").strip() or f"sheet-row-{idx + 1}" for idx, row in enumerate(rows)],
        "baseline_revision_id": None,
        "raw_output": raw_output,
        "baseline_source": f"{resolved_source}_structure",
        "week_id": resolved_week_id,
    }


def _build_llm_assist_prompt(
    *,
    provider: str,
    template: dict,
    pipeline_output: dict | None,
    llm_assist: bool,
    prompt_preset: str | None = None,
    failure_context: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    evaluator_feedback: dict[str, Any] | None = None,
    draft_rows_override: list[list[str]] | None = None,
    draft_rows_label: str | None = None,
    first_pass_rows_override: list[list[str]] | None = None,
) -> str | None:
    prompt_key = "openai_ocr_prompt" if provider == "openai" else "gemini_ocr_prompt"
    base_custom = str(template.get(prompt_key) or "").strip()
    sections: list[str] = []
    if base_custom and not _looks_like_generated_reparse_prompt(base_custom):
        sections.append(f"Facility-specific instruction:\n{base_custom}")
    if llm_assist:
        preset_text = _build_llm_assist_preset_instruction(prompt_preset)
        if preset_text:
            sections.append(f"Operator-selected focus preset:\n{preset_text}")
        first_pass_rows = [list(row) for row in (first_pass_rows_override or []) if isinstance(row, list)]
        if not first_pass_rows and isinstance(pipeline_output, dict):
            first_pass_rows = _extract_first_pass_rows_from_payload(pipeline_output, template)
            if not first_pass_rows:
                first_pass_rows = _extract_sheet_rows_from_payload(pipeline_output, template)
        structural_fields = (
            [str(field).strip() for field in (baseline.get("structure_fields") or []) if str(field).strip()]
            if isinstance(baseline, dict)
            else []
        )
        if not structural_fields and isinstance(baseline, dict):
            structural_fields = [
                str(field).strip() for field in (baseline.get("fields") or []) if str(field).strip()
            ]
        structural_rows = (
            [list(row) for row in (baseline.get("structure_rows") or []) if isinstance(row, list)]
            if isinstance(baseline, dict)
            else []
        )
        if not structural_rows and isinstance(baseline, dict):
            structural_rows = [list(row) for row in (baseline.get("rows") or []) if isinstance(row, list)]
        block_anchor_hints = _build_reparse_block_anchor_hints(
            structural_fields=structural_fields,
            structural_rows=structural_rows,
            first_pass_fields=_row_fields_from_template(template) or structural_fields,
            first_pass_rows=first_pass_rows,
        )
        sections.append(
            "Second-pass repair mode:\n"
            "- Treat the first-pass yomitoku output as the baseline draft.\n"
            "- Treat the current sheet/baseline rows as the row structure shown to the user; existing quantities may be stale and must be re-verified against the fax.\n"
            "- Use the fax image to repair or confirm that draft, not to replace the whole table unnecessarily.\n"
            "- Determine each date/daypart block's quantity pattern first, then expand it to row-level JSON.\n"
            "- Keep row order stable.\n"
            "- row_index is the structural row position from the current sheet; blank rows still consume row indexes.\n"
            "- Return the full structural rows for the current sheet, not a quantity-only sparse draft.\n"
            "- Copy date/daypart/menu cells from the current sheet structure exactly unless the fax clearly contradicts them.\n"
            "- Fill missing cells when readable; keep empty string when unreadable.\n"
            "- It is valid for some rows to remain blank across all quantity columns.\n"
            "- Do not compress blank rows out of the output, even when the first visible quantity appears later in the block.\n"
            "- If evaluator feedback identifies one structural drift example, re-check every date/daypart block for the same pattern before returning JSON.\n"
            "- Do NOT fill a row unless a quantity is directly visible for that row or an explicit visual span clearly covers that row.\n"
            "- When the sheet contains more structural rows than first-pass OCR, preserve the structural rows and insert blank quantity rows where evidence is missing.\n"
            "- If a handwritten quantity is unreadable, infer only from nearby recognized quantities within the same date/daypart block when continuity is clear; otherwise keep empty string.\n"
            "- Continuity is never clear across a block boundary or across blank-anchor structural rows.\n"
            "- Keep blank-anchor structural rows at their exact row indexes; never rotate them to the end of a block.\n"
            "- Leading blank rows inside a date/daypart block may be intentional; keep them at the start of that block unless direct row-level evidence says otherwise.\n"
            "- Do not trade missing blank rows for extra repeated quantities later in the block.\n"
            "- If a parenthesis/bracket mark spans multiple quantity cells with one number, copy that number to every covered cell.\n"
            "- If arrows/vertical range lines indicate a number applies to a span, copy that number to all cells in that span.\n"
            "- Never extend a quantity into visually separate rows above or below the marked block.\n"
            "- Apply copying/inference only within the clearly indicated range.\n"
            "- Quantity cells must contain digits only.\n"
            "- Structured table cells and suspicious-cell diagnostics are provided below; use them when deciding what to repair.\n"
            "- Return strict JSON only."
        )
        if isinstance(failure_context, dict) and failure_context:
            try:
                failure_text = json.dumps(failure_context, ensure_ascii=False)
            except TypeError:
                failure_text = str(failure_context)
            sections.append(
                "Automatic fallback context:\n"
                "The first-pass yomitoku/pipeline OCR did not produce parseable order lines.\n"
                f"{_truncate_assist_text(failure_text, max_chars=4000)}"
            )
        baseline_rows = _build_llm_assist_baseline_rows(baseline)
        structural_prompt_rows = _build_llm_assist_structural_rows(baseline)
        baseline_date_ranges = _summarize_prompt_date_blocks(structural_prompt_rows or baseline_rows)
        if baseline_rows:
            baseline_source = (
                str(baseline.get("baseline_source") or "").strip()
                if isinstance(baseline, dict)
                else ""
            )
            baseline_revision_id = (
                str(baseline.get("baseline_revision_id") or "").strip()
                if isinstance(baseline, dict)
                else ""
            )
            baseline_sections = [
                "Current sheet/baseline rows shown to the user:\n"
                f"{_truncate_assist_text(json.dumps(baseline_rows[:80], ensure_ascii=False), max_chars=5000)}"
            ]
            if baseline_source:
                baseline_sections.append(f"Current baseline source: {baseline_source}")
            if baseline_revision_id:
                baseline_sections.append(f"Current baseline revision_id: {baseline_revision_id}")
            sections.append("\n".join(baseline_sections))
            baseline_block_ranges = _summarize_prompt_row_blocks(structural_prompt_rows or baseline_rows)
            if baseline_block_ranges:
                sections.append(
                    "Row block boundaries from structural sheet/baseline:\n"
                    f"{_truncate_assist_text(json.dumps(baseline_block_ranges[:80], ensure_ascii=False), max_chars=5000)}"
                )
                sections.append(
                    "Block boundary rules:\n"
                    "- Treat each consecutive date/daypart block above as a hard row boundary.\n"
                    "- Keep every quantity inside its own block.\n"
                    "- If a block has no direct visual quantity evidence, keep the whole block blank.\n"
                    "- Never start the next block's quantity before that block begins.\n"
                    "- Never let one handwritten number continue past the end row of its marked block."
                )
        if baseline_date_ranges:
            sections.append(
                "Date block layout summary:\n"
                f"{_truncate_assist_text(json.dumps(baseline_date_ranges[:80], ensure_ascii=False), max_chars=5000)}"
            )
            sections.append(
                "Date block rules:\n"
                "- Inside each date block, preserve the exact order of its sub-blocks.\n"
                "- Blank sub-blocks may appear at the start, middle, or end of a date block.\n"
                "- Do NOT rotate a blank sub-block to the end of a date block just because later rows have quantities.\n"
                "- If the first visible quantity for a date block appears below the top rows, keep all earlier structural rows blank.\n"
                "- Never shift a lower handwritten quantity upward into earlier rows of the same date block.\n"
                "- If a sequence of blank-anchor rows exists before a visible handwritten quantity, keep that full sequence blank.\n"
                "- If one or more blank rows appear before the next visible handwritten number, those earlier rows must stay blank.\n"
                "- Never pull the next meal block's number upward just to remove blank rows.\n"
                "- A later meal block's quantity must never overwrite an earlier meal block's filled rows."
            )
        if structural_prompt_rows:
            structure_source = ""
            if isinstance(baseline, dict):
                structure_source = str(
                    baseline.get("structural_baseline_source")
                    or baseline.get("structure_source")
                    or ""
                ).strip()
            structure_sections = [
                "Structural sheet rows for blank-anchor preservation:\n"
                f"{_truncate_assist_text(json.dumps(structural_prompt_rows[:80], ensure_ascii=False), max_chars=5000)}"
            ]
            if structure_source:
                structure_sections.append(f"Structural baseline source: {structure_source}")
            sections.append("\n".join(structure_sections))
        block_ranges = block_anchor_hints.get("blocks")
        if isinstance(block_ranges, list) and block_ranges:
            sections.append(
                "Structural block anchor summary:\n"
                f"{_truncate_assist_text(json.dumps(block_ranges[:80], ensure_ascii=False), max_chars=5000)}"
            )
        if bool(block_anchor_hints.get("reference_alignment_weak")):
            sections.append(
                "Reference row alignment warning:\n"
                "- First-pass row keys are too noisy to use as row-index anchors.\n"
                "- Use only the structural date/daypart block boundaries from the current sheet.\n"
                "- Do not infer blank-anchor row indexes from the noisy first-pass rows."
            )
        unmatched_structural_row_indexes = block_anchor_hints.get("unmatched_structural_row_indexes")
        if not unmatched_structural_row_indexes:
            unmatched_structural_row_indexes = block_anchor_hints.get("structural_blank_anchor_row_indexes")
        if isinstance(unmatched_structural_row_indexes, list) and unmatched_structural_row_indexes:
            sections.append(
                "Blank-anchor structural row indexes:\n"
                f"{_truncate_assist_text(json.dumps(unmatched_structural_row_indexes[:120], ensure_ascii=False), max_chars=2000)}\n"
                "- These structural rows were not matched by first-pass OCR.\n"
                "- Keep them blank unless the fax shows direct row-level evidence.\n"
                "- Never backfill them from the next block."
            )
        if baseline_rows and first_pass_rows and len(baseline_rows) > len(first_pass_rows):
            sections.append(
                "Structural row preservation hint:\n"
                f"- Current sheet rows: {len(baseline_rows)}.\n"
                f"- First-pass yomitoku rows: {len(first_pass_rows)}.\n"
                "- Missing structural rows are not a license to copy neighboring quantities.\n"
                "- Keep quantity cells empty on unmatched structural rows unless the fax shows a direct mark or explicit span for them."
            )
        if baseline_rows:
            baseline_quantity_columns = [
                column
                for column in _template_quantity_columns(template)
                if isinstance(column, dict)
            ]
            candidate_blank_hint_rows = (
                [list(row) for row in (draft_rows_override or []) if isinstance(row, list)]
                or first_pass_rows
            )
            trailing_blank_hints = _collect_candidate_blank_edge_hints(
                candidate_rows=candidate_blank_hint_rows,
                quantity_columns=baseline_quantity_columns,
                date_blocks=baseline_date_ranges,
            )
            if trailing_blank_hints:
                sections.append(
                    "Suspicious blank-edge placement hints from the current OCR draft:\n"
                    f"{_truncate_assist_text(json.dumps(trailing_blank_hints[:40], ensure_ascii=False), max_chars=4000)}\n"
                    "- These are suspicious only; verify against the fax image before changing rows.\n"
                    "- If a blank run belongs earlier in the date block, move it back to the correct structural rows."
                )
        compact_feedback = _compact_llm_reparse_audit_feedback(evaluator_feedback)
        feedback_issue_codes: set[str] = set()
        if compact_feedback:
            feedback_issue_codes = {
                _normalize_audit_issue_code(item.get("issue_code"))
                for item in (compact_feedback.get("issues") or [])
                if isinstance(item, dict)
            }
            sections.append(
                "Evaluator feedback from previous OCR draft:\n"
                f"{_truncate_assist_text(json.dumps(compact_feedback, ensure_ascii=False), max_chars=6000)}"
            )
            feedback_row_indexes = sorted(
                {
                    int(item.get("row_index"))
                    for item in (compact_feedback.get("issues") or [])
                    if isinstance(item, dict)
                    and item.get("row_index") is not None
                    and str(item.get("row_index")).strip().lstrip("-").isdigit()
                    and int(item.get("row_index")) >= 0
                }
            )
            if feedback_row_indexes:
                sections.append(
                    "Evaluator row-index repair hints:\n"
                    f"{_truncate_assist_text(json.dumps(feedback_row_indexes[:120], ensure_ascii=False), max_chars=2000)}\n"
                    "- Re-check these rows first against the fax image.\n"
                    "- If evaluator feedback marks overextended_span or missing_blank_anchor_rows on a row, keep that row blank unless the fax shows direct row-level evidence.\n"
                    "- Do not fix an earlier flagged row by pulling the next block's quantity upward."
                )
        structure_sensitive_mode = bool(
            feedback_issue_codes
            & {
                "unexpected_dense_fill",
                "missing_blank_anchor_rows",
                "overextended_span",
                "date_anchor_drift",
                "invalid_numeric_spike",
            }
        ) or bool(isinstance(unmatched_structural_row_indexes, list) and unmatched_structural_row_indexes)
        if draft_rows_override:
            draft_label = str(draft_rows_label or "").strip() or "Previous OCR draft rows"
            try:
                draft_rows_text = json.dumps(draft_rows_override[:120], ensure_ascii=False)
            except TypeError:
                draft_rows_text = ""
            if draft_rows_text:
                sections.append(
                    f"{draft_label}:\n"
                    f"{_truncate_assist_text(draft_rows_text, max_chars=6000)}"
                )
        if isinstance(pipeline_output, dict):
            table_raw = pipeline_output.get("table_raw")
            if isinstance(table_raw, str) and table_raw.strip() and not structure_sensitive_mode:
                sections.append(
                    "First-pass yomitoku markdown:\n"
                    f"{_truncate_assist_text(table_raw.strip(), max_chars=7000)}"
                )
            if first_pass_rows:
                try:
                    rows_text = json.dumps(first_pass_rows[:80], ensure_ascii=False)
                except TypeError:
                    rows_text = ""
                if rows_text:
                    sections.append(
                        "First-pass yomitoku structured rows:\n"
                        f"{_truncate_assist_text(rows_text, max_chars=4000)}"
                    )
            quantity_subgrid_passes = _compact_prompt_quantity_subgrid_passes(pipeline_output)
            if quantity_subgrid_passes:
                sections.append(
                    "Quantity-only second pass on the numeric subgrid:\n"
                    "- This OCR pass sees only the quantity band on the right side of the table.\n"
                    "- It intentionally omits left-side notes, helper marks, and most row anchors.\n"
                    "- Use it to verify numeric patterns and repeated quantities.\n"
                    "- Prefer normalized_rows_preview when it is present; those rows keep the same quantity-grid shape but apply conservative digit-only normalization using neighboring rows.\n"
                    "- normalization_patches show where non-digit OCR text was converted into digits because surrounding numeric evidence matched.\n"
                    "- Do NOT derive date/daypart/menu anchors from this quantity-only pass.\n"
                    "- Keep row anchors from the structural current sheet and first-pass baseline.\n"
                    f"{_truncate_assist_text(json.dumps(quantity_subgrid_passes, ensure_ascii=False), max_chars=8000)}"
                )
            if structure_sensitive_mode:
                sections.append(
                    "Block-anchored repair mode:\n"
                    "- First-pass markdown/cell dumps are omitted here because structural drift was detected.\n"
                    "- Rely on structural sheet rows, block boundaries, quantity-only numeric-band OCR, and evaluator feedback before copying any quantity."
                )
            else:
                tables = _compact_prompt_tables(pipeline_output)
                if tables:
                    sections.append(
                        "First-pass yomitoku structured tables/cells:\n"
                        f"{_truncate_assist_text(json.dumps(tables, ensure_ascii=False), max_chars=10000)}"
                    )
                issues = _compact_prompt_cell_issues(pipeline_output, template)
                if issues:
                    sections.append(
                        "Suspicious first-pass cells (review before changing):\n"
                        f"{_truncate_assist_text(json.dumps(issues, ensure_ascii=False), max_chars=6000)}"
                    )
    if not sections:
        return None
    return "\n\n".join(sections)


def _build_llm_assist_preset_instruction(prompt_preset: str | None) -> str | None:
    normalized = str(prompt_preset or "").strip().lower()
    if not normalized or normalized == "freeform":
        return None
    preset_map = {
        "numeric_verification": (
            "- Primary goal: verify quantity digits before changing structure.\n"
            "- Compare neighboring quantity cells, repeated spans, and quantity-only OCR before editing a number.\n"
            "- Do not rewrite date/daypart/menu anchors unless the fax clearly contradicts them.\n"
            "- When uncertain, keep the structural row and leave the quantity blank instead of forcing a guess."
        ),
        "column_missing": (
            "- Primary goal: recover partially clipped or missing quantity columns.\n"
            "- Focus on whether leftmost/rightmost quantity columns are visible, truncated, or shifted.\n"
            "- Infer a missing quantity only when the fax shows direct evidence for that column or a clearly indicated repeated span.\n"
            "- Do not swap stable columns just to fill a gap."
        ),
        "row_alignment": (
            "- Primary goal: preserve row/block alignment across date and daypart boundaries.\n"
            "- Treat blank-anchor rows and block boundaries as hard constraints.\n"
            "- Prefer leaving quantities blank over shifting them into earlier or later rows.\n"
            "- Re-check every row in the same block if one row looks rotated or offset."
        ),
        "special_diet_semantics": (
            "- Primary goal: protect special diet semantics and prohibited-diet columns.\n"
            "- Pay extra attention to 肉禁, 魚禁, 糖尿, お茶, 袋分け, 常食(袋分け) and similar specialty columns.\n"
            "- Do not merge specialty quantities into regular columns.\n"
            "- If a specialty column is ambiguous, keep it unresolved instead of copying a regular quantity."
        ),
    }
    return preset_map.get(normalized)


def _resolve_provider_model_name(
    *,
    provider: str,
    template: dict[str, Any],
) -> str:
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == "gemini":
        return (
            str(template.get("gemini_ocr_model") or "").strip()
            or os.getenv("GEMINI_OCR_MODEL", "").strip()
            or "gemini-2.5-flash"
        )
    if normalized_provider == "openai":
        return (
            str(template.get("openai_ocr_model") or "").strip()
            or os.getenv("OPENAI_OCR_MODEL", "").strip()
            or "gpt-4.1-mini"
        )
    return ""


def _resolve_repair_focus_locations(
    *,
    current_rows: list[list[str]],
    expected_row_count: int,
    quality_detail: dict[str, Any] | None,
) -> tuple[list[int], list[int]]:
    normalized_rows = [list(row) for row in current_rows if isinstance(row, list)]
    focus_rows: set[int] = set()
    focus_columns: set[int] = set()

    if expected_row_count > 0 and len(normalized_rows) < expected_row_count:
        focus_rows.update(range(len(normalized_rows), expected_row_count))

    if not isinstance(quality_detail, dict):
        return sorted(focus_rows)[:80], sorted(focus_columns)[:40]

    for key in ("invalid_line_indexes", "source_row_mismatches", "source_row_missing"):
        values = quality_detail.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            try:
                idx = int(value)
            except Exception:
                continue
            if idx >= 0:
                focus_rows.add(idx)

    anomaly_rows = quality_detail.get("suspicious_row_indexes")
    if isinstance(anomaly_rows, list):
        for value in anomaly_rows:
            try:
                idx = int(value)
            except Exception:
                continue
            if idx >= 0:
                focus_rows.add(idx)

    column_anomalies = quality_detail.get("column_anomalies")
    if isinstance(column_anomalies, list):
        for item in column_anomalies:
            if not isinstance(item, dict):
                continue
            idx_raw = item.get("index")
            try:
                idx = int(idx_raw)
            except Exception:
                continue
            if idx >= 0:
                focus_columns.add(idx)

    return sorted(focus_rows)[:80], sorted(focus_columns)[:40]


def _build_flash_repair_summary(
    *,
    current_rows: list[list[str]],
    template: dict[str, Any],
    expected_row_count: int,
    quality_error: str,
    quality_detail: dict[str, Any] | None,
    first_pass_model: str | None,
    target_model: str | None,
) -> dict[str, Any]:
    normalized_rows = [list(row) for row in current_rows if isinstance(row, list)]
    row_count = len(normalized_rows)
    quantity_indexes = _template_quantity_column_indexes(template)
    non_empty_counts = _quantity_column_non_empty_counts(
        rows=normalized_rows,
        quantity_indexes=quantity_indexes,
    )
    rows_all_blank: list[int] = []
    if quantity_indexes:
        for row_idx, row in enumerate(normalized_rows):
            has_numeric_qty = False
            for col_idx in quantity_indexes:
                if col_idx < 0 or col_idx >= len(row):
                    continue
                if _parse_strict_numeric_cell(row[col_idx]) is not None:
                    has_numeric_qty = True
                    break
            if not has_numeric_qty:
                rows_all_blank.append(row_idx)

    focus_rows, focus_columns = _resolve_repair_focus_locations(
        current_rows=normalized_rows,
        expected_row_count=expected_row_count,
        quality_detail=quality_detail,
    )
    return {
        "quality_error": str(quality_error or ""),
        "first_pass_model": str(first_pass_model or "") or None,
        "target_model": str(target_model or "") or None,
        "expected_row_count": int(expected_row_count) if expected_row_count > 0 else None,
        "first_pass_row_count": int(row_count),
        "focus_row_indexes": focus_rows,
        "focus_quantity_column_indexes": focus_columns,
        "quantity_non_empty_by_column": {str(idx): int(non_empty_counts.get(idx, 0)) for idx in quantity_indexes},
        "rows_all_quantity_blank": rows_all_blank[:80],
        "quality_detail": quality_detail or {},
    }


def _should_use_gemini_pro_repair_pass(
    *,
    provider: str,
    template: dict[str, Any],
    quality_error: str,
) -> tuple[bool, str]:
    if provider != "gemini":
        return False, ""
    if quality_error not in {"sheet_row_coverage_low", "sheet_row_overfill", "sheet_column_anomaly"}:
        return False, ""
    enabled = str(
        os.getenv("OCR_REPARSE_ENABLE_GEMINI_PRO_ON_QUALITY_FAIL", "1")
    ).strip().lower() not in {"0", "false", "no", "off"}
    if not enabled:
        return False, ""

    first_pass_model = _resolve_provider_model_name(provider=provider, template=template)
    if "flash" not in first_pass_model.lower():
        return False, first_pass_model

    repair_model = (
        str(template.get("gemini_ocr_repair_model") or "").strip()
        or os.getenv("OCR_REPARSE_GEMINI_REPAIR_MODEL", "").strip()
        or "gemini-2.5-pro"
    )
    if repair_model.lower() == first_pass_model.lower():
        return False, repair_model
    return True, repair_model


def _build_quantity_only_repair_prompts(
    *,
    provider: str,
    template: dict[str, Any],
    current_rows: list[list[str]],
    expected_row_count: int,
    quality_error: str,
    quality_detail: dict[str, Any] | None,
    first_pass_model: str | None = None,
    target_model: str | None = None,
    baseline_rows: list[list[str]] | None = None,
    baseline_fields: list[str] | None = None,
    structural_rows: list[list[str]] | None = None,
    structural_fields: list[str] | None = None,
    evaluator_feedback: dict[str, Any] | None = None,
) -> tuple[str, str]:
    prompt_key = "openai_ocr_prompt" if provider == "openai" else "gemini_ocr_prompt"
    user_key = "openai_ocr_user_prompt" if provider == "openai" else "gemini_ocr_user_prompt"
    base_system_prompt = str(template.get(prompt_key) or "").strip()
    base_user_prompt = str(template.get(user_key) or "").strip()
    row_fields = [
        str(field)
        for field in (template.get("main_ocr_row_fields") or [])
        if isinstance(field, str) and str(field).strip()
    ]
    quantity_fields = [field for field in row_fields if field.startswith("qty.")]
    quantity_list = ", ".join(quantity_fields) if quantity_fields else "qty.*"

    missing_indexes: list[int] = []
    if expected_row_count > 0:
        current_count = len([row for row in current_rows if isinstance(row, list)])
        if current_count < expected_row_count:
            missing_indexes = list(range(current_count, expected_row_count))

    detail_text = ""
    if isinstance(quality_detail, dict) and quality_detail:
        try:
            detail_text = json.dumps(quality_detail, ensure_ascii=False)
        except Exception:
            detail_text = ""
    rows_hint = _truncate_assist_text(
        json.dumps(current_rows[:200], ensure_ascii=False),
        max_chars=12000,
    )
    normalized_baseline_rows = [list(row) for row in (baseline_rows or []) if isinstance(row, list)]
    normalized_structural_rows = [list(row) for row in (structural_rows or []) if isinstance(row, list)]
    resolved_structural_rows = normalized_structural_rows or normalized_baseline_rows
    resolved_structural_fields = list(structural_fields or baseline_fields or row_fields)
    block_anchor_hints = _build_reparse_block_anchor_hints(
        structural_fields=resolved_structural_fields,
        structural_rows=resolved_structural_rows,
        first_pass_fields=row_fields or baseline_fields or resolved_structural_fields,
        first_pass_rows=current_rows,
    )
    baseline_rows_hint = ""
    if normalized_baseline_rows:
        baseline_rows_hint = _truncate_assist_text(
            json.dumps(normalized_baseline_rows[:200], ensure_ascii=False),
            max_chars=12000,
        ) or ""
    baseline_block_ranges = _summarize_tabular_row_blocks(normalized_baseline_rows)
    baseline_block_ranges_hint = ""
    if baseline_block_ranges:
        baseline_block_ranges_hint = _truncate_assist_text(
            json.dumps(baseline_block_ranges[:120], ensure_ascii=False),
            max_chars=6000,
        ) or ""
    flash_summary = _build_flash_repair_summary(
        current_rows=current_rows,
        template=template,
        expected_row_count=expected_row_count,
        quality_error=quality_error,
        quality_detail=quality_detail,
        first_pass_model=first_pass_model,
        target_model=target_model,
    )
    summary_text = _truncate_assist_text(
        json.dumps(flash_summary, ensure_ascii=False),
        max_chars=6000,
    )
    focus_rows = flash_summary.get("focus_row_indexes") if isinstance(flash_summary, dict) else []
    focus_cols = flash_summary.get("focus_quantity_column_indexes") if isinstance(flash_summary, dict) else []
    focus_rows_hint = ", ".join(str(idx) for idx in (focus_rows or [])[:40]) if focus_rows else "none"
    focus_cols_hint = ", ".join(str(idx) for idx in (focus_cols or [])[:20]) if focus_cols else "none"
    missing_hint = (
        ", ".join(str(idx) for idx in missing_indexes[:40]) if missing_indexes else "none"
    )
    hard_rules = (
        "Second-pass OCR repair mode:\n"
        "- Use fax image as the primary source of truth.\n"
        "- Start from first-pass (Flash) output as a draft and correct only with visible evidence.\n"
        "- Treat the current sheet/baseline rows as the user-visible structural context.\n"
        "- Determine each date/daypart block's quantity pattern first, then expand it to row-level JSON.\n"
        "- row_index is the structural row position from the current sheet; blank rows still consume row indexes.\n"
        "- Return the full structural rows from the current sheet; do not return quantity-only sparse rows.\n"
        "- Copy date/daypart/menu cells from the current sheet exactly unless the fax clearly contradicts them.\n"
        "- Keep quantity columns independent and never swap values across columns.\n"
        "- Quantity fields must be digits only; unreadable cells must be empty string.\n"
        "- It is valid for some rows to remain blank across all quantity columns.\n"
        "- Do not compress blank rows out of the output, even when the first visible quantity appears later in the block.\n"
        "- If evaluator feedback identifies one structural drift example, re-check every date/daypart block for the same pattern before returning JSON.\n"
        "- Do NOT fill a row unless a quantity is directly visible for that row or an explicit visual span clearly covers that row.\n"
        "- Existing quantities in the current sheet may be stale; use them only as structural context unless the fax confirms them.\n"
        "- If the current sheet/baseline leaves a row blank, keep it blank unless the fax shows direct row-level evidence for that row.\n"
        "- If structural rows are missing from first-pass OCR, insert blank rows where evidence is missing instead of copying neighboring quantities.\n"
        "- Infer unreadable quantities only within the same date/daypart block.\n"
        "- Continuity is never clear across a block boundary or across unmatched structural row indexes.\n"
        "- Keep blank-anchor structural rows at their exact row indexes; never rotate them to the end of a block.\n"
        "- Copy numbers across cells only when explicit span marks exist.\n"
        "- Treat each consecutive date/daypart block from the current sheet as a hard boundary.\n"
        "- If a block has no direct visual quantity evidence, leave the whole block blank.\n"
        "- Treat unmatched structural row indexes as blank anchors unless the fax shows direct row-level evidence.\n"
        "- Never extend one handwritten number into visually separate rows above or below the marked block.\n"
        "- If a visible number starts below blank rows, keep those blank rows empty instead of pulling that number upward.\n"
        "- If consecutive meal blocks each have their own handwritten number, stop the earlier number before the next block begins.\n"
        "- Return strict JSON only."
    )
    if expected_row_count > 0:
        hard_rules += (
            f"\n- Output EXACTLY {expected_row_count} table body rows."
            f"\n- row_index must be continuous 0..{max(expected_row_count - 1, 0)} with no gaps."
            f"\n- Missing row indexes from first pass: {missing_hint}."
            "\n- Missing row indexes are rows to re-check carefully; if the fax does not show a number for them, leave their quantity cells empty."
            "\n- Do not invent extra rows or duplicate row indexes to make the output denser."
        )
    if quantity_fields:
        hard_rules += (
            "\n- Quantity keys (left-to-right mapping) must stay exactly this order:\n"
            f"{quantity_list}"
        )
    hard_rules += (
        f"\n- Recheck focus row indexes first: {focus_rows_hint}."
        f"\n- Recheck focus quantity column indexes first: {focus_cols_hint}."
    )

    system_sections: list[str] = []
    if base_system_prompt:
        system_sections.append(base_system_prompt)
    system_sections.append(hard_rules)

    user_sections: list[str] = []
    if base_user_prompt:
        user_sections.append(base_user_prompt)
    user_sections.append(
        "First-pass OCR candidate rows (for repair hint only):\n"
        f"{rows_hint}"
    )
    if baseline_rows_hint:
        user_sections.append(
            "Current sheet/baseline rows shown to the user:\n"
            f"{baseline_rows_hint}"
        )
    if baseline_block_ranges_hint:
        user_sections.append(
            "Current sheet block boundaries:\n"
            f"{baseline_block_ranges_hint}"
        )
    unmatched_structural_row_indexes = block_anchor_hints.get("unmatched_structural_row_indexes")
    if not unmatched_structural_row_indexes:
        unmatched_structural_row_indexes = block_anchor_hints.get("structural_blank_anchor_row_indexes")
    if isinstance(unmatched_structural_row_indexes, list) and unmatched_structural_row_indexes:
        user_sections.append(
            "Blank-anchor structural row indexes:\n"
            f"{_truncate_assist_text(json.dumps(unmatched_structural_row_indexes[:120], ensure_ascii=False), max_chars=2000)}"
        )
    if summary_text:
        user_sections.append(
            "Failure focus locations and first-pass inference summary:\n"
            f"{summary_text}"
        )
    compact_feedback = _compact_llm_reparse_audit_feedback(evaluator_feedback)
    if compact_feedback:
        user_sections.append(
            "Evaluator feedback from previous OCR draft:\n"
            f"{_truncate_assist_text(json.dumps(compact_feedback, ensure_ascii=False), max_chars=6000)}"
        )
    if detail_text:
        user_sections.append(
            "First-pass quality issue detail:\n"
            f'{{"error":"{quality_error}","detail":{detail_text}}}'
        )
    user_sections.append(
        "Repair the extraction and return JSON only. Do not output explanations."
    )

    return "\n\n".join(system_sections), "\n\n".join(user_sections)


def _parse_audit_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not re.fullmatch(r"-?\d+", text):
        return None
    try:
        return int(text)
    except Exception:
        return None


def _parse_audit_confidence(value: object) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        parsed = float(text)
    except Exception:
        return 0.0
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def _normalize_audit_issue_code(value: object) -> str:
    normalized = str(value or "").strip().lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "", normalized)
    return normalized


def _parse_llm_reparse_audit_issues(
    *,
    rows: list[list[str]],
    fields: list[str],
) -> list[dict[str, Any]]:
    if not rows or not fields:
        return []
    field_index = {field: idx for idx, field in enumerate(fields)}
    issues: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        issue_code = _normalize_audit_issue_code(
            row[field_index["issue_code"]]
            if "issue_code" in field_index and field_index["issue_code"] < len(row)
            else ""
        )
        severity = str(
            row[field_index["severity"]]
            if "severity" in field_index and field_index["severity"] < len(row)
            else ""
        ).strip().lower()
        evidence = str(
            row[field_index["evidence"]]
            if "evidence" in field_index and field_index["evidence"] < len(row)
            else ""
        ).strip()
        reason = str(
            row[field_index["reason"]]
            if "reason" in field_index and field_index["reason"] < len(row)
            else ""
        ).strip()
        row_index = _parse_audit_int(
            row[field_index["row_index"]]
            if "row_index" in field_index and field_index["row_index"] < len(row)
            else None
        )
        column_index = _parse_audit_int(
            row[field_index["column_index"]]
            if "column_index" in field_index and field_index["column_index"] < len(row)
            else None
        )
        confidence = _parse_audit_confidence(
            row[field_index["confidence"]]
            if "confidence" in field_index and field_index["confidence"] < len(row)
            else None
        )
        if not issue_code and not reason:
            continue
        issue: dict[str, Any] = {
            "issue_code": issue_code or "unknown_issue",
            "severity": severity or "medium",
            "confidence": round(confidence, 4),
            "evidence": evidence,
            "reason": reason,
        }
        if row_index is not None:
            issue["row_index"] = int(row_index)
        if column_index is not None:
            issue["column_index"] = int(column_index)
        issues.append(issue)
    return issues


def _resolve_llm_audit_provider(
    *,
    primary_provider: str,
    template: dict[str, Any],
) -> str:
    normalized_primary = str(primary_provider or "").strip().lower()
    if normalized_primary not in {"gemini", "openai"}:
        normalized_primary = "gemini"

    def _alternate(provider: str) -> str:
        return "openai" if provider == "gemini" else "gemini"

    configured = (
        str(template.get("llm_audit_provider") or "").strip().lower()
        or str(os.getenv("OCR_REPARSE_AUDIT_PROVIDER", "alternate")).strip().lower()
    )
    if configured in {"", "same", "alternate", "other", "cross", "opposite"}:
        return _alternate(normalized_primary)
    if configured in {"gemini", "openai"}:
        if configured == normalized_primary:
            # Enforce cross-model verification to avoid self-approval.
            return _alternate(normalized_primary)
        return configured
    return _alternate(normalized_primary)


def _has_openai_api_key() -> bool:
    return bool(str(os.getenv("OPENAI_API_KEY", "")).strip())


def _has_gemini_api_key() -> bool:
    return bool(
        str(os.getenv("GEMINI_API_KEY", "")).strip()
        or str(os.getenv("GOOGLE_API_KEY", "")).strip()
    )


def _resolve_gemini_audit_model(
    *,
    primary_provider: str,
    primary_model: str,
    template: dict[str, Any],
) -> str:
    configured = str(
        template.get("gemini_ocr_audit_model")
        or os.getenv("OCR_REPARSE_GEMINI_AUDIT_MODEL", "")
    ).strip()
    if configured:
        candidate = configured
    else:
        primary_model_lower = str(primary_model or "").strip().lower()
        if primary_provider == "gemini" and "flash" in primary_model_lower:
            candidate = "gemini-2.5-pro"
        elif primary_provider == "gemini" and "pro" in primary_model_lower:
            candidate = "gemini-2.5-flash"
        else:
            candidate = (
                str(os.getenv("GEMINI_OCR_MODEL", "")).strip()
                or "gemini-2.5-flash"
            )
    if (
        primary_provider == "gemini"
        and candidate.strip().lower() == str(primary_model or "").strip().lower()
    ):
        if "flash" in candidate.lower():
            return "gemini-2.5-pro"
        if "pro" in candidate.lower():
            return "gemini-2.5-flash"
    return candidate


def _build_llm_reparse_audit_prompts(
    *,
    candidate_rows: list[list[str]],
    reference_rows: list[list[str]] | None,
    quantity_columns: list[dict[str, str | int]],
    expected_row_count: int,
    baseline_rows: list[list[str]] | None = None,
    block_anchor_hints: dict[str, Any] | None = None,
) -> tuple[str, str, list[str]]:
    fields = [
        "issue_code",
        "severity",
        "row_index",
        "column_index",
        "confidence",
        "evidence",
        "reason",
    ]
    current_sheet_date_ranges = (
        _summarize_tabular_date_blocks(baseline_rows)
        if isinstance(baseline_rows, list)
        else []
    )
    candidate_blank_edge_hints = _collect_candidate_blank_edge_hints(
        candidate_rows=candidate_rows,
        quantity_columns=quantity_columns,
        date_blocks=current_sheet_date_ranges,
    )
    system_prompt = (
        "You are an OCR quality auditor for Japanese fax order sheets.\n"
        "Your task is defect finding only. Do NOT correct OCR rows.\n"
        "Use the fax image as primary evidence and candidate OCR rows as a draft.\n"
        "Return strict JSON only with shape:\n"
        '{"facility_name":"", "date_strings":[], "rows":[{"issue_code":"","severity":"","row_index":"","column_index":"","confidence":"","evidence":"","reason":""}]}\n'
        "If no clear defect is found, return rows as [].\n"
        "Rules:\n"
        "- issue_code should be one of: row_count_shortfall, week_scope_mismatch, date_anchor_drift, mirrored_sibling_columns, column_swap, invalid_numeric_spike, all_quantity_blank, unexpected_dense_fill, overextended_span, missing_blank_anchor_rows.\n"
        "- severity should be one of: critical, high, medium, low.\n"
        "- confidence must be 0.00-1.00.\n"
        "- evidence must quote concrete visual evidence from the image (short text).\n"
        "- row_index/column_index should be digits when identifiable, otherwise empty string.\n"
        "- Treat current_sheet_block_ranges as hard structural boundaries between date/daypart blocks.\n"
        "- Treat current_sheet_date_ranges as ordered parent blocks that contain those date/daypart sub-blocks.\n"
        "- Treat blank_anchor_row_indexes_hint as rows that should stay blank unless direct row-level evidence exists.\n"
        "- Flag unexpected_dense_fill when quantities are copied into rows without direct visual evidence.\n"
        "- Flag missing_blank_anchor_rows when rows that should remain blank are filled.\n"
        "- Flag missing_blank_anchor_rows when blank-anchor rows are rotated to the tail of a block instead of staying at their hinted row indexes.\n"
        "- Flag overextended_span when one handwritten number is copied beyond the visually covered block or across a current_sheet_block_ranges boundary.\n"
        "- When checking overextended_span, reject upward propagation from lower rows into earlier blank-anchor rows.\n"
        "- Flag overextended_span when a lower handwritten quantity is shifted upward into earlier rows of the same date block.\n"
        "- Never output markdown or explanations."
    )
    blank_anchor_row_indexes_hint = []
    if isinstance(block_anchor_hints, dict):
        raw_blank_anchor_hint = block_anchor_hints.get("unmatched_structural_row_indexes") or []
        if isinstance(raw_blank_anchor_hint, list):
            blank_anchor_row_indexes_hint = raw_blank_anchor_hint[:160]
        if not blank_anchor_row_indexes_hint:
            fallback_blank_anchor_hint = block_anchor_hints.get("structural_blank_anchor_row_indexes") or []
            if isinstance(fallback_blank_anchor_hint, list):
                blank_anchor_row_indexes_hint = fallback_blank_anchor_hint[:160]
    user_payload = {
        "expected_row_count": int(expected_row_count) if expected_row_count > 0 else None,
        "candidate_rows": candidate_rows[:260],
        "reference_rows_hint": (reference_rows or [])[:120],
        "current_sheet_rows_hint": (baseline_rows or [])[:120],
        "current_sheet_date_ranges": current_sheet_date_ranges[:80],
        "current_sheet_block_ranges": (
            (block_anchor_hints or {}).get("blocks")
                if isinstance(block_anchor_hints, dict)
                else _summarize_tabular_row_blocks(baseline_rows)
            ),
        "blank_anchor_row_indexes_hint": blank_anchor_row_indexes_hint,
        "candidate_blank_edge_hints": candidate_blank_edge_hints[:40],
        "quantity_columns": quantity_columns,
    }
    user_prompt = (
        "Audit the OCR candidate rows and list only defects with evidence.\n"
        f"{_truncate_assist_text(json.dumps(user_payload, ensure_ascii=False), max_chars=18000)}"
    )
    return system_prompt, user_prompt, fields


def _run_llm_reparse_audit(
    *,
    pdf_bytes: bytes,
    provider: str,
    template: dict[str, Any],
    facility_id: str | None,
    preferred_template_id: str | None,
    candidate_rows: list[list[str]],
    reference_rows: list[list[str]] | None,
    expected_row_count: int,
    baseline_rows: list[list[str]] | None = None,
) -> dict[str, Any] | None:
    if provider not in {"gemini", "openai"}:
        return None
    if not _read_reparse_bool_env("OCR_REPARSE_ENABLE_LLM_AUDIT_GATE", True):
        return None

    primary_provider = str(provider or "").strip().lower()
    primary_model = _resolve_provider_model_name(
        provider=primary_provider,
        template=template,
    )
    quantity_columns = _template_quantity_columns(template)
    template_fields = _row_fields_from_template(template)
    block_anchor_hints = _build_reparse_block_anchor_hints(
        structural_fields=template_fields,
        structural_rows=[list(row) for row in (baseline_rows or []) if isinstance(row, list)],
        first_pass_fields=template_fields,
        first_pass_rows=candidate_rows,
    )
    requested_audit_provider = _resolve_llm_audit_provider(
        primary_provider=provider,
        template=template,
    )
    audit_provider = str(requested_audit_provider or "").strip().lower() or "gemini"
    fail_closed = _read_reparse_bool_env("OCR_REPARSE_LLM_AUDIT_FAIL_CLOSED", False)
    provider_switch_reason: str | None = None
    if audit_provider == "openai" and not _has_openai_api_key():
        if _has_gemini_api_key():
            audit_provider = "gemini"
            provider_switch_reason = "openai_api_key_missing_fallback_gemini"
        else:
            return {
                "status": "fail" if fail_closed else "unknown",
                "provider": requested_audit_provider,
                "requested_provider": requested_audit_provider,
                "actual_provider": None,
                "provider_switch_reason": "openai_api_key_missing",
                "model": None,
                "issue_count": 0,
                "blocking_issue_count": 0,
                "issues": [],
                "blocking_issues": [],
                "error": "openai_api_key_missing",
            }
    elif audit_provider == "gemini" and not _has_gemini_api_key():
        if _has_openai_api_key():
            audit_provider = "openai"
            provider_switch_reason = "gemini_api_key_missing_fallback_openai"
        else:
            return {
                "status": "fail" if fail_closed else "unknown",
                "provider": requested_audit_provider,
                "requested_provider": requested_audit_provider,
                "actual_provider": None,
                "provider_switch_reason": "gemini_api_key_missing",
                "model": None,
                "issue_count": 0,
                "blocking_issue_count": 0,
                "issues": [],
                "blocking_issues": [],
                "error": "gemini_api_key_missing",
            }

    system_prompt, user_prompt, row_fields = _build_llm_reparse_audit_prompts(
        candidate_rows=candidate_rows,
        reference_rows=reference_rows,
        baseline_rows=baseline_rows,
        quantity_columns=quantity_columns,
        expected_row_count=expected_row_count,
        block_anchor_hints=block_anchor_hints,
    )

    audit_template = dict(template)
    audit_template["main_ocr_provider"] = audit_provider
    audit_template["_force_main_ocr_provider"] = audit_provider
    audit_template["llm_quantity_only_mode"] = False
    audit_template["main_ocr_row_fields"] = row_fields
    if audit_provider == "openai":
        audit_template["openai_ocr_enabled"] = True
        audit_template["openai_ocr_prompt"] = system_prompt
        audit_template["openai_ocr_user_prompt"] = user_prompt
        model_override = str(
            audit_template.get("openai_ocr_audit_model")
            or os.getenv("OCR_REPARSE_OPENAI_AUDIT_MODEL", "")
        ).strip()
        if model_override:
            audit_template["openai_ocr_model"] = model_override
    else:
        audit_template["gemini_ocr_enabled"] = True
        audit_template["gemini_ocr_prompt"] = system_prompt
        audit_template["gemini_ocr_user_prompt"] = user_prompt
        model_override = _resolve_gemini_audit_model(
            primary_provider=primary_provider,
            primary_model=primary_model,
            template=audit_template,
        )
        if model_override:
            audit_template["gemini_ocr_model"] = model_override

    min_confidence = _read_reparse_float_env(
        "OCR_REPARSE_LLM_AUDIT_MIN_CONFIDENCE",
        0.75,
        min_value=0.0,
    )
    if min_confidence > 1.0:
        min_confidence = 1.0
    blocking_codes = {
        _normalize_audit_issue_code(token)
        for token in str(
            os.getenv(
                "OCR_REPARSE_LLM_AUDIT_BLOCKING_CODES",
                (
                    "row_count_shortfall,week_scope_mismatch,date_anchor_drift,"
                    "mirrored_sibling_columns,column_swap,invalid_numeric_spike,all_quantity_blank,"
                    "unexpected_dense_fill,overextended_span,missing_blank_anchor_rows"
                ),
            )
        ).split(",")
        if str(token).strip()
    }
    template_blocking_codes = template.get("llm_audit_blocking_codes")
    if isinstance(template_blocking_codes, (list, tuple, set)):
        blocking_codes |= {
            _normalize_audit_issue_code(token)
            for token in template_blocking_codes
            if str(token).strip()
        }
    ignored_blocking_codes: set[str] = set()
    template_non_blocking_codes = template.get("llm_audit_non_blocking_codes")
    if isinstance(template_non_blocking_codes, (list, tuple, set)):
        ignored_blocking_codes = {
            _normalize_audit_issue_code(token)
            for token in template_non_blocking_codes
            if str(token).strip()
        }
        blocking_codes -= ignored_blocking_codes
    blocking_severity = {
        str(token).strip().lower()
        for token in str(
            os.getenv("OCR_REPARSE_LLM_AUDIT_BLOCKING_SEVERITY", "critical,high")
        ).split(",")
        if str(token).strip()
    }

    try:
        extracted = extract_fax_data(
            pdf_bytes,
            audit_template,
            facility_id=facility_id,
            preferred_template_id=preferred_template_id,
        )
        provider_debug = (
            extracted.provider_debug
            if isinstance(extracted.provider_debug, dict)
            else {}
        )
        actual_provider = str(
            extracted.ocr_provider
            or provider_debug.get("provider")
            or audit_provider
        ).strip().lower()
        if actual_provider.endswith("_fallback_pipeline"):
            return {
                "status": "fail" if fail_closed else "unknown",
                "provider": audit_provider,
                "requested_provider": requested_audit_provider,
                "actual_provider": actual_provider,
                "provider_switch_reason": provider_switch_reason,
                "model": str(provider_debug.get("model") or "").strip() or None,
                "issue_count": 0,
                "blocking_issue_count": 0,
                "issues": [],
                "blocking_issues": [],
                "error": "audit_provider_fallback_pipeline",
            }

        parsed_rows = [list(row) for row in (extracted.table_rows or []) if isinstance(row, list)]
        issues = _parse_llm_reparse_audit_issues(
            rows=parsed_rows,
            fields=row_fields,
        )
        blocking_issues: list[dict[str, Any]] = []
        for issue in issues:
            evidence = str(issue.get("evidence") or "").strip()
            if not evidence:
                continue
            confidence = float(issue.get("confidence") or 0.0)
            if confidence < min_confidence:
                continue
            issue_code = _normalize_audit_issue_code(issue.get("issue_code"))
            severity = str(issue.get("severity") or "").strip().lower()
            if issue_code in ignored_blocking_codes:
                continue
            if issue_code in blocking_codes or severity in blocking_severity:
                blocking_issues.append(issue)
        status = "pass"
        if blocking_issues:
            status = "fail"
        elif issues:
            status = "unknown"
        return {
            "status": status,
            "provider": audit_provider,
            "requested_provider": requested_audit_provider,
            "actual_provider": actual_provider,
            "provider_switch_reason": provider_switch_reason,
            "model": str(provider_debug.get("model") or "").strip() or None,
            "issue_count": len(issues),
            "blocking_issue_count": len(blocking_issues),
            "issues": issues[:40],
            "blocking_issues": blocking_issues[:20],
            "threshold": {
                "min_confidence": min_confidence,
                "blocking_codes": sorted(token for token in blocking_codes if token),
                "blocking_severity": sorted(token for token in blocking_severity if token),
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "fail" if fail_closed else "unknown",
            "provider": audit_provider,
            "requested_provider": requested_audit_provider,
            "actual_provider": None,
            "provider_switch_reason": provider_switch_reason,
            "model": None,
            "issue_count": 0,
            "blocking_issue_count": 0,
            "issues": [],
            "blocking_issues": [],
            "error": str(exc),
            "threshold": {
                "min_confidence": min_confidence,
                "blocking_codes": sorted(token for token in blocking_codes if token),
                "blocking_severity": sorted(token for token in blocking_severity if token),
            },
        }


def _evaluate_reparse_line_count_regression(
    *,
    provider: str,
    llm_quantity_only_active: bool,
    before_count: int,
    after_count: int,
) -> tuple[str | None, dict[str, Any] | None]:
    if provider not in {"gemini", "openai"}:
        return None, None
    if not llm_quantity_only_active:
        return None, None
    if before_count <= 0:
        return None, None

    min_before = _read_reparse_int_env(
        "OCR_REPARSE_LINE_COUNT_GUARD_MIN_BEFORE",
        24,
        min_value=1,
    )
    if before_count < min_before:
        return None, None

    min_ratio = _read_reparse_float_env(
        "OCR_REPARSE_LINE_COUNT_MIN_RATIO",
        0.70,
        min_value=0.0,
    )
    if min_ratio > 1.0:
        min_ratio = 1.0
    max_drop_abs = _read_reparse_int_env(
        "OCR_REPARSE_LINE_COUNT_MAX_DROP_ABS",
        24,
        min_value=1,
    )
    ratio = float(after_count) / float(before_count) if before_count > 0 else 1.0
    drop_abs = max(before_count - after_count, 0)
    detail = {
        "before_count": int(before_count),
        "after_count": int(after_count),
        "line_count_ratio": round(ratio, 4),
        "drop_abs": int(drop_abs),
        "min_ratio": float(min_ratio),
        "max_drop_abs": int(max_drop_abs),
        "min_before_count": int(min_before),
    }
    if ratio < min_ratio and drop_abs > max_drop_abs:
        detail["quality_issue"] = "line_count_regression"
        return "sheet_line_count_regression", detail
    return None, detail


def _build_reparse_quantity_rules(
    base_rules: dict[str, Any] | None,
    *,
    strict_llm_quantity: bool,
) -> dict[str, Any]:
    rules = dict(base_rules or {})
    if not strict_llm_quantity:
        return rules
    rules["zero_as_empty"] = False
    rules["strict_numeric_quantity_cell"] = True
    # LLM quantity-only mode can intentionally omit non-quantity columns.
    # Keep these rows so source_row_index-based position mapping can restore
    # date/daypart/menu from weekly menu entries.
    rules["allow_blank_structure_rows"] = True
    # LLM quantity-only rows already represent table-body row indexes.
    # Do not skip template header rows again when parsing them.
    rules["rows_are_body_only"] = True
    if rules.get("max_quantity_abs") is None:
        try:
            rules["max_quantity_abs"] = float(os.getenv("OCR_SHEET_MAX_QTY", "150"))
        except Exception:
            rules["max_quantity_abs"] = 150.0
    return rules


def _resolve_reparse_prompt_text(
    *,
    provider: str | None,
    template: dict[str, Any],
    user_prompt: str | None,
) -> str | None:
    normalized_provider = str(provider or "").strip().lower()
    system_prompt = ""
    provider_user_prompt = ""
    if normalized_provider == "openai":
        system_prompt = str(template.get("openai_ocr_prompt") or "").strip()
        provider_user_prompt = str(template.get("openai_ocr_user_prompt") or "").strip()
    elif normalized_provider == "gemini":
        system_prompt = str(template.get("gemini_ocr_prompt") or "").strip()
        provider_user_prompt = str(template.get("gemini_ocr_user_prompt") or "").strip()
    explicit_user_prompt = user_prompt.strip() if isinstance(user_prompt, str) else ""
    resolved_user_prompt = provider_user_prompt or explicit_user_prompt
    parts: list[str] = []
    if system_prompt:
        parts.append(f"[system]\n{system_prompt}")
    if resolved_user_prompt:
        parts.append(f"[user]\n{resolved_user_prompt}")
    if not parts:
        return None
    return "\n\n".join(parts)


def _debug_text_max_chars() -> int:
    raw = str(os.getenv("OCR_REPARSE_DEBUG_MAX_CHARS", "20000")).strip()
    try:
        value = int(raw)
    except ValueError:
        return 20000
    return min(max(value, 2000), 120000)


def _truncate_debug_text(value: object, *, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    trimmed = text[:max_chars].rstrip()
    omitted = len(text) - len(trimmed)
    return f"{trimmed}\n...[truncated {omitted} chars]"


def _compact_debug_rows(rows: object, *, max_rows: int = 8, max_cells: int = 16) -> list[list[str]]:
    if not isinstance(rows, list):
        return []
    compact: list[list[str]] = []
    for row in rows[:max_rows]:
        if isinstance(row, list):
            compact.append([str(cell or "") for cell in row[:max_cells]])
        elif isinstance(row, dict):
            values: list[str] = []
            for key, value in list(row.items())[:max_cells]:
                values.append(f"{key}={value}")
            compact.append(values)
    return compact


def _compact_debug_lines(lines: object, *, max_lines: int = 20) -> list[dict[str, Any]]:
    if not isinstance(lines, list):
        return []
    compact: list[dict[str, Any]] = []
    for line in lines[:max_lines]:
        if not isinstance(line, dict):
            continue
        compact.append(_compact_line_debug_item(line))
    return compact


def _resolve_llm_audit_cluster_fill_decision(llm_audit: dict[str, Any] | None) -> str | None:
    if not isinstance(llm_audit, dict) or not llm_audit:
        return None
    for candidate in (
        llm_audit.get("sheet_cluster_fill_decision"),
        llm_audit.get("order_line_cluster_fill_decision"),
        llm_audit.get("sheet_fill_decision"),
    ):
        parsed = _parse_sheet_fill_decision(candidate)
        if parsed is None:
            continue
        return "allow" if parsed else "deny"
    audit_status = str(llm_audit.get("status") or "").strip().lower()
    if audit_status == "fail":
        return "deny"
    return None


def _llm_reparse_audit_requires_second_pass(llm_audit: dict[str, Any] | None) -> bool:
    if not isinstance(llm_audit, dict) or not llm_audit:
        return False
    status = str(llm_audit.get("status") or "").strip().lower()
    if status == "fail":
        return True
    try:
        if int(llm_audit.get("blocking_issue_count") or 0) > 0:
            return True
    except Exception:
        pass
    try:
        if int(llm_audit.get("issue_count") or 0) > 0:
            return True
    except Exception:
        pass
    return False


def _llm_reparse_audit_issue_codes(llm_audit: dict[str, Any] | None) -> set[str]:
    if not isinstance(llm_audit, dict) or not llm_audit:
        return set()
    issue_codes: set[str] = set()
    for item in (llm_audit.get("issues") or []):
        if not isinstance(item, dict):
            continue
        code = _normalize_audit_issue_code(item.get("issue_code"))
        if code:
            issue_codes.add(code)
    return issue_codes


def _llm_reparse_audit_requires_structural_repair_prompt(
    llm_audit: dict[str, Any] | None,
) -> bool:
    issue_codes = _llm_reparse_audit_issue_codes(llm_audit)
    if not issue_codes:
        return False
    return bool(
        issue_codes
        & {
            "unexpected_dense_fill",
            "missing_blank_anchor_rows",
            "overextended_span",
            "date_anchor_drift",
        }
    )


def _augment_llm_reparse_audit_with_structural_feedback(
    *,
    llm_audit: dict[str, Any] | None,
    candidate_rows: list[list[str]] | None,
    template: dict[str, Any],
    baseline_fields: list[str] | None,
    baseline_structure_rows: list[list[str]] | None,
    reference_rows: list[list[str]] | None,
    reference_fields: list[str] | None,
) -> dict[str, Any] | None:
    if not isinstance(llm_audit, dict) or not llm_audit:
        return llm_audit
    normalized_rows = [list(row) for row in (candidate_rows or []) if isinstance(row, list)]
    if not normalized_rows:
        return llm_audit
    structural_rows = [list(row) for row in (baseline_structure_rows or []) if isinstance(row, list)]
    if not structural_rows:
        return llm_audit
    structural_fields = [str(field).strip() for field in (baseline_fields or []) if str(field).strip()]
    if not structural_fields:
        return llm_audit
    reference_row_values = [list(row) for row in (reference_rows or []) if isinstance(row, list)]
    normalized_reference_fields = [str(field).strip() for field in (reference_fields or []) if str(field).strip()]
    quantity_columns = [col for col in _template_quantity_columns(template) if isinstance(col, dict)]
    if not quantity_columns:
        return llm_audit
    try:
        default_column_index = int(quantity_columns[0].get("index"))
    except Exception:
        default_column_index = 3
    existing_issues = [dict(item) for item in (llm_audit.get("issues") or []) if isinstance(item, dict)]
    existing_keys = {
        (
            _normalize_audit_issue_code(item.get("issue_code")),
            _parse_audit_int(item.get("row_index")),
            _parse_audit_int(item.get("column_index")),
        )
        for item in existing_issues
    }
    block_anchor_hints = _build_reparse_block_anchor_hints(
        structural_fields=structural_fields,
        structural_rows=structural_rows,
        first_pass_fields=normalized_reference_fields or structural_fields,
        first_pass_rows=reference_row_values,
    )
    blank_anchor_row_indexes = block_anchor_hints.get("unmatched_structural_row_indexes")
    if not blank_anchor_row_indexes:
        blank_anchor_row_indexes = block_anchor_hints.get("structural_blank_anchor_row_indexes")
    normalized_blank_anchor_rows = [
        int(item)
        for item in (blank_anchor_row_indexes or [])
        if str(item).strip().lstrip("-").isdigit()
    ]
    quantity_indexes: list[int] = []
    for column in quantity_columns:
        try:
            col_idx = int(column.get("index"))
        except Exception:
            continue
        if col_idx >= 0:
            quantity_indexes.append(col_idx)
    offending_rows: list[int] = []
    for row_index in normalized_blank_anchor_rows:
        if row_index < 0 or row_index >= len(normalized_rows):
            continue
        row = normalized_rows[row_index]
        has_numeric = False
        for col_idx in quantity_indexes:
            if col_idx < len(row) and _parse_strict_numeric_cell(row[col_idx]) is not None:
                has_numeric = True
                break
        if has_numeric:
            offending_rows.append(row_index)
    structural_date_blocks = _summarize_tabular_date_blocks(structural_rows)
    signature_groups: dict[tuple[tuple[str, int], ...], list[dict[str, Any]]] = {}
    for block in structural_date_blocks:
        if not isinstance(block, dict):
            continue
        sub_blocks = block.get("sub_blocks")
        if not isinstance(sub_blocks, list) or not sub_blocks:
            continue
        signature: tuple[tuple[str, int], ...] = tuple(
            (
                str(item.get("daypart") or "").strip(),
                int(item.get("row_count") or 0),
            )
            for item in sub_blocks
            if isinstance(item, dict)
        )
        if not signature:
            continue
        row_start = block.get("row_start")
        row_end = block.get("row_end")
        if not isinstance(row_start, int) or not isinstance(row_end, int) or row_end < row_start:
            continue
        block_rows = normalized_rows[row_start : row_end + 1]
        if not block_rows:
            continue
        leading_blank_count = 0
        any_filled = False
        for row in block_rows:
            has_numeric = False
            for col_idx in quantity_indexes:
                if col_idx < len(row) and _parse_strict_numeric_cell(row[col_idx]) is not None:
                    has_numeric = True
                    any_filled = True
                    break
            if has_numeric:
                break
            leading_blank_count += 1
        if not any_filled:
            continue
        signature_groups.setdefault(signature, []).append(
            {
                "row_start": row_start,
                "row_end": row_end,
                "leading_blank_count": leading_blank_count,
            }
        )
    for group in signature_groups.values():
        if len(group) < 3:
            continue
        counts: dict[int, int] = {}
        for item in group:
            count = int(item.get("leading_blank_count") or 0)
            counts[count] = counts.get(count, 0) + 1
        majority_count = None
        majority_frequency = 0
        for count, frequency in counts.items():
            if frequency > majority_frequency:
                majority_count = count
                majority_frequency = frequency
        if majority_count is None or majority_count <= 0 or majority_frequency < 2:
            continue
        if majority_frequency * 2 <= len(group):
            continue
        for item in group:
            row_start = int(item.get("row_start") or 0)
            leading_blank_count = int(item.get("leading_blank_count") or 0)
            if leading_blank_count >= majority_count:
                continue
            for row_index in range(row_start + leading_blank_count, row_start + majority_count):
                if 0 <= row_index < len(normalized_rows):
                    offending_rows.append(row_index)
    offending_rows = sorted(set(offending_rows))
    if not offending_rows:
        return llm_audit
    augmented_issues = list(existing_issues)
    for row_index in offending_rows[:80]:
        for issue_code, reason in (
            (
                "missing_blank_anchor_rows",
                "Keep this structural row blank unless the fax shows direct row-level evidence.",
            ),
            (
                "unexpected_dense_fill",
                "Do not fill structurally blank rows unless the fax shows explicit row-local evidence.",
            ),
        ):
            key = (issue_code, row_index, default_column_index)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            augmented_issues.append(
                {
                    "issue_code": issue_code,
                    "severity": "high",
                    "row_index": row_index,
                    "column_index": default_column_index,
                    "confidence": 0.99,
                    "evidence": "Structural blank-anchor row was filled by the current OCR candidate.",
                    "reason": reason,
                }
            )
    if len(augmented_issues) == len(existing_issues):
        return llm_audit
    augmented = dict(llm_audit)
    augmented["issues"] = augmented_issues
    augmented["issue_count"] = len(augmented_issues)
    blocking_issues = [dict(item) for item in (augmented.get("blocking_issues") or []) if isinstance(item, dict)]
    blocking_keys = {
        (
            _normalize_audit_issue_code(item.get("issue_code")),
            _parse_audit_int(item.get("row_index")),
            _parse_audit_int(item.get("column_index")),
        )
        for item in blocking_issues
    }
    for item in augmented_issues:
        severity = str(item.get("severity") or "").strip().lower()
        if severity not in {"high", "critical"}:
            continue
        key = (
            _normalize_audit_issue_code(item.get("issue_code")),
            _parse_audit_int(item.get("row_index")),
            _parse_audit_int(item.get("column_index")),
        )
        if key in blocking_keys:
            continue
        blocking_keys.add(key)
        blocking_issues.append(dict(item))
    augmented["blocking_issues"] = blocking_issues
    augmented["blocking_issue_count"] = len(blocking_issues)
    augmented["status"] = "fail"
    return augmented


def _build_reparse_debug_payload(
    *,
    provider: str | None,
    requested_provider: str | None,
    llm_assist: bool,
    rows: list[list[str]],
    lines_count: int,
    before_count: int,
    after_count: int,
    changed: bool | None,
    date_strings: list[str] | None = None,
    extracted: object | None = None,
    parsed_output: dict | None = None,
    error: str | None = None,
    request_prompt: str | None = None,
    normalized_lines: list[dict[str, Any]] | None = None,
    reject_reasons: list[str] | None = None,
    validation_detail: dict[str, Any] | None = None,
    warning_reasons: list[str] | None = None,
    warning_detail: dict[str, Any] | None = None,
    llm_quantity_only_merge: dict[str, Any] | None = None,
    structural_row_projection: dict[str, Any] | None = None,
    llm_cost: dict[str, Any] | None = None,
    llm_audit: dict[str, Any] | None = None,
    pdf_variant_used: str | None = None,
    pdf_variant_fallback_reason: str | None = None,
    quality_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_limit = _debug_text_max_chars()
    payload: dict[str, Any] = {
        "updated_at": datetime.utcnow().isoformat(),
        "provider": provider or None,
        "requested_provider": requested_provider or None,
        "llm_assist": bool(llm_assist),
        "row_count": len(rows or []),
        "line_count": int(lines_count),
        "before_count": int(before_count),
        "after_count": int(after_count),
        "changed": changed if isinstance(changed, bool) else None,
        "error": (error or "").strip() or None,
        "date_strings": [str(item).strip() for item in (date_strings or []) if str(item).strip()][:40],
        "sample_rows": _compact_debug_rows(rows),
        "normalized_lines": _compact_debug_lines(normalized_lines or []),
    }
    prompt_text = _truncate_debug_text(request_prompt, max_chars=raw_limit)
    if prompt_text:
        payload["request_prompt"] = prompt_text
    if isinstance(reject_reasons, list):
        reasons = [str(item).strip() for item in reject_reasons if str(item).strip()]
        if reasons:
            payload["reject_reasons"] = reasons[:20]
    if isinstance(validation_detail, dict) and validation_detail:
        payload["validation_detail"] = validation_detail
    if isinstance(warning_reasons, list):
        reasons = [str(item).strip() for item in warning_reasons if str(item).strip()]
        if reasons:
            payload["warning_reasons"] = reasons[:20]
    if isinstance(warning_detail, dict) and warning_detail:
        payload["warning_detail"] = warning_detail
    if isinstance(llm_quantity_only_merge, dict) and llm_quantity_only_merge:
        payload["llm_quantity_only_merge"] = llm_quantity_only_merge
    if isinstance(structural_row_projection, dict) and structural_row_projection:
        payload["structural_row_projection"] = structural_row_projection
    if isinstance(llm_cost, dict) and llm_cost:
        payload["llm_cost"] = llm_cost
    if isinstance(llm_audit, dict) and llm_audit:
        payload["llm_audit"] = llm_audit
        cluster_fill_decision = _resolve_llm_audit_cluster_fill_decision(llm_audit)
        if cluster_fill_decision:
            payload["order_line_cluster_fill_decision"] = cluster_fill_decision
    if isinstance(pdf_variant_used, str) and pdf_variant_used.strip():
        payload["pdf_variant_used"] = pdf_variant_used.strip()
    if isinstance(pdf_variant_fallback_reason, str) and pdf_variant_fallback_reason.strip():
        payload["pdf_variant_fallback_reason"] = pdf_variant_fallback_reason.strip()
    if isinstance(quality_metadata, dict) and quality_metadata:
        for key, value in quality_metadata.items():
            if value is None:
                continue
            payload[key] = value
    provider_debug = getattr(extracted, "provider_debug", None) if extracted is not None else None
    if isinstance(provider_debug, dict) and provider_debug:
        payload["provider_debug"] = provider_debug

    raw_text: str | None = None
    if extracted is not None:
        raw_text = _truncate_debug_text(getattr(extracted, "raw_text", None), max_chars=raw_limit)
    if raw_text is None and isinstance(parsed_output, dict):
        raw_text = _truncate_debug_text(parsed_output.get("table_raw"), max_chars=raw_limit)
    if raw_text:
        payload["raw_text"] = raw_text
    return payload


def reparse_order(
    order_id: str,
    ocr_prompt: str | None = None,
    prompt_preset: str | None = None,
    ocr_provider: str | None = None,
    ocr_model: str | None = None,
    llm_assist: bool = False,
    auto_fallback_context: dict[str, Any] | None = None,
    evaluator_feedback: dict[str, Any] | None = None,
    feedback_retry_depth: int = 0,
    draft_rows_override: list[list[str]] | None = None,
    draft_rows_label: str | None = None,
):
    config_service.reload_configs()
    before_count = 0
    before_digest = ""
    existing_week_code = None
    existing_lines_updated_at: datetime | None = None
    facility_week_hint: str | None = None
    global_week_hint: str | None = None
    existing_line_anchors: list[dict[str, Any]] = []
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        if not order.facility_code:
            return None, "facility_missing"
        if not order.document_uri:
            return None, "document_missing"
        facility_id = order.facility_code
        document_uri = order.document_uri
        received_at = order.received_at or pd.Timestamp.utcnow()
        message_id = order.message_id
        existing_week_code = order.week_code
        existing_lines_updated_at = order.lines_updated_at
        facility_week_hint = (
            session.execute(
                select(Order.week_code)
                .where(Order.facility_code == facility_id, Order.week_code.is_not(None))
                .order_by(Order.received_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        global_week_hint = (
            session.execute(
                select(Order.week_code)
                .where(Order.week_code.is_not(None))
                .order_by(Order.received_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        existing_lines = session.execute(
            select(OrderLine).where(OrderLine.order_id == order_id)
        ).scalars().all()
        before_count = len(existing_lines)
        before_digest = _line_digest(
            [
                {
                    "date": line.date.isoformat() if line.date else None,
                    "daypart": line.daypart,
                    "menu_name": line.menu_name,
                    "diet_type": line.diet_type,
                    "area_id": line.area_id,
                    "bag_type": line.bag_type,
                    "quantity_original": line.quantity_original,
                    "quantity_corrected": line.quantity_corrected,
                    "change_note": line.change_note,
                }
                for line in existing_lines
            ]
        )
        existing_line_anchors = [
            {
                "date": line.date,
                "daypart": line.daypart,
                "menu_name": line.menu_name,
            }
            for line in existing_lines
        ]
    existing_anchor_dates = _collect_line_dates_for_position_scope(existing_line_anchors)
    stable_existing_anchor_scope = len(existing_anchor_dates) >= 2

    master = config_service.load_facility_master()
    base_template = master.get("fax_template_base", {})
    facility_config = None
    try:
        facility_config = config_service.get_facility_config(facility_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Facility config lookup failed", facility_id=facility_id, error=str(exc))
    if not facility_config:
        facility_config = next(
            (
                fac
                for fac in master.get("facilities", [])
                if fac.get("facility_id") == facility_id
            ),
            None,
        )
    if not facility_config:
        return None, "facility_not_found"

    template = facility_config.get("fax_template") or config_service._merge_template(
        base_template,
        facility_config.get("fax_template_override"),
    )
    template_to_use = dict(template)
    requested_provider = _normalize_reparse_provider(ocr_provider)
    inference_provider = _resolve_explicit_reparse_inference_provider(
        requested_provider=requested_provider,
        llm_assist=bool(llm_assist),
        template=template_to_use,
    )
    if llm_assist or inference_provider in {"openai", "gemini"}:
        # LLM OCR frequently returns merged rows where date/daypart/menu are omitted
        # in quantity-only rows. Enable fill-forward parsing in reparse path.
        template_to_use.setdefault("large_cell_mode", True)
        template_to_use.setdefault("fill_missing_date_with_hint", True)
    if inference_provider:
        template_to_use["main_ocr_provider"] = inference_provider
        template_to_use["_force_main_ocr_provider"] = inference_provider
        if inference_provider == "openai":
            template_to_use["openai_ocr_enabled"] = True
        elif inference_provider == "gemini":
            template_to_use["gemini_ocr_enabled"] = True
    if isinstance(ocr_model, str) and ocr_model.strip():
        if inference_provider == "openai":
            template_to_use["openai_ocr_model"] = ocr_model.strip()
        elif inference_provider == "gemini":
            template_to_use["gemini_ocr_model"] = ocr_model.strip()
    main_provider = str(
        inference_provider
        or os.getenv("OCR_MAIN_PROVIDER")
        or template_to_use.get("main_ocr_provider")
        or "pipeline"
    ).lower()
    auto_fallback_applied = isinstance(auto_fallback_context, dict) and bool(auto_fallback_context)
    auto_fallback_from_provider = (
        str(auto_fallback_context.get("from_provider") or "").strip().lower()
        if auto_fallback_applied
        else None
    )
    auto_fallback_reason = (
        str(auto_fallback_context.get("reason") or "").strip() or None
        if auto_fallback_applied
        else None
    )

    def _current_reparse_quality_metadata() -> dict[str, Any]:
        return _build_reparse_quality_metadata(
            requested_provider=requested_provider,
            effective_provider=main_provider,
            llm_assist=bool(llm_assist),
            auto_fallback_applied=bool(auto_fallback_applied),
            feedback_retry_depth=feedback_retry_depth,
        )

    llm_quantity_only_requested = bool(
        llm_assist
        or inference_provider in {"openai", "gemini"}
        or main_provider in {"openai", "gemini"}
    )
    requires_first_pass_context = bool(
        llm_assist
        or inference_provider in {"openai", "gemini"}
        or (ocr_prompt and main_provider in {"openai", "gemini"})
    )
    if llm_quantity_only_requested:
        template_to_use["llm_quantity_only_mode"] = True
    existing_first_pass_payload = (
        _load_existing_first_pass_payload_for_reparse(order_id)
        if requires_first_pass_context
        else None
    )
    reused_first_pass_payload = isinstance(existing_first_pass_payload, dict)

    pdf_bytes = load_bytes_from_uri(document_uri)
    llm_input_pdf_bytes = pdf_bytes
    llm_input_pdf_meta: dict[str, Any] = {
        "requested": "raw",
        "used": "raw",
        "fallback_reason": None,
    }
    ocr_job_id = f"OCR-{order_id}"
    _, created = create_job(ocr_job_id, input_reference=document_uri)
    if not created:
        job_updates: dict[str, Any] = {
            "status": "running",
            "error_message": None,
            "template_id": None,
            "input_reference": document_uri,
        }
        if not reused_first_pass_payload:
            job_updates["output_reference"] = None
            job_updates["metrics"] = None
        update_job(ocr_job_id, **job_updates)
    preferred_template_id, preferred_template_ids = _resolve_preferred_template_ids(facility_config)
    pipeline_output_ref: str | None = None
    pipeline_output_payload: dict | None = existing_first_pass_payload if reused_first_pass_payload else None
    if reused_first_pass_payload:
        logger.info("Reparse reusing cached first-pass OCR payload order_id={}", order_id)
        _update_reparse_job_progress(
            ocr_job_id,
            status="running",
            processing_stage="first_pass_reused",
            result_state="processing",
            error_message=None,
            metrics_patch={
                "reused_first_pass": True,
                "pipeline_run_skipped": True,
                **_current_reparse_quality_metadata(),
            },
        )
    else:
        _update_reparse_job_progress(
            ocr_job_id,
            status="running",
            processing_stage="ocr_pipeline",
            result_state="processing",
            error_message=None,
        )
        pipeline_output_ref = _run_roi_ocr_pipeline(
            job_id=ocr_job_id,
            pdf_bytes=pdf_bytes,
            facility_id=facility_id,
            input_reference=document_uri,
            preferred_template_id=preferred_template_id,
            preferred_template_ids=preferred_template_ids,
        )
    pipeline_rows_for_rescue: list[list[str]] = []
    pipeline_anchor_dates: set[date] = set()
    position_entries_for_existing_week: list[dict] = []
    expected_weekly_row_count = 0
    llm_reparse_baseline: dict[str, Any] | None = None
    llm_pre_inference_audit: dict[str, Any] | None = None
    resolved_draft_rows_override = [list(row) for row in (draft_rows_override or []) if isinstance(row, list)]
    resolved_draft_rows_label = draft_rows_label
    if existing_week_code:
        if stable_existing_anchor_scope:
            position_entries_for_existing_week = _build_position_entries_for_lines(
                week_id=existing_week_code,
                lines=existing_line_anchors,
                facility_id=facility_id,
            )
        if not position_entries_for_existing_week:
            position_entries_for_existing_week = _build_position_menu_entries_safe(existing_week_code, facility_id)
        expected_weekly_row_count = len(position_entries_for_existing_week)
    if requires_first_pass_context:
        if not isinstance(pipeline_output_payload, dict):
            llm_wait_seconds: float | None = None
            if llm_assist:
                try:
                    llm_wait_seconds = float(os.getenv("OCR_REPARSE_LLM_FIRST_PASS_WAIT_SECONDS", "20"))
                except ValueError:
                    llm_wait_seconds = 20.0
            pipeline_output_payload = _load_pipeline_output_with_retry(
                pipeline_output_ref,
                wait_seconds_override=llm_wait_seconds,
            )
        if not _payload_has_first_pass_ocr_content(pipeline_output_payload):
            rescue_payload, rescue_error = get_ocr_output(order_id, persist_cache=False)
            if rescue_error is None and isinstance(rescue_payload, dict) and _payload_has_first_pass_ocr_content(rescue_payload):
                pipeline_output_payload = rescue_payload
                logger.info(
                    "Reparse using rescued first-pass OCR payload order_id={} source=get_ocr_output",
                    order_id,
                )
        if not _payload_has_first_pass_ocr_content(pipeline_output_payload):
            _update_reparse_job_progress(
                ocr_job_id,
                status="failed",
                processing_stage="first_pass_missing",
                result_state="hard_failed",
                error_message="first_pass_ocr_missing",
                metrics_patch={
                    "error": "first_pass_ocr_missing",
                    "reused_first_pass": bool(reused_first_pass_payload),
                    "pipeline_run_skipped": bool(reused_first_pass_payload),
                    "confirmed_lines_retained": bool(before_count > 0),
                    **_current_reparse_quality_metadata(),
                },
            )
            return None, "first_pass_ocr_missing"
        if isinstance(pipeline_output_payload, dict):
            pipeline_rows_for_rescue = _extract_first_pass_rows_from_payload(
                pipeline_output_payload,
                template_to_use,
            )
            if not pipeline_rows_for_rescue:
                pipeline_rows_for_rescue = _extract_sheet_rows_from_payload(
                    pipeline_output_payload,
                    template_to_use,
                )
            if not pipeline_rows_for_rescue:
                table_raw = pipeline_output_payload.get("table_raw")
                if isinstance(table_raw, str) and table_raw.strip():
                    pipeline_rows_for_rescue = rows_from_markdown(table_raw, template_to_use) or []
            pipeline_anchor_dates = {
                item
                for item in _collect_sheet_dates_from_payload(pipeline_output_payload, received_at)
                if isinstance(item, date)
            }
            if position_entries_for_existing_week and not stable_existing_anchor_scope:
                payload_dates = set(pipeline_anchor_dates)
                if payload_dates:
                    scoped_entries = _filter_position_menu_entries_by_dates(
                        position_entries_for_existing_week,
                        payload_dates,
                        min_anchor_dates=1,
                    )
                    if scoped_entries:
                        expected_weekly_row_count = len(scoped_entries)
            expected_weekly_row_count = _resolve_llm_expected_row_count(
                menu_expected_row_count=expected_weekly_row_count,
                pipeline_rows=pipeline_rows_for_rescue,
                anchor_date_count=len(pipeline_anchor_dates),
            )
        allow_cached_rescue_rows = not (
            llm_assist
            or inference_provider in {"openai", "gemini"}
            or main_provider in {"openai", "gemini"}
        )
        if allow_cached_rescue_rows and not pipeline_rows_for_rescue:
            cached_reference_payload = _load_order_ocr_cache(order_id)
            if isinstance(cached_reference_payload, dict):
                cached_rows = _extract_first_pass_rows_from_payload(
                    cached_reference_payload,
                    template_to_use,
                )
                if not cached_rows:
                    cached_rows = _extract_sheet_rows_from_payload(
                        cached_reference_payload,
                        template_to_use,
                    )
                if cached_rows:
                    pipeline_rows_for_rescue = cached_rows
                    cached_anchor_dates = {
                        item
                        for item in _collect_sheet_dates_from_payload(cached_reference_payload, received_at)
                        if isinstance(item, date)
                    }
                    pipeline_anchor_dates = set(pipeline_anchor_dates) | cached_anchor_dates
                    expected_weekly_row_count = _resolve_llm_expected_row_count(
                        menu_expected_row_count=expected_weekly_row_count,
                        pipeline_rows=pipeline_rows_for_rescue,
                        anchor_date_count=len(pipeline_anchor_dates),
                    )
                    logger.info(
                        "Reparse using cached OCR rows as rescue reference order_id={} rows={}",
                        order_id,
                        len(pipeline_rows_for_rescue),
                    )
        llm_reparse_baseline = _resolve_reparse_llm_baseline(
            order_id=order_id,
            template=template_to_use,
            fallback_payload=pipeline_output_payload,
        )
        if not resolved_draft_rows_override:
            previous_candidate_rows, previous_candidate_label = _load_previous_llm_candidate_rows_for_reparse(
                order_id=order_id,
                payload=(
                    pipeline_output_payload
                    if isinstance(pipeline_output_payload, dict)
                    else None
                ),
            )
            if previous_candidate_rows:
                resolved_draft_rows_override = previous_candidate_rows
                resolved_draft_rows_label = previous_candidate_label or "Previous saved LLM candidate rows"
        llm_pdf_payload = (
            pipeline_output_payload
            if isinstance(pipeline_output_payload, dict)
            else _load_order_ocr_cache(order_id)
        )
        llm_input_pdf_bytes, llm_input_pdf_meta = _resolve_reparse_llm_pdf_bytes(
            document_uri=document_uri,
            payload=llm_pdf_payload if isinstance(llm_pdf_payload, dict) else None,
        )
        effective_provider = inference_provider or main_provider
        if effective_provider in {"openai", "gemini"}:
            _, baseline_reference_rows, _, _ = _resolve_reparse_baseline_rows_for_structure(llm_reparse_baseline)
            audit_reference_rows = baseline_reference_rows or [
                list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)
            ]
            audit_expected_row_count = _resolve_llm_expected_row_count(
                menu_expected_row_count=expected_weekly_row_count,
                pipeline_rows=pipeline_rows_for_rescue,
                observed_rows=baseline_reference_rows,
                anchor_date_count=len(pipeline_anchor_dates),
            )
            if pipeline_rows_for_rescue:
                llm_pre_inference_audit = _run_reparse_with_heartbeat(
                    ocr_job_id,
                    processing_stage="inference",
                    result_state="processing",
                    metrics_patch=_current_reparse_quality_metadata(),
                    func=lambda: _run_llm_reparse_audit(
                        pdf_bytes=llm_input_pdf_bytes,
                        provider=effective_provider,
                        template=template_to_use,
                        facility_id=facility_id,
                        preferred_template_id=preferred_template_id,
                        candidate_rows=[list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)],
                        reference_rows=audit_reference_rows,
                        baseline_rows=baseline_reference_rows,
                        expected_row_count=audit_expected_row_count,
                    ),
                )
            if ocr_prompt and ocr_prompt.strip():
                if effective_provider == "openai":
                    template_to_use["openai_ocr_user_prompt"] = ocr_prompt.strip()
                else:
                    template_to_use["gemini_ocr_user_prompt"] = ocr_prompt.strip()
            assist_prompt = _build_llm_assist_prompt(
                provider=effective_provider,
                template=template_to_use,
                pipeline_output=pipeline_output_payload,
                llm_assist=llm_assist,
                prompt_preset=prompt_preset,
                failure_context=auto_fallback_context,
                baseline=llm_reparse_baseline,
                evaluator_feedback=(
                    evaluator_feedback
                    if isinstance(evaluator_feedback, dict) and evaluator_feedback
                    else llm_pre_inference_audit
                ),
                draft_rows_override=resolved_draft_rows_override,
                draft_rows_label=resolved_draft_rows_label,
                first_pass_rows_override=[list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)],
            )
            if assist_prompt:
                if effective_provider == "openai":
                    template_to_use["openai_ocr_prompt"] = assist_prompt
                    template_to_use["openai_ocr_enabled"] = True
                else:
                    template_to_use["gemini_ocr_prompt"] = assist_prompt
                    template_to_use["gemini_ocr_enabled"] = True
    effective_prompt = _resolve_reparse_prompt_text(
        provider=main_provider,
        template=template_to_use,
        user_prompt=ocr_prompt,
    )
    date_strings = []
    rows = []
    tokens = []
    grid = None
    extracted_data = None
    main_ocr_error: str | None = None
    llm_finish_reason: str | None = None
    llm_truncated_output = False
    llm_rows_replaced_with_pipeline = False
    llm_quantity_only_active = False
    llm_quantity_only_merge_stats: dict[str, int] = {}
    reparse_quality_error: str | None = None
    reparse_quality_detail: dict[str, Any] | None = None
    llm_repair_pass_applied = False
    llm_repair_pass_reason: str | None = None
    llm_repair_pass_error: str | None = None
    llm_primary_model: str | None = None
    llm_repair_pass_model: str | None = None
    llm_feedback_second_pass_applied = False
    llm_feedback_second_pass_error: str | None = None
    reparse_cost_info: dict[str, Any] | None = None
    llm_audit_result: dict[str, Any] | None = None
    llm_second_pass_audit: dict[str, Any] | None = None
    blank_anchor_realign: dict[str, Any] | None = None
    structural_row_projection: dict[str, Any] | None = None
    quantity_sanitize_stats: dict[str, int] | None = None

    try:
        _update_reparse_job_progress(
            ocr_job_id,
            status="running",
            processing_stage="inference",
            result_state="processing",
            metrics_patch=_current_reparse_quality_metadata(),
        )
        extracted = _run_reparse_with_heartbeat(
            ocr_job_id,
            processing_stage="inference",
            result_state="processing",
            metrics_patch=_current_reparse_quality_metadata(),
            func=lambda: extract_fax_data(
                llm_input_pdf_bytes if main_provider in {"openai", "gemini"} else pdf_bytes,
                template_to_use,
                facility_id=facility_id,
                preferred_template_id=preferred_template_id,
            ),
        )
        extracted_data = extracted
        if extracted.ocr_provider:
            main_provider = extracted.ocr_provider
            effective_prompt = _resolve_reparse_prompt_text(
                provider=main_provider,
                template=template_to_use,
                user_prompt=ocr_prompt,
            )
        date_strings = extracted.date_strings or []
        rows = extracted.table_rows or []
        tokens = extracted.tokens or []
        grid = extracted.grid
        provider_debug = extracted.provider_debug if isinstance(extracted.provider_debug, dict) else {}
        llm_primary_model = str(provider_debug.get("model") or "").strip() or None
        rows_for_quantity_quality = [list(row) for row in rows if isinstance(row, list)]
        if not llm_primary_model and main_provider in {"openai", "gemini"}:
            resolved_model = _resolve_provider_model_name(
                provider=main_provider,
                template=template_to_use,
            )
            llm_primary_model = resolved_model or None
        llm_quantity_only_active = bool(provider_debug.get("quantity_only_mode"))
        if not llm_quantity_only_active:
            llm_quantity_only_active = _rows_look_like_quantity_only(
                rows=[list(row) for row in rows if isinstance(row, list)],
                template=template_to_use,
                rows_are_body_only=bool(provider_debug.get("quantity_only_mode")),
            )
        if llm_quantity_only_active:
            provider_debug["quantity_only_mode"] = True
            extracted.provider_debug = provider_debug
        merge_with_pipeline_enabled = _read_reparse_bool_env(
            "OCR_REPARSE_ENABLE_PIPELINE_QUANTITY_MERGE",
            False,
        )
        if llm_quantity_only_active and pipeline_rows_for_rescue and merge_with_pipeline_enabled:
            merged_rows, merge_stats = _merge_llm_quantity_only_rows_with_pipeline(
                llm_rows=[list(row) for row in rows if isinstance(row, list)],
                pipeline_rows=[list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)],
                template=template_to_use,
            )
            if merge_stats.get("quantity_cells_updated", 0) > 0:
                rows = merged_rows
                llm_quantity_only_merge_stats = merge_stats
                provider_debug["quantity_only_merge"] = merge_stats
                extracted.provider_debug = provider_debug
        elif llm_quantity_only_active and pipeline_rows_for_rescue and not merge_with_pipeline_enabled:
            provider_debug["quantity_only_merge_disabled"] = True
            extracted.provider_debug = provider_debug
        if llm_quantity_only_active and pipeline_rows_for_rescue and isinstance(llm_reparse_baseline, dict):
            rows_for_quantity_quality = [list(row) for row in rows if isinstance(row, list)]
            baseline_fields, baseline_structure_rows, _, _ = _resolve_reparse_baseline_rows_for_structure(
                llm_reparse_baseline
            )
            realigned_rows, blank_anchor_realign = _realign_quantity_only_rows_to_structural_blank_anchors(
                rows=[list(row) for row in rows if isinstance(row, list)],
                template=template_to_use,
                structural_fields=baseline_fields,
                structural_rows=baseline_structure_rows,
                reference_rows=[list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)],
                reference_fields=_row_fields_from_template(template_to_use) or baseline_fields,
            )
            if isinstance(blank_anchor_realign, dict) and blank_anchor_realign:
                rows = realigned_rows
                provider_debug["blank_anchor_realign"] = blank_anchor_realign
                extracted.provider_debug = provider_debug
            should_project_rows = _should_project_quantity_rows_to_structural_rows(
                rows=[list(row) for row in rows if isinstance(row, list)],
                structural_rows=baseline_structure_rows,
                template=template_to_use,
            )
            if should_project_rows:
                projected_rows, projected_stats = _project_quantity_only_rows_onto_structural_rows(
                    rows=[list(row) for row in rows if isinstance(row, list)],
                    template=template_to_use,
                    structural_fields=baseline_fields,
                    structural_rows=baseline_structure_rows,
                )
                if isinstance(projected_stats, dict) and projected_stats:
                    rows = projected_rows
                    structural_row_projection = projected_stats
                    provider_debug["structural_row_projection"] = projected_stats
                    extracted.provider_debug = provider_debug
        llm_finish_reason = _extract_llm_finish_reason(extracted)
        llm_truncated_output = (
            main_provider in {"openai", "gemini"} and _is_truncated_llm_output(extracted)
        )
        if llm_truncated_output:
            reason = llm_finish_reason or "truncated_output"
            main_ocr_error = f"{main_provider}_output_truncated:{reason}"
            if pipeline_rows_for_rescue:
                logger.warning(
                    "Discarding truncated LLM OCR rows and using pipeline rows provider={} reason={} llm_rows={} pipeline_rows={}",
                    main_provider,
                    reason,
                    len(rows),
                    len(pipeline_rows_for_rescue),
                )
                rows = pipeline_rows_for_rescue
                tokens = []
                llm_rows_replaced_with_pipeline = True
            else:
                rows = []
                tokens = []
        if (
            main_provider in {"openai", "gemini"}
            and not llm_rows_replaced_with_pipeline
            and expected_weekly_row_count > 0
            and len(rows) < expected_weekly_row_count
            and len(pipeline_rows_for_rescue) >= expected_weekly_row_count
        ):
            main_ocr_error = (
                f"{main_provider}_row_shortfall:{len(rows)}/{expected_weekly_row_count}"
            )
            logger.warning(
                "LLM OCR row count shortfall; using pipeline rows provider={} llm_rows={} expected_rows={} pipeline_rows={}",
                main_provider,
                len(rows),
                expected_weekly_row_count,
                len(pipeline_rows_for_rescue),
            )
            rows = pipeline_rows_for_rescue
            tokens = []
            llm_rows_replaced_with_pipeline = True

        if (
            main_provider in {"openai", "gemini"}
            and llm_quantity_only_active
            and not llm_rows_replaced_with_pipeline
        ):
            expected_weekly_row_count = _resolve_llm_expected_row_count(
                menu_expected_row_count=expected_weekly_row_count,
                pipeline_rows=pipeline_rows_for_rescue,
                anchor_date_count=len(pipeline_anchor_dates),
            )
            quality_error, quality_detail = _evaluate_quantity_only_rows_quality(
                rows=rows_for_quantity_quality,
                template=template_to_use,
                expected_row_count=expected_weekly_row_count,
                reference_rows=pipeline_rows_for_rescue,
            )
            reparse_quality_error = quality_error
            reparse_quality_detail = quality_detail

            repair_enabled = str(
                os.getenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "1")
            ).strip().lower() not in {"0", "false", "no", "off"}
            if repair_enabled and reparse_quality_error in {"sheet_row_coverage_low", "sheet_row_overfill", "sheet_column_anomaly"}:
                repair_template = dict(template_to_use)
                target_repair_model = _resolve_provider_model_name(
                    provider=main_provider,
                    template=repair_template,
                )
                use_pro_repair, pro_repair_model = _should_use_gemini_pro_repair_pass(
                    provider=main_provider,
                    template=repair_template,
                    quality_error=reparse_quality_error,
                )
                if use_pro_repair:
                    repair_template["gemini_ocr_model"] = pro_repair_model
                    target_repair_model = pro_repair_model
                llm_repair_pass_model = str(target_repair_model or "").strip() or None
                baseline_fields, baseline_structure_rows, _, _ = _resolve_reparse_baseline_rows_for_structure(
                    llm_reparse_baseline
                )
                repair_system_prompt, repair_user_prompt = _build_quantity_only_repair_prompts(
                    provider=main_provider,
                    template=repair_template,
                    current_rows=[list(row) for row in rows if isinstance(row, list)],
                    baseline_rows=baseline_structure_rows,
                    baseline_fields=baseline_fields,
                    structural_rows=baseline_structure_rows,
                    structural_fields=baseline_fields,
                    expected_row_count=expected_weekly_row_count,
                    quality_error=reparse_quality_error,
                    quality_detail=reparse_quality_detail,
                    first_pass_model=llm_primary_model,
                    target_model=llm_repair_pass_model,
                )
                if main_provider == "openai":
                    repair_template["openai_ocr_prompt"] = repair_system_prompt
                    repair_template["openai_ocr_user_prompt"] = repair_user_prompt
                    repair_template["openai_ocr_enabled"] = True
                else:
                    repair_template["gemini_ocr_prompt"] = repair_system_prompt
                    repair_template["gemini_ocr_user_prompt"] = repair_user_prompt
                    repair_template["gemini_ocr_enabled"] = True
                try:
                    repaired_extracted = _run_reparse_with_heartbeat(
                        ocr_job_id,
                        processing_stage="inference",
                        result_state="processing",
                        metrics_patch=_current_reparse_quality_metadata(),
                        func=lambda: extract_fax_data(
                            llm_input_pdf_bytes,
                            repair_template,
                            facility_id=facility_id,
                            preferred_template_id=preferred_template_id,
                        ),
                    )
                    repaired_rows = repaired_extracted.table_rows or []
                    repaired_tokens = repaired_extracted.tokens or []
                    repaired_date_strings = repaired_extracted.date_strings or date_strings
                    repaired_grid = repaired_extracted.grid or grid
                    repaired_rows_for_quality = [
                        list(row) for row in repaired_rows if isinstance(row, list)
                    ]
                    repaired_quality_error, repaired_quality_detail = _evaluate_quantity_only_rows_quality(
                        rows=repaired_rows_for_quality,
                        template=repair_template,
                        expected_row_count=expected_weekly_row_count,
                        reference_rows=pipeline_rows_for_rescue,
                    )
                    use_repaired = _is_reparse_quality_improved(
                        before_error=reparse_quality_error,
                        before_detail=reparse_quality_detail,
                        after_error=repaired_quality_error,
                        after_detail=repaired_quality_detail,
                    )
                    if use_repaired:
                        rows = [list(row) for row in repaired_rows if isinstance(row, list)]
                        rows_for_quantity_quality = repaired_rows_for_quality
                        tokens = repaired_tokens
                        date_strings = repaired_date_strings
                        grid = repaired_grid
                        extracted_data = repaired_extracted
                        reparse_quality_error = repaired_quality_error
                        reparse_quality_detail = repaired_quality_detail
                        llm_finish_reason = _extract_llm_finish_reason(repaired_extracted)
                        llm_truncated_output = _is_truncated_llm_output(repaired_extracted)
                        llm_repair_pass_applied = True
                        llm_repair_pass_reason = quality_error
                        template_to_use = repair_template
                        repaired_debug = (
                            repaired_extracted.provider_debug
                            if isinstance(repaired_extracted.provider_debug, dict)
                            else {}
                        )
                        repaired_model = str(repaired_debug.get("model") or "").strip()
                        if repaired_model:
                            llm_repair_pass_model = repaired_model
                        effective_prompt = _resolve_reparse_prompt_text(
                            provider=main_provider,
                            template=repair_template,
                            user_prompt=ocr_prompt,
                        )
                except Exception as exc:  # noqa: BLE001
                    llm_repair_pass_error = str(exc)
                    logger.warning(
                        "Reparse repair pass failed provider={} error={}",
                        main_provider,
                        llm_repair_pass_error,
                    )
        if (
            main_provider in {"openai", "gemini"}
            and rows
            and not llm_rows_replaced_with_pipeline
        ):
            _, baseline_reference_rows, _, _ = _resolve_reparse_baseline_rows_for_structure(llm_reparse_baseline)
            audit_reference_rows = baseline_reference_rows or [
                list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)
            ]
            audit_expected_row_count = _resolve_llm_expected_row_count(
                menu_expected_row_count=expected_weekly_row_count,
                pipeline_rows=pipeline_rows_for_rescue,
                observed_rows=[list(row) for row in rows if isinstance(row, list)],
                anchor_date_count=len(pipeline_anchor_dates),
            )
            llm_second_pass_audit = _run_reparse_with_heartbeat(
                ocr_job_id,
                processing_stage="validation",
                result_state="processing",
                metrics_patch=_current_reparse_quality_metadata(),
                func=lambda: _run_llm_reparse_audit(
                    pdf_bytes=llm_input_pdf_bytes,
                    provider=main_provider,
                    template=template_to_use,
                    facility_id=facility_id,
                    preferred_template_id=preferred_template_id,
                    candidate_rows=[list(row) for row in rows if isinstance(row, list)],
                    reference_rows=audit_reference_rows,
                    baseline_rows=baseline_reference_rows,
                    expected_row_count=audit_expected_row_count,
                ),
            )
            llm_second_pass_audit = _augment_llm_reparse_audit_with_structural_feedback(
                llm_audit=llm_second_pass_audit,
                candidate_rows=[list(row) for row in rows if isinstance(row, list)],
                template=template_to_use,
                baseline_fields=_resolve_reparse_baseline_rows_for_structure(llm_reparse_baseline)[0],
                baseline_structure_rows=baseline_reference_rows,
                reference_rows=[list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)],
                reference_fields=_row_fields_from_template(template_to_use) or _resolve_reparse_baseline_rows_for_structure(llm_reparse_baseline)[0],
            )
            if _llm_reparse_audit_requires_second_pass(llm_second_pass_audit):
                second_pass_template = dict(template_to_use)
                baseline_fields, baseline_structure_rows, _, _ = _resolve_reparse_baseline_rows_for_structure(
                    llm_reparse_baseline
                )
                use_structural_repair_prompt = (
                    llm_quantity_only_active
                    and _llm_reparse_audit_requires_structural_repair_prompt(llm_second_pass_audit)
                )
                if use_structural_repair_prompt:
                    second_pass_model = _resolve_provider_model_name(
                        provider=main_provider,
                        template=second_pass_template,
                    )
                    repair_system_prompt, repair_user_prompt = _build_quantity_only_repair_prompts(
                        provider=main_provider,
                        template=second_pass_template,
                        current_rows=[list(row) for row in rows if isinstance(row, list)],
                        baseline_rows=baseline_structure_rows,
                        baseline_fields=baseline_fields,
                        structural_rows=baseline_structure_rows,
                        structural_fields=baseline_fields,
                        expected_row_count=expected_weekly_row_count,
                        quality_error="sheet_structural_drift",
                        quality_detail={"evaluator_feedback": llm_second_pass_audit or {}},
                        first_pass_model=llm_primary_model,
                        target_model=second_pass_model,
                        evaluator_feedback=llm_second_pass_audit,
                    )
                    if main_provider == "openai":
                        second_pass_template["openai_ocr_prompt"] = repair_system_prompt
                        second_pass_template["openai_ocr_user_prompt"] = repair_user_prompt
                        second_pass_template["openai_ocr_enabled"] = True
                    else:
                        second_pass_template["gemini_ocr_prompt"] = repair_system_prompt
                        second_pass_template["gemini_ocr_user_prompt"] = repair_user_prompt
                        second_pass_template["gemini_ocr_enabled"] = True
                else:
                    second_pass_prompt = _build_llm_assist_prompt(
                        provider=main_provider,
                        template=second_pass_template,
                        pipeline_output=pipeline_output_payload,
                        llm_assist=True,
                        prompt_preset=prompt_preset,
                        failure_context=auto_fallback_context,
                        baseline=llm_reparse_baseline,
                        evaluator_feedback=llm_second_pass_audit,
                        draft_rows_override=[list(row) for row in rows if isinstance(row, list)],
                        draft_rows_label="First LLM inference candidate rows",
                        first_pass_rows_override=[list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)],
                    )
                    if second_pass_prompt:
                        if main_provider == "openai":
                            second_pass_template["openai_ocr_prompt"] = second_pass_prompt
                            second_pass_template["openai_ocr_enabled"] = True
                            if ocr_prompt and ocr_prompt.strip():
                                second_pass_template["openai_ocr_user_prompt"] = ocr_prompt.strip()
                        else:
                            second_pass_template["gemini_ocr_prompt"] = second_pass_prompt
                            second_pass_template["gemini_ocr_enabled"] = True
                            if ocr_prompt and ocr_prompt.strip():
                                second_pass_template["gemini_ocr_user_prompt"] = ocr_prompt.strip()
                try:
                    second_extracted = _run_reparse_with_heartbeat(
                        ocr_job_id,
                        processing_stage="inference",
                        result_state="processing",
                        metrics_patch=_current_reparse_quality_metadata(),
                        func=lambda: extract_fax_data(
                            llm_input_pdf_bytes,
                            second_pass_template,
                            facility_id=facility_id,
                            preferred_template_id=preferred_template_id,
                        ),
                    )
                    second_rows = [list(row) for row in (second_extracted.table_rows or []) if isinstance(row, list)]
                    if second_rows:
                        rows = second_rows
                        tokens = second_extracted.tokens or []
                        date_strings = second_extracted.date_strings or date_strings
                        grid = second_extracted.grid or grid
                        extracted_data = second_extracted
                        llm_finish_reason = _extract_llm_finish_reason(second_extracted)
                        llm_truncated_output = _is_truncated_llm_output(second_extracted)
                        llm_feedback_second_pass_applied = True
                        second_debug = (
                            second_extracted.provider_debug
                            if isinstance(second_extracted.provider_debug, dict)
                            else {}
                        )
                        second_model = str(second_debug.get("model") or "").strip()
                        if second_model:
                            llm_repair_pass_model = second_model
                        template_to_use = second_pass_template
                        effective_prompt = _resolve_reparse_prompt_text(
                            provider=main_provider,
                            template=second_pass_template,
                            user_prompt=ocr_prompt,
                        )
                except Exception as exc:  # noqa: BLE001
                    llm_feedback_second_pass_error = str(exc)
                    logger.warning(
                        "Reparse evaluator-guided second pass failed provider={} error={}",
                        main_provider,
                        llm_feedback_second_pass_error,
                    )
        if main_provider in {"openai", "gemini"}:
            final_debug = (
                extracted_data.provider_debug
                if extracted_data is not None and isinstance(extracted_data.provider_debug, dict)
                else {}
            )
            final_model = llm_repair_pass_model or llm_primary_model
            if not final_model and isinstance(final_debug, dict):
                final_model = str(final_debug.get("model") or "").strip() or None
            reparse_cost_info = _estimate_reparse_llm_cost(
                provider=main_provider,
                model=final_model,
                provider_debug=final_debug,
            )
            if reparse_cost_info:
                if isinstance(final_debug, dict):
                    final_debug = dict(final_debug)
                    final_debug["cost_estimate"] = reparse_cost_info
                    if extracted_data is not None:
                        extracted_data.provider_debug = final_debug
                if reparse_cost_info.get("over_soft_limit"):
                    logger.warning(
                        "Reparse LLM estimated cost over soft limit",
                        order_id=order_id,
                        provider=main_provider,
                        model=final_model,
                        estimated_cost_usd=reparse_cost_info.get("estimated_cost_usd"),
                        soft_limit_usd=reparse_cost_info.get("soft_limit_usd"),
                    )
        sample_row_text = ""
        if rows:
            try:
                sample_row_text = str(rows[0])[:320]
            except Exception:
                sample_row_text = ""
        logger.info(
            "Reparse extracted OCR rows provider={} rows={} dates={} tokens={} sample_row={}",
            main_provider,
            len(rows),
            len(date_strings),
            len(tokens),
            sample_row_text,
        )
        _maybe_dump_reparse_debug(order_id, message_id, extracted_data, tokens)
    except Exception as exc:  # noqa: BLE001
        main_ocr_error = str(exc)
        logger.warning("Main OCR reparse failed (provider={}): {}", main_provider, str(exc))
        cached = _load_cached_ocr(message_id)
        if not cached:
            _update_reparse_job_progress(
                ocr_job_id,
                status="failed",
                processing_stage="inference",
                result_state="hard_failed",
                error_message=f"main_ocr_failed:{main_provider}",
                metrics_patch={
                    **_current_reparse_quality_metadata(),
                    "error": f"main_ocr_failed:{main_provider}",
                    "confirmed_lines_retained": bool(before_count > 0),
                },
            )
            return None, f"main_ocr_failed:{main_provider}"
        date_strings = cached.get("date_strings") or []
        rows = cached.get("table_rows") or []
        tokens = cached.get("tokens") or []
        tokens = filter_tokens_by_box(tokens, template_to_use.get("table_box"))
        grid = detect_table_grid(
            llm_input_pdf_bytes if main_provider in {"openai", "gemini"} else pdf_bytes,
            template_to_use,
        )
        _maybe_dump_reparse_debug(order_id, message_id, None, tokens, grid, error=str(exc))

    default_date = None
    if date_strings:
        parsed_dates = []
        for raw in date_strings:
            parsed = parse_date_string(raw, received_at)
            if parsed:
                parsed_dates.append(parsed)
        if parsed_dates:
            default_date = min(parsed_dates)
    policy = config_service.load_ingest_policy()
    strict_llm_quantity = bool(
        llm_assist
        or inference_provider in {"openai", "gemini"}
        or main_provider in {"openai", "gemini"}
    )
    reparse_quantity_rules = _build_reparse_quantity_rules(
        policy.get("quantity_rules", {}),
        strict_llm_quantity=strict_llm_quantity,
    )
    if (
        main_provider in {"openai", "gemini"}
        and llm_quantity_only_active
        and rows
        and pipeline_rows_for_rescue
        and isinstance(llm_reparse_baseline, dict)
    ):
        baseline_fields, baseline_structure_rows, _, _ = _resolve_reparse_baseline_rows_for_structure(
            llm_reparse_baseline
        )
        final_realigned_rows, final_blank_anchor_realign = _realign_quantity_only_rows_to_structural_blank_anchors(
            rows=[list(row) for row in rows if isinstance(row, list)],
            template=template_to_use,
            structural_fields=baseline_fields,
            structural_rows=baseline_structure_rows,
            reference_rows=[list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)],
            reference_fields=_row_fields_from_template(template_to_use) or baseline_fields,
        )
        if isinstance(final_blank_anchor_realign, dict) and final_blank_anchor_realign:
            rows = final_realigned_rows
            blank_anchor_realign = final_blank_anchor_realign
            if extracted_data is not None:
                extracted_data.table_rows = [list(row) for row in final_realigned_rows]
                provider_debug = (
                    extracted_data.provider_debug if isinstance(extracted_data.provider_debug, dict) else {}
                )
                provider_debug = dict(provider_debug)
                provider_debug["blank_anchor_realign"] = final_blank_anchor_realign
                extracted_data.provider_debug = provider_debug
        should_project_rows = _should_project_quantity_rows_to_structural_rows(
            rows=[list(row) for row in rows if isinstance(row, list)],
            structural_rows=baseline_structure_rows,
            template=template_to_use,
        )
        if should_project_rows:
            projected_rows, projected_stats = _project_quantity_only_rows_onto_structural_rows(
                rows=[list(row) for row in rows if isinstance(row, list)],
                template=template_to_use,
                structural_fields=baseline_fields,
                structural_rows=baseline_structure_rows,
            )
            if isinstance(projected_stats, dict) and projected_stats:
                rows = projected_rows
                structural_row_projection = projected_stats
                if extracted_data is not None:
                    extracted_data.table_rows = [list(row) for row in projected_rows]
                    provider_debug = (
                        extracted_data.provider_debug if isinstance(extracted_data.provider_debug, dict) else {}
                    )
                    provider_debug = dict(provider_debug)
                    provider_debug["structural_row_projection"] = projected_stats
                    extracted_data.provider_debug = provider_debug
    lines = parse_order_lines(
        rows,
        template_to_use,
        received_at,
        reparse_quantity_rules,
        default_date=default_date,
        tokens=tokens,
        grid=grid.__dict__ if grid else None,
        pdf_bytes=llm_input_pdf_bytes if main_provider in {"openai", "gemini"} else pdf_bytes,
    )
    parsed_output_for_debug = pipeline_output_payload if isinstance(pipeline_output_payload, dict) else None
    if not lines:
        try:
            parsed_output = pipeline_output_payload if isinstance(pipeline_output_payload, dict) else None
            output_ref = pipeline_output_ref
            if not isinstance(parsed_output, dict):
                if not output_ref:
                    job = get_ocr_job(ocr_job_id)
                    if not job and message_id:
                        job = get_ocr_job(f"OCR-{message_id}")
                    output_ref = job.get("output_reference") if job else None
                parsed_output = _load_pipeline_output_with_retry(output_ref)
            if not isinstance(parsed_output, dict):
                cached_payload, _ = get_ocr_output(order_id)
                if isinstance(cached_payload, dict):
                    parsed_output = cached_payload
            if parsed_output:
                parsed_output_for_debug = parsed_output
                fallback_default_date = default_date
                if fallback_default_date is None:
                    date_candidates = _collect_sheet_dates_from_payload(parsed_output, received_at)
                    if date_candidates:
                        fallback_default_date = min(date_candidates)
                table_raw = parsed_output.get("table_raw")
                if isinstance(table_raw, str) and table_raw.strip():
                    fallback_rows = rows_from_markdown(table_raw, template_to_use) or []
                    if fallback_rows:
                        lines = parse_order_lines(
                            fallback_rows,
                            template_to_use,
                            received_at,
                            reparse_quantity_rules,
                            default_date=fallback_default_date,
                            tokens=[],
                            grid=None,
                            pdf_bytes=llm_input_pdf_bytes if main_provider in {"openai", "gemini"} else pdf_bytes,
                        )
                    logger.info(
                        "Reparse fallback markdown rows={} parsed_lines={}",
                        len(fallback_rows),
                        len(lines),
                    )
                if not lines:
                    lines = _build_sheet_lines_from_ocr_payload(
                        payload=parsed_output,
                        template=template_to_use,
                        received_at=received_at,
                        week_id=existing_week_code,
                        facility_id=facility_id,
                        quantity_rules=reparse_quantity_rules,
                    )
                    logger.info("Reparse fallback sheet lines={}", len(lines))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallback OCR markdown parse failed", error=str(exc))
    if (
        not lines
        and not llm_assist
        and main_provider not in {"openai", "gemini"}
        and not auto_fallback_applied
    ):
        auto_provider = _resolve_auto_llm_fallback_provider(template=template_to_use)
        if auto_provider:
            failure_reason = "lines_empty"
            if isinstance(main_ocr_error, str) and main_ocr_error.strip():
                failure_reason = main_ocr_error.strip()
            fallback_context = {
                "trigger": "yomitoku_failed",
                "from_provider": main_provider,
                "reason": failure_reason,
                "row_count": len(rows or []),
                "line_count": 0,
                "date_strings": [str(item).strip() for item in (date_strings or []) if str(item).strip()][:20],
            }
            sample_row = rows[0] if rows else None
            if sample_row is not None:
                try:
                    fallback_context["sample_row"] = str(sample_row)[:320]
                except Exception:
                    pass
            logger.info(
                "Auto triggering LLM fallback after yomitoku failure order_id={} from_provider={} to_provider={} reason={}",
                order_id,
                main_provider,
                auto_provider,
                failure_reason,
            )
            return reparse_order(
                order_id,
                ocr_prompt=ocr_prompt,
                ocr_provider=auto_provider,
                llm_assist=True,
                auto_fallback_context=fallback_context,
            )
    if not lines:
        sample_row = rows[0] if rows else None
        effective_provider = main_provider or "unknown"
        requested_provider_label = requested_provider or ""
        lines_empty_error = "lines_empty"
        if requested_provider_label and effective_provider != requested_provider_label:
            lines_empty_error = (
                f"lines_empty:provider_fallback:{requested_provider_label}->{effective_provider}"
            )
        sample_row_text = ""
        if sample_row is not None:
            try:
                sample_row_text = str(sample_row)[:320]
            except Exception:
                sample_row_text = ""
        logger.warning(
            "No lines extracted provider={} requested={} row_count={} sample_row={}",
            main_provider,
            requested_provider,
            len(rows),
            sample_row_text,
        )
        debug_payload = _build_reparse_debug_payload(
            provider=effective_provider,
            requested_provider=requested_provider_label or None,
            llm_assist=bool(llm_assist),
            rows=rows,
            lines_count=0,
            before_count=before_count,
            after_count=0,
            changed=False,
            date_strings=date_strings,
            extracted=extracted_data,
            parsed_output=parsed_output_for_debug,
            error=main_ocr_error,
            request_prompt=effective_prompt,
            normalized_lines=lines,
            reject_reasons=[lines_empty_error],
            llm_quantity_only_merge=llm_quantity_only_merge_stats or None,
            structural_row_projection=structural_row_projection,
            llm_cost=reparse_cost_info,
            llm_audit=llm_audit_result,
            pdf_variant_used=str(llm_input_pdf_meta.get("used") or "raw"),
            pdf_variant_fallback_reason=(
                str(llm_input_pdf_meta.get("fallback_reason") or "").strip() or None
            ),
            quality_metadata=_current_reparse_quality_metadata(),
        )
        if auto_fallback_applied:
            debug_payload["auto_fallback"] = dict(auto_fallback_context or {})
        if isinstance(llm_pre_inference_audit, dict) and llm_pre_inference_audit:
            debug_payload["llm_pre_inference_audit"] = llm_pre_inference_audit
        if isinstance(llm_second_pass_audit, dict) and llm_second_pass_audit:
            debug_payload["llm_second_pass_audit"] = llm_second_pass_audit
        if llm_feedback_second_pass_applied:
            debug_payload["llm_feedback_second_pass_applied"] = True
        if llm_feedback_second_pass_error:
            debug_payload["llm_feedback_second_pass_error"] = llm_feedback_second_pass_error
        if isinstance(blank_anchor_realign, dict) and blank_anchor_realign:
            debug_payload["blank_anchor_realign"] = blank_anchor_realign
        try:
            context_conflict = _order_context_conflict_detail(
                order_id=order_id,
                expected_facility_code=facility_id,
                expected_week_code=existing_week_code,
                expected_document_uri=document_uri,
                expected_lines_updated_at=existing_lines_updated_at,
            )
            if context_conflict is not None:
                _update_reparse_job_progress(
                    ocr_job_id,
                    status="failed",
                    processing_stage="stale_context",
                    result_state="hard_failed",
                    error_message="stale_order_context",
                    metrics_patch={
                        "error": "stale_order_context",
                        "context_conflict": context_conflict,
                        "confirmed_lines_retained": bool(before_count > 0),
                        **_current_reparse_quality_metadata(),
                    },
                )
                return None, "stale_order_context"
            cache_payload: dict[str, Any] = {}
            if isinstance(parsed_output_for_debug, dict):
                cache_payload = dict(parsed_output_for_debug)
            else:
                existing_payload = _load_order_ocr_cache(order_id)
                if isinstance(existing_payload, dict):
                    cache_payload = dict(existing_payload)
            cache_payload["_reparse_debug"] = debug_payload
            _save_order_ocr_cache(order_id, cache_payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to save OCR debug cache on lines_empty", error=str(exc))
        update_job(
            ocr_job_id,
            status="empty",
            error_message=lines_empty_error,
            metrics={
                "provider": effective_provider,
                "requested_provider": requested_provider_label or None,
                "row_count": len(rows),
                "line_count": 0,
                "llm_assist": bool(llm_assist),
                "finish_reason": llm_finish_reason,
                "truncated_output": bool(llm_truncated_output),
                "rows_replaced_with_pipeline": bool(llm_rows_replaced_with_pipeline),
                "llm_quantity_only_merge": llm_quantity_only_merge_stats or None,
                "structural_row_projection": structural_row_projection or None,
                "primary_model": llm_primary_model,
                "repair_pass_applied": bool(llm_repair_pass_applied),
                "repair_pass_reason": llm_repair_pass_reason,
                "repair_pass_error": llm_repair_pass_error,
                "repair_pass_model": llm_repair_pass_model,
                "quality_error": reparse_quality_error,
                "quality_detail": reparse_quality_detail or {},
                "llm_cost": reparse_cost_info or None,
                "llm_audit": llm_audit_result or None,
                "llm_pre_inference_audit": llm_pre_inference_audit or None,
                "llm_second_pass_audit": llm_second_pass_audit or None,
                "reused_first_pass": bool(reused_first_pass_payload),
                "pipeline_run_skipped": bool(reused_first_pass_payload),
                "llm_feedback_second_pass_applied": bool(llm_feedback_second_pass_applied),
                "llm_feedback_second_pass_error": llm_feedback_second_pass_error,
                "blank_anchor_realign": blank_anchor_realign or None,
                "auto_fallback_applied": bool(auto_fallback_applied),
                "auto_fallback_from_provider": auto_fallback_from_provider,
                "auto_fallback_reason": auto_fallback_reason,
                "pdf_variant_used": str(llm_input_pdf_meta.get("used") or "raw"),
                "pdf_variant_fallback_reason": (
                    str(llm_input_pdf_meta.get("fallback_reason") or "").strip() or None
                ),
                **_current_reparse_quality_metadata(),
            },
        )
        return None, "lines_empty"
    min_ratio = float(policy.get("menu_match_min_ratio", 0.72))
    week_resolution_lines: list[dict[str, Any]] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        normalized = dict(line)
        normalized["date"] = _parse_date_value(normalized.get("date"))
        week_resolution_lines.append(normalized)
    line_dates = [
        line.get("date")
        for line in week_resolution_lines
        if isinstance(line.get("date"), date)
    ]
    derived_week_id = month_id_from_dates(line_dates, received_at, policy) if line_dates else None
    week_payload = parsed_output_for_debug if isinstance(parsed_output_for_debug, dict) else None
    if not isinstance(week_payload, dict):
        cached_payload, _ = get_ocr_output(order_id, persist_cache=False)
        if isinstance(cached_payload, dict):
            week_payload = cached_payload
    week_id = _resolve_sheet_week_id(
        current_week_id=existing_week_code,
        received_at=received_at,
        order_lines=week_resolution_lines,
        ocr_payload=week_payload,
        facility_id=facility_id,
        week_hints=[hint for hint in [facility_week_hint, global_week_hint] if hint],
    )
    if not week_id:
        week_id = _prefer_existing_week_when_derived_missing_menu(
            derived_week_id=derived_week_id,
            existing_week_code=existing_week_code,
            facility_id=facility_id,
        )
    pipeline_payload_dates: set[date] = set()
    if isinstance(pipeline_output_payload, dict):
        pipeline_payload_dates = {
            item
            for item in _collect_sheet_dates_from_payload(pipeline_output_payload, received_at)
            if isinstance(item, date)
        }
    reparse_position_entries = _build_reparse_position_menu_entries(
        week_id=week_id,
        facility_id=facility_id,
        lines=week_resolution_lines,
        rows=[list(row) for row in rows if isinstance(row, list)],
        parsed_output=week_payload,
        existing_lines=existing_line_anchors,
        extra_payload_dates=pipeline_payload_dates,
        received_at=received_at,
    )
    if "rows_for_quantity_quality" not in locals():
        rows_for_quantity_quality = [list(row) for row in rows if isinstance(row, list)]
    if (
        main_provider in {"openai", "gemini"}
        and llm_quantity_only_active
        and not llm_rows_replaced_with_pipeline
    ):
        week_menu_row_count = len(_build_position_menu_entries_safe(week_id, facility_id)) if week_id else 0
        quality_anchor_dates = {
            item
            for item in _collect_line_dates_for_position_scope(week_resolution_lines)
            if isinstance(item, date)
        } | pipeline_payload_dates
        quality_expected_row_count = _resolve_llm_expected_row_count(
            menu_expected_row_count=len(reparse_position_entries),
            fallback_expected_row_count=expected_weekly_row_count or week_menu_row_count,
            pipeline_rows=pipeline_rows_for_rescue,
            observed_rows=rows_for_quantity_quality,
            anchor_date_count=len(quality_anchor_dates),
        )
        if quality_expected_row_count > 0:
            reparse_quality_error, reparse_quality_detail = _evaluate_quantity_only_rows_quality(
                rows=rows_for_quantity_quality,
                template=template_to_use,
                expected_row_count=quality_expected_row_count,
                reference_rows=pipeline_rows_for_rescue,
            )
            llm_audit_result = _run_reparse_with_heartbeat(
                ocr_job_id,
                processing_stage="validation",
                result_state="processing",
                metrics_patch=_current_reparse_quality_metadata(),
                func=lambda: _run_llm_reparse_audit(
                    pdf_bytes=llm_input_pdf_bytes,
                    provider=main_provider,
                    template=template_to_use,
                    facility_id=facility_id,
                    preferred_template_id=preferred_template_id,
                    candidate_rows=[list(row) for row in rows if isinstance(row, list)],
                    reference_rows=[list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)],
                    baseline_rows=_resolve_reparse_baseline_rows_for_structure(llm_reparse_baseline)[1],
                    expected_row_count=quality_expected_row_count,
                ),
            )
            llm_audit_result = _augment_llm_reparse_audit_with_structural_feedback(
                llm_audit=llm_audit_result,
                candidate_rows=[list(row) for row in rows if isinstance(row, list)],
                template=template_to_use,
                baseline_fields=_resolve_reparse_baseline_rows_for_structure(llm_reparse_baseline)[0],
                baseline_structure_rows=_resolve_reparse_baseline_rows_for_structure(llm_reparse_baseline)[1],
                reference_rows=[list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)],
                reference_fields=_row_fields_from_template(template_to_use) or _resolve_reparse_baseline_rows_for_structure(llm_reparse_baseline)[0],
            )
    enable_position_mapping = bool(template_to_use.get("map_menu_by_position", True))
    mapped_rows = 0
    if enable_position_mapping:
        lines, mapped_rows = _apply_menu_position_mapping_safe(
            lines,
            week_id,
            facility_id=facility_id,
            entries_override=reparse_position_entries if reparse_position_entries else None,
        )
    if mapped_rows <= 0:
        lines = _apply_menu_matching(lines, week_id, facility_id, min_ratio)
    lines, quantity_sanitize_stats = _sanitize_reparse_line_quantities(lines)
    if quantity_sanitize_stats and (
        quantity_sanitize_stats.get("quantity_adjusted", 0) > 0
        or quantity_sanitize_stats.get("quantity_dropped", 0) > 0
        or quantity_sanitize_stats.get("lines_dropped", 0) > 0
    ):
        logger.info(
            "Reparse quantity sanitize applied order_id={} adjusted={} dropped={} lines_dropped={} max_abs={}",
            order_id,
            quantity_sanitize_stats.get("quantity_adjusted", 0),
            quantity_sanitize_stats.get("quantity_dropped", 0),
            quantity_sanitize_stats.get("lines_dropped", 0),
            quantity_sanitize_stats.get("max_abs_qty"),
        )
    _update_reparse_job_progress(
        ocr_job_id,
        status="running",
        processing_stage="validation",
        result_state="processing",
        metrics_patch=_current_reparse_quality_metadata(),
    )
    validation_error, validation_detail = _validate_reparse_lines_against_weekly_menu(
        lines=lines,
        week_id=week_id,
        facility_id=facility_id,
        ocr_rows=rows,
        template=template_to_use,
        entries_override=reparse_position_entries if reparse_position_entries else None,
        rows_are_body_only=bool(reparse_quantity_rules.get("rows_are_body_only")),
    )
    date_anchor_error: str | None = None
    date_anchor_detail: dict[str, Any] | None = None
    blank_anchor_error: str | None = None
    blank_anchor_detail: dict[str, Any] | None = None
    if (
        main_provider in {"openai", "gemini"}
        and llm_quantity_only_active
        and pipeline_rows_for_rescue
    ):
        baseline_fields, baseline_structure_rows, _, _ = _resolve_reparse_baseline_rows_for_structure(
            llm_reparse_baseline
        )
        blank_anchor_error, blank_anchor_detail = _validate_reparse_blank_anchor_drift(
            lines=lines,
            structural_fields=baseline_fields,
            structural_rows=baseline_structure_rows,
            reference_rows=[list(row) for row in pipeline_rows_for_rescue if isinstance(row, list)],
            reference_fields=_row_fields_from_template(template_to_use) or baseline_fields,
        )
    if (
        main_provider in {"openai", "gemini"}
        and llm_quantity_only_active
        and existing_line_anchors
    ):
        date_anchor_error, date_anchor_detail = _validate_reparse_date_anchor_stability(
            previous_lines=existing_line_anchors,
            candidate_lines=lines,
        )
    if not validation_error and date_anchor_error:
        validation_error = date_anchor_error
        validation_detail = date_anchor_detail or {}
    if not validation_error and blank_anchor_error:
        validation_error = blank_anchor_error
        validation_detail = blank_anchor_detail or {}
    if not validation_error and reparse_quality_error:
        validation_error = reparse_quality_error
        validation_detail = reparse_quality_detail or {}
    structural_projection_error, structural_projection_detail = (
        _validate_structural_projection_requires_manual_review(
            llm_quantity_only_active=llm_quantity_only_active,
            structural_row_projection=structural_row_projection,
        )
    )
    if not validation_error and structural_projection_error:
        validation_error = structural_projection_error
        validation_detail = structural_projection_detail or {}
    line_count_error, line_count_detail = _evaluate_reparse_line_count_regression(
        provider=main_provider,
        llm_quantity_only_active=llm_quantity_only_active,
        before_count=before_count,
        after_count=len(lines),
    )
    if not validation_error and line_count_error:
        validation_error = line_count_error
        validation_detail = line_count_detail or {}
    if (
        not validation_error
        and isinstance(llm_audit_result, dict)
        and str(llm_audit_result.get("status") or "").lower() == "fail"
    ):
        if (
            main_provider in {"openai", "gemini"}
            and feedback_retry_depth < 1
        ):
            logger.info(
                "Retrying reparse with evaluator feedback order_id={} provider={} depth={}",
                order_id,
                main_provider,
                feedback_retry_depth + 1,
            )
            return reparse_order(
                order_id,
                ocr_prompt=ocr_prompt,
                ocr_provider=main_provider,
                llm_assist=True,
                auto_fallback_context=auto_fallback_context,
                evaluator_feedback=llm_audit_result,
                feedback_retry_depth=feedback_retry_depth + 1,
                draft_rows_override=[list(row) for row in rows if isinstance(row, list)],
                draft_rows_label="Previous failed LLM inference candidate rows",
            )
        validation_error = "sheet_llm_audit_failed"
        validation_detail = {
            "quality_issue": "llm_audit",
            "llm_audit": llm_audit_result,
        }
    if (
        not validation_error
        and isinstance(reparse_cost_info, dict)
        and _read_reparse_bool_env("OCR_REPARSE_COST_ENFORCE_HARD_LIMIT", True)
        and bool(reparse_cost_info.get("over_hard_limit"))
    ):
        validation_error = "llm_cost_limit_exceeded"
        validation_detail = {
            "estimated_cost_usd": reparse_cost_info.get("estimated_cost_usd"),
            "hard_limit_usd": reparse_cost_info.get("hard_limit_usd"),
            "soft_limit_usd": reparse_cost_info.get("soft_limit_usd"),
            "provider": reparse_cost_info.get("provider"),
            "model": reparse_cost_info.get("model"),
            "usage": reparse_cost_info.get("usage"),
            "pricing": reparse_cost_info.get("pricing"),
        }
    validation_warning_reasons: list[str] = []
    validation_warning_detail: dict[str, Any] | None = None
    if validation_error and _is_soft_warning_validation_error(validation_error):
        normalized_warning = str(validation_error).strip()
        if normalized_warning:
            validation_warning_reasons = [normalized_warning]
        validation_warning_detail = validation_detail if isinstance(validation_detail, dict) else {}
        logger.warning(
            "Reparse validation warned but accepted",
            order_id=order_id,
            provider=main_provider,
            warning_reasons=validation_warning_reasons,
            detail=validation_warning_detail,
        )
        validation_error = None
        validation_detail = None

    if validation_error:
        draft_fields = _row_fields_from_template(template_to_use)
        if isinstance(rows, list) and rows and draft_fields:
            cached_payload_for_draft = _load_order_ocr_cache(order_id)
            previous_revision = _select_order_sheet_revision(
                order_id=order_id,
                payload=cached_payload_for_draft,
                exact_only=False,
            )
            if isinstance(previous_revision, dict):
                draft_before_digest = _sheet_digest(
                    fields=previous_revision.get("fields"),
                    header=previous_revision.get("header"),
                    rows_payload=previous_revision.get("rows"),
                    row_ids=previous_revision.get("row_ids"),
                )
            else:
                draft_before_digest = _sheet_digest(
                    fields=draft_fields,
                    header=_sheet_header_from_template(draft_fields, template_to_use),
                    rows_payload=rows,
                    row_ids=[f"reparse-draft-{idx + 1}" for idx in range(len(rows))],
                )
            draft_after_digest = _sheet_digest(
                fields=draft_fields,
                header=_sheet_header_from_template(draft_fields, template_to_use),
                rows_payload=rows,
                row_ids=[f"reparse-draft-{idx + 1}" for idx in range(len(rows))],
            )
            _append_edited_ocr_revision(
                order_id=order_id,
                ui_mode="sheet",
                fields=draft_fields,
                header=_sheet_header_from_template(draft_fields, template_to_use),
                rows_payload=rows,
                row_ids=[f"reparse-draft-{idx + 1}" for idx in range(len(rows))],
                before_digest=draft_before_digest,
                after_digest=draft_after_digest,
                revision_meta={
                    "auto_apply_blocked": True,
                    "reject_reason": validation_error,
                    "reject_reasons": [validation_error],
                    "draft_kind": "reparse_reject",
                },
            )
        logger.warning(
            "Reparse validation rejected",
            order_id=order_id,
            provider=main_provider,
            validation_error=validation_error,
            detail=validation_detail,
        )
        debug_payload = _build_reparse_debug_payload(
            provider=main_provider,
            requested_provider=requested_provider or None,
            llm_assist=bool(llm_assist),
            rows=rows,
            lines_count=len(lines),
            before_count=before_count,
            after_count=len(lines),
            changed=None,
            date_strings=date_strings,
            extracted=extracted_data,
            parsed_output=parsed_output_for_debug,
            error=validation_error,
            request_prompt=effective_prompt,
            normalized_lines=lines,
            reject_reasons=[validation_error],
            validation_detail=validation_detail,
            llm_quantity_only_merge=llm_quantity_only_merge_stats or None,
            llm_cost=reparse_cost_info,
            llm_audit=llm_audit_result,
            pdf_variant_used=str(llm_input_pdf_meta.get("used") or "raw"),
            pdf_variant_fallback_reason=(
                str(llm_input_pdf_meta.get("fallback_reason") or "").strip() or None
            ),
            quality_metadata=_current_reparse_quality_metadata(),
        )
        if auto_fallback_applied:
            debug_payload["auto_fallback"] = dict(auto_fallback_context or {})
        if isinstance(llm_pre_inference_audit, dict) and llm_pre_inference_audit:
            debug_payload["llm_pre_inference_audit"] = llm_pre_inference_audit
        if isinstance(llm_second_pass_audit, dict) and llm_second_pass_audit:
            debug_payload["llm_second_pass_audit"] = llm_second_pass_audit
        if llm_feedback_second_pass_applied:
            debug_payload["llm_feedback_second_pass_applied"] = True
        if llm_feedback_second_pass_error:
            debug_payload["llm_feedback_second_pass_error"] = llm_feedback_second_pass_error
        if isinstance(blank_anchor_realign, dict) and blank_anchor_realign:
            debug_payload["blank_anchor_realign"] = blank_anchor_realign
        try:
            cache_payload: dict[str, Any] = {}
            if isinstance(parsed_output_for_debug, dict):
                cache_payload = dict(parsed_output_for_debug)
            else:
                existing_payload = _load_order_ocr_cache(order_id)
                if isinstance(existing_payload, dict):
                    cache_payload = dict(existing_payload)
            cache_payload["_reparse_debug"] = debug_payload
            _save_order_ocr_cache(order_id, cache_payload)
            if rows and isinstance(rows, list):
                _save_reparse_candidate_as_draft(
                    order_id=order_id,
                    template=template_to_use,
                    rows=[list(row) for row in rows if isinstance(row, list)],
                    before_digest=before_digest,
                    raw_output_override=cache_payload,
                    review_state="auto_apply_blocked",
                    review_blockers=[validation_error],
                    review_warnings=validation_warning_reasons,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Order OCR cache update failed on validation reject", order_id=order_id, error=str(exc))
        _update_reparse_job_progress(
            ocr_job_id,
            status="done" if rows else "failed",
            processing_stage="draft_saved" if rows else "validation",
            result_state="draft_ready_blocked" if rows else "hard_failed",
            error_message=None if rows else validation_error,
            metrics_patch={
                "provider": main_provider,
                "requested_provider": requested_provider or None,
                "row_count": len(rows),
                "line_count": len(lines),
                "llm_assist": bool(llm_assist),
                "finish_reason": llm_finish_reason,
                "truncated_output": bool(llm_truncated_output),
                "rows_replaced_with_pipeline": bool(llm_rows_replaced_with_pipeline),
                "llm_quantity_only_merge": llm_quantity_only_merge_stats or None,
                "structural_row_projection": structural_row_projection or None,
                "before_count": before_count,
                "after_count": len(lines),
                "changed": False,
                "error": validation_error,
                "reject_reasons": [validation_error],
                "validation_detail": validation_detail or {},
                "primary_model": llm_primary_model,
                "repair_pass_applied": bool(llm_repair_pass_applied),
                "repair_pass_reason": llm_repair_pass_reason,
                "repair_pass_error": llm_repair_pass_error,
                "repair_pass_model": llm_repair_pass_model,
                "quality_error": reparse_quality_error,
                "quality_detail": reparse_quality_detail or {},
                "llm_cost": reparse_cost_info or None,
                "llm_audit": llm_audit_result or None,
                "llm_pre_inference_audit": llm_pre_inference_audit or None,
                "llm_second_pass_audit": llm_second_pass_audit or None,
                "llm_feedback_second_pass_applied": bool(llm_feedback_second_pass_applied),
                "llm_feedback_second_pass_error": llm_feedback_second_pass_error,
                "blank_anchor_realign": blank_anchor_realign or None,
                "auto_fallback_applied": bool(auto_fallback_applied),
                "auto_fallback_from_provider": auto_fallback_from_provider,
                "auto_fallback_reason": auto_fallback_reason,
                "pdf_variant_used": str(llm_input_pdf_meta.get("used") or "raw"),
                "pdf_variant_fallback_reason": (
                    str(llm_input_pdf_meta.get("fallback_reason") or "").strip() or None
                ),
                "confirmed_lines_retained": bool(before_count > 0),
                **_current_reparse_quality_metadata(),
            },
        )
        return None, validation_error
    lines = _ensure_unique_line_ids(lines)
    after_digest = _line_digest(lines)
    after_count = len(lines)
    reparse_changed = before_digest != after_digest

    _update_reparse_job_progress(
        ocr_job_id,
        status="running",
        processing_stage="apply",
        result_state="processing",
        metrics_patch=_current_reparse_quality_metadata(),
    )
    log_payload: dict | None = None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        context_conflict = _order_context_conflict_detail(
            order_id=order_id,
            expected_facility_code=facility_id,
            expected_week_code=existing_week_code,
            expected_document_uri=document_uri,
            expected_lines_updated_at=existing_lines_updated_at,
        )
        if context_conflict is not None:
            _update_reparse_job_progress(
                ocr_job_id,
                status="failed",
                processing_stage="stale_context",
                result_state="hard_failed",
                error_message="stale_order_context",
                metrics_patch={
                    "error": "stale_order_context",
                    "context_conflict": context_conflict,
                    "confirmed_lines_retained": bool(before_count > 0),
                    **_current_reparse_quality_metadata(),
                },
            )
            return None, "stale_order_context"
        session.execute(delete(OrderLine).where(OrderLine.order_id == order.id))
        for line in lines:
            session.add(
                OrderLine(
                    id=line.get("id") or _make_line_id(),
                    order_id=order.id,
                    line_id=line.get("line_id"),
                    date=_parse_date_value(line.get("date")),
                    daypart=line.get("daypart"),
                    menu_name=line.get("menu_name"),
                    diet_type=line.get("diet_type"),
                    area_id=line.get("area_id"),
                    bag_type=line.get("bag_type"),
                    quantity_original=line.get("quantity_original"),
                    quantity_corrected=line.get("quantity_corrected"),
                    change_note=line.get("change_note"),
                )
            )
        order.lines_updated_at = datetime.utcnow()
        resolved_week_has_menu = bool(week_id and _build_position_menu_entries_safe(week_id, facility_id))
        update_week_code_to = (
            week_id
            if week_id and resolved_week_has_menu
            else (
                derived_week_id
                if derived_week_id and week_id == derived_week_id
                else None
            )
        )
        if update_week_code_to and order.week_code != update_week_code_to:
            order.week_code = update_week_code_to
        elif not order.week_code and week_id:
            order.week_code = week_id
        session.flush()
        session.refresh(order)
        log_payload = {
            "order_id": order.id,
            "facility_code": order.facility_code,
            "week_code": order.week_code,
            "line_count": len(lines),
        }
        serialized = serialize_order(order)
    if log_payload:
        record_event(
            "order_reparse",
            actor="system",
            target=log_payload["order_id"],
            fac=log_payload["facility_code"],
            wek=log_payload["week_code"],
            metadata={"line_count": log_payload["line_count"]},
        )
    _update_reparse_job_progress(
        ocr_job_id,
        status="done",
        processing_stage="applied",
        result_state="applied",
        error_message=None,
        metrics_patch={
            "provider": main_provider,
            "requested_provider": requested_provider or None,
            "row_count": len(rows),
            "line_count": after_count,
            "llm_assist": bool(llm_assist),
            "finish_reason": llm_finish_reason,
            "truncated_output": bool(llm_truncated_output),
            "rows_replaced_with_pipeline": bool(llm_rows_replaced_with_pipeline),
            "llm_quantity_only_merge": llm_quantity_only_merge_stats or None,
            "primary_model": llm_primary_model,
            "repair_pass_applied": bool(llm_repair_pass_applied),
            "repair_pass_reason": llm_repair_pass_reason,
            "repair_pass_error": llm_repair_pass_error,
            "repair_pass_model": llm_repair_pass_model,
            "quality_error": reparse_quality_error,
            "quality_detail": reparse_quality_detail or {},
            "warning_reasons": validation_warning_reasons or [],
            "warning_detail": validation_warning_detail or {},
            "llm_cost": reparse_cost_info or None,
            "llm_audit": llm_audit_result or None,
            "llm_pre_inference_audit": llm_pre_inference_audit or None,
            "llm_second_pass_audit": llm_second_pass_audit or None,
            "reused_first_pass": bool(reused_first_pass_payload),
            "pipeline_run_skipped": bool(reused_first_pass_payload),
            "llm_feedback_second_pass_applied": bool(llm_feedback_second_pass_applied),
            "llm_feedback_second_pass_error": llm_feedback_second_pass_error,
            "blank_anchor_realign": blank_anchor_realign or None,
            "structural_row_projection": structural_row_projection or None,
            "before_count": before_count,
            "after_count": after_count,
            "before_digest": before_digest,
            "after_digest": after_digest,
            "changed": reparse_changed,
            "auto_fallback_applied": bool(auto_fallback_applied),
            "auto_fallback_from_provider": auto_fallback_from_provider,
            "auto_fallback_reason": auto_fallback_reason,
            "pdf_variant_used": str(llm_input_pdf_meta.get("used") or "raw"),
            "pdf_variant_fallback_reason": (
                str(llm_input_pdf_meta.get("fallback_reason") or "").strip() or None
            ),
            "confirmed_lines_retained": False,
            **_current_reparse_quality_metadata(),
        },
    )
    reparse_debug = _build_reparse_debug_payload(
        provider=main_provider,
        requested_provider=requested_provider or None,
        llm_assist=bool(llm_assist),
        rows=rows,
        lines_count=after_count,
        before_count=before_count,
        after_count=after_count,
        changed=reparse_changed,
        date_strings=date_strings,
        extracted=extracted_data,
        parsed_output=parsed_output_for_debug,
        error=main_ocr_error,
        request_prompt=effective_prompt,
        normalized_lines=lines,
        validation_detail=reparse_quality_detail,
        warning_reasons=validation_warning_reasons or None,
        warning_detail=validation_warning_detail or None,
        llm_quantity_only_merge=llm_quantity_only_merge_stats or None,
        structural_row_projection=structural_row_projection,
        llm_cost=reparse_cost_info,
        llm_audit=llm_audit_result,
        pdf_variant_used=str(llm_input_pdf_meta.get("used") or "raw"),
        pdf_variant_fallback_reason=(
            str(llm_input_pdf_meta.get("fallback_reason") or "").strip() or None
        ),
        quality_metadata=_current_reparse_quality_metadata(),
    )
    if auto_fallback_applied:
        reparse_debug["auto_fallback"] = dict(auto_fallback_context or {})
    if isinstance(llm_pre_inference_audit, dict) and llm_pre_inference_audit:
        reparse_debug["llm_pre_inference_audit"] = llm_pre_inference_audit
    if isinstance(llm_second_pass_audit, dict) and llm_second_pass_audit:
        reparse_debug["llm_second_pass_audit"] = llm_second_pass_audit
    if llm_feedback_second_pass_applied:
        reparse_debug["llm_feedback_second_pass_applied"] = True
    if llm_feedback_second_pass_error:
        reparse_debug["llm_feedback_second_pass_error"] = llm_feedback_second_pass_error
    if isinstance(blank_anchor_realign, dict) and blank_anchor_realign:
        reparse_debug["blank_anchor_realign"] = blank_anchor_realign
    try:
        cache_ref = pipeline_output_ref
        parsed_output = pipeline_output_payload if isinstance(pipeline_output_payload, dict) else None
        if not cache_ref and not isinstance(parsed_output, dict):
            job = get_ocr_job(ocr_job_id)
            cache_ref = job.get("output_reference") if job else None
        if not isinstance(parsed_output, dict):
            parsed_output = _load_pipeline_output_with_retry(cache_ref)
        cache_payload: dict[str, Any] = {}
        if isinstance(parsed_output, dict):
            cache_payload = dict(parsed_output)
        else:
            existing_payload = _load_order_ocr_cache(order_id)
            if isinstance(existing_payload, dict):
                cache_payload = dict(existing_payload)
        cache_payload["_reparse_debug"] = reparse_debug
        _save_order_ocr_cache(order_id, cache_payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Order OCR cache update failed", order_id=order_id, error=str(exc))
    serialized["reparse"] = {
        "before_count": before_count,
        "after_count": after_count,
        "before_digest": before_digest,
        "after_digest": after_digest,
        "provider": main_provider,
        "changed": reparse_changed,
        "warning_reasons": validation_warning_reasons or [],
        "warning_detail": validation_warning_detail or {},
        "llm_cost": reparse_cost_info or None,
    }
    serialized["ocr_job_id"] = ocr_job_id
    return serialized, None


def _maybe_dump_reparse_debug(
    order_id: str,
    message_id: Optional[str],
    extracted,
    tokens: list[dict],
    grid: Optional[object] = None,
    error: Optional[str] = None,
) -> None:
    dump_dir = os.getenv("OCR_DEBUG_DUMP_DIR")
    if not dump_dir:
        return
    try:
        path = Path(dump_dir) / f"ocr_reparse_{order_id}.json"
        raw_text = getattr(extracted, "raw_text", None) if extracted else None
        provider_debug = getattr(extracted, "provider_debug", None) if extracted else None
        data = {
            "order_id": order_id,
            "message_id": message_id,
            "ocr_provider": getattr(extracted, "ocr_provider", None),
            "ocr_error": error,
            "date_strings": getattr(extracted, "date_strings", []) if extracted else [],
            "token_count": len(tokens or []),
            "tokens": tokens,
            "raw_text": raw_text,
            "provider_debug": provider_debug if isinstance(provider_debug, dict) else None,
            "grid": (
                {
                    "table_box": grid.table_box,
                    "column_edges": grid.column_edges,
                    "row_edges": grid.row_edges,
                    "confidence": grid.confidence,
                }
                if grid
                else None
            ),
        }
        path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
        logger.info("Reparse OCR debug dumped", path=str(path))
    except Exception:  # noqa: BLE001
        logger.exception("Failed to dump reparse OCR debug data")


def set_facility(
    order_id: str,
    facility_code: str,
    *,
    expected_current_facility: str | None = None,
    enforce_conflict_guard: bool = False,
) -> bool | tuple[bool, str | None]:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return (False, "order_not_found") if enforce_conflict_guard else False
        if enforce_conflict_guard:
            conflict = _selection_conflict_detail(
                field="facility",
                current_value=order.facility_code,
                expected_value=expected_current_facility,
                desired_value=facility_code,
            )
            if conflict is not None:
                return False, conflict["error"]
        if str(order.facility_code or "").strip() == str(facility_code or "").strip():
            return (True, None) if enforce_conflict_guard else True
        order.facility_code = facility_code
        logger.info("Order facility set", order_id=order_id, facility_code=facility_code)
        record_event(
            "order_facility_set",
            actor="system",
            target=order_id,
            fac=facility_code,
            wek=order.week_code,
        )
    _invalidate_orders_cache()
    try:
        workflow_state_service.refresh_workflow_state(order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow state refresh failed after facility update", order_id=order_id, error=str(exc))
    return (True, None) if enforce_conflict_guard else True


def set_week(
    order_id: str,
    week_code: str,
    *,
    expected_current_week: str | None = None,
    enforce_conflict_guard: bool = False,
) -> bool | tuple[bool, str | None]:
    normalized_week = _normalize_sheet_week_value(week_code)
    if not normalized_week:
        raise ValueError("week_code_invalid")
    normalized_expected_week = _normalize_sheet_week_value(expected_current_week) or str(expected_current_week or "").strip() or None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return (False, "order_not_found") if enforce_conflict_guard else False
        if enforce_conflict_guard:
            conflict = _selection_conflict_detail(
                field="week",
                current_value=_normalize_sheet_week_value(order.week_code) or order.week_code,
                expected_value=normalized_expected_week,
                desired_value=normalized_week,
            )
            if conflict is not None:
                return False, conflict["error"]
        if (_normalize_sheet_week_value(order.week_code) or order.week_code or "") == normalized_week:
            return (True, None) if enforce_conflict_guard else True
        order.week_code = normalized_week
        logger.info("Order week set", order_id=order_id, week_code=normalized_week)
        record_event(
            "order_week_set",
            actor="system",
            target=order_id,
            fac=order.facility_code,
            wek=normalized_week,
        )
    _invalidate_orders_cache()
    try:
        workflow_state_service.refresh_workflow_state(order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow state refresh failed after week update", order_id=order_id, error=str(exc))
    return (True, None) if enforce_conflict_guard else True


def choose_critical_decision(
    order_id: str,
    decision_type: str,
    selected_value: str,
    *,
    selected_by: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    latest_evidence = get_latest_ocr_evidence_run(order_id, backfill_from_cache=True)
    current_evidence_run_id = str((latest_evidence or {}).get("id") or "").strip() or None
    current_decision = critical_decision_service.get_latest_decision(order_id, decision_type)
    if isinstance(current_decision, dict):
        base_evidence_run_id = str(current_decision.get("base_evidence_run_id") or "").strip() or None
        if base_evidence_run_id and current_evidence_run_id and base_evidence_run_id != current_evidence_run_id:
            return None, "decision_stale"
    chosen = critical_decision_service.choose_decision(
        order_id,
        decision_type,
        selected_value,
        selected_by=selected_by,
        current_evidence_run_id=current_evidence_run_id,
    )
    if not isinstance(chosen, dict):
        return None, "decision_not_found"
    normalized_type = str(decision_type or "").strip().lower()
    if normalized_type == "facility":
        result = set_facility(order_id, selected_value)
        if result is False or result == (False, None):
            return None, "facility_update_failed"
    elif normalized_type == "week":
        try:
            result = set_week(order_id, selected_value)
        except ValueError:
            return None, "week_invalid"
        if result is False or result == (False, None):
            return None, "week_update_failed"
    try:
        workflow = workflow_state_service.refresh_workflow_state(order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Workflow state refresh failed after critical choice", order_id=order_id, error=str(exc))
        workflow = None
    return {
        "decision": chosen,
        "workflow_state": workflow,
    }, None


def save_order_facility_template_columns(
    order_id: str,
    columns: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(columns, list) or not columns:
        return None, "columns_invalid"
    normalized_columns = config_service.normalize_fax_template_columns(columns)
    if not normalized_columns:
        return None, "columns_invalid"

    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        facility_id = str(order.facility_code or "").strip()

    if not facility_id:
        return None, "facility_missing"

    config = facility_service.get_facility_config(facility_id) or {}
    next_config = dict(config)
    override = dict(next_config.get("fax_template_override") or {})
    override["columns"] = normalized_columns
    override.pop("main_ocr_row_fields", None)
    next_config["fax_template_override"] = override

    validation = validate_facility_config(next_config)
    if validation["errors"]:
        return {"validation": validation}, "validation_error"

    updated = facility_service.update_config(facility_id, next_config)
    if not updated:
        return None, "facility_not_found"
    resolved = config_service.get_facility_config(facility_id)
    return {"updated": True, "validation": validation, "resolved_config": resolved}, None


def set_status(order_id: str, status: str) -> bool:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return False
        order.status = status
        logger.info("Order status set", order_id=order_id, status=status)
        return True


def _build_menu_amount_meta(order: Order) -> dict[str, dict[str, object]]:
    names = [
        str(line.menu_name).strip()
        for line in (order.lines or [])
        if line.menu_name and str(line.menu_name).strip()
    ]
    if not names:
        return {}
    unique_names: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        unique_names.append(name)

    item_map: dict[str, dict] = {}
    order_month_id = _to_sheet_month_id(order.week_code)
    if order_month_id:
        try:
            items = menu_service.get_menu_items_for_facility(order_month_id, order.facility_code)
        except Exception:
            items = []
        item_map = {
            str(item.get("name")).strip(): item
            for item in items
            if item.get("name")
        }

    defaults = menu_service.resolve_menu_defaults(unique_names, order.facility_code)
    meta: dict[str, dict[str, object]] = {}
    for name in unique_names:
        item = item_map.get(name, {})
        fallback = defaults.get(name, {})
        qty = item.get("qty_per_serving")
        if qty is None:
            qty = fallback.get("qty_per_serving")
        unit = item.get("unit_type") or fallback.get("unit_type")
        try:
            qty_value = float(qty) if qty is not None else None
        except Exception:
            qty_value = None
        meta[name] = {
            "qty_per_serving": qty_value,
            "unit_type": unit,
        }
    return meta


def _line_final_quantity(line: OrderLine) -> float | None:
    quantity = line.quantity_corrected
    if quantity is None:
        quantity = line.quantity_original
    if quantity is None:
        return None
    try:
        return float(quantity)
    except Exception:
        return None


def _line_actual_amount(line: OrderLine, menu_meta: dict[str, dict[str, object]]) -> tuple[float | None, str | None]:
    name = str(line.menu_name or "").strip()
    if not name:
        return None, None
    meta = menu_meta.get(name) or {}
    qty_per_serving = meta.get("qty_per_serving")
    unit = meta.get("unit_type")
    final_qty = _line_final_quantity(line)
    if final_qty is None or qty_per_serving is None:
        return None, str(unit) if unit else None
    try:
        amount = float(final_qty) * float(qty_per_serving)
    except Exception:
        return None, str(unit) if unit else None
    return amount, str(unit) if unit else None


def _serialize_line_with_amount(line: OrderLine, menu_meta: dict[str, dict[str, object]]) -> dict:
    menu_name = str(line.menu_name or "").strip()
    menu_item = menu_meta.get(menu_name) or {}
    actual_amount, actual_unit = _line_actual_amount(line, menu_meta)
    return {
        "id": line.id,
        "line_id": line.line_id,
        "date": line.date.isoformat() if line.date else None,
        "daypart": line.daypart,
        "menu_name": line.menu_name,
        "diet_type": line.diet_type,
        "area_id": line.area_id,
        "bag_type": line.bag_type,
        "quantity_original": line.quantity_original,
        "quantity_corrected": line.quantity_corrected,
        "change_note": line.change_note,
        "menu_qty_per_serving": menu_item.get("qty_per_serving"),
        "menu_unit_type": menu_item.get("unit_type"),
        "actual_amount": actual_amount,
        "actual_unit_type": actual_unit,
    }


def serialize_order(order: Order):
    prompt_enabled = True
    menu_meta = _build_menu_amount_meta(order)
    week_month_id = _to_sheet_month_id(order.week_code)
    week_value = _normalize_sheet_week_value(order.week_code) or week_month_id
    week_label = _format_sheet_week_label(order.week_code) or week_month_id

    return {
        "id": order.id,
        "ocr_job_id": f"OCR-{order.id}",
        "facility": order.facility_code,
        "week": week_month_id,
        "week_value": week_value,
        "week_label": week_label,
        "status": order.status,
        "document": order.document_uri,
        "message_id": order.message_id,
        "received_at": order.received_at,
        "document_id": order.current_document_id,
        "superseded_document_ids": order.superseded_document_ids or [],
        "lines_updated_at": order.lines_updated_at,
        "ocr_prompt_enabled": prompt_enabled,
        "lines": [_serialize_line_with_amount(line, menu_meta) for line in (order.lines or [])],
    }


def serialize_order_summary(order: Order):
    week_month_id = _to_sheet_month_id(order.week_code)
    week_value = _normalize_sheet_week_value(order.week_code) or week_month_id
    week_label = _format_sheet_week_label(order.week_code) or week_month_id
    return {
        "id": order.id,
        "ocr_job_id": f"OCR-{order.id}",
        "facility": order.facility_code,
        "week": week_month_id,
        "week_value": week_value,
        "week_label": week_label,
        "status": order.status,
        "document": order.document_uri,
        "message_id": order.message_id,
        "received_at": order.received_at,
        "document_id": order.current_document_id,
        "superseded_document_ids": order.superseded_document_ids or [],
        "lines_updated_at": order.lines_updated_at,
    }
