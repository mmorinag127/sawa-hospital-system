from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import delete, func, or_, select

from src.db import session_scope
from src.models.document import OrderDocument
from src.models.facility import Facility, FacilityArea, FacilityConfig
from src.models.ingest_job import IngestJob
from src.models.menu import MenuItem, MenuMaster, MonthlyMenu, WeeklyMenu
from src.models.ocr_job import OcrJob
from src.models.order import Order, OrderLine, OrderMenuSnapshot
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.order_critical_decision import OrderCriticalDecision
from src.models.order_current_state import OrderCurrentState
from src.models.order_ocr_cache import OrderOcrCache
from src.models.order_ocr_evidence_run import OrderOcrEvidenceRun
from src.models.order_ocr_revision import OrderOcrRevision
from src.models.order_sheet_draft import OrderSheetDraft
from src.models.order_sheet_patch_candidate import OrderSheetPatchCandidate
from src.models.order_workflow_state import OrderWorkflowState
from src.models.output import Bag, DeliveryNote, LabelRow, ManufacturingAggregateRow
from src.models.uploaded_pdf import UploadedPdf, UploadedPdfAttempt
from src.services.storage_service import load_bytes_from_uri


_ORDER_SCOPED_MODELS: list[tuple[str, Any, Any]] = [
    ("label_rows", LabelRow, LabelRow.order_id),
    ("bags", Bag, Bag.order_id),
    ("delivery_notes", DeliveryNote, DeliveryNote.order_id),
    ("order_menu_snapshots", OrderMenuSnapshot, OrderMenuSnapshot.order_id),
    ("order_lines", OrderLine, OrderLine.order_id),
    ("order_current_states", OrderCurrentState, OrderCurrentState.order_id),
    ("order_critical_decisions", OrderCriticalDecision, OrderCriticalDecision.order_id),
    ("order_workflow_states", OrderWorkflowState, OrderWorkflowState.order_id),
    ("order_confirmed_snapshots", OrderConfirmedSnapshot, OrderConfirmedSnapshot.order_id),
    ("order_sheet_drafts", OrderSheetDraft, OrderSheetDraft.order_id),
    ("order_sheet_patch_candidates", OrderSheetPatchCandidate, OrderSheetPatchCandidate.order_id),
    ("order_ocr_evidence_runs", OrderOcrEvidenceRun, OrderOcrEvidenceRun.order_id),
    ("order_ocr_revisions", OrderOcrRevision, OrderOcrRevision.order_id),
    ("order_ocr_cache", OrderOcrCache, OrderOcrCache.order_id),
    ("order_documents", OrderDocument, OrderDocument.source_email_id),
]

_CANONICAL_TABLES_TO_PRESERVE: list[tuple[str, Any]] = [
    ("facilities", Facility),
    ("facility_areas", FacilityArea),
    ("facility_configs", FacilityConfig),
    ("weekly_menus", WeeklyMenu),
    ("monthly_menus", MonthlyMenu),
    ("menu_items", MenuItem),
    ("menu_masters", MenuMaster),
]


@dataclass(frozen=True)
class CleanupScope:
    all_orders: bool = False
    received_from: date | None = None
    received_to: date | None = None


def _received_bounds(scope: CleanupScope) -> tuple[datetime | None, datetime | None]:
    start = datetime.combine(scope.received_from, time.min) if scope.received_from else None
    end = datetime.combine(scope.received_to, time.max) if scope.received_to else None
    return start, end


def _order_scope_filter(scope: CleanupScope) -> list[Any]:
    filters: list[Any] = []
    start, end = _received_bounds(scope)
    if start is not None:
        filters.append(Order.received_at >= start)
    if end is not None:
        filters.append(Order.received_at <= end)
    return filters


def _uploaded_pdf_scope_filter(order_ids: list[str], message_ids: list[str]) -> list[Any]:
    filters: list[Any] = []
    if order_ids:
        filters.append(UploadedPdf.current_order_id.in_(order_ids))
    if message_ids:
        filters.append(UploadedPdf.message_id.in_(message_ids))
    return filters


