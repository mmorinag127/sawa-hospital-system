from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import object_session

from src.db import session_scope
from src.models.order import Order, OrderLine
from src.models.order_version import OrderVersion
from src.services import menu_service, sheet_week_service


def _collect_menu_items_for_week(
    week_id: str | None,
    facility_id: str | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for month_id in sheet_week_service.sheet_week_month_ids(week_id):
        payload = (
            menu_service.get_menu_for_facility(month_id, facility_id)
            if facility_id
            else menu_service.get_menu(month_id)
        ) or {}
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            items.append(dict(item))
    return items


def _serialize_order_version(version: OrderVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "order_id": version.order_id,
        "version_no": version.version_no,
        "document_id": version.document_id,
        "message_id": version.message_id,
        "storage_uri": version.storage_uri,
        "facility_code": version.facility_code,
        "week_code": version.week_code,
        "received_at": version.received_at,
        "line_snapshot": version.line_snapshot or [],
        "line_count": len(version.line_snapshot or []),
        "is_current": bool(version.is_current),
        "created_at": version.created_at,
    }


def _list_order_versions(session, order_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        select(OrderVersion)
        .where(OrderVersion.order_id == order_id)
        .order_by(OrderVersion.version_no.desc(), OrderVersion.received_at.desc(), OrderVersion.id.desc())
    ).scalars().all()
    return [_serialize_order_version(row) for row in rows]


def _list_order_versions_for_serialization(order: Order) -> list[dict[str, Any]]:
    session = object_session(order)
    if session is not None:
        return _list_order_versions(session, order.id)
    with session_scope() as scoped_session:
        return _list_order_versions(scoped_session, order.id)


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
            items = _collect_menu_items_for_week(order.week_code, order.facility_code)
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
        "confirmed_snapshot_id": line.confirmed_snapshot_id,
        "line_digest": line.line_digest,
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


def serialize_order(order: Order) -> dict[str, Any]:
    prompt_enabled = True
    menu_meta = _build_menu_amount_meta(order)
    week_month_id = sheet_week_service.to_sheet_month_id(order.week_code)
    week_value = sheet_week_service.normalize_sheet_week_value(order.week_code) or week_month_id
    week_label = sheet_week_service.format_sheet_week_label(order.week_code) or week_month_id
    versions = _list_order_versions_for_serialization(order)

    return {
        "id": order.id,
        "ocr_job_id": f"OCR-{order.id}",
        "facility": order.facility_code,
        "template_version_id": order.template_version_id,
        "week": week_month_id,
        "week_value": week_value,
        "week_label": week_label,
        "status": order.status,
        "document": order.document_uri,
        "message_id": order.message_id,
        "received_at": order.received_at,
        "document_id": order.current_document_id,
        "superseded_document_ids": order.superseded_document_ids or [],
        "versions": versions,
        "version_count": len(versions),
        "current_version": next((version for version in versions if version.get("is_current")), None),
        "lines_updated_at": order.lines_updated_at,
        "archived_at": order.archived_at,
        "archived_by": order.archived_by,
        "is_archived": bool(order.archived_at),
        "ocr_prompt_enabled": prompt_enabled,
        "lines": [_serialize_line_with_amount(line, menu_meta) for line in (order.lines or [])],
    }


def serialize_order_summary(order: Order) -> dict[str, Any]:
    week_month_id = sheet_week_service.to_sheet_month_id(order.week_code)
    week_value = sheet_week_service.normalize_sheet_week_value(order.week_code) or week_month_id
    week_label = sheet_week_service.format_sheet_week_label(order.week_code) or week_month_id
    versions = _list_order_versions_for_serialization(order)
    return {
        "id": order.id,
        "ocr_job_id": f"OCR-{order.id}",
        "facility": order.facility_code,
        "template_version_id": order.template_version_id,
        "week": week_month_id,
        "week_value": week_value,
        "week_label": week_label,
        "status": order.status,
        "document": order.document_uri,
        "message_id": order.message_id,
        "received_at": order.received_at,
        "document_id": order.current_document_id,
        "superseded_document_ids": order.superseded_document_ids or [],
        "versions": versions,
        "version_count": len(versions),
        "current_version": next((version for version in versions if version.get("is_current")), None),
        "lines_updated_at": order.lines_updated_at,
        "archived_at": order.archived_at,
        "archived_by": order.archived_by,
        "is_archived": bool(order.archived_at),
    }
