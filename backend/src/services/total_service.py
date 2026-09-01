from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable

from sqlalchemy import or_, select

from src.db import session_scope
from src.models.order import Order, OrderLine
from src.services import config_service
from src.services import order_service
from src.services import output_builder
from src.services.menu_vocabulary import bucket_diet_type_for_aggregation
from src.services.order_serialization_service import serialize_order

_MEAL_PERIOD_LABELS = {
    "朝": "朝食",
    "昼": "昼食",
    "夕": "夕食",
}


def _ensure_date(value: object) -> date | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value
    try:
        return output_builder._ensure_date(value)  # noqa: SLF001
    except Exception:
        return None


def _filter_date(value: date | None, date_from: date | None, date_to: date | None) -> bool:
    if not value:
        return False
    if date_from and value < date_from:
        return False
    if date_to and value > date_to:
        return False
    return True


def _confirmed_orders_query(date_from: date | None = None, date_to: date | None = None):
    query = select(Order).where(Order.status == "確定")
    if date_from or date_to:
        line_query = select(OrderLine.id).where(OrderLine.order_id == Order.id)
        if date_from:
            line_query = line_query.where(OrderLine.date >= date_from)
        if date_to:
            line_query = line_query.where(OrderLine.date <= date_to)
        query = query.where(line_query.exists())
    return query


def _iter_confirmed_orders(date_from: date | None = None, date_to: date | None = None) -> Iterable[Order]:
    with session_scope() as session:
        query = _confirmed_orders_query(date_from, date_to)
        orders = session.execute(query).scalars().all()
        for order in orders:
            yield order


def _resolve_facility_name(facility_id: object) -> str:
    facility_code = str(facility_id or "").strip()
    if not facility_code:
        return ""
    try:
        facility_config = config_service.get_facility_config(facility_code) or {}
    except Exception:
        facility_config = {}
    name = str(facility_config.get("facility_name") or facility_config.get("name") or "").strip()
    if name:
        return name
    try:
        facility = config_service.get_facility_by_id(facility_code) or {}
    except Exception:
        return ""
    return str(facility.get("facility_name") or facility.get("name") or "").strip()


