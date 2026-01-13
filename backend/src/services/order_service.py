from typing import Optional
from pathlib import Path
import json
import os
import hashlib
import re
import time
from urllib.parse import urlparse
from difflib import SequenceMatcher
from src.workers.ingest_mail_adapter import IngestEmailPayload
from loguru import logger
from uuid import uuid4
from datetime import date, datetime
import pandas as pd
from sqlalchemy import select, delete

from src.db import Base, engine, session_scope
from src.models.order import Order, OrderLine, OrderMenuSnapshot
from src.models.document import OrderDocument
from src.models.order_ocr_cache import OrderOcrCache
from src.models.output import Bag, LabelRow, DeliveryNote, ManufacturingAggregateRow
from src.models.ingest_job import IngestJob  # noqa: F401
from src.services.notification_service import record_event
from src.services import config_service, menu_service
from src.services.fax_extractor import extract_fax_data, filter_tokens_by_box, rows_from_markdown
from src.services.fax_parser import parse_order_lines
from src.services.ingest_policy import parse_date_string, week_id_from_dates
from src.services.storage_service import load_bytes_from_uri
from src.services.storage_service import generate_signed_url
from src.services.grid_detector import detect_table_grid
from src.services.ocr_job_service import create_job, update_job, get_job as get_ocr_job
from src.services.ocr_pipeline_service import run_ocr_pipeline

Base.metadata.create_all(bind=engine)


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


