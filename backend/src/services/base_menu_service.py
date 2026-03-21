from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import delete, inspect, select, text

from src.db import engine, session_scope
from src.models.menu import BaseMenuCycleItem
from src.services import menu_service
from src.services.menu_vocabulary import normalize_diet_type


def _ensure_base_menu_table() -> None:
    inspector = inspect(engine)
    if "base_menu_cycle_items" in inspector.get_table_names():
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS base_menu_cycle_items (
                    id VARCHAR PRIMARY KEY,
                    cycle_day INTEGER NOT NULL,
                    daypart VARCHAR NULL,
                    category VARCHAR NULL,
                    name VARCHAR NOT NULL,
                    diet_type VARCHAR NULL,
                    slot_index INTEGER NULL
                )
                """
            )
        )


_ensure_base_menu_table()


def serialize_item(item: BaseMenuCycleItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "cycle_day": item.cycle_day,
        "daypart": item.daypart,
        "category": item.category,
        "name": item.name,
        "diet_type": normalize_diet_type(item.diet_type),
        "slot_index": item.slot_index,
        "unit_type": None,
        "qty_per_serving": None,
        "temp_type": None,
        "bag_max_qty": None,
        "bag_max_unit": None,
        "condiments": [],
    }


def _is_blank_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _merge_master_defaults(items: list[dict]) -> list[dict]:
    names = [str(item.get("name") or "").strip() for item in items if str(item.get("name") or "").strip()]
    if not names:
        return items
    defaults = menu_service.resolve_menu_defaults(names, None)
    if not defaults:
        return items
    merged: list[dict] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        payload = dict(item)
        default = defaults.get(name) or {}
        for field in ("unit_type", "qty_per_serving", "temp_type", "bag_max_qty", "bag_max_unit"):
            if _is_blank_value(payload.get(field)) and not _is_blank_value(default.get(field)):
                payload[field] = default.get(field)
        condiments = default.get("condiments")
        if isinstance(condiments, list) and condiments:
            payload["condiments"] = condiments
        merged.append(payload)
    return merged


def list_items(cycle_day: int | None = None) -> list[dict]:
    with session_scope() as session:
        query = select(BaseMenuCycleItem)
        if cycle_day is not None:
            query = query.where(BaseMenuCycleItem.cycle_day == cycle_day)
        items = session.execute(query).scalars().all()
        items.sort(
            key=lambda item: (
                item.cycle_day,
                item.daypart or "",
                item.slot_index if item.slot_index is not None else 999,
                item.name,
            )
        )
        payload = [serialize_item(item) for item in items]
        return _merge_master_defaults(payload)


def replace_items(items: list[dict]) -> dict:
    cleaned: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        cycle_day = raw.get("cycle_day")
        try:
            cycle_day_val = int(cycle_day)
        except Exception:
            continue
        if cycle_day_val < 1 or cycle_day_val > 45:
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        cleaned.append(
            {
                "cycle_day": cycle_day_val,
                "daypart": (raw.get("daypart") or None),
                "category": (raw.get("category") or None),
                "name": name,
                "diet_type": normalize_diet_type(raw.get("diet_type")),
                "slot_index": raw.get("slot_index"),
            }
        )

    with session_scope() as session:
        session.execute(delete(BaseMenuCycleItem))
        for entry in cleaned:
            session.add(
                BaseMenuCycleItem(
                    id=f"BMI{uuid4().hex[:8]}",
                    cycle_day=entry["cycle_day"],
                    daypart=entry.get("daypart"),
                    category=entry.get("category"),
                    name=entry["name"],
                    diet_type=entry.get("diet_type"),
                    slot_index=entry.get("slot_index"),
                )
            )
    return {"created": len(cleaned)}


def update_item(item_id: str, body: dict) -> bool:
    if not item_id:
        return False
    with session_scope() as session:
        item = session.get(BaseMenuCycleItem, item_id)
        if not item:
            return False
        if "cycle_day" in body:
            try:
                cycle_day_val = int(body.get("cycle_day"))
                if 1 <= cycle_day_val <= 45:
                    item.cycle_day = cycle_day_val
            except Exception:
                pass
        for key in ("daypart", "category", "name", "diet_type"):
            if key in body:
                value = body.get(key)
                if value is None:
                    setattr(item, key, None)
                elif key == "diet_type":
                    setattr(item, key, normalize_diet_type(value))
                else:
                    setattr(item, key, str(value).strip())
        if "slot_index" in body:
            item.slot_index = body.get("slot_index")
        return True
