from io import BytesIO
from datetime import date, datetime
from collections import Counter
import re
import unicodedata
import threading
from loguru import logger
from uuid import uuid4
import pandas as pd

from sqlalchemy import delete, select, inspect, text, or_

from src.db import session_scope, engine
from src.models.menu import (
    MonthlyMenu,
    MonthlyMenuItem,
    MonthlyMenuEntry,
    MenuMaster,
    MenuFacilityOverride,
)
from src.models.facility import FacilityConfig
from src.services.notification_service import record_event
from src.services import menu_rule_service


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


def _ensure_menu_master_condiments() -> bool:
    inspector = inspect(engine)
    if "menu_masters" not in inspector.get_table_names():
        return False
    columns = {col.get("name") for col in inspector.get_columns("menu_masters")}
    if "condiments" in columns:
        return True
    migrated = False
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE menu_masters ADD COLUMN condiments JSON"))
            migrated = True
        except Exception as exc:
            logger.warning("Failed to ensure menu_masters.condiments", error=str(exc))
    if migrated:
        inspector = inspect(engine)
        columns = {col.get("name") for col in inspector.get_columns("menu_masters")}
    return "condiments" in columns


def _ensure_monthly_menu_items_menu_master_id() -> bool:
    inspector = inspect(engine)
    if "monthly_menu_items" not in inspector.get_table_names():
        return False
    columns = {col.get("name") for col in inspector.get_columns("monthly_menu_items")}
    if "menu_master_id" in columns:
        return True
    migrated = False
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE monthly_menu_items ADD COLUMN menu_master_id VARCHAR"))
            migrated = True
        except Exception as exc:
            logger.warning("Failed to ensure monthly_menu_items.menu_master_id", error=str(exc))
    if migrated:
        inspector = inspect(engine)
        columns = {col.get("name") for col in inspector.get_columns("monthly_menu_items")}
    return "menu_master_id" in columns


def _ensure_menu_unique_indexes() -> bool:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    required_tables_present = {"menu_facility_overrides", "monthly_menu_items"}.issubset(tables)
    with engine.begin() as conn:
        if "menu_facility_overrides" in tables:
            try:
                conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_menu_facility_overrides_master_facility
                        ON menu_facility_overrides(menu_master_id, facility_id)
                        """
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Failed to ensure unique index for menu_facility_overrides",
                    error=str(exc),
                )
        if "monthly_menu_items" in tables:
            try:
                conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_monthly_menu_items_scope_name
                        ON monthly_menu_items(monthly_menu_id, name, COALESCE(facility_override, ''))
                        """
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Failed to ensure unique index for monthly_menu_items",
                    error=str(exc),
                )
    return required_tables_present


def ensure_menu_schema() -> None:
    global _MENU_SCHEMA_INITIALIZED
    if _MENU_SCHEMA_INITIALIZED:
        return
    with _MENU_SCHEMA_LOCK:
        if _MENU_SCHEMA_INITIALIZED:
            return
        condiments_ok = _ensure_menu_master_condiments()
        monthly_item_ok = _ensure_monthly_menu_items_menu_master_id()
        indexes_ok = _ensure_menu_unique_indexes()
        # Only memoize success when the expected tables/columns are actually present.
        _MENU_SCHEMA_INITIALIZED = bool(condiments_ok and monthly_item_ok and indexes_ok)

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
    "蒸",
)


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


