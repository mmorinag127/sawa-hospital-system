import csv
import json
import math
import calendar
import re
from copy import copy
from datetime import date as dt_date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Dict, Any
from uuid import uuid4

import pandas as pd
from loguru import logger
from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell

from src.db import session_scope
from src.models.order_confirmed_snapshot import OrderConfirmedSnapshot
from src.models.output import Bag, LabelRow, DeliveryNote, ManufacturingAggregateRow
from src.services.order_service import get_order_by_id, get_order_menu_snapshot
from src.services import (
    config_service,
    menu_service,
    menu_rule_service,
    order_service,
    daily_output_override_service,
)
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
    "実量",
    "一人前",
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

_GARNISH_SPLIT_RE = re.compile(r"\s*(?:添え|添[)）:：])\s*")


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


def _latest_output_lineage_for_order(session, order_id: str) -> dict[str, str | None]:
    snapshot = (
        session.query(OrderConfirmedSnapshot)
        .filter(OrderConfirmedSnapshot.order_id == order_id)
        .order_by(OrderConfirmedSnapshot.confirmed_at.desc(), OrderConfirmedSnapshot.id.desc())
        .first()
    )
    if snapshot is None:
        return {
            "confirmed_snapshot_id": None,
            "output_bundle_id": None,
            "source_saved_sheet_id": None,
            "template_version_id": None,
        }
    snapshot_json = snapshot.snapshot_json if isinstance(snapshot.snapshot_json, dict) else {}
    output_bundle = snapshot_json.get("output_bundle") if isinstance(snapshot_json.get("output_bundle"), dict) else {}
    return {
        "confirmed_snapshot_id": snapshot.id,
        "output_bundle_id": str(output_bundle.get("output_bundle_id") or "").strip() or None,
        "source_saved_sheet_id": str(snapshot_json.get("saved_sheet_id") or output_bundle.get("source_saved_sheet_id") or "").strip() or None,
        "template_version_id": str(snapshot.template_version_id or output_bundle.get("template_version_id") or "").strip() or None,
    }


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


