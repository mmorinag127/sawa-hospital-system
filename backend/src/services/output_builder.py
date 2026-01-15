import csv
import math
import re
from copy import copy
from io import BytesIO
from pathlib import Path
from typing import Dict, Any
from uuid import uuid4

import pandas as pd
from loguru import logger
from openpyxl import load_workbook

from src.db import session_scope
from src.models.output import Bag, LabelRow, DeliveryNote, ManufacturingAggregateRow
from src.services.order_service import get_order_by_id, get_order_menu_snapshot
from src.services import config_service, menu_service, menu_rule_service
from src.services.storage_service import load_bytes_from_uri

OUTPUT_DIR = Path("/tmp/orders-outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_LABEL_FIELDS = [
    "呼び出し番号",
    "発行枚数",
    "賞味期限",
    "時間",
    "メニュー",
    "温・冷",
    "商品名１",
    "商品名２",
    "内容量",
    "内容詳細",
    "",
]
LEGACY_LABEL_FIELDS = {
    "facility_name",
    "expiry_date",
    "storage_mode",
    "meal_slot",
    "menu_category",
    "product_name",
    "quantity",
    "details",
    "maker_info",
    "notice",
}


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


def _format_number(value: float | int | None) -> str:
    if value is None:
        return ""
    try:
        num = float(value)
    except Exception:
        return str(value)
    if num.is_integer():
        return str(int(num))
    return f"{num:.2f}".rstrip("0").rstrip(".")


def _format_jp_date(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return f"{value.year}年{value.month}月{value.day}日"
    try:
        parsed = pd.to_datetime(value).date()
        return f"{parsed.year}年{parsed.month}月{parsed.day}日"
    except Exception:
        return str(value)


def _normalize_temp_label(temp: str | None) -> str:
    if not temp:
        return ""
    value = str(temp)
    if "冷" in value:
        return "冷菜"
    if "温" in value:
        return "温菜"
    lowered = value.lower()
    if lowered in {"hot", "warm"}:
        return "温菜"
    if lowered in {"cold", "chilled"}:
        return "冷菜"
    return value


def _normalize_unit_type(unit_type: str | None) -> str | None:
    if not unit_type:
        return None
    lowered = str(unit_type).lower()
    if "g" in lowered or "グラム" in lowered:
        return "g"
    if "切" in lowered or lowered in {"cut", "slice"}:
        return "切"
    if "個" in lowered or lowered in {"count", "piece", "pieces"}:
        return "個"
    return str(unit_type)


def _extract_qty_and_unit(value: Any, unit_type: str | None) -> tuple[float | None, str | None]:
    if value is None:
        return None, _normalize_unit_type(unit_type)
    if isinstance(value, (int, float)):
        return float(value), _normalize_unit_type(unit_type)
    text = str(value).strip()
    if not text:
        return None, _normalize_unit_type(unit_type)
    match = re.search(r"[-+]?[0-9]*\\.?[0-9]+", text)
    qty = float(match.group()) if match else None
    inferred_unit = None
    if "g" in text or "ｇ" in text or "グラム" in text:
        inferred_unit = "g"
    elif "切" in text:
        inferred_unit = "切"
    elif "個" in text:
        inferred_unit = "個"
    return qty, _normalize_unit_type(unit_type) or inferred_unit


def _format_amount(value: float | int | None, unit_type: str | None) -> str:
    if value is None:
        return ""
    suffix = _normalize_unit_type(unit_type)
    formatted = _format_number(value)
    if suffix:
        return f"{formatted}{suffix}"
    return formatted


def _format_servings(quantity: float | int | None) -> str:
    if quantity is None:
        return ""
    return f"{_format_number(quantity)}人前"


def _resolve_label_fields(label_profile: dict) -> tuple[list[str], str]:
    fields = label_profile.get("label_fields")
    if isinstance(fields, list) and any(field in LEGACY_LABEL_FIELDS for field in fields):
        return fields, "legacy"
    return (fields if isinstance(fields, list) and fields else DEFAULT_LABEL_FIELDS), "jp"

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
    normalized = _normalize_unit_type(unit_type)
    suffix = "g" if normalized == "g" else ("個" if normalized == "個" else ("切" if normalized == "切" else normalized))
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


def _rule_applies(rule: dict, line: dict, facility_id: str | None) -> bool:
    if rule.get("rule_type") == "facility":
        if not facility_id or not rule.get("facility_id"):
            return False
        if rule.get("facility_id") != facility_id:
            return False
    if rule.get("rule_type") in {"menu", "facility"}:
        menu_name = line.get("menu_name") or ""
        if not _match_menu_pattern(
            menu_name,
            rule.get("menu_pattern") or "",
            rule.get("match_type"),
        ):
            return False
    if rule.get("daypart") and rule.get("daypart") != line.get("daypart"):
        return False
    if rule.get("category") and rule.get("category") != line.get("menu_category"):
        return False
    if rule.get("diet_type") and rule.get("diet_type") != line.get("diet_type"):
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
            key=lambda rule: type_weight.get(rule.get("rule_type"), 0) + int(rule.get("priority") or 0),
        )
        updated = dict(line)
        if selected.get("unit_type"):
            updated["menu_unit_type"] = selected.get("unit_type")
        if selected.get("qty_per_serving") is not None:
            updated["menu_qty_per_serving"] = selected.get("qty_per_serving")
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


