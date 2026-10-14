from io import BytesIO
from contextlib import contextmanager, nullcontext
from numbers import Number
import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from difflib import SequenceMatcher
import re
import unicodedata
import threading
from zoneinfo import ZoneInfo
from loguru import logger
from uuid import uuid4
import pandas as pd

from sqlalchemy import delete, select, inspect, or_, text, func

from src.db import session_scope, engine
from src.models.menu import (
    MonthlyMenu,
    MonthlyMenuItem,
    MonthlyMenuEntry,
    MenuMaster,
    MenuFacilityOverride,
    MenuRule,
    MonthlyMenuImport,
    MenuEditRevision,
)
from src.models.facility import Facility, FacilityConfig
from src.models.user import AuditLog
from src.services.notification_service import record_event
from src.services import menu_rule_service
from src.services import menu_edit_revision_service as menu_revision
from src.services.monthly_menu_identity_schema import (
    MonthlyMenuIdentitySchemaError,
    require_canonical_identity_schema,
)
from src.services.menu_vocabulary import normalize_diet_type
from src.services.storage_service import load_bytes_from_uri


MEAL_SLOT_TERMS = {
    "朝",
    "昼",
    "夕",
    "朝食",
    "昼食",
    "夕食",
    "朝アサ",
    "昼ヒル",
    "夕ユウ",
    "am",
    "pm",
}

HEADER_SKIP_TERMS = {
    "区分",
    "日付",
    "曜日",
    "日",
    "date",
    "day",
    "time",
    "時間帯",
}

SKIP_MENU_NAMES = {
    "ごはん",
    "ご飯",
    "ごはん半量",
    "ご飯半量",
}

_MENU_TRANSLATION = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)

_MASTER_FIELDS = (
    "unit_type",
    "qty_per_serving",
    "temp_type",
    "daypart",
    "category",
    "bag_max_qty",
    "bag_max_unit",
    "condiments",
)

_CONDIMENT_HEADERS = {
    "ｿｰｽ": "ソース",
    "ﾀﾙﾀﾙｿｰｽ": "タルタルソース",
    "ｹﾁｬｯﾌﾟ": "ケチャップ",
    "ﾏﾖﾈｰｽﾞ": "マヨネーズ",
    "マヨネーズ": "マヨネーズ",
}

_MENU_OVERRIDE_TAG_KEYS = ("menu_override_tags", "menu_tags")
_MENU_OVERRIDE_TAG_PREFIX = "TAG:"
_INVALID_PATCH_VALUE = object()
_MENU_SCHEMA_INITIALIZED = False
_MENU_SCHEMA_LOCK = threading.RLock()
_JST = ZoneInfo("Asia/Tokyo")
_REPAIRED_MENU_ITEM_ID_PREFIX = "MMI_REPAIR_"

_MENU_ENTRY_DAYPART_SORT_ORDER = {
    "朝": 0,
    "朝食": 0,
    "朝アサ": 0,
    "昼": 1,
    "昼食": 1,
    "昼ヒル": 1,
    "夕": 2,
    "夕食": 2,
    "夕ユウ": 2,
}


class MenuMasterResolutionRequired(Exception):
    def __init__(self, issues: list[dict]):
        super().__init__("menu master resolution required")
        self.issues = issues


class MenuMasterRevisionConflict(Exception):
    pass


class MenuSchemaNotMigrated(RuntimeError):
    pass


def _menu_entry_sort_key(entry) -> tuple[str, int, int, str]:
    if isinstance(entry, dict):
        menu_date = str(entry.get("menu_date") or "")
        daypart = str(entry.get("daypart") or "")
        slot_index_raw = entry.get("slot_index")
        entry_id = str(entry.get("id") or "")
    else:
        menu_date_value = getattr(entry, "menu_date", None)
        menu_date = menu_date_value.isoformat() if isinstance(menu_date_value, date) else ""
        daypart = str(getattr(entry, "daypart", "") or "")
        slot_index_raw = getattr(entry, "slot_index", None)
        entry_id = str(getattr(entry, "id", "") or "")
    try:
        slot_index = int(slot_index_raw) if slot_index_raw is not None else 0
    except (TypeError, ValueError):
        slot_index = 0
    return (
        menu_date,
        _MENU_ENTRY_DAYPART_SORT_ORDER.get(daypart, 99),
        slot_index,
        entry_id,
    )


def _normalize_menu_match_key(value: str) -> str:
    normalized = _normalize_menu_name(value)
    if not normalized:
        return ""
    normalized = re.sub(r"添[)）]?[^\n]*$", "", normalized)
    normalized = normalized.replace("の", "")
    return normalized


def _monthly_item_identity_key(
    *,
    name: object,
    daypart: object = None,
    category: object = None,
    diet_type: object = None,
    facility_override: object = None,
) -> tuple[str, str, str, str, str]:
    return (
        str(name or "").strip(),
        str(_coerce_master_field_value("daypart", daypart) or "").strip(),
        str(category or "").strip(),
        str(_monthly_entry_diet_identity(diet_type) or "").strip(),
        str(facility_override or "").strip(),
    )


def _monthly_item_identity_key_from_item(item: MonthlyMenuItem) -> tuple[str, str, str, str, str]:
    return _monthly_item_identity_key(
        name=item.name,
        daypart=item.daypart,
        category=item.category,
        diet_type=item.diet_type,
        facility_override=item.facility_override,
    )


def _monthly_item_identity_key_from_payload(payload: dict, facility_override: str | None = None) -> tuple[str, str, str, str, str]:
    return _monthly_item_identity_key(
        name=payload.get("name"),
        daypart=payload.get("daypart"),
        category=payload.get("category"),
        diet_type=payload.get("diet_type"),
        facility_override=facility_override,
    )


def _monthly_item_payload_preference(payload: dict) -> tuple[int, int, int, str]:
    return (
        1 if payload.get("menu_master_id") else 0,
        1 if not payload.get("master_resolution_mode") else 0,
        0 if str(payload.get("id") or "").startswith(_REPAIRED_MENU_ITEM_ID_PREFIX) else 1,
        str(payload.get("id") or ""),
    )


def _dedupe_monthly_item_payloads_by_identity(items: list[dict]) -> list[dict]:
    selected: dict[tuple[str, str, str, str, str], dict] = {}
    for item in items:
        key = _monthly_item_identity_key_from_payload(item, item.get("facility_override"))
        if not key[0]:
            continue
        current = selected.get(key)
        if current is None or _monthly_item_payload_preference(item) > _monthly_item_payload_preference(current):
            selected[key] = item
    return list(selected.values())


def _normalize_menu_payload_items(payload: dict | None) -> dict | None:
    if not payload:
        return payload
    items = payload.get("items")
    if not isinstance(items, list):
        return payload
    normalized = dict(payload)
    normalized["items"] = _dedupe_monthly_item_payloads_by_identity([dict(item) for item in items if isinstance(item, dict)])
    return normalized


def _monthly_item_identity_key_from_entry(entry: MonthlyMenuEntry) -> tuple[str, str, str, str, str]:
    return _monthly_item_identity_key(
        name=entry.name,
        daypart=entry.daypart,
        category=entry.category,
        diet_type=entry.diet_type,
        facility_override=entry.facility_override,
    )


def _monthly_entry_diet_identity(value: object) -> str | None:
    # Persisted monthly rows use a blank diet to encode regular meals.
    if value is None or (isinstance(value, str) and not value.strip()):
        return "regular"
    return normalize_diet_type(value)


def _monthly_entry_override_key(entry: MonthlyMenuEntry) -> tuple[object, object, int, str]:
    return (
        entry.menu_date,
        entry.daypart,
        int(entry.slot_index) if entry.slot_index is not None else -1,
        str(_monthly_entry_diet_identity(entry.diet_type) or "").strip(),
    )


def _monthly_entry_diet_filter(column, diet_type: object):
    normalized = _monthly_entry_diet_identity(diet_type)
    if not normalized:
        return column.in_([])
    if normalized == "regular":
        return or_(column.is_(None), column == "", column == "regular")
    return column == normalized


def _menu_schema_missing_items(connection=None) -> list[str]:
    inspector = inspect(engine if connection is None else connection)
    tables = set(inspector.get_table_names())
    required: dict[str, set[str]] = {
        "menu_masters": {"condiments", "revision"},
        "menu_facility_overrides": {"menu_master_id", "facility_id"},
        "monthly_menu_items": {"menu_master_id", "master_resolution_mode", "bag_max_qty", "bag_max_unit"},
        "monthly_menu_entries": {"facility_override"},
    }
    missing: list[str] = []
    for table_name, required_columns in required.items():
        if table_name not in tables:
            missing.append(table_name)
            continue
        columns = {str(col.get("name") or "") for col in inspector.get_columns(table_name)}
        for column_name in sorted(required_columns - columns):
            missing.append(f"{table_name}.{column_name}")
    return missing


def _ensure_monthly_menu_item_identity_index(connection=None) -> None:
    inspector = inspect(engine if connection is None else connection)
    if "monthly_menu_items" not in set(inspector.get_table_names()):
        return
    try:
        if connection is not None:
            require_canonical_identity_schema(connection)
        else:
            with engine.connect() as schema_connection:
                require_canonical_identity_schema(schema_connection)
    except MonthlyMenuIdentitySchemaError as exc:
        raise MenuSchemaNotMigrated(str(exc)) from None


def ensure_menu_schema(*, connection=None) -> None:
    global _MENU_SCHEMA_INITIALIZED
    if connection is not None:
        missing = _menu_schema_missing_items(connection)
        if missing:
            raise MenuSchemaNotMigrated(
                "menu schema is not migrated; run database migrations before boot: "
                + ", ".join(missing)
            )
        _ensure_monthly_menu_item_identity_index(connection)
        return
    if _MENU_SCHEMA_INITIALIZED:
        return
    with _MENU_SCHEMA_LOCK:
        if _MENU_SCHEMA_INITIALIZED:
            return
        missing = _menu_schema_missing_items()
        if missing:
            raise MenuSchemaNotMigrated(
                "menu schema is not migrated; run database migrations before boot: "
                + ", ".join(missing)
            )
        _ensure_monthly_menu_item_identity_index()
        _MENU_SCHEMA_INITIALIZED = True

_TEMP_COLD_HINTS = (
    "サラダ",
    "マリネ",
    "酢の物",
    "酢",
    "和え",
    "ナムル",
    "コールスロー",
    "冷",
    "お浸し",
    "おひたし",
)
_TEMP_HOT_HINTS = (
    "煮",
    "焼",
    "揚",
    "炒",
    "チャップ",
    "フライ",
    "ソテー",
    "天ぷら",
    "スープ",
    "汁",
    "シチュー",
    "鍋",
    "カレー",
    "グラタン",
    "卵とじ",
    "オムレツ",
    "蒸",
)

_GRAM_UNIT_ALIASES = {"g", "ｇ", "gram", "grams"}
_CUT_UNIT_ALIASES = {"cut", "slice", "slices"}
_COUNT_UNIT_ALIASES = {"count", "piece", "pieces"}


def _resolve_menu_name_column(columns: list[str]) -> str:
    for candidate in ["menu", "メニュー", "品名", "商品名", "料理名", "献立"]:
        for col in columns:
            if candidate.lower() in col.lower():
                return col
    return columns[0] if columns else "menu"


def _normalize_menu_name(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).translate(_MENU_TRANSLATION)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.replace("・", "").replace("／", "/")
    normalized = re.sub(r"[‐‑‒–—―ーｰ]+", "-", normalized)
    normalized = normalized.translate(str.maketrans({"（": "(", "）": ")", "［": "[", "］": "]"}))
    normalized = re.sub(r"[()\\[\\]{}「」『』<>＜＞]", "", normalized)
    return normalized.strip().lower()


def _normalize_temp_type(value: object) -> str | None:
    if _is_blank_value(value):
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None
    lowered = text.lower().replace(" ", "")
    compact = lowered.replace("・", "").replace("/", "").replace("-", "").replace("_", "")
    if compact in {"温冷", "hotcold", "coldhot"}:
        return None
    if "冷" in text and "温" not in text:
        return "cold"
    if "温" in text and "冷" not in text:
        return "hot"
    if lowered in {"cold", "chilled", "cool"}:
        return "cold"
    if lowered in {"hot", "warm"}:
        return "hot"
    return text


def _infer_temp_type(menu_name: str | None) -> str | None:
    if not menu_name:
        return None
    text = str(menu_name)
    if any(token in text for token in _TEMP_COLD_HINTS):
        return "cold"
    if any(token in text for token in _TEMP_HOT_HINTS):
        return "hot"
    return None


def _infer_unit_type(menu_name: str | None) -> str | None:
    if not menu_name:
        return None
    text = str(menu_name)
    if "切" in text or "枚" in text:
        return "cut"
    if "個" in text:
        return "count"
    return "g"