def _count_model(session: Any, model: Any, predicate: Any | None = None) -> int:
    query = select(func.count()).select_from(model)
    if predicate is not None:
        query = query.where(predicate)
    return int(session.execute(query).scalar_one_or_none() or 0)


def build_order_cleanup_plan(scope: CleanupScope) -> dict[str, Any]:
    if not scope.all_orders and scope.received_from is None and scope.received_to is None:
        raise ValueError("cleanup scope must specify all_orders or a received date range")

    with session_scope() as session:
        query = select(Order)
        if not scope.all_orders:
            for filter_item in _order_scope_filter(scope):
                query = query.where(filter_item)
        orders = list(session.execute(query.order_by(Order.received_at.asc(), Order.id.asc())).scalars().all())
        order_ids = [str(order.id) for order in orders]
        message_ids = [str(order.message_id) for order in orders if str(order.message_id or "").strip()]
        document_uris = [str(order.document_uri) for order in orders if str(order.document_uri or "").strip()]

        counts: dict[str, int] = {"orders": len(order_ids)}
        for table_name, model, column in _ORDER_SCOPED_MODELS:
            if table_name == "order_documents":
                predicate = column.in_(message_ids) if message_ids else None
                counts[table_name] = _count_model(session, model, predicate) if predicate is not None else 0
            else:
                counts[table_name] = _count_model(session, model, column.in_(order_ids)) if order_ids else 0

        uploaded_filters = _uploaded_pdf_scope_filter(order_ids, message_ids)
        uploaded_predicate = or_(*uploaded_filters) if uploaded_filters else None
        uploaded_ids = []
        if uploaded_predicate is not None:
            uploaded_ids = [
                str(row.id)
                for row in session.execute(
                    select(UploadedPdf.id).where(uploaded_predicate)
                ).all()
            ]
        counts["uploaded_pdfs"] = len(uploaded_ids)
        counts["uploaded_pdf_attempts"] = (
            _count_model(session, UploadedPdfAttempt, UploadedPdfAttempt.uploaded_pdf_id.in_(uploaded_ids))
            if uploaded_ids
            else 0
        )

        counts["ocr_jobs"] = _count_model(
            session,
            OcrJob,
            or_(
                *(filter_item for filter_item in [
                    OcrJob.id.in_([f"OCR-{order_id}" for order_id in order_ids]) if order_ids else None,
                    OcrJob.input_reference.in_(document_uris) if document_uris else None,
                ] if filter_item is not None)
            ) if order_ids or document_uris else None,
        ) if order_ids or document_uris else 0
        counts["ingest_jobs"] = _count_model(
            session,
            IngestJob,
            IngestJob.id.in_(message_ids),
        ) if message_ids else 0
        counts["manufacturing_aggregate_rows"] = _count_model(session, ManufacturingAggregateRow)

        preserve_counts = {
            table_name: _count_model(session, model)
            for table_name, model in _CANONICAL_TABLES_TO_PRESERVE
        }

    return {
        "scope": {
            "all_orders": scope.all_orders,
            "received_from": scope.received_from.isoformat() if scope.received_from else None,
            "received_to": scope.received_to.isoformat() if scope.received_to else None,
        },
        "order_ids": order_ids,
        "message_ids": message_ids,
        "document_uris": document_uris,
        "counts": counts,
        "preserved_canonical_counts": preserve_counts,
        "total_rows_targeted": int(sum(counts.values())),
    }


def _safe_filename(order_id: str, uri: str, index: int) -> str:
    suffix = Path(urlparse(uri).path).suffix or ".pdf"
    digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:10]
    return f"{index:03d}_{order_id}_{digest}{suffix}"