def _label_payload_legacy(bag: dict, label_profile: dict, facility_name: str | None) -> dict:
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

def _label_payload_jp(bag: dict) -> dict:
    per_qty, unit = _extract_qty_and_unit(bag.get("menu_qty_per_serving"), bag.get("menu_unit_type"))
    servings = bag.get("quantity")
    total_qty = None
    if per_qty is not None and servings is not None:
        try:
            total_qty = float(per_qty) * float(servings)
        except Exception:
            total_qty = None
    return {
        "呼び出し番号": "",
        "発行枚数": 1,
        "賞味期限": _format_jp_date(bag.get("date")),
        "時間": bag.get("daypart") or "",
        "メニュー": bag.get("menu_category") or bag.get("menu_name") or "",
        "温・冷": _normalize_temp_label(bag.get("menu_temp_type")),
        "商品名１": bag.get("menu_name") or "",
        "商品名２": "",
        "内容量": _format_amount(total_qty, unit),
        "内容詳細": _format_amount(per_qty, unit),
        "": _format_servings(servings),
    }


def _merge_label_rows(rows: list[dict], fields: list[str]) -> list[dict]:
    if not rows:
        return []
    group_fields = [field for field in fields if field not in {"呼び出し番号", "発行枚数"}]
    grouped: dict[tuple, dict] = {}
    counts: dict[tuple, int] = {}
    for row in rows:
        key = tuple(row.get(field, "") for field in group_fields)
        if key not in grouped:
            grouped[key] = dict(row)
            counts[key] = 0
        counts[key] += 1
    merged = []
    for key, row in grouped.items():
        row["発行枚数"] = counts.get(key, 1)
        merged.append(row)
    merged.sort(
        key=lambda r: (
            r.get("賞味期限", ""),
            r.get("時間", ""),
            r.get("メニュー", ""),
            r.get("商品名１", ""),
            r.get("内容量", ""),
        )
    )
    return merged


def _write_label_csv(path: Path, labels: list[dict], label_fields: list[str]) -> None:
    fieldnames = label_fields or (list(labels[0].keys()) if labels else [])
    with path.open("w", newline="", encoding="cp932", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for label in labels:
            writer.writerow({k: label.get(k, "") for k in fieldnames})

def _normalize_cell_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"[\\s　]+", "", text)
    return text


def _find_delivery_header_row(ws, column_names: list[str]) -> int | None:
    targets = [_normalize_cell_text(name) for name in column_names if name]
    best_row = None
    best_hits = 0
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        row_text = [_normalize_cell_text(cell.value) for cell in row if cell.value is not None]
        if not row_text:
            continue
        hits = sum(1 for target in targets if any(target in cell for cell in row_text))
        if hits > best_hits:
            best_hits = hits
            best_row = row[0].row
    return best_row


def _build_delivery_column_map(ws, header_row: int, column_names: list[str]) -> dict[str, int]:
    column_map: dict[str, int] = {}
    header_cells = list(ws[header_row])
    for col_name in column_names:
        normalized = _normalize_cell_text(col_name)
        for cell in header_cells:
            cell_text = _normalize_cell_text(cell.value)
            if normalized and normalized in cell_text:
                column_map[col_name] = cell.col_idx
                break
    return column_map


