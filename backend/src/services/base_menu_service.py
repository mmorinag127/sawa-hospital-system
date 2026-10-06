from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import delete, inspect, select

from src.db import engine, session_scope
from src.models.menu import BaseMenuCycleItem
from src.services import menu_edit_revision_service, menu_service
from src.services.menu_vocabulary import normalize_diet_type


class BaseMenuSchemaNotMigrated(RuntimeError):
    pass


def _require_base_menu_schema() -> None:
    inspector = inspect(engine)
    if "base_menu_cycle_items" not in inspector.get_table_names():
        raise BaseMenuSchemaNotMigrated(
            "base menu schema is not migrated; run migration 0015: base_menu_cycle_items"
        )
    columns = {str(column.get("name") or "") for column in inspector.get_columns("base_menu_cycle_items")}
    required_columns = {column.name for column in BaseMenuCycleItem.__table__.columns}
    missing_columns = sorted(required_columns - columns)
    if not missing_columns:
        return
    raise BaseMenuSchemaNotMigrated(
        "base menu schema is not migrated; run migration 0015: "
        + ", ".join(f"base_menu_cycle_items.{column}" for column in missing_columns)
    )


def serialize_item(item: BaseMenuCycleItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "revision": item.revision,
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


def _merge_master_defaults(items: list[dict], defaults: dict[str, dict]) -> list[dict]:
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


def _defaults_and_sources(session, items: list[BaseMenuCycleItem], *, lock_sources: bool) -> tuple[dict[str, dict], list[tuple[str, int]]]:
    names = [str(item.name or "").strip() for item in items if str(item.name or "").strip()]
    return menu_service.resolve_menu_defaults_in_session(session, names, None, lock_sources=lock_sources)


def _snapshot(
    session,
    items: list[BaseMenuCycleItem],
    scope_revision: int,
    *,
    cycle_day: int | None,
) -> dict:
    payload = [serialize_item(item) for item in items]
    defaults, sources = _defaults_and_sources(session, items, lock_sources=True)
    return {
        "items": _merge_master_defaults(payload, defaults),
        "revision": menu_edit_revision_service.encode(
            menu_edit_revision_service.BASE_MENU_SCOPE,
            scope_revision,
            sources,
            query={"cycle_day": cycle_day},
            coverage="full" if cycle_day is None else "partial",
        ),
    }


def list_items(cycle_day: int | None = None) -> dict:
    _require_base_menu_schema()
    with session_scope() as session:
        scope_revision = menu_edit_revision_service.read_locked_current(session, menu_edit_revision_service.BASE_MENU_SCOPE)
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
        return _snapshot(session, items, scope_revision, cycle_day=cycle_day)


def replace_items(items: list[dict], expected_revision: str | None) -> dict:
    _require_base_menu_schema()
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
        expected = menu_edit_revision_service.decode_full_base_menu_snapshot(expected_revision)
        menu_edit_revision_service.compare_and_advance(
            session, menu_edit_revision_service.BASE_MENU_SCOPE, expected.revision
        )
        current_items = session.execute(select(BaseMenuCycleItem)).scalars().all()
        _, current_sources = _defaults_and_sources(session, current_items, lock_sources=True)
        if list(expected.sources) != current_sources:
            raise menu_edit_revision_service.RevisionConflict("menu master revision conflict; reload before saving")
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
        session.flush()
        saved = session.execute(select(BaseMenuCycleItem)).scalars().all()
        snapshot = _snapshot(
            session,
            saved,
            menu_edit_revision_service.current(session, menu_edit_revision_service.BASE_MENU_SCOPE),
            cycle_day=None,
        )
    return {"created": len(cleaned), **snapshot}


def update_item(item_id: str, body: dict, expected_revision: str | None) -> dict | None:
    if not item_id:
        return None
    _require_base_menu_schema()
    with session_scope() as session:
        item = session.get(BaseMenuCycleItem, item_id)
        if not item:
            return None
        expected_scope, expected_sources = menu_edit_revision_service.decode(
            expected_revision, menu_edit_revision_service.BASE_MENU_SCOPE
        )
        menu_edit_revision_service.compare_and_advance(
            session, menu_edit_revision_service.BASE_MENU_SCOPE, expected_scope
        )
        current_items = session.execute(select(BaseMenuCycleItem)).scalars().all()
        _, current_sources = _defaults_and_sources(session, current_items, lock_sources=True)
        if expected_sources != current_sources:
            raise menu_edit_revision_service.RevisionConflict("menu master revision conflict; reload before saving")
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
        session.flush()
        session.refresh(item)
        saved = serialize_item(item)
        snapshot = _snapshot(
            session,
            session.execute(select(BaseMenuCycleItem)).scalars().all(),
            menu_edit_revision_service.current(session, menu_edit_revision_service.BASE_MENU_SCOPE),
            cycle_day=None,
        )
        return {"item": saved, **snapshot}
