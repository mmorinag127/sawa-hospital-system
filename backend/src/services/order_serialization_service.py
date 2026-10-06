from __future__ import annotations

from typing import Any
from datetime import date
from types import SimpleNamespace

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
    for month_id in sheet_week_service.sheet_week_month_ids(week_id):
        payload = (
            menu_service.get_menu_for_facility(month_id, facility_id, allow_unresolved=True)
            if facility_id
            else menu_service.get_menu(month_id, allow_unresolved=True)
        ) or {}
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            payload_item = dict(item)
            payload_item["menu_dates"] = [
                entry.get("menu_date") for entry in payload.get("entries", [])
                if menu_service._monthly_item_identity_key_from_payload(entry)[:4] == menu_service._monthly_item_identity_key_from_payload(item)[:4]
            ]
            items.append(payload_item)
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


def _line_menu_identity(line):
    return (str(line.menu_name or "").strip(), getattr(line, "daypart", None),
            getattr(line, "diet_type", None), getattr(line, "date", None))


def _line_menu_identity_missing_fields(line) -> list[str]:
    """Return identity fields that must be explicit before a named line can resolve."""
    fields = []
    if not isinstance(getattr(line, "date", None), date):
        fields.append("date")
    if not str(getattr(line, "daypart", None) or "").strip():
        fields.append("daypart")
    if not str(getattr(line, "diet_type", None) or "").strip():
        fields.append("diet_type")
    return fields


def _normalized_explicit_diet_type(value) -> str:
    return str(menu_service.normalize_diet_type(value) or "").strip()


def _matches_explicit_line_identity(item: dict, *, daypart, diet, menu_date: date) -> bool:
    item_daypart = menu_service._coerce_master_field_value("daypart", item.get("daypart"))
    line_daypart = menu_service._coerce_master_field_value("daypart", daypart)
    item_diet = str(menu_service.normalize_diet_type(item.get("diet_type")) or "regular").strip()
    return (
        item_daypart == line_daypart
        and item_diet == _normalized_explicit_diet_type(diet)
        and menu_date.isoformat() in (item.get("menu_dates") or [])
    )


MENU_AMOUNT_UNRESOLVED = "menu_amount_unresolved"
MENU_AMOUNT_MESSAGE = "月次メニューの一人量または単位を確認してください。確定するまで量計算と出力はできません。"


def _resolve_menu_amounts(order: Order) -> tuple[dict, dict]:
    affected = []
    resolution = {"status": "resolved", "blocker": None, "message": None, "affected_lines": affected}
    if not any(str(line.menu_name or "").strip() for line in order.lines or []):
        return {}, resolution
    items = []
    if order.week_code:
        items = _collect_menu_items_for_week(order.week_code, order.facility_code)
    meta = {}
    for line in order.lines or []:
        name, daypart, diet, menu_date = _line_menu_identity(line)
        if not name:
            continue
        fields = _line_menu_identity_missing_fields(line)
        if fields:
            affected.append({"line_id": getattr(line, "line_id", None),
                             "date": menu_date.isoformat() if isinstance(menu_date, date) else None,
                             "daypart": daypart, "menu_name": name, "diet_type": diet,
                             "area_id": getattr(line, "area_id", None), "unresolved_fields": fields})
            continue
        candidates = [item for item in items if str(item.get("name") or "").strip() == name]
        candidates = [
            item for item in candidates
            if _matches_explicit_line_identity(item, daypart=daypart, diet=diet, menu_date=menu_date)
        ]
        fields = []
        if not candidates:
            fields = ["qty_per_serving", "unit_type"]
        else:
            identities = {
                (item.get("month_id"), item.get("id"), item.get("menu_master_id"),
                 menu_service._monthly_item_identity_key_from_payload(item, item.get("facility_override")))
                for item in candidates
            }
            if any(not item.get("id") or not item.get("month_id") for item in candidates) or len(identities) != 1:
                fields = ["menu_identity"]
            try:
                menu_service.require_resolved_menu_items(candidates)
            except menu_service.MenuReadUnresolved:
                fields = sorted(set(fields) | {field for item in candidates for field in
                                 (menu_service._unresolved_menu_fields(item) + list(item.get("unresolved_fields") or []))})
                if not fields:
                    fields = ["menu_identity"]
        if not fields:
            values = {(float(item["qty_per_serving"]), menu_service._normalize_menu_unit_type(item["unit_type"])) for item in candidates}
            if len(values) != 1:
                fields = ["qty_per_serving", "unit_type"]
        if fields:
            affected.append({"line_id": getattr(line, "line_id", None),
                             "date": menu_date.isoformat() if menu_date else None,
                             "daypart": daypart, "menu_name": name, "diet_type": diet,
                             "area_id": getattr(line, "area_id", None), "unresolved_fields": fields})
            continue
        qty_value, unit = values.pop()
        value = {
            "qty_per_serving": qty_value,
            "unit_type": unit,
        }
        meta[_line_menu_identity(line)] = value
    if affected:
        resolution.update(status="unresolved", blocker=MENU_AMOUNT_UNRESOLVED, message=MENU_AMOUNT_MESSAGE)
    return meta, resolution