def _normalize_diet_type(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    compact = text.replace(" ", "")
    diet = None
    if "軟菜" in compact:
        diet = "soft"
    elif "ミキサー" in compact:
        diet = "mixer"
    elif "常食" in compact:
        diet = "regular"
    if "1600" in compact or "１６００" in compact:
        if diet:
            return f"{diet}_1600kcal"
        return "1600kcal"
    return diet or text


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
    type_weight = {"global": 100, "menu": 200}
    enriched: list[dict] = []
    for item in items:
        updated = dict(item)
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
        if not updated.get("unit_type"):
            updated["unit_type"] = _infer_unit_type(updated.get("name"))
        if not updated.get("temp_type"):
            updated["temp_type"] = _infer_temp_type(updated.get("name"))
        enriched.append(updated)
    return enriched


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
                diet_type = _normalize_diet_type(value)
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
    name_meta: dict[str, dict[str, Counter]] = {}
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
        date_count = sum(1 for number in day_numbers if number is not None)
        if date_count >= 3:
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
            meta = name_meta.setdefault(
                name,
                {"daypart": Counter(), "category": Counter(), "diet_type": Counter()},
            )
            if current_daypart:
                meta["daypart"][current_daypart] += 1
            if category:
                meta["category"][category] += 1
            if diet_type:
                meta["diet_type"][diet_type] += 1

    items: list[dict] = []
    for name, meta in name_meta.items():
        if _is_skip_menu_name(name):
            continue
        item_payload = {"name": name}
        if meta["daypart"]:
            item_payload["daypart"] = meta["daypart"].most_common(1)[0][0]
        if meta["category"]:
            item_payload["category"] = meta["category"].most_common(1)[0][0]
        if meta["diet_type"]:
            item_payload["diet_type"] = meta["diet_type"].most_common(1)[0][0]
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
        master = (
            session.execute(select(MenuMaster).where(MenuMaster.normalized_name == normalized))
            .scalars()
            .first()
        )
    if master is None:
        master = MenuMaster(id=f"MNU{uuid4().hex[:8]}", name=text_name, normalized_name=normalized)
        session.add(master)
    else:
        master.name = text_name

    if seed_fields:
        for field, raw in seed_fields.items():
            if field not in _MASTER_FIELDS:
                continue
            value = _coerce_master_field_value(field, raw)
            if value is _INVALID_PATCH_VALUE or _is_blank_value(value):
                continue
            current = getattr(master, field, None)
            if _is_blank_value(current):
                setattr(master, field, value)
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
        override = overrides.get(master.id) if facility_id else None
        payload: dict[str, object] = {}
        for field in _MASTER_FIELDS:
            value = None
            if override is not None:
                override_value = getattr(override, field)
                if not _is_blank_value(override_value):
                    value = override_value
            if value is None:
                master_value = getattr(master, field)
                if not _is_blank_value(master_value):
                    value = master_value
            if value is not None:
                payload[field] = value
        if master.condiments:
            payload["condiments"] = list(master.condiments)
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
    names = [item.get("name") or "" for item in items]
    master_ids = [item.get("menu_master_id") or "" for item in items]
    with session_scope() as session:
        defaults_by_name, defaults_by_id = _build_master_defaults_index(session, names, master_ids, facility_id)
    if not defaults_by_name and not defaults_by_id:
        return items
    merged: list[dict] = []
    for item in items:
        payload = dict(item)
        defaults_for_item = None
        menu_master_id = str(item.get("menu_master_id") or "").strip()
        if menu_master_id:
            defaults_for_item = defaults_by_id.get(menu_master_id)
        if defaults_for_item is None:
            normalized = _normalize_menu_name(item.get("name") or "")
            defaults_for_item = defaults_by_name.get(normalized)
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


def create_menu(month_id: str, file_bytes: bytes, filename: str, sheet_name: str | None = None):
    ensure_menu_schema()
    # Seed default rules outside the write transaction to avoid sqlite write-lock contention
    # from nested session_scope calls.
    menu_rule_service.ensure_default_rules()
    parsed_items: list[dict] = []
    entries: list[dict] = []
    month_start = None
    try:
        month_start, _, parsed_items, entries = _parse_monthly_menu(
            file_bytes,
            filename,
            sheet_name,
            month_id,
        )
    except ValueError:
        parsed_items = []
        entries = []
    if parsed_items:
        names = [item["name"] for item in parsed_items]
    else:
        names = _extract_menu_names(file_bytes, filename, sheet_name)
    with session_scope() as session:
        replaced = False
        menu = session.get(MonthlyMenu, month_id)
        if menu:
            replaced = True
            menu.filename = filename
            if month_start:
                menu.month_start = month_start
            session.execute(delete(MonthlyMenuItem).where(MonthlyMenuItem.monthly_menu_id == month_id))
            session.execute(delete(MonthlyMenuEntry).where(MonthlyMenuEntry.monthly_menu_id == month_id))
        else:
            menu = MonthlyMenu(id=month_id, filename=filename, month_start=month_start)
            session.add(menu)
            session.flush()
            session.refresh(menu)

        parsed_items = _apply_rules_to_items(parsed_items)
        parsed_meta = {item.get("name"): item for item in parsed_items}
        for name in names:
            meta = parsed_meta.get(name, {})
            item_id = f"MMI{uuid4().hex[:6]}"
            seed_patch = _extract_master_patch(meta, ("unit_type", "qty_per_serving", "temp_type", "daypart", "category"))
            master = _ensure_menu_master(session, name, seed_fields=seed_patch)
            session.add(
                MonthlyMenuItem(
                    id=item_id,
                    monthly_menu_id=month_id,
                    menu_master_id=master.id if master else None,
                    name=name,
                    diet_type=meta.get("diet_type"),
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
                    diet_type=entry.get("diet_type"),
                    slot_index=entry.get("slot_index"),
                )
            )
        item_count = len(names)
        logger.info(
            "Menu uploaded",
            month_id=month_id,
            filename=filename,
            replaced=replaced,
            item_count=item_count,
        )
        payload = serialize_menu(menu)
    record_event(
        "menu_upload",
        actor="system",
        target=month_id,
        wek=month_id,
        metadata={"filename": filename, "replaced": replaced, "item_count": item_count},
    )
    return payload, replaced, item_count


def update_item_status(month_id: str, item_id: str, body: dict) -> str:
    ensure_menu_schema()
    event_fields: list[str] | None = None
    with session_scope() as session:
        item = session.get(MonthlyMenuItem, item_id)
        if not item:
            return "not_found"
        if item.monthly_menu_id != month_id:
            return "month_mismatch"
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
            item.diet_type = body.get("diet_type")
        name = (item.name or "").strip()
        if not name:
            return "invalid_name"
        conflict = _find_existing_monthly_item(
            session,
            month_id,
            name,
            item.facility_override,
            exclude_id=item.id,
        )
        if conflict:
            return "conflict"

        master_patch = _extract_master_patch(body, allow_null=True)
        master = _ensure_menu_master(session, name, item.menu_master_id)
        if master:
            item.menu_master_id = master.id
        _upsert_menu_master(session, item, master_patch, master)
        for field in _MASTER_FIELDS:
            setattr(item, field, None)
        logger.info("Menu item updated", month_id=month_id, item_id=item_id)
        event_fields = list(body.keys())
    record_event(
        "menu_edit",
        actor="system",
        target=item_id,
        wek=month_id,
        metadata={"fields": event_fields or []},
    )
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
    exclude_id: str | None = None,
) -> MonthlyMenuItem | None:
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
    if exclude_id:
        query = query.where(MonthlyMenuItem.id != exclude_id)
    query = query.order_by(MonthlyMenuItem.id.asc())
    return session.execute(query).scalars().first()


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