def export_order_pdfs_for_cleanup(
    *,
    scope: CleanupScope,
    output_dir: str | Path,
) -> dict[str, Any]:
    plan = build_order_cleanup_plan(scope)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, Any]] = []
    for index, (order_id, uri) in enumerate(zip(plan["order_ids"], plan["document_uris"]), start=1):
        filename = _safe_filename(order_id, uri, index)
        target = target_dir / filename
        item = {
            "order_id": order_id,
            "source_uri": uri,
            "target_path": str(target),
            "status": "pending",
            "bytes": 0,
            "error": None,
        }
        try:
            parsed = urlparse(uri)
            if parsed.scheme in ("", "file"):
                source_path = Path(parsed.path if parsed.scheme else uri)
                shutil.copyfile(source_path, target)
            else:
                target.write_bytes(load_bytes_from_uri(uri))
            item["bytes"] = target.stat().st_size
            item["status"] = "exported"
        except Exception as exc:  # pragma: no cover - depends on external storage credentials
            item["status"] = "failed"
            item["error"] = str(exc)
        items.append(item)

    manifest = {
        "exported_at": datetime.utcnow().isoformat(),
        "scope": plan["scope"],
        "items": items,
        "exported_count": len([item for item in items if item["status"] == "exported"]),
        "failed_count": len([item for item in items if item["status"] == "failed"]),
    }
    (target_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def apply_order_cleanup(scope: CleanupScope, *, confirm_token: str) -> dict[str, Any]:
    if confirm_token != "CLEAN_ORDER_DATA":
        raise ValueError("confirm_token must be CLEAN_ORDER_DATA")
    plan = build_order_cleanup_plan(scope)
    order_ids = list(plan["order_ids"])
    message_ids = list(plan["message_ids"])
    document_uris = list(plan["document_uris"])
    if not order_ids:
        return {**plan, "applied": True, "removed": {}}

    removed: dict[str, int] = {}
    with session_scope() as session:
        for table_name, model, column in _ORDER_SCOPED_MODELS:
            if table_name == "order_documents":
                predicate = column.in_(message_ids) if message_ids else None
            else:
                predicate = column.in_(order_ids)
            if predicate is None:
                removed[table_name] = 0
                continue
            result = session.execute(delete(model).where(predicate))
            removed[table_name] = int(result.rowcount or 0)

        uploaded_filters = _uploaded_pdf_scope_filter(order_ids, message_ids)
        uploaded_ids: list[str] = []
        if uploaded_filters:
            uploaded_predicate = or_(*uploaded_filters)
            uploaded_ids = [
                str(row.id)
                for row in session.execute(select(UploadedPdf.id).where(uploaded_predicate)).all()
            ]
            if uploaded_ids:
                result = session.execute(
                    delete(UploadedPdfAttempt).where(
                        UploadedPdfAttempt.uploaded_pdf_id.in_(uploaded_ids)
                    )
                )
                removed["uploaded_pdf_attempts"] = int(result.rowcount or 0)
            else:
                removed["uploaded_pdf_attempts"] = 0
            result = session.execute(delete(UploadedPdf).where(uploaded_predicate))
            removed["uploaded_pdfs"] = int(result.rowcount or 0)

        ocr_predicates = []
        if order_ids:
            ocr_predicates.append(OcrJob.id.in_([f"OCR-{order_id}" for order_id in order_ids]))
        if document_uris:
            ocr_predicates.append(OcrJob.input_reference.in_(document_uris))
        if ocr_predicates:
            result = session.execute(delete(OcrJob).where(or_(*ocr_predicates)))
            removed["ocr_jobs"] = int(result.rowcount or 0)
        else:
            removed["ocr_jobs"] = 0

        if message_ids:
            result = session.execute(delete(IngestJob).where(IngestJob.id.in_(message_ids)))
            removed["ingest_jobs"] = int(result.rowcount or 0)
        else:
            removed["ingest_jobs"] = 0

        result = session.execute(delete(ManufacturingAggregateRow))
        removed["manufacturing_aggregate_rows"] = int(result.rowcount or 0)

        result = session.execute(delete(Order).where(Order.id.in_(order_ids)))
        removed["orders"] = int(result.rowcount or 0)

    return {**plan, "applied": True, "removed": removed}
