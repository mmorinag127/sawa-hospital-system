from io import BytesIO
from datetime import date, datetime
from collections import Counter
import re
from loguru import logger
from uuid import uuid4
import pandas as pd

from sqlalchemy import delete, select

from src.db import session_scope
from src.models.menu import (
    MonthlyMenu,
    MonthlyMenuItem,
    MonthlyMenuEntry,
    MenuMaster,
    MenuFacilityOverride,
)
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


def _resolve_menu_name_column(columns: list[str]) -> str:
    for candidate in ["menu", "メニュー", "品名", "商品名", "料理名", "献立"]:
        for col in columns:
            if candidate.lower() in col.lower():
                return col
    return columns[0] if columns else "menu"


def _normalize_menu_name(value: str) -> str:
    if not value:
        return ""
    normalized = value.translate(_MENU_TRANSLATION)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.replace("・", "").replace("／", "/")
    return normalized.strip().lower()


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
    normalized = value.replace(" ", "")
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
    if not rules:
        return items
    type_weight = {"global": 100, "menu": 200}
    enriched: list[dict] = []
    for item in items:
        matches = [rule for rule in rules if _rule_applies_to_item(rule, item)]
        if not matches:
            enriched.append(item)
            continue
        selected = max(
            matches,
            key=lambda rule: type_weight.get(rule.get("rule_type"), 0) + int(rule.get("priority") or 0),
        )
        updated = dict(item)
        if not updated.get("unit_type") and selected.get("unit_type"):
            updated["unit_type"] = selected.get("unit_type")
        if updated.get("qty_per_serving") is None and selected.get("qty_per_serving") is not None:
            updated["qty_per_serving"] = selected.get("qty_per_serving")
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


def _build_master_defaults(
    session,
    names: list[str],
    facility_id: str | None = None,
) -> dict[str, dict]:
    normalized_names = {_normalize_menu_name(name): name for name in names if name}
    normalized_keys = [key for key in normalized_names.keys() if key]
    if not normalized_keys:
        return {}
    masters = (
        session.execute(
            select(MenuMaster).where(MenuMaster.normalized_name.in_(normalized_keys))
        )
        .scalars()
        .all()
    )
    master_by_norm = {master.normalized_name: master for master in masters}
    overrides: dict[str, MenuFacilityOverride] = {}
    if facility_id and masters:
        master_ids = [master.id for master in masters]
        override_rows = (
            session.execute(
                select(MenuFacilityOverride)
                .where(MenuFacilityOverride.facility_id == facility_id)
                .where(MenuFacilityOverride.menu_master_id.in_(master_ids))
            )
            .scalars()
            .all()
        )
        overrides = {row.menu_master_id: row for row in override_rows}

    defaults: dict[str, dict] = {}
    for normalized in normalized_keys:
        master = master_by_norm.get(normalized)
        if not master:
            continue
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
        defaults[normalized] = payload
    return defaults