def serialize_menu(menu: MonthlyMenu):
    return {"id": menu.id, "filename": menu.filename}


def serialize_menu_master(master: MenuMaster) -> dict:
    return {
        "id": master.id,
        "name": master.name,
        "normalized_name": master.normalized_name,
        "unit_type": master.unit_type,
        "qty_per_serving": master.qty_per_serving,
        "bag_max_qty": master.bag_max_qty,
        "bag_max_unit": master.bag_max_unit,
        "temp_type": master.temp_type,
        "daypart": master.daypart,
        "category": master.category,
        "condiments": list(master.condiments or []),
    }


def list_menu_masters(query: str | None = None, limit: int = 1000) -> list[dict]:
    ensure_menu_schema()
    normalized_limit = max(1, min(int(limit or 1000), 5000))
    with session_scope() as session:
        stmt = select(MenuMaster)
        if query and query.strip():
            q = f"%{query.strip()}%"
            stmt = stmt.where(MenuMaster.name.ilike(q))
        stmt = stmt.order_by(MenuMaster.name).limit(normalized_limit)
        rows = session.execute(stmt).scalars().all()
        return [serialize_menu_master(row) for row in rows]


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
            unit_type=body.get("unit_type"),
            qty_per_serving=_coerce_float(body.get("qty_per_serving")),
            bag_max_qty=_coerce_float(body.get("bag_max_qty")),
            bag_max_unit=body.get("bag_max_unit"),
            temp_type=body.get("temp_type"),
            daypart=body.get("daypart"),
            category=body.get("category"),
            condiments=body.get("condiments") if isinstance(body.get("condiments"), list) else [],
        )
        session.add(master)
        session.flush()
        session.refresh(master)
        return serialize_menu_master(master)


def update_menu_master(master_id: str, body: dict) -> bool:
    ensure_menu_schema()
    if not master_id:
        return False
    with session_scope() as session:
        master = session.get(MenuMaster, master_id)
        if not master:
            return False
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
            master.unit_type = body.get("unit_type") or None
        if "qty_per_serving" in body:
            master.qty_per_serving = _coerce_float(body.get("qty_per_serving"))
        if "bag_max_qty" in body:
            master.bag_max_qty = _coerce_float(body.get("bag_max_qty"))
        if "bag_max_unit" in body:
            master.bag_max_unit = body.get("bag_max_unit") or None
        if "temp_type" in body:
            master.temp_type = body.get("temp_type") or None
        if "daypart" in body:
            master.daypart = body.get("daypart") or None
        if "category" in body:
            master.category = body.get("category") or None
        if "condiments" in body:
            raw = body.get("condiments")
            if isinstance(raw, list):
                master.condiments = [str(item).strip() for item in raw if str(item).strip()]
            elif raw is None or raw == "":
                master.condiments = []
        return True