def _coerce_to_date(value: Any) -> dt_date | None:
    if value is None:
        return None
    if isinstance(value, dt_date):
        return value
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def _add_months(base_date: dt_date, months: int) -> dt_date:
    if months == 0:
        return base_date
    month_index = (base_date.month - 1) + months
    target_year = base_date.year + (month_index // 12)
    target_month = (month_index % 12) + 1
    target_day = min(base_date.day, calendar.monthrange(target_year, target_month)[1])
    return dt_date(target_year, target_month, target_day)


def _resolve_label_expiry_date(
    meal_date: Any,
    label_profile: dict | None,
) -> Any:
    base_date = _coerce_to_date(meal_date)
    if base_date is None:
        return meal_date
    profile = label_profile if isinstance(label_profile, dict) else {}
    raw_months = profile.get("expiry_offset_months", 0)
    raw_days = profile.get("expiry_offset_days", 0)
    try:
        offset_months = int(raw_months or 0)
    except Exception:
        offset_months = 0
    try:
        offset_days = int(raw_days or 0)
    except Exception:
        offset_days = 0
    expiry_date = _add_months(base_date, offset_months)
    if offset_days:
        expiry_date = expiry_date + timedelta(days=offset_days)
    return expiry_date


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
    raw = str(unit_type).strip()
    lowered = raw.lower()
    if "g" in lowered or "グラム" in raw:
        return "g"
    if "切" in raw or "枚" in raw or lowered in {"cut", "slice", "slices"}:
        return "切"
    if "個" in raw or lowered in {"count", "piece", "pieces"}:
        return "個"
    return raw


_AREA_TRANSLATION = str.maketrans("０１２３４５６７８９ｆＦ", "0123456789fF")


def _normalize_diet_key(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    lowered = raw.lower()
    if ("袋" in raw or "bag" in lowered) and (
        "常食" in raw or "通常" in raw or lowered in {"regular_bag", "bag"}
    ):
        return "regular_bag"
    if "常食" in raw or "通常" in raw or lowered in {"regular", "standard"}:
        return "regular"
    if "軟菜" in raw or "やわ" in raw or "ﾔﾜ" in raw or "ヤワ" in raw or lowered in {"soft"}:
        return "soft"
    if "ミキサ" in raw or "ﾐｷｻ" in raw or lowered in {"mixer"}:
        return "mixer"
    if "通所" in raw or lowered in {"daycare"}:
        return "regular"
    if "職員" in raw or lowered in {"staff"}:
        return "regular"
    if "揚げ物禁" in raw or "揚物禁" in raw or lowered in {"no_fried", "nofried"}:
        return "no_fried"
    if (
        ("肉" in raw or "meat" in lowered)
        and ("卵" in raw or "玉子" in raw or "egg" in lowered)
        and ("魚" in raw or "鯖" in raw or "さば" in raw or "fish" in lowered)
    ) or "肉卵魚禁" in raw:
        return "forbidden_other"
    if "禁" in raw and "肉" in raw:
        return "no_meat"
    if "禁" in raw and "魚" in raw:
        return "no_fish"
    return lowered


def _normalize_area_id(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.translate(_AREA_TRANSLATION)
    text = re.sub(r"[\\s　]+", "", text)
    text = text.replace("階", "F").replace("ｆ", "F").replace("f", "F")
    match = re.search(r"(\\d)F", text, re.IGNORECASE)
    if match:
        return f"{match.group(1)}F"
    return text.upper()


def _build_area_alias_map(facility_config: dict | None) -> dict[str, str]:
    if not facility_config:
        return {}
    areas = facility_config.get("areas") or []
    if not isinstance(areas, list):
        return {}
    aliases: dict[str, str] = {}
    for area in areas:
        if not isinstance(area, dict):
            continue
        area_id = area.get("area_id") or area.get("id") or ""
        name = area.get("name") or ""
        canonical = _normalize_area_id(area_id) or _normalize_area_id(name)
        if not canonical:
            continue
        for candidate in (area_id, name):
            norm = _normalize_area_id(candidate)
            if norm:
                aliases[norm] = canonical
    return aliases


def _resolve_area_key(value: str | None, aliases: dict[str, str]) -> str | None:
    norm = _normalize_area_id(value)
    if not norm:
        return None
    return aliases.get(norm, norm)


def _infer_delivery_column_meta(name: str | None) -> tuple[str | None, str | None]:
    if not name:
        return None, None
    raw = str(name)
    diet = _normalize_diet_key(raw)
    area = None
    match = re.search(r"(\\d)\\s*(?:f|ｆ|Ｆ|階)", raw, re.IGNORECASE)
    if match:
        area = f"{match.group(1)}F"
    return diet, area


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
    elif "切" in text or "枚" in text:
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
    resolved = fields if isinstance(fields, list) and fields else DEFAULT_LABEL_FIELDS
    required = ["実量", "一人前"]
    normalized = list(resolved)
    for field in required:
        if field not in normalized:
            normalized.append(field)
    return normalized, "jp"

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


def _build_menu_name_aliases(value: object) -> list[str]:
    text = str(value or "").strip().strip("　")
    if not text:
        return []
    aliases = [text]
    match = _GARNISH_SPLIT_RE.search(text)
    if match:
        base = text[:match.start()].strip().strip("　")
        if base and base not in aliases:
            aliases.append(base)
    return aliases


def _apply_menu_overrides(lines: list[dict], menu_items: list[dict]) -> list[dict]:
    if not menu_items:
        return lines
    index: dict[str, dict] = {}
    for item in menu_items:
        for alias in _build_menu_name_aliases(item.get("name")):
            key = _normalize_menu_key(alias)
            if key and key not in index:
                index[key] = item
    if not index:
        return lines
    enriched: list[dict] = []
    for line in lines:
        name_key = _normalize_menu_key(line.get("menu_name"))
        item = index.get(name_key)
        if not item:
            enriched.append(line)
            continue
        updated = dict(line)
        if item.get("daypart") and not updated.get("daypart"):
            updated["daypart"] = item.get("daypart")
        if item.get("category") and not updated.get("menu_category"):
            updated["menu_category"] = item.get("category")
        if item.get("unit_type"):
            updated["menu_unit_type"] = item.get("unit_type")
        if item.get("qty_per_serving") is not None:
            updated["menu_qty_per_serving"] = item.get("qty_per_serving")
            updated["_menu_qty_source_daypart"] = item.get("daypart")
            updated["_menu_qty_source_category"] = item.get("category")
        if item.get("bag_max_qty") is not None:
            updated["menu_bag_max_qty"] = item.get("bag_max_qty")
        if item.get("bag_max_unit"):
            updated["menu_bag_max_unit"] = item.get("bag_max_unit")
        if item.get("temp_type"):
            updated["menu_temp_type"] = item.get("temp_type")
        if item.get("condiments") is not None:
            updated["condiments"] = item.get("condiments")
        enriched.append(updated)
    return enriched


def _apply_menu_snapshot(lines: list[dict], snapshot_items: dict) -> list[dict]:
    if not snapshot_items:
        return lines
    index: dict[str, dict] = {}
    for name, item in snapshot_items.items():
        for alias in _build_menu_name_aliases(name):
            key = _normalize_menu_key(alias)
            if key and key not in index:
                index[key] = item
    enriched: list[dict] = []
    for line in lines:
        name_key = _normalize_menu_key(line.get("menu_name"))
        item = index.get(name_key)
        if not item:
            enriched.append(line)
            continue
        updated = dict(line)
        if item.get("daypart") and not updated.get("daypart"):
            updated["daypart"] = item.get("daypart")
        if item.get("category") and not updated.get("menu_category"):
            updated["menu_category"] = item.get("category")
        if item.get("unit_type"):
            updated["menu_unit_type"] = item.get("unit_type")
        if item.get("qty_per_serving") is not None:
            updated["menu_qty_per_serving"] = item.get("qty_per_serving")
            updated["_menu_qty_source_daypart"] = item.get("daypart")
            updated["_menu_qty_source_category"] = item.get("category")
        if item.get("bag_max_qty") is not None:
            updated["menu_bag_max_qty"] = item.get("bag_max_qty")
        if item.get("bag_max_unit"):
            updated["menu_bag_max_unit"] = item.get("bag_max_unit")
        if item.get("temp_type"):
            updated["menu_temp_type"] = item.get("temp_type")
        if item.get("condiments") is not None:
            updated["condiments"] = item.get("condiments")
        enriched.append(updated)
    return enriched


def _build_menu_entry_indexes(menu_entries: list[dict]) -> tuple[dict[tuple[str, str, str], dict], dict[tuple[str, str], list[dict]]]:
    exact: dict[tuple[str, str, str], dict] = {}
    by_date_name: dict[tuple[str, str], list[dict]] = {}
    for entry in menu_entries:
        line_date = _ensure_date(entry.get("menu_date"))
        if not line_date:
            continue
        name_key = _normalize_menu_key(entry.get("name"))
        if not name_key:
            continue
        date_key = line_date.isoformat()
        by_date_name.setdefault((date_key, name_key), []).append(entry)
        daypart_key = _normalize_delivery_daypart(entry.get("daypart"))
        if daypart_key:
            exact[(date_key, daypart_key, name_key)] = entry
    return exact, by_date_name


def _resolve_menu_entry_override(
    line: dict,
    exact_index: dict[tuple[str, str, str], dict],
    date_name_index: dict[tuple[str, str], list[dict]],
) -> dict | None:
    line_date = _ensure_date(line.get("date"))
    if not line_date:
        return None
    name_key = _normalize_menu_key(line.get("menu_name"))
    if not name_key:
        return None
    date_key = line_date.isoformat()
    daypart_key = _normalize_delivery_daypart(line.get("daypart"))
    if daypart_key:
        matched = exact_index.get((date_key, daypart_key, name_key))
        if matched:
            return matched
    candidates = date_name_index.get((date_key, name_key)) or []
    if len(candidates) == 1:
        return candidates[0]
    return None


def _apply_menu_entry_overrides(lines: list[dict], menu_entries: list[dict]) -> list[dict]:
    if not menu_entries:
        return lines
    exact_index, date_name_index = _build_menu_entry_indexes(menu_entries)
    if not exact_index and not date_name_index:
        return lines
    enriched: list[dict] = []
    for line in lines:
        entry = _resolve_menu_entry_override(line, exact_index, date_name_index)
        if not entry:
            enriched.append(line)
            continue
        updated = dict(line)
        entry_daypart = _normalize_delivery_daypart(entry.get("daypart"))
        if entry_daypart:
            updated["daypart"] = entry_daypart
        if entry.get("category"):
            updated["menu_category"] = entry.get("category")
        enriched.append(updated)
    return enriched


def _normalize_output_daypart(value: object) -> str:
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


def _build_menu_entry_indexes(menu_entries: list[dict]) -> tuple[dict[tuple[str, str, str], dict], dict[tuple[str, str], dict]]:
    by_exact: dict[tuple[str, str, str], dict] = {}
    by_date_name: dict[tuple[str, str], dict | None] = {}
    for entry in menu_entries:
        menu_date = str(entry.get("menu_date") or "").strip()
        menu_name = str(entry.get("name") or "").strip()
        daypart = _normalize_output_daypart(entry.get("daypart"))
        if not menu_date or not menu_name:
            continue
        for alias in _build_menu_name_aliases(menu_name):
            alias_key = _normalize_menu_key(alias)
            if not alias_key:
                continue
            if daypart:
                by_exact[(menu_date, daypart, alias_key)] = entry
            date_name_key = (menu_date, alias_key)
            existing = by_date_name.get(date_name_key)
            if existing is None:
                by_date_name[date_name_key] = entry
            elif existing != entry:
                by_date_name[date_name_key] = {}
    resolved_by_date_name = {
        key: value
        for key, value in by_date_name.items()
        if isinstance(value, dict) and value
    }
    return by_exact, resolved_by_date_name


def _apply_menu_entry_overrides(lines: list[dict], menu_entries: list[dict]) -> list[dict]:
    if not menu_entries:
        return lines
    by_exact, by_date_name = _build_menu_entry_indexes(menu_entries)
    if not by_exact and not by_date_name:
        return lines
    enriched: list[dict] = []
    for line in lines:
        menu_name_key = _normalize_menu_key(line.get("menu_name"))
        line_date = _ensure_date(line.get("date"))
        date_key = line_date.isoformat() if line_date else ""
        daypart = _normalize_output_daypart(line.get("daypart"))
        entry = None
        if date_key and daypart and menu_name_key:
            entry = by_exact.get((date_key, daypart, menu_name_key))
        if entry is None and date_key and menu_name_key:
            entry = by_date_name.get((date_key, menu_name_key))
        if not entry:
            enriched.append(line)
            continue
        updated = dict(line)
        if entry.get("daypart"):
            updated["daypart"] = _normalize_output_daypart(entry.get("daypart"))
        if entry.get("category"):
            updated["menu_category"] = entry.get("category")
        updated["_monthly_entry_override_applied"] = True
        enriched.append(updated)
    return enriched


def _normalize_category_key(value: object) -> str:
    return str(value or "").strip()


def _clear_stale_menu_qty_from_monthly_entry(lines: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for line in lines:
        if not line.get("_monthly_entry_override_applied") or line.get("menu_qty_per_serving") is None:
            enriched.append(line)
            continue
        source_daypart = _normalize_output_daypart(line.get("_menu_qty_source_daypart"))
        current_daypart = _normalize_output_daypart(line.get("daypart"))
        source_category = _normalize_category_key(line.get("_menu_qty_source_category"))
        current_category = _normalize_category_key(line.get("menu_category"))
        daypart_conflict = bool(source_daypart and current_daypart and source_daypart != current_daypart)
        category_conflict = bool(source_category and current_category and source_category != current_category)
        if not daypart_conflict and not category_conflict:
            enriched.append(line)
            continue
        updated = dict(line)
        updated["menu_qty_per_serving"] = None
        enriched.append(updated)
    return enriched


def _apply_garnish_defaults(lines: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for line in lines:
        if _normalize_category_key(line.get("menu_category")) != "添え" or line.get("menu_qty_per_serving") is not None:
            enriched.append(line)
            continue
        updated = dict(line)
        updated["menu_unit_type"] = _normalize_unit_type(updated.get("menu_unit_type")) or "g"
        updated["menu_qty_per_serving"] = 30
        enriched.append(updated)
    return enriched


def _normalize_condiments(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


def _apply_condiment_lines(lines: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for line in lines:
        updated = dict(line)
        condiments = _normalize_condiments(updated.get("condiments"))
        if condiments and not updated.get("note"):
            if any("ソース" in label for label in condiments):
                updated["note"] = "ソース"
        enriched.append(updated)
        if not condiments:
            continue
        for label in condiments:
            condiment_line = dict(updated)
            condiment_line["menu_name"] = label
            condiment_line["menu_category"] = "付属"
            condiment_line["condiments"] = []
            condiment_line["bag_type"] = "condiment"
            condiment_line["menu_bag_max_qty"] = None
            condiment_line["menu_bag_max_unit"] = None
            enriched.append(condiment_line)
    return enriched


def _build_condiment_map(menu_names: list[str], facility_id: str | None) -> dict[str, list[str]]:
    if not menu_names:
        return {}
    defaults = menu_service.resolve_menu_defaults(menu_names, facility_id)
    condiment_map: dict[str, list[str]] = {}
    for name in menu_names:
        payload = defaults.get(name, {})
        condiments = _normalize_condiments(payload.get("condiments"))
        if condiments:
            condiment_map[name] = condiments
    return condiment_map


def _apply_condiment_note(row: dict, condiments: list[str]) -> dict:
    if not condiments:
        return row
    note = row.get("note") or ""
    if any("ソース" in label for label in condiments):
        if "ソース" not in note:
            note = f"{note} / ソース".strip(" /") if note else "ソース"
    if note:
        row["note"] = note
    return row


def _resolve_bag_types(facility_config: dict | None) -> list[dict]:
    bag_types = []
    if isinstance(facility_config, dict):
        bag_types = facility_config.get("bag_types") or []
    if not isinstance(bag_types, list):
        return []
    normalized: list[dict] = []
    for entry in bag_types:
        if not isinstance(entry, dict):
            continue
        bag_id = entry.get("bag_type_id")
        max_qty = entry.get("max_qty")
        unit = entry.get("unit")
        if not bag_id or max_qty is None:
            continue
        try:
            max_qty_value = float(max_qty)
        except Exception:
            continue
        normalized.append(
            {
                "bag_type_id": str(bag_id),
                "label": entry.get("label") or str(bag_id),
                "max_qty": max_qty_value,
                "unit": _normalize_unit_type(unit or "g") or "g",
            }
        )
    normalized.sort(key=lambda item: item["max_qty"])
    return normalized


def _apply_bag_size_defaults(lines: list[dict], bag_types: list[dict]) -> list[dict]:
    if not bag_types:
        return lines
    largest_by_unit: dict[str, dict] = {}
    for entry in bag_types:
        unit = _normalize_unit_type(entry.get("unit")) or "g"
        selected = largest_by_unit.get(unit)
        if not selected or float(entry.get("max_qty", 0)) > float(selected.get("max_qty", 0)):
            largest_by_unit[unit] = entry
    for line in lines:
        if line.get("bag_type") == "condiment":
            continue
        unit = _normalize_unit_type(line.get("menu_unit_type"))
        if not unit:
            continue
        if line.get("menu_bag_max_qty") is not None:
            continue
        largest = largest_by_unit.get(unit)
        if not largest:
            continue
        line["menu_bag_max_qty"] = largest["max_qty"]
        line["menu_bag_max_unit"] = largest.get("unit") or unit
    return lines


def _assign_bag_type_for_bags(bags: list[dict], bag_types: list[dict]) -> list[dict]:
    if not bag_types:
        return bags
    bag_types_by_unit: dict[str, list[dict]] = {}
    for entry in bag_types:
        unit = _normalize_unit_type(entry.get("unit")) or "g"
        bag_types_by_unit.setdefault(unit, []).append(entry)
    for entries in bag_types_by_unit.values():
        entries.sort(key=lambda item: item["max_qty"])
    for bag in bags:
        if bag.get("bag_type") == "condiment":
            continue
        unit = _normalize_unit_type(bag.get("menu_unit_type"))
        per_qty = bag.get("menu_qty_per_serving")
        servings = bag.get("quantity")
        if not unit or per_qty is None or servings is None:
            if not bag.get("bag_type"):
                bag["bag_type"] = "standard"
            continue
        candidates = bag_types_by_unit.get(unit)
        if not candidates:
            if not bag.get("bag_type"):
                bag["bag_type"] = "standard"
            continue
        try:
            total_weight = float(per_qty) * float(servings)
        except Exception:
            if not bag.get("bag_type"):
                bag["bag_type"] = "standard"
            continue
        selected = None
        for entry in candidates:
            if total_weight <= entry["max_qty"]:
                selected = entry
                break
        if not selected:
            selected = candidates[-1]
        bag["bag_type"] = selected["bag_type_id"]
    return bags


def _exception_applies(rule: dict, line: dict) -> bool:
    menu_pattern = rule.get("menu_pattern")
    if menu_pattern:
        if not _match_menu_pattern(line.get("menu_name") or "", menu_pattern, rule.get("match_type")):
            return False
    if rule.get("daypart") and rule.get("daypart") != line.get("daypart"):
        return False
    if rule.get("diet_type") and rule.get("diet_type") != line.get("diet_type"):
        return False
    if rule.get("category") and rule.get("category") != line.get("menu_category"):
        return False
    return True


def _apply_bagging_exceptions(lines: list[dict], facility_config: dict | None) -> list[dict]:
    if not isinstance(facility_config, dict):
        return lines
    exceptions = facility_config.get("bagging_exceptions") or []
    if not isinstance(exceptions, list) or not exceptions:
        return lines
    enriched = [dict(line) for line in lines]
    for rule in exceptions:
        if not isinstance(rule, dict):
            continue
        rule_type = rule.get("type") or rule.get("id")
        if rule_type == "quantity_multiplier":
            multiplier = rule.get("multiplier")
            try:
                multiplier = float(multiplier)
            except Exception:
                continue
            if multiplier <= 0:
                continue
            for line in enriched:
                if not _exception_applies(rule, line):
                    continue
                if line.get("quantity_original") is not None:
                    line["quantity_original"] = float(line["quantity_original"]) * multiplier
                if line.get("quantity_corrected") is not None:
                    line["quantity_corrected"] = float(line["quantity_corrected"]) * multiplier
                line.setdefault("change_note", "袋分け例外適用")
    return enriched


def build_order_lines_for_outputs(order: dict) -> list[dict]:
    facility_id = order.get("facility")
    week_value = (
        str(order.get("stored_week_value") or "").strip()
        or str(order.get("week_value") or "").strip()
        or str(order.get("persisted_week_value") or "").strip()
        or str(order.get("week") or "").strip()
        or str(order.get("week_code") or "").strip()
    )
    facility_config = config_service.get_facility_config(facility_id) if facility_id else None
    raw_lines = order.get("lines", [])
    raw_lines = order_service._apply_change_override_priority_to_lines(raw_lines)  # noqa: SLF001
    raw_lines = _apply_garnish_lines(raw_lines)
    menu_entries = order_service._collect_menu_entries_for_week(week_value, facility_id) if week_value else []  # noqa: SLF001
    raw_lines = _apply_menu_entry_overrides(raw_lines, menu_entries)
    menu_items = order_service._collect_menu_items_for_week(week_value, facility_id) if week_value else []  # noqa: SLF001
    menu_entries = order_service._collect_menu_entries_for_week(week_value, facility_id) if week_value else []  # noqa: SLF001
    snapshot = get_order_menu_snapshot(order.get("id"))
    snapshot_items = snapshot.get("menu_items") if isinstance(snapshot, dict) else None
    if snapshot_items:
        order_lines = _apply_menu_snapshot(raw_lines, snapshot_items)
    else:
        order_lines = raw_lines
    # Current monthly/menu-master settings must win over stale confirmed snapshots.
    order_lines = _apply_menu_overrides(order_lines, menu_items)
    order_lines = _apply_menu_entry_overrides(order_lines, menu_entries)
    order_lines = _clear_stale_menu_qty_from_monthly_entry(order_lines)
    order_lines = _apply_menu_rules(order_lines, facility_id)
    order_lines = _apply_garnish_defaults(order_lines)
    order_lines = _apply_builtin_menu_defaults(order_lines)
    order_lines = daily_output_override_service.apply_overrides_to_lines(order_lines, facility_id)
    order_lines = _apply_bagging_exceptions(order_lines, facility_config)
    order_lines = _apply_condiment_lines(order_lines)
    bag_types = _resolve_bag_types(facility_config)
    order_lines = _apply_bag_size_defaults(order_lines, bag_types)
    return order_lines


def _normalize_rule_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", str(value)).lower()


def _split_garnish_name(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    match = _GARNISH_SPLIT_RE.search(text)
    if not match:
        return text, None
    base = text[: match.start()].strip().strip("　")
    garnish = text[match.end() :].strip().strip("　")
    if not base:
        return text, None
    if not garnish:
        return base, None
    return base, garnish


def _apply_garnish_lines(lines: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for line in lines:
        name = (line.get("menu_name") or "").strip()
        base, garnish = _split_garnish_name(name)
        if not garnish:
            if base and base != name:
                updated = dict(line)
                updated["menu_name"] = base
                enriched.append(updated)
            else:
                enriched.append(line)
            continue
        updated = dict(line)
        updated["menu_name"] = base
        enriched.append(updated)
        garnish_line = dict(line)
        garnish_line["menu_name"] = garnish
        garnish_line["menu_category"] = "添え"
        for field in (
            "menu_qty_per_serving",
            "menu_unit_type",
            "actual_amount",
            "actual_unit_type",
            "menu_bag_max_qty",
            "menu_bag_max_unit",
            "_menu_qty_source_daypart",
            "_menu_qty_source_category",
            "_monthly_entry_override_applied",
        ):
            garnish_line.pop(field, None)
        enriched.append(garnish_line)
    return enriched


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
    if rule.get("daypart"):
        rule_daypart = _normalize_output_daypart(rule.get("daypart"))
        line_daypart = _normalize_output_daypart(line.get("daypart"))
        if rule_daypart != line_daypart:
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


def _apply_builtin_menu_defaults(lines: list[dict]) -> list[dict]:
    defaults = menu_rule_service.DEFAULT_GLOBAL_RULES
    if not defaults:
        return lines
    enriched: list[dict] = []
    for line in lines:
        if line.get("menu_qty_per_serving") is not None:
            enriched.append(line)
            continue
        line_daypart = _normalize_output_daypart(line.get("daypart"))
        line_category = _normalize_category_key(line.get("menu_category"))
        matched = next(
            (
                rule
                for rule in defaults
                if _normalize_output_daypart(rule.get("daypart")) == line_daypart
                and _normalize_category_key(rule.get("category")) == line_category
            ),
            None,
        )
        if not matched:
            enriched.append(line)
            continue
        updated = dict(line)
        updated["menu_unit_type"] = matched.get("unit_type")
        updated["menu_qty_per_serving"] = matched.get("qty_per_serving")
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
        is_condiment = line.get("bag_type") == "condiment"
        default_bag_type = line.get("bag_type") or "standard"
        menu_category = "付属品" if is_condiment else line.get("menu_category")
        key_parts: list[Any] = []
        for part in split_key:
            if part == "facility":
                key_parts.append(order.get("facility"))
                continue
            if part == "date":
                key_parts.append(line_date)
                continue
            if part == "menu_name":
                key_parts.append("condiment" if is_condiment else line.get("menu_name"))
                continue
            if part == "bag_type":
                key_parts.append("condiment" if is_condiment else default_bag_type)
                continue
            if is_condiment and part in {"diet_type", "area_id"}:
                # 付属品は area/diet を跨いで1袋にまとめる。
                key_parts.append("__condiment__")
                continue
            key_parts.append(line.get(part))
        key = tuple(key_parts)
        if key not in grouped:
            grouped[key] = {
                "order_id": order["id"],
                "facility": order.get("facility"),
                "date": line_date,
                "daypart": line.get("daypart"),
                "menu_name": line.get("menu_name"),
                "menu_category": menu_category,
                "diet_type": None if is_condiment else line.get("diet_type"),
                "area_id": None if is_condiment else line.get("area_id"),
                "bag_type": "condiment" if is_condiment else default_bag_type,
                "menu_unit_type": line.get("menu_unit_type"),
                "menu_qty_per_serving": line.get("menu_qty_per_serving"),
                "menu_bag_max_qty": line.get("menu_bag_max_qty"),
                "menu_bag_max_unit": line.get("menu_bag_max_unit"),
                "menu_temp_type": line.get("menu_temp_type"),
                "quantity": 0.0,
                "_condiment_names": set(),
            }
        if is_condiment:
            name_value = (line.get("menu_name") or "").strip()
            if name_value:
                grouped[key]["_condiment_names"].add(name_value)
        grouped[key]["quantity"] += float(qty)
    result = list(grouped.values())
    for bag in result:
        if bag.get("bag_type") == "condiment":
            names = sorted(bag.pop("_condiment_names", set()))
            if names:
                bag["menu_name"] = " / ".join(names)
            bag["menu_category"] = "付属品"
    return result


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
    unit = _normalize_unit_type(bag.get("menu_unit_type"))
    bag_unit = _normalize_unit_type(bag.get("menu_bag_max_unit")) or unit
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


def _serialize_bag_payload_rows(rows: list[dict]) -> list[dict]:
    payload = [
        {
            "date": bag.get("date").isoformat() if bag.get("date") else None,
            "daypart": bag.get("daypart"),
            "menu_name": bag.get("menu_name"),
            "menu_category": bag.get("menu_category"),
            "diet_type": bag.get("diet_type"),
            "area_id": bag.get("area_id"),
            "bag_type": bag.get("bag_type"),
            "quantity": bag.get("quantity"),
        }
        for bag in rows
    ]
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
    return payload


def build_bag_rows_for_outputs(
    order: dict,
    *,
    order_lines: list[dict] | None = None,
    facility_config: dict | None = None,
) -> list[dict]:
    facility_id = order.get("facility")
    resolved_facility_config = facility_config
    if not resolved_facility_config and facility_id:
        resolved_facility_config = config_service.get_facility_config(facility_id)
    if not resolved_facility_config:
        logger.warning("Facility config missing", facility_id=facility_id)
        resolved_facility_config = {}

    packaging_policy = resolved_facility_config.get("packaging_policy", {})
    quantity_rules = config_service.load_ingest_policy().get("quantity_rules", {})

    resolved_lines = order_lines if isinstance(order_lines, list) else build_order_lines_for_outputs(order)
    order_for_outputs = {**order, "lines": resolved_lines}

    bags = _split_bags_by_max(_build_bags(order_for_outputs, packaging_policy, quantity_rules))
    bag_types = _resolve_bag_types(resolved_facility_config)
    return _assign_bag_type_for_bags(bags, bag_types)


def build_bag_payload_for_outputs(
    order: dict,
    *,
    order_lines: list[dict] | None = None,
    facility_config: dict | None = None,
) -> list[dict]:
    return _serialize_bag_payload_rows(
        build_bag_rows_for_outputs(
            order,
            order_lines=order_lines,
            facility_config=facility_config,
        )
    )


def _label_payload_legacy(bag: dict, label_profile: dict, facility_name: str | None) -> dict:
    fixed_text = label_profile.get("fixed_text", {})
    expiry_rule = label_profile.get("expiry_rule", "meal_date")
    expiry_date = bag.get("date")
    if expiry_rule == "meal_date" and expiry_date:
        expiry_value = _resolve_label_expiry_date(expiry_date, label_profile)
    else:
        expiry_value = _resolve_label_expiry_date(expiry_date, label_profile)
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

def _label_payload_jp(bag: dict, label_profile: dict | None = None) -> dict:
    per_qty, unit = _extract_qty_and_unit(bag.get("menu_qty_per_serving"), bag.get("menu_unit_type"))
    servings = bag.get("quantity")
    total_qty = None
    if per_qty is not None and servings is not None:
        try:
            total_qty = float(per_qty) * float(servings)
        except Exception:
            total_qty = None
    menu_value = bag.get("menu_category") or ""
    product_name = bag.get("menu_name") or ""
    # 重複表示を避けるため、メニュー列は分類（主菜/副菜など）を優先して扱う。
    if menu_value and product_name and str(menu_value).strip() == str(product_name).strip():
        menu_value = ""
    real_amount = _format_amount(total_qty, unit)
    total_amount = real_amount or _format_servings(servings)
    detail_value = _build_label_details(bag) or _format_amount(per_qty, unit)
    return {
        "呼び出し番号": "",
        "発行枚数": 1,
        "賞味期限": _format_jp_date(_resolve_label_expiry_date(bag.get("date"), label_profile)),
        "時間": bag.get("daypart") or "",
        "メニュー": menu_value,
        "温・冷": _normalize_temp_label(bag.get("menu_temp_type")),
        "商品名１": product_name,
        "商品名２": "",
        "内容量": total_amount,
        "内容詳細": detail_value,
        "実量": real_amount,
        "一人前": _format_amount(per_qty, unit),
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


def _find_delivery_header_row(ws, columns: list[dict]) -> int | None:
    targets = []
    for col in columns:
        header = col.get("header") or col.get("name")
        if header:
            targets.append(_normalize_cell_text(header))
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


def _build_delivery_column_map(ws, header_row: int, columns: list[dict]) -> dict[str, int]:
    column_map: dict[str, int] = {}
    header_cells = list(ws[header_row])
    for col in columns:
        name = col.get("name")
        if not name:
            continue
        column_index = col.get("column_index")
        if isinstance(column_index, int) and column_index > 0:
            column_map[name] = column_index
            continue
        header = col.get("header") or name
        normalized = _normalize_cell_text(header)
        if not normalized:
            continue
        for cell in header_cells:
            cell_text = _normalize_cell_text(cell.value)
            if normalized and normalized in cell_text:
                column_map[name] = cell.col_idx
                break
    return column_map


def _delivery_start_row(ws, header_row: int) -> int:
    max_row = header_row
    for merged in ws.merged_cells.ranges:
        if merged.min_row <= header_row <= merged.max_row:
            max_row = max(max_row, merged.max_row)
    return max_row + 1


def _normalize_delivery_daypart(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    if "朝" in text:
        return "朝"
    if "昼" in text:
        return "昼"
    if "夕" in text or "夜" in text:
        return "夕"
    return text


def _normalize_ocr_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = text.replace("\\(", "(").replace("\\)", ")")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_menu_key(value: Any) -> str:
    return _normalize_ocr_text(value)


def _normalize_ocr_daypart(value: Any) -> str:
    text = _normalize_ocr_text(value)
    if not text:
        return ""
    if "朝" in text or "明" in text:
        return "朝"
    if "昼" in text or "星" in text or "a" in text or "中" in text:
        return "昼"
    if "夕" in text or "タ" in text or "夜" in text:
        return "夕"
    return text


def _normalize_ocr_category(value: Any) -> str:
    text = _normalize_ocr_text(value)
    if not text:
        return ""
    if "主" in text:
        if "A" in text or "Ａ" in text:
            return "主Ａ"
        if "B" in text or "Ｂ" in text:
            return "主Ｂ"
        return "主"
    if "副" in text:
        if "2" in text or "②" in text:
            return "副②"
        return "副①"
    if text.isdigit():
        if "2" in text:
            return "副②"
        if "1" in text:
            return "副①"
    return ""


def _parse_ocr_date(value: str, year_hint: int | None, fallback: dt_date | None) -> dt_date | None:
    text = _normalize_ocr_text(value)
    if not text:
        return fallback
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        try:
            return dt_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return fallback
    match = re.search(r"(\d{1,2})[月/](\d{1,2})", text)
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        year = year_hint or (fallback.year if fallback else None)
        if not year:
            return fallback
        try:
            return dt_date(year, month, day)
        except ValueError:
            return fallback
    if text.isdigit():
        try:
            serial = int(text)
        except ValueError:
            return fallback
        if serial > 10000:
            base = dt_date(1899, 12, 30)
            return base + timedelta(days=serial)
    return fallback


def _parse_ocr_quantity(value: Any) -> float | None:
    text = _normalize_ocr_text(value)
    if not text:
        return None
    match = re.search(r"[-+]?[0-9]*\.?[0-9]+", text)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _parse_ocr_table_rows(table_raw: str, year_hint: int | None) -> list[dict]:
    rows: list[dict] = []
    current_date: dt_date | None = None
    current_daypart = ""
    in_table = False
    header_seen = False
    for line in table_raw.splitlines():
        raw_line = line.lstrip()
        if not in_table and raw_line.startswith("|") and "日付" in raw_line and "献立" in raw_line:
            in_table = True
            header_seen = True
            continue
        if not in_table:
            continue
        if raw_line.startswith("|-"):
            continue
        if not raw_line.startswith("|"):
            if header_seen:
                break
            continue
        cells = [cell.strip() for cell in raw_line.split("|")[1:-1]]
        if len(cells) < 4:
            continue
        date_cell = _normalize_ocr_text(cells[0])
        if date_cell:
            parsed_date = _parse_ocr_date(date_cell, year_hint, current_date)
            if parsed_date:
                current_date = parsed_date
        daypart_cell = _normalize_ocr_text(cells[1])
        if daypart_cell:
            current_daypart = _normalize_ocr_daypart(daypart_cell)
        category_cell = _normalize_ocr_category(cells[2])
        menu_name = _normalize_ocr_text(cells[3])
        if not menu_name:
            continue
        qty_values = [None] * 6
        note_value = _normalize_ocr_text(cells[10]) if len(cells) >= 11 else ""
        if len(cells) >= 10:
            qty_cells = cells[4:10]
            if len(cells) >= 11 and not cells[4] and sum(1 for c in qty_cells if _normalize_ocr_text(c)) == 5:
                qty_cells = cells[5:10] + [None]
            qty_values = [
                _parse_ocr_quantity(qty_cells[0]),
                _parse_ocr_quantity(qty_cells[1]),
                _parse_ocr_quantity(qty_cells[2]),
                _parse_ocr_quantity(qty_cells[3]),
                _parse_ocr_quantity(qty_cells[4]),
                _parse_ocr_quantity(qty_cells[5]),
            ]
        rows.append(
            {
                "date": current_date,
                "daypart": current_daypart,
                "category": category_cell,
                "menu_name": menu_name,
                "qty_regular_2f": qty_values[0],
                "qty_regular_3f": qty_values[1],
                "qty_soft_2f": qty_values[2],
                "qty_soft_3f": qty_values[3],
                "qty_mixer_2f": qty_values[4],
                "qty_mixer_3f": qty_values[5],
                "note": note_value,
            }
        )
    return rows


def _resolve_ocr_year_hint(order: dict) -> int | None:
    year_hint = None
    for line in order.get("lines", []):
        date_val = _ensure_date(line.get("date"))
        if date_val:
            year_hint = date_val.year
            break
    if not year_hint:
        week_value = order.get("week") or order.get("week_code")
        week_text = str(week_value) if week_value is not None else ""
        match = re.search(r"(\d{4})", week_text)
        if match:
            year_hint = int(match.group(1))
    if not year_hint:
        received_at = order.get("received_at")
        if isinstance(received_at, dt_date):
            year_hint = received_at.year
        elif isinstance(received_at, str):
            match = re.search(r"(\d{4})", received_at)
            if match:
                year_hint = int(match.group(1))
    return year_hint


def _load_order_ocr_payload(order_id: str) -> dict[str, Any] | None:
    parsed, _ = order_service.get_ocr_output(order_id)
    if isinstance(parsed, dict):
        return parsed
    raw_text, _ = order_service.get_ocr_raw_text(order_id)
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    raw_text = raw_text.strip()
    if raw_text.startswith("{"):
        try:
            parsed_raw = json.loads(raw_text)
        except Exception:  # noqa: BLE001
            parsed_raw = None
        if isinstance(parsed_raw, dict):
            return parsed_raw
    return {"table_raw": raw_text}


def _entry_quantity_key(diet: str | None, area: str | None) -> str | None:
    diet_key = _normalize_diet_key(diet)
    area_key = str(area or "").strip().lower()
    if not diet_key or not area_key:
        return None
    return f"{diet_key}_{area_key}"


def _legacy_entry_quantity_field(diet: str | None, area: str | None) -> str | None:
    diet_key = _normalize_diet_key(diet)
    area_key = str(area or "").strip().upper()
    if diet_key not in {"regular", "soft", "mixer"}:
        return None
    if area_key not in {"2F", "3F"}:
        return None
    return f"qty_{diet_key}_{area_key.lower()}"


def _sum_quantity_map(quantity_map: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    total = 0.0
    matched = False
    for key in keys:
        qty = _parse_ocr_quantity(quantity_map.get(key))
        if qty is None:
            continue
        total += qty
        matched = True
    if not matched:
        return None
    return total if total > 0 else None


def _generic_quantity_total(quantity_map: dict[str, Any], diet_key: str | None) -> float | None:
    normalized_diet = _normalize_diet_key(diet_key)
    if normalized_diet == "regular":
        override_qty = (
            _parse_ocr_quantity(quantity_map.get("change_2_x"))
            if "change_2_x" in quantity_map
            else None
        )
        if override_qty is None and "change_1_x" in quantity_map:
            override_qty = _parse_ocr_quantity(quantity_map.get("change_1_x"))
        if override_qty is not None:
            return override_qty
        return _sum_quantity_map(quantity_map, ("regular_x", "regular_bag_x", "staff_x", "daycare_x"))
    if normalized_diet == "regular_bag":
        return _sum_quantity_map(quantity_map, ("regular_bag_x",))
    if normalized_diet == "no_fried":
        return _sum_quantity_map(quantity_map, ("no_fried_x",))
    if normalized_diet == "soft":
        return _sum_quantity_map(quantity_map, ("soft_x",))
    if normalized_diet == "mixer":
        return _sum_quantity_map(quantity_map, ("mixer_x",))
    if normalized_diet in {"禁食", "forbidden"}:
        return _sum_quantity_map(quantity_map, ("forbidden_x", "no_meat_x", "no_fish_x", "forbidden_other_x"))
    return None


def _resolve_payload_template(payload: dict[str, Any], template: dict[str, Any] | None) -> dict[str, Any] | None:
    resolved = template if isinstance(template, dict) else None
    if not isinstance(payload, dict):
        return resolved
    template_id = payload.get("template_id")
    if not isinstance(template_id, str):
        classification = payload.get("classification")
        if isinstance(classification, dict):
            template_id = classification.get("matched_template_id")
    if not isinstance(template_id, str):
        return resolved
    template_id = template_id.strip()
    if not template_id:
        return resolved
    if isinstance(resolved, dict) and resolved.get("template_id") == template_id:
        return resolved
    registry = config_service.load_fax_template_registry()
    matched = registry.get(template_id)
    if isinstance(matched, dict) and matched:
        return matched
    return resolved


def _extract_ocr_entries_from_structured_payload(
    payload: dict[str, Any],
    template: dict[str, Any] | None,
    year_hint: int | None,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    template = _resolve_payload_template(payload, template)
    if not isinstance(template, dict):
        return []
    rows = order_service._extract_sheet_rows_from_payload(payload, template)
    if not rows:
        return []
    cell_issues = order_service._extract_payload_cell_issues(payload, template)
    issues_by_row: dict[int, list[dict[str, Any]]] = {}
    for issue in cell_issues:
        if not isinstance(issue, dict):
            continue
        row_index = issue.get("source_row_index")
        if not isinstance(row_index, int) or row_index < 0:
            continue
        issues_by_row.setdefault(row_index, []).append(dict(issue))
    fields, field_index = order_service._build_sheet_fields_and_indexes(template)
    if not fields:
        return []
    date_idx = field_index.get("date_mmdd")
    if date_idx is None:
        date_idx = field_index.get("date")
    daypart_idx = field_index.get("daypart")
    menu_idx = field_index.get("menu")
    if menu_idx is None:
        menu_idx = field_index.get("menu_name")
    note_idx = field_index.get("remarks")
    if note_idx is None:
        note_idx = field_index.get("note")

    quantity_fields: list[tuple[int, str, str]] = []
    for idx, field in enumerate(fields):
        diet, area = order_service._quantity_meta_from_field(field)
        if not diet or not area:
            continue
        quantity_fields.append((idx, diet, area))

    current_date: dt_date | None = None
    current_daypart = ""
    entries: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        if date_idx is not None and date_idx < len(row):
            parsed_date = _parse_ocr_date(str(row[date_idx] or ""), year_hint, current_date)
            if parsed_date:
                current_date = parsed_date
        if daypart_idx is not None and daypart_idx < len(row):
            daypart_value = _normalize_ocr_daypart(row[daypart_idx])
            if daypart_value:
                current_daypart = daypart_value
        menu_name = ""
        if menu_idx is not None and menu_idx < len(row):
            menu_name = _normalize_ocr_text(row[menu_idx])
        if not menu_name:
            continue
        note = ""
        if note_idx is not None and note_idx < len(row):
            note = _normalize_ocr_text(row[note_idx])
        quantity_map: dict[str, float] = {}
        payload_entry: dict[str, Any] = {
            "date": current_date,
            "daypart": current_daypart,
            "category": "",
            "menu_name": menu_name,
            "note": note,
            "index": idx,
            "quantity_map": quantity_map,
            "source": "structured_rows",
        }
        if idx in issues_by_row:
            payload_entry["ocr_issues"] = issues_by_row[idx]
            payload_entry["needs_review"] = True
        for col_idx, diet, area in quantity_fields:
            if col_idx >= len(row):
                continue
            qty = _parse_ocr_quantity(row[col_idx])
            if qty is None:
                continue
            key = _entry_quantity_key(diet, area)
            if key:
                quantity_map[key] = qty
            legacy_field = _legacy_entry_quantity_field(diet, area)
            if legacy_field:
                payload_entry[legacy_field] = qty
        entries.append(payload_entry)
    return entries


def _lookup_ocr_entry_quantity(entry: dict[str, Any], diet_key: str | None, area_key: str | None) -> float | None:
    if not isinstance(entry, dict):
        return None
    quantity_map = entry.get("quantity_map")
    normalized_diet = _normalize_diet_key(diet_key)
    normalized_area = str(area_key or "").strip().upper()
    if isinstance(quantity_map, dict):
        key = _entry_quantity_key(normalized_diet, area_key)
        if key and key in quantity_map:
            exact_qty = _parse_ocr_quantity(quantity_map.get(key))
            if normalized_area not in {"", "X"} or normalized_diet not in {"regular", "禁食", "forbidden"}:
                return exact_qty
        if normalized_area in {"", "X"}:
            generic_total = _generic_quantity_total(quantity_map, normalized_diet)
            if generic_total is not None:
                return generic_total
    if normalized_diet == "regular":
        if normalized_area == "2F":
            return _parse_ocr_quantity(entry.get("qty_regular_2f"))
        if normalized_area == "3F":
            return _parse_ocr_quantity(entry.get("qty_regular_3f"))
        qty_2f = _parse_ocr_quantity(entry.get("qty_regular_2f")) or 0.0
        qty_3f = _parse_ocr_quantity(entry.get("qty_regular_3f")) or 0.0
        total = qty_2f + qty_3f
        return total if total > 0 else None
    if normalized_diet == "soft":
        if normalized_area == "2F":
            return _parse_ocr_quantity(entry.get("qty_soft_2f"))
        if normalized_area == "3F":
            return _parse_ocr_quantity(entry.get("qty_soft_3f"))
        qty_2f = _parse_ocr_quantity(entry.get("qty_soft_2f")) or 0.0
        qty_3f = _parse_ocr_quantity(entry.get("qty_soft_3f")) or 0.0
        total = qty_2f + qty_3f
        return total if total > 0 else None
    if normalized_diet == "mixer":
        if normalized_area == "2F":
            return _parse_ocr_quantity(entry.get("qty_mixer_2f"))
        if normalized_area == "3F":
            return _parse_ocr_quantity(entry.get("qty_mixer_3f"))
        qty_2f = _parse_ocr_quantity(entry.get("qty_mixer_2f")) or 0.0
        qty_3f = _parse_ocr_quantity(entry.get("qty_mixer_3f")) or 0.0
        total = qty_2f + qty_3f
        return total if total > 0 else None
    return None


def _build_ocr_menu_meta(order: dict, facility_config: dict | None = None) -> dict[str, object]:
    order_id = order.get("id")
    if not order_id:
        return {}
    parsed = _load_order_ocr_payload(str(order_id))
    if not isinstance(parsed, dict):
        return {}
    year_hint = _resolve_ocr_year_hint(order)
    template = facility_config.get("fax_template") if isinstance(facility_config, dict) else None
    entries = _extract_ocr_entries_from_structured_payload(parsed, template, year_hint)
    if not entries:
        table_raw = parsed.get("table_raw")
        if not isinstance(table_raw, str) or not table_raw.strip():
            return {}
        entries = _parse_ocr_table_rows(table_raw, year_hint)
    meta: dict[tuple[dt_date, str], dict] = {}
    normalized_entries: list[dict] = []
    for idx, entry in enumerate(entries):
        date_val = entry.get("date")
        menu_name = _normalize_menu_key(entry.get("menu_name"))
        if not date_val or not menu_name:
            continue
        key = (date_val, menu_name)
        payload = {
            "date": date_val,
            "menu_name": entry.get("menu_name"),
            "daypart": entry.get("daypart"),
            "category": entry.get("category"),
            "index": idx,
            "qty_regular_2f": entry.get("qty_regular_2f"),
            "qty_regular_3f": entry.get("qty_regular_3f"),
            "qty_soft_2f": entry.get("qty_soft_2f"),
            "qty_soft_3f": entry.get("qty_soft_3f"),
            "qty_mixer_2f": entry.get("qty_mixer_2f"),
            "qty_mixer_3f": entry.get("qty_mixer_3f"),
            "quantity_map": dict(entry.get("quantity_map") or {}),
            "note": entry.get("note"),
            "source": entry.get("source") or "table_raw",
            "ocr_issues": list(entry.get("ocr_issues") or []),
            "needs_review": bool(entry.get("needs_review")),
        }
        normalized_entries.append(payload)
        if key in meta:
            continue
        meta[key] = payload
    return {
        "by_menu": meta,
        "entries": normalized_entries,
        "issues": [
            issue
            for entry in normalized_entries
            for issue in (entry.get("ocr_issues") or [])
            if isinstance(issue, dict)
        ],
        "review_required_count": sum(1 for entry in normalized_entries if entry.get("needs_review")),
    }


def _resolve_delivery_cell(row: dict, column: dict) -> Any:
    source = column.get("source") or column.get("name")
    value = None
    if source == "quantity":
        value = row.get(column.get("name", ""))
    else:
        value = row.get(source)
        if value is None and column.get("name"):
            value = row.get(column["name"])
    if source == "daypart":
        return _normalize_delivery_daypart(value)
    return value


def _copy_cell_style(source, target) -> None:
    target.font = copy(source.font)
    target.border = copy(source.border)
    target.fill = copy(source.fill)
    target.number_format = copy(source.number_format)
    target.protection = copy(source.protection)
    target.alignment = copy(source.alignment)


def _resolve_merged_cell(ws, row: int, column: int):
    cell = ws.cell(row=row, column=column)
    if not isinstance(cell, MergedCell):
        return cell
    for merged in ws.merged_cells.ranges:
        if merged.min_row <= row <= merged.max_row and merged.min_col <= column <= merged.max_col:
            return ws.cell(row=merged.min_row, column=merged.min_col)
    return cell


def _apply_delivery_facility_name(ws, facility_name: str | None) -> None:
    if not facility_name:
        return
    target_cell = None
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        for cell in row:
            if cell.value is None:
                continue
            text = _normalize_cell_text(cell.value)
            if "施設名" in text:
                target_cell = _resolve_merged_cell(ws, cell.row + 1, cell.col_idx)
                break
        if target_cell:
            break
    if target_cell:
        target_cell.value = facility_name


def _resolve_delivery_column_index(columns: list[dict], column_map: dict[str, int], source: str) -> int | None:
    for col in columns:
        if col.get("source") != source:
            continue
        name = col.get("name")
        if name and name in column_map:
            return column_map[name]
    return None


def _resolve_delivery_menu_column(columns: list[dict], column_map: dict[str, int]) -> int | None:
    for col in columns:
        name = str(col.get("name") or "")
        if col.get("source") == "menu_name" or "献立" in name:
            col_idx = column_map.get(col.get("name"))
            if col_idx:
                return col_idx
    return None


def _normalize_slot_label(value: Any) -> str:
    text = _normalize_cell_text(value)
    if not text:
        return ""
    if "主" in text:
        if "A" in text or "Ａ" in text:
            return "主Ａ"
        if "B" in text or "Ｂ" in text:
            return "主Ｂ"
        return "主"
    if "副" in text:
        if "2" in text or "②" in text:
            return "副②"
        return "副①"
    return text


def _find_delivery_slot_rows(ws, menu_col_idx: int) -> list[int]:
    slots: list[int] = []
    for row in range(1, ws.max_row + 1):
        cell = ws.cell(row=row, column=menu_col_idx)
        text = _normalize_cell_text(cell.value)
        if not text:
            continue
        if "副" in text or "主" in text:
            slots.append(row)
    return slots


def _find_daypart_rows(ws, daypart_col_idx: int) -> dict[str, int]:
    rows: dict[str, int] = {}
    for row in range(1, ws.max_row + 1):
        text = _normalize_cell_text(ws.cell(row=row, column=daypart_col_idx).value)
        if not text:
            continue
        normalized = _normalize_delivery_daypart(text)
        if normalized and normalized not in rows:
            rows[normalized] = row
    return rows


def _format_delivery_weekday(value: dt_date | None) -> str:
    if not value:
        return ""
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    return f"({weekdays[value.weekday()]})"


def _build_delivery_slot_map(
    ws,
    slot_rows: list[int],
    menu_col_idx: int,
    daypart_col_idx: int | None,
) -> dict[tuple[str, str], int]:
    slot_map: dict[tuple[str, str], int] = {}
    current_daypart = ""
    for row in slot_rows:
        if daypart_col_idx:
            daypart_text = _normalize_cell_text(ws.cell(row=row, column=daypart_col_idx).value)
            if daypart_text:
                current_daypart = _normalize_delivery_daypart(daypart_text)
        slot_label = _normalize_slot_label(ws.cell(row=row, column=menu_col_idx).value)
        if current_daypart and slot_label:
            slot_map[(current_daypart, slot_label)] = row
    return slot_map


def _build_delivery_slot_label_map(ws, slot_rows: list[int], menu_col_idx: int) -> dict[str, list[int]]:
    label_map: dict[str, list[int]] = {}
    for row in slot_rows:
        slot_label = _normalize_slot_label(ws.cell(row=row, column=menu_col_idx).value)
        if not slot_label:
            continue
        label_map.setdefault(slot_label, []).append(row)
    return label_map


def _build_delivery_slot_label_map_by_daypart(
    ws,
    slot_rows: list[int],
    menu_col_idx: int,
    daypart_col_idx: int | None,
) -> dict[str, dict[str, list[int]]]:
    label_map: dict[str, dict[str, list[int]]] = {}
    current_daypart = ""
    for row in slot_rows:
        if daypart_col_idx:
            daypart_text = _normalize_cell_text(ws.cell(row=row, column=daypart_col_idx).value)
            if daypart_text:
                current_daypart = _normalize_delivery_daypart(daypart_text)
        slot_label = _normalize_slot_label(ws.cell(row=row, column=menu_col_idx).value)
        if current_daypart and slot_label:
            label_map.setdefault(current_daypart, {}).setdefault(slot_label, []).append(row)
    return label_map


def _write_delivery_slot_row(
    ws,
    row_idx: int,
    row: dict,
    columns: list[dict],
    column_map: dict[str, int],
    include_menu_name: bool,
) -> None:
    for col in columns:
        name = col.get("name")
        if not name:
            continue
        col_idx = column_map.get(name)
        if not col_idx:
            continue
        source = col.get("source")
        if source in {"menu_name", "menu_display"}:
            if source == "menu_name" and not include_menu_name:
                continue
            value = row.get("menu_display") if source == "menu_display" else row.get("menu_name")
        elif source == "quantity":
            value = row.get(name)
        elif source == "note":
            value = row.get("note")
        else:
            continue
        cell = _resolve_merged_cell(ws, row_idx, col_idx)
        if isinstance(cell, MergedCell):
            continue
        cell.value = "" if value is None else value


def _clear_delivery_slot_row(
    ws,
    row_idx: int,
    columns: list[dict],
    column_map: dict[str, int],
    include_menu_name: bool,
) -> None:
    for col in columns:
        name = col.get("name")
        if not name:
            continue
        source = col.get("source")
        if source not in {"menu_name", "menu_display", "quantity", "note"}:
            continue
        if source == "menu_name" and not include_menu_name:
            continue
        col_idx = column_map.get(name)
        if not col_idx:
            continue
        cell = _resolve_merged_cell(ws, row_idx, col_idx)
        if isinstance(cell, MergedCell):
            continue
        cell.value = ""


def _assign_delivery_rows_to_slots(
    rows_for_date: list[dict],
    slot_rows: list[int],
    slot_map: dict[tuple[str, str], int],
    slot_label_map: dict[str, list[int]],
    slot_label_map_by_daypart: dict[str, dict[str, list[int]]],
    slot_rows_by_daypart: dict[str, list[int]],
) -> dict[int, dict]:
    assignments: dict[int, dict] = {}
    used_rows: set[int] = set()
    pending: list[dict] = []
    rows_for_date.sort(
        key=lambda row: (
            row.get("_order_index") if row.get("_order_index") is not None else 1_000_000,
            row.get("menu_name") or "",
        )
    )
    for row in rows_for_date:
        daypart = _normalize_delivery_daypart(row.get("daypart"))
        slot_label = _normalize_slot_label(row.get("menu_category"))
        target_row = slot_map.get((daypart, slot_label)) if daypart and slot_label else None
        if not target_row and slot_label:
            candidates = slot_label_map_by_daypart.get(daypart, {}).get(slot_label, []) if daypart else []
            if not candidates:
                candidates = slot_label_map.get(slot_label, [])
            for candidate in candidates:
                if candidate not in used_rows:
                    target_row = candidate
                    break
        if not target_row and daypart:
            for candidate in slot_rows_by_daypart.get(daypart, []):
                if candidate not in used_rows:
                    target_row = candidate
                    break
        if target_row and target_row not in used_rows:
            assignments[target_row] = row
            used_rows.add(target_row)
        else:
            pending.append(row)
    pending_iter = iter(pending)
    for slot_row in slot_rows:
        if slot_row in assignments:
            continue
        row = next(pending_iter, None)
        if row:
            assignments[slot_row] = row
            used_rows.add(slot_row)
    return assignments


def _write_delivery_note(
    path: Path,
    rows: list[dict],
    columns: list[dict],
    template_uri: str | None,
    include_menu_name: bool,
    sheet_name: str | None = None,
    facility_name: str | None = None,
) -> None:
    if not template_uri:
        if not rows:
            df = pd.DataFrame(columns=[col["name"] for col in columns])
        else:
            df = pd.DataFrame(rows)
        df.to_excel(path, index=False)
        return

    try:
        template_bytes = load_bytes_from_uri(template_uri)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Delivery template load failed", template_uri=template_uri, error=str(exc))
        if not rows:
            df = pd.DataFrame(columns=[col["name"] for col in columns])
        else:
            df = pd.DataFrame(rows)
        df.to_excel(path, index=False)
        return
    workbook = load_workbook(BytesIO(template_bytes))
    if sheet_name and sheet_name in workbook.sheetnames:
        ws = workbook[sheet_name]
    else:
        ws = workbook.active
    _apply_delivery_facility_name(ws, facility_name)

    header_row = _find_delivery_header_row(ws, columns)
    if not header_row:
        header_row = 1
    column_map = _build_delivery_column_map(ws, header_row, columns)
    menu_col_idx = _resolve_delivery_menu_column(columns, column_map)
    slot_rows = _find_delivery_slot_rows(ws, menu_col_idx) if menu_col_idx else []
    if slot_rows:
        for name in list(workbook.sheetnames):
            if workbook[name] is not ws:
                workbook.remove(workbook[name])
        rows_by_date: dict[dt_date | None, list[dict]] = {}
        for row in rows:
            rows_by_date.setdefault(row.get("date"), []).append(row)
        dates = [date_val for date_val in rows_by_date.keys() if date_val]
        dates.sort()
        daypart_col_idx = _resolve_delivery_column_index(columns, column_map, "daypart")
        date_col_idx = _resolve_delivery_column_index(columns, column_map, "date")
        slot_map = (
            _build_delivery_slot_map(ws, slot_rows, menu_col_idx, daypart_col_idx)
            if daypart_col_idx
            else {}
        )
        slot_label_map = _build_delivery_slot_label_map(ws, slot_rows, menu_col_idx)
        slot_label_map_by_daypart = (
            _build_delivery_slot_label_map_by_daypart(ws, slot_rows, menu_col_idx, daypart_col_idx)
            if daypart_col_idx
            else {}
        )
        slot_rows_by_daypart: dict[str, list[int]] = {}
        if daypart_col_idx:
            current_daypart = ""
            for row in slot_rows:
                daypart_text = _normalize_cell_text(ws.cell(row=row, column=daypart_col_idx).value)
                if daypart_text:
                    current_daypart = _normalize_delivery_daypart(daypart_text)
                if current_daypart:
                    slot_rows_by_daypart.setdefault(current_daypart, []).append(row)
        if not dates:
            workbook.save(path)
            return
        for idx, date_val in enumerate(dates):
            target_ws = ws if idx == 0 else workbook.copy_worksheet(ws)
            if idx == 0:
                target_ws.title = date_val.isoformat()
            else:
                title = date_val.isoformat()
                if title in workbook.sheetnames:
                    title = f"{title}-{idx + 1}"
                target_ws.title = title[:31]
            _apply_delivery_facility_name(target_ws, facility_name)
            if date_col_idx:
                if daypart_col_idx:
                    daypart_rows = _find_daypart_rows(target_ws, daypart_col_idx)
                    morning_row = daypart_rows.get("朝") or slot_rows[0]
                    evening_row = daypart_rows.get("夕")
                else:
                    morning_row = slot_rows[0]
                    evening_row = None
                date_cell = _resolve_merged_cell(target_ws, morning_row, date_col_idx)
                if not isinstance(date_cell, MergedCell):
                    date_cell.value = date_val
                if evening_row:
                    weekday_cell = _resolve_merged_cell(target_ws, evening_row, date_col_idx)
                    if not isinstance(weekday_cell, MergedCell):
                        weekday_cell.value = _format_delivery_weekday(date_val)
            assignments = _assign_delivery_rows_to_slots(
                rows_by_date.get(date_val, []),
                slot_rows,
                slot_map,
                slot_label_map,
                slot_label_map_by_daypart,
                slot_rows_by_daypart,
            )
            for slot_row in slot_rows:
                row_payload = assignments.get(slot_row)
                if row_payload:
                    _write_delivery_slot_row(
                        target_ws,
                        slot_row,
                        row_payload,
                        columns,
                        column_map,
                        include_menu_name,
                    )
                else:
                    _clear_delivery_slot_row(target_ws, slot_row, columns, column_map, include_menu_name)
        workbook.save(path)
        return

    start_row = _delivery_start_row(ws, header_row)

    for idx, row in enumerate(rows):
        target_row = start_row + idx
        for col in columns:
            name = col.get("name")
            if not name:
                continue
            if not include_menu_name and col.get("source") == "menu_name":
                continue
            col_idx = column_map.get(name)
            if not col_idx:
                continue
            cell = _resolve_merged_cell(ws, target_row, col_idx)
            template_cell = _resolve_merged_cell(ws, start_row, col_idx)
            _copy_cell_style(template_cell, cell)
            if isinstance(cell, MergedCell):
                continue
            cell.value = _resolve_delivery_cell(row, col)

    workbook.save(path)


def _build_delivery_rows(
    order: dict,
    template: dict,
    quantity_rules: dict,
    facility_config: dict | None = None,
    menu_meta: dict[str, object] | None = None,
) -> list[dict]:
    columns = template.get("columns", [])
    zero_as_empty = quantity_rules.get("zero_as_empty", True)
    facility_id = order.get("facility")
    area_aliases = _build_area_alias_map(facility_config)
    quantity_columns: list[dict[str, str | None]] = []
    for col in columns:
        if col.get("source") != "quantity":
            continue
        name = col.get("name")
        if not name:
            continue
        diet_type = col.get("diet_type")
        area_id = col.get("area_id")
        if not diet_type or not area_id:
            inferred_diet, inferred_area = _infer_delivery_column_meta(name)
            diet_type = diet_type or inferred_diet
            area_id = area_id or inferred_area
        quantity_columns.append(
            {
                "name": name,
                "diet_key": _normalize_diet_key(diet_type),
                "area_key": _resolve_area_key(area_id, area_aliases),
            }
        )
    prefer_ocr_rows = bool(template.get("prefer_ocr_raw_rows", False))
    if not (isinstance(menu_meta, dict) and menu_meta.get("entries")):
        menu_meta = _build_ocr_menu_meta(order, facility_config)
    entries = menu_meta.get("entries") if isinstance(menu_meta, dict) else None
    if entries and prefer_ocr_rows:
        menu_names = [entry.get("menu_name") for entry in entries if entry.get("menu_name")]
        condiment_map = _build_condiment_map(menu_names, facility_id)
        ocr_rows: list[dict] = []
        for entry in entries:
            date_val = entry.get("date")
            menu_name = entry.get("menu_name")
            if not date_val or not menu_name:
                continue
            row = {
                "date": date_val,
                "daypart": entry.get("daypart"),
                "menu_category": entry.get("category"),
                "menu_name": menu_name,
                "menu_display": "",
                "_order_index": entry.get("index"),
                "note": entry.get("note"),
            }
            for col in quantity_columns:
                name = col.get("name")
                if not name:
                    continue
                diet_key = col.get("diet_key")
                area_key = col.get("area_key")
                qty = _lookup_ocr_entry_quantity(entry, diet_key, area_key)
                if qty is None:
                    continue
                if _safe_qty({"quantity_original": qty, "quantity_corrected": None}, zero_as_empty) is None:
                    continue
                row[name] = qty
            condiments = condiment_map.get(menu_name, [])
            _apply_condiment_note(row, condiments)
            if row.get("menu_category"):
                row["menu_display"] = f"{row.get('menu_category')} {row.get('menu_name')}".strip()
            else:
                row["menu_display"] = row.get("menu_name") or ""
            ocr_rows.append(row)
        ocr_rows.sort(
            key=lambda row: (
                row.get("date") or "",
                row.get("_order_index") if row.get("_order_index") is not None else 1_000_000,
            )
        )
        return ocr_rows

    rows: dict[tuple, dict] = {}
    menu_names = []
    for line in order.get("lines", []):
        line_date = _ensure_date(line.get("date"))
        qty = _safe_qty(line, zero_as_empty)
        if qty is None:
            continue
        menu_name = line.get("menu_name")
        if menu_name:
            menu_names.append(menu_name)
        menu_key = _normalize_menu_key(menu_name)
        meta_map = menu_meta.get("by_menu") if isinstance(menu_meta, dict) else None
        meta = meta_map.get((line_date, menu_key)) if meta_map else None
        daypart_value = line.get("daypart") or line.get("menu_category")
        if meta and not daypart_value:
            daypart_value = meta.get("daypart") or daypart_value
        menu_category = line.get("menu_category") or (meta.get("category") if meta else None)
        order_index = meta.get("index") if meta else None
        key = (line_date, daypart_value, menu_name)
        row = rows.setdefault(
            key,
            {
                "date": line_date,
                "daypart": daypart_value,
                "menu_name": menu_name,
                "menu_category": menu_category,
                "menu_display": "",
                "_order_index": order_index,
            },
        )
        if menu_category and not row.get("menu_category"):
            row["menu_category"] = menu_category
        if order_index is not None and row.get("_order_index") is None:
            row["_order_index"] = order_index
        line_diet_key = _normalize_diet_key(line.get("diet_type"))
        line_area_key = _resolve_area_key(line.get("area_id"), area_aliases)
        for col in quantity_columns:
            col_diet_key = col.get("diet_key")
            if col_diet_key and col_diet_key != line_diet_key:
                continue
            col_area_key = col.get("area_key")
            if col_area_key and col_area_key != line_area_key:
                continue
            name = col.get("name")
            if not name:
                continue
            row[name] = (row.get(name) or 0) + float(qty)
    condiment_map = _build_condiment_map(menu_names, facility_id)
    result = list(rows.values())
    result.sort(
        key=lambda row: (
            row.get("date") or "",
            row.get("_order_index") if row.get("_order_index") is not None else 1_000_000,
            row.get("menu_name") or "",
        )
    )
    for row in result:
        condiments = condiment_map.get(row.get("menu_name") or "", [])
        _apply_condiment_note(row, condiments)
        if row.get("menu_category"):
            row["menu_display"] = f"{row.get('menu_category')} {row.get('menu_name')}".strip()
        else:
            row["menu_display"] = row.get("menu_name") or ""
    return result


def _build_label_rows(
    bags: list[dict],
    label_profile: dict,
    facility_name: str | None,
) -> tuple[list[dict], list[str], str]:
    label_fields, label_format = _resolve_label_fields(label_profile)
    if label_format == "legacy":
        labels = [_label_payload_legacy(bag, label_profile, facility_name) for bag in bags]
        return labels, label_fields, label_format
    labels = [_label_payload_jp(bag, label_profile) for bag in bags]
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
    labels = [_label_payload_jp(row, label_profile) for row in grouped.values()]
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


def _safe_filename_segment(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    text = re.sub(r"[^\w.-]+", "_", text)
    text = text.strip("._")
    return text or fallback


def _safe_sheet_title(value: object, fallback: str, used_titles: set[str]) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r'[\\/*?:\[\]]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = fallback
    base = text[:31] or fallback
    title = base
    suffix = 2
    while title in used_titles:
        tail = f"-{suffix}"
        title = f"{base[: max(31 - len(tail), 1)]}{tail}"
        suffix += 1
    used_titles.add(title)
    return title


def _copy_sheet_contents(source_ws, target_ws) -> None:
    for row in source_ws.iter_rows():
        for cell in row:
            if isinstance(cell, MergedCell):
                continue
            target = target_ws.cell(row=cell.row, column=cell.column)
            target.value = cell.value
            _copy_cell_style(cell, target)
    for merged_range in source_ws.merged_cells.ranges:
        target_ws.merge_cells(str(merged_range))
    for col_key, dim in source_ws.column_dimensions.items():
        target_dim = target_ws.column_dimensions[col_key]
        target_dim.width = dim.width
        target_dim.hidden = dim.hidden
        target_dim.bestFit = dim.bestFit
    for row_key, dim in source_ws.row_dimensions.items():
        target_dim = target_ws.row_dimensions[row_key]
        target_dim.height = dim.height
        target_dim.hidden = dim.hidden
    target_ws.sheet_view.showGridLines = source_ws.sheet_view.showGridLines
    target_ws.freeze_panes = source_ws.freeze_panes
    target_ws.sheet_format.defaultRowHeight = source_ws.sheet_format.defaultRowHeight
    target_ws.sheet_format.defaultColWidth = source_ws.sheet_format.defaultColWidth
    target_ws.page_margins = copy(source_ws.page_margins)
    target_ws.print_options = copy(source_ws.print_options)
    target_ws.page_setup = copy(source_ws.page_setup)


def _populate_label_sheet(ws, fieldnames: list[str], rows: list[dict]) -> None:
    ws.append(fieldnames)
    for row in rows:
        ws.append([row.get(field, "") for field in fieldnames])


def _create_daily_labels_sheet(
    workbook,
    used_titles: set[str],
    *,
    title_seed: str,
    bags: list[dict],
    label_profile: dict,
    facility_name: str | None,
) -> str:
    labels, label_fields, _ = _build_label_rows(bags, label_profile, facility_name)
    if not labels:
        raise ValueError("label rows not found for target date")
    ws = workbook.create_sheet(title=_safe_sheet_title(title_seed, "ラベル", used_titles))
    _populate_label_sheet(ws, label_fields, labels)
    return ws.title


def _create_daily_delivery_sheet(
    workbook,
    used_titles: set[str],
    *,
    title_seed: str,
    rows: list[dict],
    invoice_template: dict,
    facility_name: str | None,
    order_id: str,
) -> str:
    if not rows:
        raise ValueError("delivery rows not found for target date")
    temp_path = OUTPUT_DIR / f"{order_id}_daily_bundle_{uuid4().hex}.xlsx"
    try:
        _write_delivery_note(
            temp_path,
            rows,
            invoice_template.get("columns", []),
            invoice_template.get("template_uri"),
            bool(invoice_template.get("include_menu_name", False)),
            invoice_template.get("sheet_name"),
            facility_name,
        )
        rendered = load_workbook(temp_path)
        source_ws = rendered.active
        ws = workbook.create_sheet(title=_safe_sheet_title(title_seed, "納品書", used_titles))
        _copy_sheet_contents(source_ws, ws)
        return ws.title
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _merge_delivery_bundle_rows(rows: list[dict], invoice_template: dict | None) -> list[dict]:
    columns = invoice_template.get("columns", []) if isinstance(invoice_template, dict) else []
    quantity_names = [
        str(col.get("name") or "").strip()
        for col in columns
        if col.get("source") == "quantity" and str(col.get("name") or "").strip()
    ]
    if not rows or not quantity_names:
        return rows

    merged: dict[tuple[object, object, object, object], dict] = {}
    for row in rows:
        key = (
            _ensure_date(row.get("date")),
            row.get("daypart"),
            row.get("menu_category"),
            row.get("menu_name"),
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(row)
            continue
        target = existing
        if target.get("_order_index") is None and row.get("_order_index") is not None:
            target["_order_index"] = row.get("_order_index")
        if not target.get("menu_display") and row.get("menu_display"):
            target["menu_display"] = row.get("menu_display")
        if not target.get("note") and row.get("note"):
            target["note"] = row.get("note")
        for name in quantity_names:
            value = row.get(name)
            if value in (None, ""):
                continue
            target[name] = (target.get(name) or 0) + float(value)

    result = list(merged.values())
    result.sort(
        key=lambda row: (
            row.get("date") or "",
            row.get("_order_index") if row.get("_order_index") is not None else 1_000_000,
            row.get("menu_name") or "",
        )
    )
    return result


def build_daily_output_bundle(
    target_date: dt_date,
    *,
    bundle_type: str = "both",
    status: str | None = None,
) -> tuple[Path, dict]:
    normalized_type = str(bundle_type or "").strip().lower()
    if normalized_type not in {"labels", "delivery", "both"}:
        raise ValueError("bundle_type must be labels, delivery, or both")

    orders = order_service.list_orders_by_line_date(target_date, status=status)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    bundle_name = f"daily_outputs_{target_date.isoformat()}_{normalized_type}_{stamp}.xlsx"
    bundle_path = OUTPUT_DIR / bundle_name

    manifest_items: list[dict] = []
    grouped_outputs: dict[str, dict[str, Any]] = {}
    workbook = Workbook()
    workbook.remove(workbook.active)
    used_titles: set[str] = set()

    for order_summary in orders:
        order_id = str(order_summary.get("id") or "").strip()
        if not order_id:
            continue
        facility_code = str(order_summary.get("facility") or "").strip()
        facility_name = ""
        if facility_code:
            try:
                facility_config = config_service.get_facility_config(facility_code) or {}
                facility_name = str(facility_config.get("facility_name") or "").strip()
            except Exception:
                facility_name = ""
        try:
            ctx = _prepare_output_context(order_id)
            facility_config = ctx.get("facility_config") or {}
            facility_name = str(facility_config.get("facility_name") or facility_name or "").strip()
            facility_code = str(ctx.get("order_for_outputs", {}).get("facility") or facility_code or "").strip()
            group_key = facility_code or facility_name or order_id
            sheet_seed = facility_name or facility_code or order_id
            group = grouped_outputs.get(group_key)
            if not group:
                group = {
                    "facility_code": facility_code or None,
                    "facility_name": facility_name or None,
                    "sheet_seed": sheet_seed,
                    "order_ids": [],
                    "label_profile": ctx["label_profile"],
                    "invoice_template": ctx["invoice_template"],
                    "quantity_rules": ctx["quantity_rules"],
                    "facility_config": facility_config,
                    "ocr_menu_meta": ctx.get("ocr_menu_meta"),
                    "bags": [],
                    "delivery_rows": [],
                }
                grouped_outputs[group_key] = group

            group["order_ids"].append(order_id)
            if normalized_type in {"labels", "both"}:
                filtered_bags = [
                    bag for bag in ctx["bags"] if _ensure_date(bag.get("date")) == target_date
                ]
                group["bags"].extend(filtered_bags)
            if normalized_type in {"delivery", "both"}:
                delivery_rows = _build_delivery_rows(
                    ctx["order_for_outputs"],
                    ctx["invoice_template"],
                    ctx["quantity_rules"],
                    facility_config,
                    ctx.get("ocr_menu_meta"),
                )
                filtered_delivery_rows = [
                    row for row in delivery_rows if _ensure_date(row.get("date")) == target_date
                ]
                group["delivery_rows"].extend(filtered_delivery_rows)
        except Exception as exc:  # noqa: BLE001
            manifest_items.append(
                {
                    "order_id": order_id,
                    "facility_code": facility_code or None,
                    "facility_name": facility_name or None,
                    "status": "error",
                    "error": str(exc),
                    "files": [],
                }
            )

    success_count = 0
    for group in grouped_outputs.values():
        item_payload: dict[str, object] = {
            "order_ids": list(group["order_ids"]),
            "facility_code": group["facility_code"],
            "facility_name": group["facility_name"],
            "status": "ok",
            "files": [],
        }
        try:
            has_label_rows = bool(group["bags"])
            merged_delivery_rows = _merge_delivery_bundle_rows(
                group["delivery_rows"],
                group["invoice_template"],
            ) if normalized_type in {"delivery", "both"} else []
            has_delivery_rows = bool(merged_delivery_rows)

            should_create_label_sheet = normalized_type in {"labels", "both"} and has_label_rows
            should_create_delivery_sheet = normalized_type in {"delivery", "both"} and has_delivery_rows

            if normalized_type == "labels" and not should_create_label_sheet:
                item_payload["status"] = "empty"
                item_payload["error"] = "label rows not found for target date"
                manifest_items.append(item_payload)
                continue
            if normalized_type == "delivery" and not should_create_delivery_sheet:
                item_payload["status"] = "empty"
                item_payload["error"] = "delivery rows not found for target date"
                manifest_items.append(item_payload)
                continue
            if normalized_type == "both" and not should_create_label_sheet and not should_create_delivery_sheet:
                item_payload["status"] = "empty"
                item_payload["error"] = "no bundle rows found for target date"
                manifest_items.append(item_payload)
                continue

            if should_create_label_sheet:
                label_title_seed = (
                    group["sheet_seed"]
                    if normalized_type == "labels"
                    else f"ラベル_{group['sheet_seed']}"
                )
                sheet_name = _create_daily_labels_sheet(
                    workbook,
                    used_titles,
                    title_seed=label_title_seed,
                    bags=group["bags"],
                    label_profile=group["label_profile"],
                    facility_name=group["facility_config"].get("facility_name"),
                )
                item_payload["files"].append(sheet_name)
            if should_create_delivery_sheet:
                delivery_title_seed = (
                    group["sheet_seed"]
                    if normalized_type == "delivery"
                    else f"納品書_{group['sheet_seed']}"
                )
                sheet_name = _create_daily_delivery_sheet(
                    workbook,
                    used_titles,
                    title_seed=delivery_title_seed,
                    rows=merged_delivery_rows,
                    invoice_template=group["invoice_template"],
                    facility_name=group["facility_config"].get("facility_name"),
                    order_id=str(group["order_ids"][0]),
                )
                item_payload["files"].append(sheet_name)
            success_count += 1
        except Exception as exc:  # noqa: BLE001
            item_payload["status"] = "error"
            item_payload["error"] = str(exc)
        manifest_items.append(item_payload)

    if not workbook.sheetnames:
        raise ValueError("対象日の出力対象がありません")

    workbook.save(bundle_path)
    error_count = sum(1 for item in manifest_items if item.get("status") == "error")
    empty_count = sum(1 for item in manifest_items if item.get("status") == "empty")
    manifest = {
        "date": target_date.isoformat(),
        "bundle_type": normalized_type,
        "status_filter": status,
        "created_at": datetime.utcnow().isoformat(),
        "total_orders": len(manifest_items),
        "success_orders": success_count,
        "empty_orders": empty_count,
        "error_orders": error_count,
        "items": manifest_items,
        "file_format": "xlsx",
    }

    return bundle_path, manifest


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

    order_lines = build_order_lines_for_outputs(order)
    order_for_outputs = {**order, "lines": order_lines}
    ocr_menu_meta = _build_ocr_menu_meta(order, facility_config)

    bags = _split_bags_by_max(_build_bags(order_for_outputs, packaging_policy, quantity_rules))
    bag_types = _resolve_bag_types(facility_config)
    bags = _assign_bag_type_for_bags(bags, bag_types)
    return {
        "order": order,
        "facility_config": facility_config,
        "label_profile": label_profile,
        "invoice_template": invoice_template,
        "quantity_rules": quantity_rules,
        "order_lines": order_lines,
        "order_for_outputs": order_for_outputs,
        "ocr_menu_meta": ocr_menu_meta,
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
        delivery_rows = _build_delivery_rows(
            ctx["order_for_outputs"],
            invoice_template,
            quantity_rules,
            ctx["facility_config"],
            ctx.get("ocr_menu_meta"),
        )
        include_menu_name = bool(invoice_template.get("include_menu_name", False))
        _write_delivery_note(
            delivery_path,
            delivery_rows,
            invoice_template.get("columns", []),
            invoice_template.get("template_uri"),
            include_menu_name,
            invoice_template.get("sheet_name"),
            facility_name,
        )
        return {"delivery_note": str(delivery_path)}
    if output_type == "aggregate":
        total_rows, total_fields, _ = _build_total_rows(
            ctx["order_lines"], label_profile, facility_name, quantity_rules
        )
        _write_aggregate_csv(agg_path, total_rows, total_fields)
        return {"aggregate": str(agg_path)}
    raise ValueError(f"invalid output type: {output_type}")


def build_delivery_preview(order_id: str) -> dict:
    ctx = _prepare_output_context(order_id)
    invoice_template = ctx["invoice_template"]
    quantity_rules = ctx["quantity_rules"]
    include_menu_name = bool(invoice_template.get("include_menu_name", False))
    columns = invoice_template.get("columns", [])
    display_headers = []
    for col in columns:
        name = col.get("name")
        if not name:
            continue
        display_headers.append(col.get("header") or name)
    rows = _build_delivery_rows(
        ctx["order_for_outputs"],
        invoice_template,
        quantity_rules,
        ctx["facility_config"],
        ctx.get("ocr_menu_meta"),
    )
    preview_rows = []
    for row in rows:
        rendered = []
        for col in columns:
            if not col.get("name"):
                continue
            if not include_menu_name and col.get("source") == "menu_name":
                rendered.append("")
                continue
            rendered.append(_resolve_delivery_cell(row, col))
        preview_rows.append(rendered)
    ocr_entries = ctx.get("ocr_menu_meta", {}).get("entries", []) if isinstance(ctx.get("ocr_menu_meta"), dict) else []
    parsed, _ = order_service.get_ocr_output(order_id)
    table_raw = parsed.get("table_raw") if isinstance(parsed, dict) else None
    table_raw_len = len(table_raw) if isinstance(table_raw, str) else None
    return {
        "headers": display_headers,
        "rows": preview_rows,
        "ocr_entry_count": len(ocr_entries),
        "ocr_table_raw_len": table_raw_len,
    }


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

    delivery_rows = _build_delivery_rows(
        order_for_outputs,
        invoice_template,
        quantity_rules,
        ctx["facility_config"],
        ctx.get("ocr_menu_meta"),
    )
    include_menu_name = bool(invoice_template.get("include_menu_name", False))
    _write_delivery_note(
        delivery_path,
        delivery_rows,
        invoice_template.get("columns", []),
        invoice_template.get("template_uri"),
        include_menu_name,
        invoice_template.get("sheet_name"),
        ctx["facility_config"].get("facility_name"),
    )

    total_rows, total_fields, _ = _build_total_rows(
        order_lines, label_profile, ctx["facility_config"].get("facility_name"), quantity_rules
    )
    _write_aggregate_csv(agg_path, total_rows, total_fields)

    with session_scope() as session:
        lineage = _latest_output_lineage_for_order(session, order_id)
        session.query(Bag).filter(Bag.order_id == order_id).delete()
        session.query(LabelRow).filter(LabelRow.order_id == order_id).delete()
        session.query(DeliveryNote).filter(DeliveryNote.order_id == order_id).delete()

        for bag in bags:
            bag_id = f"BAG{uuid4().hex[:8]}"
            session.add(
                Bag(
                    id=bag_id,
                    order_id=order_id,
                    confirmed_snapshot_id=lineage["confirmed_snapshot_id"],
                    output_bundle_id=lineage["output_bundle_id"],
                    source_saved_sheet_id=lineage["source_saved_sheet_id"],
                    template_version_id=lineage["template_version_id"],
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
                    confirmed_snapshot_id=lineage["confirmed_snapshot_id"],
                    output_bundle_id=lineage["output_bundle_id"],
                    source_saved_sheet_id=lineage["source_saved_sheet_id"],
                    template_version_id=lineage["template_version_id"],
                    payload_json=label,
                )
            )
        session.add(
            DeliveryNote(
                id=f"INV{uuid4().hex[:8]}",
                order_id=order_id,
                confirmed_snapshot_id=lineage["confirmed_snapshot_id"],
                output_bundle_id=lineage["output_bundle_id"],
                source_saved_sheet_id=lineage["source_saved_sheet_id"],
                template_version_id=lineage["template_version_id"],
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
                    confirmed_snapshot_id=lineage["confirmed_snapshot_id"],
                    output_bundle_id=lineage["output_bundle_id"],
                    template_version_id=lineage["template_version_id"],
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
    order_lines = build_order_lines_for_outputs(order)
    bags = build_bag_rows_for_outputs(order, order_lines=order_lines, facility_config=facility_config)
    payload = []

    with session_scope() as session:
        lineage = _latest_output_lineage_for_order(session, order_id)
        session.query(Bag).filter(Bag.order_id == order_id).delete()
        for bag in bags:
            bag_id = f"BAG{uuid4().hex[:8]}"
            session.add(
                Bag(
                    id=bag_id,
                    order_id=order_id,
                    confirmed_snapshot_id=lineage["confirmed_snapshot_id"],
                    output_bundle_id=lineage["output_bundle_id"],
                    source_saved_sheet_id=lineage["source_saved_sheet_id"],
                    template_version_id=lineage["template_version_id"],
                    date=bag.get("date"),
                    daypart=bag.get("daypart"),
                    menu_name=bag.get("menu_name"),
                    diet_type=bag.get("diet_type"),
                    area_id=bag.get("area_id"),
                    bag_type=bag.get("bag_type"),
                    quantity=bag.get("quantity"),
                )
            )
            payload.append({"id": bag_id, **_serialize_bag_payload_rows([bag])[0]})
    return {"order_id": order_id, "generated": bool(payload), "bags": payload}