def _parse_date_value(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return pd.to_datetime(value).date()
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
                week_id = week_id_from_dates(line_dates, received_at, policy)
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
        session.refresh(order)
        return serialize_order(order)


def list_orders(status: Optional[str] = None):
    with session_scope() as session:
        query = select(Order)
        if status:
            query = query.where(Order.status == status)
        orders = session.execute(query).scalars().all()
        return [serialize_order_summary(o) for o in orders]


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


def confirm_order(order_id: str):
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
        return serialize_order(order)


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
            cache.payload = payload
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


def _load_pipeline_output_with_retry(output_ref: str | None) -> Optional[dict]:
    if not output_ref:
        return None
    try:
        wait_seconds = float(os.getenv("OCR_REPARSE_OUTPUT_WAIT_SECONDS", "15"))
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
            return json.loads(payload.decode("utf-8"))
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


def apply_ocr_markdown(order_id: str, markdown: str):
    config_service.reload_configs()
    if not markdown or not markdown.strip():
        return None, "markdown_empty"
    before_count = 0
    before_digest = ""
    existing_week_code = None
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        if not order.facility_code:
            return None, "facility_missing"
        received_at = order.received_at or pd.Timestamp.utcnow()
        facility_id = order.facility_code
        existing_week_code = order.week_code
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

    rows = rows_from_markdown(markdown, template) or []
    if not rows:
        return None, "rows_empty"

    policy = config_service.load_ingest_policy()
    lines = parse_order_lines(
        rows,
        template,
        received_at,
        policy.get("quantity_rules", {}),
    )
    if not lines:
        update_job(f"OCR-{order_id}", status="empty", error_message="lines_empty")
        return None, "lines_empty"

    min_ratio = float(policy.get("menu_match_min_ratio", 0.72))
    week_id = existing_week_code
    if not week_id:
        line_dates = [line.get("date") for line in lines if line.get("date")]
        week_id = week_id_from_dates(line_dates, received_at, policy)
    lines = _apply_menu_matching(lines, week_id, facility_id, min_ratio)

    after_digest = _line_digest(lines)
    after_count = len(lines)
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
        if not order.week_code:
            line_dates = [line.get("date") for line in lines if line.get("date")]
            if line_dates:
                week_id = week_id_from_dates(line_dates, received_at, policy)
                if week_id:
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
            metadata={"line_count": log_payload["line_count"], "source": "markdown"},
        )
    update_job(f"OCR-{order_id}", status="done", error_message=None, metrics={"source": "markdown"})
    serialized["reparse"] = {
        "before_count": before_count,
        "after_count": after_count,
        "before_digest": before_digest,
        "after_digest": after_digest,
        "provider": "markdown",
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
        return json.loads(payload.decode("utf-8"))
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
    table_raw = parsed.get("table_raw")
    if not isinstance(table_raw, str) or not table_raw.strip():
        return parsed
    candidates = config_service.match_facility_candidates(table_raw)
    if not candidates:
        return parsed
    enriched = dict(parsed)
    enriched["facility_candidates"] = candidates
    return enriched


def get_ocr_output(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        message_id = order.message_id
    job = get_ocr_job(f"OCR-{order_id}")
    parsed = _load_job_output(job, "order")
    parsed_source = "job"
    if _output_is_pending(parsed):
        parsed = None
    fallback_job = None
    if parsed is None and message_id:
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
        if _job_is_pending(active_job):
            return None, "ocr_output_pending"
        if active_job.get("output_reference"):
            return None, "ocr_output_invalid"
        return None, "ocr_output_not_found"
    if not _output_is_pending(parsed):
        _save_order_ocr_cache(order_id, parsed)
    return _attach_facility_candidates(parsed), None


def get_ocr_pages(order_id: str):
    with session_scope() as session:
        order = session.get(Order, order_id)
        if not order:
            return None, "order_not_found"
        message_id = order.message_id
    job = get_ocr_job(f"OCR-{order_id}")
    parsed = _load_job_output(job, "order")
    parsed_source = "job"
    if _output_is_pending(parsed):
        parsed = None
    fallback_job = None
    if parsed is None and message_id:
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
    return (
        {
            "order_id": order_id,
            "engine": parsed.get("engine"),
            "template_id": parsed.get("template_id"),
            "facility_id": parsed.get("facility_id"),
            "pages": pages,
            "combined": combined_urls,
        },
        None,
    )


def reparse_order(order_id: str, ocr_prompt: str | None = None):
    config_service.reload_configs()
    before_count = 0
    before_digest = ""
    existing_week_code = None
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
    main_provider = str(
        os.getenv("OCR_MAIN_PROVIDER")
        or template.get("main_ocr_provider")
        or "pipeline"
    ).lower()

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
    date_strings = []
    rows = []
    tokens = []
    grid = None
    try:
        extracted = extract_fax_data(
            pdf_bytes,
            template,
            facility_id=facility_id,
            preferred_template_id=preferred_template_id,
        )
        if extracted.ocr_provider:
            main_provider = extracted.ocr_provider
        date_strings = extracted.date_strings or []
        rows = extracted.table_rows or []
        tokens = extracted.tokens or []
        grid = extracted.grid
        _maybe_dump_reparse_debug(order_id, message_id, extracted, tokens)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Main OCR reparse failed (provider={}): {}", main_provider, str(exc))
        cached = _load_cached_ocr(message_id)
        if not cached:
            update_job(ocr_job_id, status="failed", error_message=f"main_ocr_failed:{main_provider}")
            return None, f"main_ocr_failed:{main_provider}"
        date_strings = cached.get("date_strings") or []
        rows = cached.get("table_rows") or []
        tokens = cached.get("tokens") or []
        tokens = filter_tokens_by_box(tokens, template.get("table_box"))
        grid = detect_table_grid(pdf_bytes, template)
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
    lines = parse_order_lines(
        rows,
        template,
        received_at,
        policy.get("quantity_rules", {}),
        default_date=default_date,
        tokens=tokens,
        grid=grid.__dict__ if grid else None,
        pdf_bytes=pdf_bytes,
    )
    if not lines:
        try:
            output_ref = pipeline_output_ref
            if not output_ref:
                job = get_ocr_job(ocr_job_id)
                if not job and message_id:
                    job = get_ocr_job(f"OCR-{message_id}")
                output_ref = job.get("output_reference") if job else None
            parsed_output = _load_pipeline_output_with_retry(output_ref)
            if parsed_output:
                table_raw = parsed_output.get("table_raw")
                if isinstance(table_raw, str) and table_raw.strip():
                    fallback_rows = rows_from_markdown(table_raw, template) or []
                    if fallback_rows:
                        lines = parse_order_lines(
                            fallback_rows,
                            template,
                            received_at,
                            policy.get("quantity_rules", {}),
                            default_date=default_date,
                            tokens=[],
                            grid=None,
                            pdf_bytes=None,
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallback OCR markdown parse failed", error=str(exc))
    if not lines:
        sample_row = rows[0] if rows else None
        logger.warning(
            "No lines extracted",
            provider=main_provider,
            row_count=len(rows),
            sample_row=sample_row,
        )
        update_job(ocr_job_id, status="empty", error_message="lines_empty")
        return None, "lines_empty"
    min_ratio = float(policy.get("menu_match_min_ratio", 0.72))
    week_id = existing_week_code
    if not week_id:
        line_dates = [line.get("date") for line in lines if line.get("date")]
        week_id = week_id_from_dates(line_dates, received_at, policy)
    lines = _apply_menu_matching(lines, week_id, facility_id, min_ratio)
    after_digest = _line_digest(lines)
    after_count = len(lines)

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
        if not order.week_code:
            line_dates = [line.get("date") for line in lines if line.get("date")]
            if line_dates:
                week_id = week_id_from_dates(line_dates, received_at, policy)
                if week_id:
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
    update_job(ocr_job_id, status="done", error_message=None)
    try:
        cache_ref = pipeline_output_ref
        if not cache_ref:
            job = get_ocr_job(ocr_job_id)
            cache_ref = job.get("output_reference") if job else None
        parsed_output = _load_pipeline_output_with_retry(cache_ref)
        if parsed_output:
            _save_order_ocr_cache(order_id, parsed_output)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Order OCR cache update failed", order_id=order_id, error=str(exc))
    serialized["reparse"] = {
        "before_count": before_count,
        "after_count": after_count,
        "before_digest": before_digest,
        "after_digest": after_digest,
        "provider": main_provider,
        "changed": before_digest != after_digest,
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
        data = {
            "order_id": order_id,
            "message_id": message_id,
            "ocr_provider": getattr(extracted, "ocr_provider", None),
            "ocr_error": error,
            "date_strings": getattr(extracted, "date_strings", []) if extracted else [],
            "token_count": len(tokens or []),
            "tokens": tokens,
            "raw_text": raw_text,
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


def serialize_order(order: Order):
    prompt_enabled = False

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
        "ocr_prompt_enabled": prompt_enabled,
        "lines": [
            {
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
            }
            for line in (order.lines or [])
        ],
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
    }
