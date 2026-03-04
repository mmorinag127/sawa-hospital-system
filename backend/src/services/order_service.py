from typing import Any, Optional
from pathlib import Path
import json
import os
import hashlib
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from urllib.parse import urlparse
from difflib import SequenceMatcher
from src.workers.ingest_mail_adapter import IngestEmailPayload
from loguru import logger
from uuid import uuid4
from datetime import date, datetime, timedelta
import pandas as pd
from sqlalchemy import select, delete, inspect, text, func

from src.db import Base, engine, session_scope
from src.models.order import Order, OrderLine, OrderMenuSnapshot
from src.models.document import OrderDocument
from src.models.order_ocr_cache import OrderOcrCache
from src.models.output import Bag, LabelRow, DeliveryNote, ManufacturingAggregateRow
from src.models.ingest_job import IngestJob  # noqa: F401
from src.models.user import AuditLog
from src.services.notification_service import record_event
from src.services import config_service, menu_service
from src.services.fax_extractor import extract_fax_data, filter_tokens_by_box, rows_from_markdown
from src.services.fax_parser import parse_order_lines
from src.services.ingest_policy import parse_date_string, month_id_from_dates
from src.services.storage_service import load_bytes_from_uri
from src.services.storage_service import generate_signed_url
from src.services.grid_detector import detect_table_grid, detect_table_grid_image
from src.services.ocr_job_service import create_job, update_job, get_job as get_ocr_job
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


