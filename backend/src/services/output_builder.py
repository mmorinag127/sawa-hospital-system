import csv
import math
import re
from pathlib import Path
from typing import Dict, Any
from uuid import uuid4

import pandas as pd
from loguru import logger

from src.db import session_scope
from src.models.output import Bag, LabelRow, DeliveryNote, ManufacturingAggregateRow
from src.services.order_service import get_order_by_id, get_order_menu_snapshot
from src.services import config_service, menu_service, menu_rule_service

OUTPUT_DIR = Path("/tmp/orders-outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_date(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def _serialize_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _serialize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_for_json(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value

def _safe_qty(line: dict, zero_as_empty: bool) -> float | None:
    qty = line.get("quantity_corrected")
    if qty is None:
        qty = line.get("quantity_original")
    if qty is None:
        return None
    if zero_as_empty and qty <= 0:
        return None
    return qty


def _format_menu_unit(qty: float | int | None, unit_type: str | None) -> str | None:
    if qty is None or unit_type is None:
        return None
    try:
        qty_value = float(qty)
    except Exception:
        return None
    if qty_value.is_integer():
        qty_str = str(int(qty_value))
    else:
        qty_str = str(qty_value)
    suffix = "g" if unit_type == "g" else ("count" if unit_type == "count" else unit_type)
    return f"{qty_str}{suffix}"


def _build_label_details(bag: dict) -> str:
    parts: list[str] = []
    area = bag.get("area_id")
    if area:
        parts.append(str(area))
    unit_str = _format_menu_unit(bag.get("menu_qty_per_serving"), bag.get("menu_unit_type"))
    if unit_str:
        parts.append(unit_str)
    temp = bag.get("menu_temp_type")
    if temp:
        parts.append(str(temp))
    return " / ".join(parts)


def _apply_menu_overrides(lines: list[dict], menu_items: list[dict]) -> list[dict]:
    if not menu_items:
        return lines
    index: dict[str, dict] = {}
    for item in menu_items:
        name = (item.get("name") or "").strip()
        if name:
            index[name] = item
    if not index:
        return lines
    enriched: list[dict] = []
    for line in lines:
        name = (line.get("menu_name") or "").strip()
        item = index.get(name)
        if not item:
            enriched.append(line)
            continue
        updated = dict(line)
        if item.get("daypart"):
            updated["daypart"] = item.get("daypart")
        if item.get("category"):
            updated["menu_category"] = item.get("category")
        if item.get("unit_type"):
            updated["menu_unit_type"] = item.get("unit_type")
        if item.get("qty_per_serving") is not None:
            updated["menu_qty_per_serving"] = item.get("qty_per_serving")
        if item.get("bag_max_qty") is not None:
            updated["menu_bag_max_qty"] = item.get("bag_max_qty")
        if item.get("bag_max_unit"):
            updated["menu_bag_max_unit"] = item.get("bag_max_unit")
        if item.get("temp_type"):
            updated["menu_temp_type"] = item.get("temp_type")
        enriched.append(updated)
    return enriched


def _apply_menu_snapshot(lines: list[dict], snapshot_items: dict) -> list[dict]:
    if not snapshot_items:
        return lines
    enriched: list[dict] = []
    for line in lines:
        name = (line.get("menu_name") or "").strip()
        item = snapshot_items.get(name)
        if not item:
            enriched.append(line)
            continue
        updated = dict(line)
        if item.get("daypart"):
            updated["daypart"] = item.get("daypart")
        if item.get("category"):
            updated["menu_category"] = item.get("category")
        if item.get("unit_type"):
            updated["menu_unit_type"] = item.get("unit_type")
        if item.get("qty_per_serving") is not None:
            updated["menu_qty_per_serving"] = item.get("qty_per_serving")
        if item.get("bag_max_qty") is not None:
            updated["menu_bag_max_qty"] = item.get("bag_max_qty")
        if item.get("bag_max_unit"):
            updated["menu_bag_max_unit"] = item.get("bag_max_unit")
        if item.get("temp_type"):
            updated["menu_temp_type"] = item.get("temp_type")
        enriched.append(updated)
    return enriched


def _normalize_rule_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", str(value)).lower()


def _match_menu_pattern(menu_name: str, pattern: str, match_type: str | None) -> bool:
    if not pattern:
        return False
    if match_type == "regex":
        try:
            return re.search(pattern, menu_name) is not None
        except re.error:
            return False
    normalized_menu = _normalize_rule_text(menu_name)
    normalized_pattern = _normalize_rule_text(pattern)
    if match_type == "exact":
        return normalized_menu == normalized_pattern
    return normalized_pattern in normalized_menu


def _rule_applies(rule, line: dict, facility_id: str | None) -> bool:
    if rule.rule_type == "facility":
        if not facility_id or not rule.facility_id:
            return False
        if rule.facility_id != facility_id:
            return False
    if rule.rule_type in {"menu", "facility"}:
        menu_name = line.get("menu_name") or ""
        if not _match_menu_pattern(menu_name, rule.menu_pattern or "", rule.match_type):
            return False
    if rule.daypart and rule.daypart != line.get("daypart"):
        return False
    if rule.category and rule.category != line.get("menu_category"):
        return False
    if rule.diet_type and rule.diet_type != line.get("diet_type"):
        return False
    return True


def _apply_menu_rules(lines: list[dict], facility_id: str | None) -> list[dict]:
    rules = menu_rule_service.list_active_rules()
    if not rules:
        return lines
    type_weight = {"global": 100, "menu": 200, "facility": 300}
    enriched: list[dict] = []
    for line in lines:
        matches = [
            rule
            for rule in rules
            if _rule_applies(rule, line, facility_id)
        ]
        if not matches:
            enriched.append(line)
            continue
        selected = max(
            matches,
            key=lambda rule: type_weight.get(rule.rule_type, 0) + int(rule.priority or 0),
        )
        updated = dict(line)
        if selected.unit_type:
            updated["menu_unit_type"] = selected.unit_type
        if selected.qty_per_serving is not None:
            updated["menu_qty_per_serving"] = selected.qty_per_serving
        enriched.append(updated)
    return enriched


def _build_bags(order: dict, packaging_policy: dict, quantity_rules: dict) -> list[dict]:
    split_key = packaging_policy.get(
        "split_key",
        ["facility", "date", "daypart", "menu_name", "diet_type", "area_id", "bag_type"],
    )
    zero_as_empty = quantity_rules.get("zero_as_empty", True)

    grouped: dict[tuple, dict] = {}
    for line in order.get("lines", []):
        line_date = _ensure_date(line.get("date"))
        qty = _safe_qty(line, zero_as_empty)
        if qty is None:
            continue
        key = tuple(
            order.get("facility") if part == "facility" else (line_date if part == "date" else line.get(part))
            for part in split_key
        )
        if key not in grouped:
            grouped[key] = {
                "order_id": order["id"],
                "facility": order.get("facility"),
                "date": line_date,
                "daypart": line.get("daypart"),
                "menu_name": line.get("menu_name"),
                "menu_category": line.get("menu_category"),
                "diet_type": line.get("diet_type"),
                "area_id": line.get("area_id"),
                "bag_type": line.get("bag_type"),
                "menu_unit_type": line.get("menu_unit_type"),
                "menu_qty_per_serving": line.get("menu_qty_per_serving"),
                "menu_bag_max_qty": line.get("menu_bag_max_qty"),
                "menu_bag_max_unit": line.get("menu_bag_max_unit"),
                "menu_temp_type": line.get("menu_temp_type"),
                "quantity": 0.0,
            }
        grouped[key]["quantity"] += float(qty)
    return list(grouped.values())


def _max_servings_for_bag(bag: dict) -> int | None:
    bag_max = bag.get("menu_bag_max_qty")
    per_serving = bag.get("menu_qty_per_serving")
    if bag_max is None or per_serving is None:
        return None
    try:
        bag_max_value = float(bag_max)
        per_value = float(per_serving)
    except Exception:
        return None
    if bag_max_value <= 0 or per_value <= 0:
        return None
    unit = bag.get("menu_unit_type")
    bag_unit = bag.get("menu_bag_max_unit") or unit
    if bag_unit and unit and bag_unit != unit:
        return None
    max_servings = int(math.floor(bag_max_value / per_value))
    if max_servings <= 0:
        return None
    return max_servings


def _split_bags_by_max(bags: list[dict]) -> list[dict]:
    split: list[dict] = []
    for bag in bags:
        max_servings = _max_servings_for_bag(bag)
        if not max_servings:
            split.append(bag)
            continue
        remaining = bag.get("quantity") or 0
        try:
            remaining = float(remaining)
        except Exception:
            split.append(bag)
            continue
        if remaining <= max_servings:
            split.append(bag)
            continue
        while remaining > 0:
            chunk = max_servings if remaining > max_servings else remaining
            next_bag = dict(bag)
            next_bag["quantity"] = chunk
            split.append(next_bag)
            remaining -= chunk
    return split


def _label_payload(bag: dict, label_profile: dict, facility_name: str | None) -> dict:
    fixed_text = label_profile.get("fixed_text", {})
    expiry_rule = label_profile.get("expiry_rule", "meal_date")
    expiry_date = bag.get("date")
    if expiry_rule == "meal_date" and expiry_date:
        expiry_value = expiry_date
    else:
        expiry_value = expiry_date
    menu_category = bag.get("menu_category") or bag.get("diet_type")
    return {
        "facility_name": facility_name,
        "expiry_date": expiry_value,
        "storage_mode": label_profile.get("storage_mode"),
        "meal_slot": bag.get("daypart"),
        "menu_category": menu_category,
        "product_name": bag.get("menu_name"),
        "quantity": bag.get("quantity"),
        "details": _build_label_details(bag),
        "maker_info": fixed_text.get("maker_name"),
        "notice": fixed_text.get("notice"),
    }


def _write_label_csv(path: Path, labels: list[dict], label_profile: dict) -> None:
    fieldnames = label_profile.get("label_fields") or (list(labels[0].keys()) if labels else [])
    with path.open("w", newline="", encoding="cp932", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for label in labels:
            writer.writerow({k: label.get(k, "") for k in fieldnames})


def _write_delivery_note(path: Path, rows: list[dict], columns: list[dict]) -> None:
    if not rows:
        df = pd.DataFrame(columns=[col["name"] for col in columns])
    else:
        df = pd.DataFrame(rows)
    df.to_excel(path, index=False)


def _build_delivery_rows(order: dict, template: dict, quantity_rules: dict) -> list[dict]:
    columns = template.get("columns", [])
    zero_as_empty = quantity_rules.get("zero_as_empty", True)
    rows: dict[tuple, dict] = {}
    for line in order.get("lines", []):
        line_date = _ensure_date(line.get("date"))
        qty = _safe_qty(line, zero_as_empty)
        if qty is None:
            continue
        key = (line_date, line.get("daypart"), line.get("menu_name"))
        row = rows.setdefault(
            key,
            {
                "date": line_date,
                "daypart": line.get("daypart"),
                "menu_name": line.get("menu_name"),
            },
        )
        for col in columns:
            if col.get("source") != "quantity":
                continue
            if col.get("diet_type") and col.get("diet_type") != line.get("diet_type"):
                continue
            if col.get("area_id") and col.get("area_id") != line.get("area_id"):
                continue
            name = col.get("name")
            row[name] = (row.get(name) or 0) + float(qty)
    return list(rows.values())


def _write_aggregate_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = ["week", "facility", "menu_name", "diet_type", "area_id", "bag_type", "quantity"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_outputs(order_id: str) -> Dict[str, Any]:
    order = get_order_by_id(order_id)
    if not order:
        raise ValueError("order not found")

    facility_id = order.get("facility")
    facility_config = config_service.get_facility_config(facility_id) if facility_id else None
    if not facility_config:
        logger.warning("Facility config missing", facility_id=facility_id)
        facility_config = {}

    packaging_policy = facility_config.get("packaging_policy", {})
    label_profile = facility_config.get("label_profile", {})
    invoice_template = facility_config.get("invoice_template", {})
    quantity_rules = config_service.load_ingest_policy().get("quantity_rules", {})

    month_id = order.get("week")
    menu_items = (
        menu_service.get_menu_items_for_facility(month_id, facility_id) if month_id else []
    )
    snapshot = get_order_menu_snapshot(order_id)
    snapshot_items = snapshot.get("menu_items") if isinstance(snapshot, dict) else None
    if snapshot_items:
        order_lines = _apply_menu_snapshot(order.get("lines", []), snapshot_items)
    else:
        order_lines = _apply_menu_overrides(order.get("lines", []), menu_items)
    order_lines = _apply_menu_rules(order_lines, facility_id)
    order_for_outputs = {**order, "lines": order_lines}

    bags = _split_bags_by_max(_build_bags(order_for_outputs, packaging_policy, quantity_rules))
    labels = [
        _label_payload(bag, label_profile, facility_config.get("facility_name"))
        for bag in bags
    ]

    label_path = OUTPUT_DIR / f"{order_id}_labels.csv"
    delivery_path = OUTPUT_DIR / f"{order_id}_delivery.xlsx"
    agg_path = OUTPUT_DIR / f"{order_id}_aggregate.csv"

    _write_label_csv(label_path, labels, label_profile)

    delivery_rows = _build_delivery_rows(order_for_outputs, invoice_template, quantity_rules)
    _write_delivery_note(delivery_path, delivery_rows, invoice_template.get("columns", []))

    aggregate_rows: list[dict] = []
    for bag in bags:
        aggregate_rows.append(
            {
                "week": order.get("week"),
                "facility": order.get("facility"),
                "menu_name": bag.get("menu_name"),
                "diet_type": bag.get("diet_type"),
                "area_id": bag.get("area_id"),
                "bag_type": bag.get("bag_type"),
                "quantity": bag.get("quantity"),
            }
        )
    _write_aggregate_csv(agg_path, aggregate_rows)

    with session_scope() as session:
        session.query(Bag).filter(Bag.order_id == order_id).delete()
        session.query(LabelRow).filter(LabelRow.order_id == order_id).delete()
        session.query(DeliveryNote).filter(DeliveryNote.order_id == order_id).delete()

        for bag in bags:
            bag_id = f"BAG{uuid4().hex[:8]}"
            session.add(
                Bag(
                    id=bag_id,
                    order_id=order_id,
                    date=bag.get("date"),
                    daypart=bag.get("daypart"),
                    menu_name=bag.get("menu_name"),
                    diet_type=bag.get("diet_type"),
                    area_id=bag.get("area_id"),
                    bag_type=bag.get("bag_type"),
                    quantity=bag.get("quantity"),
                )
            )
        labels_payload = _serialize_for_json(labels)
        delivery_rows_payload = _serialize_for_json(delivery_rows)
        for label in labels_payload:
            session.add(
                LabelRow(
                    id=f"LAB{uuid4().hex[:8]}",
                    order_id=order_id,
                    bag_id=None,
                    payload_json=label,
                )
            )
        session.add(
            DeliveryNote(
                id=f"INV{uuid4().hex[:8]}",
                order_id=order_id,
                facility_code=order.get("facility") or "",
                date=None,
                file_uri=str(delivery_path),
                payload_json={"rows": delivery_rows_payload},
            )
        )
        for row in aggregate_rows:
            session.add(
                ManufacturingAggregateRow(
                    id=f"MAG{uuid4().hex[:8]}",
                    week_code=row.get("week") or "",
                    facility_code=row.get("facility") or "",
                    menu_name=row.get("menu_name"),
                    diet_type=row.get("diet_type"),
                    area_id=row.get("area_id"),
                    bag_type=row.get("bag_type"),
                    quantity=row.get("quantity") or 0,
                )
            )

    return {
        "order_id": order_id,
        "labels": str(label_path),
        "delivery_note": str(delivery_path),
        "aggregate": str(agg_path),
    }