def _copy_cell_style(source, target) -> None:
    target.font = copy(source.font)
    target.border = copy(source.border)
    target.fill = copy(source.fill)
    target.number_format = copy(source.number_format)
    target.protection = copy(source.protection)
    target.alignment = copy(source.alignment)


def _write_delivery_note(
    path: Path,
    rows: list[dict],
    columns: list[dict],
    template_uri: str | None,
    sheet_name: str | None = None,
) -> None:
    if not template_uri:
        if not rows:
            df = pd.DataFrame(columns=[col["name"] for col in columns])
        else:
            df = pd.DataFrame(rows)
        df.to_excel(path, index=False)
        return

    template_bytes = load_bytes_from_uri(template_uri)
    workbook = load_workbook(BytesIO(template_bytes))
    if sheet_name and sheet_name in workbook.sheetnames:
        ws = workbook[sheet_name]
    else:
        ws = workbook.active

    column_names = [col.get("name") for col in columns if col.get("name")]
    header_row = _find_delivery_header_row(ws, column_names)
    if not header_row:
        header_row = 1
    column_map = _build_delivery_column_map(ws, header_row, column_names)
    start_row = header_row + 1

    for idx, row in enumerate(rows):
        target_row = start_row + idx
        for col in column_names:
            col_idx = column_map.get(col)
            if not col_idx:
                continue
            cell = ws.cell(row=target_row, column=col_idx)
            template_cell = ws.cell(row=start_row, column=col_idx)
            _copy_cell_style(template_cell, cell)
            cell.value = row.get(col, "")

    workbook.save(path)


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


def _build_label_rows(
    bags: list[dict],
    label_profile: dict,
    facility_name: str | None,
) -> tuple[list[dict], list[str], str]:
    label_fields, label_format = _resolve_label_fields(label_profile)
    if label_format == "legacy":
        labels = [_label_payload_legacy(bag, label_profile, facility_name) for bag in bags]
        return labels, label_fields, label_format
    labels = [_label_payload_jp(bag) for bag in bags]
    merged = _merge_label_rows(labels, label_fields)
    return merged, label_fields, label_format


def _build_total_rows(
    order_lines: list[dict],
    label_profile: dict,
    facility_name: str | None,
    quantity_rules: dict,
) -> tuple[list[dict], list[str], str]:
    label_fields, label_format = _resolve_label_fields(label_profile)
    zero_as_empty = quantity_rules.get("zero_as_empty", True)
    grouped: dict[tuple, dict] = {}
    for line in order_lines:
        line_date = _ensure_date(line.get("date"))
        qty = _safe_qty(line, zero_as_empty)
        if qty is None:
            continue
        key = (
            line_date,
            line.get("daypart"),
            line.get("menu_category"),
            line.get("menu_name"),
            line.get("menu_temp_type"),
            line.get("menu_qty_per_serving"),
            line.get("menu_unit_type"),
        )
        row = grouped.setdefault(
            key,
            {
                "date": line_date,
                "daypart": line.get("daypart"),
                "menu_category": line.get("menu_category"),
                "menu_name": line.get("menu_name"),
                "menu_temp_type": line.get("menu_temp_type"),
                "menu_qty_per_serving": line.get("menu_qty_per_serving"),
                "menu_unit_type": line.get("menu_unit_type"),
                "quantity": 0.0,
            },
        )
        row["quantity"] += float(qty)
    if label_format == "legacy":
        labels = [
            _label_payload_legacy(row, label_profile, facility_name) for row in grouped.values()
        ]
        return labels, label_fields, label_format
    labels = [_label_payload_jp(row) for row in grouped.values()]
    merged = _merge_label_rows(labels, label_fields)
    for row in merged:
        row["発行枚数"] = ""
    return merged, label_fields, label_format