def _run_roi_ocr_pipeline(
    *,
    job_id: str,
    pdf_bytes: bytes,
    facility_id: str | None,
    input_reference: str | None,
    preferred_template_id: str | None,
) -> str | None:
    try:
        logger.info(
            "ROI OCR pipeline start job_id={} facility_id={} template_id={} input_reference={}",
            job_id,
            facility_id,
            preferred_template_id,
            input_reference,
        )
        output = run_ocr_pipeline(
            pdf_bytes=pdf_bytes,
            job_id=job_id,
            facility_id=facility_id,
            input_reference=input_reference,
            preferred_template_id=preferred_template_id,
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


def clear_all():
    with session_scope() as session:
        session.execute(delete(LabelRow))
        session.execute(delete(Bag))
        session.execute(delete(DeliveryNote))
        session.execute(delete(ManufacturingAggregateRow))
        session.execute(delete(OrderDocument))
        session.execute(delete(OrderLine))
        session.execute(delete(OrderMenuSnapshot))
        session.execute(delete(Order))


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
            session.execute(delete(OrderMenuSnapshot).where(OrderMenuSnapshot.order_id == order.id))
            session.execute(delete(OrderLine).where(OrderLine.order_id == order.id))
            session.execute(delete(Order).where(Order.id == order.id))
            removed += 1
    return removed


def _make_order_id() -> str:
    return f"ORD{uuid4().hex[:8]}"


def _make_document_id() -> str:
    return f"DOC{uuid4().hex[:8]}"


def _make_line_id() -> str:
    return f"OLN{uuid4().hex[:6]}"


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
    if not week_id:
        return lines
    items = menu_service.get_menu_items_for_facility(week_id, facility_id)
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


def _build_position_menu_entries(week_id: str) -> list[dict]:
    menu = menu_service.get_menu(week_id)
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
    return entries


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

    with session_scope() as session:
        q = (
            select(OrderLine.date, OrderLine.daypart, OrderLine.menu_name)
            .join(Order, Order.id == OrderLine.order_id)
            .where(Order.week_code == week_id, OrderLine.menu_name.is_not(None))
            .order_by(OrderLine.date, OrderLine.daypart, OrderLine.menu_name)
        )
        rows_all = session.execute(q).all()
        if facility_id:
            q_fac = q.where(Order.facility_code == facility_id)
            rows_fac = session.execute(q_fac).all()
            entries_fac = _serialize_rows(rows_fac)
            if entries_fac:
                return entries_fac
        return _serialize_rows(rows_all)


def _resolve_sheet_payload_for_menu_entries(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    edited = payload.get("_edited_ocr")
    if isinstance(edited, dict):
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
    ocr_payload: dict[str, Any] | None,
    template: dict[str, Any],
    received_at: datetime,
) -> tuple[list[dict], str]:
    entries = _build_position_menu_entries(week_id)
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


def _build_reparse_position_menu_entries(
    *,
    week_id: str | None,
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
    payload_dates |= existing_line_dates
    if len(existing_line_dates) >= 2:
        lower = min(existing_line_dates) - timedelta(days=1)
        upper = max(existing_line_dates) + timedelta(days=1)
        # When persisted lines already define a week scope, ignore parsed line-date
        # anchors that are clearly outside that scope (typical LLM date drift).
        if line_dates and any(item < lower or item > upper for item in line_dates):
            logger.warning(
                "Reparse parsed line dates out of existing scope; fallback to existing anchors",
                existing_dates=[item.isoformat() for item in sorted(existing_line_dates)],
                parsed_line_dates=[item.isoformat() for item in sorted(line_dates)],
            )
            lines_for_scope = []
        payload_dates = {
            item for item in payload_dates if isinstance(item, date) and lower <= item <= upper
        } | existing_line_dates
    return _build_position_entries_for_lines(
        week_id=week_id,
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


def _max_source_row_index_for_position_scope(lines: list[dict[str, Any]] | None) -> int:
    max_index = -1
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        source_idx_raw = line.get("source_row_index")
        try:
            source_idx = int(source_idx_raw) if source_idx_raw is not None else -1
        except Exception:
            source_idx = -1
        if source_idx > max_index:
            max_index = source_idx
    return max_index


def _expand_scoped_entries_for_source_row_span(
    *,
    entries: list[dict],
    scoped_entries: list[dict],
    lines: list[dict[str, Any]] | None,
    max_extension_rows: int = 8,
) -> list[dict]:
    if not entries or not scoped_entries:
        return list(scoped_entries)
    max_source_row_index = _max_source_row_index_for_position_scope(lines)
    if max_source_row_index < 0:
        return list(scoped_entries)
    needed_count = max_source_row_index + 1
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
    lines: list[dict[str, Any]] | None,
    payload_dates: set[date] | None = None,
) -> list[dict]:
    if not week_id:
        return []
    entries = _build_position_menu_entries(week_id)
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
    if pipeline_count <= 0:
        if expected <= 0:
            return observed_count
        # If menu scope is clearly over-broad (for example month-wide 224 rows)
        # while observed rows are week-sized and no reliable pipeline rows exist,
        # use observed row count to avoid false row-coverage failures.
        if observed_count > 0 and expected >= observed_count * 3:
            return observed_count
        return expected
    if expected <= 0:
        return pipeline_count

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
    entries_override: list[dict] | None = None,
) -> tuple[list[dict], int]:
    entries: list[dict]
    if isinstance(entries_override, list):
        entries = [item for item in entries_override if isinstance(item, dict)]
    else:
        if not week_id:
            return lines, 0
        entries = _build_position_menu_entries(week_id)
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
    entries_override: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    if entries_override:
        try:
            return _apply_menu_position_mapping(
                lines,
                week_id,
                entries_override=entries_override,
            )
        except TypeError as exc:
            if "entries_override" not in str(exc):
                raise
    return _apply_menu_position_mapping(lines, week_id)


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
    numeric_rows: set[int] = set()
    for source_row_index, row in enumerate(rows[header_rows:]):
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
    columns = template.get("columns")
    if not isinstance(columns, list):
        return []
    indexes: set[int] = set()
    for col in columns:
        if not isinstance(col, dict):
            continue
        if col.get("role") not in {"quantity", "quantity_change"}:
            continue
        index_raw = col.get("index")
        if not isinstance(index_raw, int):
            continue
        indexes.add(index_raw)
    return sorted(indexes)


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
    data_rows = [row for row in rows[header_rows:] if isinstance(row, list)]
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

    if str(os.getenv("OCR_REPARSE_ENABLE_COLUMN_ANOMALY_GATE", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return None, detail

    quantity_indexes = _template_quantity_column_indexes(template)
    reference_normalized = (
        [list(row) for row in reference_rows if isinstance(row, list)]
        if isinstance(reference_rows, list)
        else []
    )
    if not quantity_indexes or not reference_normalized:
        return None, detail

    llm_counts = _quantity_column_non_empty_counts(
        rows=normalized_rows,
        quantity_indexes=quantity_indexes,
    )
    ref_counts = _quantity_column_non_empty_counts(
        rows=reference_normalized,
        quantity_indexes=quantity_indexes,
    )
    min_ratio = _read_reparse_float_env("OCR_REPARSE_COLUMN_NONEMPTY_MIN_RATIO", 0.25, min_value=0.0)
    max_ratio = _read_reparse_float_env("OCR_REPARSE_COLUMN_NONEMPTY_MAX_RATIO", 3.0, min_value=0.1)
    if max_ratio < min_ratio:
        max_ratio = min_ratio
    unexpected_abs = _read_reparse_int_env("OCR_REPARSE_COLUMN_UNEXPECTED_NONEMPTY_ABS", 4, min_value=1)
    unexpected_ratio = _read_reparse_float_env(
        "OCR_REPARSE_COLUMN_UNEXPECTED_NONEMPTY_RATIO",
        0.12,
        min_value=0.0,
    )

    anomaly_columns: list[dict[str, Any]] = []
    expected_for_threshold = max(effective_expected, len(reference_normalized))
    unexpected_threshold = max(unexpected_abs, int(expected_for_threshold * unexpected_ratio))
    for idx in quantity_indexes:
        llm_count = int(llm_counts.get(idx, 0))
        ref_count = int(ref_counts.get(idx, 0))
        if ref_count <= 0:
            if llm_count >= unexpected_threshold:
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

    detail["column_non_empty"] = {
        "llm": {str(idx): int(llm_counts.get(idx, 0)) for idx in quantity_indexes},
        "reference": {str(idx): int(ref_counts.get(idx, 0)) for idx in quantity_indexes},
    }
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
    before_col_anomaly = int(before_detail.get("column_anomaly_count") or 0)
    after_col_anomaly = int(after_detail.get("column_anomaly_count") or 0)

    if after_coverage > before_coverage:
        return True
    if after_missing_tail < before_missing_tail:
        return True
    if after_col_anomaly < before_col_anomaly:
        return True
    return False


def _prefer_existing_week_when_derived_missing_menu(
    *,
    derived_week_id: str | None,
    existing_week_code: str | None,
) -> str | None:
    if not derived_week_id:
        return existing_week_code
    if not existing_week_code:
        return derived_week_id
    if _build_position_menu_entries(derived_week_id):
        return derived_week_id
    if _build_position_menu_entries(existing_week_code):
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
    ocr_rows: list[list[str]] | None,
    template: dict[str, Any],
    entries_override: list[dict[str, Any]] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    if not lines:
        return None, None
    entries: list[dict[str, Any]]
    if isinstance(entries_override, list):
        entries = [item for item in entries_override if isinstance(item, dict)]
    else:
        if not week_id:
            return None, None
        entries = _build_position_menu_entries(week_id)
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
            )
            lines, mapped_rows = _apply_menu_position_mapping_safe(
                lines,
                week_id,
                entries_override=position_entries if position_entries else None,
            )
            if mapped_rows <= 0:
                lines = _apply_menu_matching(lines, week_id, payload.facility_hint, min_ratio)
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
        return serialize_order(order)


_orders_cache_lock = threading.Lock()
_orders_cache: dict[str, tuple[float, list[dict]]] = {}


def _fetch_orders(status: Optional[str]) -> list[dict]:
    with session_scope() as session:
        query = select(Order)
        if status:
            query = query.where(Order.status == status)
        orders = session.execute(query).scalars().all()
        return [serialize_order_summary(o) for o in orders]


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


def update_lines(order_id: str, lines: list) -> bool:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return False
        # wipe existing
        session.execute(delete(OrderLine).where(OrderLine.order_id == order_id))
        for line in lines:
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
        record_event(
            "order_lines_update",
            actor="system",
            target=order_id,
            fac=order.facility_code,
            wek=order.week_code,
            metadata={"line_count": len(lines)},
        )
        return True


def _is_blank_menu_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _build_menu_snapshot(order: Order) -> dict:
    names = sorted({line.menu_name for line in (order.lines or []) if line.menu_name})
    items: list[dict] = []
    if order.week_code:
        items = menu_service.get_menu_items_for_facility(order.week_code, order.facility_code)
    item_map = {item.get("name"): item for item in items if item.get("name")}
    defaults = menu_service.resolve_menu_defaults(names, order.facility_code)
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


def confirm_order(order_id: str):
    serialized_order: dict | None = None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None
        snapshot_payload = _build_menu_snapshot(order)
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
        logger.info("Order confirmed", order_id=order_id)
        record_event(
            "order_confirm",
            actor="system",
            target=order_id,
            fac=order.facility_code,
            wek=order.week_code,
        )
        serialized_order = serialize_order(order)
    _register_training_sample_after_confirm(order_id)
    return serialized_order


def get_order_by_id(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None
        return serialize_order(order)


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
    if not payload:
        # Bag rows are materialized during output generation/rebuild.
        # Auto-rebuild once so operators can open the bag tab without manual pre-step.
        try:
            from src.services.output_builder import rebuild_bags

            rebuilt = rebuild_bags(order_id)
            payload = rebuilt.get("bags") if isinstance(rebuilt, dict) else payload
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bag auto rebuild failed", order_id=order_id, error=str(exc))
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
            for preserved_key in ("_edited_ocr", "_reparse_debug"):
                preserved_value = existing_payload.get(preserved_key)
                if isinstance(preserved_value, dict) and preserved_key not in next_payload:
                    next_payload[preserved_key] = preserved_value
            cache.payload = next_payload
            cache.updated_at = datetime.utcnow()
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
    return _load_order_ocr_cache(order_id)


def _load_pipeline_output_with_retry(output_ref: str | None) -> Optional[dict]:
    if not output_ref:
        return None
    try:
        wait_seconds = float(os.getenv("OCR_REPARSE_OUTPUT_WAIT_SECONDS", "90"))
    except ValueError:
        wait_seconds = 15.0
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
    if "nomeat" in token or ("禁" in token and "肉" in token):
        return "no_meat"
    if "nofish" in token or ("禁" in token and "魚" in token):
        return "no_fish"
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
            "soft": "軟菜",
            "mixer": "ミキサー",
            "daycare": "通所",
            "staff": "職員",
            "no_meat": "禁食(肉禁)",
            "no_fish": "禁食(魚禁)",
        }.get(diet, diet)
        if area == "X":
            return diet_label
        return f"{diet_label}{area}"
    return field


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
        role = str(col.get("role") or "").strip().lower()
        if role == "date":
            derived.append("date_mmdd")
        elif role == "daypart":
            derived.append("daypart")
        elif role == "menu_name":
            derived.append("menu")
        elif role == "note":
            derived.append("remarks")
        elif role == "quantity":
            diet = _normalize_sheet_diet(col.get("diet_type")) or "unknown"
            area = _normalize_sheet_area(col.get("area_id")) or "X"
            derived.append(f"qty.{diet}_{area.lower()}")
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
        "warnings",
        "failed_cells",
        "combined",
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
    sanitized: list[list[str]] = []
    if not isinstance(rows_payload, list):
        return sanitized
    for row in rows_payload:
        if isinstance(row, dict):
            sanitized.append([_field_value_to_str(row.get(field)) for field in fields])
            continue
        if isinstance(row, list):
            current = [_field_value_to_str(cell) for cell in row[: len(fields)]]
            if len(current) < len(fields):
                current.extend([""] * (len(fields) - len(current)))
            sanitized.append(current)
    return sanitized


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
) -> None:
    if not fields:
        return
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
            if not isinstance(raw_output, dict):
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


def apply_ocr_table(
    order_id: str,
    *,
    markdown: str | None = None,
    header: object = None,
    rows: object = None,
    ui_mode: str | None = None,
    fields: object = None,
    row_ids: object = None,
):
    config_service.reload_configs()
    has_markdown = isinstance(markdown, str) and bool(markdown.strip())
    has_rows = isinstance(rows, list) and bool(rows)
    if not has_markdown and not has_rows:
        return None, "markdown_empty"

    before_count = 0
    before_digest = ""
    existing_week_code = None
    facility_week_hint: str | None = None
    global_week_hint: str | None = None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        if not order.facility_code:
            return None, "facility_missing"
        received_at = order.received_at or pd.Timestamp.utcnow()
        facility_id = order.facility_code
        existing_week_code = order.week_code
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

    policy = config_service.load_ingest_policy()
    lines = parse_order_lines(
        parsed_rows,
        template,
        received_at,
        policy.get("quantity_rules", {}),
    )
    if not lines:
        update_job(f"OCR-{order_id}", status="empty", error_message="lines_empty")
        return None, "lines_empty"

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
    derived_week_id = (
        month_id_from_dates(line_dates, received_at, policy) if line_dates else None
    )
    ocr_payload_for_week: dict[str, Any] | None = None
    payload_for_week, _ = get_ocr_output(order_id, persist_cache=False)
    if isinstance(payload_for_week, dict):
        ocr_payload_for_week = payload_for_week
    min_ratio = float(policy.get("menu_match_min_ratio", 0.72))
    week_id = _resolve_sheet_week_id(
        current_week_id=existing_week_code,
        received_at=received_at,
        order_lines=week_resolution_lines,
        ocr_payload=ocr_payload_for_week,
        facility_id=facility_id,
        week_hints=[hint for hint in [facility_week_hint, global_week_hint] if hint],
    )
    if not week_id:
        week_id = _prefer_existing_week_when_derived_missing_menu(
            derived_week_id=derived_week_id,
            existing_week_code=existing_week_code,
        )
    payload_dates_for_position = _collect_sheet_dates_from_rows(parsed_rows, received_at=received_at)
    if isinstance(ocr_payload_for_week, dict):
        payload_dates_for_position |= {
            item
            for item in _collect_sheet_dates_from_payload(ocr_payload_for_week, received_at)
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
        lines, mapped_rows = _apply_menu_position_mapping_safe(
            lines,
            week_id,
            entries_override=position_entries_for_apply if position_entries_for_apply else None,
        )
    if mapped_rows <= 0:
        lines = _apply_menu_matching(lines, week_id, facility_id, min_ratio)

    after_digest = _line_digest(lines)
    after_count = len(lines)
    reparse_changed = before_digest != after_digest
    log_payload: dict | None = None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
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
        resolved_week_has_menu = bool(week_id and _build_position_menu_entries(week_id))
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
            metadata={"line_count": log_payload["line_count"], "source": source},
        )
    update_job(f"OCR-{order_id}", status="done", error_message=None, metrics={"source": source})
    _append_edited_ocr_revision(
        order_id=order_id,
        ui_mode=revision_ui_mode,
        fields=revision_fields,
        header=revision_header,
        rows_payload=rows if isinstance(rows, list) else parsed_rows,
        row_ids=revision_row_ids,
        before_digest=before_digest,
        after_digest=after_digest,
    )
    serialized["reparse"] = {
        "before_count": before_count,
        "after_count": after_count,
        "before_digest": before_digest,
        "after_digest": after_digest,
        "provider": source,
        "changed": before_digest != after_digest,
    }
    serialized["ocr_job_id"] = f"OCR-{order_id}"
    return serialized, None


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
        for key in ("before_count", "after_count", "changed", "requested_provider", "llm_assist")
    ):
        return parsed, False

    provider_raw = metrics.get("provider") or metrics.get("requested_provider")
    provider = str(provider_raw or "").strip()
    if not provider:
        return parsed, False

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
    if isinstance(metrics.get("finish_reason"), str) and metrics.get("finish_reason").strip():
        _set_value("finish_reason", metrics.get("finish_reason").strip())
    if isinstance(metrics.get("truncated_output"), bool):
        _set_value("truncated_output", bool(metrics.get("truncated_output")))
    if isinstance(metrics.get("rows_replaced_with_pipeline"), bool):
        _set_value("rows_replaced_with_pipeline", bool(metrics.get("rows_replaced_with_pipeline")))
    if isinstance(metrics.get("error"), str) and metrics.get("error").strip():
        _set_value("error", metrics.get("error").strip())
    reject_reasons = metrics.get("reject_reasons")
    if isinstance(reject_reasons, list):
        normalized_reasons = [str(item).strip() for item in reject_reasons if str(item).strip()]
        if normalized_reasons:
            _set_value("reject_reasons", normalized_reasons[:20])
    validation_detail = metrics.get("validation_detail")
    if isinstance(validation_detail, dict) and validation_detail:
        _set_value("validation_detail", validation_detail)
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
    job = get_ocr_job(f"OCR-{order_id}")
    parsed = _load_job_output(job, "order")
    parsed_source = "job"
    order_job_exists = job is not None
    order_job_pending = _job_is_pending(job) or _output_is_pending(parsed)
    if _output_is_pending(parsed):
        parsed = None
    fallback_job = None
    if parsed is None and message_id and not order_job_exists:
        fallback_job = get_ocr_job(f"OCR-{message_id}")
        parsed = _load_job_output(fallback_job, "message")
        parsed_source = "message"
        if _output_is_pending(parsed):
            parsed = None
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
    if persist_cache and not _output_is_pending(parsed):
        _save_order_ocr_cache(order_id, parsed)
    cached_payload = _load_order_ocr_cache(order_id)
    if isinstance(cached_payload, dict):
        enriched = dict(parsed) if isinstance(parsed, dict) else {}
        merged = False
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
    return _attach_facility_candidates(parsed), None


