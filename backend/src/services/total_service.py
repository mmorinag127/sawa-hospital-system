from __future__ import annotations

from datetime import date
from typing import Iterable

from sqlalchemy import select

from src.db import session_scope
from src.models.order import Order
from src.services import config_service
from src.services import output_builder
from src.services.order_service import serialize_order


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


def _iter_confirmed_orders() -> Iterable[Order]:
    with session_scope() as session:
        orders = (
            session.execute(select(Order).where(Order.status == "確定"))
            .scalars()
            .all()
        )
        for order in orders:
            yield order


def build_totals(date_from: date | None, date_to: date | None) -> list[dict]:
    quantity_rules = config_service.load_ingest_policy().get("quantity_rules", {})
    zero_as_empty = quantity_rules.get("zero_as_empty", True)
    grouped: dict[tuple, dict] = {}
    for order in _iter_confirmed_orders():
        order_payload = serialize_order(order)
        order_lines = output_builder.build_order_lines_for_outputs(order_payload)
        for line in order_lines:
            line_date = _ensure_date(line.get("date"))
            if not _filter_date(line_date, date_from, date_to):
                continue
            qty = output_builder._safe_qty(line, zero_as_empty)  # noqa: SLF001
            if qty is None:
                continue
            key = (
                line_date,
                line.get("daypart"),
                line.get("menu_category"),
                line.get("menu_name"),
                line.get("diet_type"),
            )
            row = grouped.setdefault(
                key,
                {
                    "date": line_date,
                    "daypart": line.get("daypart"),
                    "menu_category": line.get("menu_category"),
                    "menu_name": line.get("menu_name"),
                    "diet_type": line.get("diet_type"),
                    "quantity": 0.0,
                },
            )
            row["quantity"] += float(qty)

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
        if row.get("date") and hasattr(row["date"], "isoformat"):
            row["date"] = row["date"].isoformat()
    return rows