def _merge_master_defaults(items: list[dict], facility_id: str | None = None) -> list[dict]:
    names = [item.get("name") or "" for item in items]
    with session_scope() as session:
        defaults = _build_master_defaults(session, names, facility_id)
    if not defaults:
        return items
    merged: list[dict] = []
    for item in items:
        normalized = _normalize_menu_name(item.get("name") or "")
        payload = dict(item)
        defaults_for_item = defaults.get(normalized)
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
        defaults = _build_master_defaults(session, names)
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

        menu_rule_service.ensure_default_rules()
        parsed_items = _apply_rules_to_items(parsed_items)
        parsed_meta = {item.get("name"): item for item in parsed_items}
        for name in names:
            default = defaults.get(_normalize_menu_name(name), {})
            meta = parsed_meta.get(name, {})
            item_id = f"MMI{uuid4().hex[:6]}"
            unit_type = meta.get("unit_type") or default.get("unit_type")
            qty_per_serving = (
                meta["qty_per_serving"] if meta.get("qty_per_serving") is not None else default.get("qty_per_serving")
            )
            session.add(
                MonthlyMenuItem(
                    id=item_id,
                    monthly_menu_id=month_id,
                    name=name,
                    unit_type=unit_type,
                    qty_per_serving=qty_per_serving,
                    temp_type=default.get("temp_type"),
                    daypart=meta.get("daypart") or default.get("daypart"),
                    category=meta.get("category") or default.get("category"),
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
        record_event(
            "menu_upload",
            actor="system",
            target=month_id,
            wek=month_id,
            metadata={"filename": filename, "replaced": replaced, "item_count": item_count},
        )
        return serialize_menu(menu), replaced, item_count


def update_item(month_id: str, item_id: str, body: dict) -> bool:
    with session_scope() as session:
        item = session.get(MonthlyMenuItem, item_id)
        if not item:
            return False
        for key in [
            "unit_type",
            "qty_per_serving",
            "temp_type",
            "daypart",
            "category",
            "facility_override",
            "name",
            "diet_type",
        ]:
            if key in body:
                setattr(item, key, body[key])
        master_patch = {}
        if "bag_max_qty" in body:
            master_patch["bag_max_qty"] = _coerce_float(body.get("bag_max_qty"))
        if "bag_max_unit" in body:
            raw_unit = body.get("bag_max_unit")
            master_patch["bag_max_unit"] = raw_unit if not _is_blank_value(raw_unit) else None
        _upsert_menu_master(session, item, master_patch)
        logger.info("Menu item updated", month_id=month_id, item_id=item_id)
        record_event(
            "menu_edit",
            actor="system",
            target=item_id,
            wek=month_id,
            metadata={"fields": list(body.keys())},
        )
        return True


def _upsert_menu_master(session, item: MonthlyMenuItem, master_patch: dict | None = None) -> None:
    name = (item.name or "").strip()
    if not name:
        return
    normalized = _normalize_menu_name(name)
    master = (
        session.execute(select(MenuMaster).where(MenuMaster.normalized_name == normalized))
        .scalars()
        .first()
    )
    if not master:
        master = MenuMaster(id=f"MNU{uuid4().hex[:8]}", name=name, normalized_name=normalized)
        session.add(master)
    else:
        master.name = name

    facility_id = (item.facility_override or "").strip()
    if not facility_id:
        _apply_master_fields(master, item, master_patch)
    else:
        override = (
            session.execute(
                select(MenuFacilityOverride)
                .where(MenuFacilityOverride.menu_master_id == master.id)
                .where(MenuFacilityOverride.facility_id == facility_id)
            )
            .scalars()
            .first()
        )
        if not override:
            override = MenuFacilityOverride(
                id=f"MFO{uuid4().hex[:8]}",
                menu_master_id=master.id,
                facility_id=facility_id,
            )
            session.add(override)
        _apply_master_fields(override, item, master_patch)


def _apply_master_fields(target, item: MonthlyMenuItem, master_patch: dict | None) -> None:
    for field in _MASTER_FIELDS:
        if master_patch is not None and field in master_patch:
            value = master_patch[field]
        else:
            value = getattr(item, field, None)
        if _is_blank_value(value):
            continue
        setattr(target, field, value)


def create_item_stub(month_id: str, name: str):
    with session_scope() as session:
        item_id = f"MMI{uuid4().hex[:6]}"
        item = MonthlyMenuItem(id=item_id, monthly_menu_id=month_id, name=name)
        session.add(item)
        session.flush()
        session.refresh(item)
        return serialize_item(item)


def serialize_menu(menu: MonthlyMenu):
    return {"id": menu.id, "filename": menu.filename}


def serialize_item(item: MonthlyMenuItem):
    return {
        "id": item.id,
        "month_id": item.monthly_menu_id,
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


def get_menu_items_for_facility(month_id: str, facility_id: str | None) -> list[dict]:
    with session_scope() as session:
        items = session.query(MonthlyMenuItem).filter(MonthlyMenuItem.monthly_menu_id == month_id).all()
        if not items:
            return []

        if not facility_id:
            base_items = [serialize_item(i) for i in items if _is_blank(i.facility_override)]
            return _merge_master_defaults(base_items, None)

        base = {
            i.name: i
            for i in items
            if _is_blank(i.facility_override)
        }
        overrides = {
            i.name: i
            for i in items
            if (i.facility_override or "").strip() == facility_id
        }
        merged = {**base, **overrides}
        return _merge_master_defaults([serialize_item(i) for i in merged.values()], facility_id)


def get_menu(month_id: str) -> dict | None:
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
    with session_scope() as session:
        items = session.query(MonthlyMenuItem).filter(MonthlyMenuItem.monthly_menu_id == month_id).all()
        return _merge_master_defaults([serialize_item(i) for i in items], None)


def get_item(item_id: str) -> dict | None:
    with session_scope() as session:
        item = session.get(MonthlyMenuItem, item_id)
        if not item:
            return None
        merged = _merge_master_defaults([serialize_item(item)], item.facility_override)
        return merged[0] if merged else serialize_item(item)


def resolve_menu_defaults(names: list[str], facility_id: str | None = None) -> dict[str, dict]:
    with session_scope() as session:
        defaults = _build_master_defaults(session, names, facility_id)
    resolved: dict[str, dict] = {}
    for name in names:
        normalized = _normalize_menu_name(name or "")
        resolved[name] = defaults.get(normalized, {})
    return resolved
