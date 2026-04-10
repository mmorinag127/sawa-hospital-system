from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4
import math
import re

from sqlalchemy import select, and_

from src.db import Base, engine, session_scope
from src.models.output import DailyOutputPortionOverride
from src.services.menu_vocabulary import normalize_diet_type

Base.metadata.create_all(bind=engine)

_GRAM_UNIT_ALIASES = {"g", "ｇ", "gram", "grams"}
_CUT_UNIT_ALIASES = {"cut", "slice", "slices"}
_COUNT_UNIT_ALIASES = {"count", "piece", "pieces"}


def normalize_override_menu_name(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = text.replace("\\(", "(").replace("\\)", ")")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_override_daypart(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("朝"):
        return "朝"
    if text.startswith("昼"):
        return "昼"
    if text.startswith("夕"):
        return "夕"
    return text


def normalize_override_category(value: object) -> str:
    return str(value or "").strip()


def normalize_override_diet_type(value: object) -> str:
    return str(normalize_diet_type(value) or "").strip()


def _coerce_override_unit_type(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.lower().replace(" ", "").replace("　", "")
    if lowered in _GRAM_UNIT_ALIASES or "グラム" in raw:
        return "g"
    if lowered in _CUT_UNIT_ALIASES or "切" in raw or "枚" in raw:
        return "切"
    if lowered in _COUNT_UNIT_ALIASES or "個" in raw:
        return "個"
    return None


def normalize_override_unit_type(value: object) -> str:
    normalized = _coerce_override_unit_type(value)
    if normalized:
        return normalized
    raise ValueError("invalid daily output override unit_type")


def serialize_override_unit_type(value: object) -> str:
    normalized = _coerce_override_unit_type(value)
    if normalized:
        return normalized
    return str(value or "").strip()


def _ensure_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except Exception:
        return None


def _safe_quantity(line: dict[str, Any]) -> float | None:
    raw = line.get("quantity_corrected")
    if raw is None:
        raw = line.get("quantity_original")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _serialize_override(row: DailyOutputPortionOverride) -> dict[str, Any]:
    return {
        "id": row.id,
        "output_date": row.output_date.isoformat() if row.output_date else None,
        "facility_id": row.facility_id,
        "menu_name": row.menu_name,
        "normalized_menu_name": row.normalized_menu_name,
        "diet_type": row.diet_type,
        "daypart": row.daypart,
        "menu_category": row.menu_category,
        "unit_type": serialize_override_unit_type(row.unit_type),
        "qty_per_serving": row.qty_per_serving,
        "note": row.note,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_overrides(
    *,
    output_date: date,
    facility_id: str | None = None,
    menu_name: str | None = None,
    diet_type: str | None = None,
    daypart: str | None = None,
    menu_category: str | None = None,
) -> list[dict[str, Any]]:
    predicates = [DailyOutputPortionOverride.output_date == output_date]
    if facility_id:
        predicates.append(DailyOutputPortionOverride.facility_id == str(facility_id).strip())
    normalized_menu_name = normalize_override_menu_name(menu_name)
    if normalized_menu_name:
        predicates.append(DailyOutputPortionOverride.normalized_menu_name == normalized_menu_name)
    normalized_diet = normalize_override_diet_type(diet_type)
    if normalized_diet:
        predicates.append(DailyOutputPortionOverride.diet_type == normalized_diet)
    normalized_daypart = normalize_override_daypart(daypart)
    if normalized_daypart:
        predicates.append(DailyOutputPortionOverride.daypart == normalized_daypart)
    normalized_category = normalize_override_category(menu_category)
    if normalized_category:
        predicates.append(DailyOutputPortionOverride.menu_category == normalized_category)
    with session_scope() as session:
        rows = (
            session.execute(
                select(DailyOutputPortionOverride).where(and_(*predicates)).order_by(
                    DailyOutputPortionOverride.facility_id.asc(),
                    DailyOutputPortionOverride.menu_name.asc(),
                    DailyOutputPortionOverride.diet_type.asc(),
                )
            )
            .scalars()
            .all()
        )
        payload = [_serialize_override(row) for row in rows]
    return payload


def upsert_override(
    *,
    output_date: date,
    facility_id: str,
    menu_name: str,
    diet_type: str | None,
    daypart: str | None,
    menu_category: str | None,
    unit_type: str,
    qty_per_serving: float,
    note: str | None = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    normalized_menu_name = normalize_override_menu_name(menu_name)
    normalized_diet = normalize_override_diet_type(diet_type)
    normalized_daypart = normalize_override_daypart(daypart)
    normalized_category = normalize_override_category(menu_category)
    normalized_unit_type = normalize_override_unit_type(unit_type)
    with session_scope() as session:
        row = (
            session.execute(
                select(DailyOutputPortionOverride).where(
                    DailyOutputPortionOverride.output_date == output_date,
                    DailyOutputPortionOverride.facility_id == facility_id,
                    DailyOutputPortionOverride.normalized_menu_name == normalized_menu_name,
                    DailyOutputPortionOverride.diet_type == normalized_diet,
                    DailyOutputPortionOverride.daypart == normalized_daypart,
                    DailyOutputPortionOverride.menu_category == normalized_category,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            row = DailyOutputPortionOverride(
                id=f"DPO{uuid4().hex[:8]}",
                output_date=output_date,
                facility_id=facility_id,
                menu_name=menu_name,
                normalized_menu_name=normalized_menu_name,
                diet_type=normalized_diet,
                daypart=normalized_daypart,
                menu_category=normalized_category,
                unit_type=normalized_unit_type,
                qty_per_serving=float(qty_per_serving),
                note=str(note or "").strip() or None,
                updated_by=str(updated_by or "").strip() or None,
            )
            session.add(row)
        else:
            row.menu_name = menu_name
            row.unit_type = normalized_unit_type
            row.qty_per_serving = float(qty_per_serving)
            row.note = str(note or "").strip() or None
            row.updated_by = str(updated_by or "").strip() or None
            row.updated_at = datetime.utcnow()
        session.flush()
        session.refresh(row)
        return _serialize_override(row)


def upsert_overrides(
    *,
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not operations:
        return []
    serialized_rows: list[dict[str, Any]] = []
    with session_scope() as session:
        touched_rows: list[DailyOutputPortionOverride] = []
        for operation in operations:
            output_date = _ensure_date(operation.get("output_date"))
            if output_date is None:
                raise ValueError("invalid daily output override date")
            facility_id = str(operation.get("facility_id") or "").strip()
            if not facility_id:
                raise ValueError("facility_id is required")
            menu_name = str(operation.get("menu_name") or "").strip()
            if not menu_name:
                raise ValueError("menu_name is required")
            normalized_menu_name = normalize_override_menu_name(menu_name)
            normalized_diet = normalize_override_diet_type(operation.get("diet_type"))
            normalized_daypart = normalize_override_daypart(operation.get("daypart"))
            normalized_category = normalize_override_category(operation.get("menu_category"))
            normalized_unit_type = normalize_override_unit_type(operation.get("unit_type"))
            qty_per_serving = float(operation.get("qty_per_serving") or 0.0)
            row = (
                session.execute(
                    select(DailyOutputPortionOverride).where(
                        DailyOutputPortionOverride.output_date == output_date,
                        DailyOutputPortionOverride.facility_id == facility_id,
                        DailyOutputPortionOverride.normalized_menu_name == normalized_menu_name,
                        DailyOutputPortionOverride.diet_type == normalized_diet,
                        DailyOutputPortionOverride.daypart == normalized_daypart,
                        DailyOutputPortionOverride.menu_category == normalized_category,
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = DailyOutputPortionOverride(
                    id=f"DPO{uuid4().hex[:8]}",
                    output_date=output_date,
                    facility_id=facility_id,
                    menu_name=menu_name,
                    normalized_menu_name=normalized_menu_name,
                    diet_type=normalized_diet,
                    daypart=normalized_daypart,
                    menu_category=normalized_category,
                    unit_type=normalized_unit_type,
                    qty_per_serving=qty_per_serving,
                    note=str(operation.get("note") or "").strip() or None,
                    updated_by=str(operation.get("updated_by") or "").strip() or None,
                )
                session.add(row)
            else:
                row.menu_name = menu_name
                row.unit_type = normalized_unit_type
                row.qty_per_serving = qty_per_serving
                row.note = str(operation.get("note") or "").strip() or None
                row.updated_by = str(operation.get("updated_by") or "").strip() or None
                row.updated_at = datetime.utcnow()
            touched_rows.append(row)
        session.flush()
        for row in touched_rows:
            session.refresh(row)
            serialized_rows.append(_serialize_override(row))
    return serialized_rows


def delete_override(override_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(DailyOutputPortionOverride, override_id)
        if row is None:
            return None
        payload = _serialize_override(row)
        session.delete(row)
        return payload


def apply_overrides_to_lines(lines: list[dict[str, Any]], facility_id: str | None) -> list[dict[str, Any]]:
    facility_code = str(facility_id or "").strip()
    if not facility_code or not lines:
        return lines
    dates = sorted({
        line_date
        for line in lines
        for line_date in [_ensure_date(line.get("date"))]
        if isinstance(line_date, date)
    })
    if not dates:
        return lines
    with session_scope() as session:
        rows = (
            session.execute(
                select(DailyOutputPortionOverride).where(
                    DailyOutputPortionOverride.facility_id == facility_code,
                    DailyOutputPortionOverride.output_date.in_(dates),
                )
            )
            .scalars()
            .all()
        )
        serialized_rows = [_serialize_override(row) for row in rows]
    if not serialized_rows:
        return lines
    override_map = {
        (
            str(row.get("output_date") or ""),
            str(row.get("facility_id") or ""),
            str(row.get("normalized_menu_name") or ""),
            str(row.get("diet_type") or ""),
            str(row.get("daypart") or ""),
            str(row.get("menu_category") or ""),
        ): row
        for row in serialized_rows
    }
    enriched: list[dict[str, Any]] = []
    for line in lines:
        line_date = _ensure_date(line.get("date"))
        if not line_date:
            enriched.append(line)
            continue
        override = override_map.get(
            (
                line_date.isoformat(),
                facility_code,
                normalize_override_menu_name(line.get("menu_name")),
                normalize_override_diet_type(line.get("diet_type")),
                normalize_override_daypart(line.get("daypart")),
                normalize_override_category(line.get("menu_category")),
            )
        )
        if override is None:
            enriched.append(line)
            continue
        updated = dict(line)
        updated["menu_qty_per_serving"] = float(override.get("qty_per_serving") or 0.0)
        updated["menu_unit_type"] = serialize_override_unit_type(override.get("unit_type"))
        updated["actual_unit_type"] = updated["menu_unit_type"]
        quantity = _safe_quantity(updated)
        updated["actual_amount"] = (
            round(quantity * float(override.get("qty_per_serving") or 0.0), 6) if quantity is not None else None
        )
        updated["_daily_output_override_applied"] = True
        updated["_daily_output_override_id"] = str(override.get("id") or "")
        updated["_daily_output_override_note"] = override.get("note")
        updated["_daily_output_override_updated_at"] = (
            str(override.get("updated_at") or "") or None
        )
        enriched.append(updated)
    return enriched


def collect_applied_override_summaries(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for line in lines:
        if not line.get("_daily_output_override_applied"):
            continue
        key = (
            str(line.get("date") or ""),
            normalize_override_daypart(line.get("daypart")),
            str(line.get("menu_name") or ""),
            normalize_override_diet_type(line.get("diet_type")),
            str(line.get("_daily_output_override_id") or ""),
        )
        if not key[-1]:
            continue
        item = summaries.setdefault(
            key,
            {
                "override_id": str(line.get("_daily_output_override_id") or ""),
                "date": str(line.get("date") or ""),
                "daypart": normalize_override_daypart(line.get("daypart")),
                "menu_name": str(line.get("menu_name") or ""),
                "menu_category": str(line.get("menu_category") or ""),
                "diet_type": normalize_override_diet_type(line.get("diet_type")) or "unknown",
                "unit_type": str(line.get("menu_unit_type") or ""),
                "qty_per_serving": line.get("menu_qty_per_serving"),
                "note": str(line.get("_daily_output_override_note") or "").strip() or None,
            },
        )
        item["menu_category"] = str(line.get("menu_category") or item["menu_category"] or "")
    rows = list(summaries.values())
    rows.sort(
        key=lambda row: (
            row.get("date") or "",
            row.get("daypart") or "",
            row.get("menu_name") or "",
            row.get("diet_type") or "",
        )
    )
    return rows