def _serialize_order_refs(order_refs: dict[tuple[str, str, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(order_refs.values())
    rows.sort(
        key=lambda row: (
            row.get("facility_id") or "",
            row.get("order_id") or "",
            row.get("source_diet_type") or "",
        )
    )
    return rows


def _resolve_meal_period(value: object) -> str:
    normalized = order_service._normalize_daypart_key(value)  # noqa: SLF001
    if normalized in _MEAL_PERIOD_LABELS:
        return _MEAL_PERIOD_LABELS[normalized]
    raw = str(value or "").strip()
    return normalized or raw


def _line_final_quantity(line: OrderLine, zero_as_empty: bool) -> float | None:
    quantity = line.quantity_corrected
    if quantity is None:
        quantity = line.quantity_original
    if quantity is None:
        return None
    try:
        quantity_value = float(quantity)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(quantity_value):
        return None
    if zero_as_empty and quantity_value <= 0:
        return None
    return quantity_value


def build_totals(date_from: date | None, date_to: date | None, include_order_refs: bool = False) -> list[dict]:
    quantity_rules = config_service.load_ingest_policy().get("quantity_rules", {})
    zero_as_empty = quantity_rules.get("zero_as_empty", True)
    grouped: dict[tuple, dict] = {}
    facility_name_cache: dict[str, str] = {}
    for order in _iter_confirmed_orders(date_from, date_to):
        order_payload = serialize_order(order)
        order_lines = output_builder.build_order_lines_for_outputs(
            order_payload,
            include_expanded_copy=False,
            allow_stale_draft_lines=True,
        )
        order_id = str(order_payload.get("id") or order.id or "").strip()
        facility_id = str(order_payload.get("facility") or "").strip()
        for line in order_lines:
            line_date = _ensure_date(line.get("date"))
            if not _filter_date(line_date, date_from, date_to):
                continue
            qty = output_builder._safe_qty(line, zero_as_empty)  # noqa: SLF001
            if qty is None:
                continue
            aggregated_diet_type = bucket_diet_type_for_aggregation(line.get("diet_type")) or "unknown"
            key = (
                line_date,
                line.get("daypart"),
                line.get("menu_category"),
                line.get("menu_name"),
                aggregated_diet_type,
            )
            row = grouped.setdefault(
                key,
                {
                    "date": line_date,
                    "daypart": line.get("daypart"),
                    "menu_category": line.get("menu_category"),
                    "menu_name": line.get("menu_name"),
                    "diet_type": aggregated_diet_type,
                    "quantity": 0.0,
                    "_order_refs": {},
                },
            )
            row["quantity"] += float(qty)
            if include_order_refs:
                facility_name = facility_name_cache.get(facility_id)
                if facility_name is None:
                    facility_name = _resolve_facility_name(facility_id)
                    facility_name_cache[facility_id] = facility_name
                source_diet_type = str(line.get("diet_type") or "").strip() or "unknown"
                ref_key = (
                    order_id,
                    facility_id,
                    source_diet_type,
                    str(line.get("area_id") or "").strip() or "X",
                )
                ref = row["_order_refs"].setdefault(
                    ref_key,
                    {
                        "order_id": order_id,
                        "facility_id": facility_id,
                        "facility_name": facility_name,
                        "source_diet_type": source_diet_type,
                        "aggregated_diet_type": aggregated_diet_type,
                        "area_id": str(line.get("area_id") or "").strip() or "X",
                        "quantity": 0.0,
                    },
                )
                ref["quantity"] += float(qty)

    rows = list(grouped.values())
    rows.sort(
        key=lambda r: (
            r.get("date") or "",
            r.get("daypart") or "",
            r.get("menu_category") or "",
            r.get("menu_name") or "",
            r.get("diet_type") or "",
        )
    )
    for row in rows:
        order_refs = row.pop("_order_refs", {})
        if row.get("date") and hasattr(row["date"], "isoformat"):
            row["date"] = row["date"].isoformat()
        if include_order_refs:
            row["order_refs"] = _serialize_order_refs(order_refs)
    return rows


def build_daily_meal_counts(target_date: date) -> dict[str, Any]:
    quantity_rules = config_service.load_ingest_policy().get("quantity_rules", {})
    zero_as_empty = quantity_rules.get("zero_as_empty", True)
    with session_scope() as session:
        confirmed_rows = session.execute(
            select(OrderLine, Order.facility_code)
            .join(Order, Order.id == OrderLine.order_id)
            .where(
                OrderLine.date == target_date,
                Order.status == "確定",
                OrderLine.confirmed_snapshot_id.is_not(None),
            )
        ).all()
        unconfirmed_rows = session.execute(
            select(Order.id, Order.facility_code, Order.status)
            .join(OrderLine, OrderLine.order_id == Order.id)
            .where(
                OrderLine.date == target_date,
                or_(Order.status.is_(None), Order.status != "確定"),
            )
            .group_by(Order.id, Order.facility_code, Order.status)
        ).all()

    slot_quantities: dict[tuple[str, str, str, str], set[float]] = {}
    slot_facilities: dict[tuple[str, str, str, str], str | None] = {}
    for line, facility_id in confirmed_rows:
        quantity = _line_final_quantity(line, zero_as_empty)
        if quantity is None:
            continue
        daypart = _resolve_meal_period(line.daypart)
        diet_type = str(line.diet_type or "").strip()
        slot_key = (str(line.order_id), daypart, diet_type, str(line.area_id or "").strip())
        slot_quantities.setdefault(slot_key, set()).add(quantity)
        slot_facilities[slot_key] = str(facility_id).strip() if facility_id is not None else None

    totals: dict[tuple[str, str], float] = {}
    inconsistent_counts: list[dict[str, Any]] = []
    for (order_id, daypart, diet_type, area_id), quantities in slot_quantities.items():
        if len(quantities) != 1:
            inconsistent_counts.append(
                {
                    "order_id": order_id,
                    "facility_id": slot_facilities[(order_id, daypart, diet_type, area_id)],
                    "daypart": daypart,
                    "diet_type": diet_type,
                    "area_id": area_id,
                    "quantities": sorted(quantities),
                }
            )
            continue
        totals[(daypart, diet_type)] = totals.get((daypart, diet_type), 0.0) + next(iter(quantities))

    groups_by_daypart: dict[str, list[dict[str, Any]]] = {}
    for (daypart, diet_type), quantity in totals.items():
        groups_by_daypart.setdefault(daypart, []).append({"diet_type": diet_type, "quantity": quantity})
    daypart_order = {"朝食": 0, "昼食": 1, "夕食": 2}
    groups = [
        {"daypart": daypart, "counts": sorted(counts, key=lambda row: row["diet_type"])}
        for daypart, counts in sorted(groups_by_daypart.items(), key=lambda item: (daypart_order.get(item[0], 99), item[0]))
    ]

    unconfirmed_orders = [
        {
            "order_id": str(order_id or "").strip(),
            "facility_id": str(facility_id).strip() if facility_id is not None else None,
            "status": str(status).strip() if status is not None else None,
        }
        for order_id, facility_id, status in unconfirmed_rows
        if str(order_id or "").strip()
    ]
    unconfirmed_orders.sort(
        key=lambda row: (
            row.get("facility_id") or "",
            row.get("order_id") or "",
            row.get("status") or "",
        )
    )

    return {
        "date": target_date.isoformat(),
        "groups": groups,
        "unconfirmed_orders": unconfirmed_orders,
        "inconsistent_counts": sorted(
            inconsistent_counts,
            key=lambda row: (row["facility_id"] or "", row["order_id"], row["daypart"], row["diet_type"], row["area_id"]),
        ),
    }