def _normalize_menu_unit_type(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower().replace(" ", "").replace("　", "")
    if lowered in _GRAM_UNIT_ALIASES or "グラム" in text:
        return "g"
    if "切" in text or "枚" in text or lowered in _CUT_UNIT_ALIASES:
        return "cut"
    if "個" in text or lowered in _COUNT_UNIT_ALIASES:
        return "count"
    return text


def _is_blank_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _normalize_override_tag(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text.startswith(_MENU_OVERRIDE_TAG_PREFIX.lower()):
        text = text[len(_MENU_OVERRIDE_TAG_PREFIX) :].strip().lower()
    text = re.sub(r"\s+", "", text)
    if not text:
        return None
    return text


def _extract_menu_override_tags(config_json: object) -> list[str]:
    if not isinstance(config_json, dict):
        return []
    values: list[str] = []
    # `menu_override_tag` is for single tag fallback.
    single = config_json.get("menu_override_tag")
    if single is not None:
        tag = _normalize_override_tag(single)
        if tag:
            values.append(tag)
    for key in _MENU_OVERRIDE_TAG_KEYS:
        raw = config_json.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            parts = [part.strip() for part in re.split(r"[,\n\r\t ]+", raw) if part.strip()]
        elif isinstance(raw, list):
            parts = [str(part).strip() for part in raw if str(part).strip()]
        else:
            continue
        for part in parts:
            tag = _normalize_override_tag(part)
            if tag:
                values.append(tag)
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _resolve_override_scope_ids(session, facility_id: str | None) -> list[str]:
    if not facility_id:
        return []
    scope_ids: list[str] = [facility_id]
    config = session.get(FacilityConfig, facility_id)
    tags = _extract_menu_override_tags(config.config_json if config else None)
    for tag in tags:
        scope_ids.append(f"{_MENU_OVERRIDE_TAG_PREFIX}{tag}")
    seen: set[str] = set()
    resolved: list[str] = []
    for item in scope_ids:
        value = (item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        resolved.append(value)
    return resolved


def _normalize_scope_override(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.upper().startswith(_MENU_OVERRIDE_TAG_PREFIX):
        normalized_tag = _normalize_override_tag(text)
        return f"{_MENU_OVERRIDE_TAG_PREFIX}{normalized_tag}" if normalized_tag else None
    return text


def resolve_menu_upload_scope(scope_type: str | None, scope_value: str | None) -> str | None:
    normalized_type = str(scope_type or "base").strip().lower() or "base"
    raw_value = str(scope_value or "").strip()
    if normalized_type == "base":
        return None
    if normalized_type == "facility":
        if not raw_value:
            raise ValueError("facility scope requires a facility code")
        return _normalize_scope_override(raw_value)
    if normalized_type == "tag":
        if not raw_value:
            raise ValueError("tag scope requires a tag name")
        normalized_tag = _normalize_override_tag(raw_value)
        if not normalized_tag:
            raise ValueError("tag scope requires a tag name")
        return f"{_MENU_OVERRIDE_TAG_PREFIX}{normalized_tag}"
    raise ValueError("scope_type must be one of: base, facility, tag")


def list_menu_scope_options() -> dict[str, list[dict]]:
    ensure_menu_schema()
    with session_scope() as session:
        facilities = (
            session.query(Facility)
            .order_by(Facility.name.asc(), Facility.id.asc())
            .all()
        )
        facility_payload = [
            {
                "id": str(facility.id or ""),
                "name": str(facility.name or ""),
            }
            for facility in facilities
            if str(facility.id or "").strip() and str(facility.name or "").strip()
        ]

        configs = session.query(FacilityConfig).all()
        facilities_by_id = {str(facility.id or ""): facility for facility in facilities}
        tag_to_facilities: dict[str, list[dict[str, str]]] = {}
        for config in configs:
            facility_id = str(config.facility_id or "").strip()
            if not facility_id:
                continue
            facility = facilities_by_id.get(facility_id)
            facility_name = str(getattr(facility, "name", "") or "").strip()
            for tag in _extract_menu_override_tags(config.config_json):
                tag_to_facilities.setdefault(tag, []).append(
                    {
                        "id": facility_id,
                        "name": facility_name or facility_id,
                    }
                )

        tag_payload: list[dict[str, object]] = []
        for tag in sorted(tag_to_facilities.keys()):
            linked = sorted(
                tag_to_facilities.get(tag, []),
                key=lambda item: (str(item.get("name") or ""), str(item.get("id") or "")),
            )
            facility_names = [str(item.get("name") or "") for item in linked if str(item.get("name") or "").strip()]
            facility_ids = [str(item.get("id") or "") for item in linked if str(item.get("id") or "").strip()]
            tag_payload.append(
                {
                    "value": tag,
                    "scope_override": f"{_MENU_OVERRIDE_TAG_PREFIX}{tag}",
                    "facility_ids": facility_ids,
                    "facility_names": facility_names,
                    "facility_count": len(facility_ids),
                }
            )

        return {
            "facilities": facility_payload,
            "tags": tag_payload,
        }


def _normalize_cell_value(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    return text


def _normalize_menu_value(value: object) -> str:
    if isinstance(value, (int, float)) and float(value) == 0.0:
        return ""
    text = _normalize_cell_value(value)
    if not text:
        return ""
    compact = text.replace("．", ".")
    if compact in {"0", "０", "0.0", "0.00"}:
        return ""
    try:
        if float(compact) == 0.0:
            return ""
    except Exception:
        pass
    return text


def _is_skip_menu_name(value: str) -> bool:
    normalized = value.replace(" ", "").replace("　", "")
    return normalized in SKIP_MENU_NAMES


def _extract_month_from_text(value: str) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", value)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    return year, month


def _parse_month_id(month_id: str | None) -> tuple[int, int] | None:
    if not month_id:
        return None
    match = re.match(r"(\d{4})-(\d{2})", month_id)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _month_id_from_date(value: date | None) -> str | None:
    if not isinstance(value, date):
        return None
    return f"{value.year:04d}-{value.month:02d}"


def _parse_day_number(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        return number if 1 <= number <= 31 else None
    try:
        number = int(float(text))
        return number if 1 <= number <= 31 else None
    except Exception:
        return None


def _find_weekday_columns(rows: list[list[str]]) -> list[int] | None:
    weekdays = ["日", "月", "火", "水", "木", "金", "土"]
    for row in rows:
        positions: dict[str, int] = {}
        for idx, value in enumerate(row):
            cell = value.strip()
            if cell in weekdays and cell not in positions:
                positions[cell] = idx
        if len(positions) >= 5:
            try:
                return [positions[name] for name in weekdays]
            except KeyError:
                continue
    return None


def _row_has_tokens(row: list[str], tokens: tuple[str, ...]) -> bool:
    for value in row:
        if any(token in value for token in tokens):
            return True
    return False


def _is_monthly_menu_date_row(row: list[str], weekday_cols: list[int], day_numbers: list[int | None]) -> bool:
    date_count = sum(1 for number in day_numbers if number is not None)
    if date_count >= 3:
        return True
    if date_count == 0:
        return False
    weekday_col_set = set(weekday_cols)
    for col_idx, value in enumerate(row):
        text = str(value or "").strip()
        if not text:
            continue
        if col_idx not in weekday_col_set:
            return False
        weekday_pos = weekday_cols.index(col_idx)
        if day_numbers[weekday_pos] is None:
            return False
    return True


def _match_menu_pattern(menu_name: str, pattern: str, match_type: str | None) -> bool:
    if not pattern:
        return False
    if match_type == "regex":
        try:
            return re.search(pattern, menu_name) is not None
        except re.error:
            return False
    normalized_menu = _normalize_menu_name(menu_name)
    normalized_pattern = _normalize_menu_name(pattern)
    if match_type == "exact":
        return normalized_menu == normalized_pattern
    return normalized_pattern in normalized_menu


def _rule_applies_to_item(rule: dict, item: dict) -> bool:
    if rule.get("rule_type") not in {"global", "menu"}:
        return False
    if rule.get("rule_type") == "menu":
        if not _match_menu_pattern(
            item.get("name") or "",
            rule.get("menu_pattern") or "",
            rule.get("match_type"),
        ):
            return False
    if rule.get("daypart") and rule.get("daypart") != item.get("daypart"):
        return False
    if rule.get("category") and rule.get("category") != item.get("category"):
        return False
    if rule.get("diet_type") and rule.get("diet_type") != item.get("diet_type"):
        return False
    return True


def _apply_rules_to_items(items: list[dict]) -> list[dict]:
    rules = menu_rule_service.list_active_rules()
    return _apply_rule_payloads_to_items(items, rules)


def _apply_rule_payloads_to_items(items: list[dict], rules: list[dict]) -> list[dict]:
    type_weight = {"global": 100, "menu": 200}
    enriched: list[dict] = []
    for item in items:
        updated = dict(item)
        explicit_fields = set(_extract_master_patch(item, _MASTER_FIELDS))
        if rules:
            matches = [rule for rule in rules if _rule_applies_to_item(rule, item)]
            if matches:
                selected = max(
                    matches,
                    key=lambda rule: type_weight.get(rule.get("rule_type"), 0)
                    + int(rule.get("priority") or 0),
                )
                if not updated.get("unit_type") and selected.get("unit_type"):
                    updated["unit_type"] = selected.get("unit_type")
                if updated.get("qty_per_serving") is None and selected.get("qty_per_serving") is not None:
                    updated["qty_per_serving"] = selected.get("qty_per_serving")
                if not updated.get("temp_type") and selected.get("temp_type"):
                    updated["temp_type"] = _normalize_temp_type(selected.get("temp_type"))
        updated["temp_type"] = _normalize_temp_type(updated.get("temp_type"))
        if not updated.get("unit_type"):
            updated["unit_type"] = _infer_unit_type(updated.get("name"))
        if not updated.get("temp_type"):
            updated["temp_type"] = _infer_temp_type(updated.get("name"))
        updated["_inferred_master_fields"] = sorted(
            set(_extract_master_patch(updated, _MASTER_FIELDS)) - explicit_fields
        )
        enriched.append(updated)
    return enriched


def _monthly_item_patch_from_source(meta: dict, master: MenuMaster | None) -> dict:
    patch = _extract_master_patch(meta, _MASTER_FIELDS)
    if master is not None:
        # Rule-generated suggestions are not changes supplied by the operator.
        inherited_fields = set(meta.get("_inferred_master_fields", []))
        for field in inherited_fields:
            patch.pop(field, None)
            value = getattr(master, field, None)
            if not _is_blank_value(value):
                patch[field] = value
    return patch


def _parse_monthly_menu(
    file_bytes: bytes,
    filename: str,
    sheet_name: str | None,
    month_id_hint: str | None,
) -> tuple[date | None, str | None, list[dict], list[dict]]:
    if filename.lower().endswith(".csv"):
        raise ValueError("monthly menu parser does not support csv")
    raw = pd.read_excel(BytesIO(file_bytes), sheet_name=sheet_name, header=None)
    if isinstance(raw, dict):
        if not raw:
            raise ValueError("menu file is empty")
        if len(raw) != 1:
            raise ValueError("select an explicit menu sheet; multiple source sheets are present")
        df = next(iter(raw.values()))
    else:
        df = raw
    if df.empty:
        raise ValueError("menu file is empty")
    rows: list[list[str]] = [
        [_normalize_cell_value(cell) for cell in row]
        for row in df.fillna("").values.tolist()
    ]
    month_start = None
    diet_type = None
    for row in rows[:20]:
        for value in row:
            if not value:
                continue
            if diet_type is None and "献立種類" in value:
                diet_type = normalize_diet_type(value)
            if month_start is None:
                parsed = _extract_month_from_text(value)
                if parsed:
                    month_start = date(parsed[0], parsed[1], 1)
        if month_start and diet_type:
            break
    if month_start is None:
        parsed_hint = _parse_month_id(month_id_hint)
        if parsed_hint:
            month_start = date(parsed_hint[0], parsed_hint[1], 1)
    weekday_cols = _find_weekday_columns(rows)
    if not weekday_cols:
        raise ValueError("weekday columns not found")

    entries: list[dict] = []
    item_meta: dict[tuple[str, str, str, str], dict] = {}
    current_dates: dict[int, date] = {}
    current_daypart: str | None = None
    slot_index = -1
    nutrition_tokens = ("エネルギー", "ｴﾈﾙｷﾞｰ", "脂質", "食塩")
    daypart_tokens = ("朝食", "昼食", "夕食")
    category_map = {
        "朝食": ["主食", "主菜", "副菜"],
        "昼食": ["主食", "主菜", "副菜", "副菜"],
        "夕食": ["主食", "主菜", "副菜", "副菜"],
    }
    for row_idx, row in enumerate(rows):
        day_numbers = [(_parse_day_number(row[col]) if col < len(row) else None) for col in weekday_cols]
        if _is_monthly_menu_date_row(row, weekday_cols, day_numbers):
            current_dates = {}
            for col_idx, day_num in zip(weekday_cols, day_numbers):
                if day_num is None or month_start is None:
                    continue
                current_dates[col_idx] = date(month_start.year, month_start.month, day_num)
            current_daypart = None
            slot_index = -1
            continue

        if not current_dates:
            continue
        if _row_has_tokens(row, nutrition_tokens):
            current_daypart = None
            slot_index = -1
            continue

        detected_daypart = None
        for token in daypart_tokens:
            if any(token in cell for cell in row if cell):
                detected_daypart = token
                break
        if detected_daypart:
            current_daypart = detected_daypart
            slot_index = 0
        elif current_daypart:
            slot_index += 1
        else:
            continue

        categories = category_map.get(current_daypart, [])
        category = categories[slot_index] if slot_index < len(categories) else None
        for col_idx, menu_date in current_dates.items():
            if col_idx >= len(row):
                continue
            name = _normalize_menu_value(row[col_idx])
            if not name or _is_skip_menu_name(name):
                continue
            entries.append(
                {
                    "menu_date": menu_date,
                    "daypart": current_daypart,
                    "name": name,
                    "category": category,
                    "diet_type": diet_type,
                    "slot_index": slot_index,
                }
            )
            item_key = (
                name,
                str(current_daypart or ""),
                str(category or ""),
                str(diet_type or ""),
            )
            meta = item_meta.setdefault(
                item_key,
                {
                    "name": name,
                    "daypart": current_daypart,
                    "category": category,
                    "diet_type": diet_type,
                    "count": 0,
                },
            )
            meta["count"] += 1

    items: list[dict] = []
    for (_name, _daypart, _category, _diet_type), meta in item_meta.items():
        name = str(meta.get("name") or "").strip()
        if _is_skip_menu_name(name):
            continue
        item_payload = {"name": name}
        if meta.get("daypart"):
            item_payload["daypart"] = meta.get("daypart")
        if meta.get("category"):
            item_payload["category"] = meta.get("category")
        if meta.get("diet_type"):
            item_payload["diet_type"] = meta.get("diet_type")
        items.append(item_payload)

    return month_start, diet_type, items, entries


def _is_date_value(value: str) -> bool:
    if not value:
        return False
    if value.isdigit() and len(value) in {6, 7, 8}:
        return True
    if "/" in value or "-" in value:
        parts = value.replace("-", "/").split("/")
        if all(part.isdigit() for part in parts if part):
            return True
    return False


def _is_meal_slot_value(value: str) -> bool:
    if not value:
        return False
    normalized = value.replace(" ", "")
    if normalized in MEAL_SLOT_TERMS:
        return True
    return False


def _is_menu_category_value(value: str) -> bool:
    if not value:
        return False
    normalized = value.replace(" ", "")
    if any(token in normalized for token in ("副菜", "主菜", "添え", "副①", "副②", "主Ａ", "主A")):
        return True
    return False


def _find_product_name_columns(columns: list[str]) -> list[str]:
    matches: list[str] = []
    for col in columns:
        header = str(col)
        if any(token in header for token in ("商品名", "品名", "料理名", "献立")):
            matches.append(col)
    return matches


def _coerce_master_field_value(field: str, value: object) -> object:
    if field in {"qty_per_serving", "bag_max_qty"}:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        coerced = _coerce_float(value)
        if coerced is None:
            return _INVALID_PATCH_VALUE
        return coerced
    if field in {"unit_type", "bag_max_unit"}:
        return _normalize_menu_unit_type(value)
    if field == "condiments":
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if _is_blank_value(value):
            return []
        return [str(value).strip()]
    if field == "temp_type":
        return _normalize_temp_type(value)
    if field == "daypart":
        if _is_blank_value(value):
            return None
        text = str(value).strip()
        if not text:
            return None
        if "朝" in text:
            return "朝食"
        if "昼" in text:
            return "昼食"
        if "夕" in text or "夜" in text:
            return "夕食"
        lowered = text.lower()
        if "breakfast" in lowered or lowered == "morning":
            return "朝食"
        if "lunch" in lowered or lowered == "noon":
            return "昼食"
        if "dinner" in lowered or "supper" in lowered or lowered == "evening":
            return "夕食"
        return text
    if _is_blank_value(value):
        return None
    return str(value).strip() if isinstance(value, str) else value


def _extract_master_patch(
    payload: dict,
    fields: tuple[str, ...] = _MASTER_FIELDS,
    *,
    allow_null: bool = False,
) -> dict[str, object]:
    patch: dict[str, object] = {}
    for field in fields:
        if field not in payload:
            continue
        value = _coerce_master_field_value(field, payload.get(field))
        if value is _INVALID_PATCH_VALUE:
            continue
        if value is None and not allow_null:
            continue
        patch[field] = value
    return patch


def _find_menu_master_by_normalized(session, normalized_name: str) -> MenuMaster | None:
    if not normalized_name:
        return None
    for pending in session.new:
        if isinstance(pending, MenuMaster) and pending.normalized_name == normalized_name:
            return pending
    return (
        session.execute(select(MenuMaster).where(MenuMaster.normalized_name == normalized_name))
        .scalars()
        .first()
    )


def _apply_seed_fields_if_blank(target, seed_fields: dict[str, object] | None) -> None:
    if not seed_fields:
        return
    for field, raw in seed_fields.items():
        if field not in _MASTER_FIELDS:
            continue
        value = _coerce_master_field_value(field, raw)
        if value is _INVALID_PATCH_VALUE or _is_blank_value(value):
            continue
        current = getattr(target, field, None)
        if _is_blank_value(current):
            setattr(target, field, value)


def _find_menu_master_candidates(session, name: str, *, limit: int = 8) -> list[dict]:
    normalized_name = _normalize_menu_name(name)
    match_key = _normalize_menu_match_key(name)
    if not normalized_name or not match_key:
        return []
    masters = session.execute(select(MenuMaster).order_by(MenuMaster.name.asc())).scalars().all()
    scored: list[tuple[int, int, str, MenuMaster, str]] = []
    for master in masters:
        master_normalized = master.normalized_name or _normalize_menu_name(master.name or "")
        master_key = _normalize_menu_match_key(master.name or "")
        score = None
        reason = None
        if master_normalized == normalized_name:
            continue
        if not master_key:
            continue
        if master_key == match_key:
            score = 95
            reason = "normalized"
        elif match_key in master_key or master_key in match_key:
            score = 87
            reason = "partial"
        else:
            ratio = SequenceMatcher(None, match_key, master_key).ratio()
            if ratio >= 0.62:
                score = round(ratio * 100)
                reason = "similar"
        if score is None:
            continue
        scored.append((score, abs(len(master_key) - len(match_key)), master.name or "", master, reason or "similar"))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    payload: list[dict] = []
    seen_ids: set[str] = set()
    for score, _, _, master, reason in scored:
        if master.id in seen_ids:
            continue
        seen_ids.add(master.id)
        candidate = serialize_menu_master(master)
        candidate["match_score"] = score
        candidate["match_reason"] = reason
        payload.append(candidate)
        if len(payload) >= limit:
            break
    return payload


def _index_menu_master_resolutions(menu_master_resolutions: list[dict] | None) -> dict[str, dict]:
    if menu_master_resolutions is not None and not isinstance(menu_master_resolutions, list):
        raise ValueError("menu master resolutions must be a list")
    indexed: dict[str, dict] = {}
    alias_owners: set[str] = set()
    for raw in menu_master_resolutions or []:
        _validate_import_resolution(raw)
        keys = [
            str(raw.get("source_name") or "").strip(),
            str(raw.get("name") or "").strip(),
            str(raw.get("issue_key") or "").strip(),
            str(raw.get("normalized_name") or "").strip(),
        ]
        normalized_source = _normalize_menu_name(raw.get("source_name"))
        if normalized_source:
            keys.append(normalized_source)
        normalized_name = _normalize_menu_name(raw.get("name"))
        if normalized_name:
            keys.append(normalized_name)
        canonical_aliases = {_normalize_menu_name(key) for key in keys if key}
        canonical_aliases.discard("")
        if alias_owners.intersection(canonical_aliases):
            raise ValueError("ambiguous normalized menu master resolution")
        alias_owners.update(canonical_aliases)
        for key in set(keys):
            if key:
                if key in indexed:
                    raise ValueError(f"ambiguous menu master resolution: {key}")
                indexed[key] = raw
    return indexed


def _build_menu_master_resolution_issue(
    name: str,
    seed_patch: dict[str, object] | None,
    candidates: list[dict],
) -> dict:
    normalized_name = _normalize_menu_name(name)
    return {
        "source_name": name,
        "normalized_name": normalized_name,
        "issue_key": normalized_name or name,
        "reason": "candidate_review_required" if candidates else "missing",
        "suggested_patch": {
            "name": name,
            "unit_type": _normalize_menu_unit_type((seed_patch or {}).get("unit_type")),
            "qty_per_serving": (seed_patch or {}).get("qty_per_serving"),
            "temp_type": _normalize_temp_type((seed_patch or {}).get("temp_type")),
            "daypart": _coerce_master_field_value("daypart", (seed_patch or {}).get("daypart")),
            "category": (seed_patch or {}).get("category"),
            "bag_max_qty": (seed_patch or {}).get("bag_max_qty"),
            "bag_max_unit": _normalize_menu_unit_type((seed_patch or {}).get("bag_max_unit")),
            "condiments": list((seed_patch or {}).get("condiments") or [])
            if isinstance((seed_patch or {}).get("condiments"), list)
            else [],
        },
        "candidates": candidates,
    }


def _unit_requires_bagging_settings(unit_type: object) -> bool:
    return _normalize_menu_unit_type(unit_type) in {"cut", "count"}


def _bagging_settings_missing(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if not _unit_requires_bagging_settings(payload.get("unit_type")):
        return False
    return _coerce_float(payload.get("bag_max_qty")) is None or _is_blank_value(
        _normalize_menu_unit_type(payload.get("bag_max_unit"))
    )


def _condiment_bagging_issues(session, condiments: object) -> list[dict]:
    issues: list[dict] = []
    labels = [str(item).strip() for item in condiments or [] if str(item).strip()] if isinstance(condiments, list) else []
    for label in labels:
        master = _find_menu_master_by_normalized(session, _normalize_menu_name(label))
        if master is None:
            issues.append(
                {
                    "condiment_name": label,
                    "issue_type": "condiment_bagging_missing",
                    "reason": "condiment_master_missing",
                    "message": "付属品の袋上限を設定してください",
                }
            )
            continue
        if _coerce_float(master.bag_max_qty) is None or _is_blank_value(_normalize_menu_unit_type(master.bag_max_unit)):
            issues.append(
                {
                    "condiment_name": label,
                    "issue_type": "condiment_bagging_missing",
                    "reason": "condiment_bagging_settings_missing",
                    "message": "付属品の袋上限を設定してください",
                    "current_master": serialize_menu_master(master),
                }
            )
    return issues


def _build_bagging_setting_issue(
    session,
    name: str,
    seed_patch: dict[str, object] | None,
    master: MenuMaster | None = None,
    *,
    item_id: str | None = None,
) -> dict | None:
    patch = dict(seed_patch or {})
    if master is not None:
        for field in _MASTER_FIELDS:
            if _is_blank_value(patch.get(field)) and not _is_blank_value(getattr(master, field, None)):
                patch[field] = getattr(master, field, None)
    missing_fields: list[dict] = []
    if _bagging_settings_missing(patch):
        if _coerce_float(patch.get("bag_max_qty")) is None:
            missing_fields.append({"field": "bag_max_qty", "label": "袋上限数"})
        if _is_blank_value(_normalize_menu_unit_type(patch.get("bag_max_unit"))):
            missing_fields.append({"field": "bag_max_unit", "label": "袋上限単位"})
    condiment_issues = _condiment_bagging_issues(session, patch.get("condiments"))
    if not missing_fields and not condiment_issues:
        return None
    issue = _build_menu_master_resolution_issue(name, patch, [])
    issue.update(
        {
            "item_id": item_id,
            "issue_type": "bagging_settings_missing",
            "reason": "bagging_settings_missing",
            "current_master": serialize_menu_master(master) if master is not None else None,
            "field_diffs": missing_fields,
            "condiment_issues": condiment_issues,
        }
    )
    return issue


def _build_upload_menu_master_plan(
    session,
    name: str,
    seed_patch: dict[str, object] | None,
    resolution: dict | None,
    *,
    require_bagging_review: bool = False,
) -> dict:
    normalized = _normalize_menu_name(name)
    if not normalized:
        raise ValueError("name is required")
    exact_master = _find_menu_master_by_normalized(session, normalized)
    resolution = resolution if isinstance(resolution, dict) else None
    if exact_master and require_bagging_review:
        bagging_issue = _build_bagging_setting_issue(session, name, seed_patch, exact_master)
        if bagging_issue and not resolution:
            bagging_issue["reason"] = "bagging_settings_missing"
            return {"issue": bagging_issue}
    if exact_master:
        if resolution:
            action = str(resolution.get("action") or "").strip().lower()
            if action in {"update", "create"}:
                update_patch = dict(seed_patch or {})
                update_patch.update(_extract_master_patch(resolution, _MASTER_FIELDS))
                if require_bagging_review and _bagging_settings_missing(update_patch):
                    raise ValueError(f"menu master update requires bag_max_qty and bag_max_unit for bagging units: {name}")
                return {
                    "action": "update",
                    "menu_master_id": exact_master.id,
                    "seed_fields": update_patch,
                }
            if action == "existing":
                selected_id = str(resolution.get("menu_master_id") or "").strip()
                if selected_id and selected_id != exact_master.id:
                    selected = session.get(MenuMaster, selected_id)
                    if not selected:
                        raise ValueError(f"menu master candidate not found: {name}")
                    return {
                        "action": "existing",
                        "menu_master_id": selected.id,
                        "seed_fields": dict(seed_patch or {}),
                    }
                if require_bagging_review and _bagging_settings_missing(
                    {
                        **dict(seed_patch or {}),
                        "unit_type": exact_master.unit_type,
                        "qty_per_serving": exact_master.qty_per_serving,
                        "bag_max_qty": exact_master.bag_max_qty,
                        "bag_max_unit": exact_master.bag_max_unit,
                    }
                ):
                    raise ValueError(f"menu master existing requires bag_max_qty and bag_max_unit for bagging units: {name}")
            elif action not in {"", "create"}:
                raise ValueError(f"unknown menu master resolution action: {name}")
        return {
            "action": "existing",
            "menu_master_id": exact_master.id,
            "seed_fields": dict(seed_patch or {}),
        }

    if resolution:
        action = str(resolution.get("action") or "").strip().lower()
        if action == "existing":
            selected_id = str(resolution.get("menu_master_id") or "").strip()
            selected = session.get(MenuMaster, selected_id) if selected_id else None
            if not selected:
                raise ValueError(f"menu master candidate not found: {name}")
            return {
                "action": "existing",
                "menu_master_id": selected.id,
                "seed_fields": dict(seed_patch or {}),
            }
        if action == "create":
            create_name = str(resolution.get("name") or name).strip() or name
            create_patch = dict(seed_patch or {})
            create_patch.update(
                _extract_master_patch(
                    resolution,
                    _MASTER_FIELDS,
                )
            )
            if _is_blank_value(create_patch.get("unit_type")) or create_patch.get("qty_per_serving") is None:
                raise ValueError(f"menu master create requires unit_type and qty_per_serving: {name}")
            if require_bagging_review and _bagging_settings_missing(create_patch):
                raise ValueError(f"menu master create requires bag_max_qty and bag_max_unit for bagging units: {name}")
            return {
                "action": "create",
                "name": create_name,
                "seed_fields": create_patch,
            }
        if action == "update":
            selected_id = str(resolution.get("menu_master_id") or "").strip()
            selected = session.get(MenuMaster, selected_id) if selected_id else exact_master
            if not selected:
                selected = _find_menu_master_by_normalized(session, normalized)
            if not selected:
                raise ValueError(f"menu master candidate not found: {name}")
            update_patch = dict(seed_patch or {})
            update_patch.update(_extract_master_patch(resolution, _MASTER_FIELDS))
            if require_bagging_review and _bagging_settings_missing(update_patch):
                raise ValueError(f"menu master update requires bag_max_qty and bag_max_unit for bagging units: {name}")
            return {
                "action": "update",
                "menu_master_id": selected.id,
                "seed_fields": update_patch,
            }
        raise ValueError(f"unknown menu master resolution action: {name}")

    candidates = _find_menu_master_candidates(session, name)
    issue = _build_bagging_setting_issue(session, name, seed_patch, None) if require_bagging_review else None
    if issue:
        issue["candidates"] = candidates
        return {"issue": issue}
    return {"issue": _build_menu_master_resolution_issue(name, seed_patch, candidates)}


def _resolve_menu_master_resolution(resolution_map: dict[str, dict], name: str) -> dict | None:
    normalized_name = _normalize_menu_name(name)
    for key in (name, normalized_name):
        if key and key in resolution_map:
            return resolution_map[key]
    return None


def _get_or_create_menu_master_without_rename(
    session,
    name: str,
    *,
    seed_fields: dict[str, object] | None = None,
) -> MenuMaster | None:
    text_name = (name or "").strip()
    if not text_name:
        return None
    normalized = _normalize_menu_name(text_name)
    if not normalized:
        return None
    master = _find_menu_master_by_normalized(session, normalized)
    if master is None:
        master = MenuMaster(id=f"MNU{uuid4().hex[:8]}", name=text_name, normalized_name=normalized)
        session.add(master)
        _apply_master_patch(master, seed_fields)
        return master
    _apply_seed_fields_if_blank(master, seed_fields)
    return master


def _materialize_upload_menu_master_plan(session, name: str, plan: dict) -> MenuMaster | None:
    action = str(plan.get("action") or "").strip().lower()
    seed_fields = plan.get("seed_fields") if isinstance(plan.get("seed_fields"), dict) else None
    if action == "existing":
        menu_master_id = str(plan.get("menu_master_id") or "").strip()
        master = session.get(MenuMaster, menu_master_id) if menu_master_id else None
        if master is None:
            raise ValueError(f"menu master candidate not found: {name}")
        _apply_seed_fields_if_blank(master, seed_fields)
        return master
    if action == "create":
        return _get_or_create_menu_master_without_rename(
            session,
            str(plan.get("name") or name),
            seed_fields=seed_fields,
        )
    if action == "update":
        menu_master_id = str(plan.get("menu_master_id") or "").strip()
        master = session.get(MenuMaster, menu_master_id) if menu_master_id else None
        if master is None:
            raise ValueError(f"menu master candidate not found: {name}")
        _update_menu_master_in_session(master=master, session=session, body=dict(seed_fields or {}))
        return master
    raise ValueError(f"unknown menu master resolution action: {name}")


def _normalize_master_field_for_compare(field: str, value: object) -> object:
    if field == "name":
        return str(value or "").strip()
    if field in {"unit_type", "bag_max_unit"}:
        return _normalize_menu_unit_type(value)
    if field in {"qty_per_serving", "bag_max_qty"}:
        return _coerce_float(value)
    if field == "temp_type":
        return _normalize_temp_type(value)
    if field == "daypart":
        return None
    if field == "category":
        text = str(value or "").strip()
        if not text:
            return None
        normalized = text.replace("（", "(").replace("）", ")")
        normalized = re.sub(r"\s+", "", normalized)
        base = re.sub(r"\([^)]*\)", "", normalized).strip()
        return base or normalized or None
    return value


def _select_single_value(values: list[object]) -> object | None:
    normalized = [value for value in values if not _is_blank_value(value)]
    if not normalized:
        return None
    unique: list[object] = []
    for value in normalized:
        if value not in unique:
            unique.append(value)
    if len(unique) != 1:
        return None
    return unique[0]


def _derive_monthly_item_patch(item: MonthlyMenuItem, entries: list[MonthlyMenuEntry]) -> dict[str, object]:
    payload = serialize_item(item)
    patch = _extract_master_patch(payload, _MASTER_FIELDS)
    scope = (item.facility_override or "").strip()
    diet_type = normalize_diet_type(getattr(item, "diet_type", None))
    matching_entries = [
        entry
        for entry in entries
        if (entry.name or "").strip() == (item.name or "").strip()
        and (entry.facility_override or "").strip() == scope
        and normalize_diet_type(entry.diet_type) == diet_type
    ]
    if "daypart" not in patch:
        inferred_daypart = _select_single_value(
            [_coerce_master_field_value("daypart", entry.daypart) for entry in matching_entries]
        )
        if inferred_daypart is not None:
            patch["daypart"] = inferred_daypart
    if "category" not in patch:
        inferred_category = _select_single_value([str(entry.category or "").strip() or None for entry in matching_entries])
        if inferred_category is not None:
            patch["category"] = inferred_category
    return patch


def _build_master_field_diffs(
    name: str,
    patch: dict[str, object],
    master: MenuMaster,
) -> list[dict]:
    diffs: list[dict] = []
    field_specs = (
        ("name", "メニュー名", name, master.name),
        ("unit_type", "単位", patch.get("unit_type"), master.unit_type),
        ("qty_per_serving", "量", patch.get("qty_per_serving"), master.qty_per_serving),
        ("temp_type", "温冷", patch.get("temp_type"), master.temp_type),
        ("category", "区分", patch.get("category"), master.category),
        ("bag_max_qty", "袋上限数", patch.get("bag_max_qty"), master.bag_max_qty),
        ("bag_max_unit", "袋上限単位", patch.get("bag_max_unit"), master.bag_max_unit),
    )
    for field, label, monthly_value, master_value in field_specs:
        normalized_monthly = _normalize_master_field_for_compare(field, monthly_value)
        if _is_blank_value(normalized_monthly):
            continue
        normalized_master = _normalize_master_field_for_compare(field, master_value)
        if field in {"qty_per_serving", "bag_max_qty"}:
            if normalized_master is not None and normalized_monthly is not None:
                if abs(float(normalized_master) - float(normalized_monthly)) < 1e-9:
                    continue
            elif normalized_master == normalized_monthly:
                continue
        elif normalized_monthly == normalized_master:
            continue
        diffs.append(
            {
                "field": field,
                "label": label,
                "monthly_value": normalized_monthly,
                "master_value": normalized_master,
            }
        )
    return diffs


def _build_menu_master_check_issue(
    session,
    item: MonthlyMenuItem,
    entries: list[MonthlyMenuEntry],
) -> dict | None:
    item_name = (item.name or "").strip()
    if not item_name:
        return None
    patch = _derive_monthly_item_patch(item, entries)
    linked_master = session.get(MenuMaster, item.menu_master_id) if item.menu_master_id else None
    exact_master = _find_menu_master_by_normalized(session, _normalize_menu_name(item_name))
    target_master = linked_master or exact_master
    if target_master is None:
        candidates = _find_menu_master_candidates(session, item_name)
        bagging_issue = _build_bagging_setting_issue(session, item_name, patch, None, item_id=item.id)
        if bagging_issue:
            bagging_issue.update(
                {
                    "issue_type": "missing",
                    "reason": "bagging_settings_missing",
                    "candidates": candidates,
                    "current_master": None,
                }
            )
            return bagging_issue
        return {
            "item_id": item.id,
            "source_name": item_name,
            "normalized_name": _normalize_menu_name(item_name),
            "issue_type": "missing",
            "reason": "candidate_review_required" if candidates else "missing",
            "suggested_patch": _build_menu_master_resolution_issue(item_name, patch, [])["suggested_patch"],
            "candidates": candidates,
            "current_master": None,
            "field_diffs": [],
        }
    if str(getattr(item, "master_resolution_mode", "") or "").strip().lower() == "month_only":
        return None
    bagging_issue = _build_bagging_setting_issue(session, item_name, patch, target_master, item_id=item.id)
    if bagging_issue:
        return bagging_issue
    field_diffs = _build_master_field_diffs(item_name, patch, target_master)
    if not field_diffs:
        return None
    return {
        "item_id": item.id,
        "source_name": item_name,
        "normalized_name": _normalize_menu_name(item_name),
        "issue_type": "diff",
        "reason": "field_diff",
        "suggested_patch": _build_menu_master_resolution_issue(item_name, patch, [])["suggested_patch"],
        "candidates": [],
        "current_master": serialize_menu_master(target_master),
        "field_diffs": field_diffs,
    }


def _build_menu_master_update_body(
    name: str,
    patch: dict[str, object],
    master: MenuMaster,
) -> dict[str, object]:
    update_body: dict[str, object] = {}
    if _normalize_master_field_for_compare("name", name) != _normalize_master_field_for_compare("name", master.name):
        update_body["name"] = name
    for field in ("unit_type", "qty_per_serving", "temp_type", "category", "bag_max_qty", "bag_max_unit"):
        normalized_patch = _normalize_master_field_for_compare(field, patch.get(field))
        if _is_blank_value(normalized_patch):
            continue
        normalized_master = _normalize_master_field_for_compare(field, getattr(master, field, None))
        if field in {"qty_per_serving", "bag_max_qty"}:
            if normalized_master is not None and normalized_patch is not None:
                if abs(float(normalized_master) - float(normalized_patch)) < 1e-9:
                    continue
            elif normalized_master == normalized_patch:
                continue
        elif normalized_patch == normalized_master:
            continue
        update_body[field] = patch.get(field)
    return update_body


def _build_menu_master_checks(session, items: list[MonthlyMenuItem], entries: list[MonthlyMenuEntry]) -> dict:
    issues: list[dict] = []
    for item in sorted(items, key=lambda row: ((row.facility_override or ""), (row.name or ""), (row.id or ""))):
        issue = _build_menu_master_check_issue(session, item, entries)
        if issue:
            issues.append(issue)
    return {
        "count": len(issues),
        "issues": issues,
    }


def _update_menu_master_in_session(session, master: MenuMaster, body: dict) -> None:
    if "name" in body:
        next_name = str(body.get("name") or "").strip()
        if not next_name:
            raise ValueError("name is required")
        next_normalized = _normalize_menu_name(next_name)
        conflict = (
            session.execute(select(MenuMaster).where(MenuMaster.normalized_name == next_normalized))
            .scalars()
            .first()
        )
        if conflict and conflict.id != master.id:
            raise ValueError("duplicate menu name")
        master.name = next_name
        master.normalized_name = next_normalized
    if "unit_type" in body:
        master.unit_type = _coerce_master_field_value("unit_type", body.get("unit_type"))
    if "qty_per_serving" in body:
        master.qty_per_serving = _coerce_float(body.get("qty_per_serving"))
    if "bag_max_qty" in body:
        master.bag_max_qty = _coerce_float(body.get("bag_max_qty"))
    if "bag_max_unit" in body:
        master.bag_max_unit = _coerce_master_field_value("bag_max_unit", body.get("bag_max_unit"))
    if "temp_type" in body:
        master.temp_type = _normalize_temp_type(body.get("temp_type"))
    if "daypart" in body:
        master.daypart = _coerce_master_field_value("daypart", body.get("daypart"))
    if "category" in body:
        master.category = body.get("category") or None
    if "condiments" in body:
        raw = body.get("condiments")
        if isinstance(raw, list):
            master.condiments = [str(item).strip() for item in raw if str(item).strip()]
        elif raw is None or raw == "":
            master.condiments = []


def _apply_monthly_item_patch_in_session(item: MonthlyMenuItem, patch: dict[str, object] | None) -> None:
    if not patch:
        return
    for field in ("unit_type", "qty_per_serving", "temp_type", "daypart", "category", "bag_max_qty", "bag_max_unit"):
        if field not in patch:
            continue
        value = _coerce_master_field_value(field, patch.get(field))
        if value is _INVALID_PATCH_VALUE:
            continue
        setattr(item, field, value)


def _ensure_menu_master(
    session,
    name: str,
    menu_master_id: str | None = None,
    seed_fields: dict[str, object] | None = None,
) -> MenuMaster | None:
    text_name = (name or "").strip()
    if not text_name:
        return None
    normalized = _normalize_menu_name(text_name)
    if not normalized:
        return None

    master = None
    if menu_master_id:
        candidate = session.get(MenuMaster, menu_master_id)
        if candidate and candidate.normalized_name == normalized:
            master = candidate
    if master is None:
        master = _find_menu_master_by_normalized(session, normalized)
    if master is None:
        master = MenuMaster(id=f"MNU{uuid4().hex[:8]}", name=text_name, normalized_name=normalized)
        session.add(master)
    else:
        master.name = text_name

    _apply_seed_fields_if_blank(master, seed_fields)
    return master


def _get_or_create_menu_override(
    session,
    menu_master_id: str,
    facility_id: str,
) -> MenuFacilityOverride:
    override = (
        session.execute(
            select(MenuFacilityOverride)
            .where(MenuFacilityOverride.menu_master_id == menu_master_id)
            .where(MenuFacilityOverride.facility_id == facility_id)
            .order_by(MenuFacilityOverride.id.asc())
        )
        .scalars()
        .first()
    )
    if override:
        return override
    override = MenuFacilityOverride(
        id=f"MFO{uuid4().hex[:8]}",
        menu_master_id=menu_master_id,
        facility_id=facility_id,
    )
    session.add(override)
    return override


def _apply_master_patch(target, patch: dict[str, object] | None) -> None:
    if not patch:
        return
    for field, raw in patch.items():
        if field not in _MASTER_FIELDS:
            continue
        value = _coerce_master_field_value(field, raw)
        if value is _INVALID_PATCH_VALUE:
            continue
        setattr(target, field, value)


def _build_master_defaults_index(
    session,
    names: list[str],
    menu_master_ids: list[str] | None = None,
    facility_id: str | None = None,
    *,
    lock_sources: bool = False,
    source_revisions: list[tuple[str, int]] | None = None,
) -> tuple[dict[str, dict], dict[str, dict]]:
    normalized_keys = [_normalize_menu_name(name) for name in names if name]
    normalized_keys = [key for key in normalized_keys if key]
    requested_ids = [str(value).strip() for value in (menu_master_ids or []) if str(value or "").strip()]
    if not normalized_keys and not requested_ids:
        return {}, {}

    predicates = []
    if normalized_keys:
        predicates.append(MenuMaster.normalized_name.in_(normalized_keys))
    if requested_ids:
        predicates.append(MenuMaster.id.in_(requested_ids))

    query = select(MenuMaster)
    if len(predicates) == 1:
        query = query.where(predicates[0])
    else:
        query = query.where(or_(*predicates))

    if lock_sources:
        query = query.with_for_update()
    masters = session.execute(query).scalars().all()
    if not masters:
        return {}, {}

    overrides: dict[str, MenuFacilityOverride] = {}
    if facility_id:
        scope_ids = _resolve_override_scope_ids(session, facility_id)
        if scope_ids:
            master_ids = [master.id for master in masters]
            override_rows = (
                session.execute(
                    select(MenuFacilityOverride)
                    .where(MenuFacilityOverride.facility_id.in_(scope_ids))
                    .where(MenuFacilityOverride.menu_master_id.in_(master_ids))
                )
                .scalars()
                .all()
            )
            rank = {scope_id: idx for idx, scope_id in enumerate(scope_ids)}
            selected: dict[str, tuple[int, MenuFacilityOverride]] = {}
            for row in override_rows:
                row_scope = (row.facility_id or "").strip()
                row_rank = rank.get(row_scope, 10_000)
                current = selected.get(row.menu_master_id)
                if current is None or row_rank < current[0] or (
                    row_rank == current[0] and (row.id or "") < (current[1].id or "")
                ):
                    selected[row.menu_master_id] = (row_rank, row)
            overrides = {menu_master_id: payload[1] for menu_master_id, payload in selected.items()}

    defaults_by_normalized: dict[str, dict] = {}
    defaults_by_id: dict[str, dict] = {}
    for master in masters:
        if source_revisions is not None:
            source_revisions.append((master.id, master.revision))
        override = overrides.get(master.id) if facility_id else None
        payload: dict[str, object] = {}
        for field in _MASTER_FIELDS:
            value = None
            if override is not None:
                override_value = getattr(override, field, None)
                if not _is_blank_value(override_value):
                    value = override_value
            if value is None:
                master_value = getattr(master, field)
                if not _is_blank_value(master_value):
                    value = master_value
            if field == "temp_type":
                value = _normalize_temp_type(value)
            elif field in {"unit_type", "bag_max_unit"}:
                value = _normalize_menu_unit_type(value)
            if value is not None:
                payload[field] = value
        if master.condiments:
            payload["condiments"] = list(master.condiments)
        if not payload.get("temp_type"):
            inferred_temp = _infer_temp_type(master.name)
            if inferred_temp:
                payload["temp_type"] = inferred_temp
        defaults_by_id[master.id] = payload
        defaults_by_normalized[master.normalized_name] = payload

    return defaults_by_normalized, defaults_by_id


def _build_master_defaults(
    session,
    names: list[str],
    facility_id: str | None = None,
) -> dict[str, dict]:
    defaults_by_name, _ = _build_master_defaults_index(session, names, None, facility_id)
    return defaults_by_name


def _merge_master_defaults(items: list[dict], facility_id: str | None = None) -> list[dict]:
    master_ids = [item.get("menu_master_id") or "" for item in items]
    with session_scope() as session:
        _defaults_by_name, defaults_by_id = _build_master_defaults_index(session, [], master_ids, facility_id)
    if not defaults_by_id:
        return items
    merged: list[dict] = []
    for item in items:
        payload = dict(item)
        defaults_for_item = None
        menu_master_id = str(item.get("menu_master_id") or "").strip()
        if menu_master_id:
            defaults_for_item = defaults_by_id.get(menu_master_id)
        if defaults_for_item:
            for field, value in defaults_for_item.items():
                if _is_blank_value(payload.get(field)):
                    payload[field] = value
            for field in ("bag_max_qty", "bag_max_unit"):
                payload.setdefault(field, defaults_for_item.get(field))
        merged.append(payload)
    return merged


def _infer_menu_name_column(df: pd.DataFrame) -> str | None:
    if df.empty:
        return None
    best_col = None
    best_score = -1
    for col in df.columns:
        header = str(col).strip()
        header_lower = header.lower()
        if any(term in header for term in HEADER_SKIP_TERMS) or any(
            term in header_lower for term in HEADER_SKIP_TERMS
        ):
            continue
        series = df[col]
        values = [_normalize_menu_value(v) for v in series]
        values = [v for v in values if v]
        if not values:
            continue
        meaningful = [
            v
            for v in values
            if not _is_meal_slot_value(v) and not _is_date_value(v) and not _is_menu_category_value(v)
        ]
        if not meaningful:
            continue
        unique_count = len(set(meaningful))
        score = unique_count
        if score > best_score:
            best_col = col
            best_score = score
    return str(best_col) if best_col is not None else None


def _load_menu_frame(
    file_bytes: bytes,
    filename: str,
    sheet_name: str | None,
    header: int | None = 0,
) -> pd.DataFrame:
    if filename.lower().endswith(".csv"):
        return pd.read_csv(BytesIO(file_bytes), header=header)
    if sheet_name:
        return pd.read_excel(BytesIO(file_bytes), sheet_name=sheet_name, header=header)
    xls = pd.ExcelFile(BytesIO(file_bytes))
    if "メニュー" in xls.sheet_names:
        return xls.parse("メニュー", header=header)
    return xls.parse(xls.sheet_names[0], header=header)


def _has_known_header(columns: list[str]) -> bool:
    tokens = ("menu", "メニュー", "品名", "商品名", "料理名", "献立")
    for col in columns:
        if any(token.lower() in col.lower() for token in tokens):
            return True
    return False


def _is_header_like_value(value: str) -> bool:
    if not value:
        return False
    normalized = value.replace(" ", "")
    if any(term in normalized for term in HEADER_SKIP_TERMS):
        return True
    if any(token in normalized for token in ("発注", "連絡表", "施設名", "締切", "ご記入", "禁食", "備考", "変更", "※")):
        return True
    return False


def _is_non_menu_value(value: str) -> bool:
    if not value:
        return True
    if _is_date_value(value) or _is_meal_slot_value(value) or _is_menu_category_value(value):
        return True
    if _is_header_like_value(value):
        return True
    return False


def _extract_menu_names(file_bytes: bytes, filename: str, sheet_name: str | None) -> list[str]:
    try:
        df = _load_menu_frame(file_bytes, filename, sheet_name)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid menu file") from exc
    if df.empty or not df.columns.tolist():
        raise ValueError("menu file is empty")
    columns = [str(c) for c in df.columns]
    if not _has_known_header(columns) and any(col.lower().startswith("unnamed") for col in columns):
        df = _load_menu_frame(file_bytes, filename, sheet_name, header=None)
        header_row = None
        for idx, row in df.iterrows():
            values = [str(v) for v in row.tolist() if not _is_blank_value(v)]
            if any("献立" in value or "メニュー" in value or "商品名" in value or "品名" in value for value in values):
                header_row = idx
                break
        if header_row is not None:
            header_values = [str(v).strip() for v in df.iloc[header_row].tolist()]
            df = df.iloc[header_row + 1 :].copy()
            df.columns = header_values
            columns = [str(c) for c in df.columns]
    product_cols = _find_product_name_columns(columns)
    if product_cols:
        names: list[str] = []
        seen: set[str] = set()
        for _, row in df.iterrows():
            for col in product_cols:
                name = _normalize_menu_value(row.get(col, ""))
                if not name or _is_non_menu_value(name) or _is_skip_menu_name(name) or name in seen:
                    continue
                seen.add(name)
                names.append(name)
        if names:
            return names
    name_col = _resolve_menu_name_column(columns)
    if name_col:
        series = df[name_col] if name_col in df.columns else None
        if series is not None:
            values = [_normalize_menu_value(v) for v in series]
            values = [v for v in values if v]
            if values:
                meal_slots = sum(1 for v in values if _is_meal_slot_value(v))
                category_slots = sum(1 for v in values if _is_menu_category_value(v))
                if meal_slots / len(values) >= 0.6 or category_slots / len(values) >= 0.6:
                    inferred = _infer_menu_name_column(df)
                    if inferred:
                        name_col = inferred
    if not name_col:
        name_col = _infer_menu_name_column(df) or (columns[0] if columns else "menu")
    names: list[str] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        name = _normalize_menu_value(row.get(name_col, ""))
        if not name or _is_non_menu_value(name) or _is_skip_menu_name(name) or name in seen:
            continue
        seen.add(name)
        names.append(name)
    if not names:
        raise ValueError("no menu items found")
    return names


def _register_month_revision(session, month_id):
    scope = menu_revision.monthly_scope(month_id)
    if session.execute(select(MenuEditRevision.revision).where(MenuEditRevision.scope == scope)).scalar_one_or_none() is not None:
        return
    if session.get(MonthlyMenu, month_id) is not None:
        raise menu_revision.RevisionSchemaNotMigrated("monthly revision missing; formal 0031 is required")
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise menu_revision.RevisionConflict("unsupported monthly revision driver")
    session.execute(insert(MenuEditRevision).values(scope=scope, revision=1).on_conflict_do_nothing(index_elements=["scope"]))


def _lock_month_revision(session, month_id, *, write):
    scope = menu_revision.monthly_scope(month_id)
    revision = session.execute(select(MenuEditRevision.revision).where(MenuEditRevision.scope == scope)
        .with_for_update(read=not write)).scalar_one_or_none()
    if revision is None:
        raise menu_revision.RevisionSchemaNotMigrated("monthly revision missing; formal 0031 is required")
    return revision


def _monthly_input_digest(session, month_id):
    source = {}
    # Scalar column reads are canonical even when a caller holds old ORM objects.
    for model, label in ((MonthlyMenu, "menu"), (MonthlyMenuItem, "items"), (MonthlyMenuEntry, "entries"),
                         (MenuMaster, "master_candidates"), (MenuRule, "rules")):
        query = select(*model.__table__.columns).order_by(model.__table__.c.id)
        if model is MonthlyMenu:
            query = query.where(model.id == month_id)
        elif model in (MonthlyMenuItem, MonthlyMenuEntry):
            query = query.where(model.monthly_menu_id == month_id)
        elif model is MenuRule:
            query = query.where(model.active.is_(True))
        source[label] = [dict(row) for row in session.execute(query).mappings()]
    raw = json.dumps(source, sort_keys=True, separators=(",", ":"), default=lambda value: value.isoformat())
    return hashlib.sha256(raw.encode()).hexdigest()


def _monthly_snapshot_body(session, month_id):
    menu = session.get(MonthlyMenu, month_id)
    items = session.execute(select(MonthlyMenuItem).where(MonthlyMenuItem.monthly_menu_id == month_id)
        .where(or_(MonthlyMenuItem.facility_override.is_(None), MonthlyMenuItem.facility_override == ""))
        .order_by(MonthlyMenuItem.id)).scalars().all()
    entries = session.execute(select(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == month_id)
        .where(or_(MonthlyMenuEntry.facility_override.is_(None), MonthlyMenuEntry.facility_override == ""))
        .order_by(MonthlyMenuEntry.menu_date, MonthlyMenuEntry.daypart, MonthlyMenuEntry.slot_index, MonthlyMenuEntry.id)).scalars().all()
    payloads = []
    for item in items:
        payload = serialize_item(item)
        master = session.get(MenuMaster, item.menu_master_id) if item.menu_master_id else None
        if item.menu_master_id and master is None:
            raise ValueError("recorded monthly menu master is missing")
        if master is not None and item.master_resolution_mode != "month_only":
            for field in _MASTER_FIELDS:
                if _is_blank_value(payload.get(field)):
                    payload[field] = getattr(master, field)
        payloads.append(payload)
    upload = _get_latest_menu_upload_log(session, month_id)
    entry_payloads = [serialize_entry(entry) for entry in entries]
    entry_payloads.sort(key=_menu_entry_sort_key)
    return {"menu": serialize_menu(menu, upload) if menu else None, "items": payloads,
        "source_month_id": month_id,
        "entries": entry_payloads, "master_checks": _build_menu_master_checks(session, items, entries),
        "revision": menu_revision.monthly_token(menu_revision.monthly_scope(month_id),
            menu_revision.current(session, menu_revision.monthly_scope(month_id)), _monthly_input_digest(session, month_id))}


def get_monthly_editor_snapshot(month_id):
    with session_scope() as session:
        menu_revision.begin_monthly_snapshot(session, write=False)
        source_month = month_id
        if session.get(MonthlyMenu, month_id) is None:
            adjacent = [_shift_month_id(month_id, delta) for delta in (-1, 1)]
            start = date.fromisoformat(f"{month_id}-01")
            end = date.fromisoformat(f"{_shift_month_id(month_id, 1)}-01")
            sources = session.execute(select(MonthlyMenuEntry.monthly_menu_id).where(
                MonthlyMenuEntry.monthly_menu_id.in_(adjacent), MonthlyMenuEntry.menu_date >= start,
                MonthlyMenuEntry.menu_date < end,
                or_(MonthlyMenuEntry.facility_override.is_(None), MonthlyMenuEntry.facility_override == ""))
                .distinct()).scalars().all()
            if len(sources) > 1:
                raise menu_revision.RevisionConflict("requested month has multiple recorded source menus")
            if sources:
                source_month = sources[0]
                if session.get(MonthlyMenu, source_month) is None:
                    raise ValueError("recorded monthly source parent is missing")
            elif menu_revision_scope_exists(session, month_id):
                _lock_month_revision(session, month_id, write=False)
                return _monthly_snapshot_body(session, month_id)
            else:
                return None
        _lock_month_revision(session, source_month, write=False)
        return _canonicalize_resolved_menu_payload(_monthly_snapshot_body(session, source_month), month_id, source_month)


def menu_revision_scope_exists(session, month_id):
    return session.execute(select(MenuEditRevision.scope).where(
        MenuEditRevision.scope == menu_revision.monthly_scope(month_id))).scalar_one_or_none() is not None


def _saved_import(session, month_id, import_id):
    row = session.execute(select(MonthlyMenuImport).where(MonthlyMenuImport.id == import_id,
        MonthlyMenuImport.month_id == month_id).with_for_update()).scalar_one_or_none()
    if row is None:
        raise ValueError("saved menu import not found for this month")
    _validate_import_receipt(row)
    return row


def _import_token(session, row, *, captured=False):
    _validate_import_receipt(row)
    recorded = row.review_json["input_snapshot"]
    digest = hashlib.sha256(json.dumps({"source": row.source_json, "review": row.review_json,
        "monthly_input": recorded["digest"] if captured else _monthly_input_digest(session, row.month_id),
        "monthly_revision": recorded["revision"] if captured else menu_revision.current(session, menu_revision.monthly_scope(row.month_id))},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return menu_revision.monthly_token(f"menu-import:{row.id}", row.revision, digest)


def _validate_import_source(row):
    source = row.source_json
    required = {"file_uri", "filename", "content_sha256", "sheet_name", "scope_override", "diet_types", "media_type"}
    if (not isinstance(source, dict) or not required.issubset(source)
        or not isinstance(row.month_id, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", row.month_id)):
        raise ValueError("saved original source is unresolved")
    for field in ("file_uri", "filename", "sheet_name", "media_type"):
        if not isinstance(source[field], str) or not source[field].strip():
            raise ValueError("saved original source is unresolved")
    if (Path(source["filename"]).name != source["filename"]
        or not isinstance(source["content_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", source["content_sha256"])):
        raise ValueError("saved original source is unresolved")
    scope = source["scope_override"]
    if scope is not None and (not isinstance(scope, str) or not scope.strip() or _normalize_scope_override(scope) != scope):
        raise ValueError("saved original source scope is unresolved")
    diets = source["diet_types"]
    if diets is not None and (not isinstance(diets, list) or not diets or any(
        not isinstance(value, str) or not value.strip() or normalize_diet_type(value) != value for value in diets
    )):
        raise ValueError("saved original source food types are unresolved")


def _validate_import_fields(value, *, editable=False):
    if not isinstance(value, dict):
        raise ValueError("saved nested fields are unresolved")
    for field in ("qty_per_serving", "bag_max_qty", "count"):
        if field in value and value[field] == "" and not editable:
            raise ValueError("saved numeric field is unresolved")
        if field in value and value[field] is not None and value[field] != "":
            raw = value[field]
            number = _coerce_float(raw)
            if isinstance(raw, bool) or not isinstance(raw, (str, int, float)) or number is None or not math.isfinite(number):
                raise ValueError("saved numeric field is unresolved")
    for field in ("unit_type", "bag_max_unit", "temp_type", "daypart", "category", "diet_type"):
        if field in value and value[field] is not None and not isinstance(value[field], str):
            raise ValueError("saved text field is unresolved")
    if "condiments" in value and value["condiments"] is not None and not (
        isinstance(value["condiments"], str) or isinstance(value["condiments"], list)
        and all(isinstance(label, str) for label in value["condiments"])
    ):
        raise ValueError("saved condiments are unresolved")


def _validate_import_resolution(value):
    if not isinstance(value, dict) or not isinstance(value.get("action"), str):
        raise ValueError("saved resolution action is unresolved")
    action = value["action"].strip().lower()
    if action not in ("existing", "create", "update"):
        raise ValueError("saved resolution action is unresolved")
    aliases = ("source_name", "name", "issue_key", "normalized_name")
    if not any(isinstance(value.get(key), str) and value[key].strip() for key in aliases):
        raise ValueError("saved resolution source is unresolved")
    for key in (*aliases, "menu_master_id"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            raise ValueError("saved resolution identity is unresolved")
    _validate_import_fields(value, editable=True)
    if action == "create" and (not isinstance(value.get("unit_type"), str)
        or not value["unit_type"].strip() or "qty_per_serving" not in value
        or isinstance(value["qty_per_serving"], bool) or _coerce_float(value["qty_per_serving"]) is None):
        raise ValueError("saved create resolution fields are unresolved")


def _validate_import_review_nested(review):
    for item in review["items"]:
        if (not {"name", "unit_type", "temp_type", "_inferred_master_fields"}.issubset(item)
            or not isinstance(item["name"], str) or not item["name"].strip()):
            raise ValueError("saved item name is unresolved")
        _validate_import_fields(item)
        if "_inferred_master_fields" in item and (not isinstance(item["_inferred_master_fields"], list)
            or any(not isinstance(field, str) or field not in _MASTER_FIELDS for field in item["_inferred_master_fields"])):
            raise ValueError("saved inferred fields are unresolved")
    for entry in review["entries"]:
        if not {"menu_date", "daypart", "name", "category", "diet_type", "slot_index"}.issubset(entry):
            raise ValueError("saved entry fields are unresolved")
        if any(not isinstance(entry[key], str) or not entry[key].strip() for key in ("menu_date", "daypart", "name")):
            raise ValueError("saved entry identity is unresolved")
        try:
            if date.fromisoformat(entry["menu_date"]).isoformat() != entry["menu_date"]:
                raise ValueError("saved entry date is unresolved")
        except ValueError as error:
            raise ValueError("saved entry date is unresolved") from error
        if type(entry["slot_index"]) is not int or entry["slot_index"] < 0:
            raise ValueError("saved entry slot is unresolved")
        _validate_import_fields(entry)
    for name, plan in review["plans"].items():
        if not isinstance(name, str) or not name.strip() or not {"action", "seed_fields"}.issubset(plan):
            raise ValueError("saved plan fields are unresolved")
        if plan["action"] not in ("existing", "create", "update"):
            raise ValueError("saved plan action is unresolved")
        identity = "name" if plan["action"] == "create" else "menu_master_id"
        if not isinstance(plan.get(identity), str) or not plan[identity].strip():
            raise ValueError("saved plan identity is unresolved")
        _validate_import_fields(plan["seed_fields"])
        if plan["action"] == "create":
            _validate_import_resolution({**plan["seed_fields"], "action": "create", "name": plan["name"]})
    for issue in review["issues"]:
        required = {"source_name", "normalized_name", "issue_key", "reason", "suggested_patch", "candidates"}
        if not required.issubset(issue) or any(not isinstance(issue[key], str) or not issue[key].strip()
            for key in ("source_name", "normalized_name", "issue_key", "reason")):
            raise ValueError("saved issue identity is unresolved")
        _validate_import_fields(issue["suggested_patch"])
        if not {"name", "unit_type", "qty_per_serving", "temp_type", "daypart", "category", "bag_max_qty", "bag_max_unit", "condiments"}.issubset(issue["suggested_patch"]):
            raise ValueError("saved issue patch is unresolved")
        if not isinstance(issue["candidates"], list) or any(not isinstance(candidate, dict)
            or not isinstance(candidate.get("id"), str) or not candidate["id"]
            or not isinstance(candidate.get("name"), str) or not candidate["name"] for candidate in issue["candidates"]):
            raise ValueError("saved issue candidates are unresolved")
    for diff in review["master_diffs"]:
        if (not {"source_name", "current_master", "field_diffs"}.issubset(diff)
            or not isinstance(diff["source_name"], str) or not diff["source_name"].strip()):
            raise ValueError("saved master difference is unresolved")
        master = diff["current_master"]
        if (not isinstance(master, dict) or not {"id", "name", "normalized_name", "unit_type", "qty_per_serving"}.issubset(master)
            or any(not isinstance(master[key], str) or not master[key].strip() for key in ("id", "name", "normalized_name"))):
            raise ValueError("saved master identity is unresolved")
        _validate_import_fields(master)
        if not isinstance(diff["field_diffs"], list) or any(not isinstance(field, dict)
            or not {"field", "label", "monthly_value", "master_value"}.issubset(field)
            or field["field"] not in ("name", *_MASTER_FIELDS) or not isinstance(field["label"], str) for field in diff["field_diffs"]):
            raise ValueError("saved master fields are unresolved")
    if review["item_count"] != len(review["items"]):
        raise ValueError("saved item count is unresolved")
    item_names = {_normalize_menu_name(item["name"]) for item in review["items"]}
    planned = {_normalize_menu_name(name) for name in review["plans"]}
    unresolved = {_normalize_menu_name(issue["source_name"]) for issue in review["issues"]}
    if planned.intersection(unresolved) or planned.union(unresolved) != item_names:
        raise ValueError("saved plan/source coverage is unresolved")
    if any(_normalize_menu_name(entry["name"]) not in item_names for entry in review["entries"]):
        raise ValueError("saved entry/source coverage is unresolved")
    _index_menu_master_resolutions(review["resolutions"])


def _validate_import_receipt(row):
    _validate_import_source(row)
    if (not isinstance(row.id, str) or not row.id or not isinstance(row.state, str) or row.state not in {"review", "applied"}
        or not isinstance(row.revision, int) or isinstance(row.revision, bool) or row.revision < 1):
        raise ValueError("saved import identity is unresolved")
    review = row.review_json
    required = {"issues", "plans", "item_count", "master_diffs", "entries", "items", "scope_override", "resolutions", "input_snapshot"}
    if (not isinstance(review, dict) or not required.issubset(review)
        or review["scope_override"] != row.source_json["scope_override"]
        or not isinstance(review["item_count"], int) or isinstance(review["item_count"], bool) or review["item_count"] < 0
        or not isinstance(review["plans"], dict) or any(not isinstance(value, dict) for value in review["plans"].values())):
        raise ValueError("saved review is unresolved")
    for field in ("issues", "master_diffs", "entries", "items", "resolutions"):
        if not isinstance(review[field], list) or any(not isinstance(value, dict) for value in review[field]):
            raise ValueError("saved review is unresolved")
    _validate_import_review_nested(review)
    recorded = review["input_snapshot"]
    if (not isinstance(recorded, dict) or not {"revision", "digest"}.issubset(recorded)
        or not isinstance(recorded["revision"], int) or isinstance(recorded["revision"], bool) or recorded["revision"] < 1
        or not isinstance(recorded["digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", recorded["digest"])):
        raise ValueError("saved review input is unresolved")


def _serialize_import(session, row):
    token = _import_token(session, row, captured=True)
    recorded = row.review_json["input_snapshot"]
    input_revision = menu_revision.monthly_token(menu_revision.monthly_scope(row.month_id), recorded["revision"], recorded["digest"])
    return {"id": row.id, "month_id": row.month_id, "state": row.state, "source": dict(row.source_json),
        "review": dict(row.review_json), "revision": token,
        "input_revision": input_revision, "current_input_revision": input_revision, "current_review": dict(row.review_json)}


def _import_review(session, row, preview, resolutions, *, initial=False):
    if initial:
        if row not in session.new or row.review_json != {}:
            raise ValueError("initial review must belong to the new receipt transaction")
        _validate_import_source(row)
        recorded = {}
    else:
        _validate_import_receipt(row)
        recorded = row.review_json["input_snapshot"]
    return {**row.review_json, **preview, "resolutions": resolutions, "input_snapshot": {**recorded,
        "revision": menu_revision.current(session, menu_revision.monthly_scope(row.month_id)),
        "digest": _monthly_input_digest(session, row.month_id)}}


def _preview_saved_import(session, row, resolutions, *, initial=False):
    if initial:
        if row not in session.new or row.review_json != {}:
            raise ValueError("initial import preview must belong to the new receipt transaction")
        _validate_import_source(row)
    else:
        _validate_import_receipt(row)
    source = row.source_json
    content = load_bytes_from_uri(source["file_uri"])
    if hashlib.sha256(content).hexdigest() != source["content_sha256"]:
        raise ValueError("saved source archive digest mismatch")
    preview = create_menu(row.month_id, content, source["filename"], source["sheet_name"],
        scope_override=source["scope_override"], menu_master_resolutions=resolutions,
        require_menu_master_review=True, _session=session, preview=True, diet_types=source["diet_types"])
    return content, preview


def save_menu_import(month_id, content, archived, *, sheet_name, scope_override, diet_types=None):
    if _normalize_month_id(month_id) != month_id:
        raise ValueError("explicit YYYY-MM month is required")
    if hashlib.sha256(content).hexdigest() != archived.content_sha256 or not archived.file_uri:
        raise ValueError("original upload was not archived")
    with pd.ExcelFile(BytesIO(content)) as workbook:
        if sheet_name:
            if sheet_name not in workbook.sheet_names:
                raise ValueError("selected source sheet is absent from archived workbook")
        elif len(workbook.sheet_names) == 1:
            sheet_name = workbook.sheet_names[0]
        else:
            raise ValueError("explicit source sheet is required: " + ", ".join(workbook.sheet_names))
    with session_scope() as session:
        menu_revision.begin_monthly_snapshot(session, write=True)
        _register_month_revision(session, month_id)
        _lock_month_revision(session, month_id, write=True)
        row = MonthlyMenuImport(id=f"MIMP{uuid4().hex}", month_id=month_id, state="review",
            source_json={"file_uri": archived.file_uri, "content_sha256": archived.content_sha256,
                "filename": archived.original_filename, "sheet_name": sheet_name, "scope_override": scope_override,
                "diet_types": diet_types, "media_type": archived.media_type}, review_json={})
        session.add(row)
        _, preview = _preview_saved_import(session, row, [], initial=True)
        row.review_json = _import_review(session, row, preview, [], initial=True)
        session.flush()
        return _serialize_import(session, row)


def read_menu_import(month_id, import_id):
    with session_scope() as session:
        menu_revision.begin_monthly_snapshot(session, write=False)
        _lock_month_revision(session, month_id, write=False)
        row = _saved_import(session, month_id, import_id)
        # A stored review is paired only with its recorded input, never re-based on GET.
        receipt = _serialize_import(session, row)
        if row.state == "review":
            _, preview = _preview_saved_import(session, row, row.review_json["resolutions"])
            receipt["current_review"] = _import_review(session, row, preview, row.review_json["resolutions"])
            receipt["current_input_revision"] = _monthly_snapshot_body(session, month_id)["revision"]
        return receipt


def list_menu_imports(month_id):
    with session_scope() as session:
        menu_revision.begin_monthly_snapshot(session, write=False)
        rows = session.execute(select(MonthlyMenuImport).where(MonthlyMenuImport.month_id == month_id)
            .order_by(MonthlyMenuImport.created_at.desc(), MonthlyMenuImport.id)).scalars().all()
        return [_serialize_import(session, row) for row in rows]


def get_menu_import_download(month_id, import_id):
    with session_scope() as session:
        menu_revision.begin_monthly_snapshot(session, write=False)
        _lock_month_revision(session, month_id, write=False)
        row = _saved_import(session, month_id, import_id)
        source = _serialize_import(session, row)["source"]
        content = load_bytes_from_uri(source["file_uri"])
        if hashlib.sha256(content).hexdigest() != source["content_sha256"]:
            raise ValueError("saved source archive digest mismatch")
        return {"bytes": content, "filename": source["filename"], "media_type": source["media_type"]}


def review_menu_import(month_id, import_id, expected, resolutions, *, apply=False, input_expected=None):
    with session_scope() as session:
        menu_revision.begin_monthly_snapshot(session, write=True)
        revision = _lock_month_revision(session, month_id, write=True)
        row = _saved_import(session, month_id, import_id)
        if row.state == "applied":
            raise menu_revision.RevisionConflict("import already applied; do not reapply")
        if row.state != "review":
            raise menu_revision.RevisionConflict("import is not in recorded review state; do not reapply")
        menu_revision.require_monthly_token(expected, _import_token(session, row, captured=not apply))
        if not apply:
            menu_revision.require_monthly_token(input_expected, _monthly_snapshot_body(session, month_id)["revision"])
        if apply:
            if resolutions is not None and resolutions != row.review_json["resolutions"]:
                raise menu_revision.RevisionConflict("save this exact review before applying")
            resolutions = row.review_json["resolutions"]
        content, preview = _preview_saved_import(session, row, resolutions)
        if apply:
            if preview["issues"]:
                raise MenuMasterResolutionRequired(preview["issues"])
            source = row.source_json
            _, replaced, count = create_menu(month_id, content, source["filename"], source["sheet_name"],
                upload_metadata={**source, "import_id": row.id}, scope_override=source["scope_override"],
                menu_master_resolutions=resolutions, require_menu_master_review=True, _session=session,
                diet_types=source["diet_types"])
            menu_revision.compare_and_advance(session, menu_revision.monthly_scope(month_id), revision)
            row.state = "applied"
        if not apply:
            row.review_json = _import_review(session, row, preview, resolutions)
        session.flush()
        own = _serialize_import(session, row)
        return {"created": True, "receipt": own, "snapshot": _monthly_snapshot_body(session, month_id),
                "replaced": replaced, "item_count": count} if apply else own


def monthly_editor_command(month_id, item_id, body, expected, *, action):
    source_month = menu_revision.monthly_command_source(expected)
    with session_scope() as session:
        menu_revision.begin_monthly_snapshot(session, write=True)
        revision = _lock_month_revision(session, source_month, write=True)
        before = _monthly_snapshot_body(session, source_month)
        menu_revision.require_monthly_token(expected, before["revision"])
        if source_month != month_id and (source_month not in [_shift_month_id(month_id, delta) for delta in (-1, 1)]
            or not _payload_contains_requested_month(before, month_id) or session.get(MonthlyMenu, month_id) is not None):
            raise menu_revision.RevisionConflict("command target differs from recorded source month")
        functions = {"item": update_item_status, "exceptions": upsert_entry_exceptions, "master": resolve_menu_master_check}
        result = functions[action](source_month, item_id, body, _session=session)
        if result in (None, "not_found", "month_mismatch"):
            raise ValueError("monthly target not found")
        if isinstance(result, str) and result != "updated":
            if result == "conflict":
                raise menu_revision.RevisionConflict("duplicate monthly menu item")
            raise ValueError(result)
        menu_revision.compare_and_advance(session, menu_revision.monthly_scope(source_month), revision)
        session.flush()
        return {**(result if isinstance(result, dict) else {}), "updated": True, "result": result, "snapshot": _canonicalize_resolved_menu_payload(
            _monthly_snapshot_body(session, source_month), month_id, source_month)}


def create_menu(
    month_id: str,
    file_bytes: bytes,
    filename: str,
    sheet_name: str | None = None,
    *,
    actor: str = "system",
    upload_metadata: dict | None = None,
    scope_override: str | None = None,
    menu_master_resolutions: list[dict] | None = None,
    require_menu_master_review: bool = False,
    _session=None,
    preview: bool = False,
    diet_types: list[str] | None = None,
):
    if _session is None:
        ensure_menu_schema()
        # Legacy standalone callers initialize rules outside their write transaction.
        menu_rule_service.ensure_default_rules()
    parsed_items: list[dict] = []
    entries: list[dict] = []
    month_start = None
    parse_error: ValueError | None = None
    try:
        month_start, _, parsed_items, entries = _parse_monthly_menu(
            file_bytes,
            filename,
            sheet_name,
            month_id,
        )
    except ValueError as exc:
        parse_error = exc
        parsed_items = []
        entries = []
    parsed_month_id = _month_id_from_date(month_start)
    requested_month_id = _normalize_month_id(month_id)
    if parsed_month_id and requested_month_id and parsed_month_id != requested_month_id:
        raise ValueError(f"menu_month_mismatch:{requested_month_id}!={parsed_month_id}")
    if parsed_items:
        names = [item["name"] for item in parsed_items]
    else:
        names = _extract_menu_names(file_bytes, filename, sheet_name)
    if require_menu_master_review and not entries:
        detail = str(parse_error) if parse_error else "日付別献立の行が見つかりません"
        raise ValueError(f"monthly_menu_entries_missing:{detail}")
    deduped_names: list[str] = []
    seen_normalized_names: set[str] = set()
    for raw_name in names:
        normalized_name = _normalize_menu_name(raw_name)
        if not normalized_name or normalized_name in seen_normalized_names:
            continue
        seen_normalized_names.add(normalized_name)
        deduped_names.append(raw_name)
    names = deduped_names
    resolved_scope_override = _normalize_scope_override(scope_override)
    if _session is None:
        parsed_items = _apply_rules_to_items(parsed_items)
    else:
        rules = [menu_rule_service._serialize_rule(rule)
            for rule in _session.query(MenuRule).filter(MenuRule.active.is_(True)).all()]
        parsed_items = _apply_rule_payloads_to_items(parsed_items, rules)
    if diet_types is not None:
        if not diet_types or any(normalize_diet_type(value) != value for value in diet_types):
            raise ValueError("explicit normalized diet_types are required")
        parsed_items = [item for item in parsed_items if normalize_diet_type(item.get("diet_type")) in diet_types]
        entries = [entry for entry in entries if normalize_diet_type(entry.get("diet_type")) in diet_types]
        names = list(dict.fromkeys(item["name"] for item in parsed_items))
        if not parsed_items or not entries:
            raise ValueError("selected food types are absent from the saved source")
    if parsed_items:
        deduped_items: list[dict] = []
        seen_item_keys: set[tuple[str, str, str, str, str]] = set()
        for item in parsed_items:
            item_key = _monthly_item_identity_key_from_payload(item, resolved_scope_override)
            if not item_key[0] or item_key in seen_item_keys:
                continue
            seen_item_keys.add(item_key)
            deduped_items.append(item)
        parsed_items = deduped_items
    else:
        parsed_items = [{"name": name} for name in names]
    resolution_map = _index_menu_master_resolutions(menu_master_resolutions)
    with (nullcontext(_session) if _session is not None else session_scope()) as session:
        master_plans: dict[str, dict] = {}
        master_plans_by_normalized_name: dict[str, dict] = {}
        issues: list[dict] = []
        for name in names:
            meta = next((item for item in parsed_items if str(item.get("name") or "") == name), {})
            seed_patch = _extract_master_patch(meta, _MASTER_FIELDS)
            plan = _build_upload_menu_master_plan(
                session,
                name,
                seed_patch,
                _resolve_menu_master_resolution(resolution_map, name),
                require_bagging_review=False,
            )
            issue = plan.get("issue") if isinstance(plan, dict) else None
            if issue:
                if require_menu_master_review:
                    issues.append(issue)
                    continue
                plan = {
                    "action": "create",
                    "name": name,
                    "seed_fields": dict(seed_patch or {}),
                }
            master_plans[name] = plan
            if plan.get("action") == "existing":
                plan["seed_fields"] = {
                    field: value for field, value in (plan.get("seed_fields") or {}).items()
                    if field not in meta.get("_inferred_master_fields", [])
                }
            normalized_plan_name = _normalize_menu_name(name)
            if normalized_plan_name:
                master_plans_by_normalized_name[normalized_plan_name] = plan
        if preview:
            master_diffs = []
            for meta in parsed_items:
                master = _find_menu_master_by_normalized(session, _normalize_menu_name(meta["name"]))
                if master is not None:
                    patch = _extract_master_patch(meta, _MASTER_FIELDS)
                    for field in meta.get("_inferred_master_fields", []):
                        patch.pop(field, None)
                    diffs = _build_master_field_diffs(meta["name"], patch, master)
                    if diffs:
                        master_diffs.append({"source_name": meta["name"], "current_master": serialize_menu_master(master), "field_diffs": diffs})
            return {"issues": issues, "plans": master_plans, "item_count": len(parsed_items),
                "master_diffs": master_diffs,
                "entries": [{**entry, "menu_date": entry["menu_date"].isoformat()} for entry in entries],
                "items": parsed_items, "scope_override": resolved_scope_override}
        if issues and require_menu_master_review:
            raise MenuMasterResolutionRequired(issues)
    with (nullcontext(_session) if _session is not None else session_scope()) as session:
        replaced = False
        menu = session.get(MonthlyMenu, month_id)
        if menu:
            replaced = True
            if resolved_scope_override is None:
                menu.filename = filename
            if month_start and resolved_scope_override is None:
                menu.month_start = month_start
            item_delete = delete(MonthlyMenuItem).where(MonthlyMenuItem.monthly_menu_id == month_id)
            entry_delete = delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == month_id)
            if resolved_scope_override is None:
                item_delete = item_delete.where(
                    or_(MonthlyMenuItem.facility_override.is_(None), MonthlyMenuItem.facility_override == "")
                )
                entry_delete = entry_delete.where(
                    or_(MonthlyMenuEntry.facility_override.is_(None), MonthlyMenuEntry.facility_override == "")
                )
            else:
                item_delete = item_delete.where(MonthlyMenuItem.facility_override == resolved_scope_override)
                entry_delete = entry_delete.where(MonthlyMenuEntry.facility_override == resolved_scope_override)
            imported_diets = diet_types if diet_types is not None else list({normalize_diet_type(item.get("diet_type")) for item in parsed_items})
            named_diets = [value for value in imported_diets if value is not None]
            item_diets = MonthlyMenuItem.diet_type.in_(named_diets)
            entry_diets = MonthlyMenuEntry.diet_type.in_(named_diets)
            if None in imported_diets:
                item_diets = or_(item_diets, MonthlyMenuItem.diet_type.is_(None), MonthlyMenuItem.diet_type == "")
                entry_diets = or_(entry_diets, MonthlyMenuEntry.diet_type.is_(None), MonthlyMenuEntry.diet_type == "")
            item_delete = item_delete.where(item_diets)
            entry_delete = entry_delete.where(entry_diets)
            session.execute(item_delete)
            session.execute(entry_delete)
        else:
            menu = MonthlyMenu(id=month_id, filename=filename, month_start=month_start)
            session.add(menu)
            session.flush()
            session.refresh(menu)

        for meta in parsed_items:
            name = str(meta.get("name") or "").strip()
            if not name:
                continue
            item_id = f"MMI{uuid4().hex[:6]}"
            plan = master_plans.get(name)
            if plan is None:
                plan = master_plans_by_normalized_name.get(_normalize_menu_name(name))
            if plan is None:
                raise ValueError(f"menu master resolution plan not found: {name}")
            master = _materialize_upload_menu_master_plan(session, name, plan)
            item_patch = _monthly_item_patch_from_source(meta, master)
            session.add(
                MonthlyMenuItem(
                    id=item_id,
                    monthly_menu_id=month_id,
                    menu_master_id=master.id if master else None,
                    name=name,
                    unit_type=_coerce_master_field_value("unit_type", item_patch.get("unit_type")),
                    qty_per_serving=_coerce_float(item_patch.get("qty_per_serving")),
                    temp_type=_normalize_temp_type(item_patch.get("temp_type")),
                    daypart=_coerce_master_field_value("daypart", item_patch.get("daypart")),
                    category=item_patch.get("category"),
                    diet_type=normalize_diet_type(meta.get("diet_type")),
                    facility_override=resolved_scope_override,
                    master_resolution_mode=None,
                    bag_max_qty=_coerce_float(item_patch.get("bag_max_qty")),
                    bag_max_unit=_coerce_master_field_value("bag_max_unit", item_patch.get("bag_max_unit")),
                )
            )
        for entry in entries:
            session.add(
                MonthlyMenuEntry(
                    id=f"MME{uuid4().hex[:7]}",
                    monthly_menu_id=month_id,
                    menu_date=entry["menu_date"],
                    daypart=entry["daypart"],
                    name=entry["name"],
                    category=entry.get("category"),
                    diet_type=normalize_diet_type(entry.get("diet_type")),
                    slot_index=entry.get("slot_index"),
                    facility_override=resolved_scope_override,
                )
            )
        item_count = len(parsed_items)
        logger.info(
            "Menu uploaded",
            month_id=month_id,
            filename=filename,
            replaced=replaced,
            item_count=item_count,
            scope_override=resolved_scope_override,
        )
        payload = serialize_menu(menu)
    metadata = {
        "filename": filename,
        "replaced": replaced,
        "item_count": item_count,
    }
    if resolved_scope_override:
        metadata["scope_override"] = resolved_scope_override
    if sheet_name:
        metadata["sheet_name"] = sheet_name
    if upload_metadata:
        metadata.update(upload_metadata)
    if _session is not None:
        if os.getenv("AUDIT_ENABLED", "true").lower() == "true":
            _session.add(AuditLog(id=f"AUD{uuid4().hex}", actor=actor, action="menu_upload",
                target=month_id, wek=month_id, metadata_json=metadata))
    else:
        record_event("menu_upload", actor=actor, target=month_id, wek=month_id, metadata=metadata)
    return payload, replaced, item_count




def list_menu_uploads(month_id: str) -> list[dict]:
    ensure_menu_schema()
    with session_scope() as session:
        logs = (
            session.query(AuditLog)
            .filter(AuditLog.action == "menu_upload")
            .filter(AuditLog.target == month_id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .all()
        )
        items: list[dict] = []
        for log in logs:
            metadata = dict(log.metadata_json or {})
            file_uri = str(metadata.get("file_uri") or "").strip()
            entry = {
                "id": log.id,
                "month_id": month_id,
                "uploaded_at": log.created_at.isoformat() if log.created_at else None,
                "filename": metadata.get("filename"),
                "sheet_name": metadata.get("sheet_name"),
                "item_count": metadata.get("item_count"),
                "replaced": bool(metadata.get("replaced")),
                "actor": log.actor,
                "download_available": bool(file_uri),
                "scope_override": metadata.get("scope_override"),
            }
            archive_error = metadata.get("archive_error")
            if archive_error:
                entry["archive_error"] = archive_error
            items.append(entry)
        return items


def _format_menu_upload_display(created_at: datetime | None) -> str | None:
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return f"{created_at.astimezone(_JST).strftime('%Y/%m/%d %H:%M')} アップロード"


def _is_scoped_menu_upload_log(log: AuditLog) -> bool:
    metadata = dict(log.metadata_json or {})
    return bool(str(metadata.get("scope_override") or "").strip())


def _get_latest_menu_upload_log(session, month_id: str, *, include_scoped: bool = False) -> AuditLog | None:
    logs = (
        session.query(AuditLog)
        .filter(AuditLog.action == "menu_upload")
        .filter(AuditLog.target == month_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .all()
    )
    for log in logs:
        if include_scoped or not _is_scoped_menu_upload_log(log):
            return log
    return None


def get_menu_upload_download(month_id: str, upload_id: str) -> dict | None:
    ensure_menu_schema()
    with session_scope() as session:
        log = (
            session.query(AuditLog)
            .filter(AuditLog.id == upload_id)
            .filter(AuditLog.action == "menu_upload")
            .filter(AuditLog.target == month_id)
            .first()
        )
        if not log:
            return None
        metadata = dict(log.metadata_json or {})
        file_uri = str(metadata.get("file_uri") or "").strip()
        if not file_uri:
            return {
                "filename": metadata.get("filename") or "monthly-menu.xlsx",
                "media_type": "application/octet-stream",
                "bytes": None,
                "download_available": False,
            }
        filename = str(metadata.get("filename") or "monthly-menu.xlsx")
    payload = load_bytes_from_uri(file_uri)
    suffix = Path(filename).suffix.lower()
    media_type = (
        "text/csv"
        if suffix == ".csv"
        else "application/vnd.ms-excel.sheet.macroEnabled.12"
        if suffix == ".xlsm"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return {
        "filename": filename,
        "media_type": media_type,
        "bytes": payload,
        "download_available": True,
    }


def update_item_status(month_id: str, item_id: str, body: dict, *, _session=None) -> str:
    if _session is None:
        ensure_menu_schema()
    event_fields: list[str] | None = None
    with (nullcontext(_session) if _session is not None else session_scope()) as session:
        item = session.get(MonthlyMenuItem, item_id)
        if not item:
            if not item_id.startswith(_REPAIRED_MENU_ITEM_ID_PREFIX):
                return "not_found"
            entry_id = item_id[len(_REPAIRED_MENU_ITEM_ID_PREFIX) :]
            entry = session.get(MonthlyMenuEntry, entry_id)
            if not entry or entry.monthly_menu_id != month_id:
                return "not_found"
            name = str(body.get("name") or entry.name or "").strip()
            if not name:
                return "invalid_name"
            item = MonthlyMenuItem(
                id=item_id,
                monthly_menu_id=month_id,
                name=name,
                facility_override=_normalize_scope_override(body.get("facility_override", entry.facility_override)),
                daypart=_coerce_master_field_value("daypart", body.get("daypart", entry.daypart)),
                category=body.get("category", entry.category),
                diet_type=normalize_diet_type(body.get("diet_type", entry.diet_type)),
                master_resolution_mode="month_only",
            )
            session.add(item)
        if item.monthly_menu_id != month_id:
            return "month_mismatch"
        keep_monthly_identity_values = bool(
            str(item.daypart or "").strip()
            or str(item.category or "").strip()
            or item_id.startswith(_REPAIRED_MENU_ITEM_ID_PREFIX)
        )
        if "name" in body:
            next_name = str(body.get("name") or "").strip()
            if next_name:
                item.name = next_name
        if "facility_override" in body:
            facility_override = body.get("facility_override")
            if _is_blank_value(facility_override):
                item.facility_override = None
            else:
                item.facility_override = str(facility_override).strip()
        if "diet_type" in body:
            item.diet_type = normalize_diet_type(body.get("diet_type"))
        if "daypart" in body:
            item.daypart = _coerce_master_field_value("daypart", body.get("daypart"))
        if "category" in body:
            item.category = str(body.get("category") or "").strip() or None
        name = (item.name or "").strip()
        if not name:
            return "invalid_name"
        conflict = _find_existing_monthly_item(
            session,
            month_id,
            name,
            item.facility_override,
            daypart=item.daypart,
            category=item.category,
            diet_type=item.diet_type,
            exclude_id=item.id,
        )
        if conflict:
            return "conflict"

        master_patch = _extract_master_patch(body, allow_null=True)
        master = _ensure_menu_master(session, name, item.menu_master_id)
        if master:
            item.menu_master_id = master.id
        _upsert_menu_master(session, item, master_patch, master)
        if keep_monthly_identity_values:
            item.master_resolution_mode = "month_only"
            for field, value in master_patch.items():
                setattr(item, field, value)
        else:
            item.master_resolution_mode = None
            for field in _MASTER_FIELDS:
                if field in ("daypart", "category"):
                    continue
                setattr(item, field, None)
        logger.info("Menu item updated", month_id=month_id, item_id=item_id)
        event_fields = list(body.keys())
    if _session is not None:
        if os.getenv("AUDIT_ENABLED", "true").lower() == "true":
            _session.add(AuditLog(id=f"AUD{uuid4().hex}", actor="system", action="menu_edit", target=item_id, wek=month_id,
                metadata_json={"fields": event_fields or []}))
    else:
        record_event("menu_edit", actor="system", target=item_id, wek=month_id,
            metadata={"fields": event_fields or []})
    return "updated"


def update_item(month_id: str, item_id: str, body: dict) -> bool:
    return update_item_status(month_id, item_id, body) == "updated"


def _upsert_menu_master(
    session,
    item: MonthlyMenuItem,
    master_patch: dict | None = None,
    master: MenuMaster | None = None,
) -> None:
    resolved_master = master or _ensure_menu_master(session, item.name, item.menu_master_id)
    if not resolved_master:
        return
    item.menu_master_id = resolved_master.id
    if not master_patch:
        return
    facility_id = (item.facility_override or "").strip()
    if not facility_id:
        _apply_master_patch(resolved_master, master_patch)
    else:
        override = _get_or_create_menu_override(session, resolved_master.id, facility_id)
        _apply_master_patch(override, master_patch)


def _find_existing_monthly_item(
    session,
    month_id: str,
    name: str,
    facility_override: str | None,
    *,
    daypart: str | None = None,
    category: str | None = None,
    diet_type: str | None = None,
    exclude_id: str | None = None,
) -> MonthlyMenuItem | None:
    normalized_diet = normalize_diet_type(diet_type)
    # Nonempty inputs discarded by normalization are not unresolved identities.
    if normalized_diet is None and diet_type is not None and str(diet_type).strip():
        return None
    query = select(MonthlyMenuItem).where(MonthlyMenuItem.monthly_menu_id == month_id).where(MonthlyMenuItem.name == name)
    scope = (facility_override or "").strip()
    if not scope:
        query = query.where(
            or_(
                MonthlyMenuItem.facility_override.is_(None),
                MonthlyMenuItem.facility_override == "",
            )
        )
    else:
        query = query.where(MonthlyMenuItem.facility_override == scope)
    normalized_daypart = str(_coerce_master_field_value("daypart", daypart) or "").strip()
    if normalized_daypart:
        query = query.where(MonthlyMenuItem.daypart == normalized_daypart)
    else:
        query = query.where(or_(MonthlyMenuItem.daypart.is_(None), MonthlyMenuItem.daypart == ""))
    normalized_category = str(category or "").strip()
    if normalized_category:
        query = query.where(MonthlyMenuItem.category == normalized_category)
    else:
        query = query.where(or_(MonthlyMenuItem.category.is_(None), MonthlyMenuItem.category == ""))
    if normalized_diet is None:
        query = query.where(or_(MonthlyMenuItem.diet_type.is_(None), MonthlyMenuItem.diet_type == ""))
    else:
        query = query.where(MonthlyMenuItem.diet_type == normalized_diet)
    if exclude_id:
        query = query.where(MonthlyMenuItem.id != exclude_id)
    matches = session.execute(query).scalars().all()
    if len(matches) > 1:
        raise ValueError("monthly_menu_item_identity_ambiguous")
    return matches[0] if matches else None


def create_item_stub(month_id: str, name: str):
    ensure_menu_schema()
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError("name is required")
    with session_scope() as session:
        existing = _find_existing_monthly_item(session, month_id, normalized_name, None)
        if existing:
            return serialize_item(existing)
        item_id = f"MMI{uuid4().hex[:6]}"
        master = _ensure_menu_master(session, normalized_name)
        item = MonthlyMenuItem(
            id=item_id,
            monthly_menu_id=month_id,
            menu_master_id=master.id if master else None,
            name=normalized_name,
        )
        session.add(item)
        session.flush()
        session.refresh(item)
        return serialize_item(item)


def _normalize_exception_facility_ids(
    session,
    values: object,
    *,
    fallback_scope: str | None = None,
) -> list[str]:
    raw_values: list[str] = []
    if isinstance(values, list):
        raw_values = [str(value or "").strip() for value in values if str(value or "").strip()]
    elif isinstance(values, str):
        raw_values = [part.strip() for part in re.split(r"[,\n\r\t ]+", values) if part.strip()]
    fallback = str(fallback_scope or "").strip()
    if not raw_values and fallback:
        if fallback.upper().startswith(_MENU_OVERRIDE_TAG_PREFIX):
            raise ValueError("tag scoped entries cannot be edited with direct facility overrides")
        raw_values = [fallback]
    seen: set[str] = set()
    normalized: list[str] = []
    for value in raw_values:
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    if not normalized:
        raise ValueError("facility_ids is required")
    facilities = (
        session.execute(select(Facility.id).where(Facility.id.in_(normalized)))
        .scalars()
        .all()
    )
    found = {str(item or "").strip() for item in facilities if str(item or "").strip()}
    missing = [facility_id for facility_id in normalized if facility_id not in found]
    if missing:
        raise ValueError(f"unknown facilities: {', '.join(missing)}")
    return normalized


def _find_existing_monthly_entry(
    session,
    *,
    month_id: str,
    menu_date: date,
    daypart: str,
    slot_index: int | None,
    facility_override: str | None,
    diet_type: str | None,
) -> MonthlyMenuEntry | None:
    if not _monthly_entry_diet_identity(diet_type):
        return None
    query = (
        select(MonthlyMenuEntry)
        .where(MonthlyMenuEntry.monthly_menu_id == month_id)
        .where(MonthlyMenuEntry.menu_date == menu_date)
    )
    normalized_daypart = str(_coerce_master_field_value("daypart", daypart) or "").strip()
    if normalized_daypart:
        query = query.where(MonthlyMenuEntry.daypart == normalized_daypart)
    else:
        query = query.where(or_(MonthlyMenuEntry.daypart.is_(None), MonthlyMenuEntry.daypart == ""))
    if slot_index is None:
        query = query.where(MonthlyMenuEntry.slot_index.is_(None))
    else:
        query = query.where(MonthlyMenuEntry.slot_index == slot_index)
    scope = (facility_override or "").strip()
    if not scope:
        query = query.where(
            or_(
                MonthlyMenuEntry.facility_override.is_(None),
                MonthlyMenuEntry.facility_override == "",
            )
        )
    else:
        query = query.where(MonthlyMenuEntry.facility_override == scope)
    query = query.where(_monthly_entry_diet_filter(MonthlyMenuEntry.diet_type, diet_type))
    matches = session.execute(query).scalars().all()
    if len(matches) > 1:
        raise ValueError("monthly_menu_entry_identity_ambiguous")
    return matches[0] if matches else None


def _find_best_monthly_item_for_entry(
    session,
    *,
    month_id: str,
    name: str,
    facility_override: str | None,
    diet_type: str | None,
    daypart: str | None = None,
    category: str | None = None,
) -> MonthlyMenuItem | None:
    if not normalize_diet_type(diet_type):
        return None
    return _find_existing_monthly_item(
        session,
        month_id,
        name,
        facility_override,
        daypart=daypart,
        category=category,
        diet_type=diet_type,
    )


def _count_monthly_entry_refs(
    session,
    *,
    month_id: str,
    name: str,
    facility_override: str | None,
    diet_type: str | None = None,
    daypart: str | None = None,
    category: str | None = None,
    exclude_entry_id: str | None = None,
) -> int:
    query = (
        select(MonthlyMenuEntry)
        .where(MonthlyMenuEntry.monthly_menu_id == month_id)
        .where(MonthlyMenuEntry.name == name)
    )
    scope = (facility_override or "").strip()
    if not scope:
        query = query.where(
            or_(
                MonthlyMenuEntry.facility_override.is_(None),
                MonthlyMenuEntry.facility_override == "",
            )
        )
    else:
        query = query.where(MonthlyMenuEntry.facility_override == scope)
    if diet_type is not None:
        query = query.where(_monthly_entry_diet_filter(MonthlyMenuEntry.diet_type, diet_type))
    normalized_daypart = str(_coerce_master_field_value("daypart", daypart) or "").strip()
    if normalized_daypart:
        query = query.where(MonthlyMenuEntry.daypart == normalized_daypart)
    normalized_category = str(category or "").strip()
    if normalized_category:
        query = query.where(MonthlyMenuEntry.category == normalized_category)
    if exclude_entry_id:
        query = query.where(MonthlyMenuEntry.id != exclude_entry_id)
    return len(session.execute(query).scalars().all())


def upsert_entry_exceptions(month_id: str, entry_id: str, body: dict, *, _session=None) -> dict | None:
    if _session is None:
        ensure_menu_schema()
    with (nullcontext(_session) if _session is not None else session_scope()) as session:
        entry = session.get(MonthlyMenuEntry, entry_id)
        if not entry or entry.monthly_menu_id != month_id:
            return None

        source_scope = str(entry.facility_override or "").strip() or None
        facility_ids = _normalize_exception_facility_ids(
            session,
            body.get("facility_ids"),
            fallback_scope=source_scope,
        )

        source_item = _find_best_monthly_item_for_entry(
            session,
            month_id=month_id,
            name=str(entry.name or "").strip(),
            facility_override=source_scope,
            diet_type=str(entry.diet_type or "").strip() or None,
            daypart=str(entry.daypart or "").strip() or None,
            category=str(entry.category or "").strip() or None,
        )
        name = str(body.get("name") or entry.name or "").strip()
        if not name:
            raise ValueError("name is required")
        category = _coerce_master_field_value(
            "category",
            body["category"] if "category" in body else entry.category or getattr(source_item, "category", None),
        )
        if category is _INVALID_PATCH_VALUE:
            raise ValueError("invalid category")
        diet_type = normalize_diet_type(
            body["diet_type"] if "diet_type" in body else entry.diet_type or getattr(source_item, "diet_type", None)
        )
        unit_type = _normalize_menu_unit_type(
            body["unit_type"] if "unit_type" in body else getattr(source_item, "unit_type", None)
        )
        qty_per_serving = _coerce_float(
            body["qty_per_serving"] if "qty_per_serving" in body else getattr(source_item, "qty_per_serving", None)
        )
        bag_max_qty = _coerce_float(
            body["bag_max_qty"] if "bag_max_qty" in body else getattr(source_item, "bag_max_qty", None)
        )
        bag_max_unit = _normalize_menu_unit_type(
            body["bag_max_unit"] if "bag_max_unit" in body else getattr(source_item, "bag_max_unit", None)
        )
        temp_type = _normalize_temp_type(
            body["temp_type"] if "temp_type" in body else getattr(source_item, "temp_type", None)
        )
        resolved_daypart = _coerce_master_field_value(
            "daypart",
            entry.daypart or getattr(source_item, "daypart", None),
        )
        if resolved_daypart is _INVALID_PATCH_VALUE:
            resolved_daypart = str(entry.daypart or "").strip() or None
        seed_fields = {
            "unit_type": unit_type,
            "qty_per_serving": qty_per_serving,
            "temp_type": temp_type,
            "daypart": resolved_daypart,
            "category": category,
            "bag_max_qty": bag_max_qty,
            "bag_max_unit": bag_max_unit,
        }

        updated_entries: list[dict] = []
        updated_items: list[dict] = []
        for facility_id in facility_ids:
            scoped_entry = _find_existing_monthly_entry(
                session,
                month_id=month_id,
                menu_date=entry.menu_date,
                daypart=str(entry.daypart or ""),
                slot_index=entry.slot_index,
                facility_override=facility_id,
                diet_type=diet_type,
            )
            previous_name = str(scoped_entry.name or "").strip() if scoped_entry else ""
            if scoped_entry is None:
                scoped_entry = MonthlyMenuEntry(
                    id=f"MME{uuid4().hex[:8]}",
                    monthly_menu_id=month_id,
                    menu_date=entry.menu_date,
                    daypart=str(entry.daypart or ""),
                    slot_index=entry.slot_index,
                    facility_override=facility_id,
                    name=name,
                    category=category if isinstance(category, str) else None,
                    diet_type=diet_type,
                )
                session.add(scoped_entry)
            else:
                scoped_entry.name = name
                scoped_entry.category = category if isinstance(category, str) else None
                scoped_entry.diet_type = diet_type

            scoped_item = _find_existing_monthly_item(
                session,
                month_id,
                name,
                facility_id,
                daypart=resolved_daypart if isinstance(resolved_daypart, str) else None,
                category=category if isinstance(category, str) else None,
                diet_type=diet_type,
            )
            if scoped_item is None and previous_name and previous_name != name:
                reusable_item = _find_existing_monthly_item(
                    session,
                    month_id,
                    previous_name,
                    facility_id,
                    daypart=resolved_daypart if isinstance(resolved_daypart, str) else None,
                    category=category if isinstance(category, str) else None,
                    diet_type=diet_type,
                )
                if reusable_item is not None and _count_monthly_entry_refs(
                    session,
                    month_id=month_id,
                    name=previous_name,
                    facility_override=facility_id,
                    diet_type=diet_type,
                    daypart=resolved_daypart if isinstance(resolved_daypart, str) else None,
                    category=category if isinstance(category, str) else None,
                    exclude_entry_id=scoped_entry.id,
                ) == 0:
                    scoped_item = reusable_item
            if scoped_item is None:
                scoped_item = MonthlyMenuItem(
                    id=f"MMI{uuid4().hex[:8]}",
                    monthly_menu_id=month_id,
                    facility_override=facility_id,
                    name=name,
                )
                session.add(scoped_item)
            resolved_master = _ensure_menu_master(
                session,
                name,
                menu_master_id=scoped_item.menu_master_id or getattr(source_item, "menu_master_id", None),
                seed_fields=seed_fields,
            )
            scoped_item.name = name
            scoped_item.facility_override = facility_id
            scoped_item.menu_master_id = resolved_master.id if resolved_master else None
            scoped_item.master_resolution_mode = "month_only"
            scoped_item.unit_type = unit_type
            scoped_item.qty_per_serving = qty_per_serving
            scoped_item.temp_type = temp_type
            scoped_item.daypart = resolved_daypart if isinstance(resolved_daypart, str) else None
            scoped_item.category = category if isinstance(category, str) else None
            scoped_item.diet_type = diet_type
            scoped_item.bag_max_qty = bag_max_qty
            scoped_item.bag_max_unit = bag_max_unit

            if previous_name and previous_name != name:
                previous_item = _find_existing_monthly_item(
                    session,
                    month_id,
                    previous_name,
                    facility_id,
                    daypart=resolved_daypart if isinstance(resolved_daypart, str) else None,
                    category=category if isinstance(category, str) else None,
                    diet_type=diet_type,
                )
                if previous_item is not None and _count_monthly_entry_refs(
                    session,
                    month_id=month_id,
                    name=previous_name,
                    facility_override=facility_id,
                    diet_type=diet_type,
                    daypart=resolved_daypart if isinstance(resolved_daypart, str) else None,
                    category=category if isinstance(category, str) else None,
                    exclude_entry_id=scoped_entry.id,
                ) == 0:
                    session.delete(previous_item)

            updated_entries.append(serialize_entry(scoped_entry))
            updated_items.append(serialize_item(scoped_item))

        logger.info(
            "Monthly menu entry exceptions upserted",
            month_id=month_id,
            entry_id=entry_id,
            facility_ids=facility_ids,
        )
        return {
            "updated": True,
            "entry_id": entry_id,
            "facility_ids": facility_ids,
            "entries": updated_entries,
            "items": _merge_master_defaults(updated_items, facility_ids[0] if len(facility_ids) == 1 else None),
        }


def serialize_menu(menu: MonthlyMenu, latest_upload_log: AuditLog | None = None):
    uploaded_at = latest_upload_log.created_at.isoformat() if latest_upload_log and latest_upload_log.created_at else None
    display_name = _format_menu_upload_display(latest_upload_log.created_at if latest_upload_log else None) or menu.filename
    return {
        "id": menu.id,
        "filename": menu.filename,
        "month_start": menu.month_start.isoformat() if menu.month_start else None,
        "display_name": display_name,
        "uploaded_at": uploaded_at,
    }


def _serialize_synthetic_menu(month_id: str, latest_upload_log: AuditLog | None = None) -> dict:
    uploaded_at = latest_upload_log.created_at.isoformat() if latest_upload_log and latest_upload_log.created_at else None
    display_name = _format_menu_upload_display(latest_upload_log.created_at if latest_upload_log else None) or month_id
    return {
        "id": month_id,
        "filename": None,
        "month_start": None,
        "display_name": display_name,
        "uploaded_at": uploaded_at,
    }


def serialize_menu_master(master: MenuMaster) -> dict:
    return {
        "id": master.id,
        "revision": master.revision,
        "name": master.name,
        "normalized_name": master.normalized_name,
        "unit_type": _normalize_menu_unit_type(master.unit_type),
        "qty_per_serving": master.qty_per_serving,
        "bag_max_qty": master.bag_max_qty,
        "bag_max_unit": _normalize_menu_unit_type(master.bag_max_unit),
        "temp_type": _normalize_temp_type(master.temp_type),
        "daypart": _coerce_master_field_value("daypart", master.daypart),
        "category": master.category,
        "condiments": list(master.condiments or []),
    }


def list_menu_masters(query: str | None = None, limit: int = 1000) -> list[dict]:
    return list_menu_masters_page(query=query, limit=limit)["items"]


_MENU_MASTER_SORT_COLUMNS = {
    "id": MenuMaster.id,
    "name": MenuMaster.name,
    "unit_type": MenuMaster.unit_type,
    "qty_per_serving": MenuMaster.qty_per_serving,
    "bag_max_qty": MenuMaster.bag_max_qty,
    "bag_max_unit": MenuMaster.bag_max_unit,
    "temp_type": MenuMaster.temp_type,
    "daypart": MenuMaster.daypart,
    "category": MenuMaster.category,
    "revision": MenuMaster.revision,
}


def list_menu_masters_page(
    query: str | None = None, limit: int = 1000, offset: int = 0,
    sort: str = "name", order: str = "asc",
) -> dict:
    ensure_menu_schema()
    normalized_limit = max(1, min(int(limit or 1000), 5000))
    if offset < 0 or sort not in _MENU_MASTER_SORT_COLUMNS or order not in {"asc", "desc"}:
        raise ValueError("invalid menu master pagination or sort")
    with session_scope() as session:
        stmt = select(MenuMaster)
        count_stmt = select(func.count()).select_from(MenuMaster)
        if query and query.strip():
            q = f"%{query.strip()}%"
            stmt = stmt.where(MenuMaster.name.ilike(q))
            count_stmt = count_stmt.where(MenuMaster.name.ilike(q))
        column = _MENU_MASTER_SORT_COLUMNS[sort]
        ordering = column.desc() if order == "desc" else column.asc()
        stmt = stmt.order_by(ordering, MenuMaster.id.asc()).offset(offset).limit(normalized_limit)
        rows = session.execute(stmt).scalars().all()
        return {
            "items": [serialize_menu_master(row) for row in rows],
            "total": session.execute(count_stmt).scalar_one(),
            "offset": offset,
            "limit": normalized_limit,
        }


def get_menu_master(master_id: str) -> dict | None:
    ensure_menu_schema()
    with session_scope() as session:
        master = session.get(MenuMaster, master_id)
        return serialize_menu_master(master) if master else None


def create_menu_master(body: dict) -> dict:
    ensure_menu_schema()
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    normalized = _normalize_menu_name(name)
    if not normalized:
        raise ValueError("name is required")
    with session_scope() as session:
        existing = (
            session.execute(select(MenuMaster).where(MenuMaster.normalized_name == normalized))
            .scalars()
            .first()
        )
        if existing:
            return serialize_menu_master(existing)
        master = MenuMaster(
            id=f"MNU{uuid4().hex[:8]}",
            name=name,
            normalized_name=normalized,
            unit_type=_coerce_master_field_value("unit_type", body.get("unit_type")),
            qty_per_serving=_coerce_float(body.get("qty_per_serving")),
            bag_max_qty=_coerce_float(body.get("bag_max_qty")),
            bag_max_unit=_coerce_master_field_value("bag_max_unit", body.get("bag_max_unit")),
            temp_type=_normalize_temp_type(body.get("temp_type")),
            daypart=_coerce_master_field_value("daypart", body.get("daypart")),
            category=body.get("category"),
            condiments=body.get("condiments") if isinstance(body.get("condiments"), list) else [],
        )
        session.add(master)
        session.flush()
        session.refresh(master)
        return serialize_menu_master(master)


def update_menu_master(master_id: str, body: dict) -> bool:
    return save_menu_master(master_id, body) is not None


def save_menu_master(master_id: str, body: dict, *, expected_revision: int | None = None) -> dict | None:
    """Shared save for API revision checks and existing internal ORM callers."""
    ensure_menu_schema()
    if not master_id:
        return None
    with session_scope() as session:
        master = session.get(MenuMaster, master_id)
        if not master:
            return None
        if expected_revision is not None and master.revision != expected_revision:
            raise MenuMasterRevisionConflict("menu master revision conflict; reload before saving")
        _update_menu_master_in_session(session, master, body)
        session.flush()
        session.refresh(master)
        return serialize_menu_master(master)


def serialize_item(item: MonthlyMenuItem):
    return {
        "id": item.id,
        "month_id": item.monthly_menu_id,
        "menu_master_id": item.menu_master_id,
        "name": item.name,
        "unit_type": _normalize_menu_unit_type(item.unit_type),
        "qty_per_serving": item.qty_per_serving,
        "temp_type": _normalize_temp_type(item.temp_type),
        "daypart": _coerce_master_field_value("daypart", item.daypart),
        "category": item.category,
        "diet_type": normalize_diet_type(getattr(item, "diet_type", None)),
        "facility_override": item.facility_override,
        "master_resolution_mode": str(getattr(item, "master_resolution_mode", "") or "").strip() or None,
        "bag_max_qty": getattr(item, "bag_max_qty", None),
        "bag_max_unit": _normalize_menu_unit_type(getattr(item, "bag_max_unit", None)),
    }


def serialize_entry(entry: MonthlyMenuEntry) -> dict:
    return {
        "id": entry.id,
        "month_id": entry.monthly_menu_id,
        "menu_date": entry.menu_date.isoformat() if entry.menu_date else None,
        "daypart": _coerce_master_field_value("daypart", entry.daypart),
        "name": entry.name,
        "category": entry.category,
        "diet_type": normalize_diet_type(entry.diet_type),
        "slot_index": entry.slot_index,
        "facility_override": entry.facility_override,
    }


def _normalize_month_id(value: object) -> str | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", text):
        return None
    return text


def _shift_month_id(month_id: str, delta: int) -> str | None:
    normalized = _normalize_month_id(month_id)
    if not normalized:
        return None
    year = int(normalized[:4])
    month = int(normalized[5:7])
    index = year * 12 + (month - 1) + delta
    shifted_year = index // 12
    shifted_month = (index % 12) + 1
    return f"{shifted_year:04d}-{shifted_month:02d}"


def _payload_contains_requested_month(payload: dict | None, month_id: str) -> bool:
    normalized = _normalize_month_id(month_id)
    if not normalized or not isinstance(payload, dict):
        return False
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        menu_date = entry.get("menu_date")
        if not menu_date:
            continue
        try:
            parsed = date.fromisoformat(str(menu_date))
        except Exception:
            continue
        if f"{parsed.year:04d}-{parsed.month:02d}" == normalized:
            return True
    return False


def _canonicalize_resolved_menu_payload(
    payload: dict | None,
    requested_month_id: str,
    resolved_month_id: str,
) -> dict | None:
    if not isinstance(payload, dict):
        return payload
    requested = _normalize_month_id(requested_month_id)
    resolved = _normalize_month_id(resolved_month_id)
    if not requested or not resolved or requested == resolved:
        return payload

    normalized_payload = dict(payload)

    menu_payload = payload.get("menu")
    if isinstance(menu_payload, dict):
        rewritten_menu = dict(menu_payload)
        rewritten_menu["id"] = requested
        rewritten_menu["requested_month_id"] = requested
        rewritten_menu["source_month_id"] = resolved
        normalized_payload["menu"] = rewritten_menu

    def _rewrite_month_id(rows: object) -> object:
        if not isinstance(rows, list):
            return rows
        rewritten_rows: list[object] = []
        for row in rows:
            if not isinstance(row, dict):
                rewritten_rows.append(row)
                continue
            updated = dict(row)
            if "month_id" in updated:
                updated["month_id"] = requested
            updated["requested_month_id"] = requested
            updated["source_month_id"] = resolved
            rewritten_rows.append(updated)
        return rewritten_rows

    normalized_payload["items"] = _rewrite_month_id(payload.get("items"))
    normalized_payload["entries"] = _rewrite_month_id(payload.get("entries"))
    if "master_checks" in payload:
        normalized_payload["master_checks"] = _rewrite_month_id(payload.get("master_checks"))
    return normalized_payload




@contextmanager
def _menu_read_session(session=None):
    if session is not None:
        with session.no_autoflush:
            ensure_menu_schema(connection=session.connection())
            yield session
        return
    with session_scope() as session:
        connection = session.connection()
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN")
        elif connection.dialect.name == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with session.no_autoflush:
            yield session


def _get_menu_for_facility_direct(month_id: str, facility_id: str | None, *, allow_unresolved: bool = False, session=None) -> dict | None:
    if session is None:
        ensure_menu_schema()
    with (_menu_read_session(session) if session is not None else _menu_read_session()) as session:
        menu = session.get(MonthlyMenu, month_id)
        latest_upload_log = _get_latest_menu_upload_log(session, month_id)
        menu_payload = (
            serialize_menu(menu, latest_upload_log)
            if menu
            else _serialize_synthetic_menu(month_id, latest_upload_log)
        )
        items = _read_items_for_facility(session, month_id, facility_id, allow_unresolved=allow_unresolved)
        entries = _read_entries_for_facility(session, month_id, facility_id)
        if not menu and not items and not entries:
            return None
        return {"menu": menu_payload, "items": items, "entries": entries}


class MenuReadUnresolved(ValueError):
    """A downstream consumer cannot use unresolved operator-facing menu items."""
    code = "menu_amount_unresolved"

    def __init__(self, items: list[dict]):
        self.items = items
        super().__init__("monthly menu quantity or unit requires explicit operator resolution")


def is_resolved_quantity(quantity) -> bool:
    try:
        return isinstance(quantity, Number) and not isinstance(quantity, bool) and math.isfinite(float(quantity))
    except (TypeError, ValueError, OverflowError):
        return False


def _unresolved_menu_fields(item: dict) -> list[str]:
    fields = []
    if not normalize_diet_type(item.get("diet_type")):
        fields.append("diet_type")
    if not is_resolved_quantity(item.get("qty_per_serving")):
        fields.append("qty_per_serving")
    if _normalize_menu_unit_type(item.get("unit_type")) not in {"g", "cut", "count"}:
        fields.append("unit_type")
    return fields


def require_resolved_menu_items(items: list[dict]) -> None:
    unresolved = [
        item
        for item in items
        if item.get("confirmation_required")
        or item.get("ambiguity")
        or _unresolved_menu_fields(item)
    ]
    if unresolved:
        raise MenuReadUnresolved(unresolved)


def _readonly_monthly_items(session, month_id, items, entries, facility_id=None):
    payloads = [serialize_item(item) for item in items]
    keys = {_monthly_item_identity_key_from_item(item) for item in items}
    for entry in entries:
        key = _monthly_item_identity_key_from_entry(entry)
        # Facility selection already chose the authoritative scope for each identity.
        matched = key in keys or (facility_id and any(candidate[:4] == key[:4] for candidate in keys))
        generic_key = (key[0], "", "", key[3], key[4])
        if matched or (not key[2] and generic_key in keys):
            continue
        keys.add(key)
        payloads.append({
            "id": f"{_REPAIRED_MENU_ITEM_ID_PREFIX}{entry.id}",
            "month_id": month_id, "menu_master_id": None, "name": entry.name,
            "unit_type": None, "qty_per_serving": None, "temp_type": None,
            "daypart": _coerce_master_field_value("daypart", entry.daypart),
            "category": entry.category, "diet_type": normalize_diet_type(entry.diet_type),
            "facility_override": entry.facility_override, "master_resolution_mode": "month_only",
            "bag_max_qty": None, "bag_max_unit": None,
        })
    scope_ids = _resolve_override_scope_ids(session, facility_id) if facility_id else []
    for payload in payloads:
        payload_scope_ids = scope_ids if facility_id else _resolve_override_scope_ids(
            session, payload.get("facility_override")
        )
        missing = []
        master_id = payload.get("menu_master_id")
        if master_id:
            master = session.get(MenuMaster, master_id)
            if master is None:
                missing.append("menu_master_id")
            else:
                overrides = session.query(MenuFacilityOverride).filter(
                    MenuFacilityOverride.menu_master_id == master_id,
                    MenuFacilityOverride.facility_id.in_(payload_scope_ids),
                ).all() if payload_scope_ids else []
                candidates = []
                if overrides:
                    best = min(payload_scope_ids.index(row.facility_id) for row in overrides)
                    candidates = [row for row in overrides if payload_scope_ids.index(row.facility_id) == best]
                if len(candidates) > 1:
                    missing.append("ambiguous_master_override")
                else:
                    override = candidates[0] if candidates else None
                    for field in ("qty_per_serving", "unit_type", "temp_type", "bag_max_qty", "bag_max_unit", "condiments"):
                        if not _is_blank_value(payload.get(field)):
                            continue
                        value = getattr(override, field, None) if override is not None else None
                        if _is_blank_value(value):
                            value = getattr(master, field, None)
                        if not _is_blank_value(value):
                            payload[field] = value
        missing.extend(_unresolved_menu_fields(payload))
        if missing:
            payload["confirmation_required"] = True
            payload["unresolved_fields"] = missing
    return payloads


def _ambiguous_menu_item_payload(item: MonthlyMenuItem, candidate_ids: list[str]) -> dict:
    """Expose an unresolved projection without choosing an arbitrary duplicate."""
    payload = {
        "id": None,
        "month_id": item.monthly_menu_id,
        "menu_master_id": None,
        "name": item.name,
        "unit_type": None,
        "qty_per_serving": None,
        "temp_type": None,
        "daypart": _coerce_master_field_value("daypart", item.daypart),
        "category": item.category,
        "diet_type": normalize_diet_type(getattr(item, "diet_type", None)),
        "facility_override": None,
        "master_resolution_mode": None,
        "bag_max_qty": None,
        "bag_max_unit": None,
    }
    payload.update({
        "confirmation_required": True,
        "unresolved_fields": ["menu_identity"],
        "ambiguity": {
            "code": "menu_identity_ambiguous",
            "candidate_ids": sorted(candidate_ids),
        },
    })
    return payload


def _readonly_monthly_items_with_scope_ambiguity(
    session,
    month_id: str,
    items: list[MonthlyMenuItem],
    entries: list[MonthlyMenuEntry],
    facility_id: str | None = None,
) -> list[dict]:
    """Return readonly items, replacing duplicate canonical identities with blockers."""
    by_identity: dict[tuple[str, str, str, str, str], list[MonthlyMenuItem]] = {}
    for item in items:
        identity = _monthly_item_identity_key_from_item(item)
        if identity[0]:
            by_identity.setdefault(identity, []).append(item)
    unique_items: list[MonthlyMenuItem] = []
    ambiguous: dict[tuple[str, str, str, str, str], list[MonthlyMenuItem]] = {}
    for identity, candidates in by_identity.items():
        if len(candidates) == 1:
            unique_items.append(candidates[0])
        else:
            ambiguous[identity] = candidates
            # Keep one object only as an identity carrier; no candidate values are projected.
            unique_items.append(candidates[0])
    payloads = _readonly_monthly_items(session, month_id, unique_items, entries, facility_id)
    for index, payload in enumerate(payloads):
        identity = _monthly_item_identity_key_from_payload(payload, payload.get("facility_override"))
        candidates = ambiguous.get(identity)
        if candidates:
            payloads[index] = _ambiguous_menu_item_payload(
                candidates[0], [str(candidate.id or "") for candidate in candidates]
            )
    return payloads


def _get_menu_direct(month_id: str, *, session=None) -> dict | None:
    if session is None:
        ensure_menu_schema()
    with (_menu_read_session(session) if session is not None else _menu_read_session()) as session:
        menu = session.get(MonthlyMenu, month_id)
        latest_upload_log = _get_latest_menu_upload_log(session, month_id)
        menu_payload = (
            serialize_menu(menu, latest_upload_log)
            if menu
            else _serialize_synthetic_menu(month_id, latest_upload_log)
        )
        items = (
            session.query(MonthlyMenuItem)
            .filter(MonthlyMenuItem.monthly_menu_id == month_id)
            .filter(or_(MonthlyMenuItem.facility_override.is_(None), MonthlyMenuItem.facility_override == ""))
            .all()
        )
        entries = (
            session.query(MonthlyMenuEntry)
            .filter(MonthlyMenuEntry.monthly_menu_id == month_id)
            .filter(or_(MonthlyMenuEntry.facility_override.is_(None), MonthlyMenuEntry.facility_override == ""))
            .order_by(MonthlyMenuEntry.menu_date, MonthlyMenuEntry.daypart, MonthlyMenuEntry.slot_index)
            .all()
        )
        if menu and not items and not entries and not latest_upload_log and not menu.month_start:
            return None
        if not menu and not items and not entries:
            return None
        payload = _readonly_monthly_items_with_scope_ambiguity(session, month_id, items, entries)
        serialized_entries = [serialize_entry(entry) for entry in entries]
        serialized_entries.sort(key=_menu_entry_sort_key)
        return {
            "menu": menu_payload,
            "items": payload,
            "entries": serialized_entries,
            "master_checks": _build_menu_master_checks(session, items, entries),
        }


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def get_menu_items_for_facility(month_id: str, facility_id: str | None, *, allow_unresolved: bool = False, session=None) -> list[dict]:
    if session is None:
        ensure_menu_schema()
    with (_menu_read_session(session) if session is not None else _menu_read_session()) as session:
        return _read_items_for_facility(session, month_id, facility_id, allow_unresolved=allow_unresolved)


def _read_items_for_facility(session, month_id: str, facility_id: str | None, *, allow_unresolved: bool = False) -> list[dict]:
    items = (
        session.query(MonthlyMenuItem)
        .filter(MonthlyMenuItem.monthly_menu_id == month_id)
        .order_by(MonthlyMenuItem.id.asc())
        .all()
    )
    scope_ids = _resolve_override_scope_ids(session, facility_id) if facility_id else []
    rank = {scope_id: idx for idx, scope_id in enumerate(scope_ids)}
    base_rank = len(scope_ids)

    if not facility_id:
        base_rows = [i for i in items if _is_blank(i.facility_override)]
        base_entries = (
            session.query(MonthlyMenuEntry)
            .filter(MonthlyMenuEntry.monthly_menu_id == month_id)
            .filter(or_(MonthlyMenuEntry.facility_override.is_(None), MonthlyMenuEntry.facility_override == ""))
            .order_by(MonthlyMenuEntry.menu_date, MonthlyMenuEntry.daypart, MonthlyMenuEntry.slot_index)
            .all()
        )
        payload = _readonly_monthly_items_with_scope_ambiguity(session, month_id, base_rows, base_entries)
        if not allow_unresolved:
            require_resolved_menu_items(payload)
        return payload

    selected: dict[tuple[str, str, str, str], tuple[int, list[MonthlyMenuItem]]] = {}
    for item in items:
        scope = (item.facility_override or "").strip()
        if scope:
            if scope not in rank:
                continue
            row_rank = rank[scope]
        else:
            row_rank = base_rank
        identity = _monthly_item_identity_key_from_item(item)
        if not identity[0]:
            continue
        key = identity[:4]
        current = selected.get(key)
        if current is None or row_rank < current[0]:
            selected[key] = (row_rank, [item])
        elif row_rank == current[0]:
            # Preserve every same-scope candidate for the readonly ambiguity projection.
            current[1].append(item)
    entry_ids = [entry["id"] for entry in _read_entries_for_facility(session, month_id, facility_id)]
    entries = session.query(MonthlyMenuEntry).filter(MonthlyMenuEntry.id.in_(entry_ids)).all() if entry_ids else []
    selected_rows = [item for _rank, candidates in selected.values() for item in candidates]
    payload = _readonly_monthly_items_with_scope_ambiguity(session, month_id, selected_rows, entries, facility_id)
    if not allow_unresolved:
        require_resolved_menu_items(payload)
    return payload


def get_menu_entries_for_facility(month_id: str, facility_id: str | None, *, session=None) -> list[dict]:
    if session is None:
        ensure_menu_schema()
    with (_menu_read_session(session) if session is not None else _menu_read_session()) as session:
        return _read_entries_for_facility(session, month_id, facility_id)


def _read_entries_for_facility(session, month_id: str, facility_id: str | None) -> list[dict]:
    entries = (
        session.query(MonthlyMenuEntry)
        .filter(MonthlyMenuEntry.monthly_menu_id == month_id)
        .order_by(
            MonthlyMenuEntry.menu_date,
            MonthlyMenuEntry.daypart,
            MonthlyMenuEntry.slot_index,
            MonthlyMenuEntry.id.asc(),
        )
        .all()
    )
    if not entries:
        return []
    if not facility_id:
        resolved = [serialize_entry(entry) for entry in entries if _is_blank(entry.facility_override)]
        resolved.sort(key=_menu_entry_sort_key)
        return resolved

    scope_ids = _resolve_override_scope_ids(session, facility_id)
    rank = {scope_id: idx for idx, scope_id in enumerate(scope_ids)}
    base_rank = len(scope_ids)
    base_entries_by_key: dict[tuple[object, object, int, str], list[MonthlyMenuEntry]] = {}
    scoped_entries_by_key: dict[tuple[object, object, int, str], list[tuple[int, MonthlyMenuEntry]]] = {}
    for entry in entries:
        scope = (entry.facility_override or "").strip()
        if scope:
            if scope not in rank:
                continue
            row_rank = rank[scope]
        else:
            row_rank = base_rank
        key = _monthly_entry_override_key(entry)
        if scope:
            scoped_entries_by_key.setdefault(key, []).append((row_rank, entry))
        else:
            base_entries_by_key.setdefault(key, []).append(entry)

    resolved_entries: list[MonthlyMenuEntry] = []
    all_keys = set(base_entries_by_key) | set(scoped_entries_by_key)
    for key in all_keys:
        base_entries = list(base_entries_by_key.get(key) or [])
        scoped_candidates = scoped_entries_by_key.get(key) or []
        if not scoped_candidates:
            resolved_entries.extend(base_entries)
            continue
        best_rank = min(row_rank for row_rank, _entry in scoped_candidates)
        scoped_entries = [
            entry
            for row_rank, entry in scoped_candidates
            if row_rank == best_rank
        ]
        if len(base_entries) > 1:
            resolved_entries.extend(base_entries)
        resolved_entries.extend(scoped_entries)
    resolved = [serialize_entry(entry) for entry in resolved_entries]
    resolved.sort(key=_menu_entry_sort_key)
    return resolved


def get_menu_for_facility(month_id: str, facility_id: str | None, *, allow_unresolved: bool = False, session=None) -> dict | None:
    session_kwargs = {"session": session} if session is not None else {}
    payload = _get_menu_for_facility_direct(month_id, facility_id, allow_unresolved=allow_unresolved, **session_kwargs)
    if payload is not None:
        return _normalize_menu_payload_items(payload)
    return None


def get_menu(month_id: str, *, allow_unresolved: bool = False, session=None) -> dict | None:
    payload = _get_menu_direct(month_id, session=session) if session is not None else _get_menu_direct(month_id)
    if payload is not None:
        if not allow_unresolved:
            require_resolved_menu_items(payload["items"])
        return _normalize_menu_payload_items(payload)
    return None


def list_recent_menus(limit: int = 12) -> list[dict]:
    ensure_menu_schema()
    normalized_limit = max(1, min(int(limit or 12), 36))
    with _menu_read_session() as session:
        rows = (
            session.query(MonthlyMenu)
            .order_by(MonthlyMenu.month_start.desc().nullslast(), MonthlyMenu.id.desc())
            .limit(normalized_limit * 3)
            .all()
        )
        results: list[dict] = []
        for row in rows:
            latest_upload_log = _get_latest_menu_upload_log(session, row.id)
            has_base_item = (
                session.query(MonthlyMenuItem.id)
                .filter(MonthlyMenuItem.monthly_menu_id == row.id)
                .filter(or_(MonthlyMenuItem.facility_override.is_(None), MonthlyMenuItem.facility_override == ""))
                .first()
                is not None
            )
            has_base_entry = (
                session.query(MonthlyMenuEntry.id)
                .filter(MonthlyMenuEntry.monthly_menu_id == row.id)
                .filter(or_(MonthlyMenuEntry.facility_override.is_(None), MonthlyMenuEntry.facility_override == ""))
                .first()
                is not None
            )
            if not latest_upload_log and not has_base_item and not has_base_entry and not row.month_start:
                continue
            results.append(serialize_menu(row, latest_upload_log))
            if len(results) >= normalized_limit:
                break
        return results


def get_latest_menu(*, allow_unresolved: bool = False) -> dict | None:
    ensure_menu_schema()
    with _menu_read_session() as session:
        menus = session.query(MonthlyMenu).all()
        if not menus:
            return None
        latest = max(
            menus,
            key=lambda row: (
                row.month_start.isoformat() if row.month_start else "",
                str(row.id or ""),
            ),
        )
        latest_upload_log = _get_latest_menu_upload_log(session, latest.id)
        items = session.query(MonthlyMenuItem).filter(MonthlyMenuItem.monthly_menu_id == latest.id).all()
        entries = (
            session.query(MonthlyMenuEntry)
            .filter(MonthlyMenuEntry.monthly_menu_id == latest.id)
            .order_by(MonthlyMenuEntry.menu_date, MonthlyMenuEntry.daypart, MonthlyMenuEntry.slot_index)
            .all()
        )
        payload = _readonly_monthly_items_with_scope_ambiguity(session, latest.id, items, entries)
        if not allow_unresolved:
            require_resolved_menu_items(payload)
        serialized_entries = [serialize_entry(entry) for entry in entries]
        serialized_entries.sort(key=_menu_entry_sort_key)
        return _normalize_menu_payload_items({
            "menu": serialize_menu(latest, latest_upload_log),
            "items": payload,
            "entries": serialized_entries,
            "master_checks": _build_menu_master_checks(session, items, entries),
        })


def get_menu_items(month_id: str, *, allow_unresolved: bool = False) -> list[dict]:
    ensure_menu_schema()
    with _menu_read_session() as session:
        items = session.query(MonthlyMenuItem).filter(MonthlyMenuItem.monthly_menu_id == month_id).all()
        entries = (
            session.query(MonthlyMenuEntry)
            .filter(MonthlyMenuEntry.monthly_menu_id == month_id)
            .order_by(MonthlyMenuEntry.menu_date, MonthlyMenuEntry.daypart, MonthlyMenuEntry.slot_index)
            .all()
        )
        payload = _readonly_monthly_items_with_scope_ambiguity(session, month_id, items, entries)
        if not allow_unresolved:
            require_resolved_menu_items(payload)
        return payload


def get_item(item_id: str) -> dict | None:
    ensure_menu_schema()
    with _menu_read_session() as session:
        item = session.get(MonthlyMenuItem, item_id)
        if not item:
            return None
        # GET must use the same explicit master-FK projection as monthly readonly views.
        return _readonly_monthly_items(
            session, item.monthly_menu_id, [item], [], item.facility_override
        )[0]


def resolve_menu_master_check(month_id: str, item_id: str, body: dict, *, _session=None) -> dict | None:
    if _session is None:
        ensure_menu_schema()
    with (nullcontext(_session) if _session is not None else session_scope()) as session:
        item = session.get(MonthlyMenuItem, item_id)
        if not item or item.monthly_menu_id != month_id:
            return None
        entries = (
            session.query(MonthlyMenuEntry)
            .filter(MonthlyMenuEntry.monthly_menu_id == month_id)
            .order_by(MonthlyMenuEntry.menu_date, MonthlyMenuEntry.daypart, MonthlyMenuEntry.slot_index)
            .all()
        )
        patch = _derive_monthly_item_patch(item, entries)
        action = str(body.get("action") or "").strip().lower()
        if action == "existing":
            menu_master_id = str(body.get("menu_master_id") or "").strip()
            master = session.get(MenuMaster, menu_master_id) if menu_master_id else None
            if not master:
                raise ValueError("menu master not found")
            item.menu_master_id = master.id
            item.master_resolution_mode = None
            return {"resolved": True, "mode": "existing", "menu_master_id": master.id}
        if action == "create":
            create_name = str(body.get("name") or item.name or "").strip()
            create_patch = dict(patch)
            create_patch.update(
                _extract_master_patch(
                    body,
                    _MASTER_FIELDS,
                )
            )
            if _is_blank_value(create_name):
                raise ValueError("name is required")
            if _is_blank_value(create_patch.get("unit_type")) or create_patch.get("qty_per_serving") is None:
                raise ValueError("unit_type and qty_per_serving are required")
            if _bagging_settings_missing(create_patch):
                raise ValueError("bag_max_qty and bag_max_unit are required for bagging units")
            master = _get_or_create_menu_master_without_rename(session, create_name, seed_fields=create_patch)
            if not master:
                raise ValueError("failed to create menu master")
            item.menu_master_id = master.id
            item.master_resolution_mode = None
            return {"resolved": True, "mode": "create", "menu_master_id": master.id}
        if action == "update":
            menu_master_id = str(body.get("menu_master_id") or item.menu_master_id or "").strip()
            master = session.get(MenuMaster, menu_master_id) if menu_master_id else None
            if master is None:
                master = _find_menu_master_by_normalized(session, _normalize_menu_name(item.name or ""))
            if not master:
                raise ValueError("menu master not found")
            update_body = _build_menu_master_update_body(item.name or "", patch, master)
            update_body.update(
                _extract_master_patch(
                    body,
                    _MASTER_FIELDS,
                )
            )
            if "name" in body and not _is_blank_value(body.get("name")):
                update_body["name"] = str(body.get("name") or "").strip()
            merged_for_validation = serialize_menu_master(master)
            merged_for_validation.update(update_body)
            if _bagging_settings_missing(merged_for_validation):
                raise ValueError("bag_max_qty and bag_max_unit are required for bagging units")
            _update_menu_master_in_session(session, master, update_body)
            item.menu_master_id = master.id
            item.master_resolution_mode = None
            return {"resolved": True, "mode": "update", "menu_master_id": master.id}
        if action == "month_only":
            menu_master_id = str(body.get("menu_master_id") or item.menu_master_id or "").strip()
            master = session.get(MenuMaster, menu_master_id) if menu_master_id else None
            if master is None:
                master = _find_menu_master_by_normalized(session, _normalize_menu_name(item.name or ""))
            item_patch = dict(patch)
            item_patch.update(
                _extract_master_patch(
                    body,
                    _MASTER_FIELDS,
                    allow_null=True,
                )
            )
            if _is_blank_value(item_patch.get("qty_per_serving")):
                fallback_qty = patch.get("qty_per_serving")
                if _is_blank_value(fallback_qty) and master is not None:
                    fallback_qty = getattr(master, "qty_per_serving", None)
                if not _is_blank_value(fallback_qty):
                    item_patch["qty_per_serving"] = fallback_qty
            if _bagging_settings_missing(item_patch):
                raise ValueError("bag_max_qty and bag_max_unit are required for bagging units")
            _apply_monthly_item_patch_in_session(item, item_patch)
            item.master_resolution_mode = "month_only"
            return {
                "resolved": True,
                "mode": "month_only",
                "menu_master_id": item.menu_master_id,
            }
        if action == "category_only":
            menu_master_id = str(body.get("menu_master_id") or item.menu_master_id or "").strip()
            master = session.get(MenuMaster, menu_master_id) if menu_master_id else None
            if master is None:
                master = _find_menu_master_by_normalized(session, _normalize_menu_name(item.name or ""))
            if not master:
                raise ValueError("menu master not found")
            category = body.get("category")
            if _is_blank_value(category):
                category = patch.get("category")
            if _is_blank_value(category):
                raise ValueError("category is required")
            _update_menu_master_in_session(session, master, {"category": category})
            item.menu_master_id = master.id
            item.master_resolution_mode = None
            return {"resolved": True, "mode": "category_only", "menu_master_id": master.id}
        raise ValueError("unknown action")


def resolve_menu_defaults(names: list[str], facility_id: str | None = None, *, session=None) -> dict[str, dict]:
    if session is not None:
        return resolve_menu_defaults_in_session(session, names, facility_id)[0]
    ensure_menu_schema()
    with session_scope() as session:
        defaults = _build_master_defaults(session, names, facility_id)
    resolved: dict[str, dict] = {}
    for name in names:
        normalized = _normalize_menu_name(name or "")
        resolved[name] = defaults.get(normalized, {})
    return resolved


def resolve_menu_defaults_in_session(session, names: list[str], facility_id: str | None = None, *, lock_sources: bool = False) -> tuple[dict[str, dict], list[tuple[str, int]]]:
    """Resolve the existing canonical defaults and their exact locked sources together."""
    sources: list[tuple[str, int]] = []
    with _menu_read_session(session):
        defaults, _ = _build_master_defaults_index(session, names, None, facility_id, lock_sources=lock_sources, source_revisions=sources)
    return ({name: defaults.get(_normalize_menu_name(name or ""), {}) for name in names}, sorted(sources))


def import_condiments(file_bytes: bytes, filename: str, sheet_name: str | None = None) -> dict:
    ensure_menu_schema()
    if filename.lower().endswith(".csv"):
        raise ValueError("condiment import does not support csv")
    sheet = sheet_name or "主菜"
    try:
        workbook = pd.ExcelFile(BytesIO(file_bytes))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid menu file") from exc
    if sheet not in workbook.sheet_names:
        raise ValueError(f"sheet not found: {sheet}")
    df = pd.read_excel(workbook, sheet_name=sheet, header=None)
    if df.empty:
        raise ValueError("menu file is empty")
    # Locate header row by scanning for condiment labels
    header_row = None
    for idx in range(min(len(df), 20)):
        row_values = [str(v).strip() if v is not None else "" for v in df.iloc[idx].tolist()]
        if any(value in _CONDIMENT_HEADERS for value in row_values):
            header_row = idx
            break
    if header_row is None:
        raise ValueError("condiment header not found")

    condiment_columns: dict[int, str] = {}
    header_values = df.iloc[header_row].tolist()
    for col_idx, raw in enumerate(header_values):
        value = str(raw).strip() if raw is not None else ""
        if value in _CONDIMENT_HEADERS:
            condiment_columns[col_idx] = _CONDIMENT_HEADERS[value]
    if not condiment_columns:
        raise ValueError("condiment columns not found")

    condiment_map: dict[str, set[str]] = {}
    for row_idx in range(header_row + 1, len(df)):
        row = df.iloc[row_idx].tolist()
        for col_idx, label in condiment_columns.items():
            flag = row[col_idx] if col_idx < len(row) else None
            if str(flag).strip() != "○":
                continue
            menu_idx = None
            for left in range(col_idx - 1, -1, -1):
                candidate = _normalize_menu_value(row[left]) if left < len(row) else ""
                if candidate:
                    menu_idx = left
                    break
            if menu_idx is None:
                continue
            name = _normalize_menu_value(row[menu_idx])
            if not name:
                continue
            condiment_map.setdefault(name, set()).add(label)

    if not condiment_map:
        return {"updated": 0, "created": 0, "items": 0}

    updated = 0
    created = 0
    with session_scope() as session:
        for name, labels in condiment_map.items():
            normalized = _normalize_menu_name(name)
            master = (
                session.execute(select(MenuMaster).where(MenuMaster.normalized_name == normalized))
                .scalars()
                .first()
            )
            if not master:
                master = MenuMaster(
                    id=f"MNU{uuid4().hex[:8]}",
                    name=name,
                    normalized_name=normalized,
                )
                session.add(master)
                created += 1
            else:
                master.name = master.name or name
                updated += 1
            master.condiments = sorted(labels)
    return {"updated": updated, "created": created, "items": len(condiment_map)}