def menu_amount_resolution(order: Order) -> dict:
    return _resolve_menu_amounts(order)[1]


def candidate_menu_amount_resolution(order: Order, candidate: dict) -> dict:
    lines = []
    invalid = []
    raw_lines = candidate.get("lines") if isinstance(candidate, dict) else None
    if not isinstance(raw_lines, list) or not raw_lines:
        return {
            "status": "unresolved",
            "blocker": MENU_AMOUNT_UNRESOLVED,
            "message": MENU_AMOUNT_MESSAGE,
            "affected_lines": [{"unresolved_fields": ["candidate_lines"]}],
        }
    for raw in raw_lines:
        if not isinstance(raw, dict):
            invalid.append({"unresolved_fields": ["candidate_line"]})
            continue
        menu_date = raw.get("date")
        fields = []
        try:
            if isinstance(menu_date, str):
                menu_date = date.fromisoformat(menu_date)
            if not isinstance(menu_date, date):
                fields.append("date")
        except ValueError:
            fields.append("date")
        if not str(raw.get("menu_name") or "").strip():
            fields.append("menu_name")
        for field in ("daypart", "diet_type"):
            if not str(raw.get(field) or "").strip():
                fields.append(field)
        quantity = raw.get("quantity_corrected")
        if quantity is None:
            quantity = raw.get("quantity_original")
        if not menu_service.is_resolved_quantity(quantity):
            fields.append("quantity")
        if fields:
            invalid.append({key: raw.get(key) for key in ("line_id", "date", "daypart", "menu_name", "diet_type", "area_id")}
                           | {"unresolved_fields": fields})
            continue
        lines.append(SimpleNamespace(menu_name=raw.get("menu_name"), daypart=raw.get("daypart"),
                                     diet_type=raw.get("diet_type"), date=menu_date,
                                     area_id=raw.get("area_id"), line_id=raw.get("line_id")))
    resolution = menu_amount_resolution(SimpleNamespace(lines=lines, week_code=order.week_code,
                                                        facility_code=order.facility_code))
    if invalid:
        resolution["affected_lines"].extend(invalid)
        resolution.update(status="unresolved", blocker=MENU_AMOUNT_UNRESOLVED, message=MENU_AMOUNT_MESSAGE)
    return resolution


def _build_menu_amount_meta(order: Order) -> dict:
    meta, resolution = _resolve_menu_amounts(order)
    if resolution["status"] == "unresolved":
        raise menu_service.MenuReadUnresolved(resolution["affected_lines"])
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
    meta = menu_meta.get(_line_menu_identity(line)) or {}
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
    menu_item = menu_meta.get(_line_menu_identity(line)) or {}
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


def serialize_order(order: Order, *, allow_unresolved_menu_amount: bool = False) -> dict[str, Any]:
    prompt_enabled = True
    if allow_unresolved_menu_amount:
        menu_meta, resolution = _resolve_menu_amounts(order)
    else:
        menu_meta = _build_menu_amount_meta(order)
        resolution = {"status": "resolved", "blocker": None, "message": None, "affected_lines": []}
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
        "menu_amount_resolution": resolution,
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