def _write_aggregate_csv(path: Path, rows: list[dict], label_fields: list[str]) -> None:
    fieldnames = label_fields or (list(rows[0].keys()) if rows else [])
    with path.open("w", newline="", encoding="cp932", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _prepare_output_context(order_id: str) -> dict:
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
    return {
        "order": order,
        "facility_config": facility_config,
        "label_profile": label_profile,
        "invoice_template": invoice_template,
        "quantity_rules": quantity_rules,
        "order_lines": order_lines,
        "order_for_outputs": order_for_outputs,
        "bags": bags,
    }


def build_output_preview(order_id: str, output_type: str) -> Dict[str, Any]:
    ctx = _prepare_output_context(order_id)
    label_profile = ctx["label_profile"]
    invoice_template = ctx["invoice_template"]
    quantity_rules = ctx["quantity_rules"]
    facility_name = ctx["facility_config"].get("facility_name")
    bags = ctx["bags"]

    label_path = OUTPUT_DIR / f"{order_id}_labels.csv"
    delivery_path = OUTPUT_DIR / f"{order_id}_delivery.xlsx"
    agg_path = OUTPUT_DIR / f"{order_id}_aggregate.csv"

    if output_type == "labels":
        labels, label_fields, _ = _build_label_rows(bags, label_profile, facility_name)
        _write_label_csv(label_path, labels, label_fields)
        return {"labels": str(label_path)}
    if output_type == "delivery":
        delivery_rows = _build_delivery_rows(ctx["order_for_outputs"], invoice_template, quantity_rules)
        _write_delivery_note(
            delivery_path,
            delivery_rows,
            invoice_template.get("columns", []),
            invoice_template.get("template_uri"),
            invoice_template.get("sheet_name"),
        )
        return {"delivery_note": str(delivery_path)}
    if output_type == "aggregate":
        total_rows, total_fields, _ = _build_total_rows(
            ctx["order_lines"], label_profile, facility_name, quantity_rules
        )
        _write_aggregate_csv(agg_path, total_rows, total_fields)
        return {"aggregate": str(agg_path)}
    raise ValueError(f"invalid output type: {output_type}")


def build_outputs(order_id: str) -> Dict[str, Any]:
    ctx = _prepare_output_context(order_id)
    order = ctx["order"]
    label_profile = ctx["label_profile"]
    invoice_template = ctx["invoice_template"]
    quantity_rules = ctx["quantity_rules"]
    order_lines = ctx["order_lines"]
    order_for_outputs = ctx["order_for_outputs"]
    bags = ctx["bags"]
    labels, label_fields, _ = _build_label_rows(
        bags, label_profile, ctx["facility_config"].get("facility_name")
    )

    label_path = OUTPUT_DIR / f"{order_id}_labels.csv"
    delivery_path = OUTPUT_DIR / f"{order_id}_delivery.xlsx"
    agg_path = OUTPUT_DIR / f"{order_id}_aggregate.csv"

    _write_label_csv(label_path, labels, label_fields)

    delivery_rows = _build_delivery_rows(order_for_outputs, invoice_template, quantity_rules)
    _write_delivery_note(
        delivery_path,
        delivery_rows,
        invoice_template.get("columns", []),
        invoice_template.get("template_uri"),
        invoice_template.get("sheet_name"),
    )

    total_rows, total_fields, _ = _build_total_rows(
        order_lines, label_profile, ctx["facility_config"].get("facility_name"), quantity_rules
    )
    _write_aggregate_csv(agg_path, total_rows, total_fields)

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
        for row in bags:
            session.add(
                ManufacturingAggregateRow(
                    id=f"MAG{uuid4().hex[:8]}",
                    week_code=order.get("week") or "",
                    facility_code=order.get("facility") or "",
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


def rebuild_bags(order_id: str) -> Dict[str, Any]:
    order = get_order_by_id(order_id)
    if not order:
        raise ValueError("order not found")

    facility_id = order.get("facility")
    facility_config = config_service.get_facility_config(facility_id) if facility_id else None
    if not facility_config:
        logger.warning("Facility config missing", facility_id=facility_id)
        facility_config = {}

    packaging_policy = facility_config.get("packaging_policy", {})
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
    payload = []

    with session_scope() as session:
        session.query(Bag).filter(Bag.order_id == order_id).delete()
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
            payload.append(
                {
                    "id": bag_id,
                    "date": bag.get("date").isoformat() if bag.get("date") else None,
                    "daypart": bag.get("daypart"),
                    "menu_name": bag.get("menu_name"),
                    "diet_type": bag.get("diet_type"),
                    "area_id": bag.get("area_id"),
                    "bag_type": bag.get("bag_type"),
                    "quantity": bag.get("quantity"),
                }
            )

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
    return {"order_id": order_id, "generated": bool(payload), "bags": payload}