def get_ocr_pages(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        message_id = order.message_id
        facility_id = order.facility_code
    job = get_ocr_job(f"OCR-{order_id}")
    parsed = _load_job_output(job, "order")
    parsed_source = "job"
    order_job_exists = job is not None
    order_job_pending = _job_is_pending(job) or _output_is_pending(parsed)
    if _output_is_pending(parsed):
        parsed = None
    fallback_job = None
    if parsed is None and message_id and not order_job_exists:
        fallback_job = get_ocr_job(f"OCR-{message_id}")
        parsed = _load_job_output(fallback_job, "message")
        parsed_source = "message"
        if _output_is_pending(parsed):
            parsed = None
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
        if not active_job:
            return None, "ocr_job_not_found"
        if order_job_pending:
            return None, "ocr_output_pending"
        if _job_is_pending(active_job):
            return None, "ocr_output_pending"
        if active_job.get("output_reference"):
            return None, "ocr_output_invalid"
        return None, "ocr_output_not_found"
    if not _output_is_pending(parsed):
        _save_order_ocr_cache(order_id, parsed)
    pages_payload = parsed.get("pages")
    if not isinstance(pages_payload, list):
        return None, "ocr_pages_not_found"
    pages: list[dict[str, object]] = []
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
            }
        )
    combined = parsed.get("combined") if isinstance(parsed.get("combined"), dict) else {}
    combined_urls = {
        key: _signed_url_from_uri(value) for key, value in combined.items() if isinstance(value, str)
    }
    table_box = None
    table_units = None
    grid_column_edges = None
    grid_row_edges = None
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
        raw_box = template.get("grid_table_box") or template.get("table_box")
        if isinstance(raw_box, list) and len(raw_box) >= 4:
            try:
                table_box = [float(value) for value in raw_box[:4]]
            except (TypeError, ValueError):
                table_box = None
        units = template.get("units")
        if isinstance(units, str) and units:
            table_units = units
        raw_edges = template.get("grid_column_edges")
        if isinstance(raw_edges, list) and len(raw_edges) >= 2:
            try:
                grid_column_edges = [float(value) for value in raw_edges]
            except (TypeError, ValueError):
                grid_column_edges = None
        raw_rows = template.get("grid_row_edges")
        if isinstance(raw_rows, list) and len(raw_rows) >= 2:
            try:
                grid_row_edges = [float(value) for value in raw_rows]
            except (TypeError, ValueError):
                grid_row_edges = None
        for key in grid_params.keys():
            if key in template:
                grid_params[key] = template.get(key)
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
        },
        None,
    )


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
    if not re.match(r"^\d{4}-\d{2}$", text):
        return None
    return text


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
    block_rows_payload = payload.get("_table_raw_blocks")
    if isinstance(block_rows_payload, list) and block_rows_payload:
        merged_rows: list[list[str]] = []
        for block in block_rows_payload:
            if not isinstance(block, str) or not block.strip():
                continue
            rows = rows_from_markdown(block, template)
            if not rows:
                continue
            merged_rows.extend([[_field_value_to_str(cell) for cell in row] for row in rows])
        if merged_rows:
            return merged_rows

    table_rows = payload.get("table_rows")
    if isinstance(table_rows, list):
        normalized: list[list[str]] = []
        for row in table_rows:
            if not isinstance(row, list):
                continue
            normalized.append([_field_value_to_str(cell) for cell in row])
        if normalized:
            return normalized

    table_raw = payload.get("table_raw")
    if isinstance(table_raw, str) and table_raw.strip():
        table_raw = _normalize_table_raw_text(table_raw)
        rows = rows_from_markdown(table_raw, template)
        if rows:
            return [[_field_value_to_str(cell) for cell in row] for row in rows]

    rows_payload = payload.get("rows")
    if isinstance(rows_payload, list):
        normalized = []
        for row in rows_payload:
            if isinstance(row, list):
                normalized.append([_field_value_to_str(cell) for cell in row])
        if normalized:
            return normalized

    nested = payload.get("table")
    if isinstance(nested, dict):
        nested_rows = nested.get("rows")
        if isinstance(nested_rows, list):
            normalized = []
            for row in nested_rows:
                if isinstance(row, list):
                    normalized.append([_field_value_to_str(cell) for cell in row])
            if normalized:
                return normalized

    return []


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
        )
        mapped_lines, mapped_rows = _apply_menu_position_mapping_safe(
            lines,
            week_id,
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
        month_id = _to_sheet_month_id(value)
        if month_id and month_id not in target:
            target.append(month_id)

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
        return bool(_build_position_menu_entries(month_id))

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
    # Guard against OCR garbage values (e.g. 3000/8000) that frequently appear
    # in free text/noisy rows and must not be applied as meal counts.
    try:
        max_abs = float(os.getenv("OCR_SHEET_MAX_QTY", "150"))
    except Exception:
        max_abs = 150.0
    if parsed < 0:
        return None
    if max_abs > 0 and abs(parsed) > max_abs:
        return None
    return parsed


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
        if qty is None:
            continue
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
                # Only allow tiny 2-row clusters to be mirrored from the known row.
                should_fill = cluster_len == 2 and missing == 1
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
            qty = _parse_sheet_quantity_cell(payload_row[col_idx])
            if qty is None:
                continue
            _set_row_quantity_value(values, col_idx, qty)
            applied_qty = True

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

    for row_idx, payload_idx in row_mapping.items():
        if row_idx < 0 or row_idx >= len(rows):
            continue
        if payload_idx < 0 or payload_idx >= len(payload_rows):
            continue
        payload_row = payload_rows[payload_idx]
        if not isinstance(payload_row, list):
            continue
        target = rows[row_idx]
        values = target.get("values")
        if not isinstance(values, list):
            continue

        parsed_quantities: list[tuple[int, float]] = []
        for col_idx in mapped_quantity_columns:
            if col_idx >= len(payload_row):
                continue
            qty = _parse_sheet_quantity_cell(payload_row[col_idx])
            if qty is None:
                continue
            parsed_quantities.append((col_idx, qty))

        filtered_quantities: list[tuple[int, float]] = []
        for col_idx, qty in parsed_quantities:
            others = [value for other_col, value in parsed_quantities if other_col != col_idx and value > 0]
            col_hits = int(quantity_hits.get(col_idx, 0))
            is_sparse_column = max_hit > 0 and col_hits < sparse_threshold
            is_spike = bool(others) and qty >= 10 and qty > (max(others) * 2.5)
            if is_sparse_column and is_spike:
                continue
            filtered_quantities.append((col_idx, qty))
        if not filtered_quantities:
            filtered_quantities = parsed_quantities

        applied_qty = False
        for col_idx, qty in filtered_quantities:
            _set_row_quantity_value(values, col_idx, qty)
            applied_qty = True

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

    cluster_filled = _fill_cluster_consensus_quantities(
        rows=rows,
        fields=fields,
        quantity_columns=mapped_quantity_columns,
    )
    if cluster_filled > 0:
        stage_counts["cluster_fill"] = int(stage_counts.get("cluster_fill", 0)) + cluster_filled

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


def get_ocr_sheet(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        facility_id = order.facility_code
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
        raw_order_lines = (
            session.execute(select(OrderLine).where(OrderLine.order_id == order_id))
            .scalars()
            .all()
        )
        order_lines = [
            {
                "id": line.id,
                "date": line.date,
                "daypart": line.daypart,
                "menu_name": line.menu_name,
                "diet_type": line.diet_type,
                "area_id": line.area_id,
                "quantity_original": line.quantity_original,
                "quantity_corrected": line.quantity_corrected,
                "change_note": line.change_note,
            }
            for line in raw_order_lines
        ]

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
    payload, _ = get_ocr_output(order_id, persist_cache=False)
    if isinstance(payload, dict):
        ocr_payload = payload

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

    if not isinstance(ocr_payload, dict):
        # Weekly menu master missing: build editable rows from this order's OCR table.
        if not _build_position_menu_entries(resolved_week_id):
            payload, _ = get_ocr_output(order_id, persist_cache=False)
            if isinstance(payload, dict):
                ocr_payload = payload

    sheet_lines = list(order_lines)
    if not sheet_lines and isinstance(ocr_payload, dict):
        sheet_lines = _build_sheet_lines_from_ocr_payload(
            payload=ocr_payload,
            template=template,
            received_at=received_at,
            week_id=resolved_week_id,
            facility_id=facility_id,
        )

    entries, entry_source = _build_sheet_menu_entries(
        week_id=resolved_week_id,
        ocr_payload=ocr_payload,
        template=template,
        received_at=received_at,
    )
    if not entries:
        return None, "menu_entries_missing"

    line_dates = {
        line.get("date")
        for line in order_lines
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
        line_dates=line_dates,
        source=entry_source,
        payload_dates=payload_dates,
    )
    if not rows:
        return None, "menu_entries_missing"

    if source == "weekly_menu":
        missing_week_dates = _collect_missing_weekly_menu_dates(
            entries=entries,
            rows=rows,
            line_dates=line_dates,
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
    payload_rows: list[list[str]] = []
    payload_unstructured_qty: list[str] = []
    if isinstance(ocr_payload, dict):
        payload_rows = _extract_sheet_rows_from_payload(ocr_payload, template)
        payload_unstructured_qty = _extract_payload_unstructured_quantity_candidates(ocr_payload)

    base_rows = _clone_sheet_rows(rows)
    mapped_count = 0
    mapped_mode = "identity"
    rows = _clone_sheet_rows(base_rows)
    sheet_warnings: list[str] = []

    def _append_sheet_warning(code: str) -> None:
        token = str(code or "").strip()
        if token and token not in sheet_warnings:
            sheet_warnings.append(token)

    # Weekly menu + template is the primary source of truth.
    # When weekly menu is available, keep non-numeric cells from weekly menu only.
    # If persisted order lines exist, those quantities are authoritative.
    # OCR payload numeric rescue is used only when persisted order lines are absent.
    if source == "weekly_menu":
        if order_lines:
            candidate_rows: list[tuple[int, int, str, list[dict[str, Any]]]] = []

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
            candidate_rows.append((identity_count, 0, "identity", rows_by_identity))

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
            candidate_rows.append((source_index_count, 1, "source_row", rows_by_source_index))

            mapped_count, _mapped_priority, mapped_mode, mapped_rows = max(
                candidate_rows,
                key=lambda item: (item[0], item[1]),
            )
            rows = mapped_rows
            if mapped_count == 0 and payload_rows:
                rows_by_payload_index = _clone_sheet_rows(base_rows)
                payload_match_stats = _apply_payload_quantities_numeric_only(
                    rows=rows_by_payload_index,
                    fields=fields,
                    quantity_index=quantity_index,
                    payload_rows=payload_rows,
                    payload_unstructured_qty=payload_unstructured_qty,
                    allow_heuristics=False,
                )
                payload_mapped_count = _count_non_empty_quantity_cells(
                    rows=rows_by_payload_index,
                    quantity_index=quantity_index,
                )
                if payload_mapped_count > 0:
                    logger.warning(
                        "Applied OCR payload numeric-only fallback after empty order-line quantity mapping",
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
                    _append_sheet_warning("sheet_order_lines_unmapped_fallback_payload")
                    mapped_count = payload_mapped_count
                    mapped_mode = "payload_row"
                    rows = rows_by_payload_index
        elif payload_rows:
            rows_by_payload_index = _clone_sheet_rows(base_rows)
            payload_match_stats = _apply_payload_quantities_numeric_only(
                rows=rows_by_payload_index,
                fields=fields,
                quantity_index=quantity_index,
                payload_rows=payload_rows,
                payload_unstructured_qty=payload_unstructured_qty,
                allow_heuristics=False,
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
        candidate_rows: list[tuple[int, int, str, list[dict[str, Any]]]] = []

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
        candidate_rows.append((identity_count, 0, "identity", rows_by_identity))

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
        candidate_rows.append((source_index_count, 1, "source_row", rows_by_source_index))

        if payload_rows:
            rows_by_payload_index = _clone_sheet_rows(base_rows)
            _apply_payload_cells_by_menu_priority(
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
            candidate_rows.append((payload_index_count, 2, "payload_row", rows_by_payload_index))

        mapped_count, _mapped_priority, mapped_mode, mapped_rows = max(
            candidate_rows,
            key=lambda item: (item[0], item[1]),
        )
        rows = mapped_rows

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

    # If we are not using payload-row mapping, validate order-line column compatibility.
    if order_lines and mapped_mode != "payload_row":
        unmapped_quantity_lines = _collect_unmapped_quantity_lines(
            order_lines=order_lines,
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

    if source == "weekly_menu" and mapped_mode == "payload_row":
        source = "weekly_menu+ocr_payload"
    if source == "ocr_table" and mapped_mode == "payload_row":
        source = "ocr_table+ocr_payload"

    trace_rows = _build_sheet_trace_rows(
        rows=rows,
        fields=fields,
        quantity_index=quantity_index,
        source=source,
        mapped_mode=mapped_mode,
        has_order_lines=bool(order_lines),
    )
    header = [_field_label(field) for field in fields]
    return (
        {
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
            "trace": {
                "rows": trace_rows,
                "mapped_mode": mapped_mode,
            },
        },
        None,
    )


def get_ocr_edit_history(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
    payload = _load_order_ocr_cache(order_id)
    if not isinstance(payload, dict):
        return {"order_id": order_id, "latest": None, "revisions": [], "raw_output": None}, None
    edited = payload.get("_edited_ocr")
    if not isinstance(edited, dict):
        return {"order_id": order_id, "latest": None, "revisions": [], "raw_output": None}, None
    revisions = edited.get("revisions")
    if not isinstance(revisions, list):
        revisions = []
    revisions = [item for item in revisions if isinstance(item, dict)]
    return {
        "order_id": order_id,
        "latest": edited.get("latest") if isinstance(edited.get("latest"), dict) else None,
        "revisions": revisions,
        "raw_output": edited.get("raw_output") if isinstance(edited.get("raw_output"), dict) else None,
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
    if isinstance(payload, dict):
        edited = payload.get("_edited_ocr")
        revisions = edited.get("revisions") if isinstance(edited, dict) else None
        if isinstance(revisions, list):
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


def _build_llm_assist_prompt(
    *,
    provider: str,
    template: dict,
    pipeline_output: dict | None,
    llm_assist: bool,
) -> str | None:
    prompt_key = "openai_ocr_prompt" if provider == "openai" else "gemini_ocr_prompt"
    base_custom = str(template.get(prompt_key) or "").strip()
    sections: list[str] = []
    if base_custom:
        sections.append(f"Facility-specific instruction:\n{base_custom}")
    if llm_assist:
        sections.append(
            "Second-pass OCR mode:\n"
            "- Use the fax image as the primary source of truth.\n"
            "- Use the first-pass OCR result only as a hint.\n"
            "- Keep row order stable.\n"
            "- Fill missing cells when readable; keep empty string when unreadable.\n"
            "- If a handwritten quantity is unreadable, infer only from nearby recognized quantities when continuity is clear; otherwise keep empty string.\n"
            "- If a parenthesis/bracket mark spans multiple quantity cells with one number, copy that number to every covered cell.\n"
            "- If arrows/vertical range lines indicate a number applies to a span, copy that number to all cells in that span.\n"
            "- Apply copying/inference only within the clearly indicated range.\n"
            "- Quantity cells must contain digits only.\n"
            "- Return strict JSON only."
        )
        if isinstance(pipeline_output, dict):
            table_raw = pipeline_output.get("table_raw")
            if isinstance(table_raw, str) and table_raw.strip():
                sections.append(
                    "First-pass OCR hint (markdown table):\n"
                    f"{_truncate_assist_text(table_raw.strip(), max_chars=7000)}"
                )
            rows = pipeline_output.get("rows")
            if isinstance(rows, list) and rows:
                try:
                    rows_text = json.dumps(rows[:80], ensure_ascii=False)
                except TypeError:
                    rows_text = ""
                if rows_text:
                    sections.append(
                        "First-pass OCR hint (structured rows):\n"
                        f"{_truncate_assist_text(rows_text, max_chars=4000)}"
                    )
    if not sections:
        return None
    return "\n\n".join(sections)


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
    if quality_error not in {"sheet_row_coverage_low", "sheet_column_anomaly"}:
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
        "- Keep quantity columns independent and never swap values across columns.\n"
        "- Quantity fields must be digits only; unreadable cells must be empty string.\n"
        "- Copy numbers across cells only when explicit span marks exist.\n"
        "- Return strict JSON only."
    )
    if expected_row_count > 0:
        hard_rules += (
            f"\n- Output EXACTLY {expected_row_count} table body rows."
            f"\n- row_index must be continuous 0..{max(expected_row_count - 1, 0)} with no gaps."
            f"\n- Missing row indexes from first pass: {missing_hint}."
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
    if summary_text:
        user_sections.append(
            "Failure focus locations and first-pass inference summary:\n"
            f"{summary_text}"
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
    llm_quantity_only_merge: dict[str, Any] | None = None,
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
    if isinstance(llm_quantity_only_merge, dict) and llm_quantity_only_merge:
        payload["llm_quantity_only_merge"] = llm_quantity_only_merge
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
    ocr_provider: str | None = None,
    llm_assist: bool = False,
):
    config_service.reload_configs()
    before_count = 0
    before_digest = ""
    existing_week_code = None
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
    if llm_assist or requested_provider in {"openai", "gemini"}:
        # LLM OCR frequently returns merged rows where date/daypart/menu are omitted
        # in quantity-only rows. Enable fill-forward parsing in reparse path.
        template_to_use.setdefault("large_cell_mode", True)
        template_to_use.setdefault("fill_missing_date_with_hint", True)
    if requested_provider:
        template_to_use["main_ocr_provider"] = requested_provider
        template_to_use["_force_main_ocr_provider"] = requested_provider
        if requested_provider == "openai":
            template_to_use["openai_ocr_enabled"] = True
        elif requested_provider == "gemini":
            template_to_use["gemini_ocr_enabled"] = True
    main_provider = str(
        requested_provider
        or os.getenv("OCR_MAIN_PROVIDER")
        or template_to_use.get("main_ocr_provider")
        or "pipeline"
    ).lower()
    llm_quantity_only_requested = bool(
        llm_assist
        or requested_provider in {"openai", "gemini"}
        or main_provider in {"openai", "gemini"}
    )
    if llm_quantity_only_requested:
        template_to_use["llm_quantity_only_mode"] = True

    pdf_bytes = load_bytes_from_uri(document_uri)
    ocr_job_id = f"OCR-{order_id}"
    _, created = create_job(ocr_job_id, input_reference=document_uri)
    if not created:
        update_job(
            ocr_job_id,
            status="running",
            error_message=None,
            template_id=None,
            output_reference=None,
            metrics=None,
            input_reference=document_uri,
        )
    preferred_template_id = facility_config.get("fax_template_id")
    pipeline_output_ref = _run_roi_ocr_pipeline(
        job_id=ocr_job_id,
        pdf_bytes=pdf_bytes,
        facility_id=facility_id,
        input_reference=document_uri,
        preferred_template_id=preferred_template_id,
    )
    pipeline_output_payload: dict | None = None
    pipeline_rows_for_rescue: list[list[str]] = []
    position_entries_for_existing_week: list[dict] = []
    expected_weekly_row_count = 0
    if existing_week_code:
        if stable_existing_anchor_scope:
            position_entries_for_existing_week = _build_position_entries_for_lines(
                week_id=existing_week_code,
                lines=existing_line_anchors,
            )
        if not position_entries_for_existing_week:
            position_entries_for_existing_week = _build_position_menu_entries(existing_week_code)
        expected_weekly_row_count = len(position_entries_for_existing_week)
    if llm_assist or requested_provider in {"openai", "gemini"} or (ocr_prompt and main_provider in {"openai", "gemini"}):
        pipeline_output_payload = _load_pipeline_output_with_retry(pipeline_output_ref)
        if isinstance(pipeline_output_payload, dict):
            pipeline_rows_for_rescue = _extract_sheet_rows_from_payload(pipeline_output_payload, template_to_use)
            if position_entries_for_existing_week and not stable_existing_anchor_scope:
                payload_dates = {
                    item
                    for item in _collect_sheet_dates_from_payload(pipeline_output_payload, received_at)
                    if isinstance(item, date)
                }
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
            )
        effective_provider = requested_provider or main_provider
        if effective_provider in {"openai", "gemini"}:
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
    try:
        extracted = extract_fax_data(
            pdf_bytes,
            template_to_use,
            facility_id=facility_id,
            preferred_template_id=preferred_template_id,
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
            )
        if llm_quantity_only_active:
            provider_debug["quantity_only_mode"] = True
            extracted.provider_debug = provider_debug
        if llm_quantity_only_active and pipeline_rows_for_rescue:
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
            )
            quality_error, quality_detail = _evaluate_quantity_only_rows_quality(
                rows=[list(row) for row in rows if isinstance(row, list)],
                template=template_to_use,
                expected_row_count=expected_weekly_row_count,
                reference_rows=pipeline_rows_for_rescue,
            )
            reparse_quality_error = quality_error
            reparse_quality_detail = quality_detail

            repair_enabled = str(
                os.getenv("OCR_REPARSE_ENABLE_REPAIR_PASS", "1")
            ).strip().lower() not in {"0", "false", "no", "off"}
            if repair_enabled and reparse_quality_error in {"sheet_row_coverage_low", "sheet_column_anomaly"}:
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
                repair_system_prompt, repair_user_prompt = _build_quantity_only_repair_prompts(
                    provider=main_provider,
                    template=repair_template,
                    current_rows=[list(row) for row in rows if isinstance(row, list)],
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
                    repaired_extracted = extract_fax_data(
                        pdf_bytes,
                        repair_template,
                        facility_id=facility_id,
                        preferred_template_id=preferred_template_id,
                    )
                    repaired_rows = repaired_extracted.table_rows or []
                    repaired_tokens = repaired_extracted.tokens or []
                    repaired_date_strings = repaired_extracted.date_strings or date_strings
                    repaired_grid = repaired_extracted.grid or grid
                    repaired_quality_error, repaired_quality_detail = _evaluate_quantity_only_rows_quality(
                        rows=[list(row) for row in repaired_rows if isinstance(row, list)],
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
            update_job(ocr_job_id, status="failed", error_message=f"main_ocr_failed:{main_provider}")
            return None, f"main_ocr_failed:{main_provider}"
        date_strings = cached.get("date_strings") or []
        rows = cached.get("table_rows") or []
        tokens = cached.get("tokens") or []
        tokens = filter_tokens_by_box(tokens, template_to_use.get("table_box"))
        grid = detect_table_grid(pdf_bytes, template_to_use)
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
        or requested_provider in {"openai", "gemini"}
        or main_provider in {"openai", "gemini"}
    )
    reparse_quantity_rules = _build_reparse_quantity_rules(
        policy.get("quantity_rules", {}),
        strict_llm_quantity=strict_llm_quantity,
    )
    lines = parse_order_lines(
        rows,
        template_to_use,
        received_at,
        reparse_quantity_rules,
        default_date=default_date,
        tokens=tokens,
        grid=grid.__dict__ if grid else None,
        pdf_bytes=pdf_bytes,
    )
    parsed_output_for_debug = pipeline_output_payload if isinstance(pipeline_output_payload, dict) else None
    if not lines:
        try:
            output_ref = pipeline_output_ref
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
                            pdf_bytes=None,
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
        )
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
                "primary_model": llm_primary_model,
                "repair_pass_applied": bool(llm_repair_pass_applied),
                "repair_pass_reason": llm_repair_pass_reason,
                "repair_pass_error": llm_repair_pass_error,
                "repair_pass_model": llm_repair_pass_model,
                "quality_error": reparse_quality_error,
                "quality_detail": reparse_quality_detail or {},
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
        lines=week_resolution_lines,
        rows=[list(row) for row in rows if isinstance(row, list)],
        parsed_output=week_payload,
        existing_lines=existing_line_anchors,
        extra_payload_dates=pipeline_payload_dates,
        received_at=received_at,
    )
    if (
        main_provider in {"openai", "gemini"}
        and llm_quantity_only_active
        and not llm_rows_replaced_with_pipeline
    ):
        week_menu_row_count = len(_build_position_menu_entries(week_id)) if week_id else 0
        quality_expected_row_count = _resolve_llm_expected_row_count(
            menu_expected_row_count=len(reparse_position_entries),
            fallback_expected_row_count=expected_weekly_row_count or week_menu_row_count,
            pipeline_rows=pipeline_rows_for_rescue,
            observed_rows=[list(row) for row in rows if isinstance(row, list)],
        )
        if quality_expected_row_count > 0:
            reparse_quality_error, reparse_quality_detail = _evaluate_quantity_only_rows_quality(
                rows=[list(row) for row in rows if isinstance(row, list)],
                template=template_to_use,
                expected_row_count=quality_expected_row_count,
                reference_rows=pipeline_rows_for_rescue,
            )
    enable_position_mapping = bool(template_to_use.get("map_menu_by_position", True))
    mapped_rows = 0
    if enable_position_mapping:
        lines, mapped_rows = _apply_menu_position_mapping_safe(
            lines,
            week_id,
            entries_override=reparse_position_entries if reparse_position_entries else None,
        )
    if mapped_rows <= 0:
        lines = _apply_menu_matching(lines, week_id, facility_id, min_ratio)
    validation_error, validation_detail = _validate_reparse_lines_against_weekly_menu(
        lines=lines,
        week_id=week_id,
        ocr_rows=rows,
        template=template_to_use,
        entries_override=reparse_position_entries if reparse_position_entries else None,
    )
    date_anchor_error: str | None = None
    date_anchor_detail: dict[str, Any] | None = None
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
    if not validation_error and reparse_quality_error:
        validation_error = reparse_quality_error
        validation_detail = reparse_quality_detail or {}
    if validation_error:
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
        )
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("Order OCR cache update failed on validation reject", order_id=order_id, error=str(exc))
        update_job(
            ocr_job_id,
            status="failed",
            error_message=validation_error,
            metrics={
                "provider": main_provider,
                "requested_provider": requested_provider or None,
                "row_count": len(rows),
                "line_count": len(lines),
                "llm_assist": bool(llm_assist),
                "finish_reason": llm_finish_reason,
                "truncated_output": bool(llm_truncated_output),
                "rows_replaced_with_pipeline": bool(llm_rows_replaced_with_pipeline),
                "llm_quantity_only_merge": llm_quantity_only_merge_stats or None,
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
            },
        )
        return None, validation_error
    after_digest = _line_digest(lines)
    after_count = len(lines)
    reparse_changed = before_digest != after_digest

    log_payload: dict | None = None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
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
        resolved_week_has_menu = bool(week_id and _build_position_menu_entries(week_id))
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
    update_job(
        ocr_job_id,
        status="done",
        error_message=None,
        metrics={
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
            "before_count": before_count,
            "after_count": after_count,
            "before_digest": before_digest,
            "after_digest": after_digest,
            "changed": reparse_changed,
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
        llm_quantity_only_merge=llm_quantity_only_merge_stats or None,
    )
    try:
        cache_ref = pipeline_output_ref
        if not cache_ref:
            job = get_ocr_job(ocr_job_id)
            cache_ref = job.get("output_reference") if job else None
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


def set_facility(order_id: str, facility_code: str) -> bool:
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return False
        order.facility_code = facility_code
        logger.info("Order facility set", order_id=order_id, facility_code=facility_code)
        record_event(
            "order_facility_set",
            actor="system",
            target=order_id,
            fac=facility_code,
            wek=order.week_code,
        )
        return True


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
    if order.week_code:
        try:
            items = menu_service.get_menu_items_for_facility(order.week_code, order.facility_code)
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
    prompt_enabled = False
    menu_meta = _build_menu_amount_meta(order)

    return {
        "id": order.id,
        "ocr_job_id": f"OCR-{order.id}",
        "facility": order.facility_code,
        "week": order.week_code,
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
    return {
        "id": order.id,
        "ocr_job_id": f"OCR-{order.id}",
        "facility": order.facility_code,
        "week": order.week_code,
        "status": order.status,
        "document": order.document_uri,
        "message_id": order.message_id,
        "received_at": order.received_at,
        "document_id": order.current_document_id,
        "superseded_document_ids": order.superseded_document_ids or [],
        "lines_updated_at": order.lines_updated_at,
    }