def serialize_item(item: MonthlyMenuItem):
    return {
        "id": item.id,
        "month_id": item.monthly_menu_id,
        "menu_master_id": item.menu_master_id,
        "name": item.name,
        "unit_type": item.unit_type,
        "qty_per_serving": item.qty_per_serving,
        "temp_type": item.temp_type,
        "daypart": item.daypart,
        "category": item.category,
        "diet_type": getattr(item, "diet_type", None),
        "facility_override": item.facility_override,
        "bag_max_qty": None,
        "bag_max_unit": None,
    }


def serialize_entry(entry: MonthlyMenuEntry) -> dict:
    return {
        "id": entry.id,
        "month_id": entry.monthly_menu_id,
        "menu_date": entry.menu_date.isoformat() if entry.menu_date else None,
        "daypart": entry.daypart,
        "name": entry.name,
        "category": entry.category,
        "diet_type": entry.diet_type,
        "slot_index": entry.slot_index,
    }


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _pick_latest_item_by_name(items: list[MonthlyMenuItem]) -> dict[str, MonthlyMenuItem]:
    selected: dict[str, MonthlyMenuItem] = {}
    ranked = sorted(
        items,
        key=lambda item: (
            1 if item.menu_master_id else 0,
            item.id or "",
        ),
    )
    for item in ranked:
        name = (item.name or "").strip()
        if not name:
            continue
        selected[name] = item
    return selected


def get_menu_items_for_facility(month_id: str, facility_id: str | None) -> list[dict]:
    ensure_menu_schema()
    with session_scope() as session:
        items = (
            session.query(MonthlyMenuItem)
            .filter(MonthlyMenuItem.monthly_menu_id == month_id)
            .order_by(MonthlyMenuItem.id.asc())
            .all()
        )
        if not items:
            return []

        if not facility_id:
            base_rows = [i for i in items if _is_blank(i.facility_override)]
            base_items = [serialize_item(i) for i in _pick_latest_item_by_name(base_rows).values()]
            return _merge_master_defaults(base_items, None)

        base_rows = [i for i in items if _is_blank(i.facility_override)]
        override_rows = [i for i in items if (i.facility_override or "").strip() == facility_id]
        base = _pick_latest_item_by_name(base_rows)
        overrides = _pick_latest_item_by_name(override_rows)
        merged = {**base, **overrides}
        return _merge_master_defaults([serialize_item(i) for i in merged.values()], facility_id)


def get_menu(month_id: str) -> dict | None:
    ensure_menu_schema()
    with session_scope() as session:
        menu = session.get(MonthlyMenu, month_id)
        if not menu:
            return None
        items = session.query(MonthlyMenuItem).filter(MonthlyMenuItem.monthly_menu_id == month_id).all()
        entries = (
            session.query(MonthlyMenuEntry)
            .filter(MonthlyMenuEntry.monthly_menu_id == month_id)
            .order_by(MonthlyMenuEntry.menu_date, MonthlyMenuEntry.daypart, MonthlyMenuEntry.slot_index)
            .all()
        )
        payload = [serialize_item(i) for i in items]
        return {
            "menu": serialize_menu(menu),
            "items": _merge_master_defaults(payload, None),
            "entries": [serialize_entry(entry) for entry in entries],
        }


def get_menu_items(month_id: str) -> list[dict]:
    ensure_menu_schema()
    with session_scope() as session:
        items = session.query(MonthlyMenuItem).filter(MonthlyMenuItem.monthly_menu_id == month_id).all()
        return _merge_master_defaults([serialize_item(i) for i in items], None)


def get_item(item_id: str) -> dict | None:
    ensure_menu_schema()
    with session_scope() as session:
        item = session.get(MonthlyMenuItem, item_id)
        if not item:
            return None
        merged = _merge_master_defaults([serialize_item(item)], item.facility_override)
        return merged[0] if merged else serialize_item(item)


def resolve_menu_defaults(names: list[str], facility_id: str | None = None) -> dict[str, dict]:
    ensure_menu_schema()
    with session_scope() as session:
        defaults = _build_master_defaults(session, names, facility_id)
    resolved: dict[str, dict] = {}
    for name in names:
        normalized = _normalize_menu_name(name or "")
        resolved[name] = defaults.get(normalized, {})
    return resolved


def _find_condiment_header_row(ws, max_scan: int = 20) -> int | None:
    for row in range(1, max_scan + 1):
        for col in range(1, ws.max_column + 1):
            value = ws.cell(row=row, column=col).value
            if value in _CONDIMENT_HEADERS:
                return row
    return None


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
